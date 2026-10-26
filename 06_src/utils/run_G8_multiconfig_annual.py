# -*- coding: utf-8 -*-
"""
G8: 多配置年度稳定性验证

VS5方案设计要求跨配置TE稳定性验证。
当前仅C1配置有年度仿真结果，缺少C2/C3/C4配置。
本脚本对4个容量原型(C1-C4)运行完整52周年度仿真，
检查TE方向、变异系数、设备级TE一致性。
"""
import sys, json, csv, time
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

print("=" * 70)
print("G8: 多配置年度稳定性验证 (C1/C2/C3/C4 x 2017)")
print("=" * 70)

# === Configs (same as G2) ===
CONFIGS = {
    'C1': {'PV_kWp': 120, 'BAT_kWh': 135, 'PEMFC_kW': 37.5, 'PEMEL_kW': 35, 'H2_kg': 25, 'desc': 'balanced'},
    'C2': {'PV_kWp': 120, 'BAT_kWh': 180, 'PEMFC_kW': 30, 'PEMEL_kW': 25, 'H2_kg': 25, 'desc': 'BAT-heavy'},
    'C3': {'PV_kWp': 120, 'BAT_kWh': 90,  'PEMFC_kW': 45, 'PEMEL_kW': 45, 'H2_kg': 40, 'desc': 'H2-heavy'},
    'C4': {'PV_kWp': 90,  'BAT_kWh': 90,  'PEMFC_kW': 30, 'PEMEL_kW': 25, 'H2_kg': 10, 'desc': 'critical'},
}

STEPS_PER_DAY = 96
STEPS_PER_WEEK = STEPS_PER_DAY * 7  # 672
WEEKS_PER_YEAR = 52

# === Load base config for EMS parameters (not capacity) ===
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
print(f"2017 data: {len(pv_full)} steps ({len(pv_full)/96:.0f} days)")

# === HPPC data (shared) ===
hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
coalition_full = CoalitionState.from_code('1111')


def make_config(cfg_dict):
    """Create EMSConfig from capacity dict + base EMS parameters."""
    return EMSConfig(
        pv_kwp=cfg_dict['PV_kWp'],
        bat_kwh=cfg_dict['BAT_kWh'],
        pemfc_kw=cfg_dict['PEMFC_kW'],
        pemel_kw=cfg_dict['PEMEL_kW'],
        h2_kg=cfg_dict['H2_kg'],
        soc_min=bat_c['soc_min'], soc_max=bat_c['soc_max'],
        soc_initial=bat_c['soc_initial'],
        h2_min_fraction=h2_c['m_min_fraction'],
        h2_max_fraction=h2_c['m_max_fraction'],
        h2_initial_fraction=h2_c['m_initial_fraction'],
        h2_leakage_per_day=h2_c['leakage_rate_per_day'],
        start_stop_threshold=ems_p['start_stop_threshold_fraction'],
        minimum_run_time_min=float(ems_p['minimum_run_time']),
        critical_load_fraction=ems_p['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'],
        min_stable_load_el=msl['PEMEL'],
        dt_min=15.0,
    )


def run_annual(cfg_dict, label):
    """Run BOL + Feedback annual simulation for one config. Returns result dict."""
    cfg = make_config(cfg_dict)
    bat_rated_kw = cfg_dict['BAT_kWh'] * 0.5
    bat_cap_kwh = cfg_dict['BAT_kWh']

    config_dict = {
        'capacity': cfg_dict, 'battery_constraints': bat_c,
        'ems_parameters': ems_p, 'hydrogen_constraints': h2_c,
        'minimum_stable_load': msl, 'topology': topo,
    }

    hp_bat = interp_bat(bat_fit, {
        'bat_kwh': cfg_dict['BAT_kWh'],
        'bat_voltage_nominal': 3.2 * 48,
        'bat_rated_kw': bat_rated_kw,
    })

    n_weeks = min(WEEKS_PER_YEAR, len(pv_full) // STEPS_PER_WEEK)

    # --- BOL ---
    D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    bol_weekly = []
    for w in range(n_weeks):
        s, e = w * STEPS_PER_WEEK, (w + 1) * STEPS_PER_WEEK
        np.random.seed(42)
        cmds, state = rule_based_dispatch(load_full[s:e], pv_full[s:e], cfg)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                            D_state=D_bol, coalition=coalition_full)
        bol_weekly.append({
            'dD_bat': float(np.sum(dD['dD_bat'])),
            'dD_fc': float(np.sum(dD['dD_fc'])),
            'dD_el': float(np.sum(dD['dD_el'])),
            'lpsp': float(np.sum(state['p_uns']) / max(np.sum(load_full[s:e]), 1e-6)),
        })

    bol_b = sum(wk['dD_bat'] for wk in bol_weekly)
    bol_f = sum(wk['dD_fc'] for wk in bol_weekly)
    bol_e = sum(wk['dD_el'] for wk in bol_weekly)
    bol_t = bol_b + bol_f + bol_e
    bol_lpsp = np.mean([wk['lpsp'] for wk in bol_weekly])

    # --- Feedback ---
    D_state = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    fb_weekly = []
    D_traj = []
    for w in range(n_weeks):
        s, e = w * STEPS_PER_WEEK, (w + 1) * STEPS_PER_WEEK
        D_bat_curr = D_state['bat']
        q_frac, r0, p_max = hp_bat(D_bat_curr)
        bat_p_max_kw = bat_rated_kw * q_frac if D_bat_curr > 0 else None
        bat_cap_kwh_eff = bat_cap_kwh * q_frac if D_bat_curr > 0 else None

        np.random.seed(42)
        cmds, state = rule_based_dispatch(load_full[s:e], pv_full[s:e], cfg,
                                           bat_p_max_kw=bat_p_max_kw,
                                           bat_capacity_kwh=bat_cap_kwh_eff)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                            D_state=D_state, coalition=coalition_full)
        dD_b = float(np.sum(dD['dD_bat']))
        dD_f = float(np.sum(dD['dD_fc']))
        dD_e = float(np.sum(dD['dD_el']))

        D_state['bat'] = min(D_state['bat'] + dD_b, 1.0)
        D_state['fc'] = min(D_state['fc'] + dD_f, 1.0)
        D_state['el'] = min(D_state['el'] + dD_e, 1.0)

        fb_weekly.append({
            'dD_bat': dD_b, 'dD_fc': dD_f, 'dD_el': dD_e,
            'lpsp': float(np.sum(state['p_uns']) / max(np.sum(load_full[s:e]), 1e-6)),
        })
        D_traj.append({'D_bat': D_state['bat'], 'D_fc': D_state['fc'], 'D_el': D_state['el']})

    fb_b = sum(wk['dD_bat'] for wk in fb_weekly)
    fb_f = sum(wk['dD_fc'] for wk in fb_weekly)
    fb_e = sum(wk['dD_el'] for wk in fb_weekly)
    fb_t = fb_b + fb_f + fb_e
    fb_lpsp = np.mean([wk['lpsp'] for wk in fb_weekly])

    ae_b = fb_b - bol_b
    ae_f = fb_f - bol_f
    ae_e = fb_e - bol_e
    ae_t = fb_t - bol_t

    te_b = ae_b / abs(bol_b) if abs(bol_b) > 1e-10 else float('nan')
    te_f = ae_f / abs(bol_f) if abs(bol_f) > 1e-10 else float('nan')
    te_e = ae_e / abs(bol_e) if abs(bol_e) > 1e-10 else float('nan')
    te_t = ae_t / abs(bol_t) if abs(bol_t) > 1e-10 else float('nan')

    # Quarterly TE
    q_te = []
    for q in range(4):
        ws, we = q * 13, (q + 1) * 13
        b_q = sum(bol_weekly[i]['dD_bat'] + bol_weekly[i]['dD_fc'] + bol_weekly[i]['dD_el'] for i in range(ws, we))
        f_q = sum(fb_weekly[i]['dD_bat'] + fb_weekly[i]['dD_fc'] + fb_weekly[i]['dD_el'] for i in range(ws, we))
        q_te.append((f_q - b_q) / abs(b_q) * 100 if abs(b_q) > 1e-10 else 0)

    return {
        'config': label, 'desc': cfg_dict['desc'],
        'n_weeks': n_weeks,
        'bol_total': bol_t, 'bol_bat': bol_b, 'bol_fc': bol_f, 'bol_el': bol_e,
        'fb_total': fb_t, 'fb_bat': fb_b, 'fb_fc': fb_f, 'fb_el': fb_e,
        'ae_total': ae_t, 'ae_bat': ae_b, 'ae_fc': ae_f, 'ae_el': ae_e,
        'te_total': te_t, 'te_bat': te_b, 'te_fc': te_f, 'te_el': te_e,
        'bol_lpsp': bol_lpsp, 'fb_lpsp': fb_lpsp,
        'D_bat_eoy': D_state['bat'], 'D_fc_eoy': D_state['fc'], 'D_el_eoy': D_state['el'],
        'quarterly_te': q_te,
        'D_traj': D_traj,
    }


# ================================================================
# Run all 4 configs
# ================================================================
results = {}
for label, cfg_dict in CONFIGS.items():
    desc = cfg_dict['desc']
    print(f"\n[{label}] {desc}: PV={cfg_dict['PV_kWp']}kWp, BAT={cfg_dict['BAT_kWh']}kWh, "
          f"FC={cfg_dict['PEMFC_kW']}kW, EL={cfg_dict['PEMEL_kW']}kW, H2={cfg_dict['H2_kg']}kg")
    t0 = time.time()
    res = run_annual(cfg_dict, label)
    results[label] = res
    dt = time.time() - t0
    print(f"  BOL={res['bol_total']:.6f}, FB={res['fb_total']:.6f}, TE={res['te_total']*100:+.2f}% ({dt:.1f}s)")
    print(f"  Device TE: BAT={res['te_bat']*100:+.1f}%, FC={res['te_fc']*100:+.1f}%, EL={res['te_el']*100:+.1f}%")
    print(f"  EOY D: BAT={res['D_bat_eoy']:.4f}, FC={res['D_fc_eoy']:.4f}, EL={res['D_el_eoy']:.4f}")
    print(f"  LPSP: BOL={res['bol_lpsp']*100:.3f}%, FB={res['fb_lpsp']*100:.3f}%")
    print(f"  Q-TE: Q1={res['quarterly_te'][0]:+.1f}%, Q2={res['quarterly_te'][1]:+.1f}%, "
          f"Q3={res['quarterly_te'][2]:+.1f}%, Q4={res['quarterly_te'][3]:+.1f}%")

# ================================================================
# Cross-config stability analysis
# ================================================================
print("\n" + "=" * 70)
print("[Analysis] Cross-config stability")
print("=" * 70)

labels = list(CONFIGS.keys())
te_vals = [results[l]['te_total'] for l in labels]
te_mean = np.mean(te_vals)
te_std = np.std(te_vals)
te_cv = te_std / abs(te_mean) if abs(te_mean) > 1e-10 else float('nan')

print(f"\n  TE values: {[f'{l}={te*100:+.2f}%' for l, te in zip(labels, te_vals)]}")
print(f"  TE mean={te_mean*100:+.2f}%, std={te_std*100:.2f}%, CV={te_cv*100:.1f}%")

# Device TE
for dev in ['bat', 'fc', 'el']:
    te_dev = [results[l][f'te_{dev}'] for l in labels]
    dev_pos = all(te > 0 for te in te_dev if not np.isnan(te))
    print(f"  {dev.upper()} TE: {[f'{te*100:+.1f}%' for te in te_dev]} -> {'PASS' if dev_pos else 'MIXED'}")

# ================================================================
# Verification checks
# ================================================================
print("\n[Checks]")
checks = []

# MC01: TE > 0 for all configs
all_pos = all(te > 0 for te in te_vals)
checks.append(('MC01', 'TE > 0 for all 4 configs', all_pos,
               f"TE = {[f'{te*100:+.2f}%' for te in te_vals]}"))

# MC02: TE CV < 50%
checks.append(('MC02', 'TE CV < 50%', te_cv < 0.50,
               f"CV = {te_cv*100:.1f}%"))

# MC03: BAT TE > 0 for all configs
te_bat_all = [results[l]['te_bat'] for l in labels]
checks.append(('MC03', 'BAT TE > 0 all configs', all(te > 0 for te in te_bat_all),
               f"BAT TE = {[f'{te*100:+.1f}%' for te in te_bat_all]}"))

# MC04: FC TE > 0 for all configs
te_fc_all = [results[l]['te_fc'] for l in labels]
checks.append(('MC04', 'FC TE > 0 all configs', all(te > 0 for te in te_fc_all),
               f"FC TE = {[f'{te*100:+.1f}%' for te in te_fc_all]}"))

# MC05: EL TE > 0 for all configs
te_el_all = [results[l]['te_el'] for l in labels]
checks.append(('MC05', 'EL TE > 0 all configs', all(te > 0 for te in te_el_all),
               f"EL TE = {[f'{te*100:+.1f}%' for te in te_el_all]}"))

# MC06: FB LPSP >= BOL LPSP for all configs (degradation should not improve reliability)
lpsp_ok = all(results[l]['fb_lpsp'] >= results[l]['bol_lpsp'] - 0.001 for l in labels)
bol_lpsp_str = [f'{results[l]["bol_lpsp"]*100:.3f}%' for l in labels]
fb_lpsp_str = [f'{results[l]["fb_lpsp"]*100:.3f}%' for l in labels]
checks.append(('MC06', 'FB LPSP >= BOL LPSP all configs', lpsp_ok,
               f'BOL={bol_lpsp_str}, FB={fb_lpsp_str}'))

# MC07: C1 TE matches P1 baseline (6.59%+-0.05)
r_c1 = results['C1']
checks.append(('MC07', 'C1 TE matches P1 baseline (6.59%+-0.05)',
               abs(r_c1['te_total'] - 0.0659) < 0.0005,
               f"C1 TE = {r_c1['te_total']*100:.2f}%"))

# MC08: Q3(夏) TE > Q1(冬) TE for all configs
q3_gt_q1 = all(results[l]['quarterly_te'][2] > results[l]['quarterly_te'][0] for l in labels)
q1_str = [f'{results[l]["quarterly_te"][0]:+.1f}%' for l in labels]
q3_str = [f'{results[l]["quarterly_te"][2]:+.1f}%' for l in labels]
checks.append(('MC08', 'Q3 TE > Q1 TE all configs (seasonal)', q3_gt_q1,
               f'Q1={q1_str}, Q3={q3_str}'))

# MC09: D_bat_EOY > 0.3 for all configs
d_bat_ok = all(results[l]['D_bat_eoy'] > 0.3 for l in labels)
d_bat_str = [f'{results[l]["D_bat_eoy"]:.3f}' for l in labels]
checks.append(('MC09', 'D_bat EOY > 0.3 all configs', d_bat_ok,
               f'D_bat = {d_bat_str}'))

# MC10: EL dominant TE for all configs (EL TE > BAT TE and EL TE > FC TE)
el_dom = all(results[l]['te_el'] > results[l]['te_bat'] and results[l]['te_el'] > results[l]['te_fc']
             for l in labels if not any(np.isnan(results[l][f'te_{d}']) for d in ['bat', 'fc', 'el']))
checks.append(('MC10', 'EL dominant TE all configs', el_dom,
               f'EL > BAT: {[results[l]["te_el"] > results[l]["te_bat"] for l in labels]}, '
               f'EL > FC: {[results[l]["te_el"] > results[l]["te_fc"] for l in labels]}'))

n_pass = sum(1 for _, _, p, _ in checks if p)
n_total = len(checks)
print(f"\n  {'ID':<6} {'Description':<45} {'Result':<8} Details")
print(f"  {'-'*6} {'-'*45} {'-'*8} {'-'*50}")
for cid, desc, passed, details in checks:
    status = 'PASS' if passed else 'FAIL'
    print(f"  {cid:<6} {desc:<45} {status:<8} {details}")
print(f"\n  Total: {n_pass}/{n_total} PASS")

# ================================================================
# Export CSV
# ================================================================
OUT_CSV = PROJECT_ROOT / "07_results" / "annual" / "G8_multiconfig_annual_stability.csv"
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Config', 'Desc', 'PV_kWp', 'BAT_kWh', 'PEMFC_kW', 'PEMEL_kW', 'H2_kg',
                'BOL_total', 'BOL_bat', 'BOL_fc', 'BOL_el',
                'FB_total', 'FB_bat', 'FB_fc', 'FB_el',
                'AE_total', 'AE_bat', 'AE_fc', 'AE_el',
                'TE_total_pct', 'TE_bat_pct', 'TE_fc_pct', 'TE_el_pct',
                'BOL_LPSP_pct', 'FB_LPSP_pct',
                'D_bat_eoy', 'D_fc_eoy', 'D_el_eoy',
                'Q1_TE_pct', 'Q2_TE_pct', 'Q3_TE_pct', 'Q4_TE_pct'])
    for l in labels:
        r = results[l]
        c = CONFIGS[l]
        w.writerow([l, c['desc'], c['PV_kWp'], c['BAT_kWh'], c['PEMFC_kW'], c['PEMEL_kW'], c['H2_kg'],
                    f"{r['bol_total']:.8f}", f"{r['bol_bat']:.8f}", f"{r['bol_fc']:.8f}", f"{r['bol_el']:.8f}",
                    f"{r['fb_total']:.8f}", f"{r['fb_bat']:.8f}", f"{r['fb_fc']:.8f}", f"{r['fb_el']:.8f}",
                    f"{r['ae_total']:+.8f}", f"{r['ae_bat']:+.8f}", f"{r['ae_fc']:+.8f}", f"{r['ae_el']:+.8f}",
                    f"{r['te_total']*100:.2f}", f"{r['te_bat']*100:.2f}", f"{r['te_fc']*100:.2f}", f"{r['te_el']*100:.2f}",
                    f"{r['bol_lpsp']*100:.4f}", f"{r['fb_lpsp']*100:.4f}",
                    f"{r['D_bat_eoy']:.6f}", f"{r['D_fc_eoy']:.6f}", f"{r['D_el_eoy']:.6f}",
                    f"{r['quarterly_te'][0]:.2f}", f"{r['quarterly_te'][1]:.2f}",
                    f"{r['quarterly_te'][2]:.2f}", f"{r['quarterly_te'][3]:.2f}"])
    w.writerow([])
    w.writerow(['=== Verification Checks ==='])
    w.writerow(['Check_ID', 'Description', 'Result', 'Details'])
    for cid, desc, passed, details in checks:
        w.writerow([cid, desc, 'PASS' if passed else 'FAIL', details])
    w.writerow([])
    w.writerow(['=== TE Statistics ==='])
    w.writerow(['Metric', 'Value'])
    w.writerow(['TE_mean', f"{te_mean*100:.2f}%"])
    w.writerow(['TE_std', f"{te_std*100:.2f}%"])
    w.writerow(['TE_CV', f"{te_cv*100:.1f}%"])
    w.writerow(['Checks_pass', f"{n_pass}/{n_total}"])

print(f"\nCSV saved: {OUT_CSV}")
print("=" * 70)
print(f"G8 Complete: {n_pass}/{n_total} checks PASS")
print("=" * 70)
