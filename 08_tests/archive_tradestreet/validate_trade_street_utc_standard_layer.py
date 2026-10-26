import csv
import datetime
from collections import Counter

BASE = r"D:\matlab\C\base data\\"
FMT = "%Y-%m-%d %H:%M:%S"


def load(filename):
    with open(BASE + filename, encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


admission = load("17_TradeStreet_15min_building_load_dynamic_admission_layer.csv")
scope = load("18_TradeStreet_dynamic_input_scope_layer.csv")
utc_admission = load("20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv")
utc_scope = load("21_TradeStreet_dynamic_input_scope_UTC_primary_local_calendar_layer.csv")

print("row_counts", len(admission), len(scope), len(utc_admission), len(utc_scope))
print("utc_verbatim", all(row["timestamp_utc"] == admission[index]["timestamp_local_civil_unresolved"] for index, row in enumerate(utc_admission)))
timestamps = [datetime.datetime.strptime(row["timestamp_utc"], FMT) for row in utc_admission]
print("utc_strict_15min", all((timestamps[index] - timestamps[index - 1]).total_seconds() == 900 for index in range(1, len(timestamps))))
print("maps17", all(row["building_load_dynamic_admission_status"] == admission[index]["building_load_dynamic_admission_status"] and row["p_net_raw_kW"] == admission[index]["p_net_raw_kW"] for index, row in enumerate(utc_admission)))
print("maps18", all(row["building_load_dynamic_admission_status"] == scope[index]["building_load_dynamic_admission_status"] and row["permitted_use"] == scope[index]["permitted_use"] for index, row in enumerate(utc_scope)))
print("offset_status", dict(Counter((row["utc_offset"], row["dst_status"]) for row in utc_admission)))
print("local_duplicate_extra", len(utc_admission) - len(set(row["timestamp_local"] for row in utc_admission)))
print("illegal_offset", sum(row["utc_offset"] not in ["-07:00", "-08:00"] for row in utc_admission))
