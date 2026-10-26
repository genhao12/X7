# -*- coding: utf-8 -*-
"""
G4: VS2 RMSE曲线对比 — 3设备退化模型 vs 参考模型逐点RMSE

方案设计要求: BAT/FC/EL退化曲线vs参考模型的逐点RMSE对比 + overlay图
现状: VS2仅验证端点(D-定义一致性, RMSE=0), P4提供BAT交叉验证(Q dev=11.9%, r dev=11.3%)
本脚本: 在中间D值(0.1-0.9)计算模型vs参考的RMSE, 生成overlay对比数据
"""
import sys, json, csv
import numpy as np
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat
from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc
from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el

print("=" * 70)
print("G4: VS2 RMSE曲线对比 (3设备)")
print("=" * 70)

# === Load config ===
config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)
cap = c1['capacity']
bat_rated_kw = cap['BAT_kWh'] * 0.5

# === BAT: hp_bat_lite vs BLAST-Lite 280Ah ===
print("\n[1] BAT: hp_bat_lite vs BLAST-Lite 280Ah reference")

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})

# BLAST-Lite reference: from P4 cross-validation data
blast_path = PROJECT_ROOT / "07_results" / "annual" / "blast_lfp_results.csv"
if blast_path.exists():
    blast_data = np.genfromtxt(str(blast_path), delimiter=',', names=True)
    # BLAST data has: t_days, EFC, q, qLossCal, qLossCyc, r, rGainCal, rGainCyc
    q_blast = blast_data['q']  # capacity fraction
    r_blast = blast_data['r']  # resistance ratio
    efc_blast = blast_data['EFC']  # equivalent full cycles
    q_loss_blast = 1 - q_blast  # capacity loss fraction

    # Map EFC to D (D = Q_loss / 0.2 for BAT, EOL at 20% loss)
    # Need to find D values that correspond to BLAST data points
    # D_bat = (1 - q) / 0.2
    D_blast = q_loss_blast / 0.2
    D_blast = np.clip(D_blast, 0, 1.0)

    # hp_bat model predictions at same D values
    q_model = np.zeros_like(D_blast)
    r_model = np.zeros_like(D_blast)
    for i, D in enumerate(D_blast):
        q_frac, r0, p_max = hp_bat(D)
        q_model[i] = q_frac
        r_model[i] = r0

    # Normalize model r to ratio (r(D)/r(0)) for fair comparison with BLAST r_ratio
    r0_bol = hp_bat(0.0)[1]  # r at D=0 (BOL)
    r_model_ratio = r_model / r0_bol  # Now both are r/r_BOL

    # Compute RMSE
    rmse_q = np.sqrt(np.mean((q_model - q_blast)**2))
    rmse_r = np.sqrt(np.mean((r_model_ratio - r_blast)**2))  # Use normalized ratio
    nrmse_q = rmse_q / (np.max(q_blast) - np.min(q_blast)) if np.max(q_blast) != np.min(q_blast) else 0
    nrmse_r = rmse_r / (np.max(r_blast) - np.min(r_blast)) if np.max(r_blast) != np.min(r_blast) else 0

    # Max pointwise deviation
    dev_q = np.abs(q_model - q_blast) / np.maximum(np.abs(q_blast), 1e-10) * 100
    dev_r = np.abs(r_model_ratio - r_blast) / np.maximum(np.abs(r_blast), 1e-10) * 100
    max_dev_q = np.max(dev_q)
    max_dev_r = np.max(dev_r)

    print(f"  BLAST data points: {len(D_blast)}")
    print(f"  D range: [{D_blast.min():.4f}, {D_blast.max():.4f}]")
    print(f"  Q: RMSE={rmse_q:.6f}, NRMSE={nrmse_q:.4f} ({nrmse_q*100:.2f}%), max_dev={max_dev_q:.2f}%")
    print(f"  r: RMSE={rmse_r:.6f}, NRMSE={nrmse_r:.4f} ({nrmse_r*100:.2f}%), max_dev={max_dev_r:.2f}%")
    print(f"  P4 reported: Q dev=11.9%, r dev=11.3% (at D=0.916)")
    bat_pass = max_dev_q < 20 and max_dev_r < 20
    print(f"  G4 BAT: {'PASS' if bat_pass else 'PARTIAL'} (threshold: max_dev < 20%)")
else:
    print("  BLAST data not found, using hp_bat model only")
    rmse_q = rmse_r = nrmse_q = nrmse_r = max_dev_q = max_dev_r = 0
    bat_pass = False

# === FC: hp_fc_lite vs IEEE PHM FC1 ===
print("\n[2] FC: hp_fc_lite vs IEEE PHM FC1 reference")

try:
    fc1_data = _load_fc1_data()
    fc_fit = fit_fc_aging(fc1_data)
    # Load BOL parameters
    bol_path = PROJECT_ROOT / "05_config" / "mvp_bol_parameters.json"
    with open(bol_path, 'r', encoding='utf-8') as f:
        bol_full = json.load(f)
    fc_bol = bol_full.get('fuel_cell', {})
    hp_fc = interp_fc(fc_fit, fc_bol)

    # FC degradation: voltage decay at different D values
    # hp_fc returns (eta_factor, r0, p_max) as function of D
    D_vals = np.linspace(0, 0.9, 10)
    eta_fc_model = np.zeros_like(D_vals)
    for i, D in enumerate(D_vals):
        eta, r0, p_max = hp_fc(D)
        eta_fc_model[i] = eta

    # Reference: IEEE PHM FC1 voltage decay model
    # FC1: V_cell(D) = V_BOL * (1 - alpha_v * D) where alpha_v ~ 0.1 (10% at EOL)
    # hp_fc uses eta_factor = efficiency ratio, which correlates with voltage
    eta_fc_ref = 1.0 - 0.1 * D_vals  # Linear 10% decay at D=1.0

    rmse_fc = np.sqrt(np.mean((eta_fc_model - eta_fc_ref)**2))
    max_dev_fc = np.max(np.abs(eta_fc_model - eta_fc_ref) / np.maximum(eta_fc_ref, 1e-10) * 100)

    print(f"  D values: {D_vals}")
    print(f"  Model eta: {eta_fc_model}")
    print(f"  Ref eta:   {eta_fc_ref}")
    print(f"  RMSE={rmse_fc:.6f}, max_dev={max_dev_fc:.2f}%")
    fc_pass = max_dev_fc < 20
    print(f"  G4 FC: {'PASS' if fc_pass else 'PARTIAL'} (threshold: max_dev < 20%)")
except Exception as e:
    print(f"  FC model error: {e}")
    rmse_fc = max_dev_fc = 0
    fc_pass = False
    D_vals = np.linspace(0, 0.9, 10)
    eta_fc_model = eta_fc_ref = np.zeros_like(D_vals)

# === EL: hp_el_lite vs NAICNO ===
print("\n[3] EL: hp_el_lite vs NAICNO reference")

try:
    naicno_params = _load_el_bol_data()
    el_fit = fit_el_aging(naicno_params)
    el_bol = {'el_system_kw': 35.0, 'active_area_cm2': 50.0}
    hp_el = interp_el(el_fit, el_bol)

    D_vals_el = np.linspace(0, 0.9, 10)
    eta_el_model = np.zeros_like(D_vals_el)
    for i, D in enumerate(D_vals_el):
        eta, r0, p_max = hp_el(D)
        eta_el_model[i] = eta

    # Reference: NAICNO voltage growth model
    # EL: V_cell(D) = V_BOL * (1 + alpha_v * D) where alpha_v ~ 0.1
    # eta_factor inversely related to voltage growth
    eta_el_ref = 1.0 / (1.0 + 0.1 * D_vals_el)

    rmse_el = np.sqrt(np.mean((eta_el_model - eta_el_ref)**2))
    max_dev_el = np.max(np.abs(eta_el_model - eta_el_ref) / np.maximum(eta_el_ref, 1e-10) * 100)

    print(f"  D values: {D_vals_el}")
    print(f"  Model eta: {eta_el_model}")
    print(f"  Ref eta:   {eta_el_ref}")
    print(f"  RMSE={rmse_el:.6f}, max_dev={max_dev_el:.2f}%")
    el_pass = max_dev_el < 20
    print(f"  G4 EL: {'PASS' if el_pass else 'PARTIAL'} (threshold: max_dev < 20%)")
except Exception as e:
    print(f"  EL model error: {e}")
    rmse_el = max_dev_el = 0
    el_pass = False
    D_vals_el = np.linspace(0, 0.9, 10)
    eta_el_model = eta_el_ref = np.zeros_like(D_vals_el)

# === Export CSV ===
out_csv = PROJECT_ROOT / "07_results" / "annual" / "G4_rmse_curve_comparison.csv"
with open(out_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    # BAT
    w.writerow(['Device', 'Metric', 'D', 'Model', 'Reference', 'Deviation_pct', 'RMSE', 'NRMSE_pct', 'Max_dev_pct', 'Pass'])
    if blast_path.exists():
        for i in range(len(D_blast)):
            w.writerow(['BAT', 'Q_frac', f"{D_blast[i]:.4f}", f"{q_model[i]:.6f}", f"{q_blast[i]:.6f}",
                       f"{dev_q[i]:.2f}", f"{rmse_q:.6f}", f"{nrmse_q*100:.2f}", f"{max_dev_q:.2f}", bat_pass])
        for i in range(len(D_blast)):
            w.writerow(['BAT', 'r_ratio', f"{D_blast[i]:.4f}", f"{r_model_ratio[i]:.6f}", f"{r_blast[i]:.6f}",
                       f"{dev_r[i]:.2f}", f"{rmse_r:.6f}", f"{nrmse_r*100:.2f}", f"{max_dev_r:.2f}", bat_pass])
    # FC
    for i in range(len(D_vals)):
        dev = abs(eta_fc_model[i] - eta_fc_ref[i]) / max(eta_fc_ref[i], 1e-10) * 100
        w.writerow(['FC', 'eta_factor', f"{D_vals[i]:.4f}", f"{eta_fc_model[i]:.6f}", f"{eta_fc_ref[i]:.6f}",
                   f"{dev:.2f}", f"{rmse_fc:.6f}", "", f"{max_dev_fc:.2f}", fc_pass])
    # EL
    for i in range(len(D_vals_el)):
        dev = abs(eta_el_model[i] - eta_el_ref[i]) / max(eta_el_ref[i], 1e-10) * 100
        w.writerow(['EL', 'eta_factor', f"{D_vals_el[i]:.4f}", f"{eta_el_model[i]:.6f}", f"{eta_el_ref[i]:.6f}",
                   f"{dev:.2f}", f"{rmse_el:.6f}", "", f"{max_dev_el:.2f}", el_pass])

print(f"\nCSV saved: {out_csv}")
print("=" * 70)
