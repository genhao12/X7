# -*- coding: utf-8 -*-
"""容量搜索: 找到LPSP≤1%的可行配置C5_feasible"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df = raw[raw['local_civil_year'] == 2017].copy()
df = df.rename(columns={
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW'})
df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
pv_full = df['P_pv_kW'].values; load_full = df['P_load_kW'].values

print("=" * 60)
print("容量搜索: LPSP≤1%")
print("=" * 60)

# 从配置模板读取EMS参数
with open(PROJECT_ROOT / "05_config" / "mvp_c1_config.json", 'r', encoding='utf-8') as f:
    c1 = json.load(f)
ep = c1['ems_parameters']; bc = c1['battery_constraints']
hc = c1['hydrogen_constraints']; msl = c1['minimum_stable_load']; topo = c1['topology']

SPW = 672; WPY = 52

def test_config(pv, bat, fc, el, h2, n_weeks=52):
    """测试给定配置的LPSP"""
    ems_cfg = EMSConfig(pv_kwp=pv, bat_kwh=bat, pemfc_kw=fc, pemel_kw=el, h2_kg=h2,
        soc_min=bc['soc_min'], soc_max=bc['soc_max'], soc_initial=bc['soc_initial'],
        h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
        h2_initial_fraction=hc['m_initial_fraction'], h2_leakage_per_day=hc['leakage_rate_per_day'],
        start_stop_threshold=ep['start_stop_threshold_fraction'],
        minimum_run_time_min=float(ep['minimum_run_time']),
        critical_load_fraction=ep['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
    total_lp = 0; n = min(n_weeks, len(pv_full)//SPW)
    for w in range(n):
        s = w * SPW; e = s + SPW
        pv_w = pv_full[s:e]; load_w = load_full[s:e]
        np.random.seed(42)
        cmds, state = rule_based_dispatch(load_w, pv_w, ems_cfg)
        total_lp += float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6))
    return total_lp / n * 100

# 逐步增加容量搜索
configs = [
    # (PV, BAT, FC, EL, H2, label)
    (120, 135, 37.5, 35, 25, "C1(原配置)"),
    (200, 300, 60, 60, 100, "2x"),
    (300, 500, 80, 80, 200, "3x"),
    (400, 800, 100, 100, 300, "4x"),
    (500, 1000, 120, 120, 400, "5x"),
    (300, 500, 100, 100, 300, "3x-adjusted"),
    (250, 400, 80, 80, 200, "2.5x"),
    (350, 600, 100, 100, 300, "3.5x"),
    (400, 600, 80, 80, 250, "4x-PV-focused"),
    (350, 700, 100, 100, 350, "3.5x-bat-heavy"),
    (300, 800, 120, 120, 400, "3x-storage-heavy"),
    (250, 600, 100, 100, 300, "2.5x-storage-heavy"),
    (200, 500, 80, 80, 200, "2x-storage-heavy"),
]

print(f"\n{'Config':<30} {'PV':>6} {'BAT':>6} {'FC':>6} {'EL':>6} {'H2':>6} {'LPSP%':>8}")
print("-" * 80)
best = None
for pv, bat, fc, el, h2, label in configs:
    lpsp = test_config(pv, bat, fc, el, h2)
    flag = " <-- ≤1%" if lpsp <= 1.0 else ""
    print(f"{label:<30} {pv:>6} {bat:>6} {fc:>6} {el:>6} {h2:>6} {lpsp:>8.2f}{flag}")
    if lpsp <= 1.0 and best is None:
        best = (pv, bat, fc, el, h2, label, lpsp)

if best:
    print(f"\n✓ 首个LPSP≤1%配置: {best[5]} (LPSP={best[6]:.2f}%)")
    print(f"  PV={best[0]}kWp, BAT={best[1]}kWh, FC={best[2]}kW, EL={best[3]}kW, H2={best[4]}kg")
else:
    print("\n✗ 未找到LPSP≤1%的配置，继续搜索更大容量...")
    # 搜索更大容量
    for pv_mult in [4, 5, 6]:
        for bat_mult in [4, 5, 6, 8]:
            pv = 120 * pv_mult
            bat = 135 * bat_mult
            fc = 37.5 * min(pv_mult, bat_mult) if pv_mult < 4 else 150
            el = 35 * min(pv_mult, bat_mult) if pv_mult < 4 else 150
            h2 = 25 * max(pv_mult, bat_mult)
            lpsp = test_config(pv, bat, fc, el, h2)
            flag = " <-- ≤1%" if lpsp <= 1.0 else ""
            print(f"PV{pv_mult}x_BAT{bat_mult}x     {pv:>6} {bat:>6} {fc:>6.0f} {el:>6.0f} {h2:>6} {lpsp:>8.2f}{flag}")
            if lpsp <= 1.0:
                best = (pv, bat, fc, el, h2, f"PV{pv_mult}x_BAT{bat_mult}x", lpsp)
                break
        if best:
            break
    if best:
        print(f"\n✓ 找到LPSP≤1%配置: {best[5]} (LPSP={best[6]:.2f}%)")
    else:
        print("\n✗ 仍未找到，LPSP可能受EMS策略限制而非容量")
