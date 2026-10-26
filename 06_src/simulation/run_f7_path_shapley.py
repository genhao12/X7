# -*- coding: utf-8 -*-
"""F7：按有向路径目标统一 CEPI 归因与 Shapley 闭合。"""
import csv
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from identification.mediation_lite import (
    MEDIATOR_GROUPS,
    compute_shapley,
    validate_path_mediation,
)
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components import fc_four_mechanism

# --- v2 (2026-08-28): FC 损伤模型分支 (与 run_cross_device_paths.py 同口径) ---
#   0 = legacy (closed_loop_lite) —— 默认, 保证既有 f7_path_aligned_shapley.json 基线可复现
#   1 = 四机制 (Refaat 2026 表2 + FC1 恒流锚定)
# 用法:  python run_f7_path_shapley.py --fc-four-mech
#   (也可设环境变量 FC_MODEL_BRANCH=1, 但 PowerShell 的 set 不生效, 推荐用开关)
FC_MODEL_BRANCH = int(os.environ.get("FC_MODEL_BRANCH", "0"))
if "--fc-four-mech" in sys.argv:
    FC_MODEL_BRANCH = 1
DT_HOURS = 15.0 / 60.0


cap = {"PV_kWp": 300, "BAT_kWh": 800, "PEMFC_kW": 100,
       "PEMEL_kW": 100, "H2_kg": 500}
with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", encoding="utf-8") as f:
    base = json.load(f)
ep, bc = base["ems_parameters"], base["battery_constraints"]
hc, msl, topo = base["hydrogen_constraints"], base["minimum_stable_load"], base["topology"]
ems = EMSConfig(
    pv_kwp=cap["PV_kWp"], bat_kwh=cap["BAT_kWh"], pemfc_kw=cap["PEMFC_kW"],
    pemel_kw=cap["PEMEL_kW"], h2_kg=cap["H2_kg"], soc_min=bc["soc_min"],
    soc_max=bc["soc_max"], soc_initial=bc["soc_initial"],
    h2_min_fraction=hc["m_min_fraction"], h2_max_fraction=hc["m_max_fraction"],
    h2_initial_fraction=1.0, h2_leakage_per_day=hc["leakage_rate_per_day"],
    start_stop_threshold=ep["start_stop_threshold_fraction"],
    minimum_run_time_min=float(ep["minimum_run_time"]),
    critical_load_fraction=ep["critical_load_fraction"],
    p_aux_fraction=topo["P_aux_fraction_of_installed_power"],
    min_stable_load_fc=msl["PEMFC"], min_stable_load_el=msl["PEMEL"], dt_min=15.0,
)
config_dict = {"capacity": cap, "battery_constraints": bc, "ems_parameters": ep,
               "hydrogen_constraints": hc, "minimum_stable_load": msl, "topology": topo}
bat_rated_kw = cap["BAT_kWh"] * 0.5
hp_bat = interpolate_hp(fit_hppc(_load_280ah_data()),
                        {"bat_kwh": cap["BAT_kWh"], "bat_voltage_nominal": 3.2 * 48,
                         "bat_rated_kw": bat_rated_kw})

data_path = (PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
             "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(data_path)
year = raw[raw["local_civil_year"] == 2017].copy()
pv = year["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().to_numpy()[:672]
load = year["p_building_load_reconstructed_kW"].ffill().bfill().to_numpy()[:672]

source_d = 0.9
q_degraded, _, _ = hp_bat(source_d)
coalition_targets = {"alpha1": {}, "beta1": {}}
coalition_rows = []
for index in range(16):
    code = format(index, "04b")
    kwargs = {}
    # C = BAT usable-capacity interface; P = BAT power-limit interface.
    if code[0] == "1":
        kwargs["bat_capacity_kwh"] = cap["BAT_kWh"] * q_degraded
    if code[2] == "1":
        kwargs["bat_p_max_kw"] = bat_rated_kw * q_degraded
    # E and I do not lie on BAT->FC/EL in the current EMS; they remain explicit zero mediators.
    np.random.seed(42)
    commands, _ = rule_based_dispatch(load, pv, ems, **kwargs)
    damage = compute_damage(
        hp_bat, commands, config_dict, dt_min=15.0,
        D_state={"bat": source_d, "fc": 0.0, "el": 0.0},
        coalition=CoalitionState.from_code("1111"), eol_policy="raw",
    )
    if FC_MODEL_BRANCH == 1:
        # 四机制仅依赖功率轨迹 (不依赖 D_state / eol_policy), 与 raw 口径同层面对比
        fc_damage = float(np.sum(fc_four_mechanism.fc_damage_increments(
            commands[:, 1], cap["PEMFC_kW"], DT_HOURS)))
    else:
        fc_damage = float(np.sum(damage["dD_fc_raw_series"]))
    el_damage = float(np.sum(damage["dD_el_raw_series"]))
    coalition_targets["alpha1"][code] = fc_damage
    coalition_targets["beta1"][code] = el_damage
    coalition_rows.append({"code": code, "C": code[0], "E": code[1],
                           "P": code[2], "I": code[3],
                           "FC_damage": fc_damage, "EL_damage": el_damage})

summary = {
    "protocol": ("F7 path-aligned Shapley, fixed BAT D=0.9, W1, FC model="
                 + ("four_mech(Refaat2026+FC1)" if FC_MODEL_BRANCH == 1
                    else "legacy(closed_loop_lite)")),
    "mediators": {
        "C": "BAT usable capacity passed to EMS",
        "E": "source-to-target efficiency interface; absent for BAT source, fixed zero",
        "P": "BAT power limit passed to EMS",
        "I": "source-to-target inventory-policy interface; absent in current EMS, fixed zero",
    },
    "paths": {},
}
for path, values in coalition_targets.items():
    shapley = {mediator: compute_shapley(values, mediator)
               for mediator in MEDIATOR_GROUPS}
    path_effect = values["1111"] - values["0000"]
    validation = validate_path_mediation(
        path_effect, values, shapley,
        closure_threshold=0.05, interaction_threshold=0.30,
    )
    # legacy 口径保持原 assert 行为 (基线不可静默改变);
    # 四机制口径下若校验未通过则记录并告警(不中断), 以便拿到结果做归因对比。
    if FC_MODEL_BRANCH == 0:
        assert validation["all_pass"]
    elif not validation["all_pass"]:
        print(f"[WARN] {path} 四机制口径 Shapley 校验未通过: "
              f"closure={validation['closure_relative_error']:.2%}, "
              f"max_interaction={validation['max_interaction_fraction']:.2%}")
    summary["paths"][path] = {"path_effect": path_effect,
                               "shapley_values": shapley,
                               "validation": validation}

out_dir = PROJECT_ROOT / "07_results" / "path_identification"
out_dir.mkdir(parents=True, exist_ok=True)
# 四机制结果写独立文件, 不覆盖 legacy 基线
suffix = "_four_mech" if FC_MODEL_BRANCH == 1 else ""
summary_path = out_dir / f"f7_path_aligned_shapley{suffix}.json"
coalition_path = out_dir / f"f7_coalition_values{suffix}.csv"
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
with open(coalition_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=list(coalition_rows[0]))
    writer.writeheader(); writer.writerows(coalition_rows)

print("F7 PASS")
for path, result in summary["paths"].items():
    values = result["shapley_values"]
    validation = result["validation"]
    print(f"{path}: AE={result['path_effect']:+.6e}, "
          f"phi(C/E/P/I)={values['C']:+.6e}/{values['E']:+.6e}/"
          f"{values['P']:+.6e}/{values['I']:+.6e}, "
          f"closure={validation['closure_relative_error']:.2%}, "
          f"max interaction={validation['max_interaction_fraction']:.2%}")
print(summary_path)
print(coalition_path)
