from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(r"D:\matlab\C\base data")
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
DATA_FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
LA = ZoneInfo("America/Los_Angeles")
EXPECTED = {
    "benchmark_24h": (24, 97, "27_TradeStreet_dynamic_degradation_benchmark_24h.csv"),
    "benchmark_7d": (168, 673, "28_TradeStreet_dynamic_degradation_benchmark_7d.csv"),
    "benchmark_35d": (840, 3361, "29_TradeStreet_dynamic_degradation_benchmark_35d.csv"),
}


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


registry = load("25_TradeStreet_dynamic_degradation_test_window_registry.csv")
summary = {x["metric"]: x["value"] for x in load("26_TradeStreet_dynamic_degradation_test_window_summary.csv")}
source = load("20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv")
source_map = {r["timestamp_utc"]: r for r in source}
errors: list[str] = []
exports: dict[str, list[dict[str, str]]] = {}

if set(r["test_window_id"] for r in registry) != set(EXPECTED) or len(registry) != 3:
    errors.append("Registry must contain exactly the expected three unique test IDs")

for reg in registry:
    test_id = reg["test_window_id"]
    if test_id not in EXPECTED:
        continue
    hours, count, filename = EXPECTED[test_id]
    data = load(filename)
    exports[test_id] = data
    times = [datetime.strptime(r["timestamp_utc"], DATA_FMT) for r in data]
    start = datetime.strptime(reg["utc_start"], UTC_FMT)
    end = datetime.strptime(reg["utc_end"], UTC_FMT)
    checks = {
        "registration": reg["export_file"] == filename and reg["source_window_id"] == "dynamic_raw_window_0267",
        "row_count": len(data) == count == int(reg["record_count_15min"]),
        "grid": all(times[i] - times[i - 1] == STEP for i in range(1, len(times))),
        "endpoints": times[0] == start and times[-1] == end,
        "duration": (end - start).total_seconds() / 3600 == hours == float(reg["actual_elapsed_hours_utc"]),
        "source_mapping": all(source_map.get(r["timestamp_utc"]) == r for r in data),
        "admission": all(r["building_load_dynamic_admission_status"] == "eligible_for_building_load_dynamic_input" for r in data),
        "flags": all(r["total_outlier_candidate_flag"] == "0" and r["pv_outlier_candidate_flag"] == "0" and r["bess_rating_screen_flag"] == "0" for r in data),
        "bess_rating": all(abs(float(r["p_bess_raw_kW_positive_charge_negative_discharge"])) <= 200 for r in data),
        "power_closure": all(abs(float(r["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"]) - float(r["p_net_raw_kW"]) - float(r["p_pv_model_kW_nonnegative"]) + float(r["p_bess_raw_kW_positive_charge_negative_discharge"])) <= 1e-9 for r in data),
    }
    local_start = datetime.strptime(data[0]["timestamp_local"], DATA_FMT).replace(tzinfo=LA)
    local_end = datetime.strptime(data[-1]["timestamp_local"], DATA_FMT).replace(tzinfo=LA)
    checks["local_metadata"] = reg["local_start"] == data[0]["timestamp_local"] and reg["local_end"] == data[-1]["timestamp_local"] and local_start.utcoffset() == local_end.utcoffset() == timedelta(hours=-8)
    failed = [key for key, valid in checks.items() if not valid]
    if failed:
        errors.append(f"{test_id}: {failed}")

for short, long in (("benchmark_24h", "benchmark_7d"), ("benchmark_7d", "benchmark_35d")):
    if exports.get(short) != exports.get(long, [])[:len(exports.get(short, []))]:
        errors.append(f"Nested-prefix relation failed: {short} -> {long}")

required_summary = {
    "selected_source_window_id": "dynamic_raw_window_0267",
    "selected_source_window_record_count_15min": "3362",
    "benchmark_count": "3",
    "timestamp_axis": "timestamp_utc",
    "dst_policy": "No selected segment crosses a DST offset change",
    "imputation_policy": "No imputed values; all exported rows require eligible_for_building_load_dynamic_input",
}
for key, expected in required_summary.items():
    if summary.get(key) != expected:
        errors.append(f"Summary mismatch {key}: expected={expected!r}; actual={summary.get(key)!r}")

print("INDEPENDENT_BENCHMARK_25_29_AUDIT")
print("registry_rows=", len(registry))
print("export_row_counts=", {key: len(value) for key, value in exports.items()})
print("errors=", errors)
raise SystemExit(1 if errors else 0)
