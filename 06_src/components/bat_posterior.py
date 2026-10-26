# -*- coding: utf-8 -*-
"""
bat_posterior — 电池循环损伤系数贝叶斯后验 + 日历-循环耦合项 (缺口1b)

目的:
    1. 用280Ah LFP老化数据 (LIC_aging_analysed.csv, 15时点, 0-700循环)
       对EFC循环损伤系数 a_d 做贝叶斯后验辨识 (替代点估计5.0714e-4),
       把VS7中占认知不确定性98%+的BAT参数从"区间采样"升级为"后验采样"。
    2. 引入日历-循环交互项 k_int:
           D_total = D_cal + D_cyc + k_int * sqrt(D_cal * D_cyc)
       实验室循环工况日历分量可忽略, 无法从280Ah数据辨识k_int,
       故取文献先验区间 [0, 0.5] (不确定性显式传播, 不假装标定)。

口径:
    Q_loss = a_qloss * EFC,  a_d = a_qloss / (1 - SoH_EOL), SoH_EOL=0.8
    日历先验: Naumann 2018/2022, LFP 25C, 2-5%/年 -> D口径 0.10-0.25 D/年
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGING_CSV = (PROJECT_ROOT / "数据文献" / "280 Ah方形LFP表征与700循环老化数据（）"
             / "_extracted" / "LIC_aging" / "LIC_aging_analysed.csv")

SOH_EOL = 0.8
A_D_POINT = 5.0714e-4        # 现行点估计 (efc_cycle_model)
K_CAL_PRIOR_RANGE = (0.02, 0.05)   # 日历Q损失/年 (Naumann区间)
K_INT_PRIOR_RANGE = (0.0, 0.5)     # 交互项先验区间


def _load_aging() -> Tuple[np.ndarray, np.ndarray]:
    """返回 (EFC, Q_loss) 数组; 循环老化协议中 EFC≈循环数。"""
    efc, qloss = [], []
    with open(AGING_CSV, "r") as f:
        for r in csv.DictReader(f):
            cyc = float(r["Total_cycles"])
            soh = float(r["SoH"])
            efc.append(cyc)
            qloss.append(1.0 - soh)
    return np.array(efc), np.array(qloss)


@dataclass
class BatPosterior:
    a_qloss_mean: float
    a_qloss_sd: float
    a_qloss_ci95: Tuple[float, float]
    r2: float
    n_points: int

    @property
    def a_d_mean(self) -> float:
        return self.a_qloss_mean / (1.0 - SOH_EOL)

    @property
    def a_d_sd(self) -> float:
        return self.a_qloss_sd / (1.0 - SOH_EOL)

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """从后验 (正态近似) 采样 a_d。"""
        return rng.normal(self.a_d_mean, self.a_d_sd, n)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "a_qloss_mean": self.a_qloss_mean,
            "a_qloss_sd": self.a_qloss_sd,
            "a_qloss_ci95": list(self.a_qloss_ci95),
            "a_d_mean": self.a_d_mean,
            "a_d_sd": self.a_d_sd,
            "a_d_ci95": [self.a_qloss_ci95[0] / (1 - SOH_EOL),
                         self.a_qloss_ci95[1] / (1 - SOH_EOL)],
            "a_d_point_estimate_legacy": A_D_POINT,
            "r2": self.r2,
            "n_points": self.n_points,
        }


def fit_posterior(bootstrap_n: int = 2000, seed: int = 42) -> BatPosterior:
    """贝叶斯自助 (bootstrap后验) 辨识 a_qloss。"""
    efc, qloss = _load_aging()
    x, y = efc, qloss

    def slope(xa, ya):
        return float(np.dot(xa - xa.mean(), ya - ya.mean()) / np.dot(xa - xa.mean(), xa - xa.mean()))

    hat = slope(x, y)
    y_pred = hat * x
    resid = y - y_pred
    r2 = 1.0 - np.sum(resid ** 2) / np.sum((y - y.mean()) ** 2)

    rng = np.random.default_rng(seed)
    boots = np.array([
        slope(x, hat * x + rng.choice(resid, size=len(x), replace=True))
        for _ in range(bootstrap_n)
    ])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return BatPosterior(
        a_qloss_mean=float(boots.mean()),
        a_qloss_sd=float(boots.std()),
        a_qloss_ci95=(float(lo), float(hi)),
        r2=round(float(r2), 6),
        n_points=len(x),
    )


def combined_damage(d_cal_D: float, d_cyc_D: float, k_int: float) -> float:
    """日历+循环+交互项 -> 总D增量 (均为D口径)。"""
    return d_cal_D + d_cyc_D + k_int * np.sqrt(max(d_cal_D, 0.0) * max(d_cyc_D, 0.0))


def propagate_uncertainty(efc_per_year: float,
                          posterior: BatPosterior = None,
                          n_samples: int = 2000,
                          seed: int = 7) -> Dict[str, Any]:
    """
    先验 vs 后验的电池年损伤不确定性传播对比。

    先验: a_d ~ U[0.5, 1.5]*点估计 (文献区间口径, VS7现行做法)
    后验: a_d ~ N(后验均值, 后验sd)
    日历 k_cal ~ U[2,5]%/年 (换算D口径 /0.2), 交互 k_int ~ U[0, 0.5]

    Args:
        efc_per_year: 代表性年EFC (从闭环仿真soc轨迹测得)
    """
    if posterior is None:
        posterior = fit_posterior()
    rng = np.random.default_rng(seed)

    out = {}
    for mode in ("prior", "posterior"):
        if mode == "prior":
            a_d = rng.uniform(0.5, 1.5, n_samples) * A_D_POINT
        else:
            a_d = posterior.sample(n_samples, rng)
        k_cal_frac = rng.uniform(*K_CAL_PRIOR_RANGE, n_samples)
        k_int = rng.uniform(*K_INT_PRIOR_RANGE, n_samples)
        d_cyc = a_d * efc_per_year
        d_cal = k_cal_frac / (1.0 - SOH_EOL)
        d_tot = np.array([combined_damage(c, y, k)
                          for c, y, k in zip(d_cal, d_cyc, k_int)])
        out[mode] = {
            "a_d_mean": float(np.mean(a_d)),
            "a_d_ci95": [float(np.percentile(a_d, 2.5)), float(np.percentile(a_d, 97.5))],
            "annual_D_mean": float(np.mean(d_tot)),
            "annual_D_ci95": [float(np.percentile(d_tot, 2.5)), float(np.percentile(d_tot, 97.5))],
            "annual_D_cv_pct": float(np.std(d_tot) / np.mean(d_tot) * 100.0),
        }
    out["efc_per_year"] = efc_per_year
    out["posterior_fit"] = posterior.to_dict()
    return out


if __name__ == "__main__":
    print("=" * 64)
    print("bat_posterior — 电池损伤系数后验 + 日历-循环耦合项 (缺口1b)")
    print("=" * 64)
    post = fit_posterior()
    print(f"[1] 280Ah老化数据 ({post.n_points}点): R2={post.r2}")
    print(f"    a_qloss 后验: {post.a_qloss_mean:.6e} +- {post.a_qloss_sd:.2e}"
          f"  95%CI [{post.a_qloss_ci95[0]:.3e}, {post.a_qloss_ci95[1]:.3e}]")
    print(f"    a_d     后验: {post.a_d_mean:.6e} +- {post.a_d_sd:.2e}"
          f"  (legacy点估计 {A_D_POINT})")
    print(f"    后验95%CI宽度/均值 = "
          f"{(post.a_qloss_ci95[1]-post.a_qloss_ci95[0])/post.a_qloss_mean*100:.2f}%"
          f"  vs 先验区间宽度/均值 = 66.7%")

    print("\n[2] 不确定性传播对比 (代表年EFC=300):")
    prop = propagate_uncertainty(300.0, post)
    for mode in ("prior", "posterior"):
        s = prop[mode]
        print(f"    {mode:>9s}: 年D均值 {s['annual_D_mean']:.4f}, "
              f"95%CI [{s['annual_D_ci95'][0]:.4f}, {s['annual_D_ci95'][1]:.4f}], "
              f"CV {s['annual_D_cv_pct']:.1f}%")
    print("\n状态: bat_posterior 就绪")
