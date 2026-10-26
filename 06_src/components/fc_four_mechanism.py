# -*- coding: utf-8 -*-
"""
fc_four_mechanism — PEMFC四机制退化模型 (缺口1a)

将 closed_loop_lite 中的单一线性退化率升级为 Refaat et al. 2026
(Energy 352:140914, 式4-6, 表2) 的四机制线性叠加结构:

    dV_cell = D_low * t_low + D_high * t_high + D_trans * |dP_FC| + D_cycle * n_starts

标定策略 (证据分级):
    - 结构与高/低负荷比例、瞬态率、启停率: 参照 Refaat 表2
      (低负荷0-80%: 10.17 uV/h; 高负荷80-100%: 11.74 uV/h;
       瞬态: 0.0441 uV/dkW (150kW机组, 折算0.0441*150=6.615 uV/du);
       启停: 23.91 uV/cycle)
    - 时间项锚定: FC1 1000h恒流70A/0.7A/cm2 (低负荷regime) 实测
      23.5 mV/1000h (hp_fc_lite, A级数据)。锚定系数
      s = 23.5 / 10.17 = 2.311, 同乘低/高负荷两项以保留Refaat的
      低/高负荷退化比例。瞬态与启停项保留Refaat原值 (FC1恒流无可辨识信号)。

D口径与 closed_loop_lite 一致:
    D = 累计电压损失 / V_EOL,  V_EOL = VOLTAGE_LOSS_EOL * U_BOL = 0.10 * 0.667V
    D=1.0 即EOL (额定电压下降10%)。

验证: 恒流70A回放 -> 23.5 mV/1000h, t_EOL = 2838h (与hp_fc_lite一致)。

================================================================================
【2026-08-28 标定等级降级: A/B 级 -> C 级】(预注册判据触发, 缺口③/B2)
================================================================================
依据: 999h 真实动态工况回放
      脚本: 06_src/simulation/run_s4_fc_four_mechanism_check.py
      结果: 07_results/gap_closure/s4_fc_four_mechanism_check.json

  - 实测基准 : 效率 0.5410 -> 0.4510 (相对降 16.64%)
               折算 D_measured = 16.64% / 10% = 1.6636 (约 601h 到 EOL)
  - 本模型   : D(999h) = 3.7433  ->  比值 r = 2.250
               **超出预注册判据 ±20% 上界 (1.2) -> 判定"偏激进"**
  - 机制分解 : 启停 2.1480 (57%) + 瞬态 1.2575 (34%) = **91%**
               低负荷 0.2676 (7%), 高负荷 0.0702 (2%)  (线性闭合残差 8.9e-16)
  - 根因     : 该动态工况 999h 内 **5992 次启停** (约 7 次/70min 周期、6 次/h),
               Refaat 启停系数 23.91 uV/cycle 被高频启停**线性放大**
  - 敏感性   : J_rated=1.75 (峰值归一化) 时 r=1.924, 仍偏激进
               -> 结论对归一化基准**稳健**, 非归一化选择所致
  - 对照     : legacy 模型 D(999h)=1.2965, r=0.779 (仅低 22%, 接近判据带)

结论与适用域:
  * **高频启停 (>1 次/h) 或强瞬态工况 -> C 级**: 本模型只可作**上界
    (保守偏大)** 使用, **不得作绝对寿命标定**; 论文引用须同时注明 C 级
    与该适用域限制。
  * **恒流 / 低频启停工况仍以 FC1 锚定为准 (A 级)**: 恒流 70A 回放复现
    23.5 mV/1000h 与 t_EOL=2838h, 该 regime 下模型标定有效。
  * 启停/瞬态项在 FC1 恒流数据中**无可辨识信号**, 直接沿用 Refaat 原值,
    是本 C 级限制的直接来源 —— 需独立动态工况数据方可重新辨识。

可改善途径:
  1. 用多个不同启停频次的独立动态工况数据集重新辨识 D_cycle 与 D_trans,
     替换 Refaat 原值 (当前无辨识信号);
  2. 对启停项引入饱和 / 频次依赖修正 (高频启停下单位启停损伤递减),
     消除线性放大;
  3. 在 VS7 / 六路径归因中以模型分支 (fc_model_branch / --fc-four-mech)
     形式并列报告 legacy 与四机制, 给出模型形式不确定性带。
================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any

import numpy as np

# === Refaat 2026 表2原始值 (150 kW机组口径) ===
REFAAT_D_LOW_UV_H = 10.17        # uV/h/cell, 低负荷 (0-80%额定)
REFAAT_D_HIGH_UV_H = 11.74       # uV/h/cell, 高负荷 (80-100%额定)
REFAAT_D_TRANS_UV_PER_KW = 0.0441   # uV per dkW/cell
REFAAT_RATED_KW = 150.0          # Refaat机组额定功率
REFAAT_D_CYCLE_UV = 23.91        # uV per start-stop cycle/cell

# === FC1锚定 (A级, hp_fc_lite) ===
FC1_K_V_UV_PER_H = 23.5           # mV/1000h -> uV/h, 恒流70A/0.7A/cm2
FC1_LOAD_FRACTION = 0.70          # 70A / (1.0 A/cm2 * 100cm2) -> 低负荷regime
U_BOL_70A_V = 0.667               # FC1 BOL电压@70A
VOLTAGE_LOSS_EOL = 0.10           # EOL: 额定电压下降10%


@dataclass
class FourMechParams:
    """四机制退化参数 (D口径的per-unit系数)。"""
    d_low_uv_h: float          # uV/h, 低负荷运行
    d_high_uv_h: float         # uV/h, 高负荷运行
    d_trans_uv_per_pu: float   # uV, 每单位p.u.功率变化(瞬态)
    d_cycle_uv: float          # uV, 每次启停
    u_bol_v: float = U_BOL_70A_V
    v_loss_eol_frac: float = VOLTAGE_LOSS_EOL
    high_load_frac: float = 0.8   # 高/低负荷分界 (额定功率比例)

    @property
    def v_eol_uv(self) -> float:
        return self.u_bol_v * self.v_loss_eol_frac * 1.0e6


def default_params() -> FourMechParams:
    """FC1锚定参数集: 时间项x2.311锚定23.5uV/h, 瞬态/启停保留Refaat原值。"""
    scale = FC1_K_V_UV_PER_H / REFAAT_D_LOW_UV_H
    return FourMechParams(
        d_low_uv_h=REFAAT_D_LOW_UV_H * scale,
        d_high_uv_h=REFAAT_D_HIGH_UV_H * scale,
        d_trans_uv_per_pu=REFAAT_D_TRANS_UV_PER_KW * REFAAT_RATED_KW,
        d_cycle_uv=REFAAT_D_CYCLE_UV,
    )


def refaat_raw_params() -> FourMechParams:
    """Refaat原值参数集 (未锚定FC1), 用于敏感性对照。"""
    return FourMechParams(
        d_low_uv_h=REFAAT_D_LOW_UV_H,
        d_high_uv_h=REFAAT_D_HIGH_UV_H,
        d_trans_uv_per_pu=REFAAT_D_TRANS_UV_PER_KW * REFAAT_RATED_KW,
        d_cycle_uv=REFAAT_D_CYCLE_UV,
    )


def fc_damage_increments(p_fc_kw: np.ndarray,
                         rated_kw: float,
                         dt_h: float,
                         params: FourMechParams = None,
                         start_threshold_frac: float = 0.275) -> np.ndarray:
    """
    PEMFC四机制逐时间步D增量。

    Args:
        p_fc_kw: (N,) FC功率kW (>=0)
        rated_kw: FC额定功率kW
        dt_h: 步长(小时)
        params: FourMechParams, None=FC1锚定默认
        start_threshold_frac: 启停判定阈值 (与closed_loop_lite一致)

    Returns:
        (N,) dD_fc per step (无量纲, 累计至1.0即EOL)
    """
    if params is None:
        params = default_params()
    p = np.asarray(p_fc_kw, dtype=float)
    p_pu = p / max(rated_kw, 1e-9)

    t_low = (p_pu > 0.0) & (p_pu < params.high_load_frac)
    t_high = p_pu >= params.high_load_frac
    dp_pu = np.abs(np.diff(p_pu, prepend=p_pu[0] if len(p_pu) else 0.0))

    active = p_pu >= start_threshold_frac
    starts = np.zeros(len(p))
    if len(p) > 1:
        starts[1:] = (active[1:] & ~active[:-1]).astype(float)

    dv_uv = (params.d_low_uv_h * t_low * dt_h
             + params.d_high_uv_h * t_high * dt_h
             + params.d_trans_uv_per_pu * dp_pu
             + params.d_cycle_uv * starts)
    return dv_uv / params.v_eol_uv


def replay_fc1_constant() -> Dict[str, Any]:
    """恒流70A回放验证: 应复现23.5uV/h与t_EOL=2838h。"""
    params = default_params()
    n = 1000
    d = fc_damage_increments(np.full(n, FC1_LOAD_FRACTION * 1.0), 1.0, 1.0, params)
    rate_uv_h = float(np.sum(d)) * params.v_eol_uv / n
    t_eol_h = params.v_eol_uv / rate_uv_h
    return {
        'replay_uv_per_h': round(rate_uv_h, 2),
        'expected_uv_per_h': FC1_K_V_UV_PER_H,
        't_eol_h': round(t_eol_h, 0),
        'expected_t_eol_h': 2838.0,
        'pass': abs(rate_uv_h - FC1_K_V_UV_PER_H) < 0.05,
    }


def compare_to_legacy(p_fc_kw: np.ndarray, rated_kw: float, dt_h: float,
                      eta_factor: float = 1.0,
                      start_threshold_frac: float = 0.275) -> Dict[str, Any]:
    """四机制 vs closed_loop_lite legacy模型在同一功率轨迹上的对比。

    legacy: dD = FC_K_RUN_H*eta_factor*p_pu*dt_h + FC_K_START*starts
    """
    from simulation.closed_loop_lite import FC_K_RUN_H, FC_K_START
    p = np.asarray(p_fc_kw, dtype=float)
    p_pu = p / max(rated_kw, 1e-9)
    active = p_pu >= start_threshold_frac
    starts = np.zeros(len(p))
    if len(p) > 1:
        starts[1:] = (active[1:] & ~active[:-1]).astype(float)
    legacy = FC_K_RUN_H * eta_factor * p_pu * dt_h + FC_K_START * starts
    four = fc_damage_increments(p, rated_kw, dt_h)
    return {
        'n_steps': int(len(p)),
        'legacy_total_D': float(np.sum(legacy)),
        'four_mech_total_D': float(np.sum(four)),
        'ratio_four_over_legacy': round(float(np.sum(four) / max(np.sum(legacy), 1e-12)), 4),
        'n_starts': int(np.sum(starts)),
    }


if __name__ == "__main__":
    # 直接以脚本运行时 sys.path[0] 是 06_src/components, 而 compare_to_legacy()
    # 内部用绝对导入 `from simulation.closed_loop_lite import ...` -> 需补 06_src
    import sys as _sys
    from pathlib import Path as _Path
    _SRC = _Path(__file__).resolve().parents[1]
    if str(_SRC) not in _sys.path:
        _sys.path.insert(0, str(_SRC))

    print("=" * 64)
    print("fc_four_mechanism — PEMFC四机制退化模型 (缺口1a)")
    print("=" * 64)
    p = default_params()
    print(f"参数: D_low={p.d_low_uv_h:.3f} uV/h, D_high={p.d_high_uv_h:.3f} uV/h,")
    print(f"      D_trans={p.d_trans_uv_per_pu:.3f} uV/pu, D_cycle={p.d_cycle_uv:.2f} uV/cycle")
    print(f"      (FC1锚定系数 s={FC1_K_V_UV_PER_H / REFAAT_D_LOW_UV_H:.4f})")
    print(f"V_EOL = {p.v_eol_uv:.0f} uV (= 10% x {p.u_bol_v}V)")

    print("\n[1] FC1恒流回放验证...")
    rep = replay_fc1_constant()
    for k, v in rep.items():
        print(f"  {k}: {v}")

    print("\n[2] 典型调度轨迹对比 (0.5pu运行+每100步一次启停)...")
    rng = np.random.default_rng(7)
    trace = np.clip(0.5 + 0.15 * rng.standard_normal(672), 0, 1.0) * 80.0
    trace[::100] = 0.0
    cmp = compare_to_legacy(trace, 80.0, 0.25)
    for k, v in cmp.items():
        print(f"  {k}: {v}")

    print("\n[3] Refaat原值 vs FC1锚定 (恒流0.7pu年损伤):")
    for name, pp in (("refaat_raw", refaat_raw_params()), ("fc1_anchored", p)):
        d = fc_damage_increments(np.full(96, 0.7), 1.0, 0.25, pp)
        print(f"  {name}: {float(np.sum(d))*365:.4f} D/年 (恒流0.7pu, 8760h)")
    print("\n状态: fc_four_mechanism 就绪")
