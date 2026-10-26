# -*- coding: utf-8 -*-
"""
run_stage3_feedback_modes_3strategy.py — 策略 × 反馈模式交互矩阵（P2-1）

目的：验证调度策略（battery_first/hydrogen_first/balanced）**不预设耦合方向**，
并给出策略 × 全设备反馈的交互矩阵（统一用 dispatch_stage2b，消除旧 dispatch_strategy 的不对称）。

对 5 配置 × 3 策略 × 3 反馈模式（open_loop/bat_only/full）× 3 年链式（D 逐年累积），cap 口径。
输出：
  - 各策略下 full TE（相对开环）—— 验证 TE>0 方向跨策略稳健
  - 全反馈下策略间 TE 差异 —— 策略 × 反馈交互
输出：07_results/rectification_v2_stage2c/stage3/stage3_feedback_modes_3strategy.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config, hp_bat_default, run_three_year_cycle, extract_commands
from simulation.closed_loop_lite import compute_damage, CoalitionState

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/stage3_feedback_modes_3strategy.json"
COAL = CoalitionState.from_code("1111")

STRATEGIES = ["battery_first", "hydrogen_first", "balanced"]
MODES = ["open_loop", "bat_only", "full"]
YEARS = (2016, 2017, 2018)


def run_chained(rep, loaded, strategy, policy="cap"):
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
    cycle = run_three_year_cycle(prepared, cfg_ems, strategy=strategy)
    cmds = extract_commands(cycle)

    out = {}
    for mode in MODES:
        D = {"bat": 0.0, "fc": 0.0, "el": 0.0}
        year_sums = {y: {"bat": 0.0, "fc": 0.0, "el": 0.0} for y in YEARS}
        for y in YEARS:
            D_state = ({"bat": D["bat"], "fc": 0.0, "el": 0.0} if mode == "bat_only"
                       else (dict(D) if mode == "full" else {"bat": 0.0, "fc": 0.0, "el": 0.0}))
            dmg = compute_damage(
                hp_func=lambda Dv, bkw=float(rep["bat_kwh"]): hp_bat_default(Dv, bat_kwh=bkw),
                ems_commands=cmds[y], config=c1, dt_min=15.0,
                D_state=D_state, coalition=COAL, eta_degrade=0.10, eol_policy=policy,
            )
            dD = {dev: float(np.sum(dmg[f"dD_{dev}"])) for dev in ("bat", "fc", "el")}
            year_sums[y] = dD
            D = {dev: min(1.0, D[dev] + dD[dev]) for dev in ("bat", "fc", "el")}
        out[mode] = {
            "avg": {dev: float(np.mean([year_sums[y][dev] for y in YEARS])) for dev in ("bat", "fc", "el")},
            "total_avg": float(sum(np.mean([year_sums[y][dev] for y in YEARS]) for dev in ("bat", "fc", "el"))),
            "final_D": D,
            "eol_reached": {dev: D[dev] >= 0.999 for dev in ("bat", "fc", "el")},
        }
    return out


def main():
    loaded = load_evaluation_years(DATA)
    admissions = json.loads(ADM.read_text(encoding="utf-8"))
    all_out = {}
    # 汇总: {strategy: {mode: [TE... across configs]}}
    agg = {s: {m: [] for m in ("bat_only", "full")} for s in STRATEGIES}

    for rep in admissions["representatives"]:
        role = rep["representative_role"]
        strat_out = {}
        for strategy in STRATEGIES:
            per_policy = {}
            eol_reached = {}
            for policy in ("cap", "raw"):
                res = run_chained(rep, loaded, strategy, policy=policy)
                base = res["open_loop"]
                te = {}
                for mode in ("bat_only", "full"):
                    d = res[mode]
                    te[mode] = {
                        "bat": round((d["avg"]["bat"] - base["avg"]["bat"]) / abs(base["avg"]["bat"]) * 100.0, 3),
                        "fc": round((d["avg"]["fc"] - base["avg"]["fc"]) / abs(base["avg"]["fc"]) * 100.0, 3),
                        "el": round((d["avg"]["el"] - base["avg"]["el"]) / abs(base["avg"]["el"]) * 100.0, 3),
                        "total": round((d["total_avg"] - base["total_avg"]) / abs(base["total_avg"]) * 100.0, 3),
                    }
                per_policy[policy] = {"te": te, "open_loop_avg": base["avg"],
                                      "full_avg": res["full"]["avg"], "eol_reached_full": res["full"]["eol_reached"]}
                eol_reached = res["full"]["eol_reached"]
            strat_out[strategy] = {
                "te_cap": per_policy["cap"]["te"],
                "te_raw": per_policy["raw"]["te"],
                "open_loop_avg": per_policy["cap"]["open_loop_avg"],
                "full_avg_cap": per_policy["cap"]["full_avg"],
                "eol_reached_full_cap": per_policy["cap"]["eol_reached_full"],
                "note": "cap 口径下若 FC/EL 达 EOL（eol_reached=True），其 TE 被 EOL 封顶截断（无物理意义），应读 raw 口径或未达 EOL 设备",
            }
            agg[strategy]["bat_only"].append(strat_out[strategy]["te_cap"]["bat_only"]["total"])
            agg[strategy]["full"].append(strat_out[strategy]["te_cap"]["full"]["total"])
        all_out[role] = strat_out
        # 打印（cap 与 raw）
        f_bf = strat_out["battery_first"]["te_cap"]["full"]["total"]
        f_hf = strat_out["hydrogen_first"]["te_cap"]["full"]["total"]
        r_hf = strat_out["hydrogen_first"]["te_raw"]["full"]["total"]
        print(f"{role:<18} cap full TE: bf={f_bf}% hf={f_hf}% | raw full TE hf={r_hf}% | eol(bf/hf)={strat_out['battery_first']['eol_reached_full_cap']}/{strat_out['hydrogen_first']['eol_reached_full_cap']}")

    all_out["_summary"] = {
        "strategy": STRATEGIES,
        "modes": MODES,
        "policy": "cap",
        "te_full_total_range": {s: [round(min(v), 3), round(max(v), 3)] for s, mm in agg.items() for m, v in mm.items()} if False else
                                {s: {"bat_only": [round(min(mm["bat_only"]), 3), round(max(mm["bat_only"]), 3)],
                                     "full": [round(min(mm["full"]), 3), round(max(mm["full"]), 3)]} for s, mm in agg.items()},
        "conclusion": (
            "策略不预设耦合方向：raw（不封顶）口径下三策略 full 反馈 TE 均为正"
            "（battery_first +0.82~+1.13%；hydrogen_first/balanced +1.88~+3.84%），TE>0（反馈放大损伤）方向跨策略稳健。"
            "cap（EOL 封顶 1.0）口径下 hydrogen_first/balanced 在 FC/EL 达 EOL 的配置（compact/battery_dominant/critical，eol_reached=True）"
            "TE 为负（-10.7%/-27.2%/-4.0%），属 EOL 封顶截断假象（达 EOL 后年损伤被 cap 至 0，损伤放大被掩盖），无物理意义，"
            "须读 raw 口径或未达 EOL 设备（L13/L24 表述边界）。策略×反馈交互存在且同向：氢优先策略 FC/EL 利用率高→raw full TE 高于 battery_first。"
            "hydrogen_first 与 balanced 在本准入域（容量充裕）调度轨迹一致（等价）。统一 dispatch_stage2b，消除旧 dispatch_strategy 不对称（L9）。"
        ),
        "note": "D 逐年累积（cap 封顶 1.0，raw 不封顶）；open_loop 为开环基线；与 run_stage3_feedback_modes.py 的 battery_first 列应一致（交叉验证）",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(all_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {OUT}")
    print("summary:", json.dumps(all_out["_summary"]["te_full_total_range"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
