from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
DATA = BASE / "62_TradeStreet_2016_2018_synthetic_reconstructed_PV_netload_evaluation_years.csv"
RULES = BASE / "63_TradeStreet_synthetic_reconstructed_PV_netload_rules_and_scope.csv"
RAW_FMT, FMT = "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)


def unique(name: str) -> dict[datetime, float]:
    staged = defaultdict(list)
    with (RAW / name).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                staged[datetime.strptime(row["DateTime"].strip(), RAW_FMT)].append(float(row["RealPower"]))
            except (KeyError, ValueError):
                pass
    return {stamp: values[0] for stamp, values in staged.items() if len(values) == 1}

net, pv = unique("TradeStreetTotal.csv"), unique("TradeStreetPV.csv")
paired = {stamp: (net[stamp], max(pv[stamp], 0.0)) for stamp in net.keys() & pv.keys()}
with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))
with RULES.open("r", encoding="utf-8-sig", newline="") as handle:
    rules = list(csv.DictReader(handle))

errors = []
required_rule_items = {"data_class", "observed_retention", "synthetic_donor_primary", "synthetic_donor_fallback", "negative_values", "missing_BESS", "closure", "prohibition"}
if not required_rule_items <= {row["item"] for row in rules}:
    errors.append("missing_required_rule_items")
if any("formal 20-year" not in row["rule"] for row in rules if row["item"] == "prohibition"):
    errors.append("formal_gate_missing")
by_year = defaultdict(list)
for row in rows:
    stamp = datetime.strptime(row["timestamp_local_civil"], FMT)
    by_year[stamp.year].append(row)
    source = row["PV_load_value_source"]
    net_value, pv_value = float(row["p_net_reconstructed_kW"]), float(row["p_pv_reconstructed_nonnegative_kW"])
    if pv_value < 0:
        errors.append(f"negative_pv:{stamp}")
    if row["evaluation_data_class"] != "synthetic_reconstructed_evaluation_year_not_actual_observation":
        errors.append(f"identity:{stamp}")
    if source == "observed_unique_total_and_PV":
        expected = paired.get(stamp)
        if expected is None or abs(net_value - expected[0]) > 1e-9 or abs(pv_value - expected[1]) > 1e-9:
            errors.append(f"observed_value:{stamp}")
    else:
        donor_text = row["donor_timestamp_local_civil"]
        if not donor_text:
            errors.append(f"missing_donor:{stamp}")
            continue
        donor = datetime.strptime(donor_text, FMT)
        expected = paired.get(donor)
        if expected is None or abs(net_value - expected[0]) > 1e-9 or abs(pv_value - expected[1]) > 1e-9:
            errors.append(f"donor_value:{stamp}->{donor}")
        if donor.year not in {2017, 2018} or donor.month != stamp.month or donor.hour != stamp.hour or donor.minute != stamp.minute:
            errors.append(f"donor_key:{stamp}->{donor}")
    bess = row["p_bess_observed_kW_positive_charge_negative_discharge"]
    status = row["bess_observation_status"]
    if (not bess and status != "not_reconstructed_control_variable_for_future_EMS") or (bess and status != "observed_unique"):
        errors.append(f"bess_status:{stamp}")

for year, expected_count in {2016: 35136, 2017: 35040, 2018: 35040}.items():
    group = by_year.get(year, [])
    stamps = [datetime.strptime(row["timestamp_local_civil"], FMT) for row in group]
    if len(group) != expected_count or not stamps:
        errors.append(f"count:{year}")
        continue
    if stamps[0] != datetime(year, 1, 1) or stamps[-1] != datetime(year + 1, 1, 1) - STEP:
        errors.append(f"boundary:{year}")
    if any(right - left != STEP for left, right in zip(stamps, stamps[1:])):
        errors.append(f"continuity:{year}")

print("SECOND_PASS_62_63_INDEPENDENT_AUDIT")
print(f"rows={len(rows)}; source_counts={dict(Counter(row['PV_load_value_source'] for row in rows))}; errors={len(errors)}")
for error in errors[:30]:
    print(error)
if errors:
    raise SystemExit(1)
print("result=PASS; 62 values, provenance, complete grids, BESS treatment, and 63 scope rules are consistent")
