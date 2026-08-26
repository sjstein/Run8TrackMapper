#!/usr/bin/env python3
"""Local area-label authoring server for the Run8 Track Mapper align viewer.

Serves an already-generated output directory (index.html, manifest.json,
data/*.json) *and* a small REST API the viewer uses to create / edit / delete
area labels without the copy-paste-into-a-config + regenerate loop.

    python serve.py <config.ini> [--port 8000] [--host 127.0.0.1] [--areas-file FILE]

Labels are written to a single "writable" areas file (the first entry of the
config's [visualization] areas_file, or --areas-file). On every change the
server rewrites that INI (via area_store, preserving comments/order) AND
refreshes manifest.json's "areas" so a fresh page load stays in sync too.

Stdlib only - no third-party dependency (this replaces `python -m http.server`).

API:
    GET    /api/ping              -> {ok, areas_file, authoring}
    GET    /api/areas             -> {areas: [...]}          (merged: all sources)
    POST   /api/areas             -> created area dict       (writes writable file)
    PUT    /api/areas/<id>        -> updated area dict
    DELETE /api/areas/<id>        -> {ok: true}
Area dicts use the shape {id,label,tile_x,tile_z,local_x,local_z,
color?,font_size?,box?,rotation?}.
"""

import argparse
import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

from config_parser import (parse_config, collect_areas, resolve_areas_files,
                            AREA_TYPE_DEFAULT)
from area_store import AreaStore, AreaStoreError, slugify, _area_to_dict
from region_extractor import SectionData, extract_trains, build_section_placer
from world_parser import parse_world_save
from output_generator import train_to_dict
from rv_length_db import load_rv_lengths


class AuthoringState:
    """Shared, thread-safe state for the request handlers."""

    def __init__(self, config_path: Path, output_dir: Path, areas_file: Path,
                 config=None, world_save: Path = None):
        self.config_path = config_path
        self.output_dir = output_dir
        self.manifest_path = output_dir / "manifest.json"
        self.store = AreaStore(areas_file)
        self.areas_file = areas_file
        self.lock = threading.Lock()

        # ---- world-save train plotting (optional, with live mtime watch) ----
        self.config = config
        self.world_save = world_save
        # regionId -> route_prefix (for filtering vehicles to each region)
        self.region_prefix = {r.id: r.route_prefix for r in (config.regions if config else [])}
        self._sections_cache = {}   # regionId -> [SectionData] (rebuilt from region JSON)
        self._trains_cache = {'mtime': None, 'payload': {'version': 0, 'trains': {}}}

        self.train_deoverlap = getattr(config, 'train_deoverlap', True) if config else True

        # Optional rail-vehicle length DB (true car length drawn over the trucks).
        self.rv_lengths = None
        rv_db = getattr(config, 'railvehicle_db', None) if config else None
        if rv_db and Path(rv_db).exists():
            try:
                self.rv_lengths = load_rv_lengths(str(rv_db))
                print(f"  Loaded {len(self.rv_lengths)} rail-vehicle length(s) from {rv_db}")
            except Exception as e:  # noqa: BLE001
                print(f"  Warning: could not load railvehicle_db {rv_db}: {e}")

    # ---- world-save trains (call under self.lock) ----------------------
    def _region_sections(self, region_id):
        """Reconstruct lightweight SectionData (id + paths) from the region's
        already-generated JSON, so trains can be re-placed without reloading the
        binary track database. Cached per region."""
        if region_id in self._sections_cache:
            return self._sections_cache[region_id]
        path = self.output_dir / 'data' / f'{region_id}.json'
        secs = []
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for s in data.get('sections', []):
                secs.append(SectionData(
                    id=s['id'],
                    paths=[[tuple(pt) for pt in p] for p in s.get('paths', [])],
                    length_ft=s.get('length_ft', 0.0),
                    length_m=s.get('length_m', 0.0),
                ))
        self._sections_cache[region_id] = secs
        return secs

    def trains_payload(self):
        """Return {version, trains:{regionId:[train dicts]}}, re-placing vehicles
        only when the world save file's mtime has changed since the last call."""
        if not self.world_save or not self.world_save.exists():
            return {'version': 0, 'trains': {}}
        mtime = self.world_save.stat().st_mtime
        if mtime == self._trains_cache['mtime']:
            return self._trains_cache['payload']

        parsed = parse_world_save(str(self.world_save))
        # A car can straddle a region boundary, so build ONE placer over every
        # region whose prefix appears on ANY truck (A or B) - not just truck A -
        # then place all trains against it at once.
        present = {truck.route_prefix
                   for tr in parsed for v in tr.vehicles
                   for truck in (v.truck_a, v.truck_b)}
        region_sections, prefix_to_region = [], {}
        for region_id, prefix in self.region_prefix.items():
            if prefix in present:
                region_sections.append((prefix, self._region_sections(region_id)))
                prefix_to_region[prefix] = region_id
        placer = build_section_placer(region_sections)
        by_prefix = extract_trains(parsed, placer, rv_lengths=self.rv_lengths,
                                   deoverlap=self.train_deoverlap)

        trains_by_region = {}
        for prefix, trains in by_prefix.items():
            region_id = prefix_to_region.get(prefix)
            if region_id:
                trains_by_region[region_id] = [train_to_dict(t) for t in trains]

        payload = {'version': mtime, 'trains': trains_by_region}
        self._trains_cache = {'mtime': mtime, 'payload': payload}
        return payload

    # ---- label operations (call under self.lock) -----------------------
    def merged_areas(self):
        return [_area_to_dict(a) for a in collect_areas(self.config_path)]

    def existing_ids(self):
        return {a['id'] for a in self.merged_areas()}

    def unique_id(self, desired: str) -> str:
        existing = self.existing_ids()
        if desired not in existing:
            return desired
        n = 2
        while f"{desired}_{n}" in existing:
            n += 1
        return f"{desired}_{n}"

    def sync_manifest(self):
        """Rewrite manifest.json's `areas` from the current merged label set."""
        if not self.manifest_path.exists():
            return
        with open(self.manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        manifest['areas'] = self.merged_areas()
        tmp = self.manifest_path.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp, self.manifest_path)


def _clean_common_fields(payload: dict, area: dict):
    """Apply the optional label fields (color/font_size/box/rotation/label)
    from a request payload onto an area dict. Raises ValueError on bad types."""
    if 'label' in payload:
        label = str(payload.get('label', '')).strip()
        if not label:
            raise ValueError("label is required")
        area['label'] = label

    if 'color' in payload:
        # Store the raw value (preset name or hex); resolved to hex at render time.
        color = (payload.get('color') or '').strip()
        if color:
            area['color'] = color
        else:
            area.pop('color', None)

    if 'font_size' in payload:
        fs = payload.get('font_size')
        if fs in (None, '', 0):
            area.pop('font_size', None)
        else:
            area['font_size'] = int(fs)

    if 'box' in payload:
        if payload.get('box'):
            area['box'] = True
        else:
            area.pop('box', None)

    if 'rotation' in payload:
        rot = payload.get('rotation')
        rot = float(rot) if rot not in (None, '') else 0.0
        if rot:
            area['rotation'] = rot
        else:
            area.pop('rotation', None)

    if 'type' in payload:
        # Any type is accepted; ones not in the config's [label_types] render white.
        t = (payload.get('type') or '').strip().lower()
        if t and t != AREA_TYPE_DEFAULT:
            area['type'] = t
        else:
            area.pop('type', None)


def make_handler(state: AuthoringState, authoring: bool):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            # Serve static assets (index.html, manifest.json, data/*) from the
            # generated output directory rather than the process CWD.
            super().__init__(*args, directory=str(state.output_dir), **kwargs)

        # ---- helpers ---------------------------------------------------
        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def _error(self, message, status=400):
            self._send_json({'error': message}, status=status)

        def _read_json(self):
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length) if length else b''
            if not raw:
                return {}
            return json.loads(raw.decode('utf-8'))

        def _api_id(self, path):
            """Extract <id> from /api/areas/<id>, or None for the collection."""
            rest = path[len('/api/areas'):]
            if rest.startswith('/'):
                return unquote(rest[1:])
            return None

        def log_message(self, fmt, *args):
            # Quieter: only log API mutations, not every static asset.
            if '/api/' in (self.path or ''):
                super().log_message(fmt, *args)

        # ---- routing ---------------------------------------------------
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/ping':
                self._send_json({'ok': True,
                                 'areas_file': state.areas_file.name,
                                 'authoring': authoring,
                                 'world': bool(state.world_save and state.world_save.exists())})
                return
            if path == '/api/trains':
                try:
                    with state.lock:
                        self._send_json(state.trains_payload())
                except Exception as e:  # noqa: BLE001
                    self._error(str(e), 500)
                return
            if path == '/api/areas':
                try:
                    with state.lock:
                        self._send_json({'areas': state.merged_areas()})
                except Exception as e:  # noqa: BLE001
                    self._error(str(e), 500)
                return
            if path.startswith('/api/'):
                self._error('Not found', 404)
                return
            super().do_GET()

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != '/api/areas':
                self._error('Not found', 404)
                return
            if not authoring:
                self._error('This server is read-only (started with --no-authoring)', 403)
                return
            try:
                payload = self._read_json()
            except json.JSONDecodeError:
                self._error('Invalid JSON body')
                return
            try:
                with state.lock:
                    area = self._build_new_area(payload)
                    state.store.add(area)
                    state.sync_manifest()
                self._send_json(area, status=201)
            except (ValueError, KeyError) as e:
                self._error(str(e))
            except AreaStoreError as e:
                self._error(str(e), 409)
            except Exception as e:  # noqa: BLE001
                self._error(str(e), 500)

        def do_PUT(self):
            path = urlsplit(self.path).path
            area_id = self._api_id(path)
            if not path.startswith('/api/areas/') or not area_id:
                self._error('Not found', 404)
                return
            if not authoring:
                self._error('This server is read-only (started with --no-authoring)', 403)
                return
            try:
                payload = self._read_json()
            except json.JSONDecodeError:
                self._error('Invalid JSON body')
                return
            try:
                with state.lock:
                    current = {a['id']: a for a in state.store.read()}
                    if area_id not in current:
                        raise AreaStoreError(
                            f"Area '{area_id}' is not in {state.areas_file.name} "
                            f"and cannot be edited here")
                    area = dict(current[area_id])
                    # optional position edits
                    for k in ('tile_x', 'tile_z'):
                        if k in payload and payload[k] is not None:
                            area[k] = int(payload[k])
                    for k in ('local_x', 'local_z'):
                        if k in payload and payload[k] is not None:
                            area[k] = float(payload[k])
                    _clean_common_fields(payload, area)
                    state.store.update(area_id, area)
                    state.sync_manifest()
                self._send_json(area)
            except (ValueError, KeyError) as e:
                self._error(str(e))
            except AreaStoreError as e:
                self._error(str(e), 404)
            except Exception as e:  # noqa: BLE001
                self._error(str(e), 500)

        def do_DELETE(self):
            path = urlsplit(self.path).path
            area_id = self._api_id(path)
            if not path.startswith('/api/areas/') or not area_id:
                self._error('Not found', 404)
                return
            if not authoring:
                self._error('This server is read-only (started with --no-authoring)', 403)
                return
            try:
                with state.lock:
                    state.store.delete(area_id)
                    state.sync_manifest()
                self._send_json({'ok': True})
            except AreaStoreError as e:
                self._error(str(e), 404)
            except Exception as e:  # noqa: BLE001
                self._error(str(e), 500)

        # ---- build a new area from a POST payload ----------------------
        def _build_new_area(self, payload):
            label = str(payload.get('label', '')).strip()
            if not label:
                raise ValueError("label is required")
            try:
                tile_x = int(payload['tile_x'])
                tile_z = int(payload['tile_z'])
                local_x = float(payload['local_x'])
                local_z = float(payload['local_z'])
            except (KeyError, TypeError, ValueError):
                raise ValueError("tile_x, tile_z, local_x, local_z are required numbers")

            desired = (payload.get('id') or '').strip() or slugify(label) or f"area_{tile_x}_{tile_z}"
            area_id = state.unique_id(desired)

            area = {'id': area_id, 'label': label,
                    'tile_x': tile_x, 'tile_z': tile_z,
                    'local_x': local_x, 'local_z': local_z}
            _clean_common_fields(payload, area)
            return area

    return Handler


def _resolve_writable_areas_file(config, config_path, override):
    """Pick (and if needed create) the single areas file the server writes to."""
    if override:
        target = Path(override)
        if not target.is_absolute():
            target = Path.cwd() / target
    else:
        candidates = resolve_areas_files(config_path)
        if not candidates:
            print("ERROR: no [visualization] areas_file is configured, so there is "
                  "nowhere to save labels.\n"
                  "  Add one to your config, e.g.:\n"
                  "      [visualization]\n"
                  "      areas_file = areas_labels.ini\n"
                  "  (create that file, even empty), then re-run. Or pass --areas-file PATH.",
                  file=sys.stderr)
            return None
        target = candidates[0]

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding='utf-8')
        print(f"Created empty areas file: {target}")

    # Warn if the writable file is not part of the config's merge set, since
    # then a full regenerate would not pick these labels up.
    resolved = {p.resolve() for p in resolve_areas_files(config_path)}
    if target.resolve() not in resolved:
        print(f"WARNING: {target.name} is not listed in [visualization] areas_file; "
              f"labels saved here won't be picked up by a normal output_generator run.")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('config', help='Path to the visualization config INI (same one used to generate the output)')
    parser.add_argument('--port', type=int, default=8000, help='Port to listen on (default 8000)')
    parser.add_argument('--host', default='127.0.0.1', help='Host/interface to bind (default 127.0.0.1)')
    parser.add_argument('--areas-file', dest='areas_file',
                        help='Override the areas file labels are written to (default: first areas_file in the config)')
    parser.add_argument('--no-authoring', dest='authoring', action='store_false',
                        help='Serve read-only (disable the create/edit/delete API)')
    parser.add_argument('--world', metavar='FILE', dest='world',
                        help='Run8 world save (.xml) to plot trains from, watched for live '
                             'updates (overrides [visualization] world_save in the config)')
    args = parser.parse_args()

    config = parse_config(args.config)
    output_dir = config.output_dir

    # Resolve the world save (CLI overrides config); watched for live refresh.
    world_save = None
    if args.world:
        world_save = Path(args.world)
        if not world_save.is_absolute():
            world_save = Path.cwd() / world_save
    elif config.world_save:
        world_save = Path(config.world_save)
    if not (output_dir / 'index.html').exists():
        print(f"ERROR: {output_dir / 'index.html'} not found. Generate it first:\n"
              f"    python output_generator.py {args.config}", file=sys.stderr)
        sys.exit(1)

    areas_file = None
    if args.authoring:
        areas_file = _resolve_writable_areas_file(config, args.config, args.areas_file)
        if areas_file is None:
            sys.exit(1)

    state = AuthoringState(Path(args.config), output_dir,
                           areas_file or output_dir / '_noauthoring.ini',
                           config=config, world_save=world_save)
    if args.authoring:
        # Make sure the served manifest matches the areas files on disk at startup.
        try:
            state.sync_manifest()
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: could not sync manifest at startup: {e}")

    handler = make_handler(state, args.authoring)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)

    print(f"\nRun8 area-label authoring server")
    print(f"  Serving : {output_dir}")
    if args.authoring:
        print(f"  Writing : {areas_file}")
    else:
        print(f"  Mode    : read-only (no label authoring)")
    if world_save:
        exists = " (not found yet)" if not world_save.exists() else ""
        print(f"  World   : {world_save}{exists}  [live-watched]")
    print(f"  URL     : http://{args.host}:{args.port}/")
    print(f"\nPress Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()


if __name__ == '__main__':
    main()
