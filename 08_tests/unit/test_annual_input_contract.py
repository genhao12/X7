import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from components.annual_input_contract import (
    PVInputContract,
    detect_high_power_clipping,
    scale_pv_available,
    validate_year_frame,
)


def test_scale_uses_233_kw_reference_and_candidate_clip():
    raw = np.array([-1.0, 116.5, 233.0, 244.424])
    out = scale_pv_available(raw, nominal_kw=150.0, contract=PVInputContract())
    np.testing.assert_allclose(out, [0.0, 75.0, 150.0, 150.0])


def test_load_is_not_scaled_with_pv_candidate():
    frame = pd.DataFrame({"pv_raw_kw": [116.5], "load_kw": [80.0]})
    frame["pv_available_kw"] = scale_pv_available(
        frame["pv_raw_kw"], 100.0, PVInputContract()
    )
    assert frame.loc[0, "load_kw"] == 80.0
    assert frame.loc[0, "pv_available_kw"] == 50.0


def test_flat_high_power_plateau_rejects_amplification():
    raw = np.r_[np.linspace(0, 180, 100), np.repeat(200.0, 24)]
    report = detect_high_power_clipping(raw, reference_kw=233.0)
    assert report.detected is True
    with pytest.raises(ValueError, match="irradiance/capacity-factor reconstruction"):
        scale_pv_available(raw, 300.0, PVInputContract(), clipping_report=report)


def test_noncontiguous_repeated_high_values_do_not_false_flag_clipping():
    raw = np.tile([0.0, 200.0, 150.0, 200.0], 20)
    report = detect_high_power_clipping(raw, reference_kw=233.0)
    assert report.detected is False


@pytest.mark.parametrize("year,points", [(2016, 35136), (2017, 35040), (2018, 35040)])
def test_year_validation_requires_complete_local_civil_grid(year, points):
    ts = pd.date_range(f"{year}-01-01", periods=points, freq="15min")
    frame = pd.DataFrame({"timestamp": ts, "pv_raw_kw": 0.0, "load_kw": 1.0})
    assert validate_year_frame(frame, year)["points"] == points
