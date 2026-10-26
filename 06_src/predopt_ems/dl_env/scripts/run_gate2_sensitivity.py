# -*- coding: utf-8 -*-
"""
run_gate2_sensitivity.py — 第二关敏感性实验（v9 §5.7 预注册阈值，DL 环境，2026-08-12）

任务：compact × 2018，比较 24h/48h/168h 三预测窗口长度的滚动 MILP 调度结果差异。
敏感性验收阈值（§5.7，2018 前预注册冻结）：
  | 指标            | 阈值（24h vs 168h 之差）|
  | LPSP           | ≤0.1 个百分点          |
  | LCOE           | ≤2%                   |
  | 设备精算损伤    | ≤5%                   |
  | TE             | ≤0.1 个百分点          |

执行范围（预注册固定，2026-08-12）：
  - 预测源：LSTM（h48/h168 预测文件仅 LSTM 版滚动拼接生成；B* 无长窗文件）
  - 模式：da（closed，应用场景；与第二关 lstm 源一致）
  - 抽样：4 季固定锚点周（2018-01-15 / 04-15 / 07-15 / 10-15 各 7 天=672 窗口，
    共 2688 窗口）——实测 672 步窗口单窗口约 40s，全年 35040 窗口=389h 不现实，
    抽样为 §9 降载精神的合理执行；锚点预先固定（不进 2018 后选日期）
  - TE 阈值项：本敏感性未重复跑 open 对照（计算量限制，h168 open 需再 ~30h）；
    标注"TE 窗口长度敏感性并入第四关 TE 配对评估"（§12 第四关含 open/closed 配对）。

产出：dl_env/results/gate2_sensitivity_result.json + 逐长度 partial（中断可续）。

运行环境：DL 环境（gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate2_sensitivity.py            # 默认 4 季×672×3 长度
  ... --lengths 96,672        # 只比 24h vs 168h（快）
  ... --limit 672             # 只冬季 1 周（快速验证）
  ... --resume                # 续跑（跳过已有 partial 的长度）
"""

import sys, os, json, time, argparse
import numpy as np
import pandas as pd

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)
sys.path.insert(0, _HERE)

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import PVInputContract
from milp_scheduler import MilpScheduler, MPCState
from run_gate1_30day import load_compact, CSV
from run_gate3_2018 import exact_damage_all, compute_lcoe

RES_DIR = os.path.join(_HERE, "..", "results")
FC_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "lstm_env", "results", "forecasts"))
OUT = os.path.join(RES_DIR, "gate2_sensitivity_result.json")

DT_H = 0.25
N_2018 = 35040
REF_KW = float(PVInputContract().reference_kw)      # 233
# 预测窗口长度 → (预测文件 PV, 预测文件 Load, 步数)
LENGTHS = {"h24": 96, "h48": 192, "h168": 672}
FILES = {
    "h24": {"pv": "PV_forecast_2018.csv", "load": "Load_forecast_2018.csv"},
    "h48": {"pv": "PV_forecast_2018_h48.csv", "load": "Load_forecast_2018_h48.csv"},
    "h168": {"pv": "PV_forecast_2018_h168.csv", "load": "Load_forecast_2018_h168.csv"},
}
# 预注册抽样锚点（4 季各 1 周，day-of-year 起点）
SEASON_ANCHORS = {"winter": (14, 672), "spring": (104, 672),
                  "summer": (195, 672), "autumn": (287, 672)}   # (day0, n_windows)
# 2018 非闰年各季天数（分季权重年化用）
SEASON_DAYS = {"winter": 90, "spring": 91, "summer": 92, "autumn": 92}
SEASON_ORDER = ["winter", "spring", "summer", "autumn"]


def season_of(day):
    """day (0-364) → 季。"""
    if day < 90:
        return "winter"
    if day < 181:
        return "spring"
    if day < 273:
        return "summer"
    return "autumn"


def sensitivity_lcoe(cfg, traj, win_idx):
    """敏感性正式 LCOE（分季权重年化，2026-08-12 完善）。

    问题：compute_lcoe 直接用抽样窗口损伤 tiling 20 年，隐含"抽样窗口=全年平均"，
    抽样窗口少（如 1 天验证）时 LCOE 被单季局部工况主导、极不稳定。
    完善：抽样窗口逐点损伤按季聚合 → 季日均损伤 × SEASON_DAYS[季] → 合成全年
    损伤总量（四季权重，与 2018 季长度一致）→ 构造 35040 点年损伤序列（季内均匀）
    → 事件驱动寿命 → compute_npc_timed → NPC/LCOE。
    仅当抽样覆盖四季（全量 2688 窗口）时 LCOE 有效；单季抽样（--limit 96）仅管线演示。
    """
    from gate3_econ_lite import build_economic_config, DOWNTIME_STEPS
    from eol_events import advance_replacement_events_with_downtime
    from npc_lite import compute_npc_timed
    from run_gate3_2018 import exact_damage_increments

    inc = exact_damage_increments(traj, cfg)          # 抽样窗口逐点增量 (n_opt,)
    n_opt = len(traj["p_uns"])
    days = win_idx[:n_opt] // 96                      # 每窗口的 day-of-year（对齐 n_opt）
    seas = np.array([season_of(int(d)) for d in days])

    # 年损伤总量 per 设备 = Σ_季(季抽样损伤 × 季天数/季抽样天数)
    annual_total = {}
    covered = []
    for dev in ("bat", "fc", "el"):
        tot = 0.0
        for s in SEASON_ORDER:
            mask = seas == s
            n_days = mask.sum() / 96.0                # 该季抽样天数（窗口数/96）
            if n_days <= 0:
                continue
            tot += inc[dev][mask].sum() * (SEASON_DAYS[s] / n_days)
            if s not in covered:
                covered.append(s)
        annual_total[dev] = tot

    # 构造 35040 点年损伤序列（均匀摊平，事件驱动对分布不敏感）
    n_yr = 35040
    feed = {dev: np.full(n_yr, annual_total[dev] / n_yr) for dev in ("bat", "fc")}   # EL 默认剔除（用户口径）
    n_horizon = int(20 * 8760 / 0.25)
    repeats = int(np.ceil(n_horizon / n_yr))
    feed = {k: np.tile(v, repeats)[:n_horizon] for k, v in feed.items()}

    res = advance_replacement_events_with_downtime(
        feed, initial_state={k: 0.0 for k in feed},
        dt_hours=0.25, downtime_steps={k: DOWNTIME_STEPS[k] for k in feed},
        start_time_hours=0.0, initial_replacement_counts={k: 0 for k in feed})
    DEV_MAP = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}
    repl = {}
    for ev in res.events:
        dev = DEV_MAP.get(ev.device, ev.device)
        repl.setdefault(dev, []).append(ev.time_hours / 8760.0)

    ecfg = build_economic_config(cfg)
    from run_gate3_2018 import load_annual_kwh
    annual_mwh = load_annual_kwh(cfg) / 1e3
    econ = compute_npc_timed(ecfg, repl, annual_mwh, label="gate2_sens")
    return {"lcoe_cny_per_kwh": econ["lcoe_cny_per_kwh"],
            "npc_million_cny": econ["npc_million_cny"],
            "replacement_schedule": {k: [round(x, 2) for x in v] for k, v in repl.items()},
            "annual_damage": {k: round(v, 5) for k, v in annual_total.items()},
            "seasons_covered": covered,
            "lcoe_method": "分季权重年化（季日均×2018季天数合成年损伤；仅四季覆盖时有效）"}


def load_pred(fname, cfg, H):
    """读 MPC 口径预测文件（任意步数 H），PV 统一缩放到 cfg.pv_kwp 口径（同第二关修复）。"""
    df = pd.read_csv(os.path.join(FC_DIR, fname))
    cols = [c for c in df.columns if c.startswith("pred_h")]
    assert len(cols) == H, f"{fname}: 列数 {len(cols)} != {H}"
    m = df[cols].to_numpy(dtype=float)
    if "PV" in fname:
        m = np.minimum(m * (cfg.pv_kwp / REF_KW), cfg.pv_kwp)
    return m


def sample_window_indexes():
    """预注册抽样窗口索引集合（4 季锚点周），返回排序索引数组。"""
    idx = []
    for day0, n in SEASON_ANCHORS.values():
        idx.extend(range(day0 * 96, day0 * 96 + n))
    return np.array(sorted(idx), dtype=int)


def run_length(cfg, pv, load, H, win_idx, verbose=True):
    """单窗口长度：抽样窗口滚动 MILP（MPC，t=0 执行，状态继承）。"""
    tag = {96: "h24", 192: "h48", 672: "h168"}[H]
    pv_pred = load_pred(FILES[tag]["pv"], cfg, H)
    ld_pred = load_pred(FILES[tag]["load"], cfg, H)

    sched = MilpScheduler(cfg, verbose=False)
    state = MPCState(soc=0.50, h2_kg=cfg.h2_kg * 0.5)
    prev_u_fc = prev_u_el = 0
    run_fc = off_fc = run_el = off_el = 0
    cols = {"p_ch": [], "p_dis": [], "p_fc": [], "p_el": [], "p_curt": [], "p_uns": [],
            "u_fc": [], "u_el": [], "soc": [], "h2": []}
    n_opt = n_infeas = n_tl = 0
    solve_times = []
    t0 = time.time()
    for k, w in enumerate(win_idx):
        res = sched.solve_window(pv_pred[w], ld_pred[w], state, mode="da")
        solve_times.append(res.solve_time_s)
        if not res.feasible:
            n_infeas += 1
            continue
        n_opt += 1
        if res.status_l3 == 9:
            n_tl += 1
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
        cols["p_ch"].append(float(res.commands[0, 0])); cols["p_dis"].append(float(res.commands[0, 1]))
        cols["p_fc"].append(float(res.commands[0, 2])); cols["p_el"].append(float(res.commands[0, 3]))
        cols["p_curt"].append(float(res.commands[0, 4])); cols["p_uns"].append(float(res.commands[0, 5]))
        cols["u_fc"].append(state.u_fc); cols["u_el"].append(state.u_el)
        cols["soc"].append(state.soc); cols["h2"].append(state.h2_kg)
        if (k + 1) % 500 == 0 and verbose:
            print(f"    [{tag}] {k+1}/{len(win_idx)} 窗口 | {time.time()-t0:.0f}s | 不可行 {n_infeas}")
    st = np.array(solve_times)
    traj = {k: np.array(v) for k, v in cols.items()}
    # LPSP：分子=执行窗口失负荷能量；分母=全部抽样窗口负荷能量（与 run_gate2 同口径的同步截断语义）
    lpsp = float(np.sum(traj["p_uns"]) * DT_H / max(np.sum(load[win_idx]) * DT_H, 1e-9))
    exact = exact_damage_all(traj, cfg)
    econ = sensitivity_lcoe(cfg, traj, win_idx)     # 正式 LCOE（分季权重年化，EL 剔除）
    print(f"  [{tag}] 完成 {n_opt} | 不可行 {n_infeas} | L3超时 {n_tl} | LPSP={lpsp*100:.4f}% | "
          f"LCOE={econ['lcoe_cny_per_kwh']:.3f}（季覆盖 {econ['seasons_covered']}）| "
          f"求解中位 {np.median(st):.1f}s max {st.max():.1f}s | 耗时 {time.time()-t0:.0f}s")
    return {"H": H, "tag": tag, "n_opt": n_opt, "n_infeas": n_infeas, "n_tl": n_tl,
            "solve_med_s": float(np.median(st)), "solve_max_s": float(st.max()),
            "lpsp": lpsp, "exact_damage": exact,
            "lcoe_cny_per_kwh": econ["lcoe_cny_per_kwh"],
            "npc_million_cny": econ["npc_million_cny"],
            "replacement_schedule": econ["replacement_schedule"],
            "annual_damage": econ["annual_damage"],
            "seasons_covered": econ["seasons_covered"],
            "fc_run_steps": int(traj["u_fc"].sum()), "el_run_steps": int(traj["u_el"].sum()),
            "soc_range": [float(traj["soc"].min()), float(traj["soc"].max())]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lengths", type=str, default="96,192,672", help="窗口长度（96/192/672）")
    ap.add_argument("--limit", type=int, default=None, help="抽样窗口数（默认 2688=4 季×672）")
    ap.add_argument("--resume", action="store_true", help="续跑：跳过已有 partial 的长度")
    args = ap.parse_args()

    lengths = [int(x) for x in args.lengths.split(",")]
    tags = {96: "h24", 192: "h48", 672: "h168"}
    for L in lengths:
        assert L in tags, f"不支持窗口长度 {L}"
    win_idx = sample_window_indexes()
    if args.limit is not None:
        win_idx = win_idx[:args.limit]
    n_win = len(win_idx)

    def partial_path(L):
        return os.path.join(RES_DIR, f"gate2_sens_partial_{tags[L]}.json")

    cfg, _rep = load_compact()
    pv, load = load_2018_data_for_sens(cfg)
    print("=" * 72)
    print(f"第二关敏感性：compact × 2018 抽样 {n_win} 窗口（4 季锚点周，预注册固定）| 长度 {[tags[L] for L in lengths]}")
    print(f"阈值（§5.7 预注册，24h vs 168h）：LPSP≤0.1pp | LCOE≤2% | 损伤≤5% | TE≤0.1pp（第四关评估）")
    print("=" * 72)

    results = {}
    for L in lengths:
        tag = tags[L]
        if args.resume and os.path.exists(partial_path(L)):
            results[L] = json.load(open(partial_path(L), encoding="utf-8"))
            print(f"  [{tag}] 已有 partial，跳过（--resume）")
            continue
        print(f"\n[{tag}]（{L} 步）滚动 MILP 开始...")
        r = run_length(cfg, pv, load, L, win_idx)
        results[L] = r
        with open(partial_path(L), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        print(f"  [{tag}] 已落盘: {partial_path(L)}")

    # 阈值判定（24h vs 168h，绝对值差，§5.7 预注册；2026-08-14 修复 abs 缺失误判）
    if 96 in results and 672 in results:
        r24, r168 = results[96], results[672]
        d_lpsp = (r168["lpsp"] - r24["lpsp"]) * 100                     # 个百分点
        d_lcoe = (r168["lcoe_cny_per_kwh"] / r24["lcoe_cny_per_kwh"] - 1) * 100   # %
        d_dmg = {dev: abs(r168["exact_damage"][f"{dev}_D"] / max(r24["exact_damage"][f"{dev}_D"], 1e-12) - 1) * 100
                 for dev in ("bat", "fc", "el")}                       # %
        checks = {"lpsp_pp": d_lpsp, "lcoe_pct": d_lcoe, "dmg_pct": d_dmg,
                  "ok": {"lpsp": abs(d_lpsp) <= 0.1, "lcoe": abs(d_lcoe) <= 2.0,
                         "dmg": all(v <= 5.0 for v in d_dmg.values())}}
        print("\n" + "-" * 72)
        print(f"阈值判定（24h vs 168h，绝对值差）: ΔLPSP={d_lpsp:+.3f}pp (≤0.1 {'✅' if abs(d_lpsp)<=0.1 else '❌'}) | "
              f"ΔLCOE={d_lcoe:+.2f}% (≤2 {'✅' if abs(d_lcoe)<=2 else '❌'}) | "
              f"Δ损伤 BAT={d_dmg['bat']:+.1f}% FC={d_dmg['fc']:+.1f}% EL={d_dmg['el']:+.1f}% (≤5 {'✅' if all(v<=5 for v in d_dmg.values()) else '❌'})")
    else:
        checks = None

    out = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "rule": "v9 §5.7 预注册阈值（2026-08-12 执行范围：LSTM da × 4 季锚点周抽样）",
           "params": {"lengths": lengths, "n_windows": n_win,
                      "season_anchors": {k: v[0] for k, v in SEASON_ANCHORS.items()}},
           "per_length": {str(k): v for k, v in results.items()},
           "threshold_24_vs_168": checks,
           "note": ("LCOE 采用分季权重年化（季日均损伤×2018 季天数合成年损伤，2026-08-12 完善，"
                    "仅抽样覆盖四季时有效）；TE 阈值项（≤0.1pp）未在本敏感性重复 open 对照"
                    "（h168 open 计算量 ~30h），并入第四关 TE 配对评估；抽样锚点预注册固定"
                    "（01-15/04-15/07-15/10-15 各 7 天），全年 672 步窗口不现实"
                    "（实测 40s/窗口 → 389h），抽样为 §9 降载精神的执行。")}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n结果已保存：{OUT}")


def load_2018_data_for_sens(cfg):
    df = pd.read_csv(CSV)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    pv_raw = d18["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d18["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    from components.annual_input_contract import scale_pv_available
    pv = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)
    return pv, load


if __name__ == "__main__":
    main()
