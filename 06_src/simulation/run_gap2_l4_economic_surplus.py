# -*- coding: utf-8 -*-
"""
run_gap2_l4_economic_surplus — 缺口2补齐: 富余配置上的 L4 经济重评与 regime 相变曲线

背景:
    L4耦合感知EMS在52周窗口上总损伤-41.64%, 但M2/M3经济对比NPC差异近零
    (离散口径被更换阈值淹没)。原因假设: 欠功regime下损伤差异跨越不了
    EOL更换阈值。本实验在LPSP<1%的富余配置(C5/C6/C7 + 诊断用C5_plus)
    上重跑 3年链式(2016-2018) M2 / M3 / M3+L4 三模式, 以连续口径为主、
    时刻贴现口径为鲁棒性检验, 输出:
      - 各配置各模式年损伤率 / LPSP / NPC / LCOE / dNPC(M2-M3_L4)
      - regime相变数据: 配置富余度(LPSP) vs 降损伤与降成本的转化
      - FC四机制模型(缺口1a)与legacy模型的FC损伤对照
      - 电池参数后验(缺口1b)不确定性传播(以实测年EFC驱动)

输出目录: 07_results/gap_closure/
运行: python 06_src/simulation/run_gap2_l4_economic_surplus.py
"""
import sys
import json
import dataclasses
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from components.efc_cycle_model import rainflow_cycles, equivalent_full_cycles
from components import fc_four_mechanism
from components.bat_posterior import fit_posterior, propagate_uncertainty
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from ems.ems_l4_lite import dispatch_l4, L4Config
from simulation.closed_loop_lite import compute_damage
from economics.npc_lite import (load_economic_config, compute_continuous_damage_cost,
                                build_timed_replacement_schedule, compute_npc_timed)

EVAL_CSV = (ROOT / "02_data_processed" / "tradestreet_pipeline"
            / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
FROZEN = ROOT / "05_config" / "frozen_configs_s8.json"
C5_PATH = ROOT / "05_config" / "c5_feasible_config.json"
ECON_PATH = ROOT / "05_config" / "economic_parameters_v1.0.json"
OUT_DIR = ROOT / "07_results" / "gap_closure"

WEEK_STEPS = 672
DT_MIN = 15.0
DT_H = DT_MIN / 60.0
ETA_DEGRADE = 0.10
L4_GENTLE = L4Config(soc_band_narrow_k=0.0, fc_min_run_extend_k=1.0, el_ramp_limit_k=10.0)


def load_configs():
    frozen = json.load(open(FROZEN, encoding="utf-8"))["frozen_configs"]["configs"]
    cfgs = {}
    for name in ("C5_feasible", "C6_compact", "C7_balanced"):
        cfgs[name] = dict(frozen[name]["capacity"])
    # 诊断用超富余点 (非冻结, 仅用于regime曲线外延)
    cfgs["C5_plus_diag"] = {"PV_kWp": 400.0, "BAT_kWh": 800.0, "PEMFC_kW": 100.0,
                            "PEMEL_kW": 100.0, "H2_kg": 1000.0}
    return cfgs


def build_ems_config(cap, base_cfg, soc0, h2_kg0):
    bc = base_cfg["battery_constraints"]
    hc = base_cfg["hydrogen_constraints"]
    ep = base_cfg["ems_parameters"]
    return EMSConfig(
        pv_kwp=cap["PV_kWp"], bat_kwh=cap["BAT_kWh"],
        pemfc_kw=cap["PEMFC_kW"], pemel_kw=cap["PEMEL_kW"], h2_kg=cap["H2_kg"],
        soc_min=bc["soc_min"], soc_max=bc["soc_max"], soc_initial=soc0,
        h2_min_fraction=hc["m_min_fraction"], h2_max_fraction=hc["m_max_fraction"],
        h2_initial_fraction=min(h2_kg0 / cap["H2_kg"], 1.0),
        h2_leakage_per_day=hc["leakage_rate_per_day"],
        start_stop_threshold=ep["start_stop_threshold_fraction"],
        minimum_run_time_min=ep["minimum_run_time"],
        critical_load_fraction=ep["critical_load_fraction"],
        dt_min=DT_MIN,
    )


def run_chain(cap, base_cfg, years, hp, mode, l4_config=None):
    """3年链式周窗口仿真。mode: M2(开环)/M3(闭环反馈)/M3_L4。"""
    dmg_cfg = {**base_cfg, "capacity": cap}   # compute_damage按本配置容量计算
    D = {"bat": 0.0, "fc": 0.0, "el": 0.0}
    soc0 = base_cfg["battery_constraints"]["soc_initial"]
    h2_kg0 = cap["H2_kg"] * base_cfg["hydrogen_constraints"]["m_initial_fraction"]

    totals = {"dD_bat": 0.0, "dD_fc": 0.0, "dD_el": 0.0, "uns_kwh": 0.0,
              "load_kwh": 0.0, "efc": 0.0, "fc_four_D": 0.0, "n_starts": 0}
    per_year = {}

    for year in sorted(years.keys()):
        frame = years[year]
        pv = scale_pv_available(frame["pv_raw_kw"].values, cap["PV_kWp"])
        load = frame["load_kw"].values
        n_weeks = len(load) // WEEK_STEPS
        acc = {k: 0.0 for k in ("dD_bat", "dD_fc", "dD_el", "uns_kwh", "load_kwh", "efc")}
        for w in range(n_weeks):
            s, e = w * WEEK_STEPS, (w + 1) * WEEK_STEPS
            feedback = mode != "M2"
            q, _r0, pmax = hp(D["bat"])
            fc_eff = 1.0 - ETA_DEGRADE * D["fc"] if feedback else None
            fc_pmax = cap["PEMFC_kW"] * (1.0 - ETA_DEGRADE * D["fc"]) if feedback else None
            el_eff = 1.0 - ETA_DEGRADE * D["el"] if feedback else None
            el_pmax = cap["PEMEL_kW"] * (1.0 - ETA_DEGRADE * D["el"]) if feedback else None

            ems = build_ems_config(cap, base_cfg, soc0, h2_kg0)
            common = dict(
                bat_p_max_kw=pmax if feedback else None,
                bat_capacity_kwh=cap["BAT_kWh"] * q if feedback else None,
                fc_eff_frac=fc_eff, fc_p_max_kw=fc_pmax,
                el_eff_frac=el_eff, el_p_max_kw=el_pmax,
            )
            if mode.startswith("M3_L4"):
                cmds, st = dispatch_l4(load[s:e], pv[s:e], ems, D_bat=D["bat"],
                                       l4_config=l4_config, **common)
            else:
                cmds, st = rule_based_dispatch(load[s:e], pv[s:e], ems, **common)

            dmg = compute_damage(hp, cmds, dmg_cfg, dt_min=DT_MIN, D_state=D,
                                 eta_degrade=ETA_DEGRADE, eol_policy="cap")
            soc_trace = st["soc_trace"]
            acc["efc"] += equivalent_full_cycles(rainflow_cycles(np.asarray(soc_trace)))
            # 经济口径用raw损伤(不截断), 反馈口径用cap截断并在D>=1时复位(EOL更换)
            acc["dD_bat"] += float(dmg["dD_bat_raw"])
            acc["dD_fc"] += float(dmg["dD_fc_raw"])
            acc["dD_el"] += float(dmg["dD_el_raw"])
            totals["fc_four_D"] += float(np.sum(fc_four_mechanism.fc_damage_increments(
                cmds[:, 1], cap["PEMFC_kW"], DT_H)))
            p_fc = cmds[:, 1]
            act = p_fc >= cap["PEMFC_kW"] * base_cfg["ems_parameters"]["start_stop_threshold_fraction"]
            totals["n_starts"] += int(np.sum(act[1:] & ~act[:-1]))
            acc["uns_kwh"] += float(np.sum(st["p_uns"])) * DT_H
            acc["load_kwh"] += float(np.sum(load[s:e])) * DT_H

            if feedback:
                for k in D:
                    D[k] = min(1.0, D[k] + float(np.sum(dmg[f"dD_{k}"])))
                    if D[k] >= 1.0:
                        D[k] -= 1.0   # EOL更换: D复位, 损伤继续累积(raw口径)
            soc0 = float(np.asarray(soc_trace)[-1])
            h2_kg0 = float(np.asarray(st["h2_trace"])[-1]) * cap["H2_kg"]

        per_year[year] = dict(acc)
        for k in acc:
            totals[k] += acc[k]

    n_years = len(per_year)
    result = {
        "annual_damage": {
            "BAT": totals["dD_bat"] / n_years,
            "PEMFC": totals["dD_fc"] / n_years,
            "PEMEL": totals["dD_el"] / n_years,
        },
        "lpsp_pct": totals["uns_kwh"] / max(totals["load_kwh"], 1e-9) * 100.0,
        "annual_energy_mwh": (totals["load_kwh"] - totals["uns_kwh"]) / 1000.0 / n_years,
        "annual_efc": totals["efc"] / n_years,
        "fc_four_mech_annual_D": totals["fc_four_D"] / n_years,
        "n_years": n_years,
        "final_D": {k: round(v, 4) for k, v in D.items()},
    }
    return result


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    print("=" * 70)
    print("缺口2补齐: 富余配置 L4 经济重评 + regime相变 (C5/C6/C7/C5_plus)")
    print("=" * 70)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base_cfg = json.load(open(C5_PATH, encoding="utf-8"))
    configs = load_configs()
    years = load_evaluation_years(str(EVAL_CSV))
    print(f"[0] 输入契约: 3年 x 15min, 年份 {sorted(years.keys())}")

    fit_bat = fit_hppc(_load_280ah_data())
    econ_base = load_economic_config(str(ECON_PATH), str(C5_PATH))
    print(f"[0] 经济基准: 贴现率 {econ_base.discount_rate}, 期限 {econ_base.horizon_years}年")

    rows = []
    for name, cap in configs.items():
        # 电池hp映射按本配置额定功率(0.5C)构建
        hp = interpolate_hp(fit_bat, {"bat_kwh": cap["BAT_kWh"],
                                      "bat_rated_kw": cap["BAT_kWh"] * 0.5})
        for mode, l4 in (("M2", None), ("M3", None), ("M3_L4", None), ("M3_L4_gentle", L4_GENTLE)):
            if mode == "M3_L4_gentle" and name != "C5_feasible":
                continue  # 温和参数仅做C5对照
            print(f"  -> {name} / {mode} ...", flush=True)
            r = run_chain(cap, base_cfg, years, hp, mode, l4_config=l4)
            econ = dataclasses.replace(
                econ_base, pv_kwp=cap["PV_kWp"], bat_kwh=cap["BAT_kWh"],
                pemfc_kw=cap["PEMFC_kW"], pemel_kw=cap["PEMEL_kW"], h2_kg=cap["H2_kg"])
            cont = compute_continuous_damage_cost(
                econ, r["annual_damage"], r["annual_energy_mwh"], label=f"{name}_{mode}")
            sched = build_timed_replacement_schedule(
                r["annual_damage"], horizon_years=econ.horizon_years,
                power_electronics_life=econ.power_electronics_life)
            timed = compute_npc_timed(
                econ, sched, r["annual_energy_mwh"], label=f"{name}_{mode}")
            row = {
                "config": name, "mode": mode,
                "lpsp_pct": round(r["lpsp_pct"], 4),
                "annual_D_bat": round(r["annual_damage"]["BAT"], 5),
                "annual_D_fc": round(r["annual_damage"]["PEMFC"], 5),
                "annual_D_el": round(r["annual_damage"]["PEMEL"], 5),
                "annual_efc": round(r["annual_efc"], 1),
                "fc_four_mech_annual_D": round(r["fc_four_mech_annual_D"], 5),
                "n_fc_starts_3yr": r["n_years"],
                "npc_cont_million_cny": cont.get("npc_million_cny"),
                "lcoe_cont_cny_kwh": cont.get("lcoe_cny_per_kwh"),
                "npc_timed_million_cny": timed.get("npc_million_cny"),
                "lcoe_timed_cny_kwh": timed.get("lcoe_cny_per_kwh"),
            }
            row["n_fc_starts_3yr"] = r  # placeholder replaced below
            rows.append((row, r))
            print(f"     LPSP={row['lpsp_pct']:.3f}%  D/年 "
                  f"BAT={row['annual_D_bat']:.4f} FC={row['annual_D_fc']:.4f} "
                  f"EL={row['annual_D_el']:.4f}  EFC/年={row['annual_efc']:.0f}  "
                  f"NPC(连续)={row['npc_cont_million_cny']:.3f} 百万元")

    # ---- 汇总 ----
    summary_rows = []
    for row, r in rows:
        summary_rows.append(row)
        row.pop("n_fc_starts_3yr", None)
    # dNPC/damage转化 (M2 - M3_L4)
    pivot = {(x["config"], x["mode"]): x for x in summary_rows}
    deltas = []
    for name in configs:
        m2 = pivot.get((name, "M2"))
        m3l4 = pivot.get((name, "M3_L4"))
        m3 = pivot.get((name, "M3"))
        if not (m2 and m3l4):
            continue
        tot = lambda x: x["annual_D_bat"] + x["annual_D_fc"] + x["annual_D_el"]
        deltas.append({
            "config": name, "lpsp_pct": m2["lpsp_pct"],
            "damage_reduction_pct": round((1 - tot(m3l4) / tot(m2)) * 100, 2),
            "dnpc_million_cny": round(m2["npc_cont_million_cny"] - m3l4["npc_cont_million_cny"], 4),
            "dnpc_pct": round((1 - m3l4["npc_cont_million_cny"] / m2["npc_cont_million_cny"]) * 100, 4),
            "dnpc_timed_million_cny": round(m2["npc_timed_million_cny"] - m3l4["npc_timed_million_cny"], 4),
            "replacements_m2": None, "replacements_m3l4": None,
        })
    # 更换次数 (时刻口径)
    for d in deltas:
        m2 = pivot[(d["config"], "M2")]
        m3l4 = pivot[(d["config"], "M3_L4")]
        sched2 = build_timed_replacement_schedule(
            {"BAT": m2["annual_D_bat"], "PEMFC": m2["annual_D_fc"], "PEMEL": m2["annual_D_el"]},
            horizon_years=20)
        sched4 = build_timed_replacement_schedule(
            {"BAT": m3l4["annual_D_bat"], "PEMFC": m3l4["annual_D_fc"], "PEMEL": m3l4["annual_D_el"]},
            horizon_years=20)
        d["replacements_m2"] = {k: len(v) for k, v in sched2.items() if isinstance(v, list)}
        d["replacements_m3l4"] = {k: len(v) for k, v in sched4.items() if isinstance(v, list)}

    # ---- 电池后验传播 (以C5 M3实测年EFC) ----
    c5m3 = pivot[("C5_feasible", "M3")]
    post = fit_posterior()
    prop = propagate_uncertainty(c5m3["annual_efc"], post)

    # ---- FC四机制 vs legacy 对照 ----
    fc_cmp = {
        "C5_M3_legacy_annual_D": c5m3["annual_D_fc"],
        "C5_M3_four_mech_annual_D": c5m3["fc_four_mech_annual_D"],
        "ratio": round(c5m3["fc_four_mech_annual_D"] / max(c5m3["annual_D_fc"], 1e-12), 3),
        "fc1_replay": fc_four_mechanism.replay_fc1_constant(),
    }

    out = {
        "experiment": "gap2_l4_economic_surplus + gap1 integration",
        "description": "富余配置(C5/C6/C7/C5_plus_diag) 3年链式 M2/M3/M3_L4 经济重评; "
                       "连续口径为主, 时刻贴现口径为鲁棒性检验; 含FC四机制与电池后验集成",
        "results": summary_rows,
        "deltas_m2_vs_m3l4": deltas,
        "fc_four_mechanism_comparison": fc_cmp,
        "battery_posterior_propagation": prop,
    }
    with open(OUT_DIR / "gap2_l4_economic_surplus.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)

    # CSV
    keys = ["config", "mode", "lpsp_pct", "annual_D_bat", "annual_D_fc", "annual_D_el",
            "annual_efc", "fc_four_mech_annual_D", "npc_cont_million_cny",
            "lcoe_cont_cny_kwh", "npc_timed_million_cny", "lcoe_timed_cny_kwh"]
    with open(OUT_DIR / "gap2_l4_economic_surplus.csv", "w", encoding="utf-8-sig") as f:
        f.write(",".join(keys) + "\n")
        for row in summary_rows:
            f.write(",".join(str(row.get(k, "")) for k in keys) + "\n")

    # ---- regime相变图 ----
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    lp = [d["lpsp_pct"] for d in deltas]
    axes[0].plot(lp, [d["damage_reduction_pct"] for d in deltas], "o-", color="#C55A11")
    axes[0].set_xlabel("LPSP (%)"); axes[0].set_ylabel("Total damage reduction (%)")
    axes[0].set_title("L4 damage reduction vs surplus"); axes[0].invert_xaxis()
    axes[1].plot(lp, [d["dnpc_million_cny"] for d in deltas], "s-", color="#1F3864")
    axes[1].set_xlabel("LPSP (%)"); axes[1].set_ylabel("dNPC M2-M3L4 (M CNY, continuous)")
    axes[1].set_title("Economic value vs surplus"); axes[1].invert_xaxis()
    modes = ["M2", "M3", "M3_L4"]
    w = 0.25
    xs = np.arange(len(deltas))
    for i, m in enumerate(modes):
        vals = [pivot[(d["config"], m)]["annual_D_bat"] + pivot[(d["config"], m)]["annual_D_fc"]
                + pivot[(d["config"], m)]["annual_D_el"] for d in deltas]
        axes[2].bar(xs + (i - 1) * w, vals, w, label=m)
    axes[2].set_xticks(xs); axes[2].set_xticklabels([d["config"] for d in deltas], rotation=15)
    axes[2].set_ylabel("Annual total damage D/yr"); axes[2].legend(); axes[2].set_title("Annual damage by config")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "gap2_regime_curve.png", dpi=150)
    print(f"\n[输出] {OUT_DIR / 'gap2_l4_economic_surplus.json'}")
    print(f"[输出] {OUT_DIR / 'gap2_l4_economic_surplus.csv'}")
    print(f"[输出] {OUT_DIR / 'gap2_regime_curve.png'}")

    print("\n[regime相变] config LPSP% 降损伤% dNPC连续(百万元) 更换次数(M2->M3L4)")
    for d in deltas:
        print(f"  {d['config']:>14s} {d['lpsp_pct']:.3f} {d['damage_reduction_pct']:>7.2f} "
              f"{d['dnpc_million_cny']:>9.4f}  {d['replacements_m2']} -> {d['replacements_m3l4']}")
    print("\n[FC四机制] legacy {:.5f} D/年 vs 四机制 {:.5f} D/年 (比值 {:.2f})".format(
        fc_cmp["C5_M3_legacy_annual_D"], fc_cmp["C5_M3_four_mech_annual_D"], fc_cmp["ratio"]))
    print("[电池后验] a_d {:.4e} (95%CI {:.2e}~{:.2e}); 年D先验CI宽度 {:.1f}% -> 后验 {:.1f}%".format(
        prop["posterior_fit"]["a_d_mean"],
        *prop["posterior_fit"]["a_d_ci95"],
        (prop["prior"]["annual_D_ci95"][1] - prop["prior"]["annual_D_ci95"][0]) / prop["prior"]["annual_D_mean"] * 100,
        (prop["posterior"]["annual_D_ci95"][1] - prop["posterior"]["annual_D_ci95"][0]) / prop["posterior"]["annual_D_mean"] * 100))
    print("\n状态: 缺口实验完成")


if __name__ == "__main__":
    main()
