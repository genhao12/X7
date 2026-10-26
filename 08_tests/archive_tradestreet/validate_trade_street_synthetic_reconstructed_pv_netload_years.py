from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
DATA = BASE / "62_TradeStreet_2016_2018_synthetic_reconstructed_PV_netload_evaluation_years.csv"
FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
EXPECTED = {2016: 35136, 2017: 35040, 2018: 35040}
ALLOWED = {
    "observed_unique_total_and_PV",
    "synthetic_donor_same_month_daytype_clock_from_2017_2018",
    "synthetic_donor_same_month_daytype_clock_fallback_from_2017_2018",
}

with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

errors = []
by_year: dict[int, list[dict[str, str]]] = {year: [] for year in EXPECTED}
for row in rows:
    stamp = datetime.strptime(row["timestamp_local_civil"], FMT)
    year = int(row["local_civil_year"])
    if stamp.year != year or year not in EXPECTED:
        errors.append(f"timestamp_year_mismatch={row['timestamp_local_civil']}")
        continue
    by_year[year].append(row)
    pv = float(row["p_pv_reconstructed_nonnegative_kW"])
    if pv < 0:
        errors.append(f"negative_pv={row['timestamp_local_civil']}")
    if row["PV_load_value_source"] not in ALLOWED:
        errors.append(f"invalid_source={row['PV_load_value_source']}")
    if row["evaluation_data_class"] != "synthetic_reconstructed_evaluation_year_not_actual_observation":
        errors.append(f"bad_class={row['timestamp_local_civil']}")
    source = row["PV_load_value_source"]
    donor = row["donor_timestamp_local_civil"]
    if source == "observed_unique_total_and_PV" and donor:
        errors.append(f"observed_has_donor={row['timestamp_local_civil']}")
    if source != "observed_unique_total_and_PV":
        if not donor:
            errors.append(f"synthetic_missing_donor={row['timestamp_local_civil']}")
        else:
            donor_stamp = datetime.strptime(donor, FMT)
            if donor_stamp.year not in {2017, 2018}:
                errors.append(f"donor_year={donor}")
            if donor_stamp.month != stamp.month or donor_stamp.hour != stamp.hour or donor_stamp.minute != stamp.minute:
                errors.append(f"donor_calendar_clock_mismatch={row['timestamp_local_civil']}->{donor}")

for year, values in by_year.items():
    if len(values) != EXPECTED[year]:
        errors.append(f"year_count_{year}={len(values)}")
        continue
    stamps = [datetime.strptime(row["timestamp_local_civil"], FMT) for row in values]
    if stamps[0] != datetime(year, 1, 1) or stamps[-1] != datetime(year + 1, 1, 1) - STEP:
        errors.append(f"year_boundary_{year}")
    if any(right - left != STEP for left, right in zip(stamps, stamps[1:])):
        errors.append(f"non_15min_grid_{year}")

counts = Counter(row["PV_load_value_source"] for row in rows)
print("SYNTHETIC_RECONSTRUCTED_PV_NETLOAD_VALIDATION")
print(f"rows={len(rows)}; source_counts={dict(counts)}")
print(f"errors={len(errors)}")
for error in errors[:30]:
    print(error)
if errors:
    raise SystemExit(1)
print("result=PASS; complete synthetic PV-netload evaluation years with explicit donor provenance; BESS history remains intentionally unreconstructed")
