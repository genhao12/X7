from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SOURCE = BASE / "47_TradeStreet_local_civil_dynamic_simulation_input_35d.csv"
INTERFACE_OUT = BASE / "48_TradeStreet_local_civil_dynamic_model_input_interface_35d.csv"
CONTRACT_OUT = BASE / "49_TradeStreet_local_civil_dynamic_model_preparation_contract.csv"

with SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
    source_rows = list(csv.DictReader(handle))
if not source_rows:
    raise ValueError("35-day dynamic simulation input is empty")

interface_rows = []
for row in source_rows:
    interface_rows.append({
        "test_window_id": row["test_window_id"],
        "simulation_step_index_1based": row["simulation_step_index_1based"],
        "interval_start_local_civil": row["interval_start_local_civil"],
        "interval_end_local_civil": row["interval_end_local_civil"],
        "interval_start_utc_derived": row["interval_start_utc_derived"],
        "interval_end_utc_derived": row["interval_end_utc_derived"],
        "dt_seconds": row["dt_seconds"],
        "dt_hours": row["dt_hours"],
        "p_net_kW": row["p_net_kW"],
        "p_pv_model_kW": row["p_pv_model_kW"],
        "p_bess_observed_kW_positive_charge_negative_discharge": row["p_bess_kW_positive_charge_negative_discharge"],
        "p_building_load_kW": row["p_building_load_kW"],
        "e_net_kWh": row["e_net_kWh"],
        "e_pv_model_kWh": row["e_pv_model_kWh"],
        "e_bess_observed_kWh_positive_charge_negative_discharge": row["e_bess_kWh_positive_charge_negative_discharge"],
        "e_building_load_kWh": row["e_building_load_kWh"],
        "observed_power_sample_semantics": row["power_sample_semantics"],
        "source_admission_status": row["source_admission_status"],
        "utc_mapping_admission_status": row["utc_mapping_admission_status"],
        "timezone_evidence_status": row["timezone_evidence_status"],
    })

with INTERFACE_OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(interface_rows[0]))
    writer.writeheader()
    writer.writerows(interface_rows)

contract = [
    ("rule_version", "v1.0_trade_street_dynamic_model_preparation_contract"),
    ("scope", "Preparation interface only; no dispatch optimization, battery state propagation, hydrogen state propagation, component sizing or degradation calibration is executed in this layer"),
    ("source_file", SOURCE.name),
    ("interface_file", INTERFACE_OUT.name),
    ("test_window_id", source_rows[0]["test_window_id"]),
    ("interval_count", str(len(interface_rows))),
    ("interval_start_local_civil", interface_rows[0]["interval_start_local_civil"]),
    ("interval_end_local_civil", interface_rows[-1]["interval_end_local_civil"]),
    ("time_axis_primary", "timestamp_local_civil; derived UTC is retained only for traceable external joins"),
    ("time_step", "fixed 900 seconds = 0.25 h"),
    ("observed_power_convention", "right endpoint sample is the average power over preceding 15-minute interval"),
    ("observed_power_boundary", "P_building=P_net+P_pv_model-P_bess_observed; positive observed BESS power denotes charging load and negative denotes discharge output"),
    ("required_dynamic_states", "Battery SOC; battery capacity/health state if degradation is activated; hydrogen tank inventory; electrolyzer operating state; fuel-cell operating state; optional thermal states only if a validated thermal model is supplied"),
    ("required_control_decisions", "PV curtailment; BESS charge/discharge command; electrolyzer power; fuel-cell power; grid import/export or unmet-load slack according to selected operating mode"),
    ("required_parameter_evidence_before_simulation", "BESS usable energy, charge/discharge limits and efficiencies; inverter limits; PV curtailment policy; electrolyzer/fuel-cell capacities and efficiency maps; hydrogen storage capacity/leakage; initial state values; degradation law and calibrated parameters"),
    ("initial_state_policy", "Initial SOC and hydrogen inventory are not inferable from the supplied interval data and must be explicitly declared, sensitivity-tested, or estimated using a documented warm-up method"),
    ("optimization_boundary_warning", "Observed BESS power is a measurement/reconstruction input for validation, not a command to force in a redesigned PV-hydrogen-BESS dispatch model unless the study explicitly reproduces observed operation"),
    ("missing_data_policy", "No imputed records are present; do not synthesize values for excluded intervals"),
    ("timezone_evidence_status", source_rows[0]["timezone_evidence_status"]),
    ("publication_limit", "The local-civil timestamp interpretation is physically supported by PV daylight behavior but remains pending confirmation by source metadata or the original metering system"),
]
with CONTRACT_OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["metric", "value"])
    writer.writerows(contract)

print(f"interface_rows={len(interface_rows)}")
print(f"local_range={interface_rows[0]['interval_start_local_civil']}..{interface_rows[-1]['interval_end_local_civil']}")
