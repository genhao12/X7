# -*- coding: utf-8 -*-
"""
bootstrap_check.py — Gate B 冻结项④：Bootstrap 无效样本规则校验脚本（方案 B）

实现 bootstrap_invalid_rule.md（v9 §13/§17.3 预注册规则，2026-08-08 方案 B 冻结）：
  1) 用 milp_scheduler 跑 compact 一段基准轨迹（默认 2 周 = 1,344 窗口，15min 滚动，
     记录每步执行后的 SOC 与 P_ch/P_dis/P_fc/P_el/u_fc/u_el）
  2) 前置验证：rainflow 周切块误差（原始连续 SOC 轨迹 EFC vs 按 672 点分块独立计数求和
     的相对差 ≤5% 才允许用 CI——量化跨周循环截断损失）
  3) 672 点周块池（起点步进 96）→ 有放回抽 ⌈L/672⌉ 块为 replicate → **每块独立重新
     rainflow 计数求和**（块间端点不连续不产生虚假循环；块内 SOC 为真实子段物理在界内
     → 无效率≈0%）→ 越界剔除规则保留
  4) FC/EL 同步重采样功率/启停块（块内统计，块边界转换不计入启停）
  5) 有效 replicate 计算 BAT EFC+日历 / FC / EL 损伤 → 90%/95% CI
  6) 判定：前置切块误差≤5%、无效率≤10%、有效数≥100 → 全部满足才认可 CI

预注册判定（不得后期调整，v9 意见 12）：
  前置验证相对差 ≤ 5%；无效 replicate 占比 ≤ 10%；有效 replicate ≥ 100。

方案变更（2026-08-08）：v1 ΔSOC 拼接+固定 SOC₀ 重构实测 2 周轨迹无效率 100%
（SOC 覆盖全边界必越界）→ v2 周块重采样+独立计数（当前，已冻结）。

CI 语义：给定已实现调度轨迹下的【条件时间变异】，非完整控制策略不确定性
（不重采样 PV/负荷、不重新求解 MILP；v8 意见 8）。

运行环境：DL 环境（D:/Anaconda/envs/DL，gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/bootstrap_check.py            # 默认 2 周轨迹
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/bootstrap_check.py --limit 672  # 1 周（快）
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/bootstrap_check.py --n-rep 500  # 更多 replicate
"""
import sys, os, json, time, argparse
from datetime import date, timedelta
import numpy as np

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SRC)
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import scale_pv_available
from components.efc_cycle_model import rainflow_cycles, equivalent_full_cycles, EFC_A_D
from milp_scheduler import MilpScheduler, MPCState
from run_gate1_30day import load_compact, CSV, WINDOW, T_ON

# —— 预注册参数（bootstrap_invalid_rule.md，2018 前冻结）——
BLOCK = 672                 # 672 点 = 1 周（15min）
BLOCK_STEP = 96             # 块池起点步进（4h）
SOC_LO, SOC_HI = 0.15, 0.95
SOC_TOL = 1e-6              # 与 solver FeasTol 匹配
FC_K_RUN_H, FC_K_START = 2.4e-4, 2.0e-4
EL_K_RUN_H, EL_K_START = 2.0e-4, 1.6e-4
BAT_K_CAL_H = 5.708e-6      # ≈ 5.0%/year 日历退化
DT_H = 0.25
OUT = os.path.join(_HERE, "..", "results", "bootstrap_check_result.json")
SEED = 0


def run_baseline(n_windows, start_day=151, init_soc=0.50):
    """跑 compact 基准轨迹（perfect 预测 + normal MILP 滚动）。
    返回 soc_trace(L+1), P_ch/P_dis/P_fc/P_el/u_fc/u_el(L)。"""
    import pandas as pd
    cfg, _rep = load_compact()
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    pv_raw = d18["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    pv = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)
    start_i = int(start_day * WINDOW)
    pv = pv[start_i:start_i + n_windows + WINDOW]
    load = load[start_i:start_i + n_windows + WINDOW]

    sched = MilpScheduler(cfg, verbose=False)
    state = MPCState(soc=init_soc, h2_kg=cfg.h2_kg * 0.5)
    run_fc = off_fc = run_el = off_el = 0
    prev_u_fc = prev_u_el = 0
    soc_trace = [state.soc]
    cols = {"p_ch": [], "p_dis": [], "p_fc": [], "p_el": [], "u_fc": [], "u_el": []}
    for i in range(n_windows):
        res = sched.solve_window(pv[i:i + WINDOW], load[i:i + WINDOW], state, mode="normal")
        if not res.feasible:
            raise RuntimeError(f"窗口 {i} 不可行 status={res.status_l1}/{res.status_l2}/{res.status_l3}")
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
        soc_trace.append(state.soc)
        cols["p_ch"].append(float(res.commands[0, 0]))
        cols["p_dis"].append(float(res.commands[0, 1]))
        cols["p_fc"].append(float(res.commands[0, 2]))
        cols["p_el"].append(float(res.commands[0, 3]))
        cols["u_fc"].append(state.u_fc)
        cols["u_el"].append(state.u_el)
    return (np.array(soc_trace), {k: np.array(v) for k, v in cols.items()})


def efc_damage(soc_trace):
    """BAT EFC 循环损伤（rainflow + Woehler k=1）+ 日历。返回 (cyc, cal, total)。"""
    cyc = EFC_A_D * equivalent_full_cycles(rainflow_cycles(np.asarray(soc_trace, dtype=float)))
    cal = BAT_K_CAL_H * DT_H * (len(soc_trace) - 1)
    return cyc, cal, cyc + cal


def fc_el_damage(u, p, k_run, k_start):
    """FC/EL 条件损伤：run_h×K_run + 启停×K_start（块内 0→1 转换，边界不计）。"""
    run_h = float(np.sum(np.asarray(u) > 0.5)) * DT_H
    ua = np.asarray(u)
    starts = int(np.sum((ua[1:] > 0.5) & (ua[:-1] <= 0.5)))
    return run_h * k_run + starts * k_start, run_h, starts


def block_pool(soc_trace, cols):
    """672 点周块池（方案 B，2026-08-08）：返回【整周 SOC 轨迹块】+ 同步 FC/EL 功率/启停块。
    块起点 0..L-BLOCK 步进 BLOCK_STEP。块内 SOC 为真实运行子段（MILP 约束保证物理在
    [0.15,0.95] 内 → 无效率≈0%）；replicate=块组合，损伤按块独立 rainflow 计数求和
    （块间端点不连续不产生虚假循环，v9 意见 12 的越界问题由此规避）。"""
    L = len(soc_trace) - 1
    starts = list(range(0, L - BLOCK + 1, BLOCK_STEP))
    if not starts:
        starts = [0]
    soc_b = [soc_trace[s:s + BLOCK + 1] for s in starts]      # 673 点 = 672 步 SOC 轨迹
    ufc = [cols["u_fc"][s:s + BLOCK] for s in starts]
    uel = [cols["u_el"][s:s + BLOCK] for s in starts]
    pfc = [cols["p_fc"][s:s + BLOCK] for s in starts]
    pel = [cols["p_el"][s:s + BLOCK] for s in starts]
    return soc_b, ufc, uel, pfc, pel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1344, help="基准轨迹窗口数（默认 1344=2 周；672=1 周快测）")
    ap.add_argument("--n-rep", type=int, default=200, help="Bootstrap replicate 数（默认 200）")
    ap.add_argument("--start-day", type=int, default=151, help="2018 起始日（默认 151=6 月 1 日夏季）")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    t0 = time.time()

    print("=" * 72)
    print("Bootstrap 无效样本规则校验（Gate B ④，v9 §13/§17.3）")
    _start_date = (date(2018, 1, 1) + timedelta(days=args.start_day)).isoformat()
    print(f"基准轨迹: compact × {args.limit // 96} 天（{_start_date} 起）| 块 {BLOCK} 点(1周) | "
          f"replicate {args.n_rep} | seed {args.seed}")
    print("=" * 72)

    # ── 1. 基准轨迹 ──
    soc_trace, cols = run_baseline(args.limit, start_day=args.start_day)
    L = len(soc_trace) - 1
    print(f"[1/5] 基准轨迹完成: {L} 步 | SOC [{soc_trace.min():.3f}, {soc_trace.max():.3f}] | "
          f"u_fc 运行 {cols['u_fc'].sum():.0f} 步 | u_el 运行 {cols['u_el'].sum():.0f} 步")

    # ── 2. 前置验证（方案 B）：rainflow 周切块误差（v9：按周切块不应显著改变精算损伤）──
    tot_orig = efc_damage(soc_trace)[2]
    n_full = L // BLOCK
    tot_cut = 0.0
    for k in range(n_full):
        b = soc_trace[k * BLOCK:(k + 1) * BLOCK + 1]
        tot_cut += efc_damage(b)[2]
    if n_full * BLOCK < L:                       # 尾部不足一块（补充，仅作完整性）
        tot_cut += efc_damage(soc_trace[n_full * BLOCK:])[2]
    pre_err = abs(tot_cut - tot_orig) / max(abs(tot_orig), 1e-12)
    pre_ok = pre_err <= 0.05
    print(f"[2/5] 前置验证(切块误差): 原始连续 {tot_orig:.6f} D vs 分块独立求和 {tot_cut:.6f} D | "
          f"相对差 {pre_err * 100:.4f}% ≤5% → {'PASS' if pre_ok else 'FAIL'}")

    # ── 3. Bootstrap 重采样（方案 B：周块=1 单位，块组合为 replicate，独立计数）──
    soc_pool, ufc_pool, uel_pool, pfc_pool, pel_pool = block_pool(soc_trace, cols)
    n_blocks = len(soc_pool)
    k_blocks = int(np.ceil(L / BLOCK))
    invalid = []          # (rep_idx, 越界块序号, 越界值)
    valid_bat = []
    valid_fc = []
    valid_el = []
    for r in range(args.n_rep):
        idx = rng.integers(0, n_blocks, size=k_blocks)
        bat_d = 0.0
        fc_d = 0.0
        el_d = 0.0
        bad_rep = None
        for i in idx:
            sb = soc_pool[i]
            bad = (sb < SOC_LO - SOC_TOL) | (sb > SOC_HI + SOC_TOL)
            if bad.any():                          # 块内越界（真实子段理论不发生，保留规则）
                bad_rep = (i, int(np.argmax(bad)), float(sb[np.argmax(bad)]))
                break
            bat_d += efc_damage(sb)[2]             # 每块独立 rainflow 计数求和（不跨块）
            fc_d += fc_el_damage(ufc_pool[i], pfc_pool[i], FC_K_RUN_H, FC_K_START)[0]
            el_d += fc_el_damage(uel_pool[i], pel_pool[i], EL_K_RUN_H, EL_K_START)[0]
        if bad_rep is not None:
            invalid.append((r, bad_rep[0], bad_rep[2]))
            continue
        valid_bat.append(bat_d)
        valid_fc.append(fc_d)
        valid_el.append(el_d)
    n_invalid = len(invalid)
    inv_rate = n_invalid / args.n_rep
    n_valid = args.n_rep - n_invalid
    inv_ok = inv_rate <= 0.10
    n_ok = n_valid >= 100
    print(f"[3/5] Bootstrap(周块独立计数): 无效 {n_invalid}/{args.n_rep} (率 {inv_rate * 100:.2f}% ≤10% → "
          f"{'PASS' if inv_ok else 'FAIL'}) | 有效 {n_valid} ≥100 → {'PASS' if n_ok else 'FAIL'} | "
          f"块池 {n_blocks} 块 × {k_blocks} 块/replicate")
    if invalid:
        print(f"      无效示例(前3): {invalid[:3]}")

    # ── 4. CI（有效 replicate）──
    if n_valid >= 100:
        bat_tot = np.array(valid_bat)
        fc_arr = np.array(valid_fc)
        el_arr = np.array(valid_el)
        def ci(a):
            return (float(np.median(a)), float(np.percentile(a, 5)), float(np.percentile(a, 95)),
                    float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5)))
        bat_m, bat_lo90, bat_hi90, bat_lo95, bat_hi95 = ci(bat_tot)
        fc_m, fc_lo90, fc_hi90, fc_lo95, fc_hi95 = ci(fc_arr)
        el_m, el_lo90, el_hi90, el_lo95, el_hi95 = ci(el_arr)
        print(f"[4/5] BAT 损伤: median {bat_m:.5f} D | 90%CI [{bat_lo90:.5f},{bat_hi90:.5f}] | "
              f"95%CI [{bat_lo95:.5f},{bat_hi95:.5f}]")
        print(f"      FC  损伤: median {fc_m:.5f} D | 90%CI [{fc_lo90:.5f},{fc_hi90:.5f}] | "
              f"95%CI [{fc_lo95:.5f},{fc_hi95:.5f}]")
        print(f"      EL  损伤: median {el_m:.5f} D | 90%CI [{el_lo90:.5f},{el_hi90:.5f}] | "
              f"95%CI [{el_lo95:.5f},{el_hi95:.5f}]")
    else:
        bat_m = fc_m = el_m = None
        bat_lo90 = bat_hi90 = bat_lo95 = bat_hi95 = None
        fc_lo90 = fc_hi90 = fc_lo95 = fc_hi95 = None
        el_lo90 = el_hi90 = el_lo95 = el_hi95 = None
        print("[4/5] 有效 replicate <100 → 不计算 CI（仅报告分布）")

    # ── 5. 判定与输出 ──
    checks = {"1_pre_validate_le5pct": bool(pre_ok),
              "2_invalid_rate_le10pct": bool(inv_ok),
              "3_valid_n_ge100": bool(n_ok)}
    all_pass = bool(all(checks.values()) and n_valid >= 100)
    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rule": "bootstrap_invalid_rule.md (Gate B ④, v9 §13/§17.3)",
        "params": {"block": BLOCK, "block_step": BLOCK_STEP, "n_rep": args.n_rep,
                   "seed": args.seed, "soc_bounds": [SOC_LO, SOC_HI], "soc_tol": SOC_TOL,
                   "pre_validate_threshold_pct": 5, "invalid_rate_threshold": 0.10,
                   "min_valid_n": 100, "ci_levels": [90, 95]},
        "baseline": {"n_windows": args.limit, "start_day": args.start_day,
                     "soc_range": [float(soc_trace.min()), float(soc_trace.max())],
                     "fc_run_steps": int(cols["u_fc"].sum()),
                     "el_run_steps": int(cols["u_el"].sum())},
        "pre_validate": {"efc_orig_continuous": round(tot_orig, 8),
                         "efc_cut_sum_blocks": round(tot_cut, 8),
                         "rel_err_pct": round(pre_err * 100, 6), "pass": bool(pre_ok)},
        "invalid": {"n": n_invalid, "rate": round(inv_rate, 6),
                    "examples": invalid[:5],
                    "rule": "SOC 越界 [0.15-1e-6, 0.95+1e-6] 剔除；率≤10% 且有效≥100 才认可 CI"},
        "valid_n": n_valid,
        "damage_ci": {"bat": {"median": bat_m, "ci90": [bat_lo90, bat_hi90], "ci95": [bat_lo95, bat_hi95]},
                      "fc": {"median": fc_m, "ci90": [fc_lo90, fc_hi90], "ci95": [fc_lo95, fc_hi95]},
                      "el": {"median": el_m, "ci90": [el_lo90, el_hi90], "ci95": [el_lo95, el_hi95]}},
        "checks": checks,
        "pass": bool(all_pass),
        "ci_semantics": ("条件时间变异：给定已实现调度轨迹下的时间块重采样变异；"
                         "非完整控制策略不确定性（不重采样 PV/负荷、不重跑 MILP）"),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("-" * 72)
    print(("BOOTSTRAP RULE PASS ✅（前置验证/无效率/有效数全部达标，CI 可用）"
           if all_pass else "BOOTSTRAP RULE FAIL ❌（见上方 FAIL 项，CI 不可用）"))
    print(f"结果已保存：{OUT}")
    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
