import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from simulation.capacity_gate import evaluate_admission, h2_lower_bound_diagnostics


def _base_metrics():
    return {
        "block_soc_delta": 0.001,
        "block_h2_delta_fraction": 0.001,
        "yearly": {
            2016: {"lpsp": 0.0, "critical_satisfaction": 1.0, "soc_delta": -0.2, "h2_delta_fraction": -0.4},
            2017: {"lpsp": 0.0, "critical_satisfaction": 1.0, "soc_delta": 0.1, "h2_delta_fraction": 0.2},
            2018: {"lpsp": 0.0, "critical_satisfaction": 1.0, "soc_delta": 0.101, "h2_delta_fraction": 0.201},
        },
        "three_year_lpsp": 0.0,
        "three_year_critical_satisfaction": 1.0,
        "max_power_residual_fraction": 0.0,
        "h2_residual_fraction": 0.0,
        "bounds_pass": True,
        "minimum_stable_power_pass": True,
        "cycle_converged": True,
    }


def test_single_year_inventory_drift_does_not_fail_block_closed_candidate():
    result = evaluate_admission(_base_metrics())
    assert result.admissible is True
    assert "single_year_state_closure" not in result.checks


def test_single_year_reliability_still_gates_admission():
    metrics = _base_metrics()
    metrics["yearly"][2017]["lpsp"] = 0.02
    result = evaluate_admission(metrics)
    assert result.admissible is False
    assert "yearly_lpsp" in result.failure_reasons


def test_h2_lower_bound_reports_first_time_duration_and_unserved_energy():
    ts = pd.date_range("2017-01-01", periods=4, freq="15min")
    diag = h2_lower_bound_diagnostics(
        ts,
        np.array([2.0, 1.0, 1.0, 2.0]),
        np.array([0.0, 2.0, 4.0, 0.0]),
        h2_min_kg=1.0,
        dt_h=0.25,
    )
    assert diag["first_timestamp"] == str(ts[1])
    assert diag["duration_h"] == 0.5
    assert diag["unserved_energy_kwh"] == 1.5
