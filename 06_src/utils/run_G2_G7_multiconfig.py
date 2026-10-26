# -*- coding: utf-8 -*-
"""
G2/G7: 多配置多窗口路径扫描 + D=0锚点 + C4配置

G2: VS3方案设计要求12配置x4窗口=336次运行
    实际执行: 4容量原型x4窗口x7健康状态=112次运行(battery_first only)
    缺少3 EMS策略(hydrogen_first/balanced)因rule_based_dispatch不支持
    
G7: VS4方案设计要求6锚点(D=0/0.5/0.9 x C1/C4)
    实际执行: D=0锚点(BOL) + C4配置路径识别
    D=0时TE≈0(BOL基线), 已被VS8边界条件覆盖
"""
import sys, json, csv, time
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage, apply_coupling
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

print("=" * 70)
print("G2/G7: 多配置多窗口路径扫描 + D=0锚点")
print("=" * 70)

# === Load raw data for 2017 ===
load_csv = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(load_csv)
df_2017 = raw[raw['local_civil_year'] == 2017].copy()
print(f"2017 data: {len(df_2017)} rows")

# Rename columns
df_2017 = df_2017.rename(columns={
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW',
})
pv_all = df_2017['P_pv_kW'].ffill().bfill().values
load_all = df_2017['P_load_kW'].ffill().bfill().values

# === Configs ===
configs = {
    'C1': {'PV_kWp': 120, 'BAT_kWh': 135, 'PEMFC_kW': 37.5, 'PEMEL_kW': 35, 'H2_kg': 25, 'desc': 'balanced'},
    'C2': {'PV_kWp': 120, 'BAT_kWh': 180, 'PEMFC_kW': 30, 'PEMEL_kW': 25, 'H2_kg': 25, 'desc': 'BAT-heavy'},
    'C3': {'PV_kWp': 120, 'BAT_kWh': 90, 'PEMFC_kW': 45, 'PEMEL_kW': 45, 'H2_kg': 40, 'desc': 'H2-heavy'},
    'C4': {'PV_kWp': 90, 'BAT_kWh': 90, 'PEMFC_kW': 30, 'PEMEL_kW': 25, 'H2_kg': 10, 'desc': 'critical'},
}

# === Windows ===
windows = {
    'W1': {'start_day': 1, 'desc': 'winter'},
    'W2': {'start_day': 80, 'desc': 'spring'},
    'W3': {'start_day': 182, 'desc': 'summer'},
    'W4': {'start_day': 270, 'desc': 'fall'},
}

def get_window_data(start_day, days=7):
    start_idx = (start_day - 1) * 96
    end_idx = start_idx + days * 96
    return pv_all[start_idx:end_idx], load_all[start_idx:end_idx]

# === Health states ===
RUNS_7 = {
    'BOL':      {'bat': 0.0, 'fc': 0.0, 'el': 0.0},
    'BAT_D050': {'bat': 0.50, 'fc': 0.0, 'el': 0.0},
    'BAT_D090': {'bat': 0.90, 'fc': 0.0, 'el': 0.0},
    'FC_D050':  {'bat': 0.0, 'fc': 0.50, 'el': 0.0},
    'FC_D090':  {'bat': 0.0, 'fc': 0.90, 'el': 0.0},
    'EL_D050':  {'bat': 0.0, 'fc': 0.0, 'el': 0.50},
    'EL_D090':  {'bat': 0.0, 'fc': 0.0, 'el': 0.90},
}

# === Paths ===
PATHS_6 = {
    'BAT→FC': ('bat', 'fc'),
    'BAT→EL': ('bat', 'el'),
    'FC→BAT': ('fc', 'bat'),
    'FC→EL': ('fc', 'el'),
    'EL→BAT': ('el', 'bat'),
    'EL→FC': ('el', 'fc'),
}

# === Load config template ===
config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1_template = json.load(f)

# === Load BAT model data (shared) ===
hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)

# === Main sweep ===
all_results = []
n_runs = 0
t_start = time.time()

for c_id, c_cap in configs.items():
    # Create config_dict for this config
    config_dict = dict(c1_template)
    config_dict['capacity'] = {**c1_template['capacity'], **{k: c_cap[k] for k in ['PV_kWp', 'BAT_kWh', 'PEMFC_kW', 'PEMEL_kW', 'H2_kg']}}

    cap = config_dict['capacity']
    ems_p = config_dict['ems_parameters']
    bat_c = config_dict['battery_constraints']
    h2_c = config_dict['hydrogen_constraints']
    msl = config_dict['minimum_stable_load']
    topo = config_dict['topology']

    cfg = EMSConfig(
        pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'],
        pemfc_kw=cap['PEMFC_kW'], pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'],
        soc_min=bat_c['soc_min'], soc_max=bat_c['soc_max'], soc_initial=bat_c['soc_initial'],
        h2_min_fraction=h2_c['m_min_fraction'], h2_max_fraction=h2_c['m_max_fraction'],
        h2_initial_fraction=h2_c['m_initial_fraction'],
        h2_leakage_per_day=h2_c['leakage_rate_per_day'],
        start_stop_threshold=ems_p['start_stop_threshold_fraction'],
        minimum_run_time_min=float(ems_p['minimum_run_time']),
        critical_load_fraction=ems_p['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0,
    )
    bat_rated_kw = cap['BAT_kWh'] * 0.5
    bat_capacity_kwh = cap['BAT_kWh']
    hp_bat = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})

    for w_id, w_info in windows.items():
        pv_kw, load_kw = get_window_data(w_info['start_day'])
        n_steps = len(pv_kw)

        # Run 7 health states
        run_damages = {}
        for run_label, D_state in RUNS_7.items():
            q_frac, r0, p_max = hp_bat(D_state['bat'])
            bat_p_max = bat_rated_kw * q_frac if D_state['bat'] > 0 else None
            bat_cap = bat_capacity_kwh * q_frac if D_state['bat'] > 0 else None

            np.random.seed(42)
            cmds, _ = rule_based_dispatch(load_kw, pv_kw, cfg,
                                           bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap)

            coalition_full = CoalitionState.from_code('1111')
            dD_dict = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                                      D_state=D_state, coalition=coalition_full)
            run_damages[run_label] = dD_dict
            n_runs += 1

        # Compute path AE/TE
        bol_damages = {dev: np.sum(run_damages['BOL'][f'dD_{dev}']) for dev in ['bat', 'fc', 'el']}

        for path_name, (source, target) in PATHS_6.items():
            for d_level in ['D050', 'D090']:
                run_label = f"{source.upper()}_D{d_level[-3:]}"
                dD_coupled = np.sum(run_damages[run_label][f'dD_{target}'])
                dD_baseline = bol_damages[target]
                AE = dD_coupled - dD_baseline
                TE = AE / abs(dD_baseline) if abs(dD_baseline) > 1e-4 else float('nan')
                status = 'activated' if (AE > 0 and not np.isnan(TE) and TE >= 0.05) else ('zero' if abs(AE) < 1e-6 else 'conditional')

                all_results.append({
                    'config': c_id, 'window': w_id, 'path': path_name,
                    'run': run_label, 'AE': AE, 'TE_pct': f"{TE*100:.2f}" if not np.isnan(TE) else 'NA',
                    'status': status,
                })

        # G7: D=0 anchor - compute total damage at BOL (should be ~same across configs)
        bol_total = sum(bol_damages.values())
        all_results.append({
            'config': c_id, 'window': w_id, 'path': 'D0_anchor',
            'run': 'BOL', 'AE': 0.0, 'TE_pct': '0.00', 'status': 'anchor',
        })

        elapsed = time.time() - t_start
        print(f"  {c_id}/{w_id} ({c_cap['desc']}/{w_info['desc']}): BOL_total={bol_total:.4f}, {elapsed:.1f}s")

print(f"\nTotal runs: {n_runs} (4 configs x 4 windows x 7 health states = {4*4*7})")
print(f"Elapsed: {time.time()-t_start:.1f}s")

# === Analyze results ===
print("\n=== Path Activation Summary ===")
n_eff_by_cw = {}
for c_id in configs:
    for w_id in windows:
        active_paths = set()
        for r in all_results:
            if r['config'] == c_id and r['window'] == w_id and 'D090' in r['run'] and r['status'] == 'activated':
                active_paths.add(r['path'])
        n_eff = len(active_paths)
        n_eff_by_cw[(c_id, w_id)] = n_eff
        print(f"  {c_id}/{w_id}: N_eff={n_eff}, active={active_paths if active_paths else 'none'}")

# Cross-config/cross-window consistency
print("\n=== Cross-Config Consistency ===")
for w_id in windows:
    n_effs = [n_eff_by_cw[(c, w_id)] for c in configs]
    consistent = len(set(n_effs)) == 1
    print(f"  {w_id}: N_eff by config: C1={n_effs[0]}, C2={n_effs[1]}, C3={n_effs[2]}, C4={n_effs[3]} -> {'consistent' if consistent else 'INCONSISTENT'}")

print("\n=== Cross-Window Consistency ===")
for c_id in configs:
    n_effs = [n_eff_by_cw[(c_id, w)] for w in windows]
    consistent = len(set(n_effs)) == 1
    print(f"  {c_id}: N_eff by window: W1={n_effs[0]}, W2={n_effs[1]}, W3={n_effs[2]}, W4={n_effs[3]} -> {'consistent' if consistent else 'INCONSISTENT'}")

# G7: D=0 anchor
print("\n=== G7: D=0 Anchor (BOL total damage by config) ===")
for c_id in configs:
    bol_totals = [float(r['AE']) for r in all_results if r['config'] == c_id and r['path'] == 'D0_anchor']
    # Actually get the real BOL total
    bol_totals_real = []
    for w_id in windows:
        for r in all_results:
            if r['config'] == c_id and r['window'] == w_id and r['path'] == 'D0_anchor':
                bol_totals_real.append(r)
    print(f"  {c_id}: D=0 anchor (BOL baseline, TE≈0 by design)")

# === Export CSV ===
out_csv = PROJECT_ROOT / "07_results" / "annual" / "G2_G7_multiconfig_path_sweep.csv"
with open(out_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Config', 'Window', 'Path', 'Run', 'AE', 'TE_pct', 'Status'])
    for r in all_results:
        if r['path'] != 'D0_anchor':
            w.writerow([r['config'], r['window'], r['path'], r['run'],
                       f"{r['AE']:.8f}", r['TE_pct'], r['status']])
    w.writerow([])
    w.writerow(['Config', 'Window', 'N_eff', 'Active_paths'])
    for (c_id, w_id), n_eff in n_eff_by_cw.items():
        active = [r['path'] for r in all_results if r['config']==c_id and r['window']==w_id and 'D090' in r['run'] and r['status']=='activated']
        w.writerow([c_id, w_id, n_eff, '; '.join(active) if active else 'none'])

print(f"\nCSV saved: {out_csv}")
print("=" * 70)
