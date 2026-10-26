from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
OUT = BASE / "56_TradeStreet_native_annual_data_preparation_and_20year_continuity_register.csv"

rows = [
    {"sequence": "A1", "work_item": "Acquire or identify complete native actual years", "status": "blocked_external_data", "acceptance_rule": "At least 3 distinct actual local-civil years; each selected nonleap year has 35,040 raw 15-min points and each leap year has 35,136", "current_evidence": "34 dynamic-admission source has no complete actual year", "required_input_or_decision": "additional raw Trade Street Total/PV/BESS data or an explicitly approved separate reconstruction source", "prohibited_shortcut": "Do not concatenate partial calendar years, repeat 35-day data, or silently fill gaps and label result actual year"},
    {"sequence": "A2", "work_item": "Reapply audit and admission pipeline to each candidate year", "status": "blocked_by_A1", "acceptance_rule": "Raw audit, PV semantic correction, BESS boundary screen and building-load closure are complete; provenance and exclusions retained", "current_evidence": "Existing pipeline supports this but source years are incomplete", "required_input_or_decision": "candidate complete raw years", "prohibited_shortcut": "Do not use paper-QC imputed layer for formal degradation/economic chronology"},
    {"sequence": "A3", "work_item": "Handle DST on each native year", "status": "blocked_by_A1", "acceptance_rule": "Spring nonexistent local labels and fall ambiguous labels follow explicit audited rule; source point count remains native-calendar consistent", "current_evidence": "v4 local-civil policy exists", "required_input_or_decision": "complete native annual source timelines", "prohibited_shortcut": "Do not silently fabricate fall-back second occurrence or shift spring-forward labels"},
    {"sequence": "A4", "work_item": "Freeze 20-year year-order schedule", "status": "blocked_by_A1", "acceptance_rule": "List selected actual years and fixed repetition order for all 20 simulated years", "current_evidence": "User requires >=3 years in fixed order", "required_input_or_decision": "three qualified annual series and chosen fixed order", "prohibited_shortcut": "Do not randomize annual order after optimization"},
    {"sequence": "A5", "work_item": "Implement cross-year continuous-state handoff", "status": "ready_after_A4", "acceptance_rule": "SOC_BAT, m_H2, SOH_BAT, PEMFC health and PEMEL health at year end equal next-year initial states; no reset", "current_evidence": "Rule frozen in 54", "required_input_or_decision": "state equations and qualified annual chronology", "prohibited_shortcut": "Do not reset SOC, H2 inventory or health at annual boundaries"},
    {"sequence": "A6", "work_item": "Freeze missing component and cost parameters", "status": "blocked_parameter_evidence", "acceptance_rule": "Every parameter has a single traceable manufacturer or cited literature source, unit, applicability range and version date", "current_evidence": "54 hard gate identifies missing values", "required_input_or_decision": "manufacturer/citation parameter table", "prohibited_shortcut": "Do not mix untraced values or use generic defaults as final evaluation parameters"},
    {"sequence": "A7", "work_item": "Run Phase A code tests", "status": "permitted_limited_scope", "acceptance_rule": "Test constraints, exact hydrogen leakage update, PV scaling, P_uns/P_cur accounting and lexicographic non-degradation on 35-day input", "current_evidence": "48, 52-55 are verified", "required_input_or_decision": "clarify auxiliary-power rating denominator and use parameterized device models", "prohibited_shortcut": "Do not report Phase A results as 20-year cost, LCOE, degradation or replacement results"},
    {"sequence": "A8", "work_item": "Authorize formal 20-year evaluation", "status": "blocked_by_A1_A6", "acceptance_rule": "A1-A6 all passed; formal gate B1-B4 in 55 changed to permitted with evidence", "current_evidence": "B1-B4 are currently blocked", "required_input_or_decision": "qualified annual data plus complete parameter evidence", "prohibited_shortcut": "Do not bypass formal gates by assumptions not recorded in the parameter source table"},
]
with OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
print(f"annual_preparation_rows={len(rows)}")
