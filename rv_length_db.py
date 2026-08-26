#!/usr/bin/env python3
"""
Rail-vehicle info lookup from the Run8 rolling-stock database.

`db_railvehicles.db` is a SQLite database whose `loco_data` and `car_data`
tables carry one row per rail-vehicle definition, keyed by `R8_FILENAME` (the
same value a world save stores as `rvXMLfilename`). We read three columns:

* ``RV_LENGTH`` (ft)     - the vehicle's *coupled* footprint (over pulling faces).
                     Run8 spaces coupled cars so their ``RV_LENGTH`` extents abut,
                     so drawing this length leaves no visible gap between cars.
* ``COUPLER_OFFSET`` (ft) - the per-end coupler length. Subtracting it from each end
                     gives the car body *between* the couplers, which restores a
                     realistic gap (the coupler region) between adjacent cars.
* ``INDUSTRY_CONFIG_CAR_TYPE`` - a broad car category (Box_Car, Tank_Car,
                     Covered_Hopper, ...), used to colour the body by car type.
                     `loco_data` has no such column; its rows get ``Locomotive``.

`load_rv_lengths()` returns a dict mapping the lower-cased filename to an
:class:`RvInfo` (``length_m``, ``coupler_m``, ``car_type``), merging both tables
(the `*_BACKUP_*` tables are ignored). The caller draws the body at
``length_m - 2 * coupler_m`` and colours it by ``car_type``.
"""

import sqlite3
from collections import namedtuple
from typing import Dict

FEET_TO_METERS = 0.3048

# Active tables (ignore the dated *_BACKUP_* copies).
_LENGTH_TABLES = ("loco_data", "car_data")

RvInfo = namedtuple("RvInfo", "length_m coupler_m car_type")


def load_rv_lengths(db_path: str) -> Dict[str, RvInfo]:
    """Load {r8_filename.lower(): RvInfo(length_m, coupler_m, car_type)} from the DB.

    Rows with a missing filename or a non-positive length are skipped. A missing
    coupler offset becomes 0.0 (body drawn at the full coupled length). Loco rows
    have car_type ``"Locomotive"``; a car row with a null category gets ``"None"``.
    If a filename appears in more than one table the first (loco) value wins; in
    practice the two tables are disjoint.
    """
    out: Dict[str, RvInfo] = {}
    con = sqlite3.connect(db_path)
    try:
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in _LENGTH_TABLES:
            if table not in have:
                continue
            is_loco = (table == "loco_data")
            cols = "R8_FILENAME, RV_LENGTH, COUPLER_OFFSET"
            if not is_loco:
                cols += ", INDUSTRY_CONFIG_CAR_TYPE"
            for row in con.execute(f'SELECT {cols} FROM "{table}"'):
                filename, length_ft, coupler_ft = row[0], row[1], row[2]
                car_type = "Locomotive" if is_loco else (row[3] or "None")
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
                out.setdefault(str(filename).strip().lower(),
                               RvInfo(length_m, coupler_m, str(car_type)))
    finally:
        con.close()
    return out


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "db_railvehicles.db"
    table = load_rv_lengths(path)
    print(f"Loaded {len(table)} rail-vehicle record(s) from {path}")
    for name in list(table)[:5]:
        info = table[name]
        print(f"  {name} -> length {info.length_m:.2f} m, coupler {info.coupler_m:.2f} m, "
              f"body {info.length_m - 2*info.coupler_m:.2f} m, type {info.car_type}")
