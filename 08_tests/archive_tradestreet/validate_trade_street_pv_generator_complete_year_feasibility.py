from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
DETAIL = BASE / "57_TradeStreet_PythonPVGenerator_complete_year_reconstruction_feasibility_audit.csv"
FMT = "%m/%d/%Y %H:%M"
STEP = timedelta(minutes=15)


def timestamps(name: str) -> set[datetime]:
    result: set[datetime] = set()
    with (RAW / name).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                stamp = datetime.strptime(row["DateTime"].strip(), FMT)
                float(row["RealPower"])
            except (KeyError, ValueError):
                continue
            if stamp in result:
                result.remove(stamp)  # Mark duplicates invalid by removing from the unique-time set.
            else:
                result.add(stamp)
    return result

# Independent availability calculation; no import from the audit script.
total = timestamps("TradeStreetTotal.csv")
pv = timestamps("TradeStreetPV.csv")
bess = timestamps("TradeStreetBattery.csv")
pv_first, pv_last = min(pv), max(pv)
expected: dict[int, dict[str, int]] = {}
for year in range(2016, 2021):
    start, stop = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    stamp = start
    raw = fill_only = 0
    while stamp < stop:
        total_ok, pv_ok, bess_ok = stamp in total, stamp in pv, stamp in bess
        if total_ok and pv_ok and bess_ok:
            raw += 1
        elif total_ok and bess_ok and (not pv_ok) and pv_first <= stamp <= pv_last:
            fill_only += 1
        stamp += STEP
    expected[year] = {"common_raw_unique": raw, "common_after_PythonPVGenerator_PV_fill_only": fill_only}

actual: dict[int, dict[str, int]] = {}
with DETAIL.open("r", encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle):
        year = int(row["local_civil_year"])
        actual.setdefault(year, {})[row["category"]] = int(row["point_count"])

errors = []
for year, categories in expected.items():
    for category, count in categories.items():
        observed = actual.get(year, {}).get(category, 0)
        if observed != count:
            errors.append(f"year={year}; category={category}; expected={count}; observed={observed}")
    expected_grid = 35136 if year in {2016, 2020} else 35040
    reconstructed = categories["common_raw_unique"] + categories["common_after_PythonPVGenerator_PV_fill_only"]
    if reconstructed >= expected_grid:
        errors.append(f"year={year}; impossible_complete_result={reconstructed}")

print("PYTHON_PV_GENERATOR_COMPLETE_YEAR_FEASIBILITY_VALIDATION")
print(f"years_checked={len(expected)}")
print(f"errors={len(errors)}")
for error in errors:
    print(error)
if errors:
    raise SystemExit(1)
print("result=PASS; PythonPVGenerator PV-only scope cannot create any complete common Total-PV-BESS annual chronology from supplied files")
