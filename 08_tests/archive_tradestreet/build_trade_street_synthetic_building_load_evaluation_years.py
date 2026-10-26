from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
PV_NET = BASE / "62_TradeStreet_2016_2018_synthetic_reconstructed_PV_netload_evaluation_years.csv"
OUT = BASE / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
RULES = BASE / "65_TradeStreet_synthetic_reconstructed_PV_building_load_rules_and_scope.csv"
RAW_FMT, FMT = "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
YEARS = (2016, 2017, 2018)


def unique(name: str) -> dict[datetime, float]:
    staged: dict[datetime, list[float]] = defaultdict(list)
    with (RAW / name).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                staged[datetime.strptime(row["DateTime"].strip(), RAW_FMT)].append(float(row["RealPower"]))
            except (KeyError, ValueError):
                continue
    return {stamp: items[0] for stamp, items in staged.items() if len(items) == 1}


def day_type(stamp: datetime) -> str:
    return "weekday" if stamp.weekday() < 5 else "weekend"


def grid(year: int) -> list[datetime]:
    current, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    values = []
    while current < end:
        values.append(current)
        current += STEP
    return values


def closest(donors: list[tuple[datetime, float]], target: datetime) -> tuple[datetime, float]:
    return min(donors, key=lambda item: (abs((item[0].date() - target.date()).days), item[0]))

net, pv_raw, bess = unique("TradeStreetTotal.csv"), unique("TradeStreetPV.csv"), unique("TradeStreetBattery.csv")
# The only legitimate building-load source uses all three synchronous observed measurements.
closed = {
    stamp: net[stamp] + max(pv_raw[stamp], 0.0) - bess[stamp]
    for stamp in net.keys() & pv_raw.keys() & bess.keys()
    if stamp.year in (2017, 2018) and abs(bess[stamp]) <= 200.0
}
primary: dict[tuple[int, str, int, int], list[tuple[datetime, float]]] = defaultdict(list)
fallback: dict[tuple[int, int, int], list[tuple[datetime, float]]] = defaultdict(list)
for stamp, load in closed.items():
    primary[(stamp.month, day_type(stamp), stamp.hour, stamp.minute)].append((stamp, load))
    fallback[(stamp.month, stamp.hour, stamp.minute)].append((stamp, load))

with PV_NET.open("r", encoding="utf-8-sig", newline="") as handle:
    pv_net_rows = {datetime.strptime(row["timestamp_local_civil"], FMT): row for row in csv.DictReader(handle)}

rows = []
for year in YEARS:
    for stamp in grid(year):
        source_pv_net = pv_net_rows[stamp]
        if stamp in closed:
            load = closed[stamp]
            load_source = "observed_closed_total_plus_PV_minus_BESS"
            donor = ""
        else:
            candidates = primary.get((stamp.month, day_type(stamp), stamp.hour, stamp.minute), [])
            if candidates:
                donor_stamp, load = closest(candidates, stamp)
                load_source = "synthetic_load_donor_same_month_daytype_clock_from_2017_2018_closed_load"
                donor = donor_stamp.strftime(FMT)
            else:
                candidates = fallback.get((stamp.month, stamp.hour, stamp.minute), [])
                if not candidates:
                    raise RuntimeError(f"no closed-load donor for {stamp}")
                donor_stamp, load = closest(candidates, stamp)
                load_source = "synthetic_load_donor_same_month_clock_fallback_from_2017_2018_closed_load"
                donor = donor_stamp.strftime(FMT)
        rows.append({
            "timestamp_local_civil": stamp.strftime(FMT),
            "local_civil_year": year,
            "day_type": day_type(stamp),
            "p_pv_available_reconstructed_nonnegative_kW": source_pv_net["p_pv_reconstructed_nonnegative_kW"],
            "p_building_load_reconstructed_kW": f"{load:.12f}",
            "building_load_value_source": load_source,
            "building_load_donor_timestamp_local_civil": donor,
            "PV_value_source_from_62": source_pv_net["PV_load_value_source"],
            "PV_donor_timestamp_local_civil_from_62": source_pv_net["donor_timestamp_local_civil"],
            "historical_bess_reconstruction": "not_performed_BESS_is_future_EMS_control_variable",
            "evaluation_data_class": "synthetic_reconstructed_PV_building_load_evaluation_year_not_actual_observation",
            "prohibited_use": "not_for_actual_year_statistics_or_formal_20_year_NPC_LCOE_degradation_replacements",
        })

with OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

rules = [
    {"category": "identity", "item": "data_class", "rule": "synthetic reconstructed PV-building-load evaluation years; not complete actual observed years", "status": "frozen_user_choice"},
    {"category": "source", "item": "building_load_closure", "rule": "observed donor load=P_net_raw+max(PV_raw,0)-P_BESS_raw; only synchronous unique 2017-2018 Total/PV/BESS records with abs(BESS)<=200 kW", "status": "implemented"},
    {"category": "source", "item": "building_load_primary_donor", "rule": "missing target load uses 2017-2018 closed-load donor with same month, weekday/weekend and clock; closest calendar date; donor timestamp retained", "status": "implemented"},
    {"category": "source", "item": "building_load_fallback_donor", "rule": "when primary donor unavailable, use 2017-2018 closed-load donor with same month and clock; donor timestamp retained and fallback labeled", "status": "implemented"},
    {"category": "PV", "item": "PV_source", "rule": "PV available power is passed from 62 with its separate raw/synthetic donor provenance", "status": "implemented"},
    {"category": "BESS", "item": "historical_BESS", "rule": "not reconstructed or copied; future off-grid optimization treats BESS power as an EMS decision variable", "status": "frozen_user_choice"},
    {"category": "scope", "item": "grid", "rule": "2016 leap year=35136 and 2017/2018=35040 local-civil 15-minute records", "status": "implemented"},
    {"category": "formal_evaluation", "item": "prohibition", "rule": "not valid for actual-year statistics or formal 20-year NPC, LCOE, degradation or replacement claims", "status": "hard_gate"},
]
with RULES.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rules[0]))
    writer.writeheader()
    writer.writerows(rules)

print("SYNTHETIC_RECONSTRUCTED_PV_BUILDING_LOAD_YEARS")
print(f"closed_donor_points={len(closed)}; output_rows={len(rows)}")
print(f"load_source_counts={dict(Counter(row['building_load_value_source'] for row in rows))}")
print(f"output={OUT}")
print(f"rules={RULES}")
