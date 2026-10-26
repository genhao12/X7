# -*- coding: utf-8 -*-
"""
run_gate3_2018.py — 第三关：5 配置 × 2018（3 组全量方法，v9 §10/§12/§4.2）

任务：5 个代表配置 × 2018 全年，3 组全量方法（v9 §4.2 口径）：
  M1 = B* 预测 + 普通 MILP（open，D_feedback=0）
  M4 = LSTM 预测 + 退化感知 MILP（da，D_feedback=D_eval，closed）
  M5 = perfect + 退化感知 MILP（da，D_feedback=D_eval，closed）
每方法滚动 35,040 窗口（MPC 语义），输出 5 指标直比。

5 指标（v9 意见 7/§12，策略优劣唯一判决依据）：
  ① BAT 精算损伤（rainflow EFC，compute_damage）
  ② FC 精算损伤（运行+启停）
  ③ EL 相对损伤指数
  ④ LPSP（ΣP_uns·Δt/Σload，准入<1%）
  ⑤ LCOE（20 年，economic_parameters_v1.0.json，round16 口径）
TE_BAT/TE_FC/TE_EL 机理解释单独报告（open/closed 配对，不作优劣判据）。

退化反馈（Gate A ① 接线，milp_scheduler.degradation 参数）：
  open：degradation=None（BOL）
  closed：degradation={'bat':D_bat,'fc':D_fc,'el':D_el}，D 按周精算更新（§8 退化更新循环）

预测层冻结规则（2026-08-09）：B*/LSTM 预测文件一次性缓存复用；
本脚本只读 lstm_env/results/forecasts/，不重新预测。

运行环境：DL 环境（D:/Anaconda/envs/DL，gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate3_2018.py                    # 全量 5 配置×3 方法（约 44h 串行）
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate3_2018.py --limit 960        # 10 天切片验证
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate3_2018.py --configs compact --methods m1,m4
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate3_2018.py --skip-econ        # 跳过经济（快）
"""

import sys, os, json, time, argparse
import numpy as np
import pandas as pd

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)
sys.path.insert(0, _HERE)

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import scale_pv_available, PVInputContract
from milp_scheduler import MilpScheduler, MPCState
from run_gate1_30day import load_compact, CSV, WINDOW
from simulation.closed_loop_lite import compute_damage, CoalitionState

RES_DIR = os.path.join(_HERE, "..", "results")
FC_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "lstm_env", "results", "forecasts"))
OUT = os.path.join(RES_DIR, "gate3_2018_result.json")

WINDOW = 96
DT_H = 0.25
BLOCK = 672                # 周块（退化更新周期）
N_2018 = 35040
D_UPDATE_WEEK = 1          # D 每周精算更新一次（§8 退化更新循环：日/周）

FC = {
    "bstar": {"pv": "Bstar_PV_forecast_2018.csv", "load": "Bstar_Load_forecast_2018.csv"},
    "lstm": {"pv": "PV_forecast_2018.csv", "load": "Load_forecast_2018.csv"},
}
ADM = r"D:\matlab\C\07_results\rectification_v2_stage2c\search\verified_admissions.json"
ECON_JSON = r"D:\matlab\C\05_config\economic_parameters_v1.0.json"
C1_JSON = r"D:\matlab\C\05_config\mvp_c1_config.json"

METHODS = {
    "m1": {"forecast": "bstar", "mode": "normal", "closed": False},
    "m4": {"forecast": "lstm", "mode": "da", "closed": True},
    "m5": {"forecast": "perfect", "mode": "da", "closed": True},
}


def load_all_configs():
    adm = json.load(open(ADM, encoding="utf-8"))
    reps = adm["representatives"]
    out = {}
    for r in reps:
        role = r["representative_role"]
        out[role] = Stage2BEMSConfig(
            pv_kwp=float(r["pv_kwp"]), bat_kwh=float(r["bat_kwh"]),
            pemfc_kw=float(r["pemfc_kw"]), pemel_kw=float(r["pemel_kw"]),
            h2_kg=float(r["h2_kg"]),
        )
    return out


def load_2018_data(cfg):
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    pv_raw = d18["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    pv = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)
    return pv, load


def load_forecast_file(fname):
    df = pd.read_csv(os.path.join(FC_DIR, fname))
    cols = [c for c in df.columns if c.startswith("pred_h")]
    return df[cols].to_numpy(dtype=float)


REF_KW = float(PVInputContract().reference_kw)   # 233（输入 CSV 参考容量）


def load_pv_pred(fname, cfg):
    """PV 预测文件（原始 reference=233 口径）统一缩放到 cfg.pv_kwp 口径。
    【修复 2026-08-12】同第二关：此前直接读原始预测，M1/M4 与 M5(perfect)
    的 PV 量级不一致（差 40%），对比被 PV 容量主导而非预测/方法质量。"""
    pred = load_forecast_file(fname)
    scaled = pred * (cfg.pv_kwp / REF_KW)
    return np.minimum(scaled, cfg.pv_kwp)


def run_method(cfg, pv, load, method, limit=None, skip_econ=False, verbose=True):
    """单配置单方法全年滚动 MILP（含退化周更新闭环）。返回轨迹 + 指标。"""
    n = N_2018 if limit is None else int(limit)
    f_src = METHODS[method]["forecast"]
    mode = METHODS[method]["mode"]
    closed = METHODS[method]["closed"]

    if f_src == "perfect":
        pv_pred = None; load_pred = None
    else:
        pv_pred = load_pv_pred(FC[f_src]["pv"], cfg)
        load_pred = load_forecast_file(FC[f_src]["load"])

    sched = MilpScheduler(cfg, verbose=False)
    state = MPCState(soc=0.50, h2_kg=cfg.h2_kg * 0.5)
    D = {"bat": 0.0, "fc": 0.0, "el": 0.0}
    deg_arg = None if not closed else dict(D)
    prev_u_fc = prev_u_el = 0
    run_fc = off_fc = run_el = off_el = 0
    cols = {"p_ch": [], "p_dis": [], "p_fc": [], "p_el": [], "p_curt": [], "p_uns": [],
            "u_fc": [], "u_el": [], "soc": [], "h2": []}
    ems_all = []          # 每窗口执行的 (P_bat, P_fc, P_el) 用于周精算
    l1_tot = l2_tot = l3_tot = 0.0
    n_opt = n_infeas = 0
    solve_times = []       # 每窗口求解耗时（诊断，2026-08-16 补）
    gaps = []              # 每窗口 L3 MIPGap（TIME_LIMIT 披露）
    n_tl = 0               # L3 TIME_LIMIT 窗口数
    tl_idx = []
    infeas_idx = []        # 不可行窗口索引
    t0 = time.time()

    for i in range(n):
        if f_src == "perfect":
            pv_w = pv[i:i + WINDOW]; load_w = load[i:i + WINDOW]
        else:
            pv_w = pv_pred[i]; load_w = load_pred[i]
        res = sched.solve_window(pv_w, load_w, state, mode=mode, degradation=deg_arg)
        solve_times.append(res.solve_time_s)
        gaps.append(res.mip_gap_l3)
        if res.status_l3 == 9:          # GRB.TIME_LIMIT
            n_tl += 1
            tl_idx.append(i)
        if not res.feasible:
            n_infeas += 1
            infeas_idx.append(i)
            continue
        n_opt += 1
        state.soc = float(res.soc_traj[1]); state.h2_kg = float(res.h2_traj[1])
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
        ems_all.append([res.commands[0, 1] - res.commands[0, 0], res.commands[0, 2], res.commands[0, 3]])
        cols["p_ch"].append(float(res.commands[0, 0]))
        cols["p_dis"].append(float(res.commands[0, 1]))
        cols["p_fc"].append(float(res.commands[0, 2]))
        cols["p_el"].append(float(res.commands[0, 3]))
        cols["p_curt"].append(float(res.commands[0, 4]))
        cols["p_uns"].append(float(res.commands[0, 5]))
        cols["u_fc"].append(state.u_fc); cols["u_el"].append(state.u_el)
        cols["soc"].append(state.soc); cols["h2"].append(state.h2_kg)
        l1_tot += float(res.l1_opt); l2_tot += float(res.l2_opt); l3_tot += float(res.l3_opt)

        # 退化周更新（§8：closed 时按周精算累计 D）
        if closed and (i + 1) % BLOCK == 0 and i + 1 < n:
            cmds = np.array(ems_all[-BLOCK:])
            d = compute_damage(
                lambda Dd: (max(0.80, 1 - 0.20 * Dd), None, min(cfg.bat_kwh * 0.5, cfg.bat_kwh * 0.5)),
                cmds, _cfg_dict(cfg), dt_min=15.0, D_state=D,
                coalition=CoalitionState(True, True, True, True))
            for k in ("bat", "fc", "el"):
                D[k] = float(np.clip(D[k] + d[f"dD_{k}"].sum(), 0.0, 1.0))
            deg_arg = dict(D)

        if (i + 1) % 5000 == 0 and verbose:
            print(f"    [{method}] {i+1}/{n} | D={D['bat']:.3f}/{D['fc']:.3f}/{D['el']:.3f} | {time.time()-t0:.0f}s")

    traj = {k: np.array(v) for k, v in cols.items()}
    lpsp = float(np.sum(traj["p_uns"]) * DT_H / max(np.sum(load[:len(traj["p_uns"])]) * DT_H, 1e-9))
    exact = exact_damage_all(traj, cfg)

    econ = None
    t_econ = 0.0
    if not skip_econ:
        t_e0 = time.time()
        econ = compute_lcoe(cfg, traj, lpsp)
        t_econ = time.time() - t_e0          # 经济计算耗时（诊断 compute_lcoe 是否慢）

    st = np.array(solve_times) if solve_times else np.array([0.0])
    gs = np.array(gaps) if gaps else np.array([0.0])
    if verbose:
        print(f"  [{method}] 完成 {n_opt} | 不可行 {n_infeas} | L3超时 {n_tl} | "
              f"D_end={D['bat']:.3f}/{D['fc']:.3f}/{D['el']:.3f} | LPSP={lpsp*100:.4f}% | "
              f"求解中位 {np.median(st):.1f}s p95 {np.percentile(st,95):.1f}s max {st.max():.1f}s | "
              f"gap max {gs.max():.3f} | 经济 {t_econ:.0f}s | 耗时 {time.time()-t0:.0f}s")
    return {
        "method": method, "forecast": f_src, "mode": mode, "closed": closed,
        "n_opt": n_opt, "n_infeas": n_infeas, "n_tl": n_tl,
        "infeas_idx": infeas_idx, "tl_idx": tl_idx,
        "solve_time_p50_s": float(np.median(st)), "solve_time_p95_s": float(np.percentile(st, 95)),
        "solve_time_max_s": float(st.max()), "mip_gap_max": float(gs.max()),
        "econ_time_s": t_econ,
        "D_end": D, "lpsp": lpsp,
        "exact_damage": exact,
        "lcoe_cny_per_kwh": (econ["lcoe_cny_per_kwh"] if econ else None),
        "npc_million_cny": (econ["npc_million_cny"] if econ else None),
        "l1_kwh": l1_tot, "l2": l2_tot, "l3": l3_tot,
        "fc_run_steps": int(traj["u_fc"].sum()), "el_run_steps": int(traj["u_el"].sum()),
        "soc_range": [float(traj["soc"].min()), float(traj["soc"].max())],
        "h2_start": float(traj["h2"][0]), "h2_end": float(traj["h2"][-1]),
    }


def _cfg_dict(cfg):
    return {
        "capacity": {"BAT_kWh": cfg.bat_kwh, "PEMFC_kW": cfg.pemfc_kw, "PEMEL_kW": cfg.pemel_kw},
        "battery_constraints": {"soc_min": cfg.soc_min, "soc_max": cfg.soc_max, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.275},
    }


def exact_damage_all(traj, cfg):
    """全年精算损伤（compute_damage，BOL D=0 精算口径，v9 §14 唯一判决依据）。"""
    p_bat = traj["p_dis"] - traj["p_ch"]
    cmds = np.stack([p_bat, traj["p_fc"], traj["p_el"]], axis=1)
    d = compute_damage(
        lambda Dd: (max(0.80, 1 - 0.20 * Dd), None, min(cfg.bat_kwh * 0.5, cfg.bat_kwh * 0.5)),
        cmds, _cfg_dict(cfg), dt_min=15.0, D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
        coalition=CoalitionState(True, True, True, True))
    return {"bat_D": float(d["dD_bat"].sum()),
            "fc_D": float(d["dD_fc"].sum()),
            "el_D": float(d["dD_el"].sum())}


def exact_damage_increments(traj, cfg):
    """全年逐点精算损伤增量（dD_bat/dD_fc/dD_el 数组，35040 点），供寿命/经济链路。"""
    p_bat = traj["p_dis"] - traj["p_ch"]
    cmds = np.stack([p_bat, traj["p_fc"], traj["p_el"]], axis=1)
    d = compute_damage(
        lambda Dd: (max(0.80, 1 - 0.20 * Dd), None, min(cfg.bat_kwh * 0.5, cfg.bat_kwh * 0.5)),
        cmds, _cfg_dict(cfg), dt_min=15.0, D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
        coalition=CoalitionState(True, True, True, True))
    return {"bat": d["dD_bat"], "fc": d["dD_fc"], "el": d["dD_el"]}


def compute_lcoe(cfg, traj, lpsp):
    """第三关正式 LCOE（v2 事件驱动，2026-08-09 用户口径；2026-08-12 接入 run_gate3）。

    用户逻辑链：全年轨迹 compute_damage 逐点损伤增量 → 年均损伤（2018 工况代表，
    正式三年链式在第四关）→ 事件驱动寿命（eol_events，与 Stage3 degradation_summary
    同口径，含 downtime）→ 替换年份 → compute_npc_timed 折现 → NPC → LCOE。
    BAT/FC 按事件驱动模型内部 EOL 阈值推寿命；EL 无绝对寿命标定默认不触发替换。
    """
    from gate3_econ_lite import build_economic_config, DOWNTIME_STEPS
    from eol_events import advance_replacement_events_with_downtime
    from npc_lite import compute_npc_timed

    inc = exact_damage_increments(traj, cfg)
    # 用户口径：EL 不喂入 → 不触发替换（相对指数仅作敏感性上界情景）
    feed = {k: inc[k] for k in ("bat", "fc")}
    # 2018 全年增量 → 20 年 horizon（2018 工况代表年均；第四关三年链式替换此处）
    n_cycle = len(next(iter(inc.values())))
    n_horizon = int(20 * 8760 / 0.25)
    repeats = int(np.ceil(n_horizon / n_cycle))
    feed = {k: np.tile(v, repeats)[:n_horizon] for k, v in feed.items()}

    res = advance_replacement_events_with_downtime(
        feed, initial_state={k: 0.0 for k in feed},
        dt_hours=0.25, downtime_steps={k: DOWNTIME_STEPS[k] for k in feed},
        start_time_hours=0.0, initial_replacement_counts={k: 0 for k in feed})

    DEV_MAP = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}
    repl_schedule = {}
    for ev in res.events:
        dev = DEV_MAP.get(ev.device, ev.device)
        repl_schedule.setdefault(dev, []).append(ev.time_hours / 8760.0)

    ecfg = build_economic_config(cfg)
    annual_mwh = load_annual_kwh(cfg) / 1e3
    econ_res = compute_npc_timed(ecfg, repl_schedule, annual_mwh, label="gate3")
    return {"lcoe_cny_per_kwh": econ_res["lcoe_cny_per_kwh"],
            "npc_million_cny": econ_res["npc_million_cny"],
            "replacement_schedule": {k: [round(x, 2) for x in v]
                                     for k, v in repl_schedule.items()}}


def load_annual_kwh(cfg):
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    return np.sum(load) * DT_H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--configs", type=str, default="compact,balanced,hydrogen_dominant,battery_dominant,critical")
    ap.add_argument("--methods", type=str, default="m1,m4,m5")
    ap.add_argument("--skip-econ", action="store_true")
    ap.add_argument("--resume", action="store_true", help="续跑：跳过已有 gate3_partial_*.json 的配置")
    args = ap.parse_args()

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    for m in methods:
        assert m in METHODS, f"未知方法 {m}"
    n = N_2018 if args.limit is None else int(args.limit)

    # 逐配置落盘（2026-08-12，第二关中断教训）：每配置 3 方法完成即写
    # gate3_partial_{cfg}.json；--resume 跳过已有 partial 的配置（全量 44h 防中断全丢）
    def partial_path(cfg):
        return os.path.join(RES_DIR, f"gate3_partial_{cfg}.json")

    all_cfg = load_all_configs()
    t0 = time.time()
    print("=" * 72)
    print(f"第三关：{len(configs)} 配置 × {methods} × 2018（{n} 窗口）")
    print("5 指标：BAT/FC/EL 精算损伤 + LPSP + LCOE；TE 机理解释（open/closed 配对）")
    print("=" * 72)

    out = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "params": {"n": n, "configs": configs,
                                                                        "methods": methods},
           "results": {}}
    for cfg_name in configs:
        if args.resume and os.path.exists(partial_path(cfg_name)):
            out["results"][cfg_name] = json.load(open(partial_path(cfg_name), encoding="utf-8"))
            print(f"  [{cfg_name}] 已有 partial 结果，跳过（--resume）")
            continue
        cfg = all_cfg[cfg_name]
        pv, load = load_2018_data(cfg)
        out["results"][cfg_name] = {}
        for m in methods:
            print(f"\n[{cfg_name}][{m}] 开始...")
            r = run_method(cfg, pv, load, m, limit=args.limit, skip_econ=args.skip_econ)
            out["results"][cfg_name][m] = r
            print(f"  [{cfg_name}][{m}] LPSP={r['lpsp']*100:.4f}% | "
                  f"损伤 BAT={r['exact_damage']['bat_D']:.4f} FC={r['exact_damage']['fc_D']:.4f} "
                  f"EL={r['exact_damage']['el_D']:.4f} | LCOE={r['lcoe_cny_per_kwh']}")
        print(f"  [{cfg_name}] 3 方法完成，累计 {time.time()-t0:.0f}s")
        with open(partial_path(cfg_name), "w", encoding="utf-8") as f:
            json.dump(out["results"][cfg_name], f, ensure_ascii=False, indent=1, default=str)
        print(f"  [{cfg_name}] 已逐配置落盘: {partial_path(cfg_name)}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n结果已保存：{OUT}（总耗时 {time.time()-t0:.0f}s）")


if __name__ == "__main__":
    main()
