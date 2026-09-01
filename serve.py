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
    GET    /api/ping              -> {ok, areas_file, authoring, uploads, world}
    POST   /api/world             -> {ok, trains, vehicles, bytes, version}
                                     push a (gzip-optional) world save into the slot;
                                     enabled by --accept-uploads, gated by --upload-token
    GET    /api/trains            -> {version, trains:{regionId:[...]}}  (re-plots on change)
    GET    /api/areas             -> {areas: [...]}          (merged: all sources)
    POST   /api/areas             -> created area dict       (writes writable file)
    PUT    /api/areas/<id>        -> updated area dict
    DELETE /api/areas/<id>        -> {ok: true}
Area dicts use the shape {id,label,tile_x,tile_z,local_x,local_z,
color?,font_size?,box?,rotation?}.
"""

import argparse
import gzip
import json
import math
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
from world_parser import parse_world_save, parse_sim_time
from output_generator import train_to_dict
from rv_length_db import load_rv_lengths


# Upload guards for POST /api/world (world saves are ~1.7 MB; caps are generous
# so a busy server's save still fits, while rejecting anything absurd / hostile).
MAX_UPLOAD_COMPRESSED = 64 * 1024 * 1024     # bytes read off the wire
MAX_UPLOAD_DECOMPRESSED = 256 * 1024 * 1024  # bytes after gunzip (zip-bomb guard)


class WorldUploadError(Exception):
    """Raised when an uploaded world save is rejected (bad size / not valid XML)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# Socket errors that just mean the client went away mid-response (a superseded 3 s
# poll, a reload, a closed tab). These are benign - swallow them instead of logging a
# noisy traceback (and never try to write an error response back over a dead socket).
_CLIENT_DISCONNECT = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Like ThreadingHTTPServer, but a client that simply disconnects mid-request (a
    superseded poll, a reload, a closed tab) raises ConnectionError on the read or
    write side - benign, so don't dump a traceback for it. Anything else logs normally."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


# A train whose representative position moved more than this (tile-world metres)
# between consecutive saves is considered "moving".
MOVE_THRESHOLD_M = 5.0


def _train_repr_pos(train_dict):
    """A representative (x, y) for a train: the first placed vehicle's first body
    point. Returns None when the train has no drawable vehicles."""
    for v in train_dict.get('vehicles', []):
        body = v.get('body')
        if body:
            return (body[0][0], body[0][1])
    return None


def _pos_dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class AuthoringState:
    """Shared, thread-safe state for the request handlers."""

    def __init__(self, config_path: Path, output_dir: Path, areas_file: Path,
                 config=None, world_save: Path = None,
                 accept_uploads: bool = False, upload_token: str = None):
        self.config_path = config_path
        self.output_dir = output_dir
        self.manifest_path = output_dir / "manifest.json"
        self.store = AreaStore(areas_file)
        self.areas_file = areas_file
        self.lock = threading.Lock()

        # ---- world-save train plotting (optional, with live mtime watch) ----
        self.config = config
        self.world_save = world_save
        # When accept_uploads is set, POST /api/world writes the pushed save into
        # `world_save` (a server-owned "slot"); the /api/trains mtime-watch then
        # re-plots it exactly as if Run8 had written the file locally. An optional
        # bearer token gates the endpoint (required for any non-localhost host).
        self.accept_uploads = accept_uploads
        self.upload_token = upload_token or None
        # regionId -> route_prefix (for filtering vehicles to each region)
        self.region_prefix = {r.id: r.route_prefix for r in (config.regions if config else [])}
        self._sections_cache = {}   # regionId -> [SectionData] (rebuilt from region JSON)
        self._trains_cache = {'mtime': None, 'payload': {'version': 0, 'trains': {}}}
        self._prev_positions = {}   # train_id -> (x, y) from the previous save, for movement
        self._still_since = {}      # train_id -> consecutive stationary save-cycles (hysteresis)
        # Keep a train flagged "moving" this many stationary cycles after its last move.
        self.move_hysteresis = int(getattr(config, 'train_moving_hysteresis', 0)) if config else 0

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
                    is_switch=s.get('is_switch', False),  # needed: placement picks the
                    # straight through-chord for switch sections (else cars on a switch
                    # ride the bowed diverging lead)
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

        try:
            parsed = parse_world_save(str(self.world_save))
        except Exception:  # noqa: BLE001
            # Torn / partial read while Run8 is rewriting the save (~every 2 min).
            # Serve the last good payload and retry on the next poll (the cache mtime
            # is left unchanged, so the next poll re-parses once the write completes).
            return self._trains_cache['payload']
        # Whole-save totals (region-independent) for the viewer status line.
        totals = {'trains': len(parsed),
                  'vehicles': sum(len(tr.vehicles) for tr in parsed)}
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
        cur_pos = {}
        for prefix, trains in by_prefix.items():
            region_id = prefix_to_region.get(prefix)
            if not region_id:
                continue
            dicts = []
            for t in trains:
                d = train_to_dict(t)
                pos = _train_repr_pos(d)
                if pos is not None:
                    cur_pos[d['train_id']] = pos
                dicts.append(d)
            trains_by_region[region_id] = dicts

        # Movement: a train whose representative position changed since the previous
        # save moved this cycle. Hysteresis (H = self.move_hysteresis save-cycles) keeps
        # a train flagged "moving" for H further STATIONARY cycles after its last real
        # movement, so a brief hold (e.g. at a signal) doesn't flicker the highlight off
        # (H = 0 -> strict per-cycle). Live-only (needs two saves; first save = baseline).
        H = self.move_hysteresis
        still_since, moving = {}, {}
        for tid, pos in cur_pos.items():
            prev = self._prev_positions.get(tid)
            if prev is not None and _pos_dist(prev, pos) > MOVE_THRESHOLD_M:
                still_since[tid] = 0            # moved this cycle
                moving[tid] = True
            else:
                prev_still = self._still_since.get(tid)
                s = (prev_still + 1) if prev_still is not None else (H + 1)  # new train = stationary
                still_since[tid] = s
                moving[tid] = s <= H           # within the trailing hysteresis window
        self._prev_positions = cur_pos
        self._still_since = still_since
        for dicts in trains_by_region.values():
            for d in dicts:
                d['moving'] = bool(moving.get(d['train_id'], False))
        moving_count = sum(1 for v in moving.values() if v)

        payload = {'version': mtime, 'trains': trains_by_region, 'totals': totals,
                   'sim_time': parse_sim_time(str(self.world_save)),
                   'moving_count': moving_count}
        self._trains_cache = {'mtime': mtime, 'payload': payload}
        return payload

    # ---- world-save upload (call under self.lock) ----------------------
    def ingest_world(self, xml_bytes: bytes) -> dict:
        """Validate and atomically install a pushed world save into the slot.

        `xml_bytes` is the already-decompressed XML. It is written to a temp file
        beside `world_save`, parsed (a torn / non-XML upload raises and is
        rejected, leaving the previous good save in place), then os.replace()'d
        into the slot. Bumping the file's mtime makes /api/trains re-plot it.
        Returns a small summary for the agent's response.
        """
        if not self.accept_uploads or self.world_save is None:
            raise WorldUploadError("This server is not accepting world uploads", 403)
        if len(xml_bytes) > MAX_UPLOAD_DECOMPRESSED:
            raise WorldUploadError("World save too large", 413)

        slot = self.world_save
        slot.parent.mkdir(parents=True, exist_ok=True)
        tmp = slot.with_suffix(slot.suffix + '.tmp')
        try:
            tmp.write_bytes(xml_bytes)
            # Validate: parse_world_save raises ET.ParseError on a torn/non-XML
            # body, so a half-written or garbage upload never clobbers the slot.
            try:
                parsed = parse_world_save(str(tmp))
            except Exception as e:  # noqa: BLE001
                raise WorldUploadError(f"Not a valid world save: {e}", 422)
            os.replace(tmp, slot)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

        return {'ok': True,
                'bytes': len(xml_bytes),
                'trains': len(parsed),
                'vehicles': sum(len(tr.vehicles) for tr in parsed),
                'version': slot.stat().st_mtime}

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
            # Track whether the response already set Cache-Control (API replies do,
            # via _send_json) so end_headers only supplies a default for static
            # assets. Must be set before super().__init__, which handles the request.
            self._sent_cache_control = False
            # Serve static assets (index.html, manifest.json, data/*) from the
            # generated output directory rather than the process CWD.
            super().__init__(*args, directory=str(state.output_dir), **kwargs)

        def send_header(self, key, value):
            if key.lower() == 'cache-control':
                self._sent_cache_control = True
            super().send_header(key, value)

        def end_headers(self):
            # Without this, SimpleHTTPRequestHandler sends only Last-Modified on
            # static files, so browsers apply heuristic freshness and serve a stale
            # index.html / manifest.json / region JSON WITHOUT revalidating on a
            # normal reload (the "F5 stale, Ctrl-F5 fixes it" bug). "no-cache" keeps
            # the file cacheable but forces a revalidation every load (cheap 304s),
            # so a live map never shows stale geometry. API replies keep their own
            # no-store. This also makes behaviour deterministic behind Caddy.
            if not self._sent_cache_control:
                self.send_header('Cache-Control', 'no-cache')
            super().end_headers()

        # ---- helpers ---------------------------------------------------
        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode('utf-8')
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
            except _CLIENT_DISCONNECT:
                # Client closed the connection mid-response - nothing to send. Drop it
                # quietly (also stops do_GET's handler from re-erroring on a dead socket).
                pass

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
                                 'uploads': state.accept_uploads,
                                 # Report a world as present when uploads are enabled even
                                 # before the first push, so the viewer starts polling
                                 # /api/trains right away and picks up trains on arrival.
                                 'world': bool(state.accept_uploads
                                               or (state.world_save and state.world_save.exists()))})
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
            if path == '/api/world':
                self._handle_world_upload()
                return
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

        # ---- world-save upload (POST /api/world) -----------------------
        def _upload_authorized(self):
            """True if the request may push a world save. When a token is
            configured it must be presented as `Authorization: Bearer <token>`."""
            if not state.upload_token:
                return True
            auth = self.headers.get('Authorization', '')
            prefix = 'Bearer '
            return auth.startswith(prefix) and auth[len(prefix):] == state.upload_token

        def _handle_world_upload(self):
            if not state.accept_uploads:
                self._error('This server is not accepting world uploads '
                            '(start it with --accept-uploads)', 403)
                return
            if not self._upload_authorized():
                self._error('Unauthorized', 401)
                return
            length = self.headers.get('Content-Length')
            if length is None:
                self._error('Content-Length required', 411)
                return
            try:
                length = int(length)
            except ValueError:
                self._error('Bad Content-Length')
                return
            if length > MAX_UPLOAD_COMPRESSED:
                self._error('Upload too large', 413)
                return
            raw = self.rfile.read(length) if length else b''

            enc = (self.headers.get('Content-Encoding') or '').lower()
            if 'gzip' in enc:
                try:
                    xml_bytes = gzip.decompress(raw)
                except (OSError, EOFError) as e:
                    self._error(f'Could not gunzip body: {e}')
                    return
            else:
                xml_bytes = raw

            try:
                with state.lock:
                    summary = state.ingest_world(xml_bytes)
            except WorldUploadError as e:
                self._error(str(e), e.status)
                return
            except Exception as e:  # noqa: BLE001
                self._error(str(e), 500)
                return
            print(f"  world upload: {summary['trains']} train(s), "
                  f"{summary['vehicles']} vehicle(s), {summary['bytes']} bytes")
            self._send_json(summary)

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
                             'updates (overrides [visualization] world_save in the config). '
                             'With --accept-uploads this is the server-owned slot the pushed '
                             'save is written to (default: <output>/_world_upload.xml).')
    parser.add_argument('--accept-uploads', dest='accept_uploads', action='store_true',
                        help='Enable POST /api/world so a remote agent can push world saves '
                             'into the slot (see --world). Trains re-plot on each push.')
    parser.add_argument('--upload-token', dest='upload_token', metavar='TOKEN',
                        help='Require this bearer token on POST /api/world. Strongly '
                             'recommended for any non-localhost host.')
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
    # With uploads on but no explicit save path, use a server-owned slot so the
    # agent has somewhere to push to (and we never touch Run8's own autosave).
    if args.accept_uploads and world_save is None:
        world_save = output_dir / '_world_upload.xml'
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
                           config=config, world_save=world_save,
                           accept_uploads=args.accept_uploads,
                           upload_token=args.upload_token)
    if args.authoring:
        # Make sure the served manifest matches the areas files on disk at startup.
        try:
            state.sync_manifest()
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: could not sync manifest at startup: {e}")

    handler = make_handler(state, args.authoring)
    httpd = _QuietThreadingHTTPServer((args.host, args.port), handler)

    print(f"\nRun8 area-label authoring server")
    print(f"  Serving : {output_dir}")
    if args.authoring:
        print(f"  Writing : {areas_file}")
    else:
        print(f"  Mode    : read-only (no label authoring)")
    if world_save:
        exists = " (not found yet)" if not world_save.exists() else ""
        role = "upload slot" if args.accept_uploads else "live-watched"
        print(f"  World   : {world_save}{exists}  [{role}]")
    if args.accept_uploads:
        tok = "token required" if args.upload_token else "NO TOKEN (localhost only!)"
        print(f"  Uploads : POST /api/world enabled  [{tok}]")
    print(f"  URL     : http://{args.host}:{args.port}/")
    print(f"\nPress Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()


if __name__ == '__main__':
    main()
