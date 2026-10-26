# -*- coding: utf-8 -*-
"""Regression test for week-to-week SOC/H2 continuity."""
import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch


class EMSStateContinuityTests(unittest.TestCase):
    def test_second_chunk_starts_from_previous_hydrogen_and_soc(self):
        cfg = EMSConfig(
            pv_kwp=0.0, bat_kwh=10.0, pemfc_kw=20.0, pemel_kw=20.0,
            h2_kg=10.0, soc_min=0.15, soc_max=0.95, soc_initial=0.15,
            h2_min_fraction=0.10, h2_max_fraction=1.0, h2_initial_fraction=1.0,
            h2_leakage_per_day=0.0, min_stable_load_fc=0.10,
            min_stable_load_el=0.10, dt_min=15.0,
        )
        load = np.full(8, 10.0)
        pv = np.zeros(8)
        _, first = rule_based_dispatch(load, pv, cfg)
        first_h2 = float(first["h2_trace"][-1])
        first_soc = float(first["soc_trace"][-1])

        _, reset = rule_based_dispatch(load, pv, cfg)
        _, continued = rule_based_dispatch(
            load, pv, cfg, soc_initial=first_soc, h2_initial_kg=first_h2,
        )

        self.assertAlmostEqual(reset["h2_trace"][-1], first_h2)
        self.assertLess(continued["h2_trace"][-1], first_h2)
        self.assertAlmostEqual(continued["initial_h2_kg"], first_h2)
        self.assertAlmostEqual(continued["initial_soc"], first_soc)
        self.assertLess(continued["h2_residual"], 1e-10)


if __name__ == "__main__":
    unittest.main()
