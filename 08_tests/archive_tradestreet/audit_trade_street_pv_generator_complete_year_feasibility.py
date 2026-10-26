from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
OUT = BASE / "57_TradeStreet_PythonPVGenerator_complete_year_reconstruction_feasibility_audit.csv"
SUMMARY = BASE / "58_TradeStreet_PythonPVGenerator_complete_year_reconstruction_feasibility_summary.csv"
FMT = "%m/%d/%Y %H:%M"
STEP = timedelta(minutes=15)


def read_series(filename: str) -> tuple[dict[datetime, list[float]], int, int]:
    values: dict[datetime, list[float]] = {}
    bad_time = 0
    bad_value = 0
    with (RAW / filename).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                stamp = datetime.strptime(row["DateTime"].strip(), FMT)
            except (KeyError, ValueError):
                bad_time += 1
                continue
            try:
                value = float(row["RealPower"])
            except (KeyError, ValueError):
                bad_value += 1
                continue
            values.setdefault(stamp, []).append(value)
    return values, bad_time, bad_value


def expected_grid(year: int) -> list[datetime]:
    first = datetime(year, 1, 1)
    final = datetime(year + 1, 1, 1)
    grid: list[datetime] = []
    current = first
    while current < final:
        grid.append(current)
        current += STEP
    return grid


def pv_generator_can_fill(stamp: datetime, pv_present: bool, pv_start: datetime, pv_end: datetime) -> bool:
    """Exact scope capability based on the supplied script: only inserts a missing point inside input first/last timestamps."""
    return (not pv_present) and pv_start <= stamp <= pv_end

series = {}
quality = {}
for label, filename in {
    "total": "TradeStreetTotal.csv",
    "pv": "TradeStreetPV.csv",
    "bess": "TradeStreetBattery.csv",
}.items():
    data, bad_time, bad_value = read_series(filename)
    series[label] = data
    quality[label] = {"bad_time_rows": bad_time, "bad_value_rows": bad_value}

bounds = {
    label: (min(values), max(values))
    for label, values in series.items()
}
all_years = range(min(stamp.year for values in series.values() for stamp in values), max(stamp.year for values in series.values() for stamp in values) + 1)
rows: list[dict[str, object]] = []
summary: list[dict[str, object]] = []

for year in all_years:
    grid = expected_grid(year)
    counts: Counter[str] = Counter()
    total_start, total_end = bounds["total"]
    pv_start, pv_end = bounds["pv"]
    bess_start, bess_end = bounds["bess"]
    for stamp in grid:
        total_present = stamp in series["total"] and len(series["total"][stamp]) == 1
        pv_present = stamp in series["pv"] and len(series["pv"][stamp]) == 1
        bess_present = stamp in series["bess"] and len(series["bess"][stamp]) == 1
        pv_fillable = pv_generator_can_fill(stamp, pv_present, pv_start, pv_end)
        common_raw = total_present and pv_present and bess_present
        common_after_pv_generator = total_present and (pv_present or pv_fillable) and bess_present
        if common_raw:
            category = "common_raw_unique"
        elif common_after_pv_generator:
            category = "common_after_PythonPVGenerator_PV_fill_only"
        elif not (bess_start <= stamp <= bess_end):
            category = "blocked_BESS_outside_observation_range"
        elif not (total_start <= stamp <= total_end):
            category = "blocked_Total_outside_observation_range"
        elif not (pv_start <= stamp <= pv_end):
            category = "blocked_PV_outside_observation_range"
        elif not bess_present:
            category = "blocked_BESS_missing_or_duplicate_not_handled_by_PythonPVGenerator"
        elif not total_present:
            category = "blocked_Total_missing_or_duplicate_not_handled_by_PythonPVGenerator"
        else:
            category = "blocked_PV_nonunique_or_unclassified"
        counts[category] += 1

    expected = len(grid)
    raw_common = counts["common_raw_unique"]
    pv_generator_common = raw_common + counts["common_after_PythonPVGenerator_PV_fill_only"]
    complete_raw = raw_common == expected
    complete_after = pv_generator_common == expected
    summary.append({
        "local_civil_year": year,
        "expected_15min_points": expected,
        "raw_common_unique_points": raw_common,
        "points_gained_by_PythonPVGenerator_PV_missing_zero_insertion_scope": counts["common_after_PythonPVGenerator_PV_fill_only"],
        "common_points_after_PythonPVGenerator_scope": pv_generator_common,
        "complete_actual_year_before_fill": complete_raw,
        "complete_year_after_PythonPVGenerator_scope": complete_after,
        "blocking_points_after_PythonPVGenerator_scope": expected - pv_generator_common,
        "primary_blocker": max((key for key in counts if key.startswith("blocked_")), key=lambda key: counts[key], default="none"),
        "decision": "not_a_complete_actual_year" if not complete_after else "complete_grid_but_not_native_actual_year_due_to_imputation",
    })
    for category, count in sorted(counts.items()):
        rows.append({
            "local_civil_year": year,
            "expected_15min_points": expected,
            "category": category,
            "point_count": count,
            "share_of_year": f"{count / expected:.12f}",
            "PythonPVGenerator_scope_interpretation": (
                "PV-only missing-timestamp zero insertion then possible correction; no load/BESS fill, no range extension, no provenance output"
                if category == "common_after_PythonPVGenerator_PV_fill_only"
                else "not_applicable"
            ),
        })

with OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

metadata = [
    {"item": "audit_scope", "value": "raw TradeStreetTotal/PV/Battery timestamp availability; no values changed"},
    {"item": "PythonPVGenerator_input_scope", "value": "single PV series only; missing timestamps inserted as zero strictly inside input first/last timestamp range"},
    {"item": "PythonPVGenerator_unsupported_requirements", "value": "does not fill Total/load or BESS; does not extend observation range; does not retain row-level fill provenance; does not handle DST policy"},
    {"item": "actual_year_definition", "value": "native observed annual chronology; imputed annual grid is not an actual observed year"},
    {"item": "total_observation_range", "value": f"{bounds['total'][0]} to {bounds['total'][1]}"},
    {"item": "pv_observation_range", "value": f"{bounds['pv'][0]} to {bounds['pv'][1]}"},
    {"item": "bess_observation_range", "value": f"{bounds['bess'][0]} to {bounds['bess'][1]}"},
]
for label in ("total", "pv", "bess"):
    metadata.append({"item": f"{label}_raw_parse_quality", "value": str(quality[label])})
for row in summary:
    metadata.append({"item": f"year_{row['local_civil_year']}_result", "value": str(row)})

with SUMMARY.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["item", "value"])
    writer.writeheader()
    writer.writerows(metadata)

print("PYTHON_PV_GENERATOR_COMPLETE_YEAR_FEASIBILITY_AUDIT")
for row in summary:
    print(row)
print(f"detail_output={OUT}")
print(f"summary_output={SUMMARY}")
