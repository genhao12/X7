from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
OUT = BASE / "59_TradeStreet_2016_2018_reconstructed_year_gap_structure_audit.csv"
FMT = "%m/%d/%Y %H:%M"
STEP = timedelta(minutes=15)
YEARS = (2016, 2017, 2018)


def series(filename: str) -> dict[datetime, list[float]]:
    result: dict[datetime, list[float]] = {}
    with (RAW / filename).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                stamp = datetime.strptime(row["DateTime"].strip(), FMT)
                value = float(row["RealPower"])
            except (KeyError, ValueError):
                continue
            result.setdefault(stamp, []).append(value)
    return result


def grid(year: int) -> list[datetime]:
    current, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    result = []
    while current < end:
        result.append(current)
        current += STEP
    return result


def contiguous_runs(missing: list[datetime]) -> list[int]:
    if not missing:
        return []
    lengths, start, previous = [], missing[0], missing[0]
    for stamp in missing[1:]:
        if stamp - previous != STEP:
            lengths.append(int((previous - start) / STEP) + 1)
            start = stamp
        previous = stamp
    lengths.append(int((previous - start) / STEP) + 1)
    return lengths

sources = {
    "p_net_kW": series("TradeStreetTotal.csv"),
    "p_pv_kW": series("TradeStreetPV.csv"),
    "p_bess_observed_kW": series("TradeStreetBattery.csv"),
}
rows = []
for year in YEARS:
    expected = len(grid(year))
    for variable, observations in sources.items():
        missing = [stamp for stamp in grid(year) if len(observations.get(stamp, [])) != 1]
        runs = contiguous_runs(missing)
        existing = expected - len(missing)
        outside_first_last = 0
        if observations:
            first, last = min(observations), max(observations)
            outside_first_last = sum(not (first <= stamp <= last) for stamp in missing)
        rows.append({
            "local_civil_year": year,
            "variable": variable,
            "expected_15min_points": expected,
            "unique_observed_points": existing,
            "missing_or_duplicate_points": len(missing),
            "missing_share": f"{len(missing) / expected:.12f}",
            "missing_run_count": len(runs),
            "longest_missing_run_points": max(runs, default=0),
            "longest_missing_run_days": f"{max(runs, default=0) / 96:.6f}",
            "missing_points_outside_series_first_last_range": outside_first_last,
            "eligible_short_gap_rule": "same_type_same_clock_donor_only_for_short_within_range_gaps" if variable != "p_bess_observed_kW" else "not_applicable_BESS_long_gaps_use_constrained_optimization_reconstruction",
            "reconstruction_class": "observed_or_joint_donor_fill" if variable != "p_bess_observed_kW" else "observed_or_constrained_EMS_reconstruction",
        })

with OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

print("RECONSTRUCTED_YEAR_GAP_STRUCTURE_AUDIT")
for row in rows:
    print(row)
print(f"output={OUT}")
