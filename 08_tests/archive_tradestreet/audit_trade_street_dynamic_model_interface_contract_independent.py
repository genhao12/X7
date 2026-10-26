from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SOURCE = "47_TradeStreet_local_civil_dynamic_simulation_input_35d.csv"
INTERFACE = "48_TradeStreet_local_civil_dynamic_model_input_interface_35d.csv"
CONTRACT = "49_TradeStreet_local_civil_dynamic_model_preparation_contract.csv"
FMT_LOCAL = "%Y-%m-%d %H:%M:%S"
FMT_UTC = "%Y-%m-%dT%H:%M:%SZ"
STEP = timedelta(minutes=15)


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


source = load(SOURCE)
interface = load(INTERFACE)
contract = {row["metric"]: row["value"] for row in load(CONTRACT)}
errors: list[str] = []

expected_interface_fields = [
    "test_window_id", "simulation_step_index_1based", "interval_start_local_civil", "interval_end_local_civil",
    "interval_start_utc_derived", "interval_end_utc_derived", "dt_seconds", "dt_hours", "p_net_kW", "p_pv_model_kW",
    "p_bess_observed_kW_positive_charge_negative_discharge", "p_building_load_kW", "e_net_kWh", "e_pv_model_kWh",
    "e_bess_observed_kWh_positive_charge_negative_discharge", "e_building_load_kWh", "observed_power_sample_semantics",
    "source_admission_status", "utc_mapping_admission_status", "timezone_evidence_status",
]
if list(interface[0]) != expected_interface_fields:
    errors.append("interface_schema does not match frozen interface schema")
if len(source) != len(interface) or len(interface) != 3360:
    errors.append(f"row_count source={len(source)} interface={len(interface)}")

for index, (src, out) in enumerate(zip(source, interface), start=1):
    mapping = {
        "test_window_id": src["test_window_id"],
        "simulation_step_index_1based": src["simulation_step_index_1based"],
        "interval_start_local_civil": src["interval_start_local_civil"],
        "interval_end_local_civil": src["interval_end_local_civil"],
        "interval_start_utc_derived": src["interval_start_utc_derived"],
        "interval_end_utc_derived": src["interval_end_utc_derived"],
        "dt_seconds": src["dt_seconds"],
        "dt_hours": src["dt_hours"],
        "p_net_kW": src["p_net_kW"],
        "p_pv_model_kW": src["p_pv_model_kW"],
        "p_bess_observed_kW_positive_charge_negative_discharge": src["p_bess_kW_positive_charge_negative_discharge"],
        "p_building_load_kW": src["p_building_load_kW"],
        "e_net_kWh": src["e_net_kWh"],
        "e_pv_model_kWh": src["e_pv_model_kWh"],
        "e_bess_observed_kWh_positive_charge_negative_discharge": src["e_bess_kWh_positive_charge_negative_discharge"],
        "e_building_load_kWh": src["e_building_load_kWh"],
        "observed_power_sample_semantics": src["power_sample_semantics"],
        "source_admission_status": src["source_admission_status"],
        "utc_mapping_admission_status": src["utc_mapping_admission_status"],
        "timezone_evidence_status": src["timezone_evidence_status"],
    }
    if out != mapping:
        errors.append(f"row {index}: interface payload not an exact intended projection of source")
        break
    local_start = datetime.strptime(out["interval_start_local_civil"], FMT_LOCAL)
    local_end = datetime.strptime(out["interval_end_local_civil"], FMT_LOCAL)
    utc_start = datetime.strptime(out["interval_start_utc_derived"], FMT_UTC)
    utc_end = datetime.strptime(out["interval_end_utc_derived"], FMT_UTC)
    closure = float(out["p_building_load_kW"]) - float(out["p_net_kW"]) - float(out["p_pv_model_kW"]) + float(out["p_bess_observed_kW_positive_charge_negative_discharge"])
    checks = [
        int(out["simulation_step_index_1based"]) == index,
        local_end - local_start == STEP,
        utc_end - utc_start == STEP,
        float(out["dt_hours"]) == 0.25 and int(out["dt_seconds"]) == 900,
        abs(closure) <= 1e-9,
        abs(float(out["e_net_kWh"]) - .25 * float(out["p_net_kW"])) <= 1e-10,
        abs(float(out["e_pv_model_kWh"]) - .25 * float(out["p_pv_model_kW"])) <= 1e-10,
        abs(float(out["e_bess_observed_kWh_positive_charge_negative_discharge"]) - .25 * float(out["p_bess_observed_kW_positive_charge_negative_discharge"])) <= 1e-10,
        abs(float(out["e_building_load_kWh"]) - .25 * float(out["p_building_load_kW"])) <= 1e-10,
        out["source_admission_status"] == "eligible_for_building_load_dynamic_input",
        out["utc_mapping_admission_status"] == "eligible_UTC_mapping_unambiguous_local_time",
    ]
    if not all(checks):
        errors.append(f"row {index}: failed checks={[i for i, ok in enumerate(checks) if not ok]}")
        break

expected_contract = {
    "rule_version": "v1.0_trade_street_dynamic_model_preparation_contract",
    "source_file": SOURCE,
    "interface_file": INTERFACE,
    "test_window_id": "benchmark_35d",
    "interval_count": str(len(interface)),
    "interval_start_local_civil": interface[0]["interval_start_local_civil"],
    "interval_end_local_civil": interface[-1]["interval_end_local_civil"],
    "time_axis_primary": "timestamp_local_civil; derived UTC is retained only for traceable external joins",
    "time_step": "fixed 900 seconds = 0.25 h",
    "missing_data_policy": "No imputed records are present; do not synthesize values for excluded intervals",
    "timezone_evidence_status": interface[0]["timezone_evidence_status"],
}
for key, value in expected_contract.items():
    if contract.get(key) != value:
        errors.append(f"contract {key}: expected={value!r}; actual={contract.get(key)!r}")

required_nonempty_policy_keys = [
    "scope", "observed_power_convention", "observed_power_boundary", "required_dynamic_states",
    "required_control_decisions", "required_parameter_evidence_before_simulation", "initial_state_policy",
    "optimization_boundary_warning", "publication_limit",
]
for key in required_nonempty_policy_keys:
    if not contract.get(key, "").strip():
        errors.append(f"contract missing/nonempty policy: {key}")
# Guard against an unsupported hidden numerical parameter in a preparation contract.
for key in ("required_parameter_evidence_before_simulation", "initial_state_policy", "optimization_boundary_warning"):
    if any(char.isdigit() for char in contract[key]):
        errors.append(f"contract {key} contains an unsupported numeric parameter")

print("INDEPENDENT_AUDIT_DYNAMIC_MODEL_INTERFACE_CONTRACT")
print(f"source_rows={len(source)}; interface_rows={len(interface)}")
print(f"local_range={interface[0]['interval_start_local_civil']}..{interface[-1]['interval_end_local_civil']}")
print(f"errors={errors}")
raise SystemExit(1 if errors else 0)
