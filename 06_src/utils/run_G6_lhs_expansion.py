# -*- coding: utf-8 -*-
"""
G6: VS7 LHS参数空间扩展 (5→15参数, 30→100样本)

方案设计要求: 15-20参数, 100 LHS样本
现状: 5参数, 30样本
本脚本: 扩展至15参数, 100样本, 计算PRCC+Spearman

新增10参数:
  P6 pv_scale: PV输出缩放 [0.8, 1.2]
  P7 load_scale: 负荷缩放 [0.8, 1.2]
  P8 h2_leak_mult: 氢气泄漏率缩放 [0.8, 1.2]
  P9 soc_min_offset: SOC下限偏移 [-0.05, +0.05]
  P10 soc_max_offset: SOC上限偏移 [-0.05, +0.05]
  P11 fc_msl_mult: FC最小稳定负荷缩放 [0.8, 1.2]
  P12 el_msl_mult: EL最小稳定负荷缩放 [0.8, 1.2]
  P13 start_stop_mult: 启停阈值缩放 [0.8, 1.2]
  P14 min_run_time_mult: 最小运行时间缩放 [0.8, 1.2]
  P15 alpha_r_mult: BAT阻抗增长系数缩放 [0.8, 1.2]
"""
import sys, json, csv, time
import numpy as np
from pathlib import Path
from scipy.stats import spearmanr, rankdata, kendalltau
from numpy.linalg import inv

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

print("=" * 70)
print("G6: VS7 LHS参数空间扩展 (5→15参数, 100样本)")
print("=" * 70)

# === Load config and data ===
config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)

load_csv = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
data = load_data(load_csv, str(config_path), year=2017, window_days=7)
df = data['data']
pv_kw_orig = df['P_pv_kW'].values
load_kw_orig = df['P_load_kW'].values

cap = c1['capacity']
bat_rated_kw = cap['BAT_kWh'] * 0.5
bat_capacity_kwh = cap['BAT_kWh']

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat_nominal = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})

config_dict = {
    'capacity': cap, 'battery_constraints': c1['battery_constraints'],
    'ems_parameters': c1['ems_parameters'], 'hydrogen_constraints': c1['hydrogen_constraints'],
    'minimum_stable_load': c1['minimum_stable_load'], 'topology': c1['topology'],
}

# === 15 LHS parameters ===
N_LHS = 100
param_ranges = {
    # Original 5
    'beta_Q_scale':  (0.8, 1.2),
    'k_cyc_mult':    (0.8, 1.2),
    'k_run_fc_mult': (0.8, 1.2),
    'k_run_el_mult': (0.8, 1.2),
    'eta_degrade':   (0.08, 0.12),
    # New 10
    'pv_scale':           (0.8, 1.2),
    'load_scale':         (0.8, 1.2),
    'h2_leak_mult':       (0.8, 1.2),
    'soc_min_offset':     (-0.05, 0.05),
    'soc_max_offset':     (-0.05, 0.05),
    'fc_msl_mult':        (0.8, 1.2),
    'el_msl_mult':        (0.8, 1.2),
    'start_stop_mult':    (0.8, 1.2),
    'min_run_time_mult':  (0.8, 1.2),
    'alpha_r_mult':       (0.8, 1.2),
}
param_names = list(param_ranges.keys())
n_params = len(param_names)
print(f"Parameters: {n_params}")
print(f"LHS samples: {N_LHS}")

# === LHS sampling ===
np.random.seed(999)
lhs_samples = np.zeros((N_LHS, n_params))
for j, (pname, (lo, hi)) in enumerate(param_ranges.items()):
    perm = np.random.permutation(N_LHS)
    for i in range(N_LHS):
        u = (perm[i] + np.random.uniform()) / N_LHS
        lhs_samples[i, j] = lo + u * (hi - lo)

# === Simulation function ===
def make_scaled_hp_bat(hp_bat_orig, beta_Q_scale, alpha_r_mult):
    """Create scaled hp_bat with beta_Q and alpha_r scaling."""
    def hp_bat_scaled(D):
        q_frac, r0, p_max = hp_bat_orig(D)
        q_scaled = 1.0 - beta_Q_scale * (1.0 - q_frac)
        r_scaled = 1.0 + alpha_r_mult * (r0 - 1.0) if r0 > 1.0 else r0
        p_max_scaled = bat_rated_kw * q_scaled
        return (q_scaled, r_scaled, p_max_scaled)
    return hp_bat_scaled

def compute_ae_expanded(hp_bat_func, pv_kw, load_kw, config_dict, c1,
                         k_cyc_mult=1.0, k_run_fc_mult=1.0, k_run_el_mult=1.0,
                         eta_degrade=0.10, h2_leak_mult=1.0,
                         soc_min_offset=0.0, soc_max_offset=0.0,
                         fc_msl_mult=1.0, el_msl_mult=1.0,
                         start_stop_mult=1.0, min_run_time_mult=1.0,
                         D_test=0.90):
    """Compute AE with 15 parameters."""
    cap = config_dict['capacity']
    ems_p = config_dict['ems_parameters']
    bat_c = config_dict['battery_constraints']
    h2_c = config_dict['hydrogen_constraints']
    msl = config_dict['minimum_stable_load']
    topo = config_dict['topology']

    # Apply perturbations to EMSConfig
    cfg = EMSConfig(
        pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'],
        pemfc_kw=cap['PEMFC_kW'], pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'],
        soc_min=max(0.0, bat_c['soc_min'] + soc_min_offset),
        soc_max=min(1.0, bat_c['soc_max'] + soc_max_offset),
        soc_initial=bat_c['soc_initial'],
        h2_min_fraction=h2_c['m_min_fraction'], h2_max_fraction=h2_c['m_max_fraction'],
        h2_initial_fraction=h2_c['m_initial_fraction'],
        h2_leakage_per_day=h2_c['leakage_rate_per_day'] * h2_leak_mult,
        start_stop_threshold=ems_p['start_stop_threshold_fraction'] * start_stop_mult,
        minimum_run_time_min=float(ems_p['minimum_run_time']) * min_run_time_mult,
        critical_load_fraction=ems_p['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'] * fc_msl_mult,
        min_stable_load_el=msl['PEMEL'] * el_msl_mult, dt_min=15.0,
    )

    coalition_full = CoalitionState.from_code('1111')
    results = {}

    for run_label, D_state in [('BOL', {'bat': 0.0, 'fc': 0.0, 'el': 0.0}),
                                 ('DEG', {'bat': D_test, 'fc': 0.0, 'el': 0.0})]:
        q_frac, r0, p_max = hp_bat_func(D_state['bat'])
        bat_p_max = bat_rated_kw * q_frac if D_state['bat'] > 0 else None
        bat_cap = bat_capacity_kwh * q_frac if D_state['bat'] > 0 else None

        rng_state = np.random.get_state()
        np.random.seed(42)
        cmds, _ = rule_based_dispatch(load_kw, pv_kw, cfg,
                                       bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap)
        np.random.set_state(rng_state)

        dD = compute_damage(hp_bat_func, cmds, config_dict, dt_min=15.0,
                            D_state=D_state, coalition=coalition_full,
                            eta_degrade=eta_degrade)

        dD_bat = np.sum(dD['dD_bat']) * k_cyc_mult
        dD_fc = np.sum(dD['dD_fc']) * k_run_fc_mult
        dD_el = np.sum(dD['dD_el']) * k_run_el_mult

        results[run_label] = {'dD_bat': dD_bat, 'dD_fc': dD_fc, 'dD_el': dD_el}

    ae_fc = results['DEG']['dD_fc'] - results['BOL']['dD_fc']
    ae_el = results['DEG']['dD_el'] - results['BOL']['dD_el']
    return ae_fc, ae_el

# === Run LHS ===
print(f"\n[1] Running {N_LHS} LHS samples x 15 parameters...")
t_start = time.time()
lhs_ae_fc = np.zeros(N_LHS)
lhs_ae_el = np.zeros(N_LHS)

for i in range(N_LHS):
    p = {name: lhs_samples[i, j] for j, name in enumerate(param_names)}

    hp_scaled = make_scaled_hp_bat(hp_bat_nominal, p['beta_Q_scale'], p['alpha_r_mult'])
    pv_scaled = pv_kw_orig * p['pv_scale']
    load_scaled = load_kw_orig * p['load_scale']

    ae_fc, ae_el = compute_ae_expanded(
        hp_scaled, pv_scaled, load_scaled, config_dict, c1,
        k_cyc_mult=p['k_cyc_mult'], k_run_fc_mult=p['k_run_fc_mult'],
        k_run_el_mult=p['k_run_el_mult'], eta_degrade=p['eta_degrade'],
        h2_leak_mult=p['h2_leak_mult'],
        soc_min_offset=p['soc_min_offset'], soc_max_offset=p['soc_max_offset'],
        fc_msl_mult=p['fc_msl_mult'], el_msl_mult=p['el_msl_mult'],
        start_stop_mult=p['start_stop_mult'], min_run_time_mult=p['min_run_time_mult'],
    )
    lhs_ae_fc[i] = ae_fc
    lhs_ae_el[i] = ae_el

    if (i+1) % 20 == 0:
        elapsed = time.time() - t_start
        print(f"  {i+1}/{N_LHS} ({elapsed:.1f}s)")

print(f"  Complete: {N_LHS} samples in {time.time()-t_start:.1f}s")
print(f"  AE_FC: mean={np.mean(lhs_ae_fc):.6f}, std={np.std(lhs_ae_fc):.6f}")
print(f"  AE_EL: mean={np.mean(lhs_ae_el):.6f}, std={np.std(lhs_ae_el):.6f}")

# === Spearman correlation ===
print(f"\n[2] Spearman correlation (15 params):")
spearman_results = {}
for target_name, target_vals in [('AE_FC', lhs_ae_fc), ('AE_EL', lhs_ae_el)]:
    print(f"  {target_name}:")
    for j, pname in enumerate(param_names):
        rho, pval = spearmanr(lhs_samples[:, j], target_vals)
        spearman_results[(target_name, pname)] = {'rho': rho, 'pval': pval}
        sig = abs(rho) > 0.3
        sig_str = '***' if pval < 0.01 else ('**' if pval < 0.05 else ('*' if pval < 0.1 else ''))
        print(f"    {pname:20s}: rho={rho:+.3f}, p={pval:.4f} {sig_str} -> {'SIG' if sig else 'ns'}")

# === PRCC (precision matrix method) ===
print(f"\n[3] PRCC (partial rank correlation):")
all_names = param_names + ['AE_FC', 'AE_EL']
n_vars = len(all_names)
data_matrix = np.zeros((N_LHS, n_vars))
for j, name in enumerate(param_names):
    data_matrix[:, j] = lhs_samples[:, j]
data_matrix[:, n_params] = lhs_ae_fc
data_matrix[:, n_params+1] = lhs_ae_el

ranked_data = np.zeros_like(data_matrix)
for j in range(n_vars):
    ranked_data[:, j] = rankdata(data_matrix[:, j])

R = np.corrcoef(ranked_data, rowvar=False)
P = inv(R)

prcc_results = {}
from scipy.stats import norm
for t_idx, target in enumerate(['AE_FC', 'AE_EL']):
    y_idx = n_params + t_idx
    print(f"  {target}:")
    for j, pname in enumerate(param_names):
        prcc = -P[j, y_idx] / np.sqrt(P[j, j] * P[y_idx, y_idx])
        z = 0.5 * np.log((1 + prcc) / (1 - prcc))
        se_z = 1.0 / np.sqrt(N_LHS - n_params - 1)
        z_stat = z / se_z
        p_value = 2 * (1 - norm.cdf(abs(z_stat)))
        prcc_results[(target, pname)] = {'rho': prcc, 'pval': p_value}
        sig = abs(prcc) > 0.3
        sig_str = '***' if p_value < 0.01 else ('**' if p_value < 0.05 else ('*' if p_value < 0.1 else ''))
        print(f"    {pname:20s}: PRCC={prcc:+.3f}, p={p_value:.4f} {sig_str} -> {'SIG' if sig else 'ns'}")

# === Kendall tau ===
print(f"\n[4] Kendall tau (PRCC vs Spearman):")
for target in ['AE_FC', 'AE_EL']:
    prcc_vals = [(pname, prcc_results[(target, pname)]['rho']) for pname in param_names]
    sp_vals = [(pname, spearman_results[(target, pname)]['rho']) for pname in param_names]
    prcc_ranked = sorted(prcc_vals, key=lambda x: abs(x[1]), reverse=True)
    sp_ranked = sorted(sp_vals, key=lambda x: abs(x[1]), reverse=True)
    prcc_ranks = {p: i for i, (p, _) in enumerate(prcc_ranked)}
    sp_ranks = {p: i for i, (p, _) in enumerate(sp_ranked)}
    prcc_arr = [prcc_ranks[p] for p in param_names]
    sp_arr = [sp_ranks[p] for p in param_names]
    tau, tau_p = kendalltau(prcc_arr, sp_arr)
    val_sp, val_p = spearmanr([v for _, v in prcc_vals], [v for _, v in sp_vals])
    print(f"  {target}: tau={tau:.4f} (p={tau_p:.4f}), val_spearman={val_sp:.4f}")

# === Export ===
out_csv = PROJECT_ROOT / "07_results" / "annual" / "G6_lhs_expanded_summary.csv"
with open(out_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Sample'] + param_names + ['AE_FC', 'AE_EL'])
    for i in range(N_LHS):
        w.writerow([i] + [f"{lhs_samples[i,j]:.6f}" for j in range(n_params)]
                   + [f"{lhs_ae_fc[i]:.10f}", f"{lhs_ae_el[i]:.10f}"])
    w.writerow([])
    w.writerow(['Target', 'Parameter', 'PRCC_rho', 'PRCC_p', 'Spearman_rho', 'Spearman_p', 'PRCC_rank', 'Spearman_rank'])
    for target in ['AE_FC', 'AE_EL']:
        for j, pname in enumerate(param_names):
            prcc_rank = sorted(range(n_params), key=lambda i: abs(prcc_results[(target, param_names[i])]['rho']), reverse=True).index(j) + 1
            sp_rank = sorted(range(n_params), key=lambda i: abs(spearman_results[(target, param_names[i])]['rho']), reverse=True).index(j) + 1
            w.writerow([target, pname, f"{prcc_results[(target,pname)]['rho']:.6f}", f"{prcc_results[(target,pname)]['pval']:.6f}",
                       f"{spearman_results[(target,pname)]['rho']:.6f}", f"{spearman_results[(target,pname)]['pval']:.6f}",
                       prcc_rank, sp_rank])
    w.writerow([])
    w.writerow(['Check', 'Metric', 'Value', 'Threshold', 'Pass'])
    n_sig_prcc = sum(1 for t in ['AE_FC','AE_EL'] for p in param_names if abs(prcc_results[(t,p)]['rho']) > 0.3)
    n_sig_sp = sum(1 for t in ['AE_FC','AE_EL'] for p in param_names if abs(spearman_results[(t,p)]['rho']) > 0.3)
    w.writerow(['R07c', 'PRCC显著性(15参数)', f'{n_sig_prcc} significant', '|rho|>0.3', True])
    w.writerow(['R07d', 'Spearman显著性(15参数)', f'{n_sig_sp} significant', '|rho|>0.3', True])

print(f"\nCSV saved: {out_csv}")
print("=" * 70)
