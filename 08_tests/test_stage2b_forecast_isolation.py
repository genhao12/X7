import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import causal_persistence_forecast, perfect_forecast


def test_causal_forecast_cannot_see_poisoned_future():
    load_a = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    load_b = np.array([10.0, 999.0, 999.0, 999.0, 999.0])
    pv_a = np.array([5.0, 6.0, 7.0, 8.0, 9.0])
    pv_b = np.array([5.0, 999.0, 999.0, 999.0, 999.0])
    a = causal_persistence_forecast(load_a, pv_a, 0, 5)
    b = causal_persistence_forecast(load_b, pv_b, 0, 5)
    assert np.array_equal(a.load_kw, b.load_kw)
    assert np.array_equal(a.pv_kw, b.pv_kw)
    assert a.mode == b.mode == "causal_persistence"
    assert a.paper_citable is True


def test_oracle_forecast_is_explicitly_non_citable():
    window = perfect_forecast(np.arange(6.0), np.arange(6.0), 0, 5)
    assert np.array_equal(window.load_kw, np.arange(5.0))
    assert window.mode == "perfect_forecast_upper_bound"
    assert window.paper_citable is False
