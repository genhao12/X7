import csv
from collections import Counter

ROOT = r"D:\matlab\C\base data\\"


def load(filename):
    with open(ROOT + filename, encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


candidates = load("15_TradeStreet_outlier_candidate_review_layer.csv")
admission = load("17_TradeStreet_15min_building_load_dynamic_admission_layer.csv")
print("candidate_flag_values", Counter((row["stream"], repr(row["outlier_candidate_flag"])) for row in candidates))
pending = [row for row in admission if row["building_load_dynamic_admission_status"] == "pending_manual_review_total_or_pv_outlier_candidate"]
print("pending_rows", len(pending))
print("pending_samples", pending[:3])
for row in pending[:3]:
    timestamp = row["timestamp_local_civil_unresolved"]
    print("matching_candidate_records", timestamp, [candidate for candidate in candidates if candidate["timestamp_local_civil_unresolved"] == timestamp])
