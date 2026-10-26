import csv
import collections
from datetime import datetime

root = "D:\\matlab\\C\\base data\\"
with open(root + "07_TradeStreetTotal_paper_QC_layer.csv", encoding="utf-8-sig", newline="") as file:
    total = list(csv.DictReader(file))
with open(root + "08_TradeStreetPV_paper_QC_layer.csv", encoding="utf-8-sig", newline="") as file:
    pv = list(csv.DictReader(file))
with open(root + "09_TradeStreet_15min_paper_QC_netload_pv_load_candidate.csv", encoding="utf-8-sig", newline="") as file:
    joint = list(csv.DictReader(file))

parse = lambda value: datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
imputed = [row for row in total + pv if row["is_imputed"] == "1"]
same_clock = all(
    parse(row["timestamp_local_civil_unresolved"]).time() == parse(row["donor_timestamp_local_civil_unresolved"]).time()
    for row in imputed
    if row["imputation_method"] == "paper_persistence_same_time_same_day_type"
)
donor_strictly_prior = all(
    parse(row["donor_timestamp_local_civil_unresolved"]) < parse(row["timestamp_local_civil_unresolved"])
    for row in imputed
)
same_day_type = all(
    row["target_day_type_US_calendar"] == row["donor_day_type_US_calendar"]
    for row in imputed
    if row["imputation_method"] == "paper_persistence_same_time_same_day_type"
)
all_donors_have_source_row = all(row["donor_source_row"] for row in imputed)
closure = max(
    abs(
        float(row["p_load_candidate_qc_kW_equals_p_net_plus_p_pv"])
        - float(row["p_net_qc_kW"])
        - float(row["p_pv_qc_kW"])
    )
    for row in joint
)
raw_dynamic = [row for row in joint if row["scenario_use_status"] == "raw_complete_eligible_for_dynamic_layer"]

print(f"imputed={len(imputed)}")
print(f"same_clock={same_clock}")
print(f"donor_strictly_prior={donor_strictly_prior}")
print(f"same_day_type={same_day_type}")
print(f"all_donors_have_source_rows={all_donors_have_source_row}")
print(f"max_closure_residual_kW={closure}")
print(f"raw_dynamic_rows={len(raw_dynamic)}")
print(f"raw_dynamic_contains_imputation={sum(row['contains_imputation'] != '0' for row in raw_dynamic)}")
print(f"status_counts={dict(collections.Counter(row['scenario_use_status'] for row in joint))}")
