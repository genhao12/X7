from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
FMT_UTC = "%Y-%m-%dT%H:%M:%SZ"
FMT_SOURCE = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
EXPECTED = {
    "benchmark_24h": (24, 97, "27_TradeStreet_dynamic_degradation_benchmark_24h.csv"),
    "benchmark_7d": (168, 673, "28_TradeStreet_dynamic_degradation_benchmark_7d.csv"),
    "benchmark_35d": (840, 3361, "29_TradeStreet_dynamic_degradation_benchmark_35d.csv"),
}


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


registry = load("25_TradeStreet_dynamic_degradation_test_window_registry.csv")
source = load("20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv")
summary = {r["metric"]: r["value"] for r in load("26_TradeStreet_dynamic_degradation_test_window_summary.csv")}
source_by_utc = {r["timestamp_utc"]: r for r in source}
errors = []
exports: dict[str, list[dict[str, str]]] = {}
for entry in registry:
    test_id = entry["test_window_id"]
    hours, expected_count, filename = EXPECTED[test_id]
    rows = load(filename)
    exports[test_id] = rows
    times = [datetime.strptime(r["timestamp_utc"], FMT_SOURCE) for r in rows]
    start = datetime.strptime(entry["utc_start"], FMT_UTC)
    end = datetime.strptime(entry["utc_end"], FMT_UTC)
    checks = [
        len(rows) == expected_count == int(entry["record_count_15min"]),
        times[0] == start and times[-1] == end,
        all(times[i] - times[i - 1] == STEP for i in range(1, len(times))),
        (times[-1] - times[0]).total_seconds() / 3600 == hours == float(entry["actual_elapsed_hours_utc"]),
        all(r["building_load_dynamic_admission_status"] == "eligible_for_building_load_dynamic_input" for r in rows),
        all(r["timestamp_utc"] in source_by_utc and source_by_utc[r["timestamp_utc"]] == r for r in rows),
        entry["contains_imputation"] == "0" and entry["crosses_dst_offset_change"] == "0",
    ]
    if not all(checks):
        errors.append(f"{test_id}: checks={checks}")

# Nested design must be exact prefixes, not merely overlapping periods.
for shorter, longer in (("benchmark_24h", "benchmark_7d"), ("benchmark_7d", "benchmark_35d")):
    if exports[shorter] != exports[longer][:len(exports[shorter])]:
        errors.append(f"{shorter} is not an exact prefix of {longer}")

expected_summary = {
    "selected_source_window_id": "dynamic_raw_window_0267",
    "benchmark_count": "3",
    "timestamp_axis": "timestamp_utc",
    "imputation_policy": "No imputed values; all exported rows require eligible_for_building_load_dynamic_input",
    "dst_policy": "No selected segment crosses a DST offset change",
}
for key, value in expected_summary.items():
    if summary.get(key) != value:
        errors.append(f"summary {key}: expected={value!r}; actual={summary.get(key)!r}")

print("STRICT_DYNAMIC_TEST_WINDOW_VALIDATION")
print("registry_rows=", len(registry))
print("export_rows=", {key: len(value) for key, value in exports.items()})
print("errors=", errors)
raise SystemExit(1 if errors else 0)
