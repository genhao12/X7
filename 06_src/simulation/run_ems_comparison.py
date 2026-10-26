# -*- coding: utf-8 -*-
"""多EMS策略对比 — battery_first / hydrogen_first / balanced"""
import sys, json, csv
import numpy as np
import pandas as pd
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
plt.rcParams.update({'axes.unicode_minus': False, 'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10, 'xtick.labelsize': 9,
    'ytick.labelsize': 9, 'legend.fontsize': 8, 'axes.linewidth': 0.8, 'lines.linewidth': 1.5,
    'grid.alpha': 0.25, 'axes.spines.top': False, 'axes.spines.right': False})

C_BF = '#3288BD'; C_HF = '#D73027'; C_BL = '#66C2A5'
STRATEGIES = ['battery_first', 'hydrogen_first', 'balanced']
STRAT_COLORS = {'battery_first': C_BF, 'hydrogen_first': C_HF, 'balanced': C_BL}
OUT_DIR = PROJECT_ROOT / "07_results" / "ems_comparison"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "ems_strategy_comparison.csv"
OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "ems_strategy_comparison.png"
SPW = 672; WPY = 52

from ems.ems_bf_lite import EMSConfig
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

cfg = json.load(open(PROJECT_ROOT / "05_config" / "mvp_c1_config.json", encoding='utf-8'))
cap = cfg['capacity']; ep = cfg['ems_parameters']; bc = cfg['battery_constraints']
hc = cfg['hydrogen_constraints']; msl = cfg['minimum_stable_load']; topo = cfg['topology']
LHV_H2 = 33.3; ETA_FC = 0.50; ETA_EL = 0.70

ems_cfg = EMSConfig(pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'], pemfc_kw=cap['PEMFC_kW'],
    pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'], soc_min=bc['soc_min'], soc_max=bc['soc_max'],
    soc_initial=bc['soc_initial'], h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=hc['m_initial_fraction'], h2_leakage_per_day=hc['leakage_rate_per_day'],
    start_stop_threshold=ep['start_stop_threshold_fraction'], minimum_run_time_min=float(ep['minimum_run_time']),
    critical_load_fraction=ep['critical_load_fraction'], p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
bat_rated_kw = cap['BAT_kWh'] * 0.5; bat_cap_kwh = cap['BAT_kWh']
config_dict = {'capacity': cap, 'battery_constraints': bc, 'ems_parameters': ep,
    'hydrogen_constraints': hc, 'minimum_stable_load': msl, 'topology': topo}
hp_bat = interp_bat(fit_hppc(_load_280ah_data()), {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})
coal = CoalitionState.from_code('1111')

print("=" * 70); print("多EMS策略对比 — battery_first / hydrogen_first / balanced"); print("=" * 70)

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df = raw[raw['local_civil_year'] == 2017].copy()
df = df.rename(columns={'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW', 'p_building_load_reconstructed_kW': 'P_load_kW'})
df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill(); df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
pv_full = df['P_pv_kW'].values; load_full = df['P_load_kW'].values
n_weeks = min(WPY, len(pv_full) // SPW)

def dispatch_strategy(load_kw, pv_kw, strategy, bat_p_max=None, bat_cap_eff=None):
    """通用规则式EMS调度，支持三种策略。"""
    n = len(load_kw); dt_h = ems_cfg.dt_min / 60.0
    bat_kwh_eff = bat_cap_eff if bat_cap_eff is not None else bat_cap_kwh
    fc_rated = ems_cfg.pemfc_kw; el_rated = ems_cfg.pemel_kw
    h2_max = ems_cfg.h2_kg; h2_min = ems_cfg.h2_kg * ems_cfg.h2_min_fraction
    p_bat_max = bat_p_max if bat_p_max is not None else bat_rated_kw
    installed = ems_cfg.pv_kwp + bat_kwh_eff * 0.5 + fc_rated + el_rated
    p_aux = ems_cfg.p_aux_fraction * installed
    fc_min_stable = fc_rated * ems_cfg.min_stable_load_fc
    el_min_stable = el_rated * ems_cfg.min_stable_load_el
    p_bat = np.zeros(n); p_fc = np.zeros(n); p_el = np.zeros(n)
    p_uns = np.zeros(n); p_cur = np.zeros(n)
    soc = ems_cfg.soc_initial; h2 = ems_cfg.h2_kg * ems_cfg.h2_initial_fraction

    for t in range(n):
        if h2 > h2_min:
            h2 = max(h2 - ems_cfg.h2_leakage_per_day * h2 / 24.0 * dt_h, h2_min)
        p_net = pv_kw[t] - load_kw[t] - p_aux

        if p_net >= 0:
            # === PV过剩 ===
            remaining = p_net
            if strategy == 'battery_first':
                # BAT charge -> EL -> curtail
                charge_cap = max(0.0, (ems_cfg.soc_max - soc) * bat_kwh_eff / dt_h)
                p_ch = min(remaining, p_bat_max, charge_cap)
                p_bat[t] = -p_ch; soc = min(soc + p_ch * dt_h / bat_kwh_eff, ems_cfg.soc_max); remaining -= p_ch
                if remaining > el_min_stable and h2 < h2_max:
                    h2_cap = max(0.0, (h2_max - h2) * LHV_H2 / (ETA_EL * dt_h))
                    p_el_raw = min(remaining, el_rated, h2_cap)
                    if p_el_raw < el_min_stable: p_el_raw = 0.0
                    p_el[t] = p_el_raw; h2 = min(h2 + p_el_raw * dt_h * ETA_EL / LHV_H2, h2_max); remaining -= p_el_raw
            elif strategy == 'hydrogen_first':
                # EL -> BAT charge -> curtail
                if remaining > el_min_stable and h2 < h2_max:
                    h2_cap = max(0.0, (h2_max - h2) * LHV_H2 / (ETA_EL * dt_h))
                    p_el_raw = min(remaining, el_rated, h2_cap)
                    if p_el_raw < el_min_stable: p_el_raw = 0.0
                    p_el[t] = p_el_raw; h2 = min(h2 + p_el_raw * dt_h * ETA_EL / LHV_H2, h2_max); remaining -= p_el_raw
                charge_cap = max(0.0, (ems_cfg.soc_max - soc) * bat_kwh_eff / dt_h)
                p_ch = min(remaining, p_bat_max, charge_cap)
                p_bat[t] = -p_ch; soc = min(soc + p_ch * dt_h / bat_kwh_eff, ems_cfg.soc_max); remaining -= p_ch
            else:  # balanced
                # Split 50/50
                half = remaining / 2.0
                charge_cap = max(0.0, (ems_cfg.soc_max - soc) * bat_kwh_eff / dt_h)
                p_ch = min(half, p_bat_max, charge_cap)
                p_bat[t] = -p_ch; soc = min(soc + p_ch * dt_h / bat_kwh_eff, ems_cfg.soc_max); remaining -= p_ch
                if remaining > el_min_stable and h2 < h2_max:
                    h2_cap = max(0.0, (h2_max - h2) * LHV_H2 / (ETA_EL * dt_h))
                    p_el_raw = min(remaining, el_rated, h2_cap)
                    if p_el_raw < el_min_stable: p_el_raw = 0.0
                    p_el[t] = p_el_raw; h2 = min(h2 + p_el_raw * dt_h * ETA_EL / LHV_H2, h2_max); remaining -= p_el_raw
            p_cur[t] = max(0.0, remaining)
        else:
            # === PV不足 ===
            deficit = -p_net; remaining = deficit
            if strategy == 'battery_first':
                # BAT discharge -> FC -> unserved
                dis_cap = max(0.0, (soc - ems_cfg.soc_min) * bat_kwh_eff / dt_h)
                p_dis = min(deficit, p_bat_max, dis_cap)
                p_bat[t] = p_dis; soc = max(soc - p_dis * dt_h / bat_kwh_eff, ems_cfg.soc_min); remaining -= p_dis
                if remaining > fc_min_stable and h2 > h2_min:
                    h2_avail = max(0.0, (h2 - h2_min) * ETA_FC * LHV_H2 / dt_h)
                    p_fc_raw = min(remaining, fc_rated, h2_avail)
                    if p_fc_raw < fc_min_stable: p_fc_raw = 0.0
                    p_fc[t] = p_fc_raw; h2 = max(h2 - p_fc_raw * dt_h / (ETA_FC * LHV_H2), h2_min); remaining -= p_fc_raw
            elif strategy == 'hydrogen_first':
                # FC -> BAT discharge -> unserved
                if remaining > fc_min_stable and h2 > h2_min:
                    h2_avail = max(0.0, (h2 - h2_min) * ETA_FC * LHV_H2 / dt_h)
                    p_fc_raw = min(remaining, fc_rated, h2_avail)
                    if p_fc_raw < fc_min_stable: p_fc_raw = 0.0
                    p_fc[t] = p_fc_raw; h2 = max(h2 - p_fc_raw * dt_h / (ETA_FC * LHV_H2), h2_min); remaining -= p_fc_raw
                dis_cap = max(0.0, (soc - ems_cfg.soc_min) * bat_kwh_eff / dt_h)
                p_dis = min(remaining, p_bat_max, dis_cap)
                p_bat[t] = p_dis; soc = max(soc - p_dis * dt_h / bat_kwh_eff, ems_cfg.soc_min); remaining -= p_dis
            else:  # balanced
                # Split 50/50
                half = deficit / 2.0
                dis_cap = max(0.0, (soc - ems_cfg.soc_min) * bat_kwh_eff / dt_h)
                p_dis = min(half, p_bat_max, dis_cap)
                p_bat[t] = p_dis; soc = max(soc - p_dis * dt_h / bat_kwh_eff, ems_cfg.soc_min); remaining -= p_dis
                if remaining > fc_min_stable and h2 > h2_min:
                    h2_avail = max(0.0, (h2 - h2_min) * ETA_FC * LHV_H2 / dt_h)
                    p_fc_raw = min(remaining, fc_rated, h2_avail)
                    if p_fc_raw < fc_min_stable: p_fc_raw = 0.0
                    p_fc[t] = p_fc_raw; h2 = max(h2 - p_fc_raw * dt_h / (ETA_FC * LHV_H2), h2_min); remaining -= p_fc_raw
            p_uns[t] = max(0.0, remaining)
    commands = np.column_stack([p_bat, p_fc, p_el])
    state = {'p_uns': p_uns, 'p_cur': p_cur, 'soc_trace': np.zeros(n), 'h2_trace': np.zeros(n)}
    return commands, state

def run_annual(strategy):
    """运行年度仿真(BOL+Feedback)，返回结果字典。"""
    # BOL
    D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}; wb = []
    for w in range(n_weeks):
        s = w * SPW; e = s + SPW; pv_w = pv_full[s:e]; load_w = load_full[s:e]
        np.random.seed(42)
        cmds, state = dispatch_strategy(load_w, pv_w, strategy)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state={'bat':0.0,'fc':0.0,'el':0.0}, coalition=coal)
        wb.append({'dD_bat': float(np.sum(dD['dD_bat'])), 'dD_fc': float(np.sum(dD['dD_fc'])), 'dD_el': float(np.sum(dD['dD_el'])),
                   'lpsp': float(np.sum(state['p_uns'])/max(np.sum(load_w),1e-6))})
    bol_b = sum(w['dD_bat'] for w in wb); bol_f = sum(w['dD_fc'] for w in wb); bol_e = sum(w['dD_el'] for w in wb)
    bol = bol_b + bol_f + bol_e; bol_lp = np.mean([w['lpsp'] for w in wb])

    # Feedback
    D = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}; wf = []
    for w in range(n_weeks):
        s = w * SPW; e = s + SPW; pv_w = pv_full[s:e]; load_w = load_full[s:e]
        D_bat = D['bat']
        if D_bat > 0:
            q, r0, p_max = hp_bat(D_bat); bat_p = bat_rated_kw * q; bat_c = bat_cap_kwh * q
        else:
            bat_p = None; bat_c = None
        np.random.seed(42)
        cmds, state = dispatch_strategy(load_w, pv_w, strategy, bat_p_max=bat_p, bat_cap_eff=bat_c)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D, coalition=coal)
        dD_b = float(np.sum(dD['dD_bat'])); dD_f = float(np.sum(dD['dD_fc'])); dD_e = float(np.sum(dD['dD_el']))
        D['bat'] = min(D['bat'] + dD_b, 1.0); D['fc'] = min(D['fc'] + dD_f, 1.0); D['el'] = min(D['el'] + dD_e, 1.0)
        wf.append({'dD_bat': dD_b, 'dD_fc': dD_f, 'dD_el': dD_e, 'lpsp': float(np.sum(state['p_uns'])/max(np.sum(load_w),1e-6)),
                   'D_bat': D['bat'], 'D_fc': D['fc'], 'D_el': D['el']})
    fb_b = sum(w['dD_bat'] for w in wf); fb_f = sum(w['dD_fc'] for w in wf); fb_e = sum(w['dD_el'] for w in wf)
    fb = fb_b + fb_f + fb_e; fb_lp = np.mean([w['lpsp'] for w in wf])
    ae = fb - bol; te = ae / abs(bol) if abs(bol) > 1e-10 else 0
    return {'strategy': strategy, 'bol': bol, 'bol_bat': bol_b, 'bol_fc': bol_f, 'bol_el': bol_e,
            'fb': fb, 'fb_bat': fb_b, 'fb_fc': fb_f, 'fb_el': fb_e,
            'ae': ae, 'te': te, 'bol_lpsp': bol_lp, 'fb_lpsp': fb_lp,
            'D_bat_eoy': D['bat'], 'D_fc_eoy': D['fc'], 'D_el_eoy': D['el'], 'weekly_fb': wf}

print("\n运行三种策略年度仿真...")
results = {}
for strat in STRATEGIES:
    print(f"\n  [{strat}]...")
    r = run_annual(strat); results[strat] = r
    print(f"  BOL={r['bol']:.6f}, FB={r['fb']:.6f}, TE={r['te']*100:+.2f}%, LPSP(BOL)={r['bol_lpsp']*100:.3f}%, LPSP(FB)={r['fb_lpsp']*100:.3f}%")
    print(f"  D_eoy: BAT={r['D_bat_eoy']:.4f}, FC={r['D_fc_eoy']:.4f}, EL={r['D_el_eoy']:.4f}")

print("\n" + "=" * 70); print("对比分析"); print("=" * 70)
print(f"\n  {'Strategy':<20} {'BOL':<12} {'FB':<12} {'TE(%)':<10} {'LPSP_BOL':<10} {'LPSP_FB':<10} {'D_BAT':<8} {'D_FC':<8} {'D_EL':<8}")
print(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
for s in STRATEGIES:
    r = results[s]
    print(f"  {s:<20} {r['bol']:<12.6f} {r['fb']:<12.6f} {r['te']*100:<10.2f} {r['bol_lpsp']*100:<10.3f} {r['fb_lpsp']*100:<10.3f} {r['D_bat_eoy']:<8.4f} {r['D_fc_eoy']:<8.4f} {r['D_el_eoy']:<8.4f}")

checks = []
bf = results['battery_first']; hf = results['hydrogen_first']; bl = results['balanced']
checks.append(('EM01', 'All TE > 0', all(results[s]['te'] > 0 for s in STRATEGIES),
               str({s: f"{results[s]['te']*100:+.2f}%" for s in STRATEGIES})))
checks.append(('EM02', 'BF TE matches P1 (6.59%+-0.05)', abs(bf['te']-0.0659)<0.0005, f"BF TE={bf['te']*100:.2f}%"))
checks.append(('EM03', 'HF LPSP != BF LPSP (strategy matters)', abs(hf['bol_lpsp']-bf['bol_lpsp'])>0.001,
               f"HF={hf['bol_lpsp']*100:.3f}% vs BF={bf['bol_lpsp']*100:.3f}%"))
checks.append(('EM04', 'BL LPSP != BF LPSP (strategy matters)', abs(bl['bol_lpsp']-bf['bol_lpsp'])>0.001,
               f"BL={bl['bol_lpsp']*100:.3f}% vs BF={bf['bol_lpsp']*100:.3f}%"))
checks.append(('EM05', 'All strategies reach FC EOL (D_fc=1.0)', all(results[s]['D_fc_eoy']>=0.99 for s in STRATEGIES),
               str({s: f"{results[s]['D_fc_eoy']:.3f}" for s in STRATEGIES})))
np_chk = sum(1 for _,_,p,_ in checks if p); nt_chk = len(checks)
print(f"\n验证检查 ({np_chk}/{nt_chk} PASS):")
for cid, desc, passed, details in checks:
    print(f"  {cid}: {'PASS' if passed else 'FAIL'} — {desc} — {details}")

print("\n导出CSV...")
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Strategy', 'BOL_total', 'BOL_bat', 'BOL_fc', 'BOL_el', 'FB_total', 'FB_bat', 'FB_fc', 'FB_el',
                'AE_total', 'TE_pct', 'BOL_LPSP_pct', 'FB_LPSP_pct', 'D_bat_eoy', 'D_fc_eoy', 'D_el_eoy'])
    for s in STRATEGIES:
        r = results[s]
        w.writerow([s, f"{r['bol']:.8f}", f"{r['bol_bat']:.8f}", f"{r['bol_fc']:.8f}", f"{r['bol_el']:.8f}",
                    f"{r['fb']:.8f}", f"{r['fb_bat']:.8f}", f"{r['fb_fc']:.8f}", f"{r['fb_el']:.8f}",
                    f"{r['ae']:+.8f}", f"{r['te']*100:.2f}", f"{r['bol_lpsp']*100:.4f}", f"{r['fb_lpsp']*100:.4f}",
                    f"{r['D_bat_eoy']:.6f}", f"{r['D_fc_eoy']:.6f}", f"{r['D_el_eoy']:.6f}"])
    w.writerow([]); w.writerow(['=== Checks ==='])
    w.writerow(['ID', 'Description', 'Result', 'Details'])
    for cid, desc, passed, details in checks:
        w.writerow([cid, desc, 'PASS' if passed else 'FAIL', details])
print(f"  CSV: {OUT_CSV}")

print("\n生成图表...")
fig, axes = plt.subplots(2, 2, figsize=(14, 10))
x = np.arange(len(STRATEGIES))
# (a) TE comparison
te_vals = [results[s]['te']*100 for s in STRATEGIES]
axes[0,0].bar(x, te_vals, color=[STRAT_COLORS[s] for s in STRATEGIES], alpha=0.8, edgecolor='k', linewidth=0.3)
axes[0,0].set_xticks(x); axes[0,0].set_xticklabels([s.replace('_','\n') for s in STRATEGIES])
axes[0,0].set_ylabel('TE (%)'); axes[0,0].axhline(0, color='gray', lw=0.5)
axes[0,0].set_title('(a) Total Error by Strategy', fontsize=10, fontweight='bold'); axes[0,0].grid(True, alpha=0.3, axis='y')
# (b) LPSP comparison
bol_lpsp = [results[s]['bol_lpsp']*100 for s in STRATEGIES]
fb_lpsp = [results[s]['fb_lpsp']*100 for s in STRATEGIES]
axes[0,1].bar(x - 0.2, bol_lpsp, 0.4, label='BOL', color='#999999', alpha=0.7)
axes[0,1].bar(x + 0.2, fb_lpsp, 0.4, label='Feedback', color='#D73027', alpha=0.7)
axes[0,1].set_xticks(x); axes[0,1].set_xticklabels([s.replace('_','\n') for s in STRATEGIES])
axes[0,1].set_ylabel('LPSP (%)'); axes[0,1].legend(fontsize=8)
axes[0,1].set_title('(b) LPSP by Strategy', fontsize=10, fontweight='bold'); axes[0,1].grid(True, alpha=0.3, axis='y')
# (c) Device damage comparison
bol_bat = [results[s]['bol_bat'] for s in STRATEGIES]; bol_fc = [results[s]['bol_fc'] for s in STRATEGIES]; bol_el = [results[s]['bol_el'] for s in STRATEGIES]
axes[1,0].bar(x - 0.3, bol_bat, 0.2, label='BAT', color='#3288BD', alpha=0.8)
axes[1,0].bar(x - 0.1, bol_fc, 0.2, label='FC', color='#D73027', alpha=0.8)
axes[1,0].bar(x + 0.1, bol_el, 0.2, label='EL', color='#66C2A5', alpha=0.8)
axes[1,0].set_xticks(x); axes[1,0].set_xticklabels([s.replace('_','\n') for s in STRATEGIES])
axes[1,0].set_ylabel('BOL Damage'); axes[1,0].legend(fontsize=8)
axes[1,0].set_title('(c) Device Damage (BOL) by Strategy', fontsize=10, fontweight='bold'); axes[1,0].grid(True, alpha=0.3, axis='y')
# (d) D trajectory
for s in STRATEGIES:
    traj = results[s]['weekly_fb']
    weeks = np.arange(1, len(traj)+1)
    D_bat = [w['D_bat'] for w in traj]
    axes[1,1].plot(weeks, D_bat, color=STRAT_COLORS[s], label=f'{s}', linewidth=1.5)
axes[1,1].axhline(1.0, color='gray', ls=':', lw=0.8); axes[1,1].set_xlabel('Week'); axes[1,1].set_ylabel('D_BAT')
axes[1,1].set_title('(d) D_BAT Trajectory by Strategy', fontsize=10, fontweight='bold'); axes[1,1].legend(fontsize=8); axes[1,1].grid(True, alpha=0.3)

fig.suptitle(f'EMS Strategy Comparison (2017) — {np_chk}/{nt_chk} PASS', fontsize=12, fontweight='bold')
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight'); plt.close(fig)
print(f"  图表: {OUT_FIG}")

print("\n" + "=" * 70); print("多EMS策略对比完成"); print("=" * 70)
print(f"  TE: BF={results['battery_first']['te']*100:+.2f}%, HF={results['hydrogen_first']['te']*100:+.2f}%, BL={results['balanced']['te']*100:+.2f}%")
print(f"  验证: {np_chk}/{nt_chk} PASS")
print("=" * 70)
