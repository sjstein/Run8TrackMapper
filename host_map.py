#!/usr/bin/env python3
"""Run8 Map self-host launcher - the single entry point an operator runs.

This is the spine of the self-host bundle. One command (or one double-click of the
packaged .exe) both **generates** the operator's map from their local Run8 install
and **serves** it read-only to the internet, watching their world autosave so trains
move live. It is the distributed alternative to hosting everyone's maps on a central
droplet: the map runs on the same Windows box that runs the operator's r8server.

    python host_map.py <config.ini> [--port 8000] [--host 0.0.0.0]
                        [--world "...\\AutoSaves\\Auto Save World.xml"]
                        [--regenerate] [--no-generate] [--no-update-check]

What it does, in order:
  1. Parse the operator's config (the same INI `output_generator.py` uses).
  2. Generate the production map (align viewer, no "Add Label" button) if the output
     is missing, or always with --regenerate. Skipped with --no-generate.
  3. Serve that output read-only, bound to 0.0.0.0 so a forwarded TCP port reaches it,
     watching the world save for live train updates (no upload endpoint, no token).
  4. In the background, check whether a newer bundle has been published and, if so,
     print a one-line notice (never blocks or fails startup).

Posture (deliberate, see deploy_handoff.md "DIRECTION CHANGE"): this faces the
internet directly with no TLS and no reverse proxy. That is acceptable because the
server is READ-ONLY (no authoring, no uploads) and its one expensive operation -
train placement - is mtime-cached, so it is a public, no-secrets map. Operators who
have a domain can still front it with Caddy for HTTPS (an advanced, optional step).
"""

import argparse
import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

from config_parser import parse_config
from output_generator import generate_output
from serve import (AuthoringState, make_handler, _QuietThreadingHTTPServer,
                   _resolve_writable_areas_file)
from version import __version__


# Where the launcher asks "is there a newer bundle?" - the GitHub Releases API for
# this repo, which returns the latest (non-draft, non-prerelease) release as JSON
# (tag_name + assets[]). Releases are the distribution channel, so no separate host
# is needed. Overridable via the RUN8MAP_UPDATE_URL env var; a missing/unreachable
# endpoint (offline box, no releases yet -> 404) is a silent no-op.
DEFAULT_UPDATE_URL = "https://api.github.com/repos/sjstein/Run8TrackMapper/releases/latest"


def _bundle_root() -> Path:
    """Directory the app-owned data assets live in. Under a PyInstaller bundle that
    is the extracted _MEIPASS root; from source it's the repo (this file's dir)."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def bundled_asset(rel: str) -> Path:
    """Absolute path to a data asset shipped inside the bundle (e.g.
    'db_railvehicles.db'). May not exist when running from a partial source tree."""
    return _bundle_root() / rel


def _apply_bundled_assets(config):
    """Supply app-owned data the operator shouldn't have to manage. The rail-vehicle
    length/colour DB (db_railvehicles.db) is shared Run8 rolling-stock data, so it
    ships in the bundle; point the config at it unless the operator named their own
    existing DB. A config with a valid railvehicle_db is left untouched."""
    rv = config.railvehicle_db
    if rv and Path(rv).exists():
        return  # operator supplied their own; respect it
    bundled = bundled_asset('db_railvehicles.db')
    if bundled.exists():
        config.railvehicle_db = bundled
        print(f"  Rail-vehicle DB: {bundled} (bundled)")
    elif rv:
        print(f"  Note: railvehicle_db {rv} not found and no bundled DB present; "
              f"car lengths/colours fall back to defaults.")


def _version_tuple(v: str):
    """Parse a 'x.y.z' version into a comparable tuple of ints, ignoring any
    non-numeric suffix. Unparseable parts sort as 0 so a bad value never wins."""
    parts = []
    for chunk in str(v).strip().split('.'):
        num = ''.join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def check_for_update(current: str, url: str, timeout: float = 3.0):
    """Fetch the latest GitHub release and return (latest, download_url) when its
    version is newer than `current`, else None. Reads the GitHub Releases API shape
    (`tag_name` + `assets[]`); the tag may be like `v0.2.0` or `0.2.0` (the leading
    v is stripped). download_url is the .zip asset's direct link when present, else
    the release page. Fully failure-tolerant: any network / parse error (incl. a 404
    when no release exists yet) returns None, so the check never blocks or breaks
    startup on an offline box."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': f'run8map/{current}',
            'Accept': 'application/vnd.github+json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        latest = str(data.get('tag_name', '')).strip().lstrip('vV')
        if latest and _version_tuple(latest) > _version_tuple(current):
            # Prefer the .zip asset's direct download; fall back to the release page.
            download_url = ''
            for asset in (data.get('assets') or []):
                if str(asset.get('name', '')).lower().endswith('.zip'):
                    download_url = str(asset.get('browser_download_url', '') or '')
                    break
            if not download_url:
                download_url = str(data.get('html_url', '') or '')
            return latest, download_url
    except Exception:  # noqa: BLE001 - any failure is a silent no-op by design
        return None
    return None


def _update_check_thread(current: str, url: str):
    """Run check_for_update off the main thread and print a notice if newer."""
    result = check_for_update(current, url)
    if result:
        latest, download_url = result
        print(f"\n  * A newer Run8 Map bundle is available: {latest} "
              f"(you have {current}).")
        if download_url:
            print(f"    Download: {download_url}")
        print("    Your map keeps running; update when convenient.\n")


def _program_dir() -> Path:
    """Folder the operator's editable files (config, output/) live beside: the
    directory of the .exe when frozen, else the current working directory."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _default_config_path():
    """When no config is given (e.g. a double-click), look for a config beside the
    program: config-socal.ini first, then config.ini. Returns a str path or None."""
    for name in ('config-socal.ini', 'config.ini'):
        candidate = _program_dir() / name
        if candidate.exists():
            return str(candidate)
    return None


def _resolve_world_save(args, config):
    """Pick the world save to watch: --world wins, else [visualization] world_save.
    May not exist yet (Run8 hasn't autosaved); serve tolerates that and picks it up
    when it appears."""
    if args.world:
        p = Path(args.world)
        return p if p.is_absolute() else Path.cwd() / p
    if config.world_save:
        return Path(config.world_save)
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('config', nargs='?', default=None,
                        help='Path to the map config INI (same one output_generator.py uses). '
                             'If omitted, looks for config-socal.ini or config.ini next to the program.')
    parser.add_argument('--port', type=int, default=8000,
                        help='TCP port to serve on (default 8000). This is the port to forward '
                             'on your router - a DIFFERENT port from Run8\'s UDP port.')
    parser.add_argument('--host', default='0.0.0.0',
                        help='Interface to bind (default 0.0.0.0 = all, so a forwarded port '
                             'reaches it). Use 127.0.0.1 to keep it local-only for testing.')
    parser.add_argument('--world', metavar='FILE',
                        help='Run8 world autosave (.xml) to watch for live trains '
                             '(overrides [visualization] world_save in the config). Point this at '
                             'your server\'s full-world save: ...\\Regions\\<REGION>\\AutoSaves\\Auto Save World.xml')
    parser.add_argument('--regenerate', action='store_true',
                        help='Force a fresh map generation even if output already exists')
    parser.add_argument('--no-generate', action='store_true',
                        help='Skip generation entirely; just serve the existing output')
    parser.add_argument('--no-update-check', action='store_true',
                        help='Do not check whether a newer bundle has been published')
    parser.add_argument('--edit-password', dest='edit_password', metavar='PASSWORD',
                        help='Shared password that lets staff edit area labels on the live '
                             'map. WITHOUT it (the default) the map is read-only. Prefer the '
                             'RUN8_EDIT_PASSWORD environment variable so the secret is not in '
                             'the command line. On a public host, serve behind a TLS tunnel '
                             '(see the hosting docs).')
    args = parser.parse_args()

    # Edit password (#56): presence enables password-gated label editing; absence keeps
    # the map read-only. Env var preferred (keeps the secret out of argv / the console).
    edit_password = args.edit_password or os.environ.get('RUN8_EDIT_PASSWORD') or None

    # Print progress promptly. A frozen console app's stdout is block-buffered when
    # it isn't attached to a real console (e.g. Git Bash / a pipe), which makes the
    # first-run generation look hung. Force line buffering so each line shows at once.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except Exception:  # noqa: BLE001 - older/odd streams: harmless to skip
            pass

    print(f"Run8 Map self-host launcher  (bundle {__version__})")

    config_path = args.config or _default_config_path()
    if not config_path or not Path(config_path).exists():
        print("ERROR: no config file given and none found next to the program.\n"
              "       Pass one:   Run8MapHost.exe <your-config.ini>\n"
              "       or place a config-socal.ini / config.ini beside the program.",
              file=sys.stderr)
        sys.exit(1)

    # The operator's box HAS Run8, so source paths in the config are real - keep the
    # normal validation (unlike the droplet, which uses require_source_files=False).
    config = parse_config(config_path)
    _apply_bundled_assets(config)
    output_dir = config.output_dir
    index_html = output_dir / 'index.html'

    # Resolve the world save now: it's what serve watches, and it's the only save we
    # bake into the static output (a nicer cold load). Trains are otherwise LIVE via
    # the serve watch, so generation must never depend on / fail on a missing save.
    world_save = _resolve_world_save(args, config)
    config.world_save = None   # never let generation fall back to a (maybe-missing) config path

    # ---- 1) generate the production map if needed ---------------------------
    need_generate = args.regenerate or not index_html.exists()
    if args.no_generate:
        if not index_html.exists():
            print(f"ERROR: --no-generate given but no map found at {index_html}.\n"
                  f"       Run once without --no-generate to build it first.", file=sys.stderr)
            sys.exit(1)
        need_generate = False
    if need_generate:
        why = "forced by --regenerate" if args.regenerate else "no existing output"
        print(f"\nGenerating map ({why}) - this can take a couple of minutes on the "
              f"first run; the map will start serving when it finishes...")
        # Bake the current save only if it's actually there; otherwise build an
        # empty-of-trains map and let the live poll fill it in within seconds.
        bake = str(world_save) if (world_save and world_save.exists()) else None
        # Always include the authoring UI; it stays inert until an edit password is set
        # and a staffer unlocks (#56). Editing itself is gated at serve time, below.
        generate_output(config, world_save=bake)
        print("Map generated.")
    else:
        print(f"\nUsing existing map at {output_dir} (pass --regenerate to rebuild).")

    if not index_html.exists():
        print(f"ERROR: {index_html} still not found after generation.", file=sys.stderr)
        sys.exit(1)

    # ---- 2) serve it, network-facing, watching the world save ----------------
    # Editing is password-gated (#56): with no edit password the map is read-only; with
    # one, staff type "edit" in the viewer and unlock with the password.
    authoring = bool(edit_password)
    areas_file = output_dir / '_noauthoring.ini'   # dummy; only written when authoring
    if authoring:
        resolved = _resolve_writable_areas_file(config, Path(config_path), None)
        if resolved is None:
            print("  Note: an edit password was set but the config has no "
                  "[visualization] areas_file, so there is nowhere to save labels - "
                  "serving READ-ONLY. Add an areas_file and restart to enable editing.")
            authoring = False
        else:
            areas_file = resolved
    state = AuthoringState(
        Path(config_path), output_dir, areas_file,
        config=config, world_save=world_save,
        accept_uploads=False, upload_token=None,
        edit_password=edit_password if authoring else None)
    handler = make_handler(state, authoring=authoring)
    if authoring:
        try:
            state.sync_manifest()   # keep served manifest in sync with the areas file
        except Exception:  # noqa: BLE001
            pass
    httpd = _QuietThreadingHTTPServer((args.host, args.port), handler)

    # ---- 3) background update check (non-blocking, best-effort) --------------
    if not args.no_update_check:
        url = os.environ.get('RUN8MAP_UPDATE_URL') or DEFAULT_UPDATE_URL
        threading.Thread(target=_update_check_thread, args=(__version__, url),
                         daemon=True).start()

    # ---- banner --------------------------------------------------------------
    shown_host = 'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host
    print(f"\n  Serving : {output_dir}  ({'editing enabled (password-gated)' if authoring else 'read-only'})")
    if authoring:
        print(f"  Editing : staff type \"edit\" in the map, then unlock with the password.")
    if world_save:
        exists = "" if world_save.exists() else "  (not found yet - will pick it up when Run8 saves)"
        print(f"  World   : {world_save}{exists}")
    else:
        print("  World   : none configured (no live trains). Pass --world or set "
              "[visualization] world_save.")
    print(f"  Local   : http://{shown_host}:{args.port}/")
    if args.host == '0.0.0.0':
        print(f"  Public  : forward TCP port {args.port} on your router and allow it through "
              f"Windows Firewall,\n            then share http://<your-address>:{args.port}/ "
              f"(a dynamic-DNS hostname keeps it stable).")
        if authoring:
            print("  WARNING : editing is enabled over plain HTTP on a public port. The "
                  "password\n            itself is never sent, but an unlocked edit session can "
                  "be hijacked\n            by a sniffer. Put the map behind a TLS tunnel "
                  "(Tailscale Funnel /\n            Caddy) instead of a raw port forward - "
                  "see the hosting docs.")
    print("\nPress Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()


if __name__ == '__main__':
    main()
