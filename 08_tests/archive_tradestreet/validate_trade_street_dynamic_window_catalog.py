from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
FMT = "%Y-%m-%dT%H:%M:%SZ"
SOURCE_FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
ELIGIBLE = "eligible_for_building_load_dynamic_input"


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

source = load("20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv")
catalog = load("23_TradeStreet_dynamic_degradation_continuous_window_catalog.csv")
summary = {row["metric"]: row["value"] for row in load("24_TradeStreet_dynamic_degradation_window_summary.csv")}
eligible = [row for row in source if row["building_load_dynamic_admission_status"] == ELIGIBLE]
source_by_utc = {row["timestamp_utc"]: row for row in eligible}
failures: Counter[str] = Counter()
issues: list[str] = []

# Reconstruct windows independently from source eligibility.
expected_windows: list[list[dict[str, str]]] = []
current: list[dict[str, str]] = []
for row in eligible:
    timestamp = datetime.strptime(row["timestamp_utc"], SOURCE_FMT)
    if not current:
        current = [row]
    else:
        prior = datetime.strptime(current[-1]["timestamp_utc"], SOURCE_FMT)
        if timestamp - prior == STEP:
            current.append(row)
        else:
            expected_windows.append(current)
            current = [row]
if current:
    expected_windows.append(current)

if len(expected_windows) != len(catalog):
    failures["window_count"] += 1
    issues.append(f"expected_windows={len(expected_windows)}; catalog_windows={len(catalog)}")

covered: set[str] = set()
for index, (expected, actual) in enumerate(zip(expected_windows, catalog), start=1):
    first, last = expected[0], expected[-1]
    start = datetime.strptime(actual["utc_start"], FMT)
    end = datetime.strptime(actual["utc_end"], FMT)
    expected_start = datetime.strptime(first["timestamp_utc"], SOURCE_FMT)
    expected_end = datetime.strptime(last["timestamp_utc"], SOURCE_FMT)
    expected_id = f"dynamic_raw_window_{index:04d}"
    expected_count = len(expected)
    expected_elapsed = (expected_end - expected_start).total_seconds() / 3600
    expected_offsets = ";".join(sorted({row["utc_offset"] for row in expected}))
    expected_dst = ";".join(sorted({row["dst_status"] for row in expected}))
    expected_crosses = "1" if len({row["utc_offset"] for row in expected}) > 1 else "0"

    checks = {
        "window_id": actual["window_id"] == expected_id,
        "start_end": start == expected_start and end == expected_end,
        "counts": int(actual["record_count_15min"]) == expected_count,
        "elapsed": abs(float(actual["elapsed_hours_utc"]) - expected_elapsed) < 1e-9,
        "local": actual["local_start"] == first["timestamp_local"] and actual["local_end"] == last["timestamp_local"],
        "offsets": actual["utc_offset_set"] == expected_offsets and actual["dst_status_set"] == expected_dst and actual["crosses_dst_offset_change"] == expected_crosses,
        "policy": actual["timestamp_axis"] == "timestamp_utc" and actual["contains_imputation"] == "0" and actual["time_grid_rule"] == "strict_consecutive_15min_UTC" and actual["admission_status_required"] == ELIGIBLE,
    }
    for name, good in checks.items():
        if not good:
            failures[f"catalog_{name}"] += 1
            if len(issues) < 10:
                issues.append(f"window={index}; check={name}; actual={actual}")
    for row in expected:
        timestamp = row["timestamp_utc"]
        if timestamp in covered:
            failures["overlap"] += 1
            if len(issues) < 10:
                issues.append(f"duplicate_covered_timestamp={timestamp}")
        covered.add(timestamp)

if covered != set(source_by_utc):
    failures["coverage"] += 1
    missing = sorted(set(source_by_utc) - covered)[:3]
    unexpected = sorted(covered - set(source_by_utc))[:3]
    issues.append(f"coverage_missing={missing}; coverage_unexpected={unexpected}")

# Check all windows themselves are strictly 15-min implied by endpoints/count.
for actual in catalog:
    start = datetime.strptime(actual["utc_start"], FMT)
    end = datetime.strptime(actual["utc_end"], FMT)
    expected_count = int((end - start).total_seconds() / 900) + 1
    if int(actual["record_count_15min"]) != expected_count:
        failures["endpoint_count_consistency"] += 1
        if len(issues) < 10:
            issues.append(f"window={actual['window_id']}; endpoints imply {expected_count}; listed={actual['record_count_15min']}")

longest = max(catalog, key=lambda row: int(row["record_count_15min"]))
bucket_counts = Counter()
for row in catalog:
    hours = float(row["elapsed_hours_utc"])
    bucket_counts["under_24h" if hours < 24 else "24h_to_under_7d" if hours < 168 else "7d_to_under_30d" if hours < 720 else "30d_or_more"] += 1
expected_summary = {
    "source_row_count": str(len(source)),
    "source_eligible_dynamic_rows": str(len(eligible)),
    "window_count": str(len(catalog)),
    "eligible_rows_reconstructed_from_windows": str(sum(int(row["record_count_15min"]) for row in catalog)),
    "longest_window_id": longest["window_id"],
    "longest_window_record_count_15min": longest["record_count_15min"],
    "longest_window_elapsed_hours_utc": longest["elapsed_hours_utc"],
    "longest_window_utc_start": longest["utc_start"],
    "longest_window_utc_end": longest["utc_end"],
    "longest_window_local_start": longest["local_start"],
    "longest_window_local_end": longest["local_end"],
    "windows_under_24h": str(bucket_counts["under_24h"]),
    "windows_24h_to_under_7d": str(bucket_counts["24h_to_under_7d"]),
    "windows_7d_to_under_30d": str(bucket_counts["7d_to_under_30d"]),
    "windows_30d_or_more": str(bucket_counts["30d_or_more"]),
    "windows_crossing_dst_offset_change": str(sum(row["crosses_dst_offset_change"] == "1" for row in catalog)),
}
for metric, expected in expected_summary.items():
    if summary.get(metric) != expected:
        failures["summary"] += 1
        if len(issues) < 10:
            issues.append(f"summary {metric}: expected={expected}; actual={summary.get(metric)}")

print("STRICT_DYNAMIC_WINDOW_CATALOG_VALIDATION")
print(f"source_rows={len(source)}; eligible_rows={len(eligible)}")
print(f"windows={len(catalog)}; reconstructed_rows={len(covered)}")
print(f"longest={longest['window_id']}; rows={longest['record_count_15min']}; elapsed_hours={longest['elapsed_hours_utc']}")
print(f"bucket_counts={dict(bucket_counts)}")
print(f"failure_counts={dict(failures)}")
for issue in issues:
    print(f"DETAIL {issue}")
raise SystemExit(1 if failures else 0)
