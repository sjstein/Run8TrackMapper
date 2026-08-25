#!/usr/bin/env python3
"""
Rail-vehicle length lookup from the Run8 rolling-stock database.

`db_railvehicles.db` is a SQLite database whose `loco_data` and `car_data`
tables carry one row per rail-vehicle definition, keyed by `R8_FILENAME` (the
same value a world save stores as `rvXMLfilename`). Two columns matter here, both
in **feet**:

* ``RV_LENGTH``     - the vehicle's *coupled* footprint (over pulling faces).
                     Run8 spaces coupled cars so their ``RV_LENGTH`` extents abut,
                     so drawing this length leaves no visible gap between cars.
* ``COUPLER_OFFSET`` - the per-end coupler length. Subtracting it from each end
                     gives the car body *between* the couplers, which restores a
                     realistic gap (the coupler region) between adjacent cars.

`load_rv_lengths()` returns a dict mapping the lower-cased filename to a
``(length_m, coupler_offset_m)`` pair in **metres**, merging both tables (the
`*_BACKUP_*` tables are ignored). The caller draws the body at
``length_m - 2 * coupler_offset_m``.
"""

import sqlite3
from typing import Dict, Tuple

FEET_TO_METERS = 0.3048

# Active tables (ignore the dated *_BACKUP_* copies).
_LENGTH_TABLES = ("loco_data", "car_data")


def load_rv_lengths(db_path: str) -> Dict[str, Tuple[float, float]]:
    """Load {r8_filename.lower(): (length_m, coupler_offset_m)} from the DB.

    Rows with a missing filename or a non-positive length are skipped. A missing
    coupler offset becomes 0.0 (body drawn at the full coupled length). If a
    filename appears in more than one table the first (loco) value wins; in
    practice the two tables are disjoint.
    """
    out: Dict[str, Tuple[float, float]] = {}
    con = sqlite3.connect(db_path)
    try:
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in _LENGTH_TABLES:
            if table not in have:
                continue
            for filename, length_ft, coupler_ft in con.execute(
                    f'SELECT R8_FILENAME, RV_LENGTH, COUPLER_OFFSET FROM "{table}"'):
                if not filename or length_ft is None:
                    continue
                try:
                    length_m = float(length_ft) * FEET_TO_METERS
                except (TypeError, ValueError):
                    continue
                if length_m <= 0:
                    continue
                try:
                    coupler_m = float(coupler_ft) * FEET_TO_METERS
                except (TypeError, ValueError):
                    coupler_m = 0.0
                if coupler_m < 0:
                    coupler_m = 0.0
                out.setdefault(str(filename).strip().lower(), (length_m, coupler_m))
    finally:
        con.close()
    return out


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "db_railvehicles.db"
    table = load_rv_lengths(path)
    print(f"Loaded {len(table)} rail-vehicle length(s) from {path}")
    for name in list(table)[:5]:
        length_m, coupler_m = table[name]
        print(f"  {name} -> length {length_m:.2f} m, coupler {coupler_m:.2f} m, "
              f"body {length_m - 2*coupler_m:.2f} m")
