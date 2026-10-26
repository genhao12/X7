# -*- coding: utf-8 -*-
"""F5 exact-time replacement economics tests."""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from economics.npc_lite import (
    EconomicConfig,
    build_timed_replacement_schedule,
    compute_annual_opex,
    compute_equipment_capex,
    compute_npc_timed,
    compute_replacement_cost,
)


class TimedReplacementEconomicsTests(unittest.TestCase):
    def test_builds_fractional_year_events_without_rounding_to_one_per_year(self):
        schedule = build_timed_replacement_schedule(
            {"BAT": 0.25, "PEMFC": 0.80, "PEMEL": 0.0},
            horizon_years=3,
        )
        self.assertEqual(schedule["BAT"], [])
        self.assertEqual(schedule["PEMEL"], [])
        self.assertEqual(len(schedule["PEMFC"]), 2)
        self.assertAlmostEqual(schedule["PEMFC"][0], 1.25)
        self.assertAlmostEqual(schedule["PEMFC"][1], 2.50)

    def test_preserves_multiple_replacements_per_year(self):
        schedule = build_timed_replacement_schedule(
            {"BAT": 0.0, "PEMFC": 2.40, "PEMEL": 0.0},
            horizon_years=1,
        )
        self.assertEqual(len(schedule["PEMFC"]), 2)
        self.assertAlmostEqual(schedule["PEMFC"][0], 1.0 / 2.4)
        self.assertAlmostEqual(schedule["PEMFC"][1], 2.0 / 2.4)

    def test_npc_discounts_replacements_at_fractional_event_time(self):
        config = EconomicConfig(
            discount_rate=0.10,
            horizon_years=2,
            power_electronics_life=15,
            pv_kwp=1.0,
            bat_kwh=1.0,
            pemfc_kw=1.0,
            pemel_kw=1.0,
            h2_kg=1.0,
        )
        schedule = {"BAT": [], "PEMFC": [0.5, 1.5], "PEMEL": [],
                    "power_electronics": []}
        result = compute_npc_timed(config, schedule, annual_energy_delivered_mwh=1.0)

        capex = compute_equipment_capex(config)
        opex = compute_annual_opex(config, capex)
        repl = compute_replacement_cost(config, capex, "PEMFC")
        expected = (
            capex["total_initial"]
            + opex / 1.10 + opex / 1.10 ** 2
            + repl / 1.10 ** 0.5 + repl / 1.10 ** 1.5
        )
        self.assertAlmostEqual(result["npc_cny"], expected)
        self.assertEqual(result["replacement_counts"]["PEMFC"], 2)


if __name__ == "__main__":
    unittest.main()
