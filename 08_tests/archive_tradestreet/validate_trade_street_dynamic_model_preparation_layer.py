from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SOURCE = "47_TradeStreet_local_civil_dynamic_simulation_input_35d.csv"
INTERFACE = "48_TradeStreet_local_civil_dynamic_model_input_interface_35d.csv"
CONTRACT = "49_TradeStreet_local_civil_dynamic_model_preparation_contract.csv"
FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


source = load(SOURCE)
interface = load(INTERFACE)
contract = {row["metric"]: row["value"] for row in load(CONTRACT)}
errors = []
if len(source) != len(interface):
    errors.append(f"row_count source={len(source)} interface={len(interface)}")
for i, (src, out) in enumerate(zip(source, interface), start=1):
    expected = {
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
    if out != expected:
        errors.append(f"payload_mismatch_row={i}")
        break
    start = datetime.strptime(out["interval_start_local_civil"], FMT)
    end = datetime.strptime(out["interval_end_local_civil"], FMT)
    if end - start != STEP:
        errors.append(f"local_step_row={i}")
        break
    closure = float(out["p_building_load_kW"]) - float(out["p_net_kW"]) - float(out["p_pv_model_kW"]) + float(out["p_bess_observed_kW_positive_charge_negative_discharge"])
    if abs(closure) > 1e-9:
        errors.append(f"power_closure_row={i}")
        break

expected_contract = {
    "source_file": SOURCE,
    "interface_file": INTERFACE,
    "interval_count": str(len(interface)),
    "interval_start_local_civil": interface[0]["interval_start_local_civil"],
    "interval_end_local_civil": interface[-1]["interval_end_local_civil"],
    "time_axis_primary": "timestamp_local_civil; derived UTC is retained only for traceable external joins",
    "time_step": "fixed 900 seconds = 0.25 h",
}
for key, value in expected_contract.items():
    if contract.get(key) != value:
        errors.append(f"contract {key}: expected={value!r}; actual={contract.get(key)!r}")

print("STRICT_DYNAMIC_MODEL_PREPARATION_VALIDATION")
print(f"source_rows={len(source)}; interface_rows={len(interface)}")
print(f"interval={interface[0]['interval_start_local_civil']}..{interface[-1]['interval_end_local_civil']}")
print(f"errors={errors}")
raise SystemExit(1 if errors else 0)
