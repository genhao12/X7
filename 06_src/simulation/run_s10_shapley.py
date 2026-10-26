# -*- coding: utf-8 -*-
"""S10: Shapley 路径效应归因分解 (EOL前窗口, 固定D协议)

协议: 路径辨识协议 — 固定D=0.5, W1窗口(7天, 672步, EOL前)
归因变量四组:
  C = 容量(Q缩减)
  E = 效率(eta_factor)
  P = 功率(Pmax限制)
  I = 库存控制(SOC/H2边界保护)
联盟: 2^4=16状态
配置: C6_compact, 全设备反馈
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path
from math import factorial

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat
from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc
from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el
from identification.mediation_lite import compute_shapley, MEDIATOR_GROUPS

print("=" * 70)
print("S10: Shapley 路径效应归因分解 (EOL前窗口, 固定D=0.5, C6_compact)")
print("=" * 70)

cap = {"PV_kWp": 200, "BAT_kWh": 500, "PEMFC_kW": 80, "PEMEL_kW": 80, "H2_kg": 500}
with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", 'r', encoding='utf-8') as f:
    c5 = json.load(f)
ep=c5['ems_parameters']; bc=c5['battery_constraints']
hc=c5['hydrogen_constraints']; msl=c5['minimum_stable_load']; topo=c5['topology']
ems_cfg = EMSConfig(pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'], pemfc_kw=cap['PEMFC_kW'],
    pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'], soc_min=bc['soc_min'], soc_max=bc['soc_max'],
    soc_initial=bc['soc_initial'], h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=1.0, h2_leakage_per_day=hc['leakage_rate_per_day'],
    start_stop_threshold=ep['start_stop_threshold_fraction'],
    minimum_run_time_min=float(ep['minimum_run_time']),
    critical_load_fraction=ep['critical_load_fraction'],
    p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
bat_rated_kw = cap['BAT_kWh'] * 0.5; bat_cap_kwh = cap['BAT_kWh']
config_dict = {'capacity': cap, 'battery_constraints': bc, 'ems_parameters': ep,
    'hydrogen_constraints': hc, 'minimum_stable_load': msl, 'topology': topo}

hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
    {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})
hp_fc = interp_fc(fit_fc_aging(_load_fc1_data()),
    {'stack_config': {}, 'fc_system_kw': cap['PEMFC_kW']})
hp_el = interp_el(fit_el_aging(_load_el_bol_data()),
    {'el_system_kw': cap['PEMEL_kW'], 'active_area_cm2': 50.0})

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

# W1窗口 (7天, EOL前)
SPW = 672
pv_w = pv_full[:SPW]; load_w = load_full[:SPW]

# 固定D=0.5 (BAT为源设备, 路径辨识协议)
D_bat = 0.5
D_state = {'bat': D_bat, 'fc': 0.0, 'el': 0.0}

# 全设备反馈参数
q_bat, r0_bat, pmax_bat = hp_bat(D_bat)
bat_p_max = bat_rated_kw * q_bat
bat_cap = bat_cap_kwh * q_bat

def run_coalition(code_str):
    """运行指定联盟状态, 返回总损伤(设备级向量)
    code: 4位二进制 CEPI
    C=容量ON: 用退化后Q (OFF则用Q=1.0)
    E=效率ON: 用退化后eta_factor (OFF则用1.0)
    P=功率ON: 用退化后Pmax (OFF则用额定)
    I=库存ON: 正常SOC/H2边界 (OFF则放宽边界)
    """
    coal = CoalitionState.from_code(code_str)
    kw = {}
    # C: 容量
    if code_str[0] == '1':
        kw['bat_p_max_kw'] = bat_p_max
        kw['bat_capacity_kwh'] = bat_cap
    # E: 效率 (FC/EL退化效率)
    if code_str[1] == '1':
        # 全反馈: D_fc=0, D_el=0, eta=1.0 (无FC/EL退化)
        pass  # D_fc=D_el=0, 无效率变化
    # P: 功率
    if code_str[2] == '1':
        pass  # D_fc=D_el=0, 无功率变化
    # I: 库存控制 (这里简化: I_ON=正常, I_OFF=放宽SOC/H2边界)
    # 实际OFF操作在CoalitionState中处理
    
    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_w, pv_w, ems_cfg, **kw)
    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D_state, coalition=coal)
    return {
        'bat': float(np.sum(dD['dD_bat'])),
        'fc': float(np.sum(dD['dD_fc'])),
        'el': float(np.sum(dD['dD_el'])),
        'total_bat': float(np.sum(dD['dD_bat'])),
    }

# 运行16个联盟状态
print("\n[16联盟状态运行 (W1, D_bat=0.5)]")
coalition_values = {}
print(f"{'Code':<6} {'C':>2}{'E':>2}{'P':>2}{'I':>2} {'D_BAT':>10} {'D_FC':>10} {'D_EL':>10} {'Total':>10}")
print('-' * 60)
for i in range(16):
    code = format(i, '04b')
    result = run_coalition(code)
    total = result['bat'] + result['fc'] + result['el']
    coalition_values[code] = total
    print(f"{code:<6} {code[0]:>2}{code[1]:>2}{code[2]:>2}{code[3]:>2} {result['bat']:>10.6f} {result['fc']:>10.6f} {result['el']:>10.6f} {total:>10.6f}")

# 计算Shapley值
print("\n[Shapley 路径效应归因分解]")
shapley_values = {}
for m in MEDIATOR_GROUPS:
    phi = compute_shapley(coalition_values, m)
    shapley_values[m] = phi
    print(f"  phi_{m} = {phi:+.6f}")

# 闭合验证: sum(phi_i) = v(M) - v(empty)
v_full = coalition_values['1111']
v_empty = coalition_values['0000']
shapley_sum = sum(shapley_values.values())
residual = abs(shapley_sum - (v_full - v_empty))
print(f"\n[闭合验证]")
print(f"  v(1111) = {v_full:.6f}")
print(f"  v(0000) = {v_empty:.6f}")
print(f"  v(M)-v(∅) = {v_full - v_empty:.6f}")
print(f"  Σφ_i = {shapley_sum:.6f}")
print(f"  残差 = {residual:.2e}")
print(f"  闭合: {'PASS' if residual < 1e-6 else 'FAIL'} (阈值1e-6)")

# 归因变量定义
print(f"\n[归因变量定义]")
print(f"  C = 容量ON: Q(D_bat)缩减反馈到EMS (bat_p_max/bat_capacity)")
print(f"  E = 效率ON: eta_factor(D)退化 (D_fc=D_el=0, 仅BAT效率影响)")
print(f"  P = 功率ON: Pmax(D)限制 (D_fc=D_el=0, 仅BAT功率影响)")
print(f"  I = 库存ON: SOC/H2边界保护 (OFF=放宽边界)")
print(f"  联盟OFF操作: g_i(D)→g_i(0)替换退化映射")

# 交互占比
print(f"\n[交互占比分析]")
# 二阶交互: phi_i + phi_j vs v({i,j}) - v(empty)
pairs = [('C','E'), ('C','P'), ('C','I'), ('E','P'), ('E','I'), ('P','I')]
for i_name, j_name in pairs:
    idx_i = MEDIATOR_GROUPS.index(i_name)
    idx_j = MEDIATOR_GROUPS.index(j_name)
    # 找到只有i和j的联盟
    code_ij = ['0','0','0','0']
    code_ij[idx_i] = '1'
    code_ij[idx_j] = '1'
    code_ij_str = ''.join(code_ij)
    v_ij = coalition_values.get(code_ij_str, 0.0)
    v_i_code = ['0','0','0','0']; v_i_code[idx_i] = '1'
    v_j_code = ['0','0','0','0']; v_j_code[idx_j] = '1'
    v_i = coalition_values.get(''.join(v_i_code), 0.0)
    v_j = coalition_values.get(''.join(v_j_code), 0.0)
    interaction = v_ij - v_i - v_j + v_empty
    pct = abs(interaction) / max(abs(v_full - v_empty), 1e-12) * 100
    print(f"  I_{{{i_name},{j_name}}} = {interaction:+.6f} ({pct:.1f}%)")

print(f"\n{'='*70}")
print("S10 Shapley 路径效应归因分解完成")
print(f"{'='*70}")
print(f"  配置: C6_compact, W1窗口, D_bat=0.5(固定)")
print(f"  闭合残差: {residual:.2e} ({'PASS' if residual < 1e-6 else 'FAIL'})")
print(f"  Shapley值: C={shapley_values['C']:+.6f}, E={shapley_values['E']:+.6f}, P={shapley_values['P']:+.6f}, I={shapley_values['I']:+.6f}")
