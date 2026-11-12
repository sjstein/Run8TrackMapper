#!/usr/bin/env python3
"""Watch one route's signal heads on the Run8 telemetry bridge and report changes.

Built to map the raw head display-state numbers (schema 4 ``signalHeads``) to what you
see in the sim: leave it running, change or watch a signal, and it prints each head whose
number changed as ``head <signal_index>: <old> -> <new>``. Signal-level aspect changes
(schema 3 ``signals``: Proceed / Fleet / FlagBy ..., Stop = absent) are reported too.

    python watch_signal_heads.py 120                 # Cajon, 1 s poll
    python watch_signal_heads.py 120 --interval 0.5
    python watch_signal_heads.py 100 --url http://127.0.0.1:40872 --token-file tok.txt
    python watch_signal_heads.py 100 --value 4       # also list every head currently at 4
    python watch_signal_heads.py 100 --value 4 --once    # list them and exit

The head number is the ``signal_index`` shown for a signal on the map (and stored in the
route's SignalHeadDatabase.r8). Needs only the Python standard library.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime


def fetch(url, token, timeout):
    req = urllib.request.Request(url.rstrip('/') + '/v1/snapshot')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def route_state(snap, route):
    """Return (heads {int: int}, aspects {int: str}) for one route from a snapshot."""
    heads = {int(k): v for k, v in (snap.get('signalHeads') or {}).get(route, {}).items()}
    aspects = {int(k): v for k, v in (snap.get('signals') or {}).get(route, {}).items()}
    return heads, aspects


def stamp(snap):
    sim = snap.get('simulationTime') or ''
    wall = datetime.now().strftime('%H:%M:%S')
    return f'{wall}  sim {sim[11:19]}' if sim else wall


def heads_with(heads, value):
    return sorted(h for h, v in heads.items() if v == value)


def fmt_heads(heads, value):
    found = heads_with(heads, value)
    return (f'{len(found)}: ' + ', '.join(str(h) for h in found)) if found else 'none'


def diff(old, new, missing='-'):
    """Yield (key, old_value, new_value) for every key whose value differs."""
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key, missing), new.get(key, missing)
        if a != b:
            yield key, a, b


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('route', help='route prefix to watch, e.g. 120')
    ap.add_argument('--url', default='http://127.0.0.1:40872', help='bridge base URL')
    ap.add_argument('--token-file', help='file holding a bridge bearer token, if one is set')
    ap.add_argument('--interval', type=float, default=1.0, help='seconds between polls (default 1)')
    ap.add_argument('--value', type=int, help='also list every head currently showing this state '
                    'number (re-listed whenever the set changes)')
    ap.add_argument('--once', action='store_true',
                    help='print the baseline (and the --value list) once, then exit')
    args = ap.parse_args()

    token = None
    if args.token_file:
        with open(args.token_file, encoding='utf-8') as f:
            token = f.read().strip()
    route = str(args.route)

    prev_heads = prev_aspects = None
    seen_states = Counter()
    first_error_shown = False
    print(f'Watching route {route} on {args.url} every {args.interval:g}s. Ctrl+C to stop.')
    try:
        while True:
            try:
                snap = fetch(args.url, token, timeout=max(5.0, args.interval * 3))
                first_error_shown = False
            except (urllib.error.URLError, OSError, ValueError) as exc:
                if not first_error_shown:
                    print(f'  [bridge unavailable: {exc}] retrying...')
                    first_error_shown = True
                time.sleep(args.interval)
                continue

            if 'signalHeads' not in snap:
                sys.exit(f"Snapshot has no 'signalHeads' (schemaVersion {snap.get('schemaVersion')}); "
                         "this needs a schema 4 producer (0.4.0.0).")
            if route not in snap['signalHeads']:
                known = ', '.join(sorted(snap['signalHeads'], key=int))
                sys.exit(f'Route {route} is not in the snapshot. Routes present: {known}')

            heads, aspects = route_state(snap, route)
            if prev_heads is None:
                counts = Counter(heads.values())
                print(f'{stamp(snap)}  baseline: {len(heads)} heads; state values present: '
                      + ', '.join(f'{v} (x{n})' for v, n in sorted(counts.items())))
                seen_states.update(heads.values())
                if args.value is not None:
                    print(f'{stamp(snap)}  heads at {args.value}: {fmt_heads(heads, args.value)}')
                if args.once:
                    return
            else:
                for head, a, b in diff(prev_heads, heads):
                    print(f'{stamp(snap)}  head {head}: {a} -> {b}')
                    seen_states[b] += 1
                for sig, a, b in diff(prev_aspects, aspects, missing='Stop'):
                    print(f'{stamp(snap)}  signal list#{sig} aspect: {a} -> {b}')
                if args.value is not None and (
                        heads_with(prev_heads, args.value) != heads_with(heads, args.value)):
                    print(f'{stamp(snap)}  heads at {args.value}: {fmt_heads(heads, args.value)}')
            prev_heads, prev_aspects = heads, aspects
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nStopped. State values seen on route {}: {}'.format(
            route, ', '.join(str(v) for v in sorted(seen_states)) or 'none'))


if __name__ == '__main__':
    main()
