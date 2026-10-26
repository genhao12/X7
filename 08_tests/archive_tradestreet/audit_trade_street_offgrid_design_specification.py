from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
INTERFACE = BASE / "48_TradeStreet_local_civil_dynamic_model_input_interface_35d.csv"

with INTERFACE.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))
if not rows:
    raise ValueError("48 interface is empty")

pv = [float(row["p_pv_model_kW"]) for row in rows]
load = [float(row["p_building_load_kW"]) for row in rows]
checks = {
    "all_pv_nonnegative": all(value >= 0 for value in pv),
    "all_load_nonnegative": all(value >= 0 for value in load),
    "fixed_15min_step": all(row["dt_seconds"] == "900" and row["dt_hours"] == "0.25" for row in rows),
    "pv_profile_exceeds_user_candidate_max_150kW": max(pv) > 150,
}
print("OFFGRID_DESIGN_INPUT_COMPATIBILITY_AUDIT")
print(f"intervals={len(rows)}")
print(f"pv_max_kW={max(pv):.6f}; pv_energy_kWh={sum(pv)*0.25:.6f}")
print(f"load_max_kW={max(load):.6f}; load_energy_kWh={sum(load)*0.25:.6f}")
print(f"checks={checks}")
print("required_interpretation=Do not directly treat the historical PV kW series as a 90-150 kWp candidate availability series without a verified historical reference nameplate capacity or an explicit normalization policy.")
