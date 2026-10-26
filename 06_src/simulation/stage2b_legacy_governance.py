"""Reproduce pre-Stage2b EMS defects without changing the legacy kernel."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List

import numpy as np

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch


def positive_run_lengths(values: Iterable[float], tolerance: float = 1e-9) -> List[int]:
    """Return lengths of contiguous strictly-positive runs."""
    runs: List[int] = []
    current = 0
    for active in np.asarray(values, dtype=float) > tolerance:
        if active:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def reproduce_legacy_defects(path: Path) -> dict:
    """Run deterministic traces that expose all three superseded behaviors."""
    cfg = EMSConfig(
        pv_kwp=100.0,
        bat_kwh=1.0,
        pemfc_kw=20.0,
        pemel_kw=20.0,
        h2_kg=10.0,
    )
    idle_commands, idle = rule_based_dispatch(
        np.array([20.0]),
        np.array([0.0]),
        cfg,
        soc_initial=cfg.soc_min,
        h2_initial_kg=cfg.h2_kg * cfg.h2_min_fraction,
    )
    commands, active = rule_based_dispatch(
        np.array([100.0, 0.0, 100.0, 0.0, 100.0]),
        np.array([0.0, 100.0, 0.0, 100.0, 0.0]),
        cfg,
        soc_initial=cfg.soc_min,
        h2_initial_kg=cfg.h2_kg,
    )
    run_lengths = positive_run_lengths(commands[:, 1]) + positive_run_lengths(
        commands[:, 2]
    )
    fixed_aux = 0.015 * cfg.pv_kwp + 0.010 * (cfg.bat_kwh * 0.5) + 0.5
    evidence = {
        "schema_version": "stage2b_legacy_invalidation.1",
        "legacy_kernel": "06_src/ems/ems_bf_lite.py",
        "legacy_pv_aux_active_at_zero_pv": bool(
            idle["p_aux"][0] >= 0.015 * cfg.pv_kwp
        ),
        "legacy_bat_aux_active_when_idle": bool(
            abs(idle_commands[0, 0]) <= 1e-12
            and idle["p_aux"][0] >= 0.010 * (cfg.bat_kwh * 0.5)
        ),
        "legacy_fc_el_flags_do_not_follow_same_step_power": bool(
            np.any(commands[:, 1:] > 1e-9)
            and np.allclose(active["p_aux"], fixed_aux, atol=1e-12)
        ),
        "legacy_has_run_shorter_than_5_steps": bool(
            run_lengths and min(run_lengths) < 5
        ),
        "observed_fc_run_lengths_steps": positive_run_lengths(commands[:, 1]),
        "observed_el_run_lengths_steps": positive_run_lengths(commands[:, 2]),
        "paper_citable": False,
        "superseded_by": "rectification_v2_stage2b",
        "reason": "pre_stage2b_auxiliary_flags_and_minimum_run_semantics",
    }
    _atomic_json(Path(path), evidence)
    return evidence


__all__ = ["positive_run_lengths", "reproduce_legacy_defects"]
