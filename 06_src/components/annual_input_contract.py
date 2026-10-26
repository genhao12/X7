"""Authoritative annual PV/load input contract for rectification v2."""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PVInputContract:
    reference_kw: float = 233.0
    converter_rule: str = "candidate_nominal"
    reject_amplification_when_clipped: bool = True


@dataclass(frozen=True)
class ClippingReport:
    detected: bool
    plateau_count: int
    plateau_value_kw: float
    reason: str

    def to_dict(self):
        return asdict(self)


def detect_high_power_clipping(
    raw_kw,
    reference_kw: float = 233.0,
    min_plateau_points: int = 8,
    tolerance_kw: float = 0.05,
) -> ClippingReport:
    """Detect a contiguous high-power flat top; repeated donor values alone do not count."""
    x = np.asarray(raw_kw, dtype=float)
    if x.size < 2:
        return ClippingReport(False, 0, 0.0, "no_high_power_points")
    same_high = (
        (x[1:] >= 0.75 * reference_kw)
        & (x[:-1] >= 0.75 * reference_kw)
        & (np.abs(np.diff(x)) <= tolerance_kw)
    )
    longest = 0
    run = 0
    end = 0
    for i, same in enumerate(same_high, start=1):
        run = run + 1 if same else 0
        if run > longest:
            longest = run
            end = i
    plateau_points = longest + 1 if longest else 0
    detected = plateau_points >= min_plateau_points
    value = float(np.mean(x[end - longest : end + 1])) if plateau_points else 0.0
    reason = "contiguous_high_power_plateau" if detected else "no_contiguous_plateau"
    return ClippingReport(detected, plateau_points, value, reason)


def scale_pv_available(
    raw_kw,
    nominal_kw: float,
    contract: PVInputContract = PVInputContract(),
    clipping_report: ClippingReport = None,
) -> np.ndarray:
    """Scale historical available PV from 233 kW and clip at candidate/converter rating."""
    nominal_kw = float(nominal_kw)
    if nominal_kw <= 0:
        raise ValueError("nominal_kw must be positive")
    x = np.maximum(np.asarray(raw_kw, dtype=float), 0.0)
    report = clipping_report or detect_high_power_clipping(x, contract.reference_kw)
    if (
        nominal_kw > contract.reference_kw
        and report.detected
        and contract.reject_amplification_when_clipped
    ):
        raise ValueError(
            "clipped historical PV requires irradiance/capacity-factor reconstruction"
        )
    scaled = x * nominal_kw / contract.reference_kw
    return np.minimum(scaled, nominal_kw)


def validate_year_frame(frame: pd.DataFrame, year: int) -> dict:
    expected = 35136 if year % 4 == 0 else 35040
    if len(frame) != expected:
        raise ValueError(f"year {year}: {len(frame)} points, expected {expected}")
    ts = pd.to_datetime(frame["timestamp"])
    if ts.duplicated().any() or not (
        ts.diff().dropna() == pd.Timedelta(minutes=15)
    ).all():
        raise ValueError(f"year {year}: timestamps are not a unique 15-minute grid")
    if frame[["pv_raw_kw", "load_kw"]].isna().any().any():
        raise ValueError(f"year {year}: missing PV/load input")
    if (frame[["pv_raw_kw", "load_kw"]] < 0).any().any():
        raise ValueError(f"year {year}: negative PV/load input")
    return {
        "year": year,
        "points": expected,
        "start": str(ts.iloc[0]),
        "end": str(ts.iloc[-1]),
    }


def load_evaluation_years(csv_path: Path) -> Dict[int, pd.DataFrame]:
    raw = pd.read_csv(csv_path, parse_dates=["timestamp_local_civil"])
    years = {}
    for year in (2016, 2017, 2018):
        src = raw.loc[raw["local_civil_year"] == year].copy()
        frame = pd.DataFrame(
            {
                "timestamp": src["timestamp_local_civil"],
                "pv_raw_kw": src[
                    "p_pv_available_reconstructed_nonnegative_kW"
                ].ffill().bfill(),
                "load_kw": src["p_building_load_reconstructed_kW"].ffill().bfill(),
            }
        ).reset_index(drop=True)
        validate_year_frame(frame, year)
        years[year] = frame
    return years
