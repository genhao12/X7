from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(r"D:\matlab\C\base data")
FMT_UTC = "%Y-%m-%dT%H:%M:%SZ"
STEP = timedelta(minutes=15)
LA = ZoneInfo("America/Los_Angeles")
FILES = {
    "benchmark_24h": "31_TradeStreet_dynamic_simulation_input_24h.csv",
    "benchmark_7d": "32_TradeStreet_dynamic_simulation_input_7d.csv",
    "benchmark_35d": "33_TradeStreet_dynamic_simulation_input_35d.csv",
}


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse(value: str) -> datetime:
    return datetime.strptime(value, FMT_UTC).replace(tzinfo=timezone.utc)

errors: Counter[str] = Counter()
notes: list[str] = []
series: dict[str, list[dict[str, str]]] = {}
for test_id, name in FILES.items():
    rows = load(name)
    series[test_id] = rows
    for i, row in enumerate(rows, start=1):
        start = parse(row["interval_start_utc"])
        end = parse(row["interval_end_utc"])
        checks = [
            int(row["simulation_step_index_1based"]) == i,
            end - start == STEP,
            row["power_sample_timestamp_utc"] == row["interval_end_utc"],
            row["source_timestamp_utc"] == end.strftime("%Y-%m-%d %H:%M:%S"),
            float(row["dt_hours"]) == 0.25 and int(row["dt_seconds"]) == 900,
            abs(float(row["p_building_load_kW"]) - float(row["p_net_kW"]) - float(row["p_pv_model_kW"]) + float(row["p_bess_kW_positive_charge_negative_discharge"])) <= 1e-9,
            abs(float(row["e_net_kWh"]) - 0.25 * float(row["p_net_kW"])) <= 1e-10,
            abs(float(row["e_pv_model_kWh"]) - 0.25 * float(row["p_pv_model_kW"])) <= 1e-10,
            abs(float(row["e_bess_kWh_positive_charge_negative_discharge"]) - 0.25 * float(row["p_bess_kW_positive_charge_negative_discharge"])) <= 1e-10,
            abs(float(row["e_building_load_kWh"]) - 0.25 * float(row["p_building_load_kW"])) <= 1e-10,
            row["source_admission_status"] == "eligible_for_building_load_dynamic_input",
        ]
        if not all(checks):
            errors[f"{test_id}_mechanical"] += 1
            if len(notes) < 8:
                notes.append(f"mechanical_failure file={name}; row={i}; failed={[j for j,v in enumerate(checks) if not v]}")

# Nested prefix relationship. Files intentionally use distinct test_window_id values,
# so compare every other field row-by-row.
def same_interval_payload(left: dict[str, str], right: dict[str, str]) -> bool:
    return all(left[key] == right[key] for key in left if key != "test_window_id")

if not all(same_interval_payload(a, b) for a, b in zip(series["benchmark_24h"], series["benchmark_7d"][:len(series["benchmark_24h"])])):
    errors["nested_prefix"] += 1
if not all(same_interval_payload(a, b) for a, b in zip(series["benchmark_7d"], series["benchmark_35d"][:len(series["benchmark_7d"])])):
    errors["nested_prefix"] += 1

# Physical plausibility under the currently frozen UTC->LA assumption.
# Conservative nighttime screen: local clock 22:00-05:45 should not contain material PV production.
for test_id, rows in series.items():
    night_positive = []
    daytime_stats = []
    for row in rows:
        utc = parse(row["interval_end_utc"])
        local = utc.astimezone(LA)
        pv = float(row["p_pv_model_kW"])
        if local.hour >= 22 or local.hour < 6:
            if pv > 1.0:
                night_positive.append((row["interval_end_utc"], local.strftime("%Y-%m-%d %H:%M:%S %z"), pv))
        daytime_stats.append((local.hour, pv))
    if night_positive:
        errors[f"{test_id}_nighttime_pv_under_utc_assumption"] = len(night_positive)
        notes.append(f"{test_id}: local-night PV>1kW count={len(night_positive)}; first={night_positive[0]}")
    notes.append(f"{test_id}: max_pv_by_local_hour=" + str({h: round(max(v for hh,v in daytime_stats if hh==h),3) for h in sorted({hh for hh,_ in daytime_stats})}))

print("STRICT_SIMULATION_INPUT_MECHANICAL_AND_PHYSICAL_AUDIT")
print("mechanical_errors=", {k:v for k,v in errors.items() if "nighttime" not in k})
print("physical_utc_assumption_errors=", {k:v for k,v in errors.items() if "nighttime" in k})
for note in notes:
    print("DETAIL", note)
# Exit 2 distinguishes a physical-assumption conflict from file arithmetic errors.
if any("nighttime" in key for key in errors):
    raise SystemExit(2)
raise SystemExit(1 if errors else 0)
