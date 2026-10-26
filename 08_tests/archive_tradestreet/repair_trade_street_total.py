import csv
import math
import os
from collections import Counter
from datetime import datetime

src = r"D:\matlab\C\UCSD-Microgrid-Database\UCSD-Microgrid-Database-master\Data Files\Trade Street Off-campus Microgrid\TradeStreetTotal.csv"
out = r"D:\matlab\C\base data\01_TradeStreetTotal_time_standardized.csv"
qc = r"D:\matlab\C\base data\01_TradeStreetTotal_quality_check.csv"
input_timestamp_format = "%m/%d/%Y %H:%M"

with open(src, encoding="utf-8-sig", newline="") as file:
    rows = [
        (
            source_row,
            record["DateTime"].strip(),
            record["RealPower"].strip(),
            datetime.strptime(record["DateTime"].strip(), input_timestamp_format),
        )
        for source_row, record in enumerate(csv.DictReader(file), start=2)
    ]

counts = Counter(row[3] for row in rows)
rows.sort(key=lambda row: (row[3], row[0]))

previous_timestamp = None
records = []
interval_counts = Counter()
missing_nan_count = 0
duplicate_marked_count = 0

for source_row, _raw_timestamp, power, timestamp in rows:
    interval = "" if previous_timestamp is None else int((timestamp - previous_timestamp).total_seconds() // 60)
    previous_timestamp = timestamp
    timestamp_status = "duplicate_timestamp" if counts[timestamp] > 1 else "valid_unique_timestamp"
    duplicate_marked_count += timestamp_status == "duplicate_timestamp"
    power_value_status = "missing_nan" if (not power or power.lower() == "nan" or not math.isfinite(float(power))) else "valid_numeric"
    missing_nan_count += power_value_status == "missing_nan"
    records.append([
        timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        power,
        source_row,
        timestamp_status,
        interval,
        power_value_status,
    ])
    interval_counts[interval] += 1

with open(out, "w", encoding="utf-8", newline="") as file:
    writer = csv.writer(file)
    writer.writerow([
        "timestamp_local_unresolved",
        "real_power_raw_kW",
        "source_row",
        "timestamp_status",
        "interval_to_previous_min",
        "power_value_status",
    ])
    writer.writerows(records)

unique_timestamps = sorted(counts)
unique_gaps = [
    int((unique_timestamps[index] - unique_timestamps[index - 1]).total_seconds() // 60)
    for index in range(1, len(unique_timestamps))
]

with open(qc, "w", encoding="utf-8", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["metric", "value"])
    writer.writerows([
        ["source_file", os.path.basename(src)],
        ["source_records", len(rows)],
        ["output_records", len(records)],
        ["timestamp_parse_format", input_timestamp_format],
        ["output_timestamp_format", "%Y-%m-%d %H:%M:%S"],
        ["sort_order", "ascending"],
        ["timezone_status", "unresolved - source provides no timezone label"],
        ["duplicate_timestamp_count", sum(count > 1 for count in counts.values())],
        ["duplicate_extra_record_count", sum(count - 1 for count in counts.values() if count > 1)],
        ["records_marked_duplicate_timestamp", duplicate_marked_count],
        ["missing_nan_power_count", missing_nan_count],
        ["valid_numeric_power_count", len(rows) - missing_nan_count],
        ["zero_minute_interval_count", interval_counts[0]],
        ["negative_interval_count", sum(count for interval, count in interval_counts.items() if isinstance(interval, int) and interval < 0)],
        ["non_15min_interval_record_count", sum(count for interval, count in interval_counts.items() if interval != "" and interval != 15)],
        ["non_15min_unique_timestamp_gap_count", sum(interval != 15 for interval in unique_gaps)],
        ["first_timestamp", records[0][0]],
        ["last_timestamp", records[-1][0]],
        ["semantic_status", "RealPower retained as raw measurement; not relabeled as load or net load"],
        ["processing_policy", "No records deleted, imputed, or power values altered; duplicates and NaN values retained with quality flags"],
    ])

print(f"records={len(records)}; missing_nan={missing_nan_count}; zero_intervals={interval_counts[0]}; non15_records={sum(count for interval, count in interval_counts.items() if interval != '' and interval != 15)}; unique_non15_gaps={sum(interval != 15 for interval in unique_gaps)}")
