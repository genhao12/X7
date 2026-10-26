from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
INTERFACE = BASE / "48_TradeStreet_local_civil_dynamic_model_input_interface_35d.csv"
CONTRACT = BASE / "49_TradeStreet_local_civil_dynamic_model_preparation_contract.csv"
REGISTER = BASE / "50_TradeStreet_dynamic_model_parameter_evidence_and_run_gate.csv"
SUMMARY = BASE / "51_TradeStreet_dynamic_model_readiness_summary.csv"

with INTERFACE.open("r", encoding="utf-8-sig", newline="") as handle:
    interface_rows = list(csv.DictReader(handle))
with CONTRACT.open("r", encoding="utf-8-sig", newline="") as handle:
    contract = {row["metric"]: row["value"] for row in csv.DictReader(handle)}
if not interface_rows:
    raise ValueError("Model interface is empty")

rows = [
    {
        "parameter_or_evidence_item": "Time step and interval convention", "model_subsystem": "global_simulation", "required_for": "all dynamic simulations", "status": "available_from_verified_data", "value_or_definition": "900 s / 0.25 h; right-endpoint power sample is preceding-15-minute average", "evidence_source": "44_TradeStreet_local_civil_dynamic_simulation_input_rules_and_summary.csv; 48 interface", "allowed_use_now": "use directly", "blocking_reason_if_unavailable": "", "required_resolution": "",
    },
    {
        "parameter_or_evidence_item": "Building-load, PV and observed-BESS time series", "model_subsystem": "data_boundary", "required_for": "observed-operation reproduction and data-driven validation", "status": "available_from_verified_data", "value_or_definition": "P_building, P_pv_model and observed P_bess are supplied for 3,360 intervals", "evidence_source": "48 interface; source admission status is eligible for all rows", "allowed_use_now": "use as exogenous observations; do not force observed BESS in redesigned dispatch", "blocking_reason_if_unavailable": "", "required_resolution": "",
    },
    {
        "parameter_or_evidence_item": "Observed Trade Street BESS rated-power screening boundary", "model_subsystem": "battery_observation_boundary", "required_for": "observed BESS data screening and historical-operation validation", "status": "available_from_verified_data", "value_or_definition": "absolute observed BESS power rating screen = 200 kW", "evidence_source": "06_UCSD_paper_QC_settings_review.csv; UCSD PythonBatteryStorage.py lines 16-18 and 61-68; UCSD paper Sec. III-H", "allowed_use_now": "use only to describe the verified observation-screening boundary", "blocking_reason_if_unavailable": "", "required_resolution": "",
    },
    {
        "parameter_or_evidence_item": "BESS usable energy capacity", "model_subsystem": "battery_state", "required_for": "SOC propagation, feasibility, degradation", "status": "missing_source_evidence", "value_or_definition": "not populated", "evidence_source": "No verified usable-capacity value in 48/49 or the frozen QC evidence layer; contextual capacity statements are not a calibration source", "allowed_use_now": "do not run SOC/degradation model", "blocking_reason_if_unavailable": "SOC cannot be determined from power alone", "required_resolution": "obtain asset specification, BMS record, or explicitly scoped sensitivity range with citations",
    },
    {
        "parameter_or_evidence_item": "BESS efficiency and redesigned-dispatch power constraints", "model_subsystem": "battery_state", "required_for": "SOC propagation and redesigned dispatch constraints", "status": "partial_verified_evidence", "value_or_definition": "200 kW is verified only as an observed-data rating screen; charge/discharge efficiency, inverter limit, duration limit and design command bounds are not populated", "evidence_source": "06_UCSD_paper_QC_settings_review.csv verifies the 200 kW observation screen; 48/49 do not identify efficiency or redesign constraints", "allowed_use_now": "may use 200 kW only for historical data-screening context; do not use it alone as a redesigned dispatch constraint", "blocking_reason_if_unavailable": "round-trip losses and feasible design command bounds remain unknown", "required_resolution": "obtain inverter/BESS datasheet or cited design assumptions; distinguish observed screening rating from redesign command bounds",
    },
    {
        "parameter_or_evidence_item": "Initial battery SOC", "model_subsystem": "battery_state", "required_for": "SOC propagation", "status": "missing_source_evidence", "value_or_definition": "not inferable from a finite power window", "evidence_source": "49 initial_state_policy", "allowed_use_now": "only after declared sensitivity or documented warm-up estimation", "blocking_reason_if_unavailable": "state equation needs an initial condition", "required_resolution": "declare source/assumption and conduct boundary sensitivity or warm-up protocol",
    },
    {
        "parameter_or_evidence_item": "Battery degradation law and calibrated coefficients", "model_subsystem": "battery_degradation", "required_for": "capacity/resistance fade analysis", "status": "missing_source_evidence", "value_or_definition": "not populated", "evidence_source": "No chemistry, temperature history, cycle-aging test, or validated degradation calibration supplied", "allowed_use_now": "do not claim degradation estimates", "blocking_reason_if_unavailable": "degradation cannot be identified from power series alone", "required_resolution": "select cited chemistry-specific law and calibrate/justify all coefficients; add temperature treatment",
    },
    {
        "parameter_or_evidence_item": "PV curtailment and inverter constraints", "model_subsystem": "pv_dispatch", "required_for": "redesigned dispatch", "status": "missing_source_evidence", "value_or_definition": "PV time series is available; controllable curtailment boundary is not", "evidence_source": "48 has modeled PV power only", "allowed_use_now": "use PV as observed availability only", "blocking_reason_if_unavailable": "curtailment decision constraints are unspecified", "required_resolution": "obtain inverter/rating data or define cited design scenario",
    },
    {
        "parameter_or_evidence_item": "Electrolyzer capacity and efficiency map", "model_subsystem": "hydrogen_production", "required_for": "PV-hydrogen dispatch", "status": "missing_source_evidence", "value_or_definition": "not populated", "evidence_source": "No electrolyzer asset data are present in 48/49", "allowed_use_now": "do not simulate hydrogen production", "blocking_reason_if_unavailable": "electrical-to-hydrogen conversion and operating bounds unknown", "required_resolution": "choose a documented technology/design case and provide rated power, part-load map, standby and ramp behavior",
    },
    {
        "parameter_or_evidence_item": "Hydrogen storage capacity, pressure and leakage", "model_subsystem": "hydrogen_storage", "required_for": "hydrogen inventory feasibility", "status": "missing_source_evidence", "value_or_definition": "not populated", "evidence_source": "No storage asset data are present in 48/49", "allowed_use_now": "do not propagate hydrogen inventory", "blocking_reason_if_unavailable": "storage state and losses unknown", "required_resolution": "provide vessel configuration, usable inventory, pressure model and leakage assumption with evidence",
    },
    {
        "parameter_or_evidence_item": "Fuel-cell capacity and efficiency map", "model_subsystem": "hydrogen_to_power", "required_for": "hydrogen-supported load dispatch", "status": "missing_source_evidence", "value_or_definition": "not populated", "evidence_source": "No fuel-cell asset data are present in 48/49", "allowed_use_now": "do not simulate fuel-cell output", "blocking_reason_if_unavailable": "hydrogen-to-electrical conversion and operating bounds unknown", "required_resolution": "choose a documented technology/design case and provide rated power, part-load map, startup and ramp behavior",
    },
    {
        "parameter_or_evidence_item": "Initial hydrogen inventory", "model_subsystem": "hydrogen_storage", "required_for": "hydrogen inventory propagation", "status": "missing_source_evidence", "value_or_definition": "not inferable from electrical time series", "evidence_source": "49 initial_state_policy", "allowed_use_now": "only after explicit sensitivity or documented warm-up estimation", "blocking_reason_if_unavailable": "inventory balance needs an initial condition", "required_resolution": "declare source/assumption and conduct boundary sensitivity or warm-up protocol",
    },
    {
        "parameter_or_evidence_item": "Operating mode and grid/unmet-load penalty policy", "model_subsystem": "system_optimization", "required_for": "objective and feasibility definition", "status": "missing_study_design_decision", "value_or_definition": "not selected", "evidence_source": "49 permits grid import/export or unmet-load slack according to selected operating mode", "allowed_use_now": "cannot formulate an unambiguous optimization objective", "blocking_reason_if_unavailable": "grid-connected/off-grid objective and penalties change results materially", "required_resolution": "freeze study mode, objective hierarchy, tariff/emission/reliability penalties and export policy",
    },
]

with REGISTER.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

counts = {status: sum(row["status"] == status for row in rows) for status in {row["status"] for row in rows}}
summary = [
    ("rule_version", "v1.1_dynamic_model_parameter_evidence_and_run_gate"),
    ("verified_interface_file", INTERFACE.name),
    ("verified_contract_file", CONTRACT.name),
    ("interval_count", str(len(interface_rows))),
    ("time_axis_primary", contract["time_axis_primary"]),
    ("available_verified_items", str(counts["available_from_verified_data"])),
    ("partial_verified_evidence_items", str(counts["partial_verified_evidence"])),
    ("missing_source_evidence_items", str(counts["missing_source_evidence"])),
    ("missing_study_design_decisions", str(counts["missing_study_design_decision"])),
    ("observed_BESS_rating_screen_evidence", "available: 200 kW for observed Trade Street BESS data screening only; not sufficient to define redesigned-dispatch limits"),
    ("observed_operation_reproduction_readiness", "conditional: data interface and observed 200 kW screen are ready, but SOC/efficiency/initial-state evidence must be resolved before state propagation"),
    ("redesigned_PV_hydrogen_BESS_dispatch_readiness", "blocked: hydrogen asset parameters, BESS efficiency/design constraints and operating-mode design decision are not supplied"),
    ("battery_degradation_readiness", "blocked: chemistry, usable capacity, SOC/temperature history and calibrated degradation law are not supplied"),
    ("prohibited_action", "Do not populate missing values from intuition or silently adopt UCSD context values as calibrated parameters"),
    ("next_permitted_action", "collect traceable parameter evidence and freeze the study operating mode; then build the state-equation and dispatch formulation"),
    ("timezone_evidence_status", contract["timezone_evidence_status"]),
]
with SUMMARY.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["metric", "value"])
    writer.writerows(summary)

print(f"register_rows={len(rows)}; status_counts={counts}")
