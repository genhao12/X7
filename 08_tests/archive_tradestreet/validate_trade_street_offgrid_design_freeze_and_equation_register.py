from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SPEC = "52_TradeStreet_offgrid_PV_H2_BESS_design_freeze_and_open_items.csv"
EQUATIONS = "53_TradeStreet_offgrid_model_equation_and_validation_register.csv"

with (BASE / SPEC).open("r", encoding="utf-8-sig", newline="") as handle:
    spec = list(csv.DictReader(handle))
with (BASE / EQUATIONS).open("r", encoding="utf-8-sig", newline="") as handle:
    equations = list(csv.DictReader(handle))
errors = []
if len(spec) != 17:
    errors.append(f"spec_rows={len(spec)}")
if len(equations) != 10:
    errors.append(f"equation_rows={len(equations)}")
by_item = {row["item"]: row for row in spec}
required = {
    "Energy exchange boundary": "Off-grid; no electricity purchase, electricity sale, external energy replenishment, hydrogen purchase or external hydrogen supply",
    "Usable-energy design search": "baseline 120; search 90 to 180",
    "Charge/discharge power design search": "search 25 to 45; do not use historical 200 kW observation screen as design upper bound",
    "PV sizing search": "baseline 90; search 90 to 150",
    "Historical reference PV capacity": "Trade Street Warehouse PV nameplate = 233",
}
for item, expected in required.items():
    if by_item.get(item, {}).get("value_or_rule") != expected:
        errors.append(f"spec {item}")

pv_scale = by_item.get("Candidate PV availability scaling", {})
if "P_PV_historical_233kW(t)*(P_PV_nom_candidate/233)" not in pv_scale.get("value_or_rule", ""):
    errors.append("PV scaling")
if "211.49 kW" not in pv_scale.get("open_item_or_caveat", ""):
    errors.append("PV raw-profile incompatibility warning")

by_block = {row["block"]: row for row in equations}
balance = by_block.get("Electrical balance", {}).get("equation_or_rule", "")
if "+P_cur" in balance.split(";")[0]:
    errors.append("curtailment incorrectly placed as electrical load")
if "P_cur=P_PV_available_candidate-P_PV_used" not in balance:
    errors.append("curtailment definition missing")
if "LPSP=sum(P_uns*dt)/sum(P_load*dt)<=0.01" not in by_block.get("Reliability stage", {}).get("equation_or_rule", ""):
    errors.append("LPSP equation")
if "D_BAT=(1-SOH_BAT)/(1-0.80)" not in by_block.get("Damage/replacement stage", {}).get("equation_or_rule", ""):
    errors.append("battery damage equation")

print("STRICT_OFFGRID_DESIGN_FREEZE_AND_EQUATION_VALIDATION")
print(f"spec_rows={len(spec)}; equation_rows={len(equations)}")
print(f"errors={errors}")
raise SystemExit(1 if errors else 0)
