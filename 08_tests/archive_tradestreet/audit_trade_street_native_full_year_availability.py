from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SOURCE = BASE / "34_TradeStreet_15min_building_load_local_civil_primary_UTC_mapping_layer.csv"
FMT = "%Y-%m-%d %H:%M:%S"
ELIGIBLE = "eligible_for_building_load_dynamic_input"
STEP = timedelta(minutes=15)

with SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

eligible = [
    row for row in rows
    if row["building_load_dynamic_admission_status"] == ELIGIBLE
    and row["utc_mapping_admission_status"].startswith("eligible_")
]
by_year: dict[int, list[dict[str, str]]] = {}
for row in eligible:
    year = datetime.strptime(row["timestamp_local_civil"], FMT).year
    by_year.setdefault(year, []).append(row)

print("NATIVE_FULL_YEAR_AVAILABILITY_AUDIT")
print(f"source_rows={len(rows)}; eligible_rows={len(eligible)}")
for year in sorted(by_year):
    year_rows = by_year[year]
    times = [datetime.strptime(row["timestamp_local_civil"], FMT) for row in year_rows]
    gaps = Counter((right - left).total_seconds() / 60 for left, right in zip(times, times[1:]))
    expected = 35136 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 35040
    print(
        f"year={year}; eligible_points={len(year_rows)}; expected_native_points={expected}; "
        f"first={times[0]}; last={times[-1]}; consecutive_15min_pairs={gaps[15.0]}; non15min_pair_types={dict((k,v) for k,v in gaps.items() if k != 15.0)}"
    )
print("formal_evaluation_requirement=Each selected actual year must be complete on its native local-civil calendar; nonleap years require 35,040 source points and leap years require 35,136 source points before DST mapping exclusions/ambiguities are assessed separately.")
