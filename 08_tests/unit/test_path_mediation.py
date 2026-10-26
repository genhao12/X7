# -*- coding: utf-8 -*-
"""F7 path-aligned Shapley validation tests."""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from identification.mediation_lite import compute_shapley, validate_path_mediation


class PathMediationTests(unittest.TestCase):
    def setUp(self):
        self.values = {}
        for i in range(16):
            code = format(i, "04b")
            c, _, p, _ = [int(bit) for bit in code]
            self.values[code] = 2.0 * c + 1.0 * p + 1.0 * c * p

    def test_path_effect_closes_and_interaction_passes(self):
        shapley = {name: compute_shapley(self.values, name) for name in "CEPI"}
        result = validate_path_mediation(
            path_effect=4.0,
            coalition_values=self.values,
            shapley_values=shapley,
            closure_threshold=0.05,
            interaction_threshold=0.30,
        )
        self.assertTrue(result["all_pass"])
        self.assertAlmostEqual(result["closure_relative_error"], 0.0)
        self.assertAlmostEqual(result["max_interaction_fraction"], 0.25)

    def test_path_mismatch_fails_closure(self):
        shapley = {name: compute_shapley(self.values, name) for name in "CEPI"}
        result = validate_path_mediation(
            path_effect=5.0,
            coalition_values=self.values,
            shapley_values=shapley,
        )
        self.assertFalse(result["closure_pass"])
        self.assertFalse(result["all_pass"])


if __name__ == "__main__":
    unittest.main()
