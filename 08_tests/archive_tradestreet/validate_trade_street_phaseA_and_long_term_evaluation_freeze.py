from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
RULES = "54_TradeStreet_phaseA_and_20year_evaluation_rule_freeze.csv"
GATES = "55_TradeStreet_formal_evaluation_data_and_parameter_gate.csv"

with (BASE / RULES).open("r", encoding="utf-8-sig", newline="") as handle:
    rules = list(csv.DictReader(handle))
with (BASE / GATES).open("r", encoding="utf-8-sig", newline="") as handle:
    gates = list(csv.DictReader(handle))
errors = []
if len(rules) != 13:
    errors.append(f"rule_rows={len(rules)}")
if len(gates) != 5:
    errors.append(f"gate_rows={len(gates)}")
by_item = {row["item"]: row for row in rules}
checks = {
    "critical": "All reconstructed building load is critical load; no noncritical-load fraction or separate allocation",
    "reliability": "Critical-load satisfaction=1-E_uns/E_load>=0.99; LPSP=E_uns/E_load<=0.01; the two are identical under all-load-critical definition",
    "retention": "0.9995^(1/96)=0.9999947903777193",
    "economics": "Evaluation period=20 years; real discount rate=6%; real price escalation=0%; residual value=0",
}
actual = {
    "critical": by_item.get("Critical-load definition", {}).get("rule_or_value"),
    "reliability": by_item.get("Reliability hard constraint", {}).get("rule_or_value"),
    "retention": by_item.get("15-minute retention factor", {}).get("rule_or_value"),
    "economics": by_item.get("Frozen financial assumptions", {}).get("rule_or_value"),
}
for key, expected in checks.items():
    if actual[key] != expected:
        errors.append(key)
if "0.05%/day" not in by_item.get("Frozen equipment and auxiliary rules", {}).get("rule_or_value", ""):
    errors.append("leakage")
if "EOL SOH=0.80" not in by_item.get("Frozen end-of-life performance criteria", {}).get("rule_or_value", ""):
    errors.append("BESS_EOL")
by_gate = {row["gate_id"]: row for row in gates}
if by_gate.get("B1", {}).get("status") != "blocked_no_complete_actual_years":
    errors.append("B1_status")
if "zero complete actual years" not in by_gate.get("B1", {}).get("current_finding", ""):
    errors.append("B1_finding")
if by_gate.get("A0", {}).get("status") != "permitted_with_existing_data":
    errors.append("A0_status")

print("STRICT_PHASEA_LONG_TERM_RULE_FREEZE_VALIDATION")
print(f"rule_rows={len(rules)}; gate_rows={len(gates)}")
print(f"errors={errors}")
raise SystemExit(1 if errors else 0)
