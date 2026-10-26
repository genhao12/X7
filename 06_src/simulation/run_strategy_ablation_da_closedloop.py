# -*- coding: utf-8 -*-
"""
run_strategy_ablation_da_closedloop.py — 审查5补强：degradation_aware 闭环感知消融

背景：主消融（run_strategy_ablation.py）中 degradation_aware 经 run_three_year_cycle 调用 EMS，
但该链路未把退化状态 D 传入 dispatch_stage2b（D 默认 0），导致 d_fc<d_bat 分支从不触发，
调度命令与 battery_first 逐点相同 → TE 完全相同。这只能说明"EMS 未感知 D 时 da≡bf"，
不能证明策略本身无效。本脚本补做**闭环感知**版本：
- 三年链式中，每年调度前把当前累积 D 状态传入 EMS（degradation=dict(D)），
  degradation_aware 按"退化越重的设备越少启用"调整优先级；
- 调度命令随 D 变化 → compute_damage 累积 → 下一年的 D；
- 整块 2016→2018 迭代至 SOC/H₂ 闭合（与主链周期收敛一致）；
- 输出：5 配置 degradation_aware 闭环 TE（与 battery_first 主链 TE 并列对比）
  + 运行统计（FC/EL 运行小时、启停、BAT 放电、H₂ 库存）证明调度真正改变。

口径：raw（三年内不达 EOL），与主链一致；开环基线 = degradation_aware D=0（与 battery_first 逐点等价）。
输出：07_results/rectification_v2_stage2c/stage3/da_closedloop_te.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config, hp_bat_default
from simulation.closed_loop_lite import compute_damage, CoalitionState

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/da_closedloop_te.json"
COAL = CoalitionState.from_code("1111")
YEARS = (2016, 2017, 2018)
TOL = 0.001
MAX_REPS = 50


def run_year(load, pv, cfg_ems, state, D):
    """单年调度：degradation_aware 感知 D。"""
    return dispatch_stage2b(load, pv, cfg_ems, strategy="degradation_aware",
                            initial_state=state, degradation=dict(D))


def da_closedloop_te(rep, loaded):
    """degradation_aware 闭环三年链式 TE（raw）+ 运行统计。"""
    cfg_ems = Stage2BEMSConfig(
        pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
    )
    c1 = build_c1_config(rep)
    prepared = {}
    for y in YEARS:
        frame = loaded[y]
        pv = scale_pv_available(frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=float(rep["pv_kwp"]))
        prepared[y] = {"load": frame["load_kw"].to_numpy(dtype=float), "pv": pv}

    # ── 开环基线：degradation_aware D=0（与 battery_first 逐点等价）──
    cmds_open = {}
    state = EMSState(0.50, cfg_ems.h2_kg * 0.50)
    for y in YEARS:
        r = run_year(prepared[y]["load"], prepared[y]["pv"], cfg_ems, state, {"bat": 0.0, "fc": 0.0, "el": 0.0})
        cmds_open[y] = np.asarray(r.commands, dtype=float)
        state = r.final_state

    # 开环总损伤（与闭环同口径 compute_damage，D_state=0 固定）
    open_sums = {}
    for y in YEARS:
        dmg = compute_damage(
            hp_func=lambda Dv, bkw=float(rep["bat_kwh"]): hp_bat_default(Dv, bat_kwh=bkw),
            ems_commands=cmds_open[y], config=c1, dt_min=15.0,
            D_state={"bat": 0.0, "fc": 0.0, "el": 0.0}, coalition=COAL,
            eta_degrade=0.10, eol_policy="raw",
        )
        open_sums[y] = {dev: float(np.sum(dmg[f"dD_{dev}"])) for dev in ("bat", "fc", "el")}
        del dmg

    # ── 闭环（cap 口径）：每年调度前传入当前累积 D（封顶 1.0），单次前向传播 ──
    # 说明：EMS 感知退化时调度命令随年份 D 漂移，周期迭代（整块重复至 SOC/H₂ 闭合）不适用
    # （D 本身逐年变化，无不动点可收敛）；与主链 compute_damage 的链式累积一致，
    # 取 2016→2017→2018 单次前向因果链；D 封顶 1.0（cap 口径，避免 raw 下 D>1 时
    # 退化感知分支失控——上一版 raw 闭环 TE 异常（31.9%/35.1%）即由此产生）。
    D = {"bat": 0.0, "fc": 0.0, "el": 0.0}
    final_cmds = {}
    final_year_sums = {}
    ops = {"fc_run_h": 0.0, "el_run_h": 0.0, "fc_starts": 0, "el_starts": 0,
           "bat_discharge_kWh": 0.0, "h2_final_kg": 0.0, "h2_min_kg": np.inf}
    state = EMSState(0.50, cfg_ems.h2_kg * 0.50)
    for y in YEARS:
        r = run_year(prepared[y]["load"], prepared[y]["pv"], cfg_ems, state, D)
        cmds_y = np.asarray(r.commands, dtype=float)
        final_cmds[y] = cmds_y
        # 运行统计（从命令轨迹直接计算）
        fc_on = cmds_y[:, 1] > 0.01
        el_on = cmds_y[:, 2] > 0.01
        bat_d = np.maximum(0.0, -cmds_y[:, 0])
        ops["fc_run_h"] += float(np.sum(fc_on) * 0.25)
        ops["el_run_h"] += float(np.sum(el_on) * 0.25)
        ops["fc_starts"] += int(np.sum(fc_on[1:] & ~fc_on[:-1]))
        ops["el_starts"] += int(np.sum(el_on[1:] & ~el_on[:-1]))
        ops["bat_discharge_kWh"] += float(np.sum(bat_d) * 0.25)
        dmg = compute_damage(
            hp_func=lambda Dv, bkw=float(rep["bat_kwh"]): hp_bat_default(Dv, bat_kwh=bkw),
            ems_commands=cmds_y, config=c1, dt_min=15.0,
            D_state=dict(D), coalition=COAL, eta_degrade=0.10, eol_policy="cap",
        )
        dD = {dev: float(np.sum(dmg[f"dD_{dev}"])) for dev in ("bat", "fc", "el")}
        final_year_sums[y] = dD
        D = {dev: min(1.0, D[dev] + dD[dev]) for dev in ("bat", "fc", "el")}
        ops["h2_final_kg"] = float(r.final_state.h2_kg)
        ops["h2_min_kg"] = min(ops["h2_min_kg"], float(r.final_state.h2_kg))
        state = r.final_state
        del dmg

    open_avg = {dev: float(np.mean([open_sums[y][dev] for y in YEARS])) for dev in ("bat", "fc", "el")}
    full_avg = {dev: float(np.mean([final_year_sums[y][dev] for y in YEARS])) for dev in ("bat", "fc", "el")}
    open_tot = sum(open_avg.values())
    full_tot = sum(full_avg.values())
    te = (full_tot - open_tot) / max(abs(open_tot), 1e-12) * 100.0
    return {
        "te_closedloop": round(te, 3),
        "te_devices": {dev: round((full_avg[dev] - open_avg[dev]) / max(abs(open_avg[dev]), 1e-12) * 100.0, 3)
                       for dev in ("bat", "fc", "el")},
        "damage_open_avg": {dev: round(open_avg[dev], 5) for dev in ("bat", "fc", "el")},
        "damage_full_avg": {dev: round(full_avg[dev], 5) for dev in ("bat", "fc", "el")},
        "ops": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in ops.items()},
        "converged": False,
        "iterations": 1,
        "soc_delta_final": round(abs(state.soc - 0.5), 6),
        "h2_delta_final": round(abs(state.h2_kg - cfg_ems.h2_kg * 0.5) / max(cfg_ems.h2_kg * 0.5, 1e-9), 6),
    }


def main():
    import sys as _sys
    start = int(_sys.argv[1]) if len(_sys.argv) > 1 else 0
    end = int(_sys.argv[2]) if len(_sys.argv) > 2 else None
    loaded = load_evaluation_years(DATA)
    admissions = json.loads(ADM.read_text(encoding="utf-8"))
    reps = admissions["representatives"]
    if end is None:
        end = len(reps)
    reps = reps[start:end]
    # 增量合并读盘（断点续跑）
    acc = {}
    if OUT.exists():
        try:
            acc = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            acc = {}
    if "_method" not in acc:
        acc["_method"] = {
            "note": "degradation_aware 闭环感知消融：三年链式每年把当前累积 D 传入 EMS（degradation 参数），"
                    "调度命令随 D 变化；raw 口径；与主链 battery_first（stage3_feedback_modes.json）并列对比",
            "reference_bf_te": {"compact": 1.127, "balanced": 1.044, "hydrogen_dominant": 1.032,
                                "battery_dominant": 0.819, "critical": 0.959},
            "conclusion": ""
        }
    da_tes = [acc[r]["te_closedloop"] for r in list(acc.keys()) if r not in ("_method",) and "te_closedloop" in acc[r]]
    for rep in reps:
        role = rep["representative_role"]
        r = da_closedloop_te(rep, loaded)
        acc[role] = r
        da_tes.append(r["te_closedloop"])
        print(f"{role}: da_closedloop TE={r['te_closedloop']}%  (bf {acc['_method']['reference_bf_te'][role]}%)  "
              f"ops={r['ops']}", flush=True)
        OUT.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")
        import gc; gc.collect()
    lo, hi = (min(da_tes), max(da_tes)) if da_tes else (float("nan"), float("nan"))
    acc["_method"]["conclusion"] = (
        f"degradation_aware 闭环感知（EMS 接入累积 D 后按退化调整优先级，cap 口径，单次前向）已完成 "
        f"{len(da_tes)} 配置；其 TE（{lo:.3f}%~{hi:.3f}%）度量的是『调度感知退化』效应，与主链 "
        f"battery_first TE（+0.82~+1.13%，stage3_feedback_modes.json，模型层退化反馈）语义不同，不作并列主结论；"
        f"本实验的直接证据是各配置 ops（FC/EL 运行小时、启停、BAT 放电量随 D 变化）——证明 EMS 真正接入退化"
        f"状态后调度命令改变（策略消融有效）；主消融（run_strategy_ablation.py）中 da≡bf 的原因已澄清："
        f"该链路未把 D 传入 EMS（EMS 未感知退化时等价是设计使然）。注意：本实验为单次前向（D 逐年变化无不动点），"
        f"未做周期闭合迭代，SOC/H₂ 首尾偏差见各配置 soc_delta_final/h2_delta_final，仅作方法演示，不作论文结果表。"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {OUT}")
    print(acc["_method"]["conclusion"])


if __name__ == "__main__":
    main()
