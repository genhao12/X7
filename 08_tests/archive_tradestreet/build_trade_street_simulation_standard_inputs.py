from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
FMT = "%Y-%m-%d %H:%M:%S"
STEP_HOURS = 0.25
STEP = timedelta(minutes=15)
TESTS = [
    ("benchmark_24h", "27_TradeStreet_dynamic_degradation_benchmark_24h.csv", "31_TradeStreet_dynamic_simulation_input_24h.csv"),
    ("benchmark_7d", "28_TradeStreet_dynamic_degradation_benchmark_7d.csv", "32_TradeStreet_dynamic_simulation_input_7d.csv"),
    ("benchmark_35d", "29_TradeStreet_dynamic_degradation_benchmark_35d.csv", "33_TradeStreet_dynamic_simulation_input_35d.csv"),
]
SUMMARY = BASE / "30_TradeStreet_dynamic_simulation_input_rules_and_summary.csv"


def read_rows(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


summary_rows = [
    ("rule_version", "v1.0_right_endpoint_15min_average_dynamic_simulation_input"),
    ("source_layer", "27-29 frozen original-complete dynamic benchmark power series"),
    ("ucsd_power_semantics", "UCSD paper reports most data as average power over the preceding 15-minute period"),
    ("simulation_interval_convention", "For interval [t_(k-1), t_k], use the observed right-endpoint sample at t_k as the preceding-15-minute average power"),
    ("initial_anchor_policy", "First timestamp in each benchmark is an initial time anchor only; it creates no simulated interval and no energy contribution"),
    ("time_step_hours", "0.25"),
    ("time_step_seconds", "900"),
    ("power_unit", "kW"),
    ("energy_unit", "kWh"),
    ("building_load_boundary", "P_building=P_net+P_pv_model-P_bess; BESS positive is charging load and negative is discharge output"),
    ("pv_model_policy", "Use p_pv_model_kW_nonnegative; raw PV remains traceable in source files"),
    ("imputation_policy", "No imputed data; all intervals originate from eligible_for_building_load_dynamic_input rows"),
    ("timestamp_axis", "timestamp_utc"),
    ("timezone_evidence_status", "project_instruction_assumption_pending_source_confirmation"),
]

for test_id, source_name, output_name in TESTS:
    rows = read_rows(source_name)
    if len(rows) < 2:
        raise ValueError(f"{source_name} has fewer than two timestamp anchors")
    outputs: list[dict[str, str]] = []
    for index in range(1, len(rows)):
        left, right = rows[index - 1], rows[index]
        start = datetime.strptime(left["timestamp_utc"], FMT)
        end = datetime.strptime(right["timestamp_utc"], FMT)
        if end - start != STEP:
            raise ValueError(f"Non-15-minute source gap at {source_name}, position {index}")
        if right["building_load_dynamic_admission_status"] != "eligible_for_building_load_dynamic_input":
            raise ValueError(f"Noneligible right endpoint at {source_name}, position {index}")
        p_net = float(right["p_net_raw_kW"])
        p_pv = float(right["p_pv_model_kW_nonnegative"])
        p_bess = float(right["p_bess_raw_kW_positive_charge_negative_discharge"])
        p_building = float(right["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"])
        if abs(p_building - p_net - p_pv + p_bess) > 1e-9:
            raise ValueError(f"Power closure failure at {source_name}, position {index}")
        outputs.append({
            "test_window_id": test_id,
            "simulation_step_index_1based": str(index),
            "interval_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval_end_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval_end_local": right["timestamp_local"],
            "dt_hours": "0.25",
            "dt_seconds": "900",
            "power_sample_timestamp_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "power_sample_semantics": "right_endpoint_preceding_15min_average_kW",
            "p_net_kW": right["p_net_raw_kW"],
            "p_pv_model_kW": right["p_pv_model_kW_nonnegative"],
            "p_bess_kW_positive_charge_negative_discharge": right["p_bess_raw_kW_positive_charge_negative_discharge"],
            "p_building_load_kW": right["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"],
            "e_net_kWh": f"{p_net * STEP_HOURS:.12g}",
            "e_pv_model_kWh": f"{p_pv * STEP_HOURS:.12g}",
            "e_bess_kWh_positive_charge_negative_discharge": f"{p_bess * STEP_HOURS:.12g}",
            "e_building_load_kWh": f"{p_building * STEP_HOURS:.12g}",
            "total_outlier_candidate_flag": right["total_outlier_candidate_flag"],
            "pv_outlier_candidate_flag": right["pv_outlier_candidate_flag"],
            "bess_rating_screen_flag": right["bess_rating_screen_flag"],
            "source_admission_status": right["building_load_dynamic_admission_status"],
            "source_timestamp_utc": right["timestamp_utc"],
            "timezone_evidence_status": right["timezone_evidence_status"],
        })
    with (BASE / output_name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(outputs[0]))
        writer.writeheader()
        writer.writerows(outputs)
    summary_rows.extend([
        (f"{test_id}_source_file", source_name),
        (f"{test_id}_simulation_input_file", output_name),
        (f"{test_id}_source_anchor_rows", str(len(rows))),
        (f"{test_id}_simulation_steps", str(len(outputs))),
        (f"{test_id}_elapsed_hours", f"{len(outputs) * STEP_HOURS:.2f}"),
        (f"{test_id}_interval_start_utc", outputs[0]["interval_start_utc"]),
        (f"{test_id}_interval_end_utc", outputs[-1]["interval_end_utc"]),
    ])

with SUMMARY.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["metric", "value"])
    writer.writerows(summary_rows)

print("Created dynamic simulation standard inputs: 30-33")
