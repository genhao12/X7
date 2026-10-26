from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
CATALOG = BASE / "23_TradeStreet_dynamic_degradation_continuous_window_catalog.csv"
SOURCE = BASE / "20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv"
REGISTRY = BASE / "25_TradeStreet_dynamic_degradation_test_window_registry.csv"
SUMMARY = BASE / "26_TradeStreet_dynamic_degradation_test_window_summary.csv"
FMT_UTC = "%Y-%m-%dT%H:%M:%SZ"
FMT_SOURCE = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
SOURCE_WINDOW_ID = "dynamic_raw_window_0267"
ELIGIBLE = "eligible_for_building_load_dynamic_input"

# Inclusive endpoint counts: 24h=97, 7d=673, 35d=3361 at 15-minute spacing.
TESTS = [
    ("benchmark_24h", 24, 97, "27_TradeStreet_dynamic_degradation_benchmark_24h.csv"),
    ("benchmark_7d", 168, 673, "28_TradeStreet_dynamic_degradation_benchmark_7d.csv"),
    ("benchmark_35d", 840, 3361, "29_TradeStreet_dynamic_degradation_benchmark_35d.csv"),
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


catalog = read_rows(CATALOG)
source = read_rows(SOURCE)
selected = next(row for row in catalog if row["window_id"] == SOURCE_WINDOW_ID)
if selected["crosses_dst_offset_change"] != "0" or selected["contains_imputation"] != "0":
    raise ValueError("Selected source window violates no-DST-change or no-imputation test constraint")

window_start = datetime.strptime(selected["utc_start"], FMT_UTC)
window_end = datetime.strptime(selected["utc_end"], FMT_UTC)
source_by_time = {datetime.strptime(row["timestamp_utc"], FMT_SOURCE): row for row in source}
window_rows = []
current = window_start
while current <= window_end:
    row = source_by_time.get(current)
    if row is None:
        raise ValueError(f"Missing source timestamp in catalog window: {current}")
    if row["building_load_dynamic_admission_status"] != ELIGIBLE:
        raise ValueError(f"Noneligible source record inside catalog window: {current}")
    window_rows.append(row)
    current += STEP
if len(window_rows) != int(selected["record_count_15min"]):
    raise ValueError("Catalog row count does not match source rows")

registry_rows = []
for test_id, duration_hours, expected_rows, filename in TESTS:
    rows = window_rows[:expected_rows]
    if len(rows) != expected_rows:
        raise ValueError(f"Insufficient rows for {test_id}")
    first, last = rows[0], rows[-1]
    start = datetime.strptime(first["timestamp_utc"], FMT_SOURCE)
    end = datetime.strptime(last["timestamp_utc"], FMT_SOURCE)
    actual_hours = (end - start).total_seconds() / 3600
    if actual_hours != duration_hours:
        raise ValueError(f"Duration mismatch for {test_id}: {actual_hours}")
    with (BASE / filename).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(first))
        writer.writeheader()
        writer.writerows(rows)
    registry_rows.append({
        "test_window_id": test_id,
        "benchmark_design": "nested_duration_benchmark_same_UTC_start",
        "source_window_id": SOURCE_WINDOW_ID,
        "source_catalog_file": CATALOG.name,
        "source_data_file": SOURCE.name,
        "export_file": filename,
        "timestamp_axis": "timestamp_utc",
        "utc_start": start.strftime(FMT_UTC),
        "utc_end": end.strftime(FMT_UTC),
        "local_start": first["timestamp_local"],
        "local_end": last["timestamp_local"],
        "target_elapsed_hours_utc": str(duration_hours),
        "actual_elapsed_hours_utc": f"{actual_hours:.2f}",
        "record_count_15min": str(len(rows)),
        "inclusive_endpoint_rule": "record_count=elapsed_hours*4+1",
        "crosses_dst_offset_change": "0",
        "contains_imputation": "0",
        "source_admission_status": ELIGIBLE,
        "selection_rule": "Use the first exact-duration UTC segment of the longest no-DST-change original-complete admitted window; nested segments intentionally isolate duration effects and are not independent holdout samples",
        "allowed_use": "dynamic_degradation_ramp_start_stop_coupling_identification_duration_benchmark",
        "not_allowed_use": "independent_train_test_generalization_claim",
        "timezone_evidence_status": "project_instruction_assumption_pending_source_confirmation",
    })

with REGISTRY.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(registry_rows[0]))
    writer.writeheader()
    writer.writerows(registry_rows)

summary_rows = [
    ("rule_version", "v1.0_nested_duration_benchmark_window_freeze"),
    ("source_catalog_file", CATALOG.name),
    ("selected_source_window_id", SOURCE_WINDOW_ID),
    ("selected_source_window_record_count_15min", selected["record_count_15min"]),
    ("selected_source_window_elapsed_hours_utc", selected["elapsed_hours_utc"]),
    ("source_window_selection", "Longest original-complete admitted UTC window with no DST offset change"),
    ("benchmark_design", "Nested 24h, 7d and 35d segments share one UTC start to isolate duration effects; not independent holdout samples"),
    ("benchmark_count", str(len(registry_rows))),
    ("imputation_policy", "No imputed values; all exported rows require eligible_for_building_load_dynamic_input"),
    ("dst_policy", "No selected segment crosses a DST offset change"),
    ("timestamp_axis", "timestamp_utc"),
    ("timezone_evidence_status", "project_instruction_assumption_pending_source_confirmation"),
]
with SUMMARY.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["metric", "value"])
    writer.writerows(summary_rows)

print(f"source_window={SOURCE_WINDOW_ID}; source_rows={len(window_rows)}")
for row in registry_rows:
    print(f"{row['test_window_id']}; rows={row['record_count_15min']}; {row['utc_start']}..{row['utc_end']}")
