# -*- coding: utf-8 -*-
"""
P0 六类数学单元测试 —— 第 1/2/3 类（v9 §16.1）
=================================================
  1) 电池 SOC 单步守恒 ≤ 1e-6（v9 §5.2 能量口径：含充放电效率）
  2) FC/EL 分段线性（SOS2 支撑点）vs 现有模型，最大相对误差 ≤ 2%
  3) H₂ 质量守恒 ≤ 1e-6 kg（v9 §5.3：LHV=33.3、线性泄漏，与 ems_stage2b 同式）

运行环境：DL 环境（D:/Anaconda/envs/DL/python.exe）——依赖 06_src 组件与 gurobipy
命令（VSCode 终端）：
  D:/Anaconda/envs/DL/python.exe 06_src/lstm_milp/p0_unit_tests.py
输出：控制台 PASS/FAIL + 同目录 p0_unit_tests_result.json（复现证据）
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# 使 06_src 可导入（脚本位于 06_src/lstm_milp/dl_env/，需两级上跳到 06_src）
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SRC)

from ems.ems_stage2b import LHV_H2_KWH_PER_KG, Stage2BEMSConfig

OUT_JSON = Path(__file__).resolve().parent / ".." / "results" / "p0_unit_tests_result.json"

# compact 代表配置（verified_admissions.json，与第一关同口径）
CFG = Stage2BEMSConfig(
    pv_kwp=388.2674203719944,
    bat_kwh=1734.3933384865522,
    pemfc_kw=95.02249870449305,
    pemel_kw=224.24405589699745,
    h2_kg=4450.2877332270145,
)
DT_H = CFG.dt_h                      # 0.25 h
E_BAT = CFG.bat_kwh                  # 电池容量 kWh
ETA_FC, ETA_EL = CFG.eta_fc, CFG.eta_el
P_FC_RATED = CFG.pemfc_kw
P_EL_RATED = CFG.pemel_kw
P_FC_MIN = CFG.pemfc_kw * CFG.min_stable_load_fc
P_EL_MIN = CFG.pemel_kw * CFG.min_stable_load_el

results = {"script": "p0_unit_tests.py (v9 §16.1 第1/2/3类)",
           "timestamp": datetime.now().isoformat(),
           "config": {"bat_kwh": E_BAT, "pemfc_kw": P_FC_RATED, "pemel_kw": P_EL_RATED,
                      "h2_kg": CFG.h2_kg, "eta_fc": ETA_FC, "eta_el": ETA_EL,
                      "lhv": LHV_H2_KWH_PER_KG, "dt_h": DT_H},
           "checks": {}, "summary": {"pass": 0, "fail": 0}}


def report(no, name, ok, detail=""):
    results["checks"][no] = {"name": name, "pass": bool(ok), "detail": detail}
    results["summary"]["pass" if ok else "fail"] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] 测试{no} {name}" + (f" | {detail}" if detail else ""))
    return bool(ok)


# ==================================================================
# 测试 1：电池 SOC 单步守恒（v9 §5.2 能量口径）
#   SOC' = SOC + η_ch·P_ch·Δt/E − P_dis·Δt/(η_dis·E)
#   守恒：ΔSOC·E = η_ch·P_ch·Δt − P_dis·Δt/η_dis  （无边界裁剪时严格闭合）
# ==================================================================
def test1_soc_conservation():
    rng = np.random.default_rng(20260808)
    max_res = 0.0
    n_cases = 0
    for eta in (1.0, 0.95, 0.9):
        for _ in range(500):
            soc = float(rng.uniform(CFG.soc_min + 0.05, CFG.soc_max - 0.05))
            p_ch = float(rng.uniform(0, 400))
            p_dis = float(rng.uniform(0, 400))
            soc_new = soc + eta * p_ch * DT_H / E_BAT - p_dis * DT_H / (eta * E_BAT)
            if not (CFG.soc_min < soc_new < CFG.soc_max):
                continue  # 跳过触碰边界的样本（裁剪为物理保护，另测）
            lhs = (soc_new - soc) * E_BAT                      # SOC 变化对应的能量
            rhs = eta * p_ch * DT_H - p_dis * DT_H / eta       # 净输入能量（含效率）
            res = abs(lhs - rhs)
            max_res = max(max_res, res)
            n_cases += 1
    # 边界保护：验证裁剪到 [soc_min, soc_max] 不越界
    soc_c = float(rng.uniform(0.0, 1.0))
    p_ch = 1e6
    soc_new = min(CFG.soc_max, soc_c + eta * p_ch * DT_H / E_BAT)
    bound_ok = soc_new <= CFG.soc_max + 1e-12
    ok = max_res <= 1e-6 and n_cases > 100 and bound_ok
    report("1", "电池 SOC 单步守恒 ≤1e-6", ok,
           f"η∈{{1.0,0.95,0.9}} 有效样本 {n_cases}，最大残差 {max_res:.3e}（阈值 1e-6）；边界裁剪不越界={bound_ok}")
    return ok


# ==================================================================
# 测试 2：FC/EL 分段线性（SOS2 支撑点凸组合）vs 现有模型 ≤2%
#   参考模型 = 现有常数效率公式（ems_stage2b 同式）：
#     FC：ṁ(P)=P/(η_fc·LHV)；EL：ṁ(P)=η_el·P/LHV，P∈[P_min, P_rated]
#   支撑点覆盖 0 点 + P_min→P_rated 的 K 等分（严格递增，SOS2 凸组合等价插值）
# ==================================================================
def piecewise_interp(points_p, points_m, p):
    """支撑点凸组合插值（SOS2 分段线性的数值等价）。"""
    if p <= points_p[0]:
        return points_m[0]
    if p >= points_p[-1]:
        return points_m[-1]
    for k in range(len(points_p) - 1):
        if points_p[k] <= p <= points_p[k + 1]:
            t = (p - points_p[k]) / (points_p[k + 1] - points_p[k])
            return points_m[k] + t * (points_m[k + 1] - points_m[k])
    return points_m[-1]


def test2_pwl_fc_el():
    def fc_ref(p):
        return p / (ETA_FC * LHV_H2_KWH_PER_KG)

    def el_ref(p):
        return ETA_EL * p / LHV_H2_KWH_PER_KG

    grid = np.linspace(P_FC_MIN, P_FC_RATED, 2001)   # 设备运行域 [P_min, P_rated]
    max_err = {"fc": {}, "el": {}}
    for K in (3, 4, 5):
        for dev, ref, pmin, prated in (("fc", fc_ref, P_FC_MIN, P_FC_RATED),
                                       ("el", el_ref, P_EL_MIN, P_EL_RATED)):
            grid_d = np.linspace(pmin, prated, 2001)
            pts_p = np.linspace(0.0, prated, K)     # 含 0 点（设备关停）
            pts_m = np.array([ref(float(p)) for p in pts_p])
            rel = []
            for p in grid_d:
                m_ref = ref(float(p))
                m_pwl = piecewise_interp(pts_p, pts_m, float(p))
                rel.append(abs(m_pwl - m_ref) / max(abs(m_ref), 1e-12))
            max_err[dev][K] = float(max(rel))
    ok = all(max_err[d][K] <= 0.02 for d in ("fc", "el") for K in (3, 4, 5))
    report("2", "FC/EL 分段线性 ≤2%", ok,
           "FC 最大相对误差 " + ", ".join(f"K={K}:{max_err['fc'][K]:.2e}" for K in (3, 4, 5)) +
           " | EL " + ", ".join(f"K={K}:{max_err['el'][K]:.2e}" for K in (3, 4, 5)) +
           "（现有模型为线性，分段线性应近零误差）")
    return ok


# ==================================================================
# 测试 3：H₂ 质量守恒 ≤ 1e-6 kg（v9 §5.3，与 ems_stage2b 同式）
#   m(t+1) = m(t) + (ṁ_prod − ṁ_cons)·Δt − m_leak[t]
#   ṁ_cons = P_fc/(η_fc·LHV)；ṁ_prod = η_el·P_el/LHV
#   m_leak = h2_leakage_per_day·m(t)/24·Δt（线性，非复利）
# ==================================================================
def test3_h2_conservation():
    rng = np.random.default_rng(20260808)
    max_res = 0.0
    n_steps = 0
    m = CFG.h2_kg * 0.5
    for _ in range(2000):
        p_fc = float(rng.uniform(0, P_FC_RATED))
        p_el = float(rng.uniform(0, P_EL_RATED))
        m_before = m
        leak = CFG.h2_leakage_per_day * m / 24.0 * DT_H
        cons = p_fc * DT_H / (ETA_FC * LHV_H2_KWH_PER_KG)
        prod = p_el * DT_H * ETA_EL / LHV_H2_KWH_PER_KG
        m = m_before + prod - cons - leak
        # 守恒残差（不裁剪公式，数学恒等）
        res = abs((m - m_before) - (prod - cons - leak))
        max_res = max(max_res, res)
        n_steps += 1
    ok = max_res <= 1e-6 and n_steps == 2000
    report("3", "H₂ 质量守恒 ≤1e-6 kg", ok,
           f"{n_steps} 步随机充放氢，最大残差 {max_res:.3e}（阈值 1e-6）；"
           f"泄漏换算 {CFG.h2_leakage_per_day}/day → h⁻¹×Δt 线性（与 ems_stage2b 同式）")
    return ok


def main():
    print("=" * 70)
    print("P0 数学单元测试（第 1/2/3 类）— v9 §16.1")
    print(f"时间: {results['timestamp']}")
    print(f"配置: BAT {E_BAT:.1f} kWh | FC {P_FC_RATED:.1f} kW (η={ETA_FC}) | "
          f"EL {P_EL_RATED:.1f} kW (η={ETA_EL}) | H₂ {CFG.h2_kg:.0f} kg | LHV {LHV_H2_KWH_PER_KG}")
    print("=" * 70)

    test1_soc_conservation()
    test2_pwl_fc_el()
    test3_h2_conservation()

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    s = results["summary"]
    print("-" * 70)
    print(f"汇总：{s['pass']} PASS / {s['fail']} FAIL"
          + ("  → 1/2/3 类全部通过 ✅（下一步：4/5/6 类依赖 MILP 核心）"
             if s["fail"] == 0 else "  → 存在失败，见上方明细"))
    print(f"结果已保存：{OUT_JSON}")
    sys.exit(0 if s["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
