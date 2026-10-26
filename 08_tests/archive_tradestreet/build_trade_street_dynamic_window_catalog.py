from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SOURCE = BASE / "20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv"
WINDOWS_OUT = BASE / "23_TradeStreet_dynamic_degradation_continuous_window_catalog.csv"
SUMMARY_OUT = BASE / "24_TradeStreet_dynamic_degradation_window_summary.csv"
FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
ELIGIBLE = "eligible_for_building_load_dynamic_input"

with SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
    source_rows = list(csv.DictReader(handle))

eligible_rows = [
    row for row in source_rows
    if row["building_load_dynamic_admission_status"] == ELIGIBLE
]
for row in eligible_rows:
    row["_utc"] = datetime.strptime(row["timestamp_utc"], FMT)

windows: list[list[dict[str, str]]] = []
current: list[dict[str, str]] = []
for row in eligible_rows:
    if not current or row["_utc"] - current[-1]["_utc"] == STEP:
        current.append(row)
    else:
        windows.append(current)
        current = [row]
if current:
    windows.append(current)

window_rows: list[dict[str, object]] = []
for index, window in enumerate(windows, start=1):
    first, last = window[0], window[-1]
    utc_start, utc_end = first["_utc"], last["_utc"]
    local_offsets = sorted({row["utc_offset"] for row in window})
    dst_statuses = sorted({row["dst_status"] for row in window})
    elapsed_hours = (utc_end - utc_start).total_seconds() / 3600
    local_dates = {row["local_date"] for row in window}
    window_rows.append({
        "window_id": f"dynamic_raw_window_{index:04d}",
        "source_layer": SOURCE.name,
        "admission_status_required": ELIGIBLE,
        "timestamp_axis": "timestamp_utc",
        "utc_start": utc_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utc_end": utc_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local_start": first["timestamp_local"],
        "local_end": last["timestamp_local"],
        "record_count_15min": len(window),
        "elapsed_hours_utc": f"{elapsed_hours:.2f}",
        "elapsed_days_utc": f"{elapsed_hours / 24:.6f}",
        "local_calendar_day_count": len(local_dates),
        "utc_offset_set": ";".join(local_offsets),
        "dst_status_set": ";".join(dst_statuses),
        "crosses_dst_offset_change": "1" if len(local_offsets) > 1 else "0",
        "contains_imputation": "0",
        "time_grid_rule": "strict_consecutive_15min_UTC",
        "allowed_use": "dynamic_degradation_ramp_start_stop_coupling_identification",
        "timezone_evidence_status": "project_instruction_assumption_pending_source_confirmation",
    })

fields = list(window_rows[0]) if window_rows else []
with WINDOWS_OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(window_rows)

count_by_duration = Counter()
for row in window_rows:
    hours = float(row["elapsed_hours_utc"])
    if hours < 24:
        bucket = "under_24h"
    elif hours < 24 * 7:
        bucket = "24h_to_under_7d"
    elif hours < 24 * 30:
        bucket = "7d_to_under_30d"
    else:
        bucket = "30d_or_more"
    count_by_duration[bucket] += 1

longest = max(window_rows, key=lambda row: int(row["record_count_15min"]))
metrics = [
    ("rule_version", "v1.0_dynamic_degradation_raw_continuous_window_catalog"),
    ("source_file", SOURCE.name),
    ("source_row_count", str(len(source_rows))),
    ("source_eligible_dynamic_rows", str(len(eligible_rows))),
    ("eligibility_status", ELIGIBLE),
    ("timestamp_axis", "timestamp_utc"),
    ("continuity_rule", "Adjacent eligible rows must differ by exactly 15 minutes on timestamp_utc"),
    ("imputation_policy", "No imputed record is included; all windows come from original raw-complete admitted rows"),
    ("window_count", str(len(window_rows))),
    ("eligible_rows_reconstructed_from_windows", str(sum(int(row["record_count_15min"]) for row in window_rows))),
    ("longest_window_id", longest["window_id"]),
    ("longest_window_record_count_15min", str(longest["record_count_15min"])),
    ("longest_window_elapsed_hours_utc", longest["elapsed_hours_utc"]),
    ("longest_window_utc_start", longest["utc_start"]),
    ("longest_window_utc_end", longest["utc_end"]),
    ("longest_window_local_start", longest["local_start"]),
    ("longest_window_local_end", longest["local_end"]),
    ("windows_under_24h", str(count_by_duration["under_24h"])),
    ("windows_24h_to_under_7d", str(count_by_duration["24h_to_under_7d"])),
    ("windows_7d_to_under_30d", str(count_by_duration["7d_to_under_30d"])),
    ("windows_30d_or_more", str(count_by_duration["30d_or_more"])),
    ("windows_crossing_dst_offset_change", str(sum(row["crosses_dst_offset_change"] == "1" for row in window_rows))),
    ("timezone_evidence_status", "project_instruction_assumption_pending_source_confirmation"),
]
with SUMMARY_OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["metric", "value"])
    writer.writerows(metrics)

print(f"eligible_rows={len(eligible_rows)}")
print(f"window_count={len(window_rows)}")
print(f"longest_window={longest['window_id']}; rows={longest['record_count_15min']}; hours={longest['elapsed_hours_utc']}")
