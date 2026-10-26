from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
FILE = "56_TradeStreet_native_annual_data_preparation_and_20year_continuity_register.csv"
with (BASE / FILE).open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))
errors = []
if len(rows) != 8:
    errors.append(f"row_count={len(rows)}")
by_sequence = {row["sequence"]: row for row in rows}
if by_sequence.get("A1", {}).get("status") != "blocked_external_data":
    errors.append("A1 status")
if "35,040" not in by_sequence.get("A1", {}).get("acceptance_rule", "") or "35,136" not in by_sequence.get("A1", {}).get("acceptance_rule", ""):
    errors.append("A1 point counts")
if by_sequence.get("A5", {}).get("status") != "ready_after_A4":
    errors.append("A5 status")
if "no reset" not in by_sequence.get("A5", {}).get("acceptance_rule", ""):
    errors.append("A5 continuity")
if by_sequence.get("A7", {}).get("status") != "permitted_limited_scope":
    errors.append("A7 status")
if by_sequence.get("A8", {}).get("status") != "blocked_by_A1_A6":
    errors.append("A8 status")
print("STRICT_ANNUAL_DATA_PREPARATION_REGISTER_VALIDATION")
print(f"rows={len(rows)}; errors={errors}")
raise SystemExit(1 if errors else 0)
