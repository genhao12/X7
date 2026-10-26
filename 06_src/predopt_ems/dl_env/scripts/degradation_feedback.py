# -*- coding: utf-8 -*-
"""
degradation_feedback.py — 闭环退化反馈映射唯一接口（Gate A 项① + 项③）
=========================================================================
① 退化反馈映射（从现有 hp_*_default 导出，与 closed_loop_lite 同源）：
   BAT：E_bat(D) = bat_kwh·Q(D)，Q(D)=max(0.80, 1−0.20D)        （20% 容量损失 @ EOL）
        Pmax_bat(D) = min(bat_rated·(1−0.10D), bat_rated)        （10% 功率降额 @ EOL）
        η_ch/η_dis：现有模型无退化 → 常数 √η_rt（v9 §5.2；若补须 2018 前冻结）
   FC：  η_fc(D) = η_bol·max(0.90, 1−0.10D)                      （10% 效率损失 @ EOL）
        Pmax_fc(D) = P_rated·max(0.95, 1−0.05D)                  （5% 功率降额 @ EOL）
   EL：  η_el(D) = η_bol·max(0.90, 1−0.10D)
        Pmax_el(D) = P_rated·max(0.95, 1−0.05D)
   支撑点重构 (P_k, ṁ_k) = f(D)：ṁ_fc=P_k/(η_fc(D)·LHV)、ṁ_el=η_el(D)·P_k/LHV
   open-loop D_feedback=0；closed-loop D_feedback=D_eval（§8/§11）

③ δ 阈值（main）：用 compute_damage 在极小命令下实测各设备
   最小可分辨非零损伤增量 → δ_BAT/δ_FC/δ_EL（2018 前冻结，§11）

运行环境：DL 环境
命令（VSCode 终端）：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/degradation_feedback.py
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SRC)

from ems.ems_stage2b import LHV_H2_KWH_PER_KG, Stage2BEMSConfig
from simulation.closed_loop_lite import CoalitionState, compute_damage
from simulation.run_stage3_full_analysis import build_c1_config, hp_bat_default

OUT_JSON = Path(__file__).resolve().parent / ".." / "results" / "degradation_feedback_result.json"

# ==================================================================
# ① 退化反馈映射（唯一接口）
# ==================================================================

def bat_health(D: float) -> Dict[str, float]:
    """BAT 健康-性能映射 D→(Q, R0, P_max_frac)。与 hp_bat_default 同源（无 bat_kwh 依赖部分）。"""
    D = float(np.clip(D, 0.0, 1.0))
    return {
        "Q": max(0.80, 1.0 - 0.20 * D),
        "R0": 1.0 + 0.50 * D,
        "P_max_frac": max(0.90, 1.0 - 0.10 * D),
    }


def fc_health(D: float) -> Dict[str, float]:
    """FC 健康-性能映射 D→(eta_frac, R_hfr, P_max_frac)。与 hp_fc_default 同源。"""
    D = float(np.clip(D, 0.0, 1.0))
    return {
        "eta_frac": max(0.90, 1.0 - 0.10 * D),
        "R_hfr": 1.0 + 0.20 * D,
        "P_max_frac": max(0.95, 1.0 - 0.05 * D),
    }


def el_health(D: float) -> Dict[str, float]:
    """EL 健康-性能映射 D→(eta_frac, R_ohm, P_max_frac)。与 hp_el_default 同源。"""
    D = float(np.clip(D, 0.0, 1.0))
    return {
        "eta_frac": max(0.90, 1.0 - 0.10 * D),
        "R_ohm": 1.0 + 0.30 * D,
        "P_max_frac": max(0.95, 1.0 - 0.05 * D),
    }


def bat_params_milp(D: float, cfg: Stage2BEMSConfig,
                    eta_rt: float = 0.90) -> Dict[str, float]:
    """MILP 用 BAT 参数（v9 §5.2）：E_bat(D)、Pmax_bat(D)、η_ch/η_dis。"""
    h = bat_health(D)
    bat_rated = cfg.bat_kwh * cfg.bat_c_rate
    return {
        "E_bat_kwh": cfg.bat_kwh * h["Q"],
        "Pmax_bat_kw": min(bat_rated * h["P_max_frac"], bat_rated),
        "eta_ch": float(np.sqrt(eta_rt)),
        "eta_dis": float(np.sqrt(eta_rt)),
        "Q": h["Q"],
    }


def fc_breakpoints(D: float, cfg: Stage2BEMSConfig, n_seg: int = 4) -> Tuple[List[float], List[float]]:
    """FC 支撑点 (P_k, ṁ_k) 重构（v9 §5.3，D 冻结值下）。ṁ_k = P_k/(η_fc(D)·LHV)。"""
    h = fc_health(D)
    eta_d = cfg.eta_fc * h["eta_frac"]
    p_rated_d = cfg.pemfc_kw * h["P_max_frac"]
    p_min_d = min(cfg.pemfc_kw * cfg.min_stable_load_fc, p_rated_d)
    pts = sorted(set([0.0] + [round(float(p), 6) for p in
                              np.linspace(p_min_d, p_rated_d, n_seg)]))
    if pts[-1] < p_rated_d:
        pts.append(p_rated_d)
    flows = [p / (eta_d * LHV_H2_KWH_PER_KG) for p in pts]
    return pts, flows


def el_breakpoints(D: float, cfg: Stage2BEMSConfig, n_seg: int = 4) -> Tuple[List[float], List[float]]:
    """EL 支撑点 (P_k, ṁ_k) 重构。ṁ_k = η_el(D)·P_k/LHV。"""
    h = el_health(D)
    eta_d = cfg.eta_el * h["eta_frac"]
    p_rated_d = cfg.pemel_kw * h["P_max_frac"]
    p_min_d = min(cfg.pemel_kw * cfg.min_stable_load_el, p_rated_d)
    pts = sorted(set([0.0] + [round(float(p), 6) for p in
                              np.linspace(p_min_d, p_rated_d, n_seg)]))
    if pts[-1] < p_rated_d:
        pts.append(p_rated_d)
    flows = [eta_d * p / LHV_H2_KWH_PER_KG for p in pts]
    return pts, flows


# ==================================================================
# ③ δ 阈值：compute_damage 极小命令实测最小可分辨非零增量
# ==================================================================

def determine_delta_thresholds(cfg: Stage2BEMSConfig) -> Dict[str, float]:
    """构造 96 步极小命令，用 compute_damage 实测各设备最小可分辨非零损伤增量。"""
    rep = {"pv_kwp": cfg.pv_kwp, "bat_kwh": cfg.bat_kwh,
           "pemfc_kw": cfg.pemfc_kw, "pemel_kw": cfg.pemel_kw, "h2_kg": cfg.h2_kg}
    c1 = build_c1_config(rep)
    hp = lambda D: hp_bat_default(D, bat_kwh=cfg.bat_kwh)
    coalition = CoalitionState.from_code("1111")
    n = 96
    cmds = np.zeros((n, 3))   # [P_BAT, P_FC, P_EL]
    # BAT：一个微循环（放电 1 kW × 1 步 + 充电 1 kW × 1 步）→ 最小 rainflow 半循环
    cmds[0, 0] = 1.0
    cmds[1, 0] = -1.0
    # FC：1 步最小稳定功率（ss_threshold×rated）
    cmds[2, 1] = cfg.pemfc_kw * 0.15
    # EL：1 步最小稳定功率
    cmds[3, 2] = cfg.pemel_kw * 0.15

    dmg = compute_damage(hp, cmds, c1, dt_min=15.0,
                         D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                         coalition=coalition, eta_degrade=0.10, eol_policy="raw")

    def min_nonzero(arr):
        a = np.asarray(arr, dtype=float)
        nz = a[np.abs(a) > 1e-15]
        return float(np.min(np.abs(nz))) if len(nz) else 0.0

    delta = {
        "bat": min_nonzero(dmg["dD_bat"]),
        "fc": min_nonzero(dmg["dD_fc"]),
        "el": min_nonzero(dmg["dD_el"]),
    }
    return delta


def main():
    print("=" * 70)
    print("Gate A：闭环退化反馈映射唯一接口 + δ 阈值确定（v9 §11）")
    print(f"时间: {datetime.now().isoformat()}")
    print("=" * 70)

    cfg = Stage2BEMSConfig(
        pv_kwp=388.2674203719944, bat_kwh=1734.3933384865522,
        pemfc_kw=95.02249870449305, pemel_kw=224.24405589699745,
        h2_kg=4450.2877332270145,
    )

    # —— ① 映射数值演示（D=0 / 0.5 / 1.0）——
    print("\n【① 退化反馈映射（D=0 / 0.5 / 1.0）】")
    for D in (0.0, 0.5, 1.0):
        b = bat_params_milp(D, cfg)
        fc_p, fc_m = fc_breakpoints(D, cfg)
        el_p, el_m = el_breakpoints(D, cfg)
        print(f"D={D:.1f}: E_bat {b['E_bat_kwh']:.0f} kWh (Q={b['Q']:.3f}) | "
              f"Pmax_bat {b['Pmax_bat_kw']:.0f} kW | η_fc 支撑点[{len(fc_p)}] ṁ_rated "
              f"{fc_m[-1]:.3f} kg/h | η_el 支撑点[{len(el_p)}] ṁ_rated {el_m[-1]:.3f} kg/h")

    # —— ③ δ 阈值实测 ——
    print("\n【③ δ 阈值（compute_damage 最小可分辨非零增量）】")
    delta = determine_delta_thresholds(cfg)
    for dev in ("bat", "fc", "el"):
        print(f"  δ_{dev.upper()} = {delta[dev]:.3e}")

    out = {"timestamp": datetime.now().isoformat(),
           "mapping_demo": {
               "bat": {str(D): bat_params_milp(D, cfg) for D in (0.0, 0.5, 1.0)},
               "fc_pmax_frac": {str(D): fc_health(D)["P_max_frac"] for D in (0.0, 0.5, 1.0)},
               "el_pmax_frac": {str(D): el_health(D)["P_max_frac"] for D in (0.0, 0.5, 1.0)},
           },
           "delta_thresholds": delta,
           "note": ("δ 为计算器最小可分辨非零增量（2018 前冻结）；D_open≤δ 时 TE_dev=undefined，"
                    "单独报告绝对差（v9 §11）；BAT η 退化现有模型无 → 常数 √η_rt；"
                    "FC/EL ramp 现有参数表无 → 冻结为不约束")}
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存：{OUT_JSON}")
    print("-" * 70)
    print("Gate A 项①映射 + 项③δ 完成 ✅（项② FC/EL 参数：T_on/T_off=5、P_min=0.15×rated 已冻结；"
          "ramp 不约束已冻结）")


if __name__ == "__main__":
    main()
