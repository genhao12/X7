# -*- coding: utf-8 -*-
"""
ghi_15min_reconstruction.py — 审查3修复：NASA 小时 GHI → 15min（插值 + 随机爬坡 + 严格能量守恒）

方案（插值-爬坡-守恒三段式）：
  1. 小时 GHI 三次样条插值到 15min（平滑包络，保留昼夜/云团小时尺度形态）
  2. 白天段叠加随机爬坡扰动：GHI_15 += N(0, σ·GHI_env)，σ 由 UCSD 实测 15min 爬坡校准
     （相对功率 std=0.0571，换算到 GHI 域 σ ≈ sigma_frac × 日峰值）
  3. 严格小时能量守恒：每小时内 4 点比例校正，使 4 点均值 == 原小时值（数学恒等）
  4. 输出 + 报告：能量守恒误差（应≈0，机器精度级）、爬坡统计 vs UCSD 实测

校准基准（审查3 补强，run_pv_reconstruction_validation.py 动态验证）：
  UCSD 原生 PV（08_TradeStreetPV_paper_QC_layer.csv，15min，observed_raw_valid）白天段
  （>5%×峰值 244.4 kW）|ΔP|/峰值：std=0.0571、p95=0.1607；>10 kW 步变化占比 19.5%。
  注：早期文档/注释中的 0.0775 无独立计算来源，已由本脚本动态统计的 0.0571 取代；
  采用 sigma_frac=0.15 生成的重构爬坡 std≈0.065–0.076，≥ UCSD 实测 0.0571——重构保留
  （并略强于）UCSD 分钟级波动，不低估电池循环损伤（保守方向）。
"""
import numpy as np
from scipy.interpolate import CubicSpline


def reconstruct_ghi_15min(ghi_hourly, lat_deg=None, seed=20260806,
                          sigma_frac=0.30, method="interp+ramp+conservation"):
    """小时 GHI → 15min。返回 (ghi_15, report)。"""
    rng = np.random.default_rng(seed)
    n_h = len(ghi_hourly)
    t_h = np.arange(n_h)

    # 1) 三次样条插值到 15min（边界填充防过冲）
    ghi_pad = np.concatenate([[ghi_hourly[0]], ghi_hourly, [ghi_hourly[-1]]])
    t_pad = np.concatenate([[t_h[0] - 1], t_h, [t_h[-1] + 1]])
    spl = CubicSpline(t_pad, ghi_pad, bc_type="natural")
    t_15 = np.linspace(t_h[0], t_h[-1], 4 * n_h)
    ghi_env = np.clip(spl(t_15), 0.0, None)

    # 2) 随机爬坡（白天段）：σ 相对日峰值（校准自 UCSD 15min 爬坡 std=0.0571 相对最大功率，
    #    见 run_pv_reconstruction_validation.py 动态验证；sigma_frac 使重构 std≈0.065-0.076 ≥ 实测 0.0571）
    days = (t_15 // 24).astype(int)
    daily_peak = np.zeros(len(t_15))
    for d in np.unique(days):
        m = days == d
        v = ghi_env[m]
        daily_peak[m] = np.percentile(v[v > 10], 95) if (v > 10).any() else 0.0
    day_mask = ghi_env > 10.0
    sigma = sigma_frac * daily_peak
    noise = rng.normal(0.0, sigma) * day_mask
    ghi_ramped = np.clip(ghi_env + noise, 0.0, None)

    # 3) 严格小时能量守恒：每小时内 4 点比例校正（数学恒等：均值回原小时值）
    ghi_ramped_h = ghi_ramped.reshape(-1, 4)
    ghi_hourly_orig = ghi_hourly
    hour_mean = ghi_ramped_h.mean(axis=1)
    day_rows = ghi_hourly_orig > 1.0
    scale = np.ones_like(ghi_hourly_orig)
    scale[day_rows] = ghi_hourly_orig[day_rows] / np.maximum(hour_mean[day_rows], 1e-6)
    ghi_15 = ghi_ramped_h * scale[:, None]
    ghi_15 = np.clip(ghi_15, 0.0, None).ravel()

    # 能量守恒误差（校正后 = 机器精度）
    ghi_15_h = ghi_15.reshape(-1, 4)
    ghi_hourly_recon = ghi_15_h.mean(axis=1)
    day_valid = ghi_hourly > 1.0
    energy_err = np.abs(ghi_hourly_recon[day_valid] - ghi_hourly[day_valid]) / np.maximum(ghi_hourly[day_valid], 1e-6)

    # 爬坡统计（相对峰值）
    peak = np.max(ghi_15) if np.max(ghi_15) > 0 else 1.0
    d = np.abs(np.diff(ghi_15)) / peak
    day_d = d[(ghi_15[:-1] > 0.05 * peak)]
    report = {
        "method": method,
        "sigma_frac": sigma_frac,
        "hourly_energy_conservation": {
            "max_rel_err_pct": round(float(np.max(energy_err) * 100.0), 6),
            "mean_rel_err_pct": round(float(np.mean(energy_err) * 100.0), 8),
            "p95_rel_err_pct": round(float(np.percentile(energy_err, 95) * 100.0), 6),
        },
        "ramp_stats_vs_ucsd": {
            "recon_std": round(float(day_d.std()), 4),
            "recon_p95": round(float(np.percentile(day_d, 95)), 4),
            "ucsd_std": 0.0571, "ucsd_p95": 0.1607,
        },
        "n_hours": n_h, "n_15min": len(ghi_15),
    }
    return ghi_15, report
