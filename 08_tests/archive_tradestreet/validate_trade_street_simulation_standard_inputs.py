from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
FMT_SOURCE = "%Y-%m-%d %H:%M:%S"
FMT_UTC = "%Y-%m-%dT%H:%M:%SZ"
STEP = timedelta(minutes=15)
TESTS = [
    ("benchmark_24h", "27_TradeStreet_dynamic_degradation_benchmark_24h.csv", "31_TradeStreet_dynamic_simulation_input_24h.csv"),
    ("benchmark_7d", "28_TradeStreet_dynamic_degradation_benchmark_7d.csv", "32_TradeStreet_dynamic_simulation_input_7d.csv"),
    ("benchmark_35d", "29_TradeStreet_dynamic_degradation_benchmark_35d.csv", "33_TradeStreet_dynamic_simulation_input_35d.csv"),
]


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


summary = {r["metric"]: r["value"] for r in load("30_TradeStreet_dynamic_simulation_input_rules_and_summary.csv")}
errors: list[str] = []
for test_id, source_name, simulation_name in TESTS:
    source = load(source_name)
    simulation = load(simulation_name)
    if len(simulation) != len(source) - 1:
        errors.append(f"{test_id}: wrong step count")
        continue
    for index, row in enumerate(simulation, start=1):
        left, right = source[index - 1], source[index]
        start = datetime.strptime(row["interval_start_utc"], FMT_UTC)
        end = datetime.strptime(row["interval_end_utc"], FMT_UTC)
        closure = float(row["p_building_load_kW"]) - float(row["p_net_kW"]) - float(row["p_pv_model_kW"]) + float(row["p_bess_kW_positive_charge_negative_discharge"])
        checks = [
            int(row["simulation_step_index_1based"]) == index,
            end - start == STEP,
            start == datetime.strptime(left["timestamp_utc"], FMT_SOURCE),
            end == datetime.strptime(right["timestamp_utc"], FMT_SOURCE),
            row["source_timestamp_utc"] == right["timestamp_utc"],
            row["p_net_kW"] == right["p_net_raw_kW"],
            row["p_pv_model_kW"] == right["p_pv_model_kW_nonnegative"],
            row["p_bess_kW_positive_charge_negative_discharge"] == right["p_bess_raw_kW_positive_charge_negative_discharge"],
            row["p_building_load_kW"] == right["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"],
            abs(closure) <= 1e-9,
            abs(float(row["e_building_load_kWh"]) - float(row["p_building_load_kW"]) * 0.25) <= 1e-10,
            abs(float(row["e_net_kWh"]) - float(row["p_net_kW"]) * 0.25) <= 1e-10,
            abs(float(row["e_pv_model_kWh"]) - float(row["p_pv_model_kW"]) * 0.25) <= 1e-10,
            abs(float(row["e_bess_kWh_positive_charge_negative_discharge"]) - float(row["p_bess_kW_positive_charge_negative_discharge"]) * 0.25) <= 1e-10,
            row["source_admission_status"] == "eligible_for_building_load_dynamic_input",
            row["power_sample_semantics"] == "right_endpoint_preceding_15min_average_kW",
        ]
        if not all(checks):
            errors.append(f"{test_id}: row={index}; failed={[i for i, x in enumerate(checks) if not x]}")
            break
    if summary.get(f"{test_id}_simulation_steps") != str(len(simulation)):
        errors.append(f"{test_id}: summary step mismatch")

print("STRICT_SIMULATION_STANDARD_INPUT_VALIDATION")
print("errors=", errors)
raise SystemExit(1 if errors else 0)
