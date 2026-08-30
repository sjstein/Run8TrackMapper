#!/usr/bin/env python3
"""Run8 world-save uploader agent (Goal #1, phase P1).

Runs on the machine hosting a Run8 *server* and pushes its full-world autosave
to a Track Mapper web host over outbound HTTPS, so trains can be plotted on a
remote map with no inbound/firewall access on the server operator's side.

Run8 mirrors the newest full-world autosave to a fixed filename:

    ...\\Content\\V3Routes\\Regions\\<REGION>\\AutoSaves\\Auto Save World.xml

This agent watches that fixed file, waits for each 2-minute write to settle,
skips unchanged saves, gzips the XML (~10:1) and POSTs it to the host's
`/api/world` endpoint (see serve.py --accept-uploads / --upload-token).

Stdlib only, so it bundles cleanly to a single .exe with PyInstaller later.

    python r8_world_agent.py --host-url http://localhost:8000 \
        --save-file "C:\\...\\Regions\\SouthernCA\\AutoSaves\\Auto Save World.xml"

Settings may also live in an INI (default: agent.ini beside this script) under
an [agent] section; command-line flags override it:

    [agent]
    host_url   = http://localhost:8000
    save_file  = C:\\...\\Regions\\SouthernCA\\AutoSaves\\Auto Save World.xml
    token      =
    poll       = 10
"""

import argparse
import configparser
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

DEFAULT_SAVE_NAME = "Auto Save World.xml"
DEFAULT_ENDPOINT = "/api/world"
DEFAULT_POLL = 10.0      # seconds between mtime checks (cadence is ~120 s)
DEFAULT_SETTLE = 2.0     # seconds the size/mtime must hold steady before reading
BACKOFF_CAP = 120.0      # max seconds between retries after an upload failure


def _log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_ini_defaults(config_path: Path) -> dict:
    """Read [agent] settings from an INI, if present. Returns a plain dict."""
    if not config_path or not config_path.exists():
        return {}
    cp = configparser.ConfigParser()
    cp.read(config_path, encoding='utf-8')
    if not cp.has_section('agent'):
        return {}
    return dict(cp.items('agent'))


def _resolve_target(args) -> Path:
    """Resolve the world-save file to watch from --save-file / --save-dir."""
    if args.save_file:
        return Path(args.save_file)
    if args.save_dir:
        return Path(args.save_dir) / args.save_name
    raise SystemExit("ERROR: give --save-file (or --save-dir) pointing at the "
                     f"Run8 AutoSaves '{DEFAULT_SAVE_NAME}'.")


def _read_when_settled(path: Path, settle: float, poll: float) -> bytes:
    """Block until `path` stops changing for `settle` seconds, then return its
    bytes. Guards against reading a half-written save and against the brief
    write-lock Run8 may hold. Returns b'' only if the file disappears."""
    last = None
    while True:
        try:
            st = path.stat()
        except FileNotFoundError:
            return b''
        sig = (st.st_size, st.st_mtime)
        if sig == last:
            # Stable across one settle interval; try to read it.
            try:
                return path.read_bytes()
            except (PermissionError, OSError):
                # Still locked by Run8; wait and re-confirm stability.
                time.sleep(settle)
                last = None
                continue
        last = sig
        time.sleep(settle)


def _upload(host_url: str, endpoint: str, xml_bytes: bytes, filename: str,
            token: str, use_gzip: bool) -> dict:
    """POST the world save to the host. Returns the parsed JSON response.
    Raises HTTPError / URLError on failure."""
    body = gzip.compress(xml_bytes) if use_gzip else xml_bytes
    url = host_url.rstrip('/') + endpoint
    req = urlrequest.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/xml')
    req.add_header('X-World-Filename', filename)
    if use_gzip:
        req.add_header('Content-Encoding', 'gzip')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    with urlrequest.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return {'ok': True}


def _push_once(target: Path, args, state: dict) -> bool:
    """Read (settled), dedupe, and upload the current save. Returns True on a
    successful upload, False if skipped (unchanged / missing) or failed."""
    try:
        mtime = target.stat().st_mtime
    except FileNotFoundError:
        return False
    if mtime == state.get('mtime') and not args.once:
        return False  # not modified since last look

    xml_bytes = _read_when_settled(target, args.settle, args.poll)
    if not xml_bytes:
        return False
    digest = hashlib.sha256(xml_bytes).hexdigest()
    if digest == state.get('hash'):
        state['mtime'] = mtime  # same content re-saved; note mtime, don't resend
        return False

    kb = len(xml_bytes) / 1024
    try:
        summary = _upload(args.host_url, args.endpoint, xml_bytes,
                          target.name, args.token, not args.no_gzip)
    except HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        _log(f"upload FAILED: HTTP {e.code} {detail}")
        return False
    except URLError as e:
        _log(f"upload FAILED: {e.reason}")
        return False

    state['mtime'] = mtime
    state['hash'] = digest
    _log(f"uploaded {kb:.0f} KB -> {summary.get('trains', '?')} train(s), "
         f"{summary.get('vehicles', '?')} vehicle(s)")
    return True


def main():
    script_dir = Path(__file__).resolve().parent
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config', default=str(script_dir / 'agent.ini'),
                     help='INI with an [agent] section (default: agent.ini beside this script)')
    known, _ = pre.parse_known_args()
    ini = _load_ini_defaults(Path(known.config))

    def d(key, fallback=None):
        return ini.get(key, fallback)

    parser = argparse.ArgumentParser(
        description=__doc__, parents=[pre],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host-url', default=d('host_url'),
                        help='Base URL of the Track Mapper host, e.g. http://localhost:8000')
    parser.add_argument('--endpoint', default=d('endpoint', DEFAULT_ENDPOINT),
                        help=f'Upload path (default {DEFAULT_ENDPOINT})')
    parser.add_argument('--save-file', default=d('save_file'),
                        help=f"Full path to Run8's '{DEFAULT_SAVE_NAME}'")
    parser.add_argument('--save-dir', default=d('save_dir'),
                        help="Run8 AutoSaves directory (used with --save-name)")
    parser.add_argument('--save-name', default=d('save_name', DEFAULT_SAVE_NAME),
                        help=f"File to watch inside --save-dir (default '{DEFAULT_SAVE_NAME}')")
    parser.add_argument('--token', default=d('token'),
                        help='Bearer token, if the host requires one')
    parser.add_argument('--poll', type=float, default=float(d('poll', DEFAULT_POLL)),
                        help=f'Seconds between checks (default {DEFAULT_POLL})')
    parser.add_argument('--settle', type=float, default=float(d('settle', DEFAULT_SETTLE)),
                        help=f'Seconds of no-change before reading (default {DEFAULT_SETTLE})')
    parser.add_argument('--no-gzip', action='store_true',
                        default=str(d('no_gzip', '')).lower() in ('1', 'true', 'yes'),
                        help='Upload uncompressed (default: gzip)')
    parser.add_argument('--once', action='store_true',
                        help='Upload the current save once and exit (for testing)')
    args = parser.parse_args()

    if not args.host_url:
        raise SystemExit("ERROR: --host-url is required (or set host_url in agent.ini).")
    target = _resolve_target(args)

    _log(f"agent starting")
    _log(f"  watching : {target}")
    _log(f"  host     : {args.host_url.rstrip('/') + args.endpoint}")
    _log(f"  gzip     : {'off' if args.no_gzip else 'on'}   token: {'yes' if args.token else 'no'}")
    if not target.exists():
        _log(f"  (file not present yet - will wait for Run8 to write it)")

    state = {'mtime': None, 'hash': None}

    if args.once:
        # Wait up to ~30 s for the file to appear, then push once.
        for _ in range(30):
            if target.exists():
                break
            time.sleep(1.0)
        ok = _push_once(target, args, state)
        _log("done." if ok else "nothing uploaded.")
        return

    backoff = args.poll
    try:
        while True:
            uploaded = _push_once(target, args, state)
            # On a failed attempt (file present, changed, but upload errored),
            # back off; otherwise poll normally.
            changed = target.exists() and target.stat().st_mtime != state.get('mtime')
            if changed and not uploaded:
                backoff = min(backoff * 2, BACKOFF_CAP)
                _log(f"retry in {backoff:.0f}s")
                time.sleep(backoff)
            else:
                backoff = args.poll
                time.sleep(args.poll)
    except KeyboardInterrupt:
        _log("stopped.")


if __name__ == '__main__':
    main()
