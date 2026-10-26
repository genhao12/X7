from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
ELIGIBLE = "eligible_for_building_load_dynamic_input"
TESTS = [
    ("benchmark_24h", 24, 97, "41_TradeStreet_local_civil_dynamic_degradation_benchmark_24h.csv", "45_TradeStreet_local_civil_dynamic_simulation_input_24h.csv"),
    ("benchmark_7d", 168, 673, "42_TradeStreet_local_civil_dynamic_degradation_benchmark_7d.csv", "46_TradeStreet_local_civil_dynamic_simulation_input_7d.csv"),
    ("benchmark_35d", 840, 3361, "43_TradeStreet_local_civil_dynamic_degradation_benchmark_35d.csv", "47_TradeStreet_local_civil_dynamic_simulation_input_35d.csv"),
]


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


source = load("34_TradeStreet_15min_building_load_local_civil_primary_UTC_mapping_layer.csv")
catalog = load("37_TradeStreet_local_civil_dynamic_degradation_continuous_window_catalog.csv")
summary38 = {row["metric"]: row["value"] for row in load("38_TradeStreet_local_civil_dynamic_degradation_window_summary.csv")}
registry = load("39_TradeStreet_local_civil_dynamic_degradation_test_window_registry.csv")
summary40 = {row["metric"]: row["value"] for row in load("40_TradeStreet_local_civil_dynamic_degradation_test_window_summary.csv")}
summary44 = {row["metric"]: row["value"] for row in load("44_TradeStreet_local_civil_dynamic_simulation_input_rules_and_summary.csv")}
errors: Counter[str] = Counter()
details: list[str] = []

eligible = [
    row for row in source
    if row["building_load_dynamic_admission_status"] == ELIGIBLE
    and row["utc_mapping_admission_status"].startswith("eligible_")
]
for row in eligible:
    row["_local"] = datetime.strptime(row["timestamp_local_civil"], FMT)
expected_windows: list[list[dict[str, str]]] = []
current: list[dict[str, str]] = []
for row in eligible:
    if not current or row["_local"] - current[-1]["_local"] == STEP:
        current.append(row)
    else:
        expected_windows.append(current)
        current = [row]
if current:
    expected_windows.append(current)
if len(expected_windows) != len(catalog):
    errors["catalog_window_count"] += 1

covered: set[str] = set()
for index, (expected, actual) in enumerate(zip(expected_windows, catalog), start=1):
    first, last = expected[0], expected[-1]
    expected_id = f"local_civil_raw_window_{index:04d}"
    expected_elapsed = (last["_local"] - first["_local"]).total_seconds() / 3600
    expected_offsets = ";".join(sorted({row["utc_offset"] for row in expected if row["utc_offset"]}))
    expected_classes = ";".join(sorted({row["dst_local_time_classification"] for row in expected}))
    checks = [
        actual["window_id"] == expected_id,
        actual["local_civil_start"] == first["timestamp_local_civil"] and actual["local_civil_end"] == last["timestamp_local_civil"],
        int(actual["record_count_15min"]) == len(expected),
        abs(float(actual["elapsed_hours_local_civil"]) - expected_elapsed) < 1e-9,
        actual["utc_offset_set"] == expected_offsets,
        actual["dst_local_time_classification_set"] == expected_classes,
        actual["contains_DST_ambiguous_local_label"] == ("1" if "ambiguous_fall_back_local_time" in expected_classes else "0"),
        actual["timestamp_axis"] == "timestamp_local_civil",
        actual["contains_imputation"] == "0",
        actual["time_grid_rule"] == "strict_consecutive_15min_local_civil_primary_axis",
    ]
    if not all(checks):
        errors["catalog_content"] += 1
        if len(details) < 5:
            details.append(f"catalog {index}: failed={[i for i, ok in enumerate(checks) if not ok]}")
    for row in expected:
        timestamp = row["timestamp_local_civil"]
        if timestamp in covered:
            errors["catalog_overlap"] += 1
        covered.add(timestamp)
if covered != {row["timestamp_local_civil"] for row in eligible}:
    errors["catalog_coverage"] += 1

longest = max(catalog, key=lambda row: int(row["record_count_15min"]))
classes = Counter(row["dst_local_time_classification"] for row in source)
buckets = Counter()
for row in catalog:
    hours = float(row["elapsed_hours_local_civil"])
    buckets["under_24h" if hours < 24 else "24h_to_under_7d" if hours < 168 else "7d_to_under_30d" if hours < 720 else "30d_or_more"] += 1
expected38 = {
    "source_row_count": str(len(source)),
    "source_eligible_dynamic_rows": str(len(eligible)),
    "window_count": str(len(catalog)),
    "eligible_rows_reconstructed_from_windows": str(len(covered)),
    "longest_window_id": longest["window_id"],
    "longest_window_record_count_15min": longest["record_count_15min"],
    "longest_window_elapsed_hours_local_civil": longest["elapsed_hours_local_civil"],
    "windows_under_24h": str(buckets["under_24h"]),
    "windows_24h_to_under_7d": str(buckets["24h_to_under_7d"]),
    "windows_7d_to_under_30d": str(buckets["7d_to_under_30d"]),
    "windows_30d_or_more": str(buckets["30d_or_more"]),
}
for key, value in expected38.items():
    if summary38.get(key) != value:
        errors["summary38"] += 1

source_by_local = {row["timestamp_local_civil"]: row for row in source}
exports: dict[str, list[dict[str, str]]] = {}
simulations: dict[str, list[dict[str, str]]] = {}
for entry, (test_id, hours, expected_count, benchmark_file, sim_file) in zip(registry, TESTS):
    rows = load(benchmark_file)
    sim = load(sim_file)
    exports[test_id] = rows
    simulations[test_id] = sim
    times = [datetime.strptime(row["timestamp_local_civil"], FMT) for row in rows]
    checks = [
        entry["test_window_id"] == test_id,
        len(rows) == expected_count == int(entry["record_count_15min"]),
        times[0].strftime(FMT) == entry["local_civil_start"] and times[-1].strftime(FMT) == entry["local_civil_end"],
        all(right - left == STEP for left, right in zip(times, times[1:])),
        (times[-1] - times[0]).total_seconds() / 3600 == hours == float(entry["actual_elapsed_hours_local_civil"]),
        all(row["building_load_dynamic_admission_status"] == ELIGIBLE for row in rows),
        all(row["utc_mapping_admission_status"].startswith("eligible_") for row in rows),
        all(
            source_by_local.get(row["timestamp_local_civil"]) is not None
            and {key: value for key, value in source_by_local[row["timestamp_local_civil"]].items() if key != "_local"} == row
            for row in rows
        ),
        entry["contains_DST_ambiguous_local_label"] == "0" and entry["contains_imputation"] == "0",
        len(sim) == len(rows) - 1,
    ]
    if not all(checks):
        errors[f"benchmark_{test_id}"] += 1
        details.append(f"benchmark {test_id}: failed={[i for i, ok in enumerate(checks) if not ok]}")
    for index, sim_row in enumerate(sim, start=1):
        left, right = rows[index - 1], rows[index]
        start = datetime.strptime(sim_row["interval_start_local_civil"], FMT)
        end = datetime.strptime(sim_row["interval_end_local_civil"], FMT)
        closure = float(sim_row["p_building_load_kW"]) - float(sim_row["p_net_kW"]) - float(sim_row["p_pv_model_kW"]) + float(sim_row["p_bess_kW_positive_charge_negative_discharge"])
        checks = [
            int(sim_row["simulation_step_index_1based"]) == index,
            end - start == STEP,
            sim_row["interval_start_local_civil"] == left["timestamp_local_civil"],
            sim_row["interval_end_local_civil"] == right["timestamp_local_civil"],
            sim_row["source_timestamp_local_civil"] == right["timestamp_local_civil"],
            sim_row["p_net_kW"] == right["p_net_raw_kW"],
            sim_row["p_pv_model_kW"] == right["p_pv_model_kW_nonnegative"],
            sim_row["p_bess_kW_positive_charge_negative_discharge"] == right["p_bess_raw_kW_positive_charge_negative_discharge"],
            sim_row["p_building_load_kW"] == right["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"],
            abs(closure) <= 1e-9,
            abs(float(sim_row["e_net_kWh"]) - 0.25 * float(sim_row["p_net_kW"])) <= 1e-10,
            abs(float(sim_row["e_pv_model_kWh"]) - 0.25 * float(sim_row["p_pv_model_kW"])) <= 1e-10,
            abs(float(sim_row["e_bess_kWh_positive_charge_negative_discharge"]) - 0.25 * float(sim_row["p_bess_kW_positive_charge_negative_discharge"])) <= 1e-10,
            abs(float(sim_row["e_building_load_kWh"]) - 0.25 * float(sim_row["p_building_load_kW"])) <= 1e-10,
            sim_row["source_admission_status"] == ELIGIBLE,
            sim_row["power_sample_semantics"] == "right_endpoint_preceding_15min_average_kW_on_local_civil_primary_axis",
        ]
        if not all(checks):
            errors[f"simulation_{test_id}"] += 1
            details.append(f"simulation {test_id} row {index}: failed={[i for i, ok in enumerate(checks) if not ok]}")
            break

for shorter, longer in (("benchmark_24h", "benchmark_7d"), ("benchmark_7d", "benchmark_35d")):
    if exports[shorter] != exports[longer][:len(exports[shorter])]:
        errors["benchmark_nested_prefix"] += 1
    if not all(all(a[key] == b[key] for key in a if key != "test_window_id") for a, b in zip(simulations[shorter], simulations[longer][:len(simulations[shorter])])):
        errors["simulation_nested_prefix"] += 1

# PV daylight sanity: strict night screen on primary San Diego local civil clock.
night_counts: dict[str, int] = {}
for test_id, rows in simulations.items():
    night_positive = [
        row for row in rows
        if (datetime.strptime(row["interval_end_local_civil"], FMT).hour >= 22 or datetime.strptime(row["interval_end_local_civil"], FMT).hour < 6)
        and float(row["p_pv_model_kW"]) > 1.0
    ]
    night_counts[test_id] = len(night_positive)
    if night_positive:
        errors["nighttime_pv"] += len(night_positive)
        details.append(f"night PV {test_id}: first={night_positive[0]['interval_end_local_civil']}; pv={night_positive[0]['p_pv_model_kW']}")

expected40 = {"benchmark_count": "3", "timestamp_axis": "timestamp_local_civil", "selected_source_window_id": registry[0]["source_window_id"]}
for key, value in expected40.items():
    if summary40.get(key) != value:
        errors["summary40"] += 1
if summary44.get("timestamp_axis") != "timestamp_local_civil":
    errors["summary44"] += 1

print("STRICT_LOCAL_CIVIL_DYNAMIC_INPUT_VALIDATION")
print(f"source_rows={len(source)}; eligible_rows={len(eligible)}; local_time_classes={dict(classes)}")
print(f"catalog_windows={len(catalog)}; reconstructed_rows={len(covered)}; longest={longest['window_id']} rows={longest['record_count_15min']}")
print(f"benchmark_rows={ {key: len(value) for key, value in exports.items()} }")
print(f"simulation_steps={ {key: len(value) for key, value in simulations.items()} }")
print(f"nighttime_pv_over_1kW={night_counts}")
print(f"errors={dict(errors)}")
for detail in details[:10]:
    print(f"DETAIL {detail}")
raise SystemExit(1 if errors else 0)
