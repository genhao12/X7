# -*- coding: utf-8 -*-
"""逐时间步的设备 EOL（寿命终点）与更换事件模型。"""
from dataclasses import dataclass
from typing import Dict, List, Mapping

import numpy as np


@dataclass(frozen=True)
class ReplacementEvent:
    """单次设备更换事件。"""

    device: str
    time_hours: float
    step_index: int
    replacement_number: int


@dataclass(frozen=True)
class ReplacementAdvanceResult:
    """一段损伤序列推进后的状态与事件。"""

    final_state: Dict[str, float]
    replacement_counts: Dict[str, int]
    events: List[ReplacementEvent]
    raw_damage_totals: Dict[str, float]


def operating_hour_damage(
    power_kw: np.ndarray,
    rated_power_kw: float,
    eol_operating_hours: float,
    dt_hours: float,
    active_threshold_fraction: float = 0.0,
) -> np.ndarray:
    """将运行小时标定转换为逐步损伤增量。

    达到活动阈值的每个时间步累计 ``dt_hours / eol_operating_hours``。
    F2 的 PEMFC 标定以动态参考循环运行小时为自变量，因此不再按额定功率
    线性缩放损伤率。
    """
    if rated_power_kw <= 0 or eol_operating_hours <= 0 or dt_hours <= 0:
        raise ValueError("rated power, EOL hours and dt_hours must be positive")
    if not 0.0 <= active_threshold_fraction <= 1.0:
        raise ValueError("active_threshold_fraction must be in [0, 1]")

    power = np.asarray(power_kw, dtype=float)
    if not np.all(np.isfinite(power)):
        raise ValueError("power_kw contains non-finite values")
    active = power >= rated_power_kw * active_threshold_fraction
    if active_threshold_fraction == 0.0:
        active = power > 0.0
    return active.astype(float) * (dt_hours / eol_operating_hours)


def advance_replacement_events(
    damage_increments: Mapping[str, np.ndarray],
    initial_state: Mapping[str, float],
    dt_hours: float,
    start_time_hours: float = 0.0,
    initial_replacement_counts: Mapping[str, int] = None,
) -> ReplacementAdvanceResult:
    """推进原始损伤并在 ``D=1`` 时立即更换设备。

    损伤增量在每个时间步内按常速分布，因此事件时间可定位到步内分数。
    更换为瞬时事件：旧设备达到 ``D=1`` 后，新设备从 ``D=0`` 开始承接
    当前时间步尚未消耗的损伤。该假设用于 F4/F5 寿命与经济事件计算。
    """
    if dt_hours <= 0:
        raise ValueError("dt_hours must be positive")
    if not damage_increments:
        raise ValueError("damage_increments must not be empty")

    arrays = {name: np.asarray(values, dtype=float).reshape(-1)
              for name, values in damage_increments.items()}
    lengths = {values.size for values in arrays.values()}
    if len(lengths) != 1:
        raise ValueError("all damage series must have equal length")

    devices = list(arrays)
    state: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    totals: Dict[str, float] = {}
    events: List[ReplacementEvent] = []

    for device in devices:
        values = arrays[device]
        if not np.all(np.isfinite(values)):
            raise ValueError(f"non-finite damage for {device}")
        if np.any(values < 0):
            raise ValueError(f"negative damage for {device}")

        d0 = float(initial_state.get(device, 0.0))
        if not 0.0 <= d0 < 1.0:
            raise ValueError(f"initial state for {device} must be in [0, 1)")
        state[device] = d0
        counts[device] = int((initial_replacement_counts or {}).get(device, 0))
        totals[device] = float(np.sum(values))

    for step_index in range(next(iter(lengths))):
        step_events: List[ReplacementEvent] = []
        for device in devices:
            increment = float(arrays[device][step_index])
            consumed = 0.0

            while increment > 0.0 and state[device] + increment >= 1.0:
                to_eol = 1.0 - state[device]
                consumed += to_eol
                fraction = consumed / float(arrays[device][step_index])
                counts[device] += 1
                step_events.append(ReplacementEvent(
                    device=device,
                    time_hours=start_time_hours + (step_index + fraction) * dt_hours,
                    step_index=step_index,
                    replacement_number=counts[device],
                ))
                increment -= to_eol
                state[device] = 0.0

            state[device] += increment
            if state[device] >= 1.0 - 1e-12:
                state[device] = 0.0

        events.extend(sorted(step_events, key=lambda event: (event.time_hours, event.device)))

    return ReplacementAdvanceResult(
        final_state=state,
        replacement_counts=counts,
        events=events,
        raw_damage_totals=totals,
    )


def recalculate_soc_after_replacement(
    soc_before: float,
    d_before: float,
    bat_kwh_bol: float,
    capacity_retention_func,
    soc_min: float = 0.0,
    soc_max: float = 1.0,
) -> float:
    """Recalculate SOC after BAT replacement.

    Stored energy is preserved; SOC is recalculated based on the new (BOL)
    capacity.

    Args:
        soc_before: SOC fraction before replacement [0, 1]
        d_before: BAT degradation state before replacement [0, 1]
        bat_kwh_bol: BOL battery capacity in kWh
        capacity_retention_func: callable(D) → Q fraction [0, 1]
        soc_min: minimum valid SOC (default 0.0)
        soc_max: maximum valid SOC (default 1.0)

    Returns:
        New SOC fraction after replacement, clamped to [soc_min, soc_max].
    """
    q_before = capacity_retention_func(d_before)
    effective_cap_before = bat_kwh_bol * q_before
    stored_energy_kwh = soc_before * effective_cap_before

    # After replacement: D=0, Q=capacity_retention_func(0)
    q_after = capacity_retention_func(0.0)
    effective_cap_after = bat_kwh_bol * q_after

    if effective_cap_after <= 0:
        return soc_min

    soc_after = stored_energy_kwh / effective_cap_after
    return max(soc_min, min(soc_max, soc_after))


@dataclass(frozen=True)
class SystemStateTransferResult:
    """Result of transferring system state across device replacements."""

    soc: float
    h2_kg: float
    fc_on: bool
    fc_steps_remaining: int
    el_on: bool
    el_steps_remaining: int

    def to_dict(self) -> Dict:
        return {
            "soc": self.soc,
            "h2_kg": self.h2_kg,
            "fc_on": self.fc_on,
            "fc_steps_remaining": self.fc_steps_remaining,
            "el_on": self.el_on,
            "el_steps_remaining": self.el_steps_remaining,
        }


def transfer_system_state(
    system_state: Mapping,
    replaced_devices: set,
    bat_kwh_bol: float,
    capacity_retention_func,
    d_before: Mapping[str, float],
    soc_before: float,
    soc_min: float = 0.15,
    soc_max: float = 0.95,
) -> Dict:
    """Transfer system state across device replacements.

    Rules:
      - H₂ inventory is unchanged by any device replacement
      - FC state machine (fc_on, fc_steps_remaining) resets on FC replacement
      - EL state machine (el_on, el_steps_remaining) resets on EL replacement
      - BAT SOC is recalculated on BAT replacement (energy preserved)
      - Non-replaced devices keep their state

    Args:
        system_state: dict with keys soc, h2_kg, fc_on, fc_steps_remaining,
                      el_on, el_steps_remaining
        replaced_devices: set of device names that were replaced (e.g. {"fc", "bat"})
        bat_kwh_bol: BOL battery capacity
        capacity_retention_func: callable(D) → Q fraction
        d_before: dict of D values before replacement for replaced devices
        soc_before: SOC before replacement
        soc_min: minimum SOC
        soc_max: maximum SOC

    Returns:
        dict with updated system state
    """
    result = dict(system_state)

    # H₂ unchanged
    # (no modification needed)

    # FC state machine reset
    if "fc" in replaced_devices:
        result["fc_on"] = False
        result["fc_steps_remaining"] = 0

    # EL state machine reset
    if "el" in replaced_devices:
        result["el_on"] = False
        result["el_steps_remaining"] = 0

    # BAT SOC recalculation
    if "bat" in replaced_devices:
        result["soc"] = recalculate_soc_after_replacement(
            soc_before=soc_before,
            d_before=d_before.get("bat", 0.0),
            bat_kwh_bol=bat_kwh_bol,
            capacity_retention_func=capacity_retention_func,
            soc_min=soc_min,
            soc_max=soc_max,
        )

    return result


@dataclass(frozen=True)
class ReplacementWithDowntimeResult:
    """Result of advancing replacement events with downtime support."""

    final_state: Dict[str, float]
    replacement_counts: Dict[str, int]
    events: List[ReplacementEvent]
    raw_damage_totals: Dict[str, float]
    downtime_remaining: Dict[str, int]


def advance_replacement_events_with_downtime(
    damage_increments: Mapping[str, np.ndarray],
    initial_state: Mapping[str, float],
    dt_hours: float,
    downtime_steps: Mapping[str, int] = None,
    start_time_hours: float = 0.0,
    initial_replacement_counts: Mapping[str, int] = None,
) -> ReplacementWithDowntimeResult:
    """Advance damage with replacement events and per-device downtime.

    During downtime steps, the device is offline and damage increments are
    discarded. After downtime expires, the device resumes normal operation.

    Args:
        damage_increments: {device: array of per-step damage increments}
        initial_state: {device: D value} initial degradation states
        dt_hours: time step in hours
        downtime_steps: {device: number of steps offline after replacement}
        start_time_hours: absolute start time for event timestamps
        initial_replacement_counts: {device: count} pre-existing replacements

    Returns:
        ReplacementWithDowntimeResult with final states, counts, events,
        and remaining downtime per device.
    """
    if dt_hours <= 0:
        raise ValueError("dt_hours must be positive")
    if not damage_increments:
        raise ValueError("damage_increments must not be empty")

    downtime_steps = downtime_steps or {}

    arrays = {name: np.asarray(values, dtype=float).reshape(-1)
              for name, values in damage_increments.items()}
    lengths = {values.size for values in arrays.values()}
    if len(lengths) != 1:
        raise ValueError("all damage series must have equal length")

    devices = list(arrays)
    state: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    totals: Dict[str, float] = {}
    events: List[ReplacementEvent] = []
    downtime_remaining: Dict[str, int] = {}

    for device in devices:
        values = arrays[device]
        if not np.all(np.isfinite(values)):
            raise ValueError(f"non-finite damage for {device}")
        if np.any(values < 0):
            raise ValueError(f"negative damage for {device}")

        d0 = float(initial_state.get(device, 0.0))
        if not 0.0 <= d0 < 1.0:
            raise ValueError(f"initial state for {device} must be in [0, 1)")
        state[device] = d0
        counts[device] = int((initial_replacement_counts or {}).get(device, 0))
        totals[device] = float(np.sum(values))
        downtime_remaining[device] = 0

    for step_index in range(next(iter(lengths))):
        step_events: List[ReplacementEvent] = []
        for device in devices:
            # Skip damage during downtime
            if downtime_remaining[device] > 0:
                downtime_remaining[device] -= 1
                continue

            increment = float(arrays[device][step_index])
            consumed = 0.0

            while increment > 0.0 and state[device] + increment >= 1.0:
                to_eol = 1.0 - state[device]
                consumed += to_eol
                fraction = consumed / float(arrays[device][step_index])
                counts[device] += 1
                step_events.append(ReplacementEvent(
                    device=device,
                    time_hours=start_time_hours + (step_index + fraction) * dt_hours,
                    step_index=step_index,
                    replacement_number=counts[device],
                ))
                increment -= to_eol
                state[device] = 0.0

                # Start downtime after replacement
                dt_steps = int(downtime_steps.get(device, 0))
                if dt_steps > 0:
                    downtime_remaining[device] = dt_steps
                    break  # Remaining damage discarded during downtime

            if downtime_remaining[device] == 0:
                state[device] += increment
                if state[device] >= 1.0 - 1e-12:
                    state[device] = 0.0

        events.extend(sorted(step_events, key=lambda e: (e.time_hours, e.device)))

    return ReplacementWithDowntimeResult(
        final_state=state,
        replacement_counts=counts,
        events=events,
        raw_damage_totals=totals,
        downtime_remaining=downtime_remaining,
    )
