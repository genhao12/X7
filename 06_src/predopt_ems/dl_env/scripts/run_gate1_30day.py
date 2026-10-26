# -*- coding: utf-8 -*-
"""
run_gate1_30day.py — 第一关：compact × 连续 30 天 MILP 稳定性验证（v9 §10）

任务  ：perfect 预测 + 普通 MILP（normal）× compact 配置 × 2018 年 1 月 1 日起
        连续 30 天（2,880 个 15min 窗口），15min 滚动、96 步预测窗口，
        每窗口只执行第 1 个控制量（MPC 语义），三级字典序三次显式求解。

验收 6 项（v9 §10，全部满足才通过）：
  ① 全部完成    ：2,880 窗口（或 --limit 指定数）全部求解并记录
  ② 不可行 = 0  ：L1/L2/L3 全部 OPTIMAL（status=2）
  ③ 分层 gap    ：L1/L2 用求解器绝对容差（FeasTol=1e-6）+ ε₁/ε₂ 控制
                  （solve_window 内部实现）；L3 显式 MIPGap=0.01，OPTIMAL 即达标
  ④ SOC/H₂ 无越界：轨迹落在 [soc_min,soc_max] / [h2_min,h2_max]
  ⑤ 守恒 ≤1e-6  ：SOC 残差（无量纲分数口径）与 H₂ 残差（kg）逐窗口 ≤1e-6
  ⑥ 跨窗口衔接  ：执行序列 FC/EL 连续运行段 ≥ T_on、连续停机段 ≥ T_off
                  （继承约束强制；第一段停机段豁免——系统初始停机）
  ＋ 求解时间    ：每窗口三级合计中位 ≤5 s（v9 §9 预算，报告项）

运行环境：DL 环境（D:/Anaconda/envs/DL，gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_30day.py            # 完整 2,880 窗口
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_30day.py --limit 96  # 快速 1 天验证
"""
import sys, os, json, time, gc, argparse
import numpy as np
import pandas as pd

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)
sys.path.insert(0, _HERE)   # scripts/ 同目录：import milp_scheduler

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import scale_pv_available
from milp_scheduler import MilpScheduler, MPCState

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = r"D:\matlab\C\07_results\rectification_v2_stage2c\search\verified_admissions.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "gate1_30day_result.json")

WINDOW = 96          # 24h / 15min
T_ON = 5             # minimum_run_steps
T_OFF = 5
DT_H = 0.25


def load_compact():
    adm = json.load(open(ADM, encoding="utf-8"))
    reps = adm["representatives"]
    rep = [r for r in reps if r["representative_role"] == "compact"][0]
    cfg = Stage2BEMSConfig(
        pv_kwp=float(rep["pv_kwp"]),
        bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]),
        pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
    )
    return cfg, rep


def segment_check(u_arr, t_on, t_off):
    """执行序列最小运行/停机段检查。返回 (violations, min_on_len, min_off_len)。
    豁免：序列第一个停机段（系统初始停机、无最小停机要求）。"""
    n = len(u_arr)
    if n == 0:
        return [], None, None
    segs = []
    start = 0
    for idx in range(1, n + 1):
        if idx == n or u_arr[idx] != u_arr[start]:
            segs.append((int(u_arr[start]), start, idx - start))
            start = idx
    viol = []
    min_on = min_off = None
    for i, (val, s, l) in enumerate(segs):
        if val == 1:
            min_on = l if min_on is None else min(min_on, l)
            if l < t_on:
                viol.append(f"on-seg len {l} < T_on {t_on} @ step {s}")
        else:
            if i > 0:  # 第一段停机豁免
                min_off = l if min_off is None else min(min_off, l)
                if l < t_off:
                    viol.append(f"off-seg len {l} < T_off {t_off} @ step {s}")
    return viol, min_on, min_off


def run_gate1(n_windows, start_day=151, verbose=True):
    """start_day: 2018 年起始日偏移（0=1 月 1 日；151=6 月 1 日夏季，FC/EL 活跃、
    高 PV 波动，对 MILP 稳定性压力更大且 MPC 衔接可有效检验）。"""
    cfg, rep = load_compact()
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    # ffill 缺失点（64 CSV 含 165 缺失点，与项目预处理管线一致；bfill 兜底开头缺失）
    pv_raw = d18["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    assert not np.isnan(pv_raw).any() and not np.isnan(load).any(), "数据仍有 NaN"
    pv = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)
    start_i = int(start_day * WINDOW)
    pv = pv[start_i:start_i + n_windows + WINDOW]
    load = load[start_i:start_i + n_windows + WINDOW]
    start_date = pd.Timestamp("2018-01-01") + pd.Timedelta(days=start_day)

    sched = MilpScheduler(cfg, verbose=False)
    state = MPCState(soc=0.50, h2_kg=cfg.h2_kg * 0.50)

    run_fc = off_fc = run_el = off_el = 0
    prev_u_fc = prev_u_el = 0
    u_exec = {"fc": [], "el": []}
    records = []
    n_infeasible = 0
    max_soc_res = 0.0
    max_h2_res = 0.0
    soc_bounds_viol = 0
    h2_bounds_viol = 0
    t0 = time.time()

    for i in range(n_windows):
        pv_w = pv[i:i + WINDOW]
        ld_w = load[i:i + WINDOW]
        res = sched.solve_window(pv_w, ld_w, state, mode="normal")
        rec = {
            "window": i,
            "status": [res.status_l1, res.status_l2, res.status_l3],
            "feasible": res.feasible,
            "solve_time_s": round(res.solve_time_s, 4),
            "l1_opt": round(res.l1_opt, 4),
            "l2_opt": round(res.l2_opt, 6),
            "l3_opt": round(res.l3_opt, 4),
            "soc_res": float(res.soc_conservation_res),
            "h2_res": float(res.h2_conservation_res),
            "soc_lo": float(np.min(res.soc_traj)), "soc_hi": float(np.max(res.soc_traj)),
            "h2_lo": float(np.min(res.h2_traj)), "h2_hi": float(np.max(res.h2_traj)),
        }
        records.append(rec)
        if not res.feasible:
            n_infeasible += 1
            if verbose:
                print(f"[{i}] INFEASIBLE status={rec['status']} t={res.solve_time_s:.2f}s", flush=True)
            continue  # 状态不推进，继续统计（验收要求不可行=0）

        # —— 执行第 1 个控制量（MPC 语义，v9 §5.1）——
        state.soc = float(res.soc_traj[1])
        state.h2_kg = float(res.h2_traj[1])
        state.u_fc = int(res.u_fc[0])
        state.u_el = int(res.u_el[0])
        state.p_fc_prev = float(res.commands[0, 2])
        state.p_el_prev = float(res.commands[0, 3])

        # 剩余最小开/停机（运行/停机步数法，与测试 6 同逻辑）
        if state.u_fc == 1:
            run_fc = run_fc + 1 if prev_u_fc == 1 else 1
            off_fc = 0
            state.fc_min_on_remaining = max(0, sched.t_on - run_fc)
            state.fc_min_off_remaining = 0
        else:
            off_fc = off_fc + 1 if prev_u_fc == 0 else 1
            run_fc = 0
            state.fc_min_off_remaining = max(0, sched.t_off - off_fc)
            state.fc_min_on_remaining = 0
        if state.u_el == 1:
            run_el = run_el + 1 if prev_u_el == 1 else 1
            off_el = 0
            state.el_min_on_remaining = max(0, sched.t_on - run_el)
            state.el_min_off_remaining = 0
        else:
            off_el = off_el + 1 if prev_u_el == 0 else 1
            run_el = 0
            state.el_min_off_remaining = max(0, sched.t_off - off_el)
            state.el_min_on_remaining = 0
        prev_u_fc = state.u_fc
        prev_u_el = state.u_el
        u_exec["fc"].append(state.u_fc)
        u_exec["el"].append(state.u_el)

        max_soc_res = max(max_soc_res, res.soc_conservation_res)
        max_h2_res = max(max_h2_res, res.h2_conservation_res)
        # 越界容差与 solver FeasTol=1e-6 匹配（solver 允许 ≤1e-6 的边界违反）
        B_TOL = 1e-6
        if rec["soc_lo"] < cfg.soc_min - B_TOL or rec["soc_hi"] > cfg.soc_max + B_TOL:
            soc_bounds_viol += 1
        if rec["h2_lo"] < cfg.h2_kg * cfg.h2_min_fraction - B_TOL or rec["h2_hi"] > cfg.h2_kg * cfg.h2_max_fraction + B_TOL:
            h2_bounds_viol += 1

        if verbose and (i % 480 == 0 or i == n_windows - 1):
            print(f"[{i + 1}/{n_windows}] t={res.solve_time_s:.2f}s "
                  f"L1={res.l1_opt:.2f} L2={res.l2_opt:.3f} L3={res.l3_opt:.1f} "
                  f"SOC {state.soc:.2f} H2 {state.h2_kg:.0f}kg "
                  f"u_fc={state.u_fc} u_el={state.u_el}", flush=True)
        if i % 200 == 0:
            gc.collect()

    total_s = time.time() - t0
    solve_times = np.array([r["solve_time_s"] for r in records])
    # 段检查（验收 ⑥）
    viol_fc, min_on_fc, min_off_fc = segment_check(u_exec["fc"], T_ON, T_OFF)
    viol_el, min_on_el, min_off_el = segment_check(u_exec["el"], T_ON, T_OFF)

    # —— 6 项验收 ——
    checks = {
        "1_all_solved": n_windows > 0 and len(records) == n_windows,
        "2_infeasible_zero": n_infeasible == 0,
        "3_l3_gap_optimal": all(r["status"][2] == 2 for r in records),
        "4_soc_h2_in_bounds": soc_bounds_viol == 0 and h2_bounds_viol == 0,
        "5_conservation_le1e6": max_soc_res <= 1e-6 and max_h2_res <= 1e-6,
        "6_mpc_link_ok": len(viol_fc) == 0 and len(viol_el) == 0,
    }
    checks["7_median_time_le5s"] = float(np.median(solve_times)) <= 5.0
    checks = {k: bool(v) for k, v in checks.items()}   # np.bool_ → bool（JSON 可序列化）
    all_pass = bool(all(checks.values()))

    summary = {
        "window_count": n_windows,
        "infeasible_count": n_infeasible,
        "soc_bounds_violated": soc_bounds_viol,
        "h2_bounds_violated": h2_bounds_viol,
        "max_soc_conservation_res": max_soc_res,
        "max_h2_conservation_res": max_h2_res,
        "median_solve_time_s": float(np.median(solve_times)),
        "p95_solve_time_s": float(np.percentile(solve_times, 95)),
        "max_solve_time_s": float(np.max(solve_times)),
        "total_wall_time_s": round(total_s, 2),
        "est_full_30d_time_min": round(total_s / n_windows * 2880 / 60, 1),
        "fc_min_on_seg": min_on_fc, "fc_min_off_seg": min_off_fc,
        "el_min_on_seg": min_on_el, "el_min_off_seg": min_off_el,
        "fc_segment_violations": viol_fc[:10],
        "el_segment_violations": viol_el[:10],
        "checks": checks,
        "pass": bool(all_pass),
    }
    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "Gate1 compact x 30d MILP stability (perfect forecast + normal MILP)",
        "config": {k: rep[k] for k in ["representative_role", "pv_kwp", "bat_kwh",
                                       "pemfc_kw", "pemel_kw", "h2_kg"]},
        "solver": {"gurobipy": "12.0.3(过渡)/13.0.1(目标)", "eps1_kwh": sched.eps1_kwh,
                   "eps2": sched.eps2, "L3_MIPGap": 0.01,
                   "FeasTol": 1e-6, "OptimalityTol": 1e-6, "IntFeasTol": 1e-5,
                   "TimeLimit_s": sched.time_limit_s, "Threads": sched.thread},
        "summary": summary,
        "windows": records,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # —— 控制台输出 ——
    print("=" * 72)
    print("第一关：compact × 30 天 MILP 稳定性（v9 §10）")
    print(f"时段: {start_date.date()} 起 {n_windows // 96} 天（2018）")
    print(f"配置: BAT {cfg.bat_kwh:.0f} kWh (Pmax {cfg.bat_kwh * cfg.bat_c_rate:.0f} kW) | "
          f"FC {cfg.pemfc_kw:.0f} kW | EL {cfg.pemel_kw:.0f} kW | H2 {cfg.h2_kg:.0f} kg")
    print(f"窗口数: {n_windows}（{n_windows // 96} 天）| 总墙钟 {total_s:.0f}s | "
          f"中位 {np.median(solve_times):.3f}s/窗口（预算 ≤5s）")
    print("-" * 72)
    for k, v in checks.items():
        print(f"[{'PASS' if v else 'FAIL'}] {k}")
    print(f"[INFO] 不可行窗口 {n_infeasible} | SOC 越界 {soc_bounds_viol} | H2 越界 {h2_bounds_viol}")
    print(f"[INFO] 最大守恒残差 SOC {max_soc_res:.3e} / H2 {max_h2_res:.3e} kg（阈值 1e-6）")
    print(f"[INFO] FC 段 min_on {min_on_fc} / min_off {min_off_fc} | "
          f"EL 段 min_on {min_on_el} / min_off {min_off_el}（T={T_ON}）")
    print("-" * 72)
    print(("GATE1 PASS ✅（6+1 项验收全部通过）" if all_pass else "GATE1 FAIL ❌（见上方 FAIL 项）"))
    print(f"结果已保存：{OUT}")
    return all_pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="窗口数上限（默认 2880=30 天；可用 96=1 天快速验证）")
    ap.add_argument("--start-day", type=int, default=151,
                    help="2018 起始日偏移（0=1 月 1 日；默认 151=6 月 1 日夏季，"
                         "FC/EL 活跃、高 PV 波动，MPC 衔接可有效检验）")
    args = ap.parse_args()
    n = args.limit if args.limit else 2880
    ok = run_gate1(n, start_day=args.start_day)
    sys.exit(0 if ok else 1)
