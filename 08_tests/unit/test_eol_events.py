# -*- coding: utf-8 -*-
"""F4 EOL replacement-event model tests."""
import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from simulation.eol_events import advance_replacement_events, operating_hour_damage
from simulation.closed_loop_lite import compute_damage


class AdvanceReplacementEventsTests(unittest.TestCase):
    def test_accumulates_damage_without_replacement(self):
        result = advance_replacement_events(
            {"fc": np.array([0.10, 0.20])},
            initial_state={"fc": 0.30},
            dt_hours=1.0,
        )
        self.assertAlmostEqual(result.final_state["fc"], 0.60)
        self.assertEqual(result.replacement_counts["fc"], 0)
        self.assertEqual(result.events, [])

    def test_crossing_eol_records_fractional_time_and_resets_residual(self):
        result = advance_replacement_events(
            {"fc": np.array([0.30])},
            initial_state={"fc": 0.80},
            dt_hours=2.0,
            start_time_hours=10.0,
        )
        self.assertAlmostEqual(result.final_state["fc"], 0.10)
        self.assertEqual(result.replacement_counts["fc"], 1)
        self.assertAlmostEqual(result.events[0].time_hours, 10.0 + 2.0 * (0.20 / 0.30))
        self.assertEqual(result.events[0].device, "fc")
        self.assertEqual(result.events[0].replacement_number, 1)

    def test_supports_multiple_replacements_inside_one_step(self):
        result = advance_replacement_events(
            {"fc": np.array([2.40])},
            initial_state={"fc": 0.20},
            dt_hours=1.0,
        )
        self.assertAlmostEqual(result.final_state["fc"], 0.60)
        self.assertEqual(result.replacement_counts["fc"], 2)
        self.assertAlmostEqual(result.events[0].time_hours, 0.80 / 2.40)
        self.assertAlmostEqual(result.events[1].time_hours, 1.80 / 2.40)

    def test_tracks_devices_independently(self):
        result = advance_replacement_events(
            {
                "bat": np.array([0.15, 0.10]),
                "fc": np.array([0.60, 0.60]),
                "el": np.array([0.05, 0.05]),
            },
            initial_state={"bat": 0.80, "fc": 0.00, "el": 0.20},
            dt_hours=1.0,
        )
        self.assertEqual(result.replacement_counts, {"bat": 1, "fc": 1, "el": 0})
        self.assertAlmostEqual(result.final_state["bat"], 0.05)
        self.assertAlmostEqual(result.final_state["fc"], 0.20)
        self.assertAlmostEqual(result.final_state["el"], 0.30)
        self.assertEqual([e.device for e in result.events], ["bat", "fc"])

    def test_rejects_negative_damage(self):
        with self.assertRaisesRegex(ValueError, "negative damage"):
            advance_replacement_events(
                {"fc": np.array([0.10, -0.01])},
                initial_state={"fc": 0.0},
                dt_hours=1.0,
            )

    def test_operating_hour_damage_uses_active_time(self):
        damage = operating_hour_damage(
            power_kw=np.array([0.0, 20.0, 60.0, 100.0]),
            rated_power_kw=100.0,
            eol_operating_hours=2.0,
            dt_hours=0.25,
            active_threshold_fraction=0.50,
        )
        np.testing.assert_allclose(damage, [0.0, 0.0, 0.125, 0.125])

    def test_compute_damage_raw_policy_preserves_eol_crossing_increment(self):
        commands = np.array([[0.0, 1000.0, 0.0]])
        config = {
            "capacity": {"BAT_kWh": 10.0, "PEMFC_kW": 1.0, "PEMEL_kW": 1.0},
            "battery_constraints": {},
            "ems_parameters": {"start_stop_threshold_fraction": 0.1},
        }
        hp_func = lambda _d: (1.0, 1.0, 5.0)

        capped = compute_damage(
            hp_func, commands, config,
            D_state={"bat": 0.0, "fc": 0.95, "el": 0.0},
        )
        raw = compute_damage(
            hp_func, commands, config,
            D_state={"bat": 0.0, "fc": 0.95, "el": 0.0},
            eol_policy="raw",
        )

        # Capped: remaining = 1 - 0.95 = 0.05, so dD_fc capped at 0.05
        self.assertAlmostEqual(capped["dD_fc"][0], 0.05)
        # Raw: FC_K_RUN_H * eta_factor(1.095) * p_ratio(1000) * dt_h(0.25)
        #    = 2.4e-4 * 1.095 * 1000 * 0.25 = 0.0657
        self.assertAlmostEqual(raw["dD_fc"][0], 0.0657, places=3)
        self.assertGreater(raw["dD_fc"][0], capped["dD_fc"][0])
        self.assertAlmostEqual(raw["dD_fc_raw_series"][0], raw["dD_fc"][0])


if __name__ == "__main__":
    unittest.main()
