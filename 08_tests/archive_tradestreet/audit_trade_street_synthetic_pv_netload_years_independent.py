from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
RAW = ROOT / "UCSD-Microgrid-Database" / "UCSD-Microgrid-Database-master" / "Data Files" / "Trade Street Off-campus Microgrid"
BASE = ROOT / "base data"
DATA = BASE / "62_TradeStreet_2016_2018_synthetic_reconstructed_PV_netload_evaluation_years.csv"
FMT_RAW = "%m/%d/%Y %H:%M"
FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)


def raw_series(name: str) -> dict[datetime, float]:
    staged: dict[datetime, list[float]] = defaultdict(list)
    with (RAW / name).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                staged[datetime.strptime(row["DateTime"].strip(), FMT_RAW)].append(float(row["RealPower"]))
            except (KeyError, ValueError):
                continue
    return {stamp: items[0] for stamp, items in staged.items() if len(items) == 1}

net, pv = raw_series("TradeStreetTotal.csv"), raw_series("TradeStreetPV.csv")
observed = {stamp: (net[stamp], max(pv[stamp], 0.0)) for stamp in net.keys() & pv.keys()}
with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

errors = []
source_counts = Counter()
by_year = defaultdict(list)
for row in rows:
    stamp = datetime.strptime(row["timestamp_local_civil"], FMT)
    by_year[stamp.year].append(row)
    source = row["PV_load_value_source"]
    source_counts[source] += 1
    p_net, p_pv = float(row["p_net_reconstructed_kW"]), float(row["p_pv_reconstructed_nonnegative_kW"])
    if p_pv < 0:
        errors.append(f"negative_pv:{stamp}")
    if source == "observed_unique_total_and_PV":
        if stamp not in observed or any(abs(actual - expected) > 1e-9 for actual, expected in zip((p_net, p_pv), observed[stamp])):
            errors.append(f"observed_mismatch:{stamp}")
    else:
        donor = datetime.strptime(row["donor_timestamp_local_civil"], FMT)
        if donor not in observed:
            errors.append(f"invalid_donor:{stamp}->{donor}")
            continue
        donor_net, donor_pv = observed[donor]
        if any(abs(actual - expected) > 1e-9 for actual, expected in zip((p_net, p_pv), (donor_net, donor_pv))):
            errors.append(f"donor_value_mismatch:{stamp}->{donor}")
        if donor.year not in {2017, 2018}:
            errors.append(f"donor_wrong_year:{stamp}->{donor}")
        if (donor.month, donor.hour, donor.minute) != (stamp.month, stamp.hour, stamp.minute):
            errors.append(f"donor_time_mismatch:{stamp}->{donor}")
        if source == "synthetic_donor_same_month_daytype_clock_from_2017_2018" and donor.weekday() // 5 != stamp.weekday() // 5:
            errors.append(f"primary_donor_daytype_mismatch:{stamp}->{donor}")
    if row["bess_observation_status"] == "not_reconstructed_control_variable_for_future_EMS" and row["p_bess_observed_kW_positive_charge_negative_discharge"]:
        errors.append(f"bess_missing_status_has_value:{stamp}")

for year, year_rows in by_year.items():
    expected = 35136 if year == 2016 else 35040
    stamps = [datetime.strptime(row["timestamp_local_civil"], FMT) for row in year_rows]
    if len(stamps) != expected or stamps[0] != datetime(year, 1, 1) or stamps[-1] != datetime(year + 1, 1, 1) - STEP:
        errors.append(f"grid_boundary_or_count:{year}")
    if any(right - left != STEP for left, right in zip(stamps, stamps[1:])):
        errors.append(f"grid_continuity:{year}")

print("INDEPENDENT_SYNTHETIC_PV_NETLOAD_AUDIT")
print(f"rows={len(rows)}; source_counts={dict(source_counts)}; errors={len(errors)}")
for item in errors[:40]:
    print(item)
if errors:
    raise SystemExit(1)
print("result=PASS; observed values and donor values independently match raw Total/PV; annual grids complete; BESS correctly not fabricated")
