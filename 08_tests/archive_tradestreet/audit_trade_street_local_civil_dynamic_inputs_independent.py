from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

BASE = Path(r"D:\matlab\C\base data")
FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
ELIGIBLE = "eligible_for_building_load_dynamic_input"
SOURCE_NAME = "34_TradeStreet_15min_building_load_local_civil_primary_UTC_mapping_layer.csv"
CATALOG_NAME = "37_TradeStreet_local_civil_dynamic_degradation_continuous_window_catalog.csv"
REGISTRY_NAME = "39_TradeStreet_local_civil_dynamic_degradation_test_window_registry.csv"
SUMMARY_NAME = "44_TradeStreet_local_civil_dynamic_simulation_input_rules_and_summary.csv"
TESTS = {
    "benchmark_24h": (24, 97, "41_TradeStreet_local_civil_dynamic_degradation_benchmark_24h.csv", "45_TradeStreet_local_civil_dynamic_simulation_input_24h.csv"),
    "benchmark_7d": (168, 673, "42_TradeStreet_local_civil_dynamic_degradation_benchmark_7d.csv", "46_TradeStreet_local_civil_dynamic_simulation_input_7d.csv"),
    "benchmark_35d": (840, 3361, "43_TradeStreet_local_civil_dynamic_degradation_benchmark_35d.csv", "47_TradeStreet_local_civil_dynamic_simulation_input_35d.csv"),
}


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def clean(row: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in row.items() if key != "_local"}


def check(condition: bool, label: str, errors: Counter[str], details: list[str], detail: str = "") -> None:
    if not condition:
        errors[label] += 1
        if detail and len(details) < 25:
            details.append(f"{label}: {detail}")


source = load(SOURCE_NAME)
catalog = load(CATALOG_NAME)
registry = load(REGISTRY_NAME)
summary = {row["metric"]: row["value"] for row in load(SUMMARY_NAME)}
errors: Counter[str] = Counter()
details: list[str] = []

# Reconstruct the complete local-civil eligible window partition independently.
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
check(len(expected_windows) == len(catalog), "catalog_window_count", errors, details, f"expected={len(expected_windows)} actual={len(catalog)}")

covered: set[str] = set()
for index, (expected, actual) in enumerate(zip(expected_windows, catalog), start=1):
    first, last = expected[0], expected[-1]
    elapsed = (last["_local"] - first["_local"]).total_seconds() / 3600
    expected_offsets = ";".join(sorted({row["utc_offset"] for row in expected if row["utc_offset"]}))
    expected_classes = ";".join(sorted({row["dst_local_time_classification"] for row in expected}))
    checks = {
        "window_id": actual["window_id"] == f"local_civil_raw_window_{index:04d}",
        "source_layer": actual["source_layer"] == SOURCE_NAME,
        "axis": actual["timestamp_axis"] == "timestamp_local_civil",
        "endpoints": actual["local_civil_start"] == first["timestamp_local_civil"] and actual["local_civil_end"] == last["timestamp_local_civil"],
        "count": int(actual["record_count_15min"]) == len(expected),
        "elapsed": abs(float(actual["elapsed_hours_local_civil"]) - elapsed) < 1e-9,
        "endpoint_count": int(actual["record_count_15min"]) == int(elapsed * 4) + 1,
        "offsets": actual["utc_offset_set"] == expected_offsets,
        "dst_classes": actual["dst_local_time_classification_set"] == expected_classes,
        "ambiguity": actual["contains_DST_ambiguous_local_label"] == ("1" if "ambiguous_fall_back_local_time" in expected_classes else "0"),
        "imputation": actual["contains_imputation"] == "0",
        "grid_rule": actual["time_grid_rule"] == "strict_consecutive_15min_local_civil_primary_axis",
    }
    for label, passed in checks.items():
        check(passed, f"catalog_{label}", errors, details, f"window={index}")
    for row in expected:
        key = row["timestamp_local_civil"]
        check(key not in covered, "catalog_overlap", errors, details, key)
        covered.add(key)
check(covered == {row["timestamp_local_civil"] for row in eligible}, "catalog_coverage", errors, details)

# Validate registered nested benchmarks against the 35-day selection and source payload.
source_by_local = {row["timestamp_local_civil"]: clean(row) for row in source}
exports: dict[str, list[dict[str, str]]] = {}
simulations: dict[str, list[dict[str, str]]] = {}
for entry in registry:
    test_id = entry["test_window_id"]
    if test_id not in TESTS:
        errors["unknown_registry_test"] += 1
        continue
    hours, expected_anchor_count, benchmark_name, simulation_name = TESTS[test_id]
    benchmark = load(benchmark_name)
    simulation = load(simulation_name)
    exports[test_id] = benchmark
    simulations[test_id] = simulation
    times = [datetime.strptime(row["timestamp_local_civil"], FMT) for row in benchmark]
    check(entry["timestamp_axis"] == "timestamp_local_civil", "registry_axis", errors, details, test_id)
    check(len(benchmark) == expected_anchor_count == int(entry["record_count_15min"]), "benchmark_anchor_count", errors, details, test_id)
    check(times[0].strftime(FMT) == entry["local_civil_start"] and times[-1].strftime(FMT) == entry["local_civil_end"], "benchmark_endpoints", errors, details, test_id)
    check(all(right - left == STEP for left, right in zip(times, times[1:])), "benchmark_continuity", errors, details, test_id)
    check((times[-1] - times[0]).total_seconds() / 3600 == hours == float(entry["actual_elapsed_hours_local_civil"]), "benchmark_duration", errors, details, test_id)
    check(all(row["building_load_dynamic_admission_status"] == ELIGIBLE for row in benchmark), "benchmark_admission", errors, details, test_id)
    check(all(row["utc_mapping_admission_status"] == "eligible_UTC_mapping_unambiguous_local_time" for row in benchmark), "benchmark_dst_ambiguity", errors, details, test_id)
    check(all(source_by_local.get(row["timestamp_local_civil"]) == row for row in benchmark), "benchmark_source_payload", errors, details, test_id)
    check(entry["contains_DST_ambiguous_local_label"] == "0" and entry["contains_imputation"] == "0", "registry_exclusion_policy", errors, details, test_id)

    check(len(simulation) == len(benchmark) - 1, "simulation_step_count", errors, details, test_id)
    for i, sim in enumerate(simulation, start=1):
        left, right = benchmark[i - 1], benchmark[i]
        start = datetime.strptime(sim["interval_start_local_civil"], FMT)
        end = datetime.strptime(sim["interval_end_local_civil"], FMT)
        closure = float(sim["p_building_load_kW"]) - float(sim["p_net_kW"]) - float(sim["p_pv_model_kW"]) + float(sim["p_bess_kW_positive_charge_negative_discharge"])
        values = {
            "index": int(sim["simulation_step_index_1based"]) == i,
            "local_interval": start == times[i - 1] and end == times[i] and end - start == STEP,
            "utc_interval": sim["interval_start_utc_derived"] == left["timestamp_utc_iso8601"] and sim["interval_end_utc_derived"] == right["timestamp_utc_iso8601"],
            "right_endpoint": sim["power_sample_timestamp_local_civil"] == right["timestamp_local_civil"] and sim["source_timestamp_local_civil"] == right["timestamp_local_civil"],
            "source_values": sim["p_net_kW"] == right["p_net_raw_kW"] and sim["p_pv_model_kW"] == right["p_pv_model_kW_nonnegative"] and sim["p_bess_kW_positive_charge_negative_discharge"] == right["p_bess_raw_kW_positive_charge_negative_discharge"] and sim["p_building_load_kW"] == right["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"],
            "power_closure": abs(closure) <= 1e-9,
            "energy_net": abs(float(sim["e_net_kWh"]) - 0.25 * float(sim["p_net_kW"])) <= 1e-10,
            "energy_pv": abs(float(sim["e_pv_model_kWh"]) - 0.25 * float(sim["p_pv_model_kW"])) <= 1e-10,
            "energy_bess": abs(float(sim["e_bess_kWh_positive_charge_negative_discharge"]) - 0.25 * float(sim["p_bess_kW_positive_charge_negative_discharge"])) <= 1e-10,
            "energy_load": abs(float(sim["e_building_load_kWh"]) - 0.25 * float(sim["p_building_load_kW"])) <= 1e-10,
            "admission": sim["source_admission_status"] == ELIGIBLE and sim["utc_mapping_admission_status"] == "eligible_UTC_mapping_unambiguous_local_time",
        }
        for label, passed in values.items():
            check(passed, f"simulation_{label}", errors, details, f"{test_id} row={i}")

# Exact nested prefixes, accounting only for deliberately distinct test id.
for small, large in (("benchmark_24h", "benchmark_7d"), ("benchmark_7d", "benchmark_35d")):
    check(exports[small] == exports[large][:len(exports[small])], "benchmark_nested_prefix", errors, details, f"{small}->{large}")
    compare = lambda left, right: all(left[key] == right[key] for key in left if key != "test_window_id")
    check(all(compare(left, right) for left, right in zip(simulations[small], simulations[large][:len(simulations[small])])), "simulation_nested_prefix", errors, details, f"{small}->{large}")

# Independent physical screens on declared primary local civil time.
physical = {}
for test_id, simulation in simulations.items():
    night = []
    daily_pv = []
    for row in simulation:
        timestamp = datetime.strptime(row["interval_end_local_civil"], FMT)
        pv = float(row["p_pv_model_kW"])
        if timestamp.hour >= 22 or timestamp.hour < 6:
            if pv > 1.0:
                night.append((timestamp, pv))
        daily_pv.append((timestamp.hour, pv))
    physical[test_id] = {
        "night_pv_over_1kW": len(night),
        "max_pv_kW": max(pv for _, pv in daily_pv),
        "peak_hour_mean_pv": max(range(24), key=lambda hour: mean([pv for h, pv in daily_pv if h == hour]) if any(h == hour for h, _ in daily_pv) else -1),
    }
    check(not night, "physical_nighttime_pv", errors, details, f"{test_id} first={night[0] if night else ''}")

# Rules summary needs to accurately bind the exports.
for test_id, (hours, anchors, benchmark_name, simulation_name) in TESTS.items():
    expected = {
        f"{test_id}_source_file": benchmark_name,
        f"{test_id}_simulation_input_file": simulation_name,
        f"{test_id}_source_anchor_rows": str(anchors),
        f"{test_id}_simulation_steps": str(anchors - 1),
        f"{test_id}_elapsed_hours_local_civil": f"{hours:.2f}",
    }
    for metric, value in expected.items():
        check(summary.get(metric) == value, "summary_binding", errors, details, f"{metric} expected={value} actual={summary.get(metric)}")
check(summary.get("timestamp_axis") == "timestamp_local_civil", "summary_axis", errors, details)
check(summary.get("time_step_seconds") == "900" and summary.get("time_step_hours") == "0.25", "summary_timestep", errors, details)

print("INDEPENDENT_AUDIT_LOCAL_CIVIL_DYNAMIC_INPUTS")
print(f"source_rows={len(source)}; eligible_rows={len(eligible)}")
print(f"catalog_windows={len(catalog)}; reconstructed_coverage={len(covered)}")
print(f"registry_rows={len(registry)}; benchmark_anchor_rows={ {key: len(value) for key, value in exports.items()} }")
print(f"simulation_steps={ {key: len(value) for key, value in simulations.items()} }")
print(f"physical_screens={physical}")
print(f"errors={dict(errors)}")
for detail in details:
    print(f"DETAIL {detail}")
raise SystemExit(1 if errors else 0)
