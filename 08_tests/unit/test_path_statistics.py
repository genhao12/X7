# -*- coding: utf-8 -*-
"""F6 path-activation statistical gate tests."""
import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from identification.path_statistics import evaluate_path_activation


class PathStatisticsTests(unittest.TestCase):
    def test_consistent_effect_passes_all_statistical_gates(self):
        effects = np.linspace(0.0010, 0.0020, 52)
        result = evaluate_path_activation(
            effects,
            absolute_threshold=1e-4,
            direction_threshold=0.90,
            nc1_residual=0.0,
            nc3_residual=0.0,
            bootstrap_samples=500,
            seed=7,
        )
        self.assertTrue(result.activated)
        self.assertGreater(result.ci_low, 0.0)
        self.assertEqual(result.direction_consistency, 1.0)
        self.assertTrue(all(result.gates.values()))

    def test_zero_effect_is_statistical_zero(self):
        result = evaluate_path_activation(
            np.zeros(52),
            absolute_threshold=1e-4,
            nc1_residual=0.0,
            nc3_residual=0.0,
            bootstrap_samples=200,
            seed=7,
        )
        self.assertFalse(result.activated)
        self.assertFalse(result.gates["absolute_effect"])
        self.assertFalse(result.gates["bootstrap_ci_excludes_zero"])

    def test_negative_control_failure_blocks_activation(self):
        result = evaluate_path_activation(
            np.full(52, 0.002),
            absolute_threshold=1e-4,
            nc1_residual=2e-4,
            nc3_residual=0.0,
            negative_control_threshold=1e-4,
            bootstrap_samples=200,
            seed=7,
        )
        self.assertFalse(result.activated)
        self.assertFalse(result.gates["nc1"])


if __name__ == "__main__":
    unittest.main()
