from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
DATA = BASE / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
RULES = BASE / "65_TradeStreet_synthetic_reconstructed_PV_building_load_rules_and_scope.csv"
PV_NET = BASE / "62_TradeStreet_2016_2018_synthetic_reconstructed_PV_netload_evaluation_years.csv"
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

net, pv, bess = unique("TradeStreetTotal.csv"), unique("TradeStreetPV.csv"), unique("TradeStreetBattery.csv")
closed = {stamp: net[stamp] + max(pv[stamp], 0.0) - bess[stamp] for stamp in net.keys() & pv.keys() & bess.keys() if stamp.year in {2017, 2018} and abs(bess[stamp]) <= 200}
with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))
with PV_NET.open("r", encoding="utf-8-sig", newline="") as handle:
    pv_net = {datetime.strptime(row["timestamp_local_civil"], FMT): row for row in csv.DictReader(handle)}
with RULES.open("r", encoding="utf-8-sig", newline="") as handle:
    rules = list(csv.DictReader(handle))

errors, by_year = [], defaultdict(list)
for row in rows:
    stamp = datetime.strptime(row["timestamp_local_civil"], FMT)
    by_year[stamp.year].append(row)
    if row["historical_bess_reconstruction"] != "not_performed_BESS_is_future_EMS_control_variable":
        errors.append(f"bess_fabrication_status:{stamp}")
    pv_reference = pv_net.get(stamp)
    if not pv_reference or row["p_pv_available_reconstructed_nonnegative_kW"] != pv_reference["p_pv_reconstructed_nonnegative_kW"]:
        errors.append(f"pv_passthrough:{stamp}")
    load = float(row["p_building_load_reconstructed_kW"])
    source = row["building_load_value_source"]
    if source == "observed_closed_total_plus_PV_minus_BESS":
        expected = closed.get(stamp)
        if expected is None or abs(load - expected) > 1e-9 or row["building_load_donor_timestamp_local_civil"]:
            errors.append(f"observed_closure:{stamp}")
    else:
        donor_text = row["building_load_donor_timestamp_local_civil"]
        if not donor_text:
            errors.append(f"missing_load_donor:{stamp}")
            continue
        donor = datetime.strptime(donor_text, FMT)
        expected = closed.get(donor)
        if expected is None or abs(load - expected) > 1e-9:
            errors.append(f"load_donor_value:{stamp}->{donor}")
        if donor.year not in {2017, 2018} or donor.month != stamp.month or donor.hour != stamp.hour or donor.minute != stamp.minute:
            errors.append(f"load_donor_key:{stamp}->{donor}")
    if row["evaluation_data_class"] != "synthetic_reconstructed_PV_building_load_evaluation_year_not_actual_observation":
        errors.append(f"data_class:{stamp}")

for year, expected_count in {2016: 35136, 2017: 35040, 2018: 35040}.items():
    group = by_year[year]
    stamps = [datetime.strptime(row["timestamp_local_civil"], FMT) for row in group]
    if len(group) != expected_count or stamps[0] != datetime(year, 1, 1) or stamps[-1] != datetime(year + 1, 1, 1) - STEP:
        errors.append(f"grid_boundary_count:{year}")
    if any(right - left != STEP for left, right in zip(stamps, stamps[1:])):
        errors.append(f"grid_step:{year}")

if not {"data_class", "building_load_closure", "building_load_primary_donor", "PV_source", "historical_BESS", "prohibition"} <= {row["item"] for row in rules}:
    errors.append("rules_missing")
print("INDEPENDENT_SYNTHETIC_PV_BUILDING_LOAD_AUDIT")
print(f"rows={len(rows)}; load_source_counts={dict(Counter(row['building_load_value_source'] for row in rows))}; errors={len(errors)}")
for error in errors[:30]:
    print(error)
if errors:
    raise SystemExit(1)
print("result=PASS; complete grids, PV passthrough, observed closures and synthetic load donor mappings are correct; BESS history remains unconstructed")
