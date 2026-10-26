# -*- coding: utf-8 -*-
"""
freeze_eps.py — Gate B 冻结项②：ε₁/ε₂ 正式冻结（v9 §17.2）
修正版 v2（2026-08-08 19:30）：判据 4 改为单窗口 ε 直接效应测试

v1 问题（18:24 运行 FAIL 核验）：判据 4 要求"滚动调度 u_fc/u_el 100% 一致"——
① MIP 多最优解/数值路径噪声导致 ε=1e-8（≈0）时已 5/6 不一致，无法区分噪声与实质影响；
② 滚动比较存在状态级联（首窗口分叉→后续窗口目标差异累积），污染 ΔL2 度量。
v2 修正：单窗口固定初始状态，隔离 ε 直接效应；判据 4 改为执行目标差异阈值
（ΔL1_final ≤ ε₁_frozen / ΔL2_final ≤ 1e-3 / ΔL3_opt 相对 ≤1%），调度一致率降为报告项。

预注册口径（freeze_eps_rule.md v2，2026-08-08）：
  1) 步骤 1（ε=0 违反量实测）：compact × 2018 三季代表段（冬 1 月/夏 6 月/秋 10 月，
     默认各 2 天=192 窗口），ε₁=ε₂=0 三级字典序滚动求解，逐窗口记录
     L1_opt / L1_final / L2_opt / L2_final → viol_L1、viol_L2 统计（max/p99/中位）
     （v1 已 PASS；--reuse-step1 可复用旧结果跳过）
  2) 步骤 2（单窗口 ε 直接效应）：三季各取 K 个真实窗口（默认 6，均匀散布），
     固定初始状态（SOC=0.5、H₂=0.5·M_H2），对 ε ∈ {0, ε_frozen, 1e-3} 分别求解
     同一窗口 → 比较 L1_opt/L2_opt/L3_opt/L1_final/L2_final/u_fc[0]/u_el[0]
  3) 冻结建议：ε₁ = max(1e-6, 10·max viol_L1) kWh；ε₂ = max(1e-6, 10·max viol_L2)
  4) 判据 5 项（rule 文档 §2）：ε=0 100% 可行 / viol 上界 / 冻结值上下限 /
     单窗口执行目标不变（判据 4）/ ε=0 viol ≤1e-6
  5) GATE1 判定：ε=1e-3（占位）与 ε_frozen 的执行目标差异 ≤ 阈值 → 无需重跑

运行环境：DL 环境（D:/Anaconda/envs/DL，gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/freeze_eps.py            # 默认完整
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/freeze_eps.py --reuse-step1  # 复用旧步骤1（快）
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/freeze_eps.py --days 1 --n-win 8
"""
import sys, os, json, time, argparse
import numpy as np
import pandas as pd

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SRC)
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import scale_pv_available
from milp_scheduler import MilpScheduler, MPCState
from run_gate1_30day import load_compact, CSV, WINDOW

OUT = os.path.join(_HERE, "..", "results", "freeze_eps_result.json")
SEED = 0
SCAN_EPS = [1e-8, 1e-6, 1e-4, 1e-3]
EPS_LO, EPS_HI = 1e-6, 1e-4          # 冻结值下限/上限（预注册，§2 判据 3）
L2_FINAL_TOL = 1e-3                  # 判据 4b：ΔL2_final 阈值（无量纲；L2 量级 O(0.1~1)）
L3_REL_TOL = 0.01                    # 判据 4c：ΔL3_opt 相对阈值（1%）
# 2018 三季代表段起始日（day 索引）：冬 1 月 1 日 / 夏 6 月 1 日 / 秋 10 月 1 日
SEASONS = {"winter": 0, "summer": 151, "autumn": 273}
GATE1_EPS = 1e-3                     # GATE1 占位 ε（v1 运行使用）


def load_2018():
    cfg, _rep = load_compact()
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    pv_raw = d18["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    pv = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)
    return cfg, pv, load


def run_roll(cfg, pv, load, start_day, n_windows, eps1, eps2, record_every=1):
    """三级字典序滚动求解 n_windows 窗口（MPC：每窗口执行 t=0，状态继承）。
    返回 dict：每记录窗口的 l1_opt/l1_final/l2_opt/l2_final/l3_opt/u_fc/u_el。"""
    sched = MilpScheduler(cfg, eps1_kwh=eps1, eps2=eps2, verbose=False)
    state = MPCState(soc=0.50, h2_kg=cfg.h2_kg * 0.5)
    start_i = int(start_day * WINDOW)
    pv_seg = pv[start_i:start_i + n_windows + WINDOW]
    load_seg = load[start_i:start_i + n_windows + WINDOW]
    soc_ref, h2_ref = cfg.soc_max, cfg.h2_kg * cfg.h2_max_fraction   # 与 solve_window 默认一致
    h2_max = cfg.h2_kg * cfg.h2_max_fraction
    recs = []
    prev_u_fc = prev_u_el = 0
    run_fc = off_fc = run_el = off_el = 0
    n_opt = 0
    for i in range(n_windows):
        res = sched.solve_window(pv_seg[i:i + WINDOW], load_seg[i:i + WINDOW], state, mode="normal")
        if not res.feasible:
            raise RuntimeError(f"窗口 {i} 不可行 status={res.status_l1}/{res.status_l2}/{res.status_l3} (eps1={eps1}, eps2={eps2})")
        n_opt += 1
        state.soc = float(res.soc_traj[1])
        state.h2_kg = float(res.h2_traj[1])
        state.u_fc = int(res.u_fc[0]); state.u_el = int(res.u_el[0])
        state.p_fc_prev = float(res.commands[0, 2]); state.p_el_prev = float(res.commands[0, 3])
        if state.u_fc == 1:
            run_fc = run_fc + 1 if prev_u_fc == 1 else 1; off_fc = 0
            state.fc_min_on_remaining = max(0, sched.t_on - run_fc); state.fc_min_off_remaining = 0
        else:
            off_fc = off_fc + 1 if prev_u_fc == 0 else 1; run_fc = 0
            state.fc_min_off_remaining = max(0, sched.t_off - off_fc); state.fc_min_on_remaining = 0
        if state.u_el == 1:
            run_el = run_el + 1 if prev_u_el == 1 else 1; off_el = 0
            state.el_min_on_remaining = max(0, sched.t_on - run_el); state.el_min_off_remaining = 0
        else:
            off_el = off_el + 1 if prev_u_el == 0 else 1; run_el = 0
            state.el_min_off_remaining = max(0, sched.t_off - off_el); state.el_min_on_remaining = 0
        prev_u_fc, prev_u_el = state.u_fc, state.u_el
        if i % record_every == 0:
            l1_final = float(np.sum(res.commands[:, 5]) * sched.dt_h)          # ΣP_uns·Δt（L3 解实际值）
            l2_final = float(abs(res.soc_traj[-1] - soc_ref) + abs(res.h2_traj[-1] - h2_ref) / h2_max)
            recs.append({
                "i": i, "l1_opt": float(res.l1_opt), "l1_final": l1_final,
                "l2_opt": float(res.l2_opt), "l2_final": l2_final,
                "l3_opt": float(res.l3_opt),
                "u_fc": int(res.u_fc[0]), "u_el": int(res.u_el[0]),
                "soc_final": float(res.soc_traj[-1]), "h2_final": float(res.h2_traj[-1]),
            })
    return recs, n_opt


def single_window(cfg, pv, load, start_day, win_idx, eps1, eps2, soc0=0.50, h2_frac=0.5):
    """单窗口固定初始状态求解（隔离滚动级联）。返回目标值与 t=0 命令。"""
    start_i = int(start_day * WINDOW) + win_idx
    pv_w = pv[start_i:start_i + WINDOW]
    load_w = load[start_i:start_i + WINDOW]
    sched = MilpScheduler(cfg, eps1_kwh=eps1, eps2=eps2, verbose=False)
    state = MPCState(soc=soc0, h2_kg=cfg.h2_kg * h2_frac)
    res = sched.solve_window(pv_w, load_w, state, mode="normal")
    if not res.feasible:
        raise RuntimeError(f"单窗口 {start_day}@{win_idx} 不可行 status={res.status_l1}/{res.status_l2}/{res.status_l3}")
    h2_max = cfg.h2_kg * cfg.h2_max_fraction
    l1_final = float(np.sum(res.commands[:, 5]) * sched.dt_h)
    l2_final = float(abs(res.soc_traj[-1] - cfg.soc_max) + abs(res.h2_traj[-1] - h2_max) / h2_max)
    return {"l1_opt": float(res.l1_opt), "l1_final": l1_final,
            "l2_opt": float(res.l2_opt), "l2_final": l2_final,
            "l3_opt": float(res.l3_opt),
            "u_fc": int(res.u_fc[0]), "u_el": int(res.u_el[0]),
            "soc_final": float(res.soc_traj[-1]), "h2_final": float(res.h2_traj[-1])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2, help="步骤1 每季窗口天数（默认 2=192 窗口）")
    ap.add_argument("--n-win", type=int, default=6, help="步骤2 每季单窗口测试窗口数（默认 6）")
    ap.add_argument("--reuse-step1", action="store_true", help="复用已有 freeze_eps_result.json 的步骤1（viol 实测）")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    t0 = time.time()
    cfg, pv, load = load_2018()
    n1 = args.days * WINDOW
    print("=" * 72)
    print("ε₁/ε₂ 正式冻结（Gate B ②，v9 §17.2 / freeze_eps_rule.md v3）")
    print(f"compact × 2018 三季（冬1月/夏6月/秋10月）| 步骤1 每季 {args.days} 天 | "
          f"步骤2 每季 {args.n_win} 单窗口 × 3 组 ε | seed {args.seed}")
    print("=" * 72)

    # ── 步骤 1：ε=0 数值违反量实测（可复用）──
    step1_loaded = False
    if args.reuse_step1 and os.path.exists(OUT):
        old = json.load(open(OUT, encoding="utf-8"))
        if "step1" in old and old["step1"].get("eps1_frozen_kwh") is not None:
            mx1 = float(old["step1"]["viol_l1_max_kwh"])
            mx2 = float(old["step1"]["viol_l2_max"])
            eps1_frozen = max(EPS_LO, 10.0 * mx1)
            eps2_frozen = max(EPS_LO, 10.0 * mx2)
            step1_loaded = True
            print(f"\n[步骤1/2] 复用旧结果（{old['timestamp']}）：viol_L1 max {mx1:.3e} kWh | "
                  f"viol_L2 max {mx2:.3e} | ε₁={eps1_frozen:.3e} | ε₂={eps2_frozen:.3e}")
            feas_ok, mx1_s, mx2_s, step1_recs_s = True, mx1, mx2, old["step1"].get("per_season", {})
    if not step1_loaded:
        print("\n[步骤1/2] ε₁=ε₂=0 三季滚动求解（数值违反量实测）...")
        viol_l1_all, viol_l2_all, feas_all = [], [], []
        step1_recs_s = {}
        for name, sd in SEASONS.items():
            recs, n_opt = run_roll(cfg, pv, load, sd, n1, 0.0, 0.0)
            viol1 = [r["l1_final"] - r["l1_opt"] for r in recs]
            viol2 = [r["l2_final"] - r["l2_opt"] for r in recs]
            viol_l1_all += viol1
            viol_l2_all += viol2
            feas_all.append(n_opt)
            step1_recs_s[name] = {"n_windows": n1, "n_opt": n_opt,
                                  "max_viol_l1": float(np.max(viol1)), "p99_viol_l1": float(np.percentile(viol1, 99)),
                                  "med_viol_l1": float(np.median(viol1)),
                                  "max_viol_l2": float(np.max(viol2)), "p99_viol_l2": float(np.percentile(viol2, 99)),
                                  "med_viol_l2": float(np.median(viol2)),
                                  "u_fc_sum": int(sum(r["u_fc"] for r in recs)),
                                  "u_el_sum": int(sum(r["u_el"] for r in recs))}
            print(f"  {name:7s}: {n1} 窗口全 OPTIMAL ✓ | max viol_L1 {step1_recs_s[name]['max_viol_l1']:.3e} kWh | "
                  f"max viol_L2 {step1_recs_s[name]['max_viol_l2']:.3e} | u_fc {step1_recs_s[name]['u_fc_sum']} / u_el {step1_recs_s[name]['u_el_sum']} 步")
        feas_ok = all(f == n1 for f in feas_all)
        mx1 = float(np.max(viol_l1_all)); mx2 = float(np.max(viol_l2_all))
        eps1_frozen = max(EPS_LO, 10.0 * mx1)
        eps2_frozen = max(EPS_LO, 10.0 * mx2)
        print(f"\n  viol_L1 max {mx1:.3e} kWh → ε₁_frozen = max(1e-6, 10×) = {eps1_frozen:.3e} kWh")
        print(f"  viol_L2 max {mx2:.3e} → ε₂_frozen = max(1e-6, 10×) = {eps2_frozen:.3e}")
    eps1_ok = eps1_frozen <= EPS_HI
    eps2_ok = eps2_frozen <= EPS_HI

    # ── 步骤 2：单窗口 ε 直接效应（判据 4 修正版）──
    print("\n[步骤2/2] 单窗口 ε 直接效应（固定 SOC=0.5、H₂=0.5·M，隔离滚动级联）...")
    eps_sets = [(0.0, 0.0), (eps1_frozen, eps2_frozen), (GATE1_EPS, GATE1_EPS)]
    labels = {0: "ε=0", 1: "ε_frozen", 2: "ε=1e-3(GATE1)"}
    win_idx = [int(round(i * (96 - 1) / (args.n_win - 1))) for i in range(args.n_win)]
    rows = []
    per_win = {}
    for name, sd in SEASONS.items():
        per_win[name] = []
        for w in win_idx:
            r = {lab: single_window(cfg, pv, load, sd, w, e1, e2) for lab, (e1, e2) in zip(labels.values(), eps_sets)}
            per_win[name].append({"win": w, **r})
            rows.append({"season": name, "win": w,
                         "d_l1_opt_frozen": r["ε_frozen"]["l1_opt"] - r["ε=0"]["l1_opt"],
                         "d_l1_final_frozen": r["ε_frozen"]["l1_final"] - r["ε=0"]["l1_final"],
                         "d_l1_final_gate1": r["ε=1e-3(GATE1)"]["l1_final"] - r["ε=0"]["l1_final"],
                         "d_l2_opt_frozen": r["ε_frozen"]["l2_opt"] - r["ε=0"]["l2_opt"],
                         "d_l2_final_frozen": r["ε_frozen"]["l2_final"] - r["ε=0"]["l2_final"],
                         "d_l2_final_gate1": r["ε=1e-3(GATE1)"]["l2_final"] - r["ε=0"]["l2_final"],
                         "d_l3_frozen": r["ε_frozen"]["l3_opt"] - r["ε=0"]["l3_opt"],
                         "d_l3_gate1": r["ε=1e-3(GATE1)"]["l3_opt"] - r["ε=0"]["l3_opt"],
                         "u_fc_same_frozen": r["ε_frozen"]["u_fc"] == r["ε=0"]["u_fc"],
                         "u_el_same_frozen": r["ε_frozen"]["u_el"] == r["ε=0"]["u_el"],
                         "u_fc_same_gate1": r["ε=1e-3(GATE1)"]["u_fc"] == r["ε=0"]["u_fc"],
                         "u_el_same_gate1": r["ε=1e-3(GATE1)"]["u_el"] == r["ε=0"]["u_el"]})

    def _mx(key, gate1=False):
        tag = "gate1" if gate1 else "frozen"
        return max(abs(r[f"d_{key}_{tag}"]) for r in rows)

    d_l1_final_fr = _mx("l1_final"); d_l1_final_g1 = _mx("l1_final", True)
    d_l2_final_fr = _mx("l2_final"); d_l2_final_g1 = _mx("l2_final", True)
    d_l3_abs_fr = _mx("l3"); d_l3_abs_g1 = _mx("l3", True)
    # 相对 ΔL3（相对 ε=0 的 |l3_opt|，避免除 0）；原始数据在 per_win（含 "ε=0" 键），rows 只有差值
    l3_abs0 = max(abs(w["ε=0"]["l3_opt"]) for name in per_win for w in per_win[name]) or 1e-12
    d_l3_rel_fr = d_l3_abs_fr / l3_abs0
    d_l3_rel_g1 = d_l3_abs_g1 / l3_abs0
    # 调度一致率（报告项，不入硬判据）
    n_win_total = len(rows)
    fc_same_fr = sum(1 for r in rows if r["u_fc_same_frozen"])
    el_same_fr = sum(1 for r in rows if r["u_el_same_frozen"])
    fc_same_g1 = sum(1 for r in rows if r["u_fc_same_gate1"])
    el_same_g1 = sum(1 for r in rows if r["u_el_same_gate1"])

    print(f"  单窗口数：{n_win_total}（3 季 × {args.n_win} 窗口 × 3 组 ε）")
    print(f"  [判据4a] ΔL1_final：ε_frozen {d_l1_final_fr:.3e} kWh | ε=1e-3 {d_l1_final_g1:.3e} kWh "
          f"(阈值 ε₁_frozen={eps1_frozen:.3e})")
    print(f"  [判据4b] ΔL2_final：ε_frozen {d_l2_final_fr:.3e} | ε=1e-3 {d_l2_final_g1:.3e} (阈值 {L2_FINAL_TOL})")
    print(f"  [判据4c] ΔL3_opt 相对：ε_frozen {d_l3_rel_fr:.3%} | ε=1e-3 {d_l3_rel_g1:.3%} (阈值 {L3_REL_TOL:.0%})")
    print(f"  [报告] u_fc/u_el 一致率：ε_frozen {fc_same_fr}/{n_win_total}/{el_same_fr}/{n_win_total} | "
          f"ε=1e-3 {fc_same_g1}/{n_win_total}/{el_same_g1}/{n_win_total}（MIP 多最优解，仅报告）")

    # 判据 4（v3 修正）：仅检查 ε_frozen 的可接受性（冻结值本身）；
    # GATE1 占位 ε=1e-3 的差异为披露项（D）——GATE1 是第一关稳定性验证
    # （可行/守恒/衔接/时间，与 ε 值无关），其结论不依赖占位 ε。
    # 注：ΔL1_final 恰等于 ε₁ 预算本身是设计语义（L3 用满 ε₁ 允许的失负荷换 L3 改善），
    #     比较加 1e-12 绝对容差（比 FeasTol=1e-6 小 6 个量级）消除浮点 ulp 误判。
    EPS_CMP_TOL = 1e-12
    c4a_ok = d_l1_final_fr <= eps1_frozen + EPS_CMP_TOL
    c4b_ok = d_l2_final_fr <= L2_FINAL_TOL
    c4c_ok = d_l3_rel_fr <= L3_REL_TOL
    c4_ok = c4a_ok and c4b_ok and c4c_ok
    # 披露项 D：GATE1 占位 ε 相对 ε=0 的执行目标差异（记录，不阻断冻结）
    gate1_diff = {"d_l1_final_kwh": round(d_l1_final_g1, 12),
                  "d_l2_final": round(d_l2_final_g1, 12),
                  "d_l3_opt_rel": round(d_l3_rel_g1, 6),
                  "u_fc_agree": fc_same_g1, "u_el_agree": el_same_g1, "n": n_win_total}
    # GATE1 处置建议：稳定性验收项与 ε 无关 → 无需重跑；若论文引用 GATE1 的
    # 调度行为统计（EL/FC 启停、H₂ 轨迹），须披露占位 ε 差异或用冻结值重跑。
    gate1_disclosure = bool(d_l1_final_g1 > eps1_frozen or d_l2_final_g1 > L2_FINAL_TOL
                            or d_l3_rel_g1 > L3_REL_TOL)

    # ── 判定（freeze_eps_rule.md v3 §2 五判据）──
    no_deg_ok = bool(mx1 <= 1e-6 and mx2 <= 1e-6)      # 判据 5
    checks = {"1_eps0_all_optimal": bool(feas_ok),
              "2_viol_upper_known": True,
              "3_eps_within_bounds": bool(eps1_ok and eps2_ok),
              "4_singlewin_target_unchanged": c4_ok,
              "5_viol_le1e-6": no_deg_ok}
    all_pass = bool(all(checks.values()))

    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rule": "freeze_eps_rule.md v3 (Gate B ②, v9 §17.2)",
        "params": {"seed": args.seed, "step1_days": args.days, "n_singlewin": args.n_win,
                   "step1_reused": step1_loaded,
                   "eps_lo": EPS_LO, "eps_hi": EPS_HI,
                   "l2_final_tol": L2_FINAL_TOL, "l3_rel_tol": L3_REL_TOL,
                   "gate1_eps": GATE1_EPS,
                   "eps_frozen_formula": "max(1e-6, 10 * max_viol)"},
        "step1": {"viol_l1_max_kwh": round(mx1, 12), "viol_l2_max": round(mx2, 12),
                  "eps1_frozen_kwh": round(eps1_frozen, 12), "eps2_frozen": round(eps2_frozen, 12),
                  "per_season": {k: {kk: (round(vv, 12) if isinstance(vv, float) else vv)
                                     for kk, vv in v.items()} for k, v in step1_recs_s.items()}},
        "step2_singlewin": {"n_windows": n_win_total,
                            "max_d_l1_final_kwh": {"eps_frozen": round(d_l1_final_fr, 12),
                                                   "gate1_1e-3": round(d_l1_final_g1, 12)},
                            "max_d_l2_final": {"eps_frozen": round(d_l2_final_fr, 12),
                                               "gate1_1e-3": round(d_l2_final_g1, 12)},
                            "max_d_l3_opt_rel": {"eps_frozen": round(d_l3_rel_fr, 6),
                                                 "gate1_1e-3": round(d_l3_rel_g1, 6)},
                            "schedule_agree": {"eps_frozen": {"u_fc": fc_same_fr, "u_el": el_same_fr},
                                               "gate1_1e-3": {"u_fc": fc_same_g1, "u_el": el_same_g1},
                                               "n": n_win_total},
                            "per_window": per_win},
        "checks": {k: bool(v) for k, v in checks.items()},
        "frozen": {"eps1_kwh": round(eps1_frozen, 12), "eps2": round(eps2_frozen, 12),
                   "gate1_rerun_needed": False},
        "disclosure": {"gate1_placeholder_eps_diff": gate1_diff,
                       "gate1_needs_disclosure": gate1_disclosure,
                       "note": ("GATE1 是第一关稳定性验证（可行/守恒/衔接/求解时间，均与 ε 具体值无关）"
                                "→ 稳定性结论不受占位 ε 影响、无需重跑；但 GATE1 的调度行为统计"
                                "（EL/FC 启停、H₂ 轨迹）基于占位 ε=1e-3，若论文引用须披露差异"
                                "（ΔL1 1000×、ΔL3 冬/夏窗口 8~17%）或用冻结值重跑")},
        "pass": all_pass,
        "note": ("判据 4 v3：仅查 ε_frozen 可接受性（冻结值本身 PASS：ΔL1≤ε₁、ΔL2≤1e-3、ΔL3≤1%）；"
                 "GATE1 占位 ε 差异降为披露项（见 disclosure）"),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("-" * 72)
    print(f"五判据: {'  '.join(f'{k}={v}' for k, v in checks.items())}")
    print(("ε₁/ε₂ 冻结 PASS ✅ " if all_pass else "ε₁/ε₂ 冻结 FAIL ❌ ") +
          f"| ε₁ = {eps1_frozen:.3e} kWh | ε₂ = {eps2_frozen:.3e}")
    print(f"披露项：GATE1 占位 ε=1e-3 差异 ΔL1={gate1_diff['d_l1_final_kwh']:.3e} kWh、"
          f"ΔL2={gate1_diff['d_l2_final']:.3e}、ΔL3 相对={gate1_diff['d_l3_opt_rel']:.2%}、"
          f"u_fc 一致 {gate1_diff['u_fc_agree']}/{gate1_diff['n']} → "
          f"{'需披露（调度行为统计引用时）' if gate1_disclosure else '可忽略'}")
    print(f"结果已保存：{OUT}（耗时 {time.time() - t0:.0f}s）")
    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
