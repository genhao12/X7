# -*- coding: utf-8 -*-
"""六路径激活的统计门控。"""
from dataclasses import dataclass
from typing import Dict

import numpy as np


@dataclass(frozen=True)
class PathActivationResult:
    mean_effect: float
    ci_low: float
    ci_high: float
    direction_consistency: float
    quarterly_effects: tuple
    gates: Dict[str, bool]
    activated: bool


def evaluate_path_activation(
    effects,
    absolute_threshold: float = 1e-4,
    direction_threshold: float = 0.90,
    nc1_residual: float = 0.0,
    nc3_residual: float = 0.0,
    negative_control_threshold: float = 1e-4,
    bootstrap_samples: int = 2000,
    seed: int = 42,
) -> PathActivationResult:
    """按有限性、效应阈值、Bootstrap、方向、季度复现与负对照判定。"""
    values = np.asarray(effects, dtype=float).reshape(-1)
    if values.size < 4:
        raise ValueError("at least four windows are required")
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")
    if absolute_threshold < 0 or not 0.5 <= direction_threshold <= 1.0:
        raise ValueError("invalid activation thresholds")

    finite = bool(np.all(np.isfinite(values)))
    if not finite:
        mean_effect = float('nan')
        ci_low = ci_high = float('nan')
        consistency = 0.0
        quarter_means = (float('nan'),) * 4
    else:
        mean_effect = float(np.mean(values))
        rng = np.random.default_rng(seed)
        indices = rng.integers(0, values.size, size=(bootstrap_samples, values.size))
        bootstrap_means = np.mean(values[indices], axis=1)
        ci_low, ci_high = [float(x) for x in np.percentile(bootstrap_means, [2.5, 97.5])]

        if mean_effect > 0:
            consistency = float(np.mean(values > 0))
        elif mean_effect < 0:
            consistency = float(np.mean(values < 0))
        else:
            consistency = 0.0
        quarter_means = tuple(float(np.mean(part)) for part in np.array_split(values, 4))

    effect_gate = finite and abs(mean_effect) >= absolute_threshold
    ci_gate = finite and (ci_low > 0.0 or ci_high < 0.0)
    direction_gate = finite and consistency >= direction_threshold
    if mean_effect > 0:
        quarter_gate = all(value >= absolute_threshold for value in quarter_means)
    elif mean_effect < 0:
        quarter_gate = all(value <= -absolute_threshold for value in quarter_means)
    else:
        quarter_gate = False

    gates = {
        "finite": finite,
        "absolute_effect": effect_gate,
        "bootstrap_ci_excludes_zero": ci_gate,
        "direction_consistency": direction_gate,
        "quarterly_reproduction": quarter_gate,
        "nc1": abs(float(nc1_residual)) <= negative_control_threshold,
        "nc3": abs(float(nc3_residual)) <= negative_control_threshold,
    }
    return PathActivationResult(
        mean_effect=mean_effect,
        ci_low=ci_low,
        ci_high=ci_high,
        direction_consistency=consistency,
        quarterly_effects=quarter_means,
        gates=gates,
        activated=all(gates.values()),
    )
