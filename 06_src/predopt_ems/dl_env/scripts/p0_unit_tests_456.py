# -*- coding: utf-8 -*-
"""
P0 六类数学单元测试 —— 第 4/5/6 类（v9 §16.1，挂 milp_scheduler）
================================================================
  4) 三级字典序不破坏：L1_final ≤ L1_opt+ε₁、L2_final ≤ L2_opt+ε₂（多窗口轨迹层面验证）
  5) open/closed 一致：D_feedback=0 时代理目标函数相同 → 轨迹一致（确定性 + 前两层无关 mode）
  6) MPC 跨窗口衔接：u 连续、最小开/停机剩余继承、窗口内新启动段满足 T_on/T_off、状态连续

运行环境：DL 环境（gurobipy 12.0.3 / 13.0.1）
命令（VSCode 终端）：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/p0_unit_tests_456.py
输出：控制台 PASS/FAIL + 同目录 p0_unit_tests_456_result.json
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)
sys.path.insert(0, _HERE)   # scripts/ 同目录：import milp_scheduler

from ems.ems_stage2b import Stage2BEMSConfig
from milp_scheduler import MilpScheduler, MPCState

OUT_JSON = Path(__file__).resolve().parent / ".." / "results" / "p0_unit_tests_456_result.json"

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
CFG = Stage2BEMSConfig(
    pv_kwp=388.2674203719944, bat_kwh=1734.3933384865522,
    pemfc_kw=95.02249870449305, pemel_kw=224.24405589699745,
    h2_kg=4450.2877332270145,
)
T_ON = T_OFF = CFG.minimum_run_steps          # 5 步
EPS1, EPS2 = 1e-3, 1e-3
TOL = 1e-5                                    # 求解器数值容差

results = {"script": "p0_unit_tests_456.py (v9 §16.1 第4/5/6类)",
           "timestamp": datetime.now().isoformat(),
           "checks": {}, "summary": {"pass": 0, "fail": 0}}


def report(no, name, ok, detail=""):
    results["checks"][no] = {"name": name, "pass": bool(ok), "detail": detail}
    results["summary"]["pass" if ok else "fail"] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] 测试{no} {name}" + (f" | {detail}" if detail else ""))
    return bool(ok)


def load_day2018(day_idx: int):
    """取 2018 年第 day_idx 天（0-based）96 步 PV/负荷，PV 归一到配置容量。"""
    df = pd.read_csv(CSV, usecols=["local_civil_year", "p_pv_available_reconstructed_nonnegative_kW",
                                   "p_building_load_reconstructed_kW"])
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    seg = d18.iloc[96 * day_idx:96 * (day_idx + 1)]
    pv_raw = seg["p_pv_available_reconstructed_nonnegative_kW"].to_numpy(dtype=float)
    load = seg["p_building_load_reconstructed_kW"].to_numpy(dtype=float)
    pv = pv_raw * (CFG.pv_kwp / max(np.max(pv_raw), 1e-6))
    return pv, load


def min_run_check(u: np.ndarray, t_req: int, skip_first: int = 0) -> bool:
    """检查 u 序列：每个 0→1 新启动段之后连续运行 ≥ t_req 步（跳过前 skip_first 步继承区）。"""
    n = len(u)
    i = skip_first
    while i < n:
        if u[i] == 0:
            i += 1
            continue
        # 找到段起点：u[i-1]==0 或 i==0（窗口内新启动）
        start = i
        while i < n and u[i] == 1:
            i += 1
        seg_len = i - start
        if start > 0 and u[start - 1] == 0:      # 窗口内 0→1 新启动
            if seg_len < t_req:
                return False
    return True


def main():
    print("=" * 70)
    print("P0 数学单元测试（第 4/5/6 类）— v9 §16.1（挂 milp_scheduler）")
    print(f"时间: {results['timestamp']}")
    print("=" * 70)

    sched = MilpScheduler(CFG, verbose=False)
    M_H2 = CFG.h2_kg

    # ============ 测试 4：三级字典序不破坏 ============
    ok4 = True
    details4 = []
    for label, day in (("冬 01-15", 14), ("夏 06-15", 166), ("秋 09-15", 257)):
        pv, load = load_day2018(day)
        st = MPCState(soc=0.5, h2_kg=CFG.h2_kg * 0.5)
        res = sched.solve_window(pv, load, st, mode="normal")
        l1_final = float(np.sum(res.commands[:, 5]) * CFG.dt_h)
        d_soc = abs(res.soc_traj[-1] - sched.soc_max)          # 默认 soc_ref=soc_max
        d_h2 = abs(res.h2_traj[-1] - sched.h2_max)             # 默认 h2_ref=h2_max
        l2_final = d_soc + d_h2 / M_H2
        ok1 = res.feasible and l1_final <= res.l1_opt + EPS1 + TOL
        ok2 = res.feasible and l2_final <= res.l2_opt + EPS2 + TOL
        ok4 &= ok1 and ok2
        details4.append(f"{label}: L1 {l1_final:.4f}≤{res.l1_opt:.4f}+ε₁ {ok1} | "
                        f"L2 {l2_final:.6f}≤{res.l2_opt:.6f}+ε₂ {ok2}")
    report("4", "三级字典序不破坏（L1_final≤L1_opt+ε₁、L2_final≤L2_opt+ε₂）", ok4,
           "；".join(details4))

    # ============ 测试 5：open/closed 一致（D_feedback=0）============
    pv, load = load_day2018(166)
    st = MPCState(soc=0.5, h2_kg=CFG.h2_kg * 0.5)
    r1 = sched.solve_window(pv, load, st, mode="da", degradation={"bat": 0, "fc": 0, "el": 0})
    r2 = sched.solve_window(pv, load, st, mode="da", degradation={"bat": 0, "fc": 0, "el": 0})
    max_diff = float(np.max(np.abs(r1.commands - r2.commands)))
    det_ok = r1.feasible and r2.feasible and max_diff <= 1e-8
    rn = sched.solve_window(pv, load, st, mode="normal")
    l12_ok = (abs(rn.l1_opt - r1.l1_opt) <= 1e-6 and abs(rn.l2_opt - r1.l2_opt) <= 1e-6)
    report("5", "open/closed 一致（D_feedback=0：确定性 + 前两层与 mode 无关）",
           det_ok and l12_ok,
           f"两次 da(D=0) 命令最大差 {max_diff:.2e} (≤1e-8)；normal 与 da L1/L2 一致={l12_ok}；"
           f"注：D_eval>0 时 open/closed 差异最终经 Gate A 的 f_E/f_η/f_P 接口验证")

    # ============ 测试 6：MPC 跨窗口衔接 ============
    pv1, load1 = load_day2018(166)
    pv2, load2 = load_day2018(167)
    w1 = sched.solve_window(pv1, load1, MPCState(soc=0.5, h2_kg=CFG.h2_kg * 0.5), mode="normal")
    if not w1.feasible:
        report("6", "MPC 跨窗口衔接", False, "窗口1 不可行")
    else:
        uf1, ue1 = int(w1.u_fc[-1]), int(w1.u_el[-1])
        # 窗口1 末尾连续运行/停机步数
        def trail(arr, val):
            c = 0
            for v in arr[::-1]:
                if v == val:
                    c += 1
                else:
                    break
            return c
        fc_run_end, el_run_end = trail(w1.u_fc, 1), trail(w1.u_el, 1)
        fc_off_end, el_off_end = trail(w1.u_fc, 0), trail(w1.u_el, 0)
        st2 = MPCState(
            soc=float(w1.soc_traj[-1]), h2_kg=float(w1.h2_traj[-1]),
            u_fc=uf1, u_el=ue1,
            fc_min_on_remaining=max(0, T_ON - fc_run_end) if uf1 else 0,
            el_min_on_remaining=max(0, T_ON - el_run_end) if ue1 else 0,
            fc_min_off_remaining=max(0, T_OFF - fc_off_end) if not uf1 else 0,
            el_min_off_remaining=max(0, T_OFF - el_off_end) if not ue1 else 0,
            p_fc_prev=float(w1.commands[-1, 2]), p_el_prev=float(w1.commands[-1, 3]),
        )
        w2 = sched.solve_window(pv2, load2, st2, mode="normal")
        u_cont = (w2.feasible and w2.u_fc[0] == uf1 and w2.u_el[0] == ue1)
        state_cont = (abs(w2.soc_traj[0] - w1.soc_traj[-1]) <= 1e-9
                      and abs(w2.h2_traj[0] - w1.h2_traj[-1]) <= 1e-9)
        # 继承强制区验证
        force_ok = True
        if uf1 and st2.fc_min_on_remaining > 0:
            force_ok &= bool(np.all(w2.u_fc[:st2.fc_min_on_remaining] == 1))
        if ue1 and st2.el_min_on_remaining > 0:
            force_ok &= bool(np.all(w2.u_el[:st2.el_min_on_remaining] == 1))
        # 窗口2 内新启动段最小运行（跳过继承强制区）
        skip = max(st2.fc_min_on_remaining, st2.el_min_on_remaining)
        run_ok = min_run_check(w2.u_fc, T_ON, skip) and min_run_check(w2.u_el, T_ON, skip)
        # 【2026-08-08 语义更新】t=0 可自由启停（差分基准继承，不再强制 u[0]==state）：
        # u_cont 仅作报告；核心断言 = 状态连续 + 继承强制区 + 新启动段 ≥ T_on。
        ok6 = w2.feasible and state_cont and force_ok and run_ok
        report("6", "MPC 跨窗口衔接（t=0 自由启停/剩余开停机继承/新启动段≥T_on/状态连续）", ok6,
               f"u_fc {uf1}→{w2.u_fc[0]}、u_el {ue1}→{w2.u_el[0]}（u_cont={u_cont}，继承强制区外可自由启停）；"
               f"状态连续={state_cont}；继承强制={force_ok}；新启动段 min-run={run_ok}；"
               f"FC 窗口1末连运行 {fc_run_end} 步 → 窗口2继承 {st2.fc_min_on_remaining} 步")

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    s = results["summary"]
    print("-" * 70)
    print(f"汇总：{s['pass']} PASS / {s['fail']} FAIL"
          + ("  → 4/5/6 类全部通过 ✅（六类测试完成，可进入 Gate A / 第一关）"
             if s["fail"] == 0 else "  → 存在失败，见上方明细"))
    print(f"结果已保存：{OUT_JSON}")
    sys.exit(0 if s["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
