# -*- coding: utf-8 -*-
"""
G8补充: HF/BL策略多配置年度稳定性验证

修复"仅battery_first有多配置验证"限制。
对C1-C4配置运行hydrogen_first和balanced策略的年度仿真。
"""
import sys, json, csv, time
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

# Import dispatch_strategy from run_ems_comparison
# We'll set module-level variables before calling it
import importlib.util
spec = importlib.util.spec_from_file_location("ems_cmp",
    PROJECT_ROOT / "06_src" / "simulation" / "run_ems_comparison.py")
ems_cmp = importlib.util.module_from_spec(spec)
# Don't exec yet - we'll set variables first

print("=" * 70)
print("G8补充: HF/BL策略多配置年度稳定性验证")
print("=" * 70)

# === Configs ===
CONFIGS = {
    'C1': {'PV_kWp': 120, 'BAT_kWh': 135, 'PEMFC_kW': 37.5, 'PEMEL_kW': 35, 'H2_kg': 25, 'desc': 'balanced'},
    'C2': {'PV_kWp': 120, 'BAT_kWh': 180, 'PEMFC_kW': 30, 'PEMEL_kW': 25, 'H2_kg': 25, 'desc': 'BAT-heavy'},
    'C3': {'PV_kWp': 120, 'BAT_kWh': 90,  'PEMFC_kW': 45, 'PEMEL_kW': 45, 'H2_kg': 40, 'desc': 'H2-heavy'},
    'C4': {'PV_kWp': 90,  'BAT_kWh': 90,  'PEMFC_kW': 30, 'PEMEL_kW': 25, 'H2_kg': 10, 'desc': 'critical'},
}

STEPS_PER_WEEK = 96 * 7
WEEKS_PER_YEAR = 52

# === Load base config ===
with open(PROJECT_ROOT / "05_config" / "mvp_c1_config.json", 'r', encoding='utf-8') as f:
    c1_base = json.load(f)

ems_p = c1_base['ems_parameters']
bat_c = c1_base['battery_constraints']
h2_c = c1_base['hydrogen_constraints']
msl = c1_base['minimum_stable_load']
topo = c1_base['topology']

# === Load 2017 data ===
csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path)
df_2017 = raw[raw['local_civil_year'] == 2017].copy()
df_2017 = df_2017.rename(columns={
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW',
})
pv_full = df_2017['P_pv_kW'].ffill().bfill().values
load_full = df_2017['P_load_kW'].ffill().bfill().values
print(f"2017 data: {len(pv_full)} steps")

# === Shared BAT model data ===
hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
coalition_full = CoalitionState.from_code('1111')


def run_annual_strategy(cfg_dict, strategy, label):
    """Run BOL + Feedback annual simulation for one config x strategy. Returns result dict."""
    # Set up module-level variables for dispatch_strategy
    cap = cfg_dict
    ems_cfg = EMSConfig(
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
    config_dict_local = {
        'capacity': cap, 'battery_constraints': bat_c,
        'ems_parameters': ems_p, 'hydrogen_constraints': h2_c,
        'minimum_stable_load': msl, 'topology': topo,
    }
    hp_bat = interp_bat(bat_fit, {
        'bat_kwh': cap['BAT_kWh'],
        'bat_voltage_nominal': 3.2 * 48,
        'bat_rated_kw': bat_rated_kw,
    })

    # Set module-level variables for dispatch_strategy
    ems_cmp.ems_cfg = ems_cfg
    ems_cmp.bat_rated_kw = bat_rated_kw
    ems_cmp.bat_cap_kwh = bat_capacity_kwh
    ems_cmp.config_dict = config_dict_local
    ems_cmp.hp_bat = hp_bat
    ems_cmp.coal = coalition_full

    n_weeks = min(WEEKS_PER_YEAR, len(pv_full) // STEPS_PER_WEEK)

    # --- BOL ---
    D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    bol_b = bol_f = bol_e = 0.0
    bol_lpsp_list = []
    for w in range(n_weeks):
        s, e = w * STEPS_PER_WEEK, (w + 1) * STEPS_PER_WEEK
        cmds, state = ems_cmp.dispatch_strategy(load_full[s:e], pv_full[s:e], strategy)
        dD = compute_damage(hp_bat, cmds, config_dict_local, dt_min=15.0,
                            D_state=D_bol, coalition=coalition_full)
        bol_b += float(np.sum(dD['dD_bat']))
        bol_f += float(np.sum(dD['dD_fc']))
        bol_e += float(np.sum(dD['dD_el']))
        bol_lpsp_list.append(float(np.sum(state['p_uns']) / max(np.sum(load_full[s:e]), 1e-6)))
    bol_t = bol_b + bol_f + bol_e
    bol_lpsp = np.mean(bol_lpsp_list)

    # --- Feedback ---
    D_state = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    fb_b = fb_f = fb_e = 0.0
    fb_lpsp_list = []
    for w in range(n_weeks):
        s, e = w * STEPS_PER_WEEK, (w + 1) * STEPS_PER_WEEK
        D_bat_curr = D_state['bat']
        q_frac, r0, p_max = hp_bat(D_bat_curr)
        bat_p_max = bat_rated_kw * q_frac if D_bat_curr > 0 else None
        bat_cap = bat_capacity_kwh * q_frac if D_bat_curr > 0 else None

        cmds, state = ems_cmp.dispatch_strategy(load_full[s:e], pv_full[s:e], strategy,
                                                 bat_p_max=bat_p_max, bat_cap_eff=bat_cap)
        dD = compute_damage(hp_bat, cmds, config_dict_local, dt_min=15.0,
                            D_state=D_state, coalition=coalition_full)
        dD_b = float(np.sum(dD['dD_bat']))
        dD_f = float(np.sum(dD['dD_fc']))
        dD_e = float(np.sum(dD['dD_el']))

        D_state['bat'] = min(D_state['bat'] + dD_b, 1.0)
        D_state['fc'] = min(D_state['fc'] + dD_f, 1.0)
        D_state['el'] = min(D_state['el'] + dD_e, 1.0)

        fb_b += dD_b
        fb_f += dD_f
        fb_e += dD_e
        fb_lpsp_list.append(float(np.sum(state['p_uns']) / max(np.sum(load_full[s:e]), 1e-6)))

    fb_t = fb_b + fb_f + fb_e
    fb_lpsp = np.mean(fb_lpsp_list)

    ae_b = fb_b - bol_b
    ae_f = fb_f - bol_f
    ae_e = fb_e - bol_e
    ae_t = fb_t - bol_t

    te_b = ae_b / abs(bol_b) if abs(bol_b) > 1e-10 else float('nan')
    te_f = ae_f / abs(bol_f) if abs(bol_f) > 1e-10 else float('nan')
    te_e = ae_e / abs(bol_e) if abs(bol_e) > 1e-10 else float('nan')
    te_t = ae_t / abs(bol_t) if abs(bol_t) > 1e-10 else float('nan')

    return {
        'config': label, 'strategy': strategy,
        'bol_total': bol_t, 'bol_bat': bol_b, 'bol_fc': bol_f, 'bol_el': bol_e,
        'fb_total': fb_t, 'fb_bat': fb_b, 'fb_fc': fb_f, 'fb_el': fb_e,
        'ae_total': ae_t, 'ae_bat': ae_b, 'ae_fc': ae_f, 'ae_el': ae_e,
        'te_total': te_t, 'te_bat': te_b, 'te_fc': te_f, 'te_el': te_e,
        'bol_lpsp': bol_lpsp, 'fb_lpsp': fb_lpsp,
        'D_bat_eoy': D_state['bat'], 'D_fc_eoy': D_state['fc'], 'D_el_eoy': D_state['el'],
    }


# Execute the module to make dispatch_strategy available
spec.loader.exec_module(ems_cmp)

# === Run all configs x strategies ===
results = []
strategies = ['hydrogen_first', 'balanced']

for label, cfg_dict in CONFIGS.items():
    for strat in strategies:
        desc = cfg_dict['desc']
        print(f"\n[{label}/{strat}] {desc}...")
        t0 = time.time()
        res = run_annual_strategy(cfg_dict, strat, label)
        results.append(res)
        dt = time.time() - t0
        print(f"  TE={res['te_total']*100:+.2f}% (BAT={res['te_bat']*100:+.1f}%, "
              f"FC={res['te_fc']*100:+.1f}%, EL={res['te_el']*100:+.1f}%) ({dt:.1f}s)")
        print(f"  EOY D: BAT={res['D_bat_eoy']:.4f}, LPSP={res['fb_lpsp']*100:.3f}%")

# === Also load BF results from G8 for comparison ===
bf_csv = PROJECT_ROOT / "07_results" / "annual" / "G8_multiconfig_annual_stability.csv"
bf_results = {}
if bf_csv.exists():
    with open(bf_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('Config') and row.get('TE_total_pct'):
                bf_results[row['Config']] = float(row['TE_total_pct'])

# === Analysis ===
print("\n" + "=" * 70)
print("[Analysis] Cross-strategy x cross-config TE comparison")
print("=" * 70)

print(f"\n{'Config':<6} {'BF TE%':>8} {'HF TE%':>8} {'BL TE%':>8} {'HF-BF':>8} {'BL-BF':>8}")
print(f"{'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
for label in CONFIGS:
    bf_te = bf_results.get(label, float('nan'))
    hf_res = [r for r in results if r['config'] == label and r['strategy'] == 'hydrogen_first']
    bl_res = [r for r in results if r['config'] == label and r['strategy'] == 'balanced']
    hf_te = hf_res[0]['te_total'] * 100 if hf_res else float('nan')
    bl_te = bl_res[0]['te_total'] * 100 if bl_res else float('nan')
    diff_hf = hf_te - bf_te
    diff_bl = bl_te - bf_te
    print(f"{label:<6} {bf_te:>+8.2f} {hf_te:>+8.2f} {bl_te:>+8.2f} {diff_hf:>+8.2f} {diff_bl:>+8.2f}")

# Checks
print("\n[Checks]")
checks = []

# MC11: HF TE > 0 for all configs
hf_tes = [r['te_total'] for r in results if r['strategy'] == 'hydrogen_first']
checks.append(('MC11', 'HF TE > 0 all configs', all(te > 0 for te in hf_tes),
               f"HF TE = {[f'{te*100:+.2f}%' for te in hf_tes]}"))

# MC12: BL TE > 0 for all configs
bl_tes = [r['te_total'] for r in results if r['strategy'] == 'balanced']
checks.append(('MC12', 'BL TE > 0 all configs', all(te > 0 for te in bl_tes),
               f"BL TE = {[f'{te*100:+.2f}%' for te in bl_tes]}"))

# MC13: HF TE CV < 50%
hf_cv = np.std(hf_tes) / abs(np.mean(hf_tes)) if abs(np.mean(hf_tes)) > 1e-10 else float('nan')
checks.append(('MC13', 'HF TE CV < 50%', hf_cv < 0.50, f"CV = {hf_cv*100:.1f}%"))

# MC14: BL TE CV < 50%
bl_cv = np.std(bl_tes) / abs(np.mean(bl_tes)) if abs(np.mean(bl_tes)) > 1e-10 else float('nan')
checks.append(('MC14', 'BL TE CV < 50%', bl_cv < 0.50, f"CV = {bl_cv*100:.1f}%"))

# MC15: EL dominant TE for all HF configs
hf_el_dom = all(r['te_el'] > r['te_bat'] and r['te_el'] > r['te_fc'] for r in results if r['strategy'] == 'hydrogen_first')
checks.append(('MC15', 'EL dominant TE all HF configs', hf_el_dom, ''))

# MC16: EL dominant TE for all BL configs
bl_el_dom = all(r['te_el'] > r['te_bat'] and r['te_el'] > r['te_fc'] for r in results if r['strategy'] == 'balanced')
checks.append(('MC16', 'EL dominant TE all BL configs', bl_el_dom, ''))

# MC17: BF > HF > BL (strategy ordering consistent, BF highest)
bf_vals = [bf_results.get(l, 0) for l in CONFIGS]
hf_vals = [r['te_total']*100 for r in results if r['strategy'] == 'hydrogen_first']
bl_vals = [r['te_total']*100 for r in results if r['strategy'] == 'balanced']
bf_gt_hf = all(b > h for b, h in zip(bf_vals, hf_vals))
hf_gt_bl = all(h > b for h, b in zip(hf_vals, bl_vals))
checks.append(('MC17', 'BF > HF > BL all configs', bf_gt_hf and hf_gt_bl,
               f"BF>HF: {bf_gt_hf}, HF>BL: {hf_gt_bl}"))

n_pass = sum(1 for _, _, p, _ in checks if p)
n_total = len(checks)
print(f"\n  {'ID':<6} {'Description':<40} {'Result':<8}")
print(f"  {'-'*6} {'-'*40} {'-'*8}")
for cid, desc, passed, details in checks:
    print(f"  {cid:<6} {desc:<40} {'PASS' if passed else 'FAIL':<8} {details}")
print(f"\n  Total: {n_pass}/{n_total} PASS")

# === Export CSV ===
OUT_CSV = PROJECT_ROOT / "07_results" / "annual" / "G8_HF_BL_multiconfig_stability.csv"
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Config', 'Strategy', 'TE_total_pct', 'TE_bat_pct', 'TE_fc_pct', 'TE_el_pct',
                'BOL_LPSP_pct', 'FB_LPSP_pct', 'D_bat_eoy', 'D_fc_eoy', 'D_el_eoy'])
    for r in results:
        w.writerow([r['config'], r['strategy'],
                    f"{r['te_total']*100:.2f}", f"{r['te_bat']*100:.2f}",
                    f"{r['te_fc']*100:.2f}", f"{r['te_el']*100:.2f}",
                    f"{r['bol_lpsp']*100:.4f}", f"{r['fb_lpsp']*100:.4f}",
                    f"{r['D_bat_eoy']:.6f}", f"{r['D_fc_eoy']:.6f}", f"{r['D_el_eoy']:.6f}"])
    w.writerow([])
    w.writerow(['=== Verification Checks ==='])
    w.writerow(['Check_ID', 'Description', 'Result', 'Details'])
    for cid, desc, passed, details in checks:
        w.writerow([cid, desc, 'PASS' if passed else 'FAIL', details])

print(f"\nCSV saved: {OUT_CSV}")
print("=" * 70)
print(f"G8 HF/BL Complete: {n_pass}/{n_total} checks PASS")
print("=" * 70)
