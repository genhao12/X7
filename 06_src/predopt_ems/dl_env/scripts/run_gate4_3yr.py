# -*- coding: utf-8 -*-
"""
run_gate4_3yr.py — 第四关：3 年链式（2016-2018）+ TE/NPC/LCOE/不确定性（DL 环境，2026-08-16）

任务（v9 §12）：三年连续滚动 MILP，检验退化反馈的**长时程价值**——
第三关单年 D 仅累积 ~0.16（容量缩 ~3%，反馈信号弱），三年链式 D 累积 0.5+ 后
容量/效率退化显著，退化感知（closed）与不感知（open）的差异才可能显现。

方法（§4.2 全量 3 组 + TE 配对补充）：
  M1  = B* 预测 + normal（open，D_feedback=0）
  M1c = B* 预测 + da（closed，D_feedback=D_eval）——**TE 配对源**（与 M1 同预测/同权重/仅退化反馈不同）
  M4  = LSTM + da（closed）
  M5  = perfect + da（closed）
TE_BAT/TE_FC/TE_EL = 三年损伤(M1c) − 三年损伤(M1)（同源配对，机理解释，不作优劣判据 §12）。

关键实现（与 run_gate3 的差异）：
  ① 3 年连续滚动（2016 闰年 35136 + 2017 35040 + 2018 35040 = 105216 窗口）
  ② 状态（soc/H2/u_fc/u_el/min_on/off/ramp）与 D 跨年连续继承，不重置
  ③ 退化周更新（672 步）跨年连续
  ④ 预测输入按年切换三套文件（第 0 步 gen_forecasts_2016_2017.py 产出）
  ⑤ 2016 首日 96 窗口预测为 perfect 兜底（预测文件已处理，此处透明）

指标：
  三年链式精算损伤（BAT/FC/EL，BOL compute_damage）+ 逐年 LPSP + D_end（三年末）
  + 正式 LCOE（三年损伤增量 → 20 年 tiling → eol_events 寿命 → compute_npc_timed，
    BAT/FC 按 EOL 推寿命、EL 默认不触发替换，用户口径）
  + TE（M1c vs M1 三年损伤差）
  + 周块 Bootstrap 95%CI（672 点周块重采样，给定轨迹的条件变异，§13）

降载：默认 compact × 4 方法（M1/M1c/M4/M5）；--configs 可加 hydrogen_dominant。
计算量：compact 单方法 3 年 ≈ 第三关单年的 3 倍（M1 ~27h / M1c ~27h / M5 ~21h / M4 ~70h，
合计约 145h 串行）；M1c 与 M1 同源同难度。

运行环境：DL 环境
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate4_3yr.py                    # 默认 compact × 4 方法
  ... --configs compact,hydrogen_dominant
  ... --methods m1,m4                  # 选方法
  ... --limit 960                      # 快速切片（前 10 天）
  ... --no-bootstrap                   # 跳过 Bootstrap CI（快）
  ... --resume                         # 续跑（跳过已有 partial 的方法）
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
from run_gate1_30day import CSV
from run_gate3_2018 import (load_all_configs, exact_damage_all, _cfg_dict,
                            exact_damage_increments)
from simulation.closed_loop_lite import compute_damage, CoalitionState

RES_DIR = os.path.join(_HERE, "..", "results")
FC_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "lstm_env", "results", "forecasts"))
OUT = os.path.join(RES_DIR, "gate4_3yr_result.json")

DT_H = 0.25
BLOCK = 672
YEARS = [("2016", 35136), ("2017", 35040), ("2018", 35040)]
N_TOTAL = 105216
REF_KW = float(PVInputContract().reference_kw)

METHODS = {
    "m1":  {"forecast": "bstar", "mode": "normal", "closed": False},
    "m1c": {"forecast": "bstar", "mode": "da", "closed": True},
    "m4":  {"forecast": "lstm", "mode": "da", "closed": True},
    "m5":  {"forecast": "perfect", "mode": "da", "closed": True},
}
FC = {
    "bstar": {"pv": "Bstar_PV_forecast_{y}.csv", "load": "Bstar_Load_forecast_{y}.csv"},
    "lstm": {"pv": "PV_forecast_{y}.csv", "load": "Load_forecast_{y}.csv"},
}


def load_pv_pred(fname, cfg, n_cols=96):
    df = pd.read_csv(os.path.join(FC_DIR, fname))
    cols = [c for c in df.columns if c.startswith("pred_h")]
    assert len(cols) == n_cols, f"{fname} 预测列数 {len(cols)} != {n_cols}（防错配）"
    m = df[cols].to_numpy(dtype=float)
    return np.minimum(m * (cfg.pv_kwp / REF_KW), cfg.pv_kwp)


def load_pred(fname):
    df = pd.read_csv(os.path.join(FC_DIR, fname))
    cols = [c for c in df.columns if c.startswith("pred_h")]
    return df[cols].to_numpy(dtype=float)


def load_3yr_data(cfg):
    """三年实际 PV（缩放）/load 分段 + 预测矩阵（按年、按方法）。"""
    df = pd.read_csv(CSV)
    pv_all = df["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load_all_ = df["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    # 分段（0:35136=2016, 35136:70176=2017, 70176:=2018）
    pv_act, ld_act = {}, {}
    for name, n, start in (("2016", 35136, 0), ("2017", 35040, 35136), ("2018", 35040, 70176)):
        pv_act[name] = scale_pv_available(pv_all[start:start + n], nominal_kw=cfg.pv_kwp)
        ld_act[name] = load_all_[start:start + n]
    return pv_act, ld_act


def load_preds(cfg, f_src):
    """按年读取预测矩阵：{'2016': (35136,96), '2017': (35040,96), '2018': (35040,96)}。"""
    out = {}
    for name, n in YEARS:
        out[name] = {"pv": load_pv_pred(FC[f_src]["pv"].format(y=name), cfg),
                     "load": load_pred(FC[f_src]["load"].format(y=name))}
    return out


def run_method_3yr(cfg, pv_act, ld_act, method, limit=None, skip_econ=False, bootstrap=True, verbose=True):
    f_src = METHODS[method]["forecast"]
    mode = METHODS[method]["mode"]
    closed = METHODS[method]["closed"]
    n = N_TOTAL if limit is None else int(limit)

    preds = None if f_src == "perfect" else load_preds(cfg, f_src)
    # 年起点全局索引
    y_off = {"2016": 0, "2017": 35136, "2018": 70176}

    sched = MilpScheduler(cfg, verbose=False)
    state = MPCState(soc=0.50, h2_kg=cfg.h2_kg * 0.5)     # 链式起点（2016-01-01）
    D = {"bat": 0.0, "fc": 0.0, "el": 0.0}
    deg_arg = None if not closed else dict(D)
    prev_u_fc = prev_u_el = 0
    run_fc = off_fc = run_el = off_el = 0
    cols = {k: [] for k in ("p_ch", "p_dis", "p_fc", "p_el", "p_curt", "p_uns",
                            "u_fc", "u_el", "soc", "h2")}
    ems_all = []
    l1_tot = l2_tot = l3_tot = 0.0
    n_opt = n_infeas = n_tl = 0
    solve_times, gaps, tl_idx, infeas_idx = [], [], [], []
    exec_idx = []               # 执行窗口的全局索引（逐年 LPSP 归集口径，2026-08-18）
    t0 = time.time()

    for i in range(n):
        # 定位年份与年内偏移
        if i < 35136:
            y, w = "2016", i
        elif i < 70176:
            y, w = "2017", i - 35136
        else:
            y, w = "2018", i - 70176
        if f_src == "perfect":
            pv_w = pv_act[y][w:w + 96]
            ld_w = ld_act[y][w:w + 96]
        else:
            pv_w = preds[y]["pv"][w]
            ld_w = preds[y]["load"][w]
        res = sched.solve_window(pv_w, ld_w, state, mode=mode, degradation=deg_arg)
        solve_times.append(res.solve_time_s)
        gaps.append(res.mip_gap_l3)
        if res.status_l3 == 9:
            n_tl += 1
            tl_idx.append(i)
        if not res.feasible:
            n_infeas += 1
            infeas_idx.append(i)
            continue
        n_opt += 1
        exec_idx.append(i)
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
        cols["p_ch"].append(float(res.commands[0, 0])); cols["p_dis"].append(float(res.commands[0, 1]))
        cols["p_fc"].append(float(res.commands[0, 2])); cols["p_el"].append(float(res.commands[0, 3]))
        cols["p_curt"].append(float(res.commands[0, 4])); cols["p_uns"].append(float(res.commands[0, 5]))
        cols["u_fc"].append(state.u_fc); cols["u_el"].append(state.u_el)
        cols["soc"].append(state.soc); cols["h2"].append(state.h2_kg)
        l1_tot += float(res.l1_opt); l2_tot += float(res.l2_opt); l3_tot += float(res.l3_opt)

        # 退化周更新（§8：closed，672 步，跨年连续不重置）
        if closed and (i + 1) % BLOCK == 0 and i + 1 < n:
            cmds = np.array(ems_all[-BLOCK:])
            d = compute_damage(
                lambda Dd: (max(0.80, 1 - 0.20 * Dd), None, min(cfg.bat_kwh * 0.5, cfg.bat_kwh * 0.5)),
                cmds, _cfg_dict(cfg), dt_min=15.0, D_state=D,
                coalition=CoalitionState(True, True, True, True))
            for k in ("bat", "fc", "el"):
                D[k] = float(np.clip(D[k] + d[f"dD_{k}"].sum(), 0.0, 1.0))
            deg_arg = dict(D)

        if (i + 1) % 10000 == 0 and verbose:
            print(f"    [{method}] {i+1}/{n} | D={D['bat']:.3f}/{D['fc']:.3f}/{D['el']:.3f} | {time.time()-t0:.0f}s")

    traj = {k: np.array(v) for k, v in cols.items()}
    n_opt = len(traj["p_uns"])
    # 三年逐年 LPSP + 总 LPSP（分母=该年实际负荷）
    lpsp_total = float(np.sum(traj["p_uns"]) * DT_H / max(np.sum(np.concatenate([ld_act[y] for y, _ in YEARS])) * DT_H, 1e-9))
    lpsp_yearly = {}
    offs = [("2016", 0, 35136), ("2017", 35136, 35040), ("2018", 70176, 35040)]
    ei = np.array(exec_idx, dtype=int)
    for y, s0, n_y in offs:
        mask = (ei >= s0) & (ei < s0 + n_y)
        seg = traj["p_uns"][mask]
        denom = ld_act[y].sum() * DT_H
        lpsp_yearly[y] = float(seg.sum() * DT_H / max(denom, 1e-9))
    exact = exact_damage_all(traj, cfg)          # 三年链式总损伤（BOL 精算）

    econ = None
    t_econ = 0.0
    if not skip_econ:
        t_e0 = time.time()
        inc = exact_damage_increments(traj, cfg)  # 三年逐点增量 → 20 年 tiling（用户逻辑链）
        econ = lcoe_3yr(cfg, inc)
        t_econ = time.time() - t_e0

    ci = None
    if bootstrap:
        ci = bootstrap_ci(traj, cfg)

    st = np.array(solve_times) if solve_times else np.array([0.0])
    gs = np.array(gaps) if gaps else np.array([0.0])
    if verbose:
        print(f"  [{method}] 完成 {n_opt} | 不可行 {n_infeas} | L3超时 {n_tl} | "
              f"D_end={D['bat']:.3f}/{D['fc']:.3f}/{D['el']:.3f} | 总LPSP={lpsp_total*100:.4f}% | "
              f"求解中位 {np.median(st):.1f}s p95 {np.percentile(st,95):.1f}s max {st.max():.1f}s | "
              f"gap max {gs.max():.3f} | 经济 {t_econ:.0f}s | 耗时 {time.time()-t0:.0f}s")
    return {
        "method": method, "forecast": f_src, "mode": mode, "closed": closed,
        "n_opt": n_opt, "n_infeas": n_infeas, "n_tl": n_tl,
        "infeas_idx": infeas_idx, "tl_idx": tl_idx,
        "solve_time_p50_s": float(np.median(st)), "solve_time_p95_s": float(np.percentile(st, 95)),
        "solve_time_max_s": float(st.max()), "mip_gap_max": float(gs.max()), "econ_time_s": t_econ,
        "D_end": D, "lpsp_total": lpsp_total, "lpsp_yearly": lpsp_yearly,
        "exact_damage_3yr": exact,
        "lcoe_cny_per_kwh": (econ["lcoe_cny_per_kwh"] if econ else None),
        "npc_million_cny": (econ["npc_million_cny"] if econ else None),
        "replacement_schedule": (econ["replacement_schedule"] if econ else None),
        "bootstrap_ci": ci,
        "fc_run_steps": int(traj["u_fc"].sum()), "el_run_steps": int(traj["u_el"].sum()),
        "soc_range": [float(traj["soc"].min()), float(traj["soc"].max())],
    }


def lcoe_3yr(cfg, inc):
    """正式 LCOE：三年损伤增量 → 20 年 tiling → eol_events → compute_npc_timed（用户口径）。"""
    from gate3_econ_lite import build_economic_config, DOWNTIME_STEPS
    from eol_events import advance_replacement_events_with_downtime
    from npc_lite import compute_npc_timed
    from run_gate3_2018 import load_annual_kwh

    feed = {k: inc[k] for k in ("bat", "fc")}                     # EL 默认剔除
    n_cyc = len(next(iter(inc.values())))
    n_hor = int(20 * 8760 / DT_H)
    feed = {k: np.tile(v, int(np.ceil(n_hor / n_cyc)))[:n_hor] for k, v in feed.items()}
    res = advance_replacement_events_with_downtime(
        feed, initial_state={k: 0.0 for k in feed},
        dt_hours=DT_H, downtime_steps={k: DOWNTIME_STEPS[k] for k in feed},
        start_time_hours=0.0, initial_replacement_counts={k: 0 for k in feed})
    DEV_MAP = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}
    repl = {}
    for ev in res.events:
        dev = DEV_MAP.get(ev.device, ev.device)
        repl.setdefault(dev, []).append(ev.time_hours / 8760.0)
    ecfg = build_economic_config(cfg)
    annual_mwh = load_annual_kwh(cfg) / 1e3
    econ = compute_npc_timed(ecfg, repl, annual_mwh, label="gate4")
    return {"lcoe_cny_per_kwh": econ["lcoe_cny_per_kwh"],
            "npc_million_cny": econ["npc_million_cny"],
            "replacement_schedule": {k: [round(x, 2) for x in v] for k, v in repl.items()}}


def bootstrap_ci(traj, cfg, n_rep=200, seed=20260816):
    """周块 Bootstrap 95%CI（§13：672 点周块，给定轨迹的条件时间变异）。
    三年轨迹取前 156 个整周块（104832 点，尾部 384 点≈4 天忽略，披露），
    有放回抽 156 块求和 → n_rep 次 → 2.5/97.5 分位数。"""
    rng = np.random.default_rng(seed)
    p_bat = traj["p_dis"] - traj["p_ch"]
    cmds = np.stack([p_bat, traj["p_fc"], traj["p_el"]], axis=1)
    d = compute_damage(
        lambda Dd: (max(0.80, 1 - 0.20 * Dd), None, min(cfg.bat_kwh * 0.5, cfg.bat_kwh * 0.5)),
        cmds, _cfg_dict(cfg), dt_min=15.0, D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
        coalition=CoalitionState(True, True, True, True))
    n_blk = len(traj["p_uns"]) // BLOCK
    out = {}
    for dev in ("bat", "fc", "el"):
        arr = d[f"dD_{dev}"][:n_blk * BLOCK].reshape(n_blk, BLOCK).sum(axis=1)
        samples = np.array([arr[rng.integers(0, n_blk, n_blk)].sum() for _ in range(n_rep)])
        out[dev] = {"median": float(np.median(samples)),
                    "ci95": [float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))],
                    "n_blocks": n_blk, "note": "672 点周块条件变异，尾部 384 点忽略"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", type=str, default="compact")
    ap.add_argument("--methods", type=str, default="m1,m1c,m4,m5")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip-econ", action="store_true")
    ap.add_argument("--no-bootstrap", action="store_true")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    for m in methods:
        assert m in METHODS, f"未知方法 {m}"
    n = N_TOTAL if args.limit is None else int(args.limit)

    def partial_path(cfg, m):
        return os.path.join(RES_DIR, f"gate4_partial_{cfg}_{m}.json")

    all_cfg = load_all_configs()
    t0 = time.time()
    print("=" * 72)
    print(f"第四关：{len(configs)} 配置 × {methods} × 3 年链式（{n} 窗口）")
    print("指标：3 年损伤 + 逐年 LPSP + TE(M1c-M1) + 正式 LCOE + 周块 Bootstrap 95%CI")
    print("=" * 72)

    out = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "params": {"configs": configs, "methods": methods, "n_windows": n},
           "results": {}}
    for cfg_name in configs:
        cfg = all_cfg[cfg_name]
        pv_act, ld_act = load_3yr_data(cfg)
        out["results"][cfg_name] = {}
        for m in methods:
            if args.resume and os.path.exists(partial_path(cfg_name, m)):
                out["results"][cfg_name][m] = json.load(open(partial_path(cfg_name, m), encoding="utf-8"))
                print(f"  [{cfg_name}][{m}] 已有 partial，跳过（--resume）")
                continue
            print(f"\n[{cfg_name}][{m}] 3 年链式开始...")
            r = run_method_3yr(cfg, pv_act, ld_act, m, limit=args.limit,
                               skip_econ=args.skip_econ, bootstrap=not args.no_bootstrap)
            out["results"][cfg_name][m] = r
            with open(partial_path(cfg_name, m), "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1, default=str)
            print(f"  [{cfg_name}][{m}] 已落盘: {partial_path(cfg_name, m)}")
        print(f"  [{cfg_name}] 完成，累计 {time.time()-t0:.0f}s")

    # TE（M1c vs M1，同源 B* 配对；分母保护 2026-08-16：open 损伤近零时 effect_pct 无意义）
    for cfg_name in configs:
        r = out["results"][cfg_name]
        if "m1" in r and "m1c" in r:
            te = {}
            for dev in ("bat", "fc", "el"):
                d_open = r["m1"]["exact_damage_3yr"][f"{dev}_D"]
                d_cl = r["m1c"]["exact_damage_3yr"][f"{dev}_D"]
                effect = (d_cl / d_open - 1) * 100 if d_open > 1e-6 else None
                te[dev] = {"open": d_open, "closed": d_cl,
                           "delta": d_cl - d_open,
                           "effect_pct": effect,
                           "note": None if effect is not None else "open 损伤近零(≤1e-6)，effect_pct 无意义"}
            out["results"][cfg_name]["_TE_m1c_vs_m1"] = te
            print(f"  [{cfg_name}] TE(M1c-M1): " + ", ".join(
                f"{d} Δ={te[d]['delta']:+.4f} ({te[d]['effect_pct']:+.1f}%)" if te[d]['effect_pct'] is not None
                else f"{d} Δ={te[d]['delta']:+.4f} (N/A)"
                for d in ("bat", "fc", "el")))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n结果已保存：{OUT}（总耗时 {time.time()-t0:.0f}s）")


if __name__ == "__main__":
    main()
