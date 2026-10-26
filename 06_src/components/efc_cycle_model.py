# -*- coding: utf-8 -*-
"""
EFC / energy-throughput battery cycle-damage model.

Replaces the "BATKCYC_H running-hour proxy"
    dD_cyc = BAT_K_CYC_H * |DeltaSoC| * p_ratio * dt_h        (proxy)
with an equivalent-full-cycle (EFC) model based on the SoC trace:

  1. Rainflow count the SoC trace (ASTM E1049-85) -> half-cycle histogram
     {range_i, count_i=0.5, [i_start, i_end]}.
  2. Equivalent full cycles with Woehler depth weighting:
        EFC = sum_i count_i * (range_i / DOD_ref)**k
     With k=1 this reduces exactly to the energy-throughput convention
     EFC = sum(|dSoC|)/2 (BLAST-Lite), since the half-cycle ranges sum to
     the total variation of the trace. k>1 emphasises deep cycles.
  3. Cycle capacity loss:  Q_loss_cyc = a_qloss * EFC
     Calibrated to 280Ah LFP aging (LIC_aging_analysed.csv):
     SoH 1.0 -> 0.929 over 700 full cycles  =>  a_qloss = 0.071/700.
  4. Normalised damage:     D_cyc = Q_loss_cyc / (1 - SoH_EOL),  SoH_EOL = 0.8
     => a_d = 0.071/700/0.2 = 5.0714e-4 D per EFC.

Calibration cross-check:
  - 700 full cycles  ->  D = 0.355, matches hp_bat_lite D-range 0..0.354.
  - Legacy proxy gives ~2e-3 D per full cycle -> 700 cycles = 1.4 D (~4x
    more aggressive than the 280Ah data, hence the upgrade).
"""
from __future__ import annotations

from collections import deque
from typing import Iterator, List, Tuple

import numpy as np

# === Calibration constants ===
SOH_EOL = 0.8  # must match hp_bat_lite / mvp_c1_config battery_constraints.soh_eol

EFC_A_QLOSS = 0.071 / 700.0                # Q-loss fraction per equivalent full cycle
EFC_A_D = EFC_A_QLOSS / (1.0 - SOH_EOL)    # D increment per EFC = 5.0714e-4
EFC_DOD_REF = 1.0                          # reference DOD (full cycle) for EFC definition
EFC_WOHLER_K = 1.0                         # Woehler exponent (linear = energy-throughput)


def _reversal_indices_values(
    soc: np.ndarray,
) -> Tuple[List[int], List[float]]:
    """Extract reversal points of the SoC trace.

    Returns (orig_indices, values) where orig_indices are positions in the
    input array (duplicate-consecutive values removed) and values are the
    corresponding SoC levels at those reversals.
    """
    soc = np.asarray(soc, dtype=float)
    n = len(soc)
    if n == 0:
        return [], []
    keep = np.concatenate(([True], soc[1:] != soc[:-1]))
    vals = soc[keep]
    orig = np.nonzero(keep)[0]
    m = len(vals)
    if m <= 2:
        return list(orig), list(vals)
    d = np.diff(vals)
    rev = np.where((d[:-1] > 0) != (d[1:] > 0))[0] + 1
    idx = np.concatenate(([0], rev, [m - 1]))
    return list(orig[idx]), list(vals[idx])


def _rainflow_on_values(vals: List[float]) -> List[Tuple[float, float, int, int]]:
    """ASTM E1049-85 rainflow on a reversal-value list.

    Returns (range, mean, count, i_start, i_end) with i_start/i_end indexing
    the reversal list. count = 0.5 per half-cycle.
    """
    points = deque()

    def fmt(p1, p2, count):
        (i1, x1), (i2, x2) = p1, p2
        return abs(x1 - x2), 0.5 * (x1 + x2), count, i1, i2

    cycles: List[Tuple[float, float, int, int]] = []
    for i, v in enumerate(vals):
        points.append((i, v))
        while len(points) >= 3:
            (i1, x1), (i2, x2), (i3, x3) = points[0], points[1], points[2]
            if abs(x1 - x2) <= abs(x2 - x3):
                # a half-cycle closes: count (x1, x2) once, drop x1 (BLAST convention)
                cycles.append(fmt(points[0], points[1], 0.5))
                del points[0]
            else:
                break
    while len(points) >= 2:
        cycles.append(fmt(points[0], points[1], 0.5))
        del points[0]
    return cycles


def rainflow_cycles(
    soc: np.ndarray,
) -> List[Tuple[float, float, float, int, int]]:
    """Rainflow count an SoC trace.

    Returns (range, mean, count, i_start, i_end); i_start/i_end are indices
    into the original input array. count = 0.5 for every half-cycle; a full
    cycle is a pair of 0.5 half-cycles.
    """
    orig_idx, vals = _reversal_indices_values(soc)
    out = []
    for rng, mean, count, ri0, ri1 in _rainflow_on_values(vals):
        out.append((rng, mean, count, orig_idx[ri0], orig_idx[ri1]))
    return out


def equivalent_full_cycles(
    cycles: List[Tuple[float, float, float, int, int]],
    k: float = EFC_WOHLER_K,
    dod_ref: float = EFC_DOD_REF,
) -> float:
    """EFC total with Woehler depth weighting."""
    efc = 0.0
    for rng, _mean, count, _i0, _i1 in cycles:
        depth = min(rng / dod_ref, 1.0) if dod_ref > 0 else 0.0
        efc += count * (depth ** k)
    return float(efc)


def total_variation_efc(soc: np.ndarray) -> float:
    """BLAST-Lite energy-throughput EFC: sum(|dSoC|)/2."""
    soc = np.asarray(soc, dtype=float)
    if len(soc) < 2:
        return 0.0
    return float(np.sum(np.abs(np.diff(soc))) / 2.0)


def cycle_damage_increments(
    soc_trace: np.ndarray,
    a_d: float = EFC_A_D,
    k: float = EFC_WOHLER_K,
    dod_ref: float = EFC_DOD_REF,
) -> np.ndarray:
    """Per-step cyclic damage increments over the SoC trace.

    Every counted half-cycle contributes a_d * count * (range/DOD_ref)**k
    units of D, distributed uniformly across the input-step span
    [i_start, i_end]. The returned array has one entry per input step and
    its sum equals a_d * EFC (the k-weighted EFC total).
    """
    soc = np.asarray(soc_trace, dtype=float)
    out = np.zeros(len(soc))
    if len(soc) < 2:
        return out
    for rng, _mean, count, i0, i1 in rainflow_cycles(soc):
        depth = min(rng / dod_ref, 1.0) if dod_ref > 0 else 0.0
        total = a_d * count * (depth ** k)
        span = i1 - i0  # number of time steps covered by this cycle
        if span <= 0:
            out[i0] += total
        else:
            out[i0:i1] += total / span
    return out


def energy_throughput_mwh(p_bat_kw: np.ndarray, dt_h: float) -> float:
    """Energy throughput (MWh) of the battery power signal."""
    return float(np.sum(np.abs(np.asarray(p_bat_kw, dtype=float))) * dt_h / 1000.0)


def throughput_equivalent_full_cycles(energy_mwh: float, capacity_kwh: float) -> float:
    """EFC estimate from energy throughput (MWh) normalised by nominal capacity (kWh)."""
    return float(energy_mwh * 1000.0 / capacity_kwh)


if __name__ == "__main__":
    print("EFC_A_QLOSS =", EFC_A_QLOSS, "Q-loss/EFC")
    print("EFC_A_D     =", EFC_A_D, "D/EFC")

    # full cycle 0 -> 1 -> 0  (with intermediate points)
    soc_full = np.array([0.0, 0.5, 1.0, 0.5, 0.0])
    cyc_full = rainflow_cycles(soc_full)
    efc_full = equivalent_full_cycles(cyc_full)
    tv_full = total_variation_efc(soc_full)
    print("\nfull cycle (0->1->0): cycles =", cyc_full)
    print("  rainflow EFC (k=1) =", efc_full, "| BLAST total-variation EFC =", tv_full)

    # half cycle 0 -> 1
    soc_half = np.array([0.0, 0.5, 1.0])
    print("half cycle (0->1): EFC =", equivalent_full_cycles(rainflow_cycles(soc_half)),
          "| TV EFC =", total_variation_efc(soc_half))

    # depth weighting k=2
    soc_half_05 = np.array([0.0, 0.25, 0.5])  # DOD 0.5 half-cycle
    print("half cycle DOD=0.5: EFC k=1 =", equivalent_full_cycles(rainflow_cycles(soc_half_05), k=1.0),
          "| EFC k=2 =", equivalent_full_cycles(rainflow_cycles(soc_half_05), k=2.0))

    # daily wiggle: rainflow(k=1) must equal total-variation EFC
    soc_daily = np.array([0.5, 0.4, 0.3, 0.5, 0.7, 0.9, 0.6, 0.4, 0.5])
    print("daily wiggle: rainflow EFC k=1 =", equivalent_full_cycles(rainflow_cycles(soc_daily), k=1.0),
          "| TV EFC =", total_variation_efc(soc_daily))

    # 280Ah calibration: 700 full cycles -> D = 0.355
    print("\n280Ah 700 full cycles -> D =", EFC_A_D * 700.0, "(expect ~0.355)")

    # damage increments conserve total
    inc = cycle_damage_increments(soc_full)
    print("full-cycle damage sum =", float(np.sum(inc)), "(expect", EFC_A_D, ")")
    inc_daily = cycle_damage_increments(soc_daily)
    print("daily damage sum =", float(np.sum(inc_daily)),
          "== a_d*EFC(k=1) =", EFC_A_D * equivalent_full_cycles(rainflow_cycles(soc_daily), k=1.0))
