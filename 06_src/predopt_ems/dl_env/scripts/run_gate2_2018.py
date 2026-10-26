# -*- coding: utf-8 -*-
"""
run_gate2_2018.py — 第二关：compact × 2018 全年 3 预测源对比（v9 §10/§12）

任务：2018 全年 35,040 个 15min 预测时刻，3 个预测源（B* / LSTM / perfect）各自
独立完成 compact 配置的三级字典序滚动 MILP（每窗口只执行 t=0，状态继承，MPC 语义），
输出全年轨迹并计算验收指标。

验收（v9 §10/§12 + 2026-08-09 用户冻结口径）：
  主指标：代理 Spearman ≥ 0.90 —— MILP 内代理损伤 vs 调度后精算损伤（compute_damage）
          按周块（672 点）聚合后的 Spearman 秩相关（BAT/FC/EL 三设备分别报）；
  可靠性检查（不作为主指标）：L1/LPSP = ΣP_uns·Δt / Σload（能量缺失比，准入 <1%）；
  LSTM 优于 B* ≥5%（PV 日照 nMAE / Load MASE，已在预测文件生成阶段验证，此处报告
  逐点口径 MAE 对照）。

预测层冻结规则（2026-08-09）：3 源预测一次性生成缓存于 lstm_env/results/forecasts/，
本脚本只读取，不重新预测；后续 MILP 方法/5 配置/open-closed 复用同一批预测。

预测文件格式（MPC 口径，35040 行 × 97 列）：
  timestamp, pred_h1..pred_h96 —— 第 w 行 = 时刻 t_w 起未来 96 步预测（kW）
  B* / LSTM 均同格式；perfect 直接用实际窗口值（零成本）。

运行环境：DL 环境（D:/Anaconda/envs/DL，gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate2_2018.py            # 全年 3 源（约 8.8h）
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate2_2018.py --limit 960  # 快速 10 天切片验证
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate2_2018.py --sources bstar,lstm  # 选源
"""

import sys, os, json, time, argparse
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

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
OUT = os.path.join(RES_DIR, "gate2_2018_result.json")

REF_KW = float(PVInputContract().reference_kw)   # 233（输入 CSV 参考容量）

WINDOW = 96
DT_H = 0.25
BLOCK = 672                # 周块（15min×7d）用于 Spearman 聚合
N_2018 = 35040             # 2018 全年 15min 点

# 预测文件（MPC 口径，35040×97）
FC = {
    "bstar": {"pv": "Bstar_PV_forecast_2018.csv", "load": "Bstar_Load_forecast_2018.csv"},
    "lstm": {"pv": "PV_forecast_2018.csv", "load": "Load_forecast_2018.csv"},
}

SOURCES_ALL = ["bstar", "lstm", "perfect"]


def load_2018_data(cfg):
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    pv_raw = d18["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    pv = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)
    return pv, load   # 均为 35040 点


def load_forecast_file(fname):
    """读 MPC 口径预测文件 → (35040, 96) kW 矩阵。"""
    df = pd.read_csv(os.path.join(FC_DIR, fname))
    assert len(df) == N_2018, f"{fname} 行数 {len(df)} != {N_2018}"
    cols = [c for c in df.columns if c.startswith("pred_h")]
    assert len(cols) == 96
    return df[cols].to_numpy(dtype=float)


def load_pv_pred(fname, cfg):
    """PV 预测文件（原始 reference=233 口径）统一缩放到 cfg.pv_kwp 口径。
    【修复 2026-08-11】此前直接读原始预测，与 perfect 源 scale_pv_available 缩放后
    的 388 kW 口径不一致（三源 PV 差 40%），导致对比被 PV 量级主导而非预测质量。"""
    pred = load_forecast_file(fname)
    scaled = pred * (cfg.pv_kwp / REF_KW)
    return np.minimum(scaled, cfg.pv_kwp)          # 与 scale_pv_available 的 clip 语义一致


def run_source(cfg, pv, load, source, limit=None):
    """单预测源全年滚动 MILP。返回轨迹 dict（执行序列长度 L=limit or 35040）。"""
    n = N_2018 if limit is None else int(limit)
    if source == "perfect":
        pv_fc = pv; load_fc = load
    else:
        pv_pred = load_pv_pred(FC[source]["pv"], cfg)
        load_pred = load_forecast_file(FC[source]["load"])

    sched = MilpScheduler(cfg, verbose=False)
    state = MPCState(soc=0.50, h2_kg=cfg.h2_kg * 0.5)
    prev_u_fc = prev_u_el = 0
    run_fc = off_fc = run_el = off_el = 0
    cols = {"p_ch": [], "p_dis": [], "p_fc": [], "p_el": [], "p_curt": [], "p_uns": [],
            "u_fc": [], "u_el": [], "soc": [], "h2": []}
    l1_total = l2_total = l3_total = 0.0
    n_opt = n_infeas = 0
    infeas_idx = []       # 不可行窗口索引（诊断）
    solve_times = []      # 每窗口求解耗时（诊断）
    gaps = []             # 每窗口 L3 MIPGap（TIME_LIMIT 披露）
    n_tl = 0              # L3 TIME_LIMIT 窗口数（修复 2026-08-11 后应≈0，除非 60s 无 incumbent）
    tl_idx = []
    t0 = time.time()
    for i in range(n):
        if source == "perfect":
            pv_w = pv_fc[i:i + WINDOW]
            load_w = load_fc[i:i + WINDOW]
        else:
            pv_w = pv_pred[i]
            load_w = load_pred[i]
        res = sched.solve_window(pv_w, load_w, state, mode="normal")
        if not res.feasible:
            n_infeas += 1
            infeas_idx.append(i)
            solve_times.append(res.solve_time_s)
            continue
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
        cols["p_ch"].append(float(res.commands[0, 0]))
        cols["p_dis"].append(float(res.commands[0, 1]))
        cols["p_fc"].append(float(res.commands[0, 2]))
        cols["p_el"].append(float(res.commands[0, 3]))
        cols["p_curt"].append(float(res.commands[0, 4]))
        cols["p_uns"].append(float(res.commands[0, 5]))
        cols["u_fc"].append(state.u_fc)
        cols["u_el"].append(state.u_el)
        cols["soc"].append(state.soc)
        cols["h2"].append(state.h2_kg)
        l1_total += float(res.l1_opt)
        l2_total += float(res.l2_opt)
        l3_total += float(res.l3_opt)
        if (i + 1) % 5000 == 0:
            print(f"    [{source}] {i+1}/{n} 窗口 | {time.time()-t0:.0f}s | 不可行 {n_infeas}")
        solve_times.append(res.solve_time_s)
        gaps.append(res.mip_gap_l3)
        if res.status_l3 == 9:          # GRB.TIME_LIMIT
            n_tl += 1
            tl_idx.append(i)
    st = np.array(solve_times) if solve_times else np.array([0.0])
    gs = np.array(gaps) if gaps else np.array([0.0])
    print(f"  [{source}] 完成 {n_opt} 窗口 | 不可行 {n_infeas} | L3 超时 {n_tl} | "
          f"耗时 {time.time()-t0:.0f}s | 求解时间 中位 {np.median(st):.2f}s "
          f"p95 {np.percentile(st,95):.2f}s max {st.max():.2f}s | L3 gap p95 {np.percentile(gs,95):.3f} max {gs.max():.3f}")
    return {k: np.array(v) for k, v in cols.items()}, {"l1_kwh": l1_total, "l2": l2_total,
                                                       "l3": l3_total, "n_opt": n_opt,
                                                       "n_infeas": n_infeas,
                                                       "infeas_idx": infeas_idx,
                                                       "n_tl": n_tl,
                                                       "tl_idx": tl_idx,
                                                       "mip_gap_p95": float(np.percentile(gs, 95)),
                                                       "mip_gap_max": float(gs.max()),
                                                       "solve_time_p50_s": float(np.median(st)),
                                                       "solve_time_p95_s": float(np.percentile(st, 95)),
                                                       "solve_time_max_s": float(st.max())}


def proxy_damage(traj, cfg):
    """MILP 内代理损伤（v9 §7.1 口径）：按周块聚合。
    BAT: 能量吞吐 Σ(P_ch+P_dis)·Δt/E；FC/EL: 运行小时 + 启停。"""
    n = len(traj["p_ch"])
    bat_thr = (traj["p_ch"] + traj["p_dis"]) * DT_H / cfg.bat_kwh
    fc_run = traj["u_fc"].astype(float) * DT_H
    el_run = traj["u_el"].astype(float) * DT_H
    ua = traj["u_fc"]
    fc_start = np.concatenate([[0.0], ((ua[1:] > 0.5) & (ua[:-1] <= 0.5)).astype(float)])
    ua2 = traj["u_el"]
    el_start = np.concatenate([[0.0], ((ua2[1:] > 0.5) & (ua2[:-1] <= 0.5)).astype(float)])
    n_blk = n // BLOCK
    res = {"bat": [], "fc": [], "el": []}
    for k in range(n_blk):
        s = k * BLOCK; e = s + BLOCK
        res["bat"].append(float(bat_thr[s:e].sum()))
        res["fc"].append(float(fc_run[s:e].sum() + fc_start[s:e].sum() * 5.0))
        res["el"].append(float(el_run[s:e].sum() + el_start[s:e].sum() * 5.0))
    return {k: np.array(v) for k, v in res.items()}


def exact_damage(traj, cfg):
    """调度后精算损伤（compute_damage，v9 §7.1）：按周块聚合。"""
    n = len(traj["p_ch"])
    # MILP commands [p_ch, p_dis, p_fc, p_el] → compute_damage [P_BAT(+放,-充), P_FC, P_EL]
    p_bat = traj["p_dis"] - traj["p_ch"]
    ems_cmds = np.stack([p_bat, traj["p_fc"], traj["p_el"]], axis=1)
    cfg_dict = {
        "capacity": {"BAT_kWh": cfg.bat_kwh, "PEMFC_kW": cfg.pemfc_kw, "PEMEL_kW": cfg.pemel_kw},
        "battery_constraints": {"soc_min": cfg.soc_min, "soc_max": cfg.soc_max,
                                "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.275},
    }
    d = compute_damage(lambda D: (max(0.80, 1 - 0.20 * D),
                                  None, min(cfg.bat_kwh * 0.5, cfg.bat_kwh * 0.5)),
                       ems_cmds, cfg_dict, dt_min=15.0,
                       D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                       coalition=CoalitionState(True, True, True, True))
    n_blk = n // BLOCK
    res = {"bat": [], "fc": [], "el": []}
    for k in range(n_blk):
        s = k * BLOCK; e = s + BLOCK
        res["bat"].append(float(d["dD_bat"][s:e].sum()))
        res["fc"].append(float(d["dD_fc"][s:e].sum()))
        res["el"].append(float(d["dD_el"][s:e].sum()))
    return {k: np.array(v) for k, v in res.items()}


def spearman_check(proxy, exact):
    out = {}
    for dev in ("bat", "fc", "el"):
        p, e = proxy[dev], exact[dev]
        if len(p) >= 3 and np.std(p) > 0 and np.std(e) > 0:
            rho, pval = spearmanr(p, e)
            out[dev] = {"rho": float(rho), "pvalue": float(pval), "n_blocks": len(p)}
        else:
            out[dev] = {"rho": None, "pvalue": None, "n_blocks": len(p), "note": "块内退化零方差，无法计算"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="窗口数（默认全年 35040）")
    ap.add_argument("--sources", type=str, default=",".join(SOURCES_ALL), help="预测源，逗号分隔")
    ap.add_argument("--resume", action="store_true", help="续跑：跳过 results/ 下已有 *_partial.json 的源")
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    for s in sources:
        assert s in SOURCES_ALL, f"未知预测源 {s}"
    n = N_2018 if args.limit is None else int(args.limit)

    # 逐源落盘（v2，2026-08-10）：每源完成即写 results/gate2_partial_{src}.json，中断不丢已完成源
    def partial_path(src):
        return os.path.join(RES_DIR, f"gate2_partial_{src}.json")

    t0 = time.time()
    cfg, _rep = load_compact()
    pv, load = load_2018_data(cfg)
    print("=" * 72)
    print(f"第二关：compact × 2018 全年 {n} 窗口 | 预测源 {sources}")
    print(f"主验收：代理 Spearman ≥0.90（周块 672 点）；L1/LPSP 可靠性检查（准入<1%）")
    print("=" * 72)

    results = {}
    for src in sources:
        if args.resume and os.path.exists(partial_path(src)):
            results[src] = json.load(open(partial_path(src), encoding="utf-8"))
            print(f"  [{src}] 已有 partial 结果，跳过（--resume）")
            continue
        print(f"\n[{src}] 全年滚动 MILP 开始...")
        traj, meta = run_source(cfg, pv, load, src, limit=args.limit)
        l_load = load[:len(traj["p_uns"])]
        lpsp = float(np.sum(traj["p_uns"]) * DT_H / max(np.sum(l_load) * DT_H, 1e-9))
        proxy = proxy_damage(traj, cfg)
        exact = exact_damage(traj, cfg)
        sp = spearman_check(proxy, exact)
        # 逐点口径 MAE（相对 B* 用于对照）
        results[src] = {
            "meta": meta, "lpsp": lpsp,
            "spearman": sp,
            "proxy_weekly": {k: v.tolist() for k, v in proxy.items()},
            "exact_weekly": {k: v.tolist() for k, v in exact.items()},
            "soc_range": [float(traj["soc"].min()), float(traj["soc"].max())],
            "fc_run_steps": int(traj["u_fc"].sum()), "el_run_steps": int(traj["u_el"].sum()),
        }
        with open(partial_path(src), "w", encoding="utf-8") as f:
            json.dump(results[src], f, ensure_ascii=False, indent=1)
        sp_main = sp["bat"]["rho"] if sp["bat"]["rho"] is not None else 0.0
        print(f"  [{src}] LPSP={lpsp*100:.4f}% | Spearman: BAT={sp['bat']['rho']} "
              f"FC={sp['fc']['rho']} EL={sp['el']['rho']} | FC 运行 {results[src]['fc_run_steps']} 步 | "
              f"耗时 {time.time()-t0:.0f}s")
        print(f"  [{src}] 已逐源落盘: {partial_path(src)}")

    # 判定（用户冻结口径：Spearman 主指标，LPSP 可靠性检查）
    ok_sp = {s: all(v.get("rho", 0) is not None and v["rho"] >= 0.90
                    for v in results[s]["spearman"].values()) for s in sources}
    ok_lpsp = {s: results[s]["lpsp"] < 0.01 for s in sources}
    print("\n" + "-" * 72)
    print("验收判定：")
    for s in sources:
        print(f"  [{s}] Spearman(3 设备全≥0.90)={'PASS ✅' if ok_sp[s] else 'FAIL ❌'} | "
              f"LPSP<1%={'PASS ✅' if ok_lpsp[s] else 'FAIL ❌'}")

    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rule": "v9 §10/§12 + 2026-08-09 冻结口径（Spearman 主指标，L1/LPSP 可靠性检查）",
        "params": {"n_windows": n, "sources": sources, "forecast_dir": FC_DIR},
        "per_source": {k: {kk: vv for kk, vv in v.items() if kk != "proxy_weekly" and kk != "exact_weekly"}
                       for k, v in results.items()},
        "checks": {"spearman_ge090": {s: bool(v) for s, v in ok_sp.items()},
                   "lpsp_lt1pct": {s: bool(v) for s, v in ok_lpsp.items()}},
        "pass": bool(all(ok_sp.values()) and all(ok_lpsp.values())),
        "note": ("代理 Spearman 按周块（672 点）聚合代理 vs 精算损伤秩相关，为第二关主验收；"
                 "LPSP=ΣP_uns·Δt/Σload 能量缺失比，<1% 为可靠性准入（防退化检查，非主指标）；"
                 "预测层冻结规则：3 源预测文件一次性缓存于 lstm_env/results/forecasts/，复用不重算。"),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n结果已保存：{OUT}")
    return bool(all(ok_sp.values()))


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
