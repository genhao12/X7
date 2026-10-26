"""TDD tests for EOL step-internal event model with full system state transfer.

Tests cover:
  1. Step-internal triggering (fractional time interpolation)
  2. Damage capping (dD = min(dD_raw, 1-D))
  3. Replacement reset (D→0, performance recovery to BOL)
  4. BAT actual energy retention and SOC recalculation
  5. H₂ and state machine continuous transfer
  6. Shutdown/recovery verification

All tests follow TDD: write test first (RED), then implement (GREEN).
"""
import sys
import unittest
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from simulation.eol_events import (
    advance_replacement_events,
    ReplacementEvent,
    ReplacementAdvanceResult,
)
from simulation.closed_loop_lite import compute_damage


# ── TDD-1: Step-internal triggering ──

class TestStepInternalTriggering(unittest.TestCase):
    """EOL events must be detected within a step, not at step boundaries."""

    def test_eol_detected_at_fractional_position_within_step(self):
        """D crosses 1.0 at a precise fraction within a single step."""
        # D starts at 0.8, increment = 0.4 → crosses at fraction 0.2/0.4 = 0.5
        result = advance_replacement_events(
            {"fc": np.array([0.4])},
            initial_state={"fc": 0.8},
            dt_hours=0.25,
            start_time_hours=100.0,
        )
        self.assertEqual(result.replacement_counts["fc"], 1)
        # Event time = 100.0 + (0 + 0.5) * 0.25 = 100.125
        self.assertAlmostEqual(result.events[0].time_hours, 100.125)
        # Remaining damage after EOL: 0.4 - 0.2 = 0.2
        self.assertAlmostEqual(result.final_state["fc"], 0.2)

    def test_multiple_crossings_in_single_step(self):
        """Multiple EOL crossings within one large-damage step."""
        # D starts at 0.5, increment = 1.8 → crosses at 0.5/1.8 and 1.5/1.8
        result = advance_replacement_events(
            {"fc": np.array([1.8])},
            initial_state={"fc": 0.5},
            dt_hours=1.0,
        )
        self.assertEqual(result.replacement_counts["fc"], 2)
        # First crossing: fraction = 0.5/1.8 → time = 0.2778
        self.assertAlmostEqual(result.events[0].time_hours, 0.5 / 1.8)
        # Second crossing: fraction = 1.5/1.8 → time = 0.8333
        self.assertAlmostEqual(result.events[1].time_hours, 1.5 / 1.8)
        # Remaining: 1.8 - 0.5 - 1.0 = 0.3
        self.assertAlmostEqual(result.final_state["fc"], 0.3)

    def test_exact_eol_at_step_boundary(self):
        """D reaches exactly 1.0 at end of step (no crossing, just touching)."""
        result = advance_replacement_events(
            {"fc": np.array([0.3])},
            initial_state={"fc": 0.7},
            dt_hours=1.0,
        )
        # D reaches exactly 1.0 → replacement at step end
        self.assertEqual(result.replacement_counts["fc"], 1)
        self.assertAlmostEqual(result.events[0].time_hours, 1.0)
        self.assertAlmostEqual(result.final_state["fc"], 0.0)


# ── TDD-2: Damage capping ──

class TestDamageCapping(unittest.TestCase):
    """Stepwise damage must be capped: dD_used(t) = min(dD_raw(t), 1-D(t))."""

    def test_cap_at_eol_boundary_in_closed_loop(self):
        """In closed_loop compute_damage with cap policy, damage never exceeds 1-D."""
        commands = np.array([[0.0, 1000.0, 0.0]])
        config = {
            "capacity": {"BAT_kWh": 10.0, "PEMFC_kW": 1.0, "PEMEL_kW": 1.0},
            "battery_constraints": {},
            "ems_parameters": {"start_stop_threshold_fraction": 0.1},
        }
        hp_func = lambda _d: (1.0, 1.0, 5.0)

        # FC at D=0.95 with large power → raw damage >> 0.05
        capped = compute_damage(
            hp_func, commands, config,
            D_state={"bat": 0.0, "fc": 0.95, "el": 0.0},
        )
        # Capped FC damage must not exceed 1 - 0.95 = 0.05
        self.assertLessEqual(capped["dD_fc"][0], 0.05 + 1e-10)

    def test_raw_policy_does_not_cap(self):
        """Raw policy returns uncapped damage for event-model use."""
        commands = np.array([[0.0, 1000.0, 0.0]])
        config = {
            "capacity": {"BAT_kWh": 10.0, "PEMFC_kW": 1.0, "PEMEL_kW": 1.0},
            "battery_constraints": {},
            "ems_parameters": {"start_stop_threshold_fraction": 0.1},
        }
        hp_func = lambda _d: (1.0, 1.0, 5.0)

        raw = compute_damage(
            hp_func, commands, config,
            D_state={"bat": 0.0, "fc": 0.95, "el": 0.0},
            eol_policy="raw",
        )
        # Raw FC damage should exceed the remaining capacity
        self.assertGreater(raw["dD_fc"][0], 0.05)

    def test_zero_damage_when_already_at_eol(self):
        """When D=1.0 (device at EOL), capped damage must be zero."""
        commands = np.array([[0.0, 500.0, 0.0]])
        config = {
            "capacity": {"BAT_kWh": 10.0, "PEMFC_kW": 1.0, "PEMEL_kW": 1.0},
            "battery_constraints": {},
            "ems_parameters": {"start_stop_threshold_fraction": 0.1},
        }
        hp_func = lambda _d: (1.0, 1.0, 5.0)

        capped = compute_damage(
            hp_func, commands, config,
            D_state={"bat": 0.0, "fc": 1.0, "el": 0.0},
        )
        # FC at D=1.0 → remaining = 0 → capped damage = 0
        self.assertAlmostEqual(capped["dD_fc"][0], 0.0)

    def test_incremental_capping_over_multiple_steps(self):
        """Damage capping applied step-by-step over a sequence."""
        from simulation.eol_events import advance_replacement_events
        # Raw increments: [0.3, 0.4, 0.5] starting from D=0.6
        # Step 0: D=0.6, raw=0.3, used=min(0.3, 0.4)=0.3, D→0.9
        # Step 1: D=0.9, raw=0.4, used=min(0.4, 0.1)=0.1, D→1.0→replace→0.0
        #   (remaining 0.3 continues) D=0.0+0.3=0.3
        # Step 2: D=0.3, raw=0.5, used=min(0.5, 0.7)=0.5, D→0.8
        result = advance_replacement_events(
            {"fc": np.array([0.3, 0.4, 0.5])},
            initial_state={"fc": 0.6},
            dt_hours=1.0,
        )
        self.assertEqual(result.replacement_counts["fc"], 1)
        self.assertAlmostEqual(result.final_state["fc"], 0.8)


# ── TDD-3: Replacement reset ──

class TestReplacementReset(unittest.TestCase):
    """After replacement, D resets to 0 and device performance recovers to BOL."""

    def test_d_resets_to_zero_after_replacement(self):
        result = advance_replacement_events(
            {"fc": np.array([0.5])},
            initial_state={"fc": 0.7},
            dt_hours=1.0,
        )
        # D=0.7 + 0.5 = 1.2 → crosses at 0.3/0.5=0.6, remaining=0.2
        self.assertEqual(result.replacement_counts["fc"], 1)
        self.assertAlmostEqual(result.final_state["fc"], 0.2)

    def test_multiple_devices_replace_independently(self):
        result = advance_replacement_events(
            {
                "bat": np.array([0.5]),
                "fc": np.array([0.8]),
                "el": np.array([0.1]),
            },
            initial_state={"bat": 0.6, "fc": 0.3, "el": 0.95},
            dt_hours=1.0,
        )
        # BAT: 0.6 + 0.5 = 1.1 → replace at 0.4/0.5=0.8, remaining=0.1
        self.assertEqual(result.replacement_counts["bat"], 1)
        self.assertAlmostEqual(result.final_state["bat"], 0.1)
        # FC: 0.3 + 0.8 = 1.1 → replace at 0.7/0.8=0.875, remaining=0.1
        self.assertEqual(result.replacement_counts["fc"], 1)
        self.assertAlmostEqual(result.final_state["fc"], 0.1)
        # EL: 0.95 + 0.1 = 1.05 → replace at 0.05/0.1=0.5, remaining=0.05
        self.assertEqual(result.replacement_counts["el"], 1)
        self.assertAlmostEqual(result.final_state["el"], 0.05)


# ── TDD-4: BAT energy retention and SOC recalculation ──

class TestBATEnergyRetention(unittest.TestCase):
    """During BAT replacement, actual stored energy is preserved;
    SOC is recalculated based on the new (BOL) capacity."""

    def test_soc_recalculated_after_bat_replacement(self):
        """BAT replacement restores capacity to BOL; stored energy constant;
        SOC = stored_energy / new_capacity."""
        from simulation.eol_events import recalculate_soc_after_replacement

        bat_kwh_bol = 100.0  # BOL capacity
        soc_before = 0.60    # SOC at D=0.4 → effective capacity = 60 kWh
        d_before = 0.4       # degradation state before replacement

        # Effective capacity before replacement: 100 * (1 - 0.2*0.4) = 92 kWh
        # (using Q(D) = 1 - 0.2*D as the capacity retention model)
        # Stored energy = soc_before * effective_capacity = 0.60 * 92 = 55.2 kWh
        # After replacement: D=0, effective capacity = 100 kWh
        # New SOC = 55.2 / 100 = 0.552
        soc_after = recalculate_soc_after_replacement(
            soc_before=soc_before,
            d_before=d_before,
            bat_kwh_bol=bat_kwh_bol,
            capacity_retention_func=lambda d: max(0.80, 1.0 - 0.20 * d),
        )
        # Stored energy = 0.60 * 100 * (1 - 0.2*0.4) = 0.60 * 92 = 55.2
        # New SOC = 55.2 / 100 = 0.552
        self.assertAlmostEqual(soc_after, 0.552, places=3)

    def test_soc_increases_when_capacity_restored(self):
        """After replacement (D→0), effective capacity increases,
        so stored energy stays the same but SOC fraction decreases."""
        from simulation.eol_events import recalculate_soc_after_replacement

        soc_after = recalculate_soc_after_replacement(
            soc_before=0.50,
            d_before=0.8,
            bat_kwh_bol=200.0,
            capacity_retention_func=lambda d: max(0.80, 1.0 - 0.20 * d),
        )
        # Effective cap before: 200 * (1 - 0.2*0.8) = 200 * 0.84 = 168
        # Stored energy = 0.50 * 168 = 84 kWh
        # New SOC = 84 / 200 = 0.42
        self.assertAlmostEqual(soc_after, 0.42, places=3)
        # SOC must decrease because capacity increased while energy stayed same
        self.assertLess(soc_after, 0.50)

    def test_soc_clamped_to_valid_range(self):
        """SOC after replacement must be within [soc_min, soc_max]."""
        from simulation.eol_events import recalculate_soc_after_replacement

        # Extreme case: very high D, very low SOC → after replacement SOC may go very low
        soc_after = recalculate_soc_after_replacement(
            soc_before=0.16,  # near soc_min
            d_before=0.9,
            bat_kwh_bol=100.0,
            capacity_retention_func=lambda d: max(0.80, 1.0 - 0.20 * d),
            soc_min=0.15,
            soc_max=0.95,
        )
        self.assertGreaterEqual(soc_after, 0.15)
        self.assertLessEqual(soc_after, 0.95)


# ── TDD-5: H₂ and state machine continuous transfer ──

class TestH2AndStateMachineTransfer(unittest.TestCase):
    """H₂ inventory and FC/EL state machine must carry over across replacements."""

    def test_h2_inventory_unchanged_during_device_replacement(self):
        """Replacing FC or EL does not change H₂ inventory."""
        from simulation.eol_events import transfer_system_state

        system_state = {
            "soc": 0.55,
            "h2_kg": 350.0,
            "fc_on": True,
            "fc_steps_remaining": 3,
            "el_on": False,
            "el_steps_remaining": 0,
        }

        # Replace FC only
        new_state = transfer_system_state(
            system_state,
            replaced_devices={"fc"},
            bat_kwh_bol=100.0,
            capacity_retention_func=lambda d: max(0.80, 1.0 - 0.20 * 0.5),
            d_before={"fc": 0.95},
            soc_before=0.55,
        )

        # H₂ must be unchanged
        self.assertAlmostEqual(new_state["h2_kg"], 350.0)
        # SOC must be recalculated for BAT if BAT was replaced (it wasn't)
        self.assertAlmostEqual(new_state["soc"], 0.55)

    def test_fc_state_machine_resets_on_fc_replacement(self):
        """After FC replacement, FC starts fresh: fc_on=False, fc_steps_remaining=0."""
        from simulation.eol_events import transfer_system_state

        system_state = {
            "soc": 0.50,
            "h2_kg": 200.0,
            "fc_on": True,
            "fc_steps_remaining": 3,
            "el_on": True,
            "el_steps_remaining": 2,
        }

        new_state = transfer_system_state(
            system_state,
            replaced_devices={"fc"},
            bat_kwh_bol=100.0,
            capacity_retention_func=lambda d: max(0.80, 1.0 - 0.20 * 0.3),
            d_before={"fc": 1.0},
            soc_before=0.50,
        )

        # FC state machine resets
        self.assertFalse(new_state["fc_on"])
        self.assertEqual(new_state["fc_steps_remaining"], 0)
        # EL state machine unchanged
        self.assertTrue(new_state["el_on"])
        self.assertEqual(new_state["el_steps_remaining"], 2)

    def test_el_state_machine_resets_on_el_replacement(self):
        """After EL replacement, EL starts fresh."""
        from simulation.eol_events import transfer_system_state

        system_state = {
            "soc": 0.60,
            "h2_kg": 400.0,
            "fc_on": False,
            "fc_steps_remaining": 0,
            "el_on": True,
            "el_steps_remaining": 4,
        }

        new_state = transfer_system_state(
            system_state,
            replaced_devices={"el"},
            bat_kwh_bol=100.0,
            capacity_retention_func=lambda d: 1.0,
            d_before={"el": 1.0},
            soc_before=0.60,
        )

        self.assertFalse(new_state["el_on"])
        self.assertEqual(new_state["el_steps_remaining"], 0)
        # FC unchanged
        self.assertFalse(new_state["fc_on"])

    def test_bat_replacement_preserves_energy_resets_soc(self):
        """BAT replacement: stored energy preserved, SOC recalculated,
        state machine (not applicable for BAT) unchanged."""
        from simulation.eol_events import transfer_system_state

        system_state = {
            "soc": 0.70,
            "h2_kg": 300.0,
            "fc_on": True,
            "fc_steps_remaining": 2,
            "el_on": False,
            "el_steps_remaining": 0,
        }

        new_state = transfer_system_state(
            system_state,
            replaced_devices={"bat"},
            bat_kwh_bol=100.0,
            capacity_retention_func=lambda d: max(0.80, 1.0 - 0.20 * d),
            d_before={"bat": 0.5},
            soc_before=0.70,
        )

        # SOC recalculated: stored energy = 0.70 * 100 * (1-0.2*0.5) = 0.70 * 90 = 63
        # New SOC = 63 / 100 = 0.63
        self.assertAlmostEqual(new_state["soc"], 0.63, places=3)
        # H₂ unchanged
        self.assertAlmostEqual(new_state["h2_kg"], 300.0)
        # FC/EL state unchanged
        self.assertTrue(new_state["fc_on"])
        self.assertEqual(new_state["fc_steps_remaining"], 2)

    def test_multiple_simultaneous_replacements(self):
        """BAT + FC replaced simultaneously: both state transfers applied."""
        from simulation.eol_events import transfer_system_state

        system_state = {
            "soc": 0.50,
            "h2_kg": 250.0,
            "fc_on": True,
            "fc_steps_remaining": 3,
            "el_on": False,
            "el_steps_remaining": 0,
        }

        new_state = transfer_system_state(
            system_state,
            replaced_devices={"bat", "fc"},
            bat_kwh_bol=200.0,
            capacity_retention_func=lambda d: max(0.80, 1.0 - 0.20 * d),
            d_before={"bat": 0.6, "fc": 1.0},
            soc_before=0.50,
        )

        # BAT: stored energy = 0.50 * 200 * (1-0.2*0.6) = 0.50 * 176 = 88
        # New SOC = 88 / 200 = 0.44
        self.assertAlmostEqual(new_state["soc"], 0.44, places=3)
        # FC state machine resets
        self.assertFalse(new_state["fc_on"])
        self.assertEqual(new_state["fc_steps_remaining"], 0)
        # H₂ unchanged
        self.assertAlmostEqual(new_state["h2_kg"], 250.0)


# ── TDD-6: Shutdown and recovery ──

class TestShutdownAndRecovery(unittest.TestCase):
    """Device must be offline during replacement downtime and recover after."""

    def test_downtime_zero_means_instant_replacement(self):
        """With downtime_steps=0, replacement is instantaneous (current behavior)."""
        from simulation.eol_events import advance_replacement_events_with_downtime

        result = advance_replacement_events_with_downtime(
            {"fc": np.array([0.5])},
            initial_state={"fc": 0.7},
            dt_hours=1.0,
            downtime_steps={"fc": 0},
        )
        self.assertEqual(result.replacement_counts["fc"], 1)
        self.assertAlmostEqual(result.final_state["fc"], 0.2)
        self.assertEqual(result.downtime_remaining["fc"], 0)

    def test_downtime_blocks_device_output(self):
        """During downtime, device cannot produce power."""
        from simulation.eol_events import advance_replacement_events_with_downtime

        # FC at D=0.7, increment=0.5 → crosses at fraction 0.3/0.5=0.6 of step 0
        # After replacement, downtime=3 steps → steps 1,2,3 skipped
        # Step 0: replacement, remaining 0.2 discarded (downtime starts immediately)
        # Steps 1,2,3: downtime → damage discarded
        # Step 4: FC available again → D = 0 + 0.1 = 0.1
        result = advance_replacement_events_with_downtime(
            {"fc": np.array([0.5, 0.1, 0.1, 0.1, 0.1])},
            initial_state={"fc": 0.7},
            dt_hours=0.25,
            downtime_steps={"fc": 3},
        )
        self.assertEqual(result.replacement_counts["fc"], 1)
        self.assertAlmostEqual(result.final_state["fc"], 0.1)
        self.assertEqual(result.downtime_remaining["fc"], 0)

    def test_downtime_damage_skipped_during_offline(self):
        """Damage increments during downtime are discarded (device not operating)."""
        from simulation.eol_events import advance_replacement_events_with_downtime

        # FC replaces at step 0 (fraction 0.6), downtime=2 steps
        # Step 0: D=0.7+0.5=1.2 → replace, remaining 0.2 discarded (downtime starts)
        # Steps 1,2: downtime → damage discarded (0.2 and 0.2 lost)
        # Step 3: FC available → D = 0 + 0.3 = 0.3
        result = advance_replacement_events_with_downtime(
            {"fc": np.array([0.5, 0.2, 0.2, 0.3])},
            initial_state={"fc": 0.7},
            dt_hours=0.25,
            downtime_steps={"fc": 2},
        )
        self.assertAlmostEqual(result.final_state["fc"], 0.3)
        self.assertEqual(result.downtime_remaining["fc"], 0)

    def test_no_replacement_no_downtime(self):
        """When no replacement occurs, downtime is zero."""
        from simulation.eol_events import advance_replacement_events_with_downtime

        result = advance_replacement_events_with_downtime(
            {"fc": np.array([0.1, 0.1])},
            initial_state={"fc": 0.3},
            dt_hours=1.0,
            downtime_steps={"fc": 3},
        )
        self.assertEqual(result.replacement_counts["fc"], 0)
        self.assertAlmostEqual(result.final_state["fc"], 0.5)
        self.assertEqual(result.downtime_remaining["fc"], 0)


if __name__ == "__main__":
    unittest.main()
