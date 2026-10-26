# -*- coding: utf-8 -*-
"""
run_gate1_fc_active.py — 第一关补充：FC 强制活跃专项验证

背景：GATE1 完整 30 天（2018-06-01 起、初始 SOC=0.50）中 FC/EL 未启动——
      L3 占位权重（w_fc=1.0/w_el=0.3/w_curt=0.1/start=5.0）下电池充足覆盖夜间
      （L1 无失负荷）、弃光罚低于制氢成本（EL 不制氢），验收⑥（MPC 跨窗口衔接）
      成为"空验证"。本专项用【低初始 SOC=0.15】+【冬季 2018-01-01 起】触发
      FC 夜间启动（L1 失负荷风险强制），检验启停事件跨窗口最小运行/停机约束
      （T_on=T_off=5）的真实行为。

与 GATE1 完全一致（v9 §10）：perfect 预测 + normal MILP × compact，
15min 滚动、96 步窗口、每窗口只执行第 1 个控制量、三级字典序三次显式求解、
状态继承（SOC/H₂/u/剩余开停机/P_prev）。仅初始 SOC 与时段不同——
【不改 L3 权重】（权重按 v9 在 2017 验证集选择，此处保持占位）。

验收：
  ① 不可行 = 0（L1/L2/L3 全 OPTIMAL）
  ② FC 启动事件 ≥ 3（证明真实启停发生，专项目的）
  ③ FC/EL 段检查无违例（运行段 ≥ T_on、停机段 ≥ T_off，含跨窗口段）
  ④ 守恒 ≤1e-6（SOC 无量纲分数 / H₂ kg）
  ⑤ 中位求解 ≤5 s
  ⑥ L1 如实记录（低 SOC 冬季可能 >0，不做硬性要求）

运行环境：DL 环境（D:/Anaconda/envs/DL，gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_fc_active.py              # 默认 10 天
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_fc_active.py --limit 2880  # 30 天
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_fc_active.py --limit 96    # 1 天快速验证
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_fc_active.py --init-soc 0.30 --start-day 151
"""
import sys, os, json, time, gc, argparse
import numpy as np
import pandas as pd

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SRC)
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import scale_pv_available
from milp_scheduler import MilpScheduler, MPCState
from run_gate1_30day import load_compact, segment_check

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
OUT = os.path.join(_HERE, "..", "results", "fc_active_result.json")

WINDOW = 96
T_ON = 5
T_OFF = 5


def find_events(u_arr):
    """从执行序列找启停事件。u_arr[0]==1 计为初始启动（系统初始 u=0，t=0 即启动）。
    返回 (events, n_start, n_stop)。"""
    events = []
    n_start = n_stop = 0
    if len(u_arr) > 0 and u_arr[0] == 1:
        events.append(("start", 0))
        n_start += 1
    for i in range(1, len(u_arr)):
        if u_arr[i] == 1 and u_arr[i - 1] == 0:
            events.append(("start", i))
            n_start += 1
        elif u_arr[i] == 0 and u_arr[i - 1] == 1:
            events.append(("stop", i))
            n_stop += 1
    return events, n_start, n_stop


def run_fc_active(n_windows, start_day=0, init_soc=0.15, init_h2_frac=0.5, verbose=True):
    cfg, rep = load_compact()
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    pv_raw = d18["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    assert not np.isnan(pv_raw).any() and not np.isnan(load).any(), "数据仍有 NaN"
    pv = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)
    start_i = int(start_day * WINDOW)
    pv = pv[start_i:start_i + n_windows + WINDOW]
    load = load[start_i:start_i + n_windows + WINDOW]
    start_date = pd.Timestamp("2018-01-01") + pd.Timedelta(days=start_day)

    sched = MilpScheduler(cfg, verbose=False)
    state = MPCState(soc=float(init_soc), h2_kg=cfg.h2_kg * float(init_h2_frac))

    run_fc = off_fc = run_el = off_el = 0
    prev_u_fc = prev_u_el = 0
    u_exec = {"fc": [], "el": []}
    records = []
    n_infeasible = 0
    max_soc_res = 0.0
    max_h2_res = 0.0
    soc_bounds_viol = h2_bounds_viol = 0
    fc_run_steps = el_run_steps = 0
    l1_max = 0.0
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
            "u_fc0": int(res.u_fc[0]) if res.feasible else -1,
            "u_el0": int(res.u_el[0]) if res.feasible else -1,
            "p_fc0": round(float(res.commands[0, 2]), 2) if res.feasible else -1,
            "soc_lo": float(np.min(res.soc_traj)), "soc_hi": float(np.max(res.soc_traj)),
            "h2_lo": float(np.min(res.h2_traj)), "h2_hi": float(np.max(res.h2_traj)),
            "soc_res": float(res.soc_conservation_res),
            "h2_res": float(res.h2_conservation_res),
        }
        records.append(rec)
        l1_max = max(l1_max, res.l1_opt)
        if not res.feasible:
            n_infeasible += 1
            if verbose:
                print(f"[{i}] INFEASIBLE status={rec['status']}", flush=True)
            continue

        # —— 执行第 1 个控制量（MPC 语义）——
        state.soc = float(res.soc_traj[1])
        state.h2_kg = float(res.h2_traj[1])
        state.u_fc = int(res.u_fc[0])
        state.u_el = int(res.u_el[0])
        state.p_fc_prev = float(res.commands[0, 2])
        state.p_el_prev = float(res.commands[0, 3])

        # 剩余最小开/停机（运行步数法，与测试 6 / GATE1 同逻辑）
        if state.u_fc == 1:
            run_fc = run_fc + 1 if prev_u_fc == 1 else 1
            off_fc = 0
            state.fc_min_on_remaining = max(0, sched.t_on - run_fc)
            state.fc_min_off_remaining = 0
            fc_run_steps += 1
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
            el_run_steps += 1
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
        B_TOL = 1e-6
        if rec["soc_lo"] < cfg.soc_min - B_TOL or rec["soc_hi"] > cfg.soc_max + B_TOL:
            soc_bounds_viol += 1
        if rec["h2_lo"] < cfg.h2_kg * cfg.h2_min_fraction - B_TOL or rec["h2_hi"] > cfg.h2_kg * cfg.h2_max_fraction + B_TOL:
            h2_bounds_viol += 1

        if verbose and (i % 480 == 0 or i == n_windows - 1):
            print(f"[{i + 1}/{n_windows}] t={res.solve_time_s:.2f}s L1={res.l1_opt:.2f} "
                  f"L2={res.l2_opt:.3f} L3={res.l3_opt:.1f} SOC {state.soc:.2f} H2 {state.h2_kg:.0f}kg "
                  f"u_fc={state.u_fc} u_el={state.u_el}", flush=True)
        if i % 200 == 0:
            gc.collect()

    total_s = time.time() - t0
    solve_times = np.array([r["solve_time_s"] for r in records])

    # —— 启停事件与段检查 ——
    ev_fc, n_start_fc, n_stop_fc = find_events(u_exec["fc"])
    ev_el, n_start_el, n_stop_el = find_events(u_exec["el"])
    viol_fc, min_on_fc, min_off_fc = segment_check(u_exec["fc"], T_ON, T_OFF)
    viol_el, min_on_el, min_off_el = segment_check(u_exec["el"], T_ON, T_OFF)

    # —— 验收（专项标准）——
    checks = {
        "1_infeasible_zero": n_infeasible == 0,
        "2_fc_start_ge3": n_start_fc >= 3,
        "3_fc_segments_ok": len(viol_fc) == 0 and min_on_fc is not None and min_on_fc >= T_ON,
        "4_el_segments_ok": len(viol_el) == 0,
        "5_conservation_le1e6": max_soc_res <= 1e-6 and max_h2_res <= 1e-6,
        "6_median_time_le5s": float(np.median(solve_times)) <= 5.0,
    }
    checks = {k: bool(v) for k, v in checks.items()}
    all_pass = bool(all(checks.values()))

    summary = {
        "n_windows": n_windows,
        "start_date": str(start_date.date()),
        "init_soc": init_soc, "init_h2_frac": init_h2_frac,
        "infeasible_count": n_infeasible,
        "soc_bounds_violated": soc_bounds_viol, "h2_bounds_violated": h2_bounds_viol,
        "fc_start_events": n_start_fc, "fc_stop_events": n_stop_fc,
        "el_start_events": n_start_el, "el_stop_events": n_stop_el,
        "fc_run_steps": fc_run_steps, "el_run_steps": el_run_steps,
        "fc_run_h": round(fc_run_steps * 0.25, 1), "el_run_h": round(el_run_steps * 0.25, 1),
        "fc_min_on_seg": min_on_fc, "fc_min_off_seg": min_off_fc,
        "el_min_on_seg": min_on_el, "el_min_off_seg": min_off_el,
        "fc_segment_violations": viol_fc[:10],
        "el_segment_violations": viol_el[:10],
        "max_soc_conservation_res": max_soc_res,
        "max_h2_conservation_res": max_h2_res,
        "l1_max_kwh": l1_max,
        "median_solve_time_s": float(np.median(solve_times)),
        "p95_solve_time_s": float(np.percentile(solve_times, 95)),
        "total_wall_time_s": round(total_s, 2),
        "checks": checks,
        "pass": bool(all_pass),
    }
    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "Gate1 supplement: FC-forced-active MPC linkage validation (low SOC winter)",
        "config": {k: rep[k] for k in ["representative_role", "pv_kwp", "bat_kwh",
                                       "pemfc_kw", "pemel_kw", "h2_kg"]},
        "weights_placeholder": {"w_fc": sched.w["fc"], "w_el": sched.w["el"],
                                "w_curt": sched.w["curt"], "start_fc": sched.w["start_fc"],
                                "note": "占位权重（v9：2017 验证集选择后冻结），本专项不改权重"},
        "summary": summary,
        "windows": records,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # —— 控制台 ——
    print("=" * 72)
    print("第一关补充：FC 强制活跃专项（MPC 衔接真实检验，v9 §10）")
    print(f"时段: {start_date.date()} 起 {n_windows // 96} 天 | 初始 SOC={init_soc} H2={init_h2_frac}x")
    print(f"配置: BAT {cfg.bat_kwh:.0f} kWh | FC {cfg.pemfc_kw:.0f} kW | EL {cfg.pemel_kw:.0f} kW | H2 {cfg.h2_kg:.0f} kg")
    print(f"窗口数: {n_windows} | 总墙钟 {total_s:.0f}s | 中位 {np.median(solve_times):.3f}s/窗口")
    print("-" * 72)
    for k, v in checks.items():
        print(f"[{'PASS' if v else 'FAIL'}] {k}")
    print(f"[INFO] FC 启动 {n_start_fc} 次 / 停机 {n_stop_fc} 次 | 运行 {fc_run_steps} 步 ({fc_run_steps * 0.25:.0f}h)")
    print(f"[INFO] EL 启动 {n_start_el} 次 / 停机 {n_stop_el} 次 | 运行 {el_run_steps} 步 ({el_run_steps * 0.25:.0f}h)")
    print(f"[INFO] FC 段 min_on {min_on_fc} / min_off {min_off_fc} | EL 段 min_on {min_on_el} / min_off {min_off_el}（T={T_ON}）")
    print(f"[INFO] 守恒 SOC {max_soc_res:.3e} / H2 {max_h2_res:.3e} kg | L1 max {l1_max:.3f} kWh | 不可行 {n_infeasible}")
    print("-" * 72)
    print(("FC-ACTIVE PASS ✅（启停衔接约束真实检验通过）" if all_pass else "FC-ACTIVE FAIL ❌（见上方 FAIL 项）"))
    print(f"结果已保存：{OUT}")
    return all_pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=960,
                    help="窗口数上限（默认 960=10 天；2880=30 天；96=1 天快速）")
    ap.add_argument("--start-day", type=int, default=0,
                    help="2018 起始日偏移（默认 0=1 月 1 日冬季，FC 更活跃）")
    ap.add_argument("--init-soc", type=float, default=0.15,
                    help="初始 SOC（默认 0.15=低电量触发 FC 夜间启动）")
    ap.add_argument("--init-h2-frac", type=float, default=0.5,
                    help="初始储氢占比（默认 0.5）")
    args = ap.parse_args()
    ok = run_fc_active(args.limit, start_day=args.start_day,
                       init_soc=args.init_soc, init_h2_frac=args.init_h2_frac)
    sys.exit(0 if ok else 1)
