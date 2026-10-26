from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
OUTPUT = BASE / "62_TradeStreet_2016_2018_synthetic_reconstructed_PV_netload_evaluation_years.csv"
RULES = BASE / "63_TradeStreet_synthetic_reconstructed_PV_netload_rules_and_scope.csv"
FMT = "%m/%d/%Y %H:%M"
OUT_FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
YEARS = (2016, 2017, 2018)


def read_unique(filename: str) -> dict[datetime, float]:
    staged: dict[datetime, list[float]] = defaultdict(list)
    with (RAW / filename).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                staged[datetime.strptime(row["DateTime"].strip(), FMT)].append(float(row["RealPower"]))
            except (KeyError, ValueError):
                continue
    return {stamp: values[0] for stamp, values in staged.items() if len(values) == 1}


def day_type(stamp: datetime) -> str:
    return "weekday" if stamp.weekday() < 5 else "weekend"


def grid(year: int) -> list[datetime]:
    current, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    result = []
    while current < end:
        result.append(current)
        current += STEP
    return result

net = read_unique("TradeStreetTotal.csv")
pv_raw = read_unique("TradeStreetPV.csv")
bess = read_unique("TradeStreetBattery.csv")

# A source value is eligible only when Total and PV occur at the same local-civil timestamp.
observed = {
    stamp: (net[stamp], max(pv_raw[stamp], 0.0))
    for stamp in net.keys() & pv_raw.keys()
}

# Build donor pools strictly from observed 2017-2018 timestamps; exclusion of 2016 prevents circular self-donation.
pools: dict[tuple[int, int, str, int, int], list[tuple[datetime, float, float]]] = defaultdict(list)
for stamp, (p_net, p_pv) in observed.items():
    if stamp.year not in (2017, 2018):
        continue
    key = (stamp.month, stamp.day, day_type(stamp), stamp.hour, stamp.minute)
    pools[key].append((stamp, p_net, p_pv))

# Fallback uses same month, weekday/weekend, clock. It is marked separately.
fallback: dict[tuple[int, str, int, int], list[tuple[datetime, float, float]]] = defaultdict(list)
for stamp, (p_net, p_pv) in observed.items():
    if stamp.year in (2017, 2018):
        fallback[(stamp.month, day_type(stamp), stamp.hour, stamp.minute)].append((stamp, p_net, p_pv))


def choose(donors: list[tuple[datetime, float, float]], target: datetime) -> tuple[datetime, float, float]:
    # Deterministic nearest calendar-day donor; tie resolves to earlier timestamp.
    return min(donors, key=lambda item: (abs((item[0].date() - target.date()).days), item[0]))

rows = []
for year in YEARS:
    for stamp in grid(year):
        p_bess = bess.get(stamp)
        if stamp in observed:
            p_net, p_pv = observed[stamp]
            value_source = "observed_unique_total_and_PV"
            donor = ""
        else:
            exact_key = (stamp.month, stamp.day, day_type(stamp), stamp.hour, stamp.minute)
            candidates = pools.get(exact_key, [])
            if candidates:
                donor_stamp, p_net, p_pv = choose(candidates, stamp)
                value_source = "synthetic_donor_same_month_daytype_clock_from_2017_2018"
                donor = donor_stamp.strftime(OUT_FMT)
            else:
                fallback_key = (stamp.month, day_type(stamp), stamp.hour, stamp.minute)
                candidates = fallback.get(fallback_key, [])
                if not candidates:
                    raise RuntimeError(f"no donor available for {stamp}")
                donor_stamp, p_net, p_pv = choose(candidates, stamp)
                value_source = "synthetic_donor_same_month_daytype_clock_fallback_from_2017_2018"
                donor = donor_stamp.strftime(OUT_FMT)
        # This layer intentionally does not reconstruct observed BESS or building load closure.
        rows.append({
            "timestamp_local_civil": stamp.strftime(OUT_FMT),
            "local_civil_year": year,
            "day_type": day_type(stamp),
            "p_net_reconstructed_kW": f"{p_net:.12f}",
            "p_pv_reconstructed_nonnegative_kW": f"{p_pv:.12f}",
            "p_bess_observed_kW_positive_charge_negative_discharge": "" if p_bess is None else f"{p_bess:.12f}",
            "bess_observation_status": "observed_unique" if p_bess is not None else "not_reconstructed_control_variable_for_future_EMS",
            "PV_load_value_source": value_source,
            "donor_timestamp_local_civil": donor,
            "evaluation_data_class": "synthetic_reconstructed_evaluation_year_not_actual_observation",
            "prohibited_use": "not_for_actual_year_statistics_or_formal_20_year_NPC_LCOE_degradation_replacements",
        })

with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

rules = [
    {"category": "identity", "item": "data_class", "rule": "synthetic reconstructed evaluation years; not complete actual observed years", "status": "frozen_user_choice"},
    {"category": "scope", "item": "years", "rule": "2016, 2017, 2018 complete local-civil 15-minute grids", "status": "implemented"},
    {"category": "PV_load", "item": "observed_retention", "rule": "retain a value only when raw Total and PV both have one numeric record at same timestamp", "status": "implemented"},
    {"category": "PV_load", "item": "synthetic_donor_primary", "rule": "for missing total/PV pair, donor pool=2017-2018 values with same month, weekday/weekend and clock; deterministic closest calendar date", "status": "implemented"},
    {"category": "PV_load", "item": "synthetic_donor_fallback", "rule": "when primary pool empty, donor pool=2017-2018 same month, weekday/weekend and clock; all fallback points labeled", "status": "implemented"},
    {"category": "PV", "item": "negative_values", "rule": "raw negative PV is converted to 0 only in reconstructed model field; source raw data unchanged", "status": "implemented"},
    {"category": "BESS", "item": "missing_BESS", "rule": "do not fabricate BESS history; keep observed values only; missing BESS is future EMS control variable", "status": "frozen_user_choice"},
    {"category": "building_load", "item": "closure", "rule": "not emitted because BESS is intentionally not reconstructed; future optimization computes load balance using decision variables", "status": "frozen_user_choice"},
    {"category": "formal_evaluation", "item": "prohibition", "rule": "not valid for actual-year statistics or formal 20-year NPC, LCOE, degradation or replacement claims", "status": "hard_gate"},
]
with RULES.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rules[0]))
    writer.writeheader()
    writer.writerows(rules)

from collections import Counter
counts = Counter(row["PV_load_value_source"] for row in rows)
print("SYNTHETIC_RECONSTRUCTED_PV_NETLOAD_YEARS")
print(f"rows={len(rows)}; per_year={{2016: 35136, 2017: 35040, 2018: 35040}}")
print(f"value_source_counts={dict(counts)}")
print(f"output={OUTPUT}")
print(f"rules={RULES}")
