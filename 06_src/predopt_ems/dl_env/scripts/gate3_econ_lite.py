# -*- coding: utf-8 -*-
"""
gate3_econ_lite.py — 第三关正式经济模块 v2（2026-08-09 用户口径，LCOE 升格）

v1 问题（10:45 核验发现）：自造 D_EOL/D_annual 简化寿命与 Stage3 正式口径
（eol_events 事件驱动 + downtime）不自洽（bat 1.58 年 vs 正式 7.9 年）。
v2 修正：**复用 Stage3 正式事件驱动寿命模型**
  eol_events.advance_replacement_events_with_downtime（含替换停机、EOL 内部阈值），
  与 degradation_summary.json 的 lifetime/eol_events 完全同口径。

用户逻辑链（2026-08-09 定义）：
  三年累计损伤增量 → 事件驱动寿命（替换时刻/次数，含 downtime）
    → compute_npc_timed 折现替换成本 → NPC → LCOE

设备级规则（用户口径）：
  - BAT/FC：按事件驱动模型内部 EOL 阈值推寿命（与 Stage3 一致）
  - EL：仅相对损伤指数、无可靠绝对寿命标定 → **不喂入 EL 损伤增量**，
        不触发 EL 替换；EL 替换成本影响通过敏感性情景（--el-repl 开关，
        此时按 Stage3 的 EL 寿命标定加入替换计划，作为上界情景）

用法（DL 环境）：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/gate3_econ_lite.py \
      --damage-increments '{"bat": [...], "fc": [...], "el": [...]}' --role compact
  其中 damage-increments 为全年（或三年链式）逐步损伤增量数组（JSON 列表），
  由 run_gate3 轨迹 compute_damage 生成；也可用 --from-stage3 读现有 degradation_summary
  的年度汇总（快速验证）。
  输出：console + gate3_econ_result.json
"""

import sys, os, json, argparse
import numpy as np

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.join(_SRC, "economics"))
sys.path.insert(0, os.path.join(_SRC, "simulation"))
sys.path.insert(0, _HERE)

from npc_lite import EconomicConfig, compute_npc_timed
from eol_events import advance_replacement_events_with_downtime
from run_gate3_2018 import load_all_configs, load_annual_kwh

ECON_JSON = r"D:\matlab\C\05_config\economic_parameters_v1.0.json"
C1_JSON = r"D:\matlab\C\05_config\mvp_c1_config.json"
STAGE3_SUMMARY = r"D:\matlab\C\07_results\rectification_v2_stage2c\stage3\degradation_summary.json"
OUT = os.path.join(_HERE, "..", "results", "gate3_econ_result.json")

DT_H = 0.25
DOWNTIME_STEPS = {"bat": 96, "fc": 48, "el": 48}   # 24h/12h/12h @15min（与 Stage3 一致）
HORIZON_YEARS = 20


def build_economic_config(cfg) -> EconomicConfig:
    econ = json.load(open(ECON_JSON, encoding="utf-8"))
    c1 = json.load(open(C1_JSON, encoding="utf-8"))
    cap = c1["capacity"]
    return EconomicConfig(
        discount_rate=econ["discount"]["rate"], horizon_years=econ["horizon"]["years"],
        residual_fraction=econ["residual_value"]["fraction_of_capex"],
        exchange_rate=econ["currency"]["exchange_rate_usd_to_cny"],
        pv_unit_cost=econ["capex"]["PV"]["unit_cost_cny_per_kWp"],
        bat_unit_cost=econ["capex"]["BAT"]["unit_cost_cny_per_kWh"],
        pemfc_unit_cost=econ["capex"]["PEMFC"]["unit_cost_cny_per_kW"],
        pemel_unit_cost=econ["capex"]["PEMEL"]["unit_cost_cny_per_kW"],
        h2_tank_unit_cost=econ["capex"]["H2_tank"]["unit_cost_cny_per_kg"],
        bat_replacement_fraction=econ["capex"]["BAT"]["replacement_cost_fraction_of_capex"],
        pemfc_replacement_fraction=econ["capex"]["PEMFC"]["replacement_cost_fraction_of_capex"],
        pemel_replacement_fraction=econ["capex"]["PEMEL"]["replacement_cost_fraction_of_capex"],
        power_electronics_fraction=econ["capex"]["power_electronics"]["cost_fraction_of_total_equipment_capex"],
        power_electronics_life=econ["capex"]["power_electronics"]["design_life_years"],
        installation_fraction=econ["capex"]["installation_and_engineering"]["cost_fraction_of_equipment_capex"],
        gas_conditioning_fraction=econ["capex"]["pemel_gas_conditioning"]["cost_fraction_of_pemel_capex"],
        h2_compression_fraction=econ["capex"]["h2_compression"]["cost_fraction_of_pemel_capex"],
        system_bop_fraction=econ["capex"]["system_bop_aux"]["cost_fraction_of_equipment_capex"],
        tax_fraction=econ["taxes"]["fraction"],
        pv_opex_fraction=econ["opex"]["PV"]["annual_fraction_of_capex"],
        bat_opex_fraction=econ["opex"]["BAT"]["annual_fraction_of_capex"],
        pemfc_opex_fraction=econ["opex"]["PEMFC"]["annual_fraction_of_capex"],
        pemel_opex_fraction=econ["opex"]["PEMEL"]["annual_fraction_of_capex"],
        h2_tank_opex_fraction=econ["opex"]["H2_tank"]["annual_fraction_of_capex"],
        power_electronics_opex_fraction=econ["opex"]["power_electronics"]["annual_fraction_of_capex"],
        general_opex_fraction=econ["opex"]["general"]["annual_fixed_fraction_of_total_capex"],
        pv_kwp=cfg.pv_kwp, bat_kwh=cfg.bat_kwh, pemfc_kw=cfg.pemfc_kw,
        pemel_kw=cfg.pemel_kw, h2_kg=cfg.h2_kg,
    )


def stage3_annual_damage_increments(role, el_repl=False):
    """从 degradation_summary 读年度汇总构造损伤增量（快速验证用）。
    实际第三关应用 run_gate3 轨迹的 compute_damage 逐步增量（--damage-increments）。"""
    d = json.load(open(STAGE3_SUMMARY, encoding="utf-8"))
    r = [x for x in d["representatives"] if x["role"] == role][0]
    ad = r["annual_damage"]   # {'2016': {'dD_bat':..}, ...}
    years = ["2016", "2017", "2018"]
    steps_per_year = 35040
    inc = {"bat": [], "fc": [], "el": []}
    for y in years:
        for dev in ("bat", "fc", "el"):
            v = ad[y][f"dD_{dev}"]
            inc[dev].extend([v / steps_per_year] * steps_per_year)   # 均匀摊到 15min 步
    return inc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--damage-increments", type=str, default=None,
                    help='JSON: {"bat": [...], "fc": [...], "el": [...]} 逐步损伤增量')
    ap.add_argument("--role", type=str, default="compact")
    ap.add_argument("--from-stage3", action="store_true", help="从 degradation_summary 读年度汇总（快速验证）")
    ap.add_argument("--el-repl", action="store_true", help="EL 替换敏感性上界（按 Stage3 EL 寿命加入替换）")
    args = ap.parse_args()

    cfgs = load_all_configs()
    assert args.role in cfgs
    cfg = cfgs[args.role]

    if args.from_stage3:
        inc = stage3_annual_damage_increments(args.role, el_repl=args.el_repl)
        # Stage3 口径：3 年周期 tiling 到 20 年 horizon（事件驱动寿命评估）
        n_cycle = len(next(iter(inc.values())))
        n_horizon = int(HORIZON_YEARS * 8760 / DT_H)
        repeats = int(np.ceil(n_horizon / n_cycle))
        inc = {k: np.tile(v, repeats)[:n_horizon] for k, v in inc.items()}
    else:
        assert args.damage_increments, "需 --damage-increments 或 --from-stage3"
        inc = json.loads(args.damage_increments)
    inc = {k: np.asarray(v, dtype=float) for k, v in inc.items()}

    # 用户口径：EL 无可靠绝对寿命标定 → 默认不触发替换
    devices_feed = {"bat", "fc"} if not args.el_repl else {"bat", "fc", "el"}
    feed = {k: inc[k] for k in devices_feed if k in inc}
    # 三年链式（2016-2018 摊平后）作为寿命评估输入：按用户逻辑链"三年累计→年均"
    res = advance_replacement_events_with_downtime(
        feed, initial_state={k: 0.0 for k in feed},
        dt_hours=DT_H, downtime_steps={k: DOWNTIME_STEPS[k] for k in feed},
        start_time_hours=0.0, initial_replacement_counts={k: 0 for k in feed})

    # 替换年份（小时 → 年；设备名映射到 npc_lite 口径 BAT/PEMFC/PEMEL）
    DEV_MAP = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}
    repl_schedule = {}
    for ev in res.events:
        dev = DEV_MAP.get(ev.device, ev.device)
        repl_schedule.setdefault(dev, []).append(ev.time_hours / 8760.0)
    # 若设备无事件，但用户要上界情景 EL 寿命（--el-repl 已喂入，无需额外处理）

    ecfg = build_economic_config(cfg)
    annual_mwh = load_annual_kwh(cfg) / 1e3
    econ_res = compute_npc_timed(ecfg, repl_schedule, annual_mwh,
                                 label=f"gate3_{args.role}")

    print("=" * 72)
    print(f"第三关正式经济（v2 事件驱动，用户口径）：{args.role}")
    print(f"喂入设备: {sorted(devices_feed)}（EL 默认剔除，--el-repl 为敏感性上界）")
    print(f"替换事件: {[(ev.device, round(ev.time_hours/8760, 2)) for ev in res.events]}")
    print(f"替换年份: { {k: [round(x,2) for x in v] for k,v in repl_schedule.items()} }")
    print(f"年供电量: {annual_mwh:.1f} MWh/yr")
    print(f"NPC = {econ_res['npc_million_cny']:.4f} 百万元 | LCOE = {econ_res['lcoe_cny_per_kwh']:.4f} 元/kWh")
    print(f"替换现值占比 = {econ_res['replacement_pv_cny']/econ_res['npc_cny']*100:.1f}%")
    print("=" * 72)

    out = {"timestamp": np.datetime64("now").astype(str),
           "role": args.role, "method": "eol_events event-driven (Stage3 同口径)",
           "devices_fed": sorted(devices_feed), "el_repl_sensitivity": bool(args.el_repl),
           "replacement_events": [(ev.device, float(ev.time_hours)) for ev in res.events],
           "replacement_schedule_years": {k: [round(x, 4) for x in v]
                                          for k, v in repl_schedule.items()},
           "annual_energy_mwh": annual_mwh,
           "npc_million_cny": econ_res["npc_million_cny"],
           "lcoe_cny_per_kwh": econ_res["lcoe_cny_per_kwh"],
           "replacement_pv_frac": econ_res["replacement_pv_cny"] / econ_res["npc_cny"],
           "replacement_counts": econ_res["replacement_counts"],
           "note": ("用户口径：BAT/FC 按事件驱动模型内部 EOL 阈值推寿命（与 degradation_summary 同口径）；"
                    "EL 无绝对寿命标定默认不触发替换（--el-repl 为上界敏感性）；"
                    "LCOE 为正式口径（非方法演示），第四关三年链式接入同链路")}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n结果已保存: {OUT}")


if __name__ == "__main__":
    main()
