# -*- coding: utf-8 -*-
"""S12: 回归测试"""
import sys
import json
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(r'D:\matlab\C') / '06_src'))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

print('S12: 回归测试')
print('=' * 50)

hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
    {'bat_kwh': 135, 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': 67.5})
ems = EMSConfig()
config_dict = {'capacity': {'PV_kWp':120,'BAT_kWh':135,'PEMFC_kW':37.5,'PEMEL_kW':35,'H2_kg':25},
    'battery_constraints': {'soc_min':0.15,'soc_max':0.95,'soc_initial':0.5},
    'ems_parameters': {'start_stop_threshold_fraction':0.275},
    'hydrogen_constraints': {'m_min_fraction':0.1,'m_max_fraction':1.0},
    'minimum_stable_load': {'PEMFC':0.15,'PEMEL':0.15},
    'topology': {'P_aux_fraction_of_installed_power':0.02}}
coal = CoalitionState.from_code('1111')
load = np.ones(672) * 50; pv = np.ones(672) * 60
cmds, state = rule_based_dispatch(load, pv, ems)

# 1. EOL守卫
print('[1] EOL守卫边界测试')
dD0 = compute_damage(hp_bat, cmds, config_dict, D_state={'bat':0,'fc':0,'el':0}, coalition=coal)
print(f'  D=0: dD_bat_sum={np.sum(dD0["dD_bat"]):.6f}')
dD5 = compute_damage(hp_bat, cmds, config_dict, D_state={'bat':0.5,'fc':0,'el':0}, coalition=coal)
print(f'  D=0.5: dD_bat_sum={np.sum(dD5["dD_bat"]):.6f}')
dD99 = compute_damage(hp_bat, cmds, config_dict, D_state={'bat':0.99,'fc':0,'el':0}, coalition=coal)
print(f'  D=0.99: dD_bat_sum={np.sum(dD99["dD_bat"]):.6f}')
dD100 = compute_damage(hp_bat, cmds, config_dict, D_state={'bat':1.0,'fc':0,'el':0}, coalition=coal)
print(f'  D=1.0: dD_bat_sum={np.sum(dD100["dD_bat"]):.6f} (应=0)')

# 2. 功率守恒
print('[2] 功率守恒')
print(f'  残差: {state["power_residual"]:.2e}')

# 3. 辅机trace
print('[3] 辅机模型 (S6)')
p_aux = state['p_aux']
print(f'  类型: {type(p_aux).__name__}, 长度: {len(p_aux) if hasattr(p_aux, "__len__") else 1}')
print(f'  均值: {np.mean(p_aux):.3f} kW')

# 4. 路径符号
print('[4] 路径符号 (S1 v3.0)')
from utils.run_VS3_path_identification import PATHS_6
expected = {'alpha1': 'BAT→FC', 'alpha2': 'EL→FC', 'beta1': 'BAT→EL',
            'beta2': 'FC→EL', 'gamma1': 'FC→BAT', 'gamma2': 'EL→BAT'}
for k, v in expected.items():
    actual = PATHS_6[k]['desc']
    status = 'PASS' if actual == v else 'FAIL'
    print(f'  {k}: {actual} (期望={v}) {status}')

checks = [
    ('S12-01', 'EOL守卫 D=1.0→0', abs(np.sum(dD100['dD_bat'])) < 1e-10),
    ('S12-02', '功率守恒', state['power_residual'] < 1e-3),
    ('S12-03', '辅机trace长度=672', len(p_aux) == 672),
    ('S12-04', '路径alpha1=BAT→FC', PATHS_6['alpha1']['desc'] == 'BAT→FC'),
    ('S12-05', '路径alpha2=EL→FC', PATHS_6['alpha2']['desc'] == 'EL→FC'),
    ('S12-06', '路径beta1=BAT→EL', PATHS_6['beta1']['desc'] == 'BAT→EL'),
    ('S12-07', '路径beta2=FC→EL', PATHS_6['beta2']['desc'] == 'FC→EL'),
    ('S12-08', '路径gamma1=FC→BAT', PATHS_6['gamma1']['desc'] == 'FC→BAT'),
    ('S12-09', '路径gamma2=EL→BAT', PATHS_6['gamma2']['desc'] == 'EL→BAT'),
]
np_chk = sum(1 for _, _, p in checks if p)
print(f'\nS12回归测试 ({np_chk}/{len(checks)} PASS):')
for cid, desc, passed in checks:
    print(f'  {cid}: {"PASS" if passed else "FAIL"} - {desc}')

structured = [
    {'check_id': 'S12-01', 'name': 'EOL逐步封顶守卫', 'input': 'D_bat=1.0, 672步',
     'expected': 'sum(dD_bat)=0', 'actual': float(np.sum(dD100['dD_bat'])),
     'tolerance': 1e-10, 'passed': bool(checks[0][2])},
    {'check_id': 'S12-02', 'name': '逐步功率守恒', 'input': 'load=50kW, PV=60kW, 672步',
     'expected': 'max_abs_residual<1e-3 kW', 'actual': float(state['power_residual']),
     'tolerance': 1e-3, 'passed': bool(checks[1][2])},
    {'check_id': 'S12-03', 'name': '辅机功率时序长度', 'input': '672步调度',
     'expected': 'length=672', 'actual': int(len(p_aux)),
     'tolerance': 0, 'passed': bool(checks[2][2])},
]
for offset, (key, expected_desc) in enumerate(expected.items(), start=4):
    structured.append({
        'check_id': f'S12-{offset:02d}', 'name': f'路径符号 {key}',
        'input': 'PATHS_6 v3.0冻结定义', 'expected': expected_desc,
        'actual': PATHS_6[key]['desc'], 'tolerance': 'exact_string',
        'passed': bool(checks[offset - 1][2]),
    })

log_path = Path(r'D:\matlab\C\07_results\rectification_v2\tests\s12_regression.json')
log_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = log_path.with_suffix('.json.tmp')
tmp_path.write_text(json.dumps({
    'suite': 'S12', 'passed_count': np_chk, 'total_count': len(checks),
    'all_passed': np_chk == len(checks), 'checks': structured,
}, ensure_ascii=False, indent=2), encoding='utf-8')
tmp_path.replace(log_path)
print(f'  结构化日志: {log_path}')
if np_chk != len(checks):
    raise SystemExit(1)
