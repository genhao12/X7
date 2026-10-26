"""Cross-device degradation coupling paths: α₂(EL→FC), β₂(FC→EL), γ₁(FC→BAT), γ₂(EL→BAT).

For each path, runs counterfactual dispatch with source device degraded
vs BOL, and measures target device damage difference.

Paths:
  α₂: EL degradation → FC damage (via H₂ production change)
  β₂: FC degradation → EL damage (via H₂ consumption change)
  γ₁: FC degradation → BAT damage (via FC power and H₂ balance change)
  γ₂: EL degradation → BAT damage (via EL power and H₂ balance change)
"""
import sys
import json
import numpy as np
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.closed_loop_lite import compute_damage
from components import fc_four_mechanism

# --- v2 (2026-08-28): FC 损伤模型分支, 与 VS7 的 fc_model_branch 同口径 ---
#   0 = legacy (closed_loop_lite) —— **默认**, 保证已发布基线 α1=+24.33% 可复现
#   1 = 四机制 (Refaat 2026 表2 + FC1 恒流 23.5uV/1000h 锚定)
# 切换方式 (不改动代码/不破坏基线):
#   ★推荐(最稳妥, 不依赖 shell 语义): python run_cross_device_paths.py --fc-four-mech
#   环境变量(必须与 python 同一条命令/同一会话, 否则静默跑成 legacy):
#     cmd  : set FC_MODEL_BRANCH=1 && python run_cross_device_paths.py
#     ps   : $env:FC_MODEL_BRANCH=1; python run_cross_device_paths.py
#     bash : FC_MODEL_BRANCH=1 python run_cross_device_paths.py
#   ⚠ 坑: PowerShell 的 `set` 是 Set-Variable 的别名, **不会**设置环境变量。
import os
FC_MODEL_BRANCH = int(os.environ.get("FC_MODEL_BRANCH", "0"))
DT_HOURS = 15.0 / 60.0   # 15min 准静态步长
from simulation.run_stage3_full_analysis import (
    build_c1_config, run_three_year_cycle, extract_commands,
    compute_annual_damage, hp_bat_default,
)


# ── Degraded dispatch functions ──

def dispatch_with_degraded_bat(
    cfg_ems: Stage2BEMSConfig,
    load: np.ndarray,
    pv: np.ndarray,
    D_bat: float = 0.0,
    strategy: str = "battery_first",
) -> "DispatchResult":
    """Dispatch with degraded BAT parameters (alpha1/beta1 paths).

    BAT degradation model (mirrors hp_bat_lite):
      bat_kwh_eff = bat_kwh * (1 - 0.20 * D_bat)      — capacity shrink
      bat_c_rate_eff = bat_c_rate * (1 - 0.05 * D_bat) — power derating
    """
    bat_kwh_eff = cfg_ems.bat_kwh * (1.0 - 0.20 * D_bat)
    bat_c_rate_eff = cfg_ems.bat_c_rate * (1.0 - 0.05 * D_bat)

    degraded_cfg = Stage2BEMSConfig(
        pv_kwp=cfg_ems.pv_kwp,
        bat_kwh=bat_kwh_eff,
        pemfc_kw=cfg_ems.pemfc_kw,
        pemel_kw=cfg_ems.pemel_kw,
        h2_kg=cfg_ems.h2_kg,
        soc_min=cfg_ems.soc_min,
        soc_max=cfg_ems.soc_max,
        dt_min=cfg_ems.dt_min,
        h2_min_fraction=cfg_ems.h2_min_fraction,
        h2_max_fraction=cfg_ems.h2_max_fraction,
        h2_leakage_per_day=cfg_ems.h2_leakage_per_day,
        min_stable_load_fc=cfg_ems.min_stable_load_fc,
        min_stable_load_el=cfg_ems.min_stable_load_el,
        minimum_run_steps=cfg_ems.minimum_run_steps,
        standby_kw=cfg_ems.standby_kw,
        pv_aux_fraction=cfg_ems.pv_aux_fraction,
        bat_aux_fraction=cfg_ems.bat_aux_fraction,
        fc_aux_fraction=cfg_ems.fc_aux_fraction,
        el_aux_fraction=cfg_ems.el_aux_fraction,
        eta_fc=cfg_ems.eta_fc,
        eta_el=cfg_ems.eta_el,
        bat_c_rate=bat_c_rate_eff,
    )
    initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
    return dispatch_stage2b(
        load, pv, degraded_cfg,
        strategy=strategy, initial_state=initial,
    )


def dispatch_with_degraded_fc(
    cfg_ems: Stage2BEMSConfig,
    load: np.ndarray,
    pv: np.ndarray,
    D_fc: float = 0.0,
    strategy: str = "battery_first",
) -> "DispatchResult":
    """Dispatch with degraded FC parameters.

    FC degradation model:
      eta_fc_eff = eta_fc * (1 - 0.10 * D_fc)  — efficiency loss
      pemfc_kw_eff = pemfc_kw * (1 - 0.05 * D_fc)  — power derating
    """
    eta_fc_eff = cfg_ems.eta_fc * (1.0 - 0.10 * D_fc)
    pemfc_kw_eff = cfg_ems.pemfc_kw * (1.0 - 0.05 * D_fc)

    degraded_cfg = Stage2BEMSConfig(
        pv_kwp=cfg_ems.pv_kwp,
        bat_kwh=cfg_ems.bat_kwh,
        pemfc_kw=pemfc_kw_eff,
        pemel_kw=cfg_ems.pemel_kw,
        h2_kg=cfg_ems.h2_kg,
        soc_min=cfg_ems.soc_min,
        soc_max=cfg_ems.soc_max,
        dt_min=cfg_ems.dt_min,
        h2_min_fraction=cfg_ems.h2_min_fraction,
        h2_max_fraction=cfg_ems.h2_max_fraction,
        h2_leakage_per_day=cfg_ems.h2_leakage_per_day,
        min_stable_load_fc=cfg_ems.min_stable_load_fc,
        min_stable_load_el=cfg_ems.min_stable_load_el,
        minimum_run_steps=cfg_ems.minimum_run_steps,
        standby_kw=cfg_ems.standby_kw,
        pv_aux_fraction=cfg_ems.pv_aux_fraction,
        bat_aux_fraction=cfg_ems.bat_aux_fraction,
        fc_aux_fraction=cfg_ems.fc_aux_fraction,
        el_aux_fraction=cfg_ems.el_aux_fraction,
        eta_fc=eta_fc_eff,
        eta_el=cfg_ems.eta_el,
        bat_c_rate=cfg_ems.bat_c_rate,
    )
    initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
    return dispatch_stage2b(
        load, pv, degraded_cfg,
        strategy=strategy, initial_state=initial,
    )


def dispatch_with_degraded_el(
    cfg_ems: Stage2BEMSConfig,
    load: np.ndarray,
    pv: np.ndarray,
    D_el: float = 0.0,
    strategy: str = "battery_first",
) -> "DispatchResult":
    """Dispatch with degraded EL parameters.

    EL degradation model:
      eta_el_eff = eta_el * (1 - 0.10 * D_el)  — efficiency loss
      pemel_kw_eff = pemel_kw * (1 - 0.05 * D_el)  — power derating
    """
    eta_el_eff = cfg_ems.eta_el * (1.0 - 0.10 * D_el)
    pemel_kw_eff = cfg_ems.pemel_kw * (1.0 - 0.05 * D_el)

    degraded_cfg = Stage2BEMSConfig(
        pv_kwp=cfg_ems.pv_kwp,
        bat_kwh=cfg_ems.bat_kwh,
        pemfc_kw=cfg_ems.pemfc_kw,
        pemel_kw=pemel_kw_eff,
        h2_kg=cfg_ems.h2_kg,
        soc_min=cfg_ems.soc_min,
        soc_max=cfg_ems.soc_max,
        dt_min=cfg_ems.dt_min,
        h2_min_fraction=cfg_ems.h2_min_fraction,
        h2_max_fraction=cfg_ems.h2_max_fraction,
        h2_leakage_per_day=cfg_ems.h2_leakage_per_day,
        min_stable_load_fc=cfg_ems.min_stable_load_fc,
        min_stable_load_el=cfg_ems.min_stable_load_el,
        minimum_run_steps=cfg_ems.minimum_run_steps,
        standby_kw=cfg_ems.standby_kw,
        pv_aux_fraction=cfg_ems.pv_aux_fraction,
        bat_aux_fraction=cfg_ems.bat_aux_fraction,
        fc_aux_fraction=cfg_ems.fc_aux_fraction,
        el_aux_fraction=cfg_ems.el_aux_fraction,
        eta_fc=cfg_ems.eta_fc,
        eta_el=eta_el_eff,
        bat_c_rate=cfg_ems.bat_c_rate,
    )
    initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
    return dispatch_stage2b(
        load, pv, degraded_cfg,
        strategy=strategy, initial_state=initial,
    )


# ── Path analysis ──
# All six paths use the SAME counterfactual framework and unit:
#   source device degraded at D_source (0.3/0.5/0.7) vs BOL,
#   target device damage difference normalised as
#   effect_pct = (damage_on - damage_off) / |damage_off| * 100
# (percent contribution relative to the target device's own degradation).

PATHS = {
    "alpha1": {"source": "bat", "target": "fc", "D_source": 0.5},
    "beta1":  {"source": "bat", "target": "el", "D_source": 0.5},
    "alpha2": {"source": "el", "target": "fc", "D_source": 0.5},
    "beta2":  {"source": "fc", "target": "el", "D_source": 0.5},
    "gamma1": {"source": "fc", "target": "bat", "D_source": 0.5},
    "gamma2": {"source": "el", "target": "bat", "D_source": 0.5},
}


def compute_cross_device_effect(
    cfg_ems: Stage2BEMSConfig,
    load: np.ndarray,
    pv: np.ndarray,
    c1_config: dict,
    path_name: str,
    D_source: float = 0.5,
    fc_branch: int = FC_MODEL_BRANCH,
) -> dict:
    """Compute cross-device coupling effect for one path.

    Returns target device damage with source degraded vs BOL,
    and the coupling effect (difference).
    """
    path_def = PATHS[path_name]
    source = path_def["source"]
    target = path_def["target"]

    # Dispatch with source degraded
    if source == "bat":
        result_on = dispatch_with_degraded_bat(cfg_ems, load, pv, D_bat=D_source)
        result_off = dispatch_with_degraded_bat(cfg_ems, load, pv, D_bat=0.0)
    elif source == "fc":
        result_on = dispatch_with_degraded_fc(cfg_ems, load, pv, D_fc=D_source)
        result_off = dispatch_with_degraded_fc(cfg_ems, load, pv, D_fc=0.0)
    elif source == "el":
        result_on = dispatch_with_degraded_el(cfg_ems, load, pv, D_el=D_source)
        result_off = dispatch_with_degraded_el(cfg_ems, load, pv, D_el=0.0)

    # Compute target damage for both
    dD_on = compute_annual_damage(
        result_on.commands, c1_config,
        D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
    )
    dD_off = compute_annual_damage(
        result_off.commands, c1_config,
        D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
    )

    target_key = f"dD_{target}"
    damage_on = dD_on[target_key]
    damage_off = dD_off[target_key]

    # v2: 四机制 FC 分支 —— 仅当 target 为 fc 时按四机制重算损伤。
    # 两分支都不依赖 D_state (四机制输入仅为功率/额定/dt), 故与 BOL 对照同口径。
    if target == "fc" and fc_branch == 1:
        damage_on = float(np.sum(fc_four_mechanism.fc_damage_increments(
            result_on.commands[:, 1], cfg_ems.pemfc_kw, DT_HOURS)))
        damage_off = float(np.sum(fc_four_mechanism.fc_damage_increments(
            result_off.commands[:, 1], cfg_ems.pemfc_kw, DT_HOURS)))

    effect = damage_on - damage_off

    # Also compute source damage for reference
    source_key = f"dD_{source}"
    source_damage_on = dD_on[source_key]
    source_damage_off = dD_off[source_key]

    # FC start-stop counts
    fc_threshold = cfg_ems.pemfc_kw * cfg_ems.min_stable_load_fc
    fc_on_cmds = result_on.commands[:, 1]
    fc_off_cmds = result_off.commands[:, 1]
    fc_starts_on = int(np.sum((fc_on_cmds[1:] >= fc_threshold) & (fc_off_cmds[:-1] < fc_threshold)))
    fc_starts_off = int(np.sum((fc_off_cmds[1:] >= fc_threshold) & (fc_off_cmds[:-1] < fc_threshold)))

    # H₂ balance comparison
    h2_on = result_on.trace["h2_kg"]
    h2_off = result_off.trace["h2_kg"]
    h2_diff_mean = float(np.mean(h2_on - h2_off))
    h2_diff_final = float(h2_on[-1] - h2_off[-1])

    return {
        "path": path_name,
        "source": source,
        "target": target,
        "D_source": D_source,
        "target_damage_on": damage_on,
        "target_damage_off": damage_off,
        "effect": effect,
        "effect_pct": effect / max(abs(damage_off), 1e-12) * 100,
        "source_damage_on": source_damage_on,
        "source_damage_off": source_damage_off,
        "fc_starts_on": fc_starts_on,
        "fc_starts_off": fc_starts_off,
        "h2_diff_mean_kg": h2_diff_mean,
        "h2_diff_final_kg": h2_diff_final,
    }


def run_all_paths(
    prepared: dict,
    cfg_ems: Stage2BEMSConfig,
    c1_config: dict,
    D_levels: list = [0.3, 0.5, 0.7],
    fc_branch: int = FC_MODEL_BRANCH,
) -> dict:
    """Run all 4 cross-device paths at multiple D levels."""
    year = 2017
    load = prepared[year]["load"]
    pv = prepared[year]["pv"]

    all_results = {}
    for path_name in PATHS:
        path_results = {}
        for D in D_levels:
            result = compute_cross_device_effect(
                cfg_ems, load, pv, c1_config, path_name, D_source=D,
                fc_branch=fc_branch,
            )
            path_results[str(D)] = result
        all_results[path_name] = path_results

    return all_results


def print_path_report(role: str, all_results: dict):
    """Print formatted cross-device path report."""
    print(f"\n{'='*80}")
    print(f"Cross-device paths: {role}")
    print(f"FC model: {'四机制 (four-mechanism)' if FC_MODEL_BRANCH == 1 else 'legacy (closed_loop_lite)'}")
    print(f"{'='*80}")

    for path_name, path_results in all_results.items():
        path_def = PATHS[path_name]
        print(f"\n  {path_name} ({path_def['source']}→{path_def['target']}):")
        print(f"  {'D_source':>8s} | {'target_off':>10s} | {'target_on':>10s} | "
              f"{'effect':>10s} | {'effect%':>8s} | {'ΔH₂_final':>10s} | {'ΔFC_starts':>10s}")
        print(f"  {'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}")

        for D_str, r in path_results.items():
            print(f"  {D_str:>8s} | {r['target_damage_off']:10.6f} | {r['target_damage_on']:10.6f} | "
                  f"{r['effect']:+10.6f} | {r['effect_pct']:+7.1f}% | "
                  f"{r['h2_diff_final_kg']:+10.3f} | "
                  f"{r['fc_starts_on']-r['fc_starts_off']:+10d}")


if __name__ == "__main__":
    # 命令行开关优先于环境变量 (避免 set/export 漏设后静默跑成 legacy)
    if "--fc-four-mech" in sys.argv:
        FC_MODEL_BRANCH = 1
    config_path = ROOT / "05_config" / "rectification_v2_stage2c.json"
    data_path = ROOT / "02_data_processed" / "tradestreet_pipeline" / \
        "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    admissions_path = ROOT / "07_results" / "rectification_v2_stage2c" / "search" / "verified_admissions.json"

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    admissions = json.loads(admissions_path.read_text(encoding="utf-8"))
    loaded = load_evaluation_years(data_path)

    all_path_results = {}
    for rep in admissions["representatives"]:
        role = rep["representative_role"]
        pv_kwp = float(rep["pv_kwp"])
        bat_kwh = float(rep["bat_kwh"])
        pemfc_kw = float(rep["pemfc_kw"])
        pemel_kw = float(rep["pemel_kw"])
        h2_kg = float(rep["h2_kg"])

        cfg_ems = Stage2BEMSConfig(
            pv_kwp=pv_kwp, bat_kwh=bat_kwh, pemfc_kw=pemfc_kw,
            pemel_kw=pemel_kw, h2_kg=h2_kg,
        )
        prepared = {}
        for year, frame in loaded.items():
            pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
            pv_scaled = scale_pv_available(pv_raw, nominal_kw=pv_kwp)
            prepared[year] = {
                "load": frame["load_kw"].to_numpy(dtype=float),
                "pv": pv_scaled,
            }

        c1_config = build_c1_config(rep)
        results = run_all_paths(prepared, cfg_ems, c1_config,
                                fc_branch=FC_MODEL_BRANCH)
        print_path_report(role, results)
        all_path_results[role] = results

    # Save results
    # 四机制结果写独立文件, 绝不覆盖 legacy 基线
    suffix = "_four_mech" if FC_MODEL_BRANCH == 1 else ""
    output_path = (ROOT / "07_results" / "rectification_v2_stage2c" / "stage3"
                   / f"cross_device_paths{suffix}.json")
    # Make JSON-serializable
    serializable = {}
    for role, paths in all_path_results.items():
        serializable[role] = {}
        for path_name, D_results in paths.items():
            serializable[role][path_name] = {}
            for D_str, r in D_results.items():
                serializable[role][path_name][D_str] = {
                    k: (float(v) if isinstance(v, (np.floating, float)) else
                        int(v) if isinstance(v, (np.integer, int)) else v)
                    for k, v in r.items()
                }

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to: {output_path}")
