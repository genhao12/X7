from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(r"D:\matlab\C\base data")
FILE = BASE / "33_TradeStreet_dynamic_simulation_input_35d.csv"
FMT = "%Y-%m-%dT%H:%M:%SZ"
LA = ZoneInfo("America/Los_Angeles")

with FILE.open("r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

# Hypothesis A: timestamp text is UTC, converted to Los Angeles local time.
# Hypothesis B: timestamp text is already Los Angeles civil time (no UTC shift).
results = {}
for hypothesis in ("A_text_is_UTC", "B_text_is_LA_local_civil"):
    nighttime = []
    pv_by_hour: dict[int, list[float]] = {}
    for row in rows:
        timestamp = datetime.strptime(row["interval_end_utc"], FMT)
        if hypothesis == "A_text_is_UTC":
            hour = timestamp.replace(tzinfo=timezone.utc).astimezone(LA).hour
        else:
            hour = timestamp.hour
        pv = float(row["p_pv_model_kW"])
        pv_by_hour.setdefault(hour, []).append(pv)
        if (hour >= 22 or hour < 6) and pv > 1.0:
            nighttime.append((row["interval_end_utc"], hour, pv))
    results[hypothesis] = {
        "nighttime_pv_gt_1kw": len(nighttime),
        "first_nighttime": nighttime[:1],
        "max_pv_by_hour": {h: round(max(v), 3) for h, v in sorted(pv_by_hour.items())},
    }

print("TIMESTAMP_INTERPRETATION_PHYSICAL_COMPARISON")
for key, value in results.items():
    print(key, value)
