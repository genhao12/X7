from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(r"D:\matlab\C\base data")
SOURCE_NAME = "20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv"
CATALOG_NAME = "23_TradeStreet_dynamic_degradation_continuous_window_catalog.csv"
SUMMARY_NAME = "24_TradeStreet_dynamic_degradation_window_summary.csv"
FMT_UTC = "%Y-%m-%dT%H:%M:%SZ"
FMT_LOCAL = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
UTC = timezone.utc
LOCAL_ZONE = ZoneInfo("America/Los_Angeles")
ELIGIBLE_STATUS = "eligible_for_building_load_dynamic_input"


def read_rows(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def utc_from_source(value: str) -> datetime:
    return datetime.strptime(value, FMT_LOCAL).replace(tzinfo=UTC)


def utc_from_catalog(value: str) -> datetime:
    return datetime.strptime(value, FMT_UTC).replace(tzinfo=UTC)


def offset_text(value: datetime) -> str:
    raw = value.strftime("%z")
    return raw[:3] + ":" + raw[3:]


source = read_rows(SOURCE_NAME)
catalog = read_rows(CATALOG_NAME)
summary = {row["metric"]: row["value"] for row in read_rows(SUMMARY_NAME)}
errors: Counter[str] = Counter()
details: list[str] = []

# Source assumptions used by the catalog.
eligible = [row for row in source if row["building_load_dynamic_admission_status"] == ELIGIBLE_STATUS]
eligible_times = [utc_from_source(row["timestamp_utc"]) for row in eligible]
if any(eligible_times[i] <= eligible_times[i - 1] for i in range(1, len(eligible_times))):
    errors["source_eligible_not_strictly_ascending"] += 1
if len(set(eligible_times)) != len(eligible_times):
    errors["source_eligible_duplicate_utc"] += 1

# Check catalog order, strict maximal non-overlap, fields, and independent endpoint arithmetic.
covered: set[datetime] = set()
previous_end: datetime | None = None
catalog_total_records = 0
bucket_count: Counter[str] = Counter()
dst_crossing_count = 0
for ordinal, row in enumerate(catalog, start=1):
    start = utc_from_catalog(row["utc_start"])
    end = utc_from_catalog(row["utc_end"])
    expected_id = f"dynamic_raw_window_{ordinal:04d}"
    record_count = int(row["record_count_15min"])
    expected_count = int((end - start) / STEP) + 1
    hours = (end - start).total_seconds() / 3600

    static_ok = (
        row["window_id"] == expected_id
        and row["source_layer"] == SOURCE_NAME
        and row["admission_status_required"] == ELIGIBLE_STATUS
        and row["timestamp_axis"] == "timestamp_utc"
        and row["contains_imputation"] == "0"
        and row["time_grid_rule"] == "strict_consecutive_15min_UTC"
        and row["timezone_evidence_status"] == "project_instruction_assumption_pending_source_confirmation"
    )
    if not static_ok:
        errors["catalog_static_fields"] += 1
        if len(details) < 8:
            details.append(f"static_fields window={row['window_id']}")
    if end < start or record_count != expected_count or abs(float(row["elapsed_hours_utc"]) - hours) > 1e-9 or abs(float(row["elapsed_days_utc"]) - hours / 24) > 5.1e-7:
        errors["endpoint_arithmetic"] += 1
        if len(details) < 8:
            details.append(f"arithmetic window={row['window_id']}; expected_count={expected_count}; listed={record_count}; hours={hours}")
    if previous_end is not None:
        gap = start - previous_end
        if gap <= timedelta(0):
            errors["catalog_overlap_or_order"] += 1
        if gap == STEP:
            errors["catalog_nonmaximal_split"] += 1
            if len(details) < 8:
                details.append(f"nonmaximal_split between previous end={previous_end} and start={start}")
    previous_end = end

    expected_window_times = {start + i * STEP for i in range(expected_count)}
    eligible_window_times = {t for t in eligible_times if start <= t <= end}
    if expected_window_times != eligible_window_times:
        errors["window_source_membership"] += 1
        if len(details) < 8:
            details.append(f"source_membership window={row['window_id']}; expected={len(expected_window_times)}; source={len(eligible_window_times)}")
    overlap = covered & expected_window_times
    if overlap:
        errors["window_overlap"] += 1
    covered.update(expected_window_times)
    catalog_total_records += record_count

    local_start = start.astimezone(LOCAL_ZONE)
    local_end = end.astimezone(LOCAL_ZONE)
    expected_offsets = ";".join(sorted({offset_text((start + i * STEP).astimezone(LOCAL_ZONE)) for i in range(expected_count)}))
    expected_dst_statuses = ";".join(sorted({"PDT_DST_active_UTC_minus_07" if (start + i * STEP).astimezone(LOCAL_ZONE).dst() != timedelta(0) else "PST_standard_UTC_minus_08" for i in range(expected_count)}))
    expected_crosses = "1" if ";" in expected_offsets else "0"
    expected_local_days = len({(start + i * STEP).astimezone(LOCAL_ZONE).strftime("%Y-%m-%d") for i in range(expected_count)})
    if (
        row["local_start"] != local_start.strftime(FMT_LOCAL)
        or row["local_end"] != local_end.strftime(FMT_LOCAL)
        or row["utc_offset_set"] != expected_offsets
        or row["dst_status_set"] != expected_dst_statuses
        or row["crosses_dst_offset_change"] != expected_crosses
        or int(row["local_calendar_day_count"]) != expected_local_days
    ):
        errors["local_dst_metadata"] += 1
        if len(details) < 8:
            details.append(f"local_dst window={row['window_id']}")
    dst_crossing_count += expected_crosses == "1"
    bucket_count["under_24h" if hours < 24 else "24h_to_under_7d" if hours < 168 else "7d_to_under_30d" if hours < 720 else "30d_or_more"] += 1

if covered != set(eligible_times):
    errors["full_source_coverage"] += 1
    if len(details) < 8:
        details.append(f"source_coverage expected={len(set(eligible_times))}; covered={len(covered)}")

longest = max(catalog, key=lambda row: int(row["record_count_15min"]))
expected_summary = {
    "source_file": SOURCE_NAME,
    "source_row_count": str(len(source)),
    "source_eligible_dynamic_rows": str(len(eligible)),
    "eligibility_status": ELIGIBLE_STATUS,
    "timestamp_axis": "timestamp_utc",
    "window_count": str(len(catalog)),
    "eligible_rows_reconstructed_from_windows": str(catalog_total_records),
    "longest_window_id": longest["window_id"],
    "longest_window_record_count_15min": longest["record_count_15min"],
    "longest_window_elapsed_hours_utc": longest["elapsed_hours_utc"],
    "longest_window_utc_start": longest["utc_start"],
    "longest_window_utc_end": longest["utc_end"],
    "longest_window_local_start": longest["local_start"],
    "longest_window_local_end": longest["local_end"],
    "windows_under_24h": str(bucket_count["under_24h"]),
    "windows_24h_to_under_7d": str(bucket_count["24h_to_under_7d"]),
    "windows_7d_to_under_30d": str(bucket_count["7d_to_under_30d"]),
    "windows_30d_or_more": str(bucket_count["30d_or_more"]),
    "windows_crossing_dst_offset_change": str(dst_crossing_count),
    "timezone_evidence_status": "project_instruction_assumption_pending_source_confirmation",
}
for key, expected in expected_summary.items():
    if summary.get(key) != expected:
        errors["summary_mismatch"] += 1
        if len(details) < 8:
            details.append(f"summary {key}: expected={expected!r}; actual={summary.get(key)!r}")

print("INDEPENDENT_DYNAMIC_WINDOW_AUDIT")
print(f"source_rows={len(source)}; eligible_rows={len(eligible)}; catalog_windows={len(catalog)}")
print(f"catalog_records={catalog_total_records}; covered_unique_eligible_utc={len(covered)}")
print(f"longest_window={longest['window_id']}; records={longest['record_count_15min']}; utc={longest['utc_start']}..{longest['utc_end']}")
print(f"duration_buckets={dict(bucket_count)}; dst_crossing_windows={dst_crossing_count}")
print(f"error_counts={dict(errors)}")
for message in details:
    print(f"DETAIL {message}")
raise SystemExit(1 if errors else 0)
