# -*- coding: utf-8 -*-
"""
P2 Lifetime Extrapolation - 全寿命退化反馈仿真
基于P1年度仿真扩展到多年度(10年=520周), 对比两种维护策略:

策略A (EOL更换): 设备D>=1.0时重置D=0(模拟更换), 跟踪更换周期
策略B (无更换): D封顶1.0, 跟踪系统性能退化直到崩溃

对比BOL线性外推寿命(恒定年损伤速率, 无反馈)

输出:
  - 07_results/lifetime/lifetime_trajectory.csv  (520周D轨迹+更换事件)
  - 07_results/lifetime/lifetime_summary.csv     (年度+更换汇总)
  - 07_results/figures/lifetime_extrapolation.png (D轨迹+LPSP+更换标记)
"""
import sys, json, csv
import numpy as np
import pandas as pd
from pathlib import Path

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
from utils.fit_table import auto_fit_table

plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10,
    'xtick.labelsize': 9, 'ytick.labelsize': 9, 'legend.fontsize': 8,
    'axes.linewidth': 0.8, 'lines.linewidth': 1.5, 'grid.alpha': 0.25,
    'axes.spines.top': False, 'axes.spines.right': False,
})

C_BAT = '#3288BD'; C_FC = '#D73027'; C_EL = '#66C2A5'
C_REPL_BAT = '#5AA5D6'; C_REPL_FC = '#E84A4A'; C_REPL_EL = '#7AD5B0'
C_BOL = '#999999'; C_NOREPL = '#D73027'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "lifetime_extrapolation.png"
OUT_CSV_TRAJ = PROJECT_ROOT / "07_results" / "lifetime" / "lifetime_trajectory.csv"
OUT_CSV_SUMMARY = PROJECT_ROOT / "07_results" / "lifetime" / "lifetime_summary.csv"

STEPS_PER_DAY = 96
STEPS_PER_WEEK = STEPS_PER_DAY * 7
WEEKS_PER_YEAR = 52
MAX_YEARS = 10
MAX_WEEKS = MAX_YEARS * WEEKS_PER_YEAR

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)

cap = c1['capacity']; ems_p = c1['ems_parameters']
bat_c = c1['battery_constraints']; h2_c = c1['hydrogen_constraints']
msl = c1['minimum_stable_load']; topo = c1['topology']

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

config_dict = {
    'capacity': cap, 'battery_constraints': bat_c,
    'ems_parameters': ems_p, 'hydrogen_constraints': h2_c,
    'minimum_stable_load': msl, 'topology': topo,
}

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'],
                               'bat_voltage_nominal': 3.2*48,
                               'bat_rated_kw': bat_rated_kw})

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")

coalition_full = CoalitionState.from_code('1111')

print("=" * 70)
print("P2 Lifetime Extrapolation - 全寿命退化反馈仿真")
print("=" * 70)

print("\n[0] 加载2017年数据(循环用于多年度仿真)...")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df_2017 = raw[raw['local_civil_year'] == 2017].copy()
df_2017 = df_2017.rename(columns={
    'timestamp_local_civil': 'timestamp',
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW',
})
df_2017['P_pv_kW'] = df_2017['P_pv_kW'].ffill().bfill()
df_2017['P_load_kW'] = df_2017['P_load_kW'].ffill().bfill()
pv_base = df_2017['P_pv_kW'].values
load_base = df_2017['P_load_kW'].values
n_base = len(pv_base)
steps_per_year = WEEKS_PER_YEAR * STEPS_PER_WEEK
print(f"  2017年: {n_base}步, 仿真{MAX_YEARS}年={MAX_WEEKS}周={MAX_WEEKS*STEPS_PER_WEEK}步")


def get_week_data(year_idx, week_idx):
    """获取指定年/周的数据(循环2017年数据)."""
    s = week_idx * STEPS_PER_WEEK
    e = s + STEPS_PER_WEEK
    return pv_base[s:e], load_base[s:e]


# ================================================================
# 策略A: EOL更换 (D>=1.0时重置, 跟踪更换周期)
# ================================================================
print("\n[A] 策略A: EOL更换 (设备D>=1.0时重置D=0)...")
D_state_a = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
traj_a = []       # 每周D轨迹
replacements_a = {'bat': 0, 'fc': 0, 'el': 0}
repl_events_a = []  # 更换事件列表
annual_a = []     # 年度汇总

for yr in range(MAX_YEARS):
    yr_bat_dmg = 0.0; yr_fc_dmg = 0.0; yr_el_dmg = 0.0
    yr_lpsp = []; yr_curt = 0.0; yr_fc_e = 0.0; yr_el_e = 0.0

    for wk in range(WEEKS_PER_YEAR):
        global_wk = yr * WEEKS_PER_YEAR + wk
        pv_w, load_w = get_week_data(yr, wk)

        D_bat_curr = D_state_a['bat']
        q_frac, r0, p_max = hp_bat(D_bat_curr)
        bat_p_max_kw = bat_rated_kw * q_frac if D_bat_curr > 0 else None
        bat_cap_kwh = bat_capacity_kwh * q_frac if D_bat_curr > 0 else None

        np.random.seed(42)
        cmds, state = rule_based_dispatch(load_w, pv_w, cfg,
                                           bat_p_max_kw=bat_p_max_kw,
                                           bat_capacity_kwh=bat_cap_kwh)

        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                            D_state=D_state_a, coalition=coalition_full)

        dD_bat = float(np.sum(dD['dD_bat']))
        dD_fc = float(np.sum(dD['dD_fc']))
        dD_el = float(np.sum(dD['dD_el']))

        D_bat_before = D_state_a['bat']
        D_fc_before = D_state_a['fc']
        D_el_before = D_state_a['el']

        D_state_a['bat'] = min(D_state_a['bat'] + dD_bat, 1.0)
        D_state_a['fc'] = min(D_state_a['fc'] + dD_fc, 1.0)
        D_state_a['el'] = min(D_state_a['el'] + dD_el, 1.0)

        # EOL更换检测
        if D_state_a['bat'] >= 1.0 and D_bat_before < 1.0:
            replacements_a['bat'] += 1
            repl_events_a.append({'week': global_wk + 1, 'device': 'BAT',
                                   'D_before': D_state_a['bat']})
            D_state_a['bat'] = 0.0
        if D_state_a['fc'] >= 1.0 and D_fc_before < 1.0:
            replacements_a['fc'] += 1
            repl_events_a.append({'week': global_wk + 1, 'device': 'FC',
                                   'D_before': D_state_a['fc']})
            D_state_a['fc'] = 0.0
        if D_state_a['el'] >= 1.0 and D_el_before < 1.0:
            replacements_a['el'] += 1
            repl_events_a.append({'week': global_wk + 1, 'device': 'EL',
                                   'D_before': D_state_a['el']})
            D_state_a['el'] = 0.0

        lpsp_w = float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6))
        yr_bat_dmg += dD_bat; yr_fc_dmg += dD_fc; yr_el_dmg += dD_el
        yr_lpsp.append(lpsp_w)
        yr_curt += float(np.sum(state['p_cur']) * 0.25)
        yr_fc_e += float(np.sum(cmds[:, 1]) * 0.25)
        yr_el_e += float(np.sum(cmds[:, 2]) * 0.25)

        traj_a.append({
            'week': global_wk + 1, 'year': yr + 1, 'week_of_year': wk + 1,
            'D_bat': D_state_a['bat'], 'D_fc': D_state_a['fc'], 'D_el': D_state_a['el'],
            'dD_bat': dD_bat, 'dD_fc': dD_fc, 'dD_el': dD_el,
            'lpsp': lpsp_w,
        })

    annual_a.append({
        'year': yr + 1,
        'dD_bat': yr_bat_dmg, 'dD_fc': yr_fc_dmg, 'dD_el': yr_el_dmg,
        'dD_total': yr_bat_dmg + yr_fc_dmg + yr_el_dmg,
        'lpsp_mean': float(np.mean(yr_lpsp)),
        'curtailment_kWh': yr_curt, 'fc_energy_kWh': yr_fc_e, 'el_energy_kWh': yr_el_e,
        'repl_bat': replacements_a['bat'], 'repl_fc': replacements_a['fc'],
        'repl_el': replacements_a['el'],
        'D_bat_eoy': D_state_a['bat'], 'D_fc_eoy': D_state_a['fc'],
        'D_el_eoy': D_state_a['el'],
    })

    if (yr + 1) % 2 == 0 or yr == 0:
        print(f"  Yr{yr+1:2d}: D_end BAT={D_state_a['bat']:.4f} FC={D_state_a['fc']:.4f} EL={D_state_a['el']:.4f}, "
              f"repl BAT={replacements_a['bat']} FC={replacements_a['fc']} EL={replacements_a['el']}, "
              f"LPSP={np.mean(yr_lpsp)*100:.1f}%")

print(f"\n  策略A总更换: BAT={replacements_a['bat']}次, FC={replacements_a['fc']}次, EL={replacements_a['el']}次")
print(f"  策略A平均更换周期: BAT={MAX_YEARS/max(replacements_a['bat'],1):.2f}y, "
      f"FC={MAX_YEARS/max(replacements_a['fc'],1):.2f}y, EL={MAX_YEARS/max(replacements_a['el'],1):.2f}y")

# ================================================================
# 策略B: 无更换 (D封顶1.0, 系统退化到崩溃)
# ================================================================
print("\n[B] 策略B: 无更换 (D封顶1.0, 跟踪系统退化)...")
D_state_b = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
traj_b = []
annual_b = []
system_collapse_week = None

for yr in range(MAX_YEARS):
    yr_lpsp = []
    yr_bat_dmg = 0.0; yr_fc_dmg = 0.0; yr_el_dmg = 0.0

    for wk in range(WEEKS_PER_YEAR):
        global_wk = yr * WEEKS_PER_YEAR + wk
        pv_w, load_w = get_week_data(yr, wk)

        D_bat_curr = D_state_b['bat']
        q_frac, r0, p_max = hp_bat(D_bat_curr)
        bat_p_max_kw = bat_rated_kw * q_frac if D_bat_curr > 0 else None
        bat_cap_kwh = bat_capacity_kwh * q_frac if D_bat_curr > 0 else None

        np.random.seed(42)
        cmds, state = rule_based_dispatch(load_w, pv_w, cfg,
                                           bat_p_max_kw=bat_p_max_kw,
                                           bat_capacity_kwh=bat_cap_kwh)

        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                            D_state=D_state_b, coalition=coalition_full)

        dD_bat = float(np.sum(dD['dD_bat']))
        dD_fc = float(np.sum(dD['dD_fc']))
        dD_el = float(np.sum(dD['dD_el']))

        D_bat_before_b = D_state_b['bat']
        D_fc_before_b = D_state_b['fc']
        D_el_before_b = D_state_b['el']

        D_state_b['bat'] = min(D_state_b['bat'] + dD_bat, 1.0)
        D_state_b['fc'] = min(D_state_b['fc'] + dD_fc, 1.0)
        D_state_b['el'] = min(D_state_b['el'] + dD_el, 1.0)

        # Only count actual D increase (not virtual damage past EOL cap)
        yr_bat_dmg += D_state_b['bat'] - D_bat_before_b
        yr_fc_dmg += D_state_b['fc'] - D_fc_before_b
        yr_el_dmg += D_state_b['el'] - D_el_before_b

        lpsp_w = float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6))
        yr_lpsp.append(lpsp_w)

        traj_b.append({
            'week': global_wk + 1, 'year': yr + 1,
            'D_bat': D_state_b['bat'], 'D_fc': D_state_b['fc'], 'D_el': D_state_b['el'],
            'dD_bat': D_state_b['bat'] - D_bat_before_b,
            'dD_fc': D_state_b['fc'] - D_fc_before_b,
            'dD_el': D_state_b['el'] - D_el_before_b,
            'lpsp': lpsp_w,
        })

        # 系统崩溃检测: LPSP>80% 视为崩溃
        if system_collapse_week is None and lpsp_w > 0.80:
            system_collapse_week = global_wk + 1

    annual_b.append({
        'year': yr + 1,
        'dD_bat': yr_bat_dmg, 'dD_fc': yr_fc_dmg, 'dD_el': yr_el_dmg,
        'dD_total': yr_bat_dmg + yr_fc_dmg + yr_el_dmg,
        'lpsp_mean': float(np.mean(yr_lpsp)),
        'D_bat_eoy': D_state_b['bat'], 'D_fc_eoy': D_state_b['fc'],
        'D_el_eoy': D_state_b['el'],
    })

    if (yr + 1) % 2 == 0 or yr == 0:
        print(f"  Yr{yr+1:2d}: D_end BAT={D_state_b['bat']:.4f} FC={D_state_b['fc']:.4f} EL={D_state_b['el']:.4f}, "
              f"LPSP={np.mean(yr_lpsp)*100:.1f}%")

if system_collapse_week:
    print(f"\n  系统崩溃(LPSP>80%): Week {system_collapse_week} (Yr {system_collapse_week//WEEKS_PER_YEAR+1})")
else:
    print(f"\n  10年内未崩溃(LPSP始终<80%)")

# ================================================================
# BOL线性外推 (恒定年损伤, 无反馈)
# ================================================================
print("\n[C] BOL线性外推 (P1年度损伤速率恒定)...")
# P1 BOL年度损伤
bol_annual_bat = 0.90596308
bol_annual_fc = 1.29489792
bol_annual_el = 1.65836847

# Load BOL LPSP from P1 annual results
p1_summary_path = PROJECT_ROOT / "07_results" / "annual" / "annual_damage_summary.csv"
if p1_summary_path.exists():
    p1_df = pd.read_csv(p1_summary_path)
    p1_weekly = p1_df[p1_df['Week'].astype(str).str.match(r'^\d+$', na=False)]
    bol_lpsp = float(pd.to_numeric(p1_weekly['BOL_LPSP_pct'], errors='coerce').mean()) / 100.0
    print(f"  BOL LPSP (from P1): {bol_lpsp*100:.1f}%")
else:
    bol_lpsp = 0.444  # fallback: P1 BOL mode average
    print(f"  [警告] P1结果不存在, 使用fallback BOL LPSP: {bol_lpsp*100:.1f}%")

bol_eol_bat = 1.0 / bol_annual_bat if bol_annual_bat > 0 else float('inf')
bol_eol_fc = 1.0 / bol_annual_fc if bol_annual_fc > 0 else float('inf')
bol_eol_el = 1.0 / bol_annual_el if bol_annual_el > 0 else float('inf')

print(f"  BOL线性EOL: BAT={bol_eol_bat:.2f}y, FC={bol_eol_fc:.2f}y, EL={bol_eol_el:.2f}y")
print(f"  BOL更换次数(10年): BAT={int(MAX_YEARS/bol_eol_bat)}, "
      f"FC={int(MAX_YEARS/bol_eol_fc)}, EL={int(MAX_YEARS/bol_eol_el)}")

# ================================================================
# 策略对比汇总
# ================================================================
print("\n[D] 策略对比汇总...")

# 策略A反馈平均年损伤
fb_avg_bat = np.mean([a['dD_bat'] for a in annual_a])
fb_avg_fc = np.mean([a['dD_fc'] for a in annual_a])
fb_avg_el = np.mean([a['dD_el'] for a in annual_a])
fb_avg_total = fb_avg_bat + fb_avg_fc + fb_avg_el

# 寿命放大因子 (反馈 vs BOL)
lf_bat = fb_avg_bat / bol_annual_bat if bol_annual_bat > 0 else float('inf')
lf_fc = fb_avg_fc / bol_annual_fc if bol_annual_fc > 0 else float('inf')
lf_el = fb_avg_el / bol_annual_el if bol_annual_el > 0 else float('inf')

print(f"  平均年损伤: BAT FB={fb_avg_bat:.4f} vs BOL={bol_annual_bat:.4f} (x{lf_bat:.3f})")
print(f"  平均年损伤: FC  FB={fb_avg_fc:.4f} vs BOL={bol_annual_fc:.4f} (x{lf_fc:.3f})")
print(f"  平均年损伤: EL  FB={fb_avg_el:.4f} vs BOL={bol_annual_el:.4f} (x{lf_el:.3f})")
print(f"  寿命放大因子(反馈/BOL): BAT={1/lf_bat:.3f}x, FC={1/lf_fc:.3f}x, EL={1/lf_el:.3f}x")

# 策略A vs B LPSP对比
a_lpsp_10y = np.mean([a['lpsp_mean'] for a in annual_a])
b_lpsp_10y = np.mean([a['lpsp_mean'] for a in annual_b])
print(f"  10年平均LPSP: 策略A(更换)={a_lpsp_10y*100:.1f}%, 策略B(无更换)={b_lpsp_10y*100:.1f}%")

# ================================================================
# 导出CSV
# ================================================================
print("\n[E] 导出CSV...")

with open(OUT_CSV_TRAJ, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Week', 'Year', 'WeekOfYear',
                'A_D_bat', 'A_D_fc', 'A_D_el',
                'A_dD_bat', 'A_dD_fc', 'A_dD_el', 'A_LPSP_pct',
                'B_D_bat', 'B_D_fc', 'B_D_el',
                'B_dD_bat', 'B_dD_fc', 'B_dD_el', 'B_LPSP_pct'])
    for i in range(MAX_WEEKS):
        a = traj_a[i]
        b = traj_b[i]
        w.writerow([a['week'], a['year'], a.get('week_of_year', 0),
                    f"{a['D_bat']:.8f}", f"{a['D_fc']:.8f}", f"{a['D_el']:.8f}",
                    f"{a['dD_bat']:.8f}", f"{a['dD_fc']:.8f}", f"{a['dD_el']:.8f}",
                    f"{a['lpsp']*100:.4f}",
                    f"{b['D_bat']:.8f}", f"{b['D_fc']:.8f}", f"{b['D_el']:.8f}",
                    f"{b['dD_bat']:.8f}", f"{b['dD_fc']:.8f}", f"{b['dD_el']:.8f}",
                    f"{b['lpsp']*100:.4f}"])
    w.writerow([])
    w.writerow(['=== Replacement Events (Strategy A) ==='])
    w.writerow(['Week', 'Device', 'D_before_replacement'])
    for ev in repl_events_a:
        w.writerow([ev['week'], ev['device'], f"{ev['D_before']:.8f}"])
print(f"  Trajectory: {OUT_CSV_TRAJ}")

with open(OUT_CSV_SUMMARY, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Year',
                'A_dD_bat', 'A_dD_fc', 'A_dD_el', 'A_dD_total',
                'A_LPSP_pct', 'A_curtail_kWh', 'A_FC_kWh', 'A_EL_kWh',
                'A_repl_bat', 'A_repl_fc', 'A_repl_el',
                'A_D_bat_eoy', 'A_D_fc_eoy', 'A_D_el_eoy',
                'B_dD_bat', 'B_dD_fc', 'B_dD_el', 'B_dD_total',
                'B_LPSP_pct', 'B_D_bat_eoy', 'B_D_fc_eoy', 'B_D_el_eoy'])
    for yr in range(MAX_YEARS):
        a = annual_a[yr]
        b = annual_b[yr]
        w.writerow([yr + 1,
                    f"{a['dD_bat']:.8f}", f"{a['dD_fc']:.8f}", f"{a['dD_el']:.8f}",
                    f"{a['dD_total']:.8f}",
                    f"{a['lpsp_mean']*100:.4f}", f"{a['curtailment_kWh']:.2f}",
                    f"{a['fc_energy_kWh']:.2f}", f"{a['el_energy_kWh']:.2f}",
                    a['repl_bat'], a['repl_fc'], a['repl_el'],
                    f"{a['D_bat_eoy']:.8f}", f"{a['D_fc_eoy']:.8f}", f"{a['D_el_eoy']:.8f}",
                    f"{b['dD_bat']:.8f}", f"{b['dD_fc']:.8f}", f"{b['dD_el']:.8f}",
                    f"{b['dD_total']:.8f}",
                    f"{b['lpsp_mean']*100:.4f}",
                    f"{b['D_bat_eoy']:.8f}", f"{b['D_fc_eoy']:.8f}", f"{b['D_el_eoy']:.8f}"])
    w.writerow([])
    w.writerow(['=== Lifetime Comparison ==='])
    w.writerow(['Metric', 'BOL_linear', 'Strategy_A_replace', 'Strategy_B_no_replace'])
    w.writerow(['EOL_BAT(years)', f"{bol_eol_bat:.2f}",
                f"{MAX_YEARS/max(replacements_a['bat'],1):.2f}", 'N/A(capped)'])
    w.writerow(['EOL_FC(years)', f"{bol_eol_fc:.2f}",
                f"{MAX_YEARS/max(replacements_a['fc'],1):.2f}", 'N/A(capped)'])
    w.writerow(['EOL_EL(years)', f"{bol_eol_el:.2f}",
                f"{MAX_YEARS/max(replacements_a['el'],1):.2f}", 'N/A(capped)'])
    w.writerow(['Replacements_BAT(10y)', f"{int(MAX_YEARS/bol_eol_bat)}",
                replacements_a['bat'], 0])
    w.writerow(['Replacements_FC(10y)', f"{int(MAX_YEARS/bol_eol_fc)}",
                replacements_a['fc'], 0])
    w.writerow(['Replacements_EL(10y)', f"{int(MAX_YEARS/bol_eol_el)}",
                replacements_a['el'], 0])
    w.writerow(['Avg_annual_damage_BAT', f"{bol_annual_bat:.6f}", f"{fb_avg_bat:.6f}",
                f"{np.mean([b['dD_bat'] for b in annual_b]):.6f}"])
    w.writerow(['Avg_annual_damage_FC', f"{bol_annual_fc:.6f}", f"{fb_avg_fc:.6f}",
                f"{np.mean([b['dD_fc'] for b in annual_b]):.6f}"])
    w.writerow(['Avg_annual_damage_EL', f"{bol_annual_el:.6f}", f"{fb_avg_el:.6f}",
                f"{np.mean([b['dD_el'] for b in annual_b]):.6f}"])
    w.writerow(['Lifetime_amplification_BAT', '1.000', f"{lf_bat:.3f}",
                f"{np.mean([b['dD_bat'] for b in annual_b])/bol_annual_bat:.3f}"])
    w.writerow(['Lifetime_amplification_FC', '1.000', f"{lf_fc:.3f}",
                f"{np.mean([b['dD_fc'] for b in annual_b])/bol_annual_fc:.3f}"])
    w.writerow(['Lifetime_amplification_EL', '1.000', f"{lf_el:.3f}",
                f"{np.mean([b['dD_el'] for b in annual_b])/bol_annual_el:.3f}"])
    w.writerow(['10y_avg_LPSP(%)', f"{bol_lpsp*100:.1f}",
                f"{a_lpsp_10y*100:.1f}", f"{b_lpsp_10y*100:.1f}"])
    if system_collapse_week:
        w.writerow(['System_collapse_week', 'N/A', 'N/A', system_collapse_week])
    else:
        w.writerow(['System_collapse_week', 'N/A', 'N/A', 'None(>10y)'])
print(f"  Summary: {OUT_CSV_SUMMARY}")

# ================================================================
# 生成图表
# ================================================================
print("\n[F] 生成全寿命外推图表...")

fig = plt.figure(figsize=(16, 14))
gs = GridSpec(4, 2, hspace=0.45, wspace=0.30, left=0.07, right=0.93, top=0.93, bottom=0.06)

weeks = np.arange(1, MAX_WEEKS + 1)
years_ticks = np.arange(0, MAX_YEARS + 1) * WEEKS_PER_YEAR
year_labels = [f'Y{y}' for y in range(MAX_YEARS + 1)]

A_D_bat = np.array([t['D_bat'] for t in traj_a])
A_D_fc = np.array([t['D_fc'] for t in traj_a])
A_D_el = np.array([t['D_el'] for t in traj_a])
B_D_bat = np.array([t['D_bat'] for t in traj_b])
B_D_fc = np.array([t['D_fc'] for t in traj_b])
B_D_el = np.array([t['D_el'] for t in traj_b])
A_LPSP = np.array([t['lpsp']*100 for t in traj_a])
B_LPSP = np.array([t['lpsp']*100 for t in traj_b])

# (a) 策略A D轨迹 (EOL更换)
ax1 = fig.add_subplot(gs[0, 0])
ax1.plot(weeks, A_D_bat, color=C_BAT, label='D_BAT', linewidth=1.2)
ax1.plot(weeks, A_D_fc, color=C_FC, label='D_FC', linewidth=1.2)
ax1.plot(weeks, A_D_el, color=C_EL, label='D_EL', linewidth=1.2)
ax1.axhline(1.0, color='gray', ls='--', lw=0.6)
# 更换事件标记
for ev in repl_events_a:
    c = {'BAT': C_BAT, 'FC': C_FC, 'EL': C_EL}[ev['device']]
    ax1.axvline(ev['week'], color=c, ls=':', lw=0.5, alpha=0.5)
ax1.set_xticks(years_ticks); ax1.set_xticklabels(year_labels, fontsize=8)
ax1.set_ylabel('D (with replacement)')
ax1.set_title('(a) Strategy A: D Trajectory (EOL Replacement)', fontsize=10, fontweight='bold')
ax1.legend(fontsize=8); ax1.grid(True, alpha=0.3)
ax1.set_xlim(0, MAX_WEEKS)

# (b) 策略B D轨迹 (无更换)
ax2 = fig.add_subplot(gs[0, 1])
ax2.plot(weeks, B_D_bat, color=C_BAT, label='D_BAT', linewidth=1.2)
ax2.plot(weeks, B_D_fc, color=C_FC, label='D_FC', linewidth=1.2)
ax2.plot(weeks, B_D_el, color=C_EL, label='D_EL', linewidth=1.2)
ax2.axhline(1.0, color='gray', ls='--', lw=0.6)
if system_collapse_week:
    ax2.axvline(system_collapse_week, color='red', ls='--', lw=1.5, label=f'Collapse W{system_collapse_week}')
ax2.set_xticks(years_ticks); ax2.set_xticklabels(year_labels, fontsize=8)
ax2.set_ylabel('D (no replacement)')
ax2.set_title('(b) Strategy B: D Trajectory (No Replacement)', fontsize=10, fontweight='bold')
ax2.legend(fontsize=8); ax2.grid(True, alpha=0.3)
ax2.set_xlim(0, MAX_WEEKS)

# (c) LPSP对比
ax3 = fig.add_subplot(gs[1, 0])
# 4周移动平均平滑
window = 4
A_LPSP_smooth = np.convolve(A_LPSP, np.ones(window)/window, mode='valid')
B_LPSP_smooth = np.convolve(B_LPSP, np.ones(window)/window, mode='valid')
ax3.plot(weeks[:len(A_LPSP_smooth)], A_LPSP_smooth, color=C_BAT, label=f'A: Replace (10y avg={a_lpsp_10y*100:.1f}%)', linewidth=1.2)
ax3.plot(weeks[:len(B_LPSP_smooth)], B_LPSP_smooth, color=C_NOREPL, label=f'B: No replace (10y avg={b_lpsp_10y*100:.1f}%)', linewidth=1.2)
ax3.axhline(80, color='red', ls='--', lw=0.8, label='Collapse threshold (80%)')
ax3.set_xticks(years_ticks); ax3.set_xticklabels(year_labels, fontsize=8)
ax3.set_ylabel('LPSP (%)')
ax3.set_title('(c) Weekly LPSP Comparison (4-week MA)', fontsize=10, fontweight='bold')
ax3.legend(fontsize=8); ax3.grid(True, alpha=0.3)
ax3.set_xlim(0, MAX_WEEKS)

# (d) 年度损伤对比
ax4 = fig.add_subplot(gs[1, 1])
years = np.arange(1, MAX_YEARS + 1)
A_annual_total = np.array([a['dD_total'] for a in annual_a])
B_annual_total = np.array([b['dD_total'] for b in annual_b])
BOL_annual_total = bol_annual_bat + bol_annual_fc + bol_annual_el
ax4.bar(years - 0.25, A_annual_total, 0.25, label='A: Replace', color=C_BAT, alpha=0.8, edgecolor='k', linewidth=0.3)
ax4.bar(years, B_annual_total, 0.25, label='B: No replace', color=C_NOREPL, alpha=0.8, edgecolor='k', linewidth=0.3)
ax4.axhline(BOL_annual_total, color=C_BOL, ls='--', lw=1, label=f'BOL linear ({BOL_annual_total:.2f})')
ax4.set_xlabel('Year'); ax4.set_ylabel('Annual total dD')
ax4.set_title('(d) Annual Damage Comparison', fontsize=10, fontweight='bold')
ax4.legend(fontsize=8); ax4.grid(True, alpha=0.3, axis='y')

# (e) 累积更换次数
ax5 = fig.add_subplot(gs[2, 0])
cum_repl_bat = np.cumsum([1 if any(ev['week'] == w+1 and ev['device'] == 'BAT' for ev in repl_events_a) else 0 for w in range(MAX_WEEKS)])
cum_repl_fc = np.cumsum([1 if any(ev['week'] == w+1 and ev['device'] == 'FC' for ev in repl_events_a) else 0 for w in range(MAX_WEEKS)])
cum_repl_el = np.cumsum([1 if any(ev['week'] == w+1 and ev['device'] == 'EL' for ev in repl_events_a) else 0 for w in range(MAX_WEEKS)])
ax5.step(weeks, cum_repl_bat, color=C_BAT, label=f'BAT ({replacements_a["bat"]} total)', linewidth=1.5, where='post')
ax5.step(weeks, cum_repl_fc, color=C_FC, label=f'FC ({replacements_a["fc"]} total)', linewidth=1.5, where='post')
ax5.step(weeks, cum_repl_el, color=C_EL, label=f'EL ({replacements_a["el"]} total)', linewidth=1.5, where='post')
# BOL线性外推累计更换
bol_cum_bat = np.array([int(w / (bol_eol_bat * WEEKS_PER_YEAR)) for w in range(1, MAX_WEEKS+1)])
bol_cum_fc = np.array([int(w / (bol_eol_fc * WEEKS_PER_YEAR)) for w in range(1, MAX_WEEKS+1)])
bol_cum_el = np.array([int(w / (bol_eol_el * WEEKS_PER_YEAR)) for w in range(1, MAX_WEEKS+1)])
ax5.step(weeks, bol_cum_bat, color=C_BAT, ls='--', lw=0.8, alpha=0.5, label='BAT (BOL linear)')
ax5.step(weeks, bol_cum_fc, color=C_FC, ls='--', lw=0.8, alpha=0.5, label='FC (BOL linear)')
ax5.step(weeks, bol_cum_el, color=C_EL, ls='--', lw=0.8, alpha=0.5, label='EL (BOL linear)')
ax5.set_xticks(years_ticks); ax5.set_xticklabels(year_labels, fontsize=8)
ax5.set_ylabel('Cumulative replacements')
ax5.set_title('(e) Cumulative Replacements (Strategy A vs BOL)', fontsize=10, fontweight='bold')
ax5.legend(fontsize=7, ncol=2); ax5.grid(True, alpha=0.3)
ax5.set_xlim(0, MAX_WEEKS)

# (f) 年度LPSP趋势
ax6 = fig.add_subplot(gs[2, 1])
A_annual_lpsp = np.array([a['lpsp_mean']*100 for a in annual_a])
B_annual_lpsp = np.array([b['lpsp_mean']*100 for b in annual_b])
ax6.plot(years, A_annual_lpsp, 'o-', color=C_BAT, label='A: Replace', linewidth=1.5, markersize=5)
ax6.plot(years, B_annual_lpsp, 's-', color=C_NOREPL, label='B: No replace', linewidth=1.5, markersize=5)
ax6.axhline(80, color='red', ls='--', lw=0.8, label='Collapse (80%)')
ax6.set_xlabel('Year'); ax6.set_ylabel('Annual mean LPSP (%)')
ax6.set_title('(f) Annual LPSP Trend', fontsize=10, fontweight='bold')
ax6.legend(fontsize=8); ax6.grid(True, alpha=0.3)
ax6.set_xticks(years)

# (g) 寿命对比汇总表
ax7 = fig.add_subplot(gs[3, :])
ax7.axis('off')
table_data = [
    ['BAT', f'{bol_eol_bat:.2f}y', f'{MAX_YEARS/max(replacements_a["bat"],1):.2f}y',
     f'{int(MAX_YEARS/bol_eol_bat)}', f'{replacements_a["bat"]}',
     f'{lf_bat:.3f}x', f'{1/lf_bat:.3f}x'],
    ['FC', f'{bol_eol_fc:.2f}y', f'{MAX_YEARS/max(replacements_a["fc"],1):.2f}y',
     f'{int(MAX_YEARS/bol_eol_fc)}', f'{replacements_a["fc"]}',
     f'{lf_fc:.3f}x', f'{1/lf_fc:.3f}x'],
    ['EL', f'{bol_eol_el:.2f}y', f'{MAX_YEARS/max(replacements_a["el"],1):.2f}y',
     f'{int(MAX_YEARS/bol_eol_el)}', f'{replacements_a["el"]}',
     f'{lf_el:.3f}x', f'{1/lf_el:.3f}x'],
]
col_labels = ['Device', 'BOL EOL', 'A: Repl Cycle', 'BOL Repl(10y)', 'A: Repl(10y)',
              'Damage Amplif.', 'Lifetime Ratio']
table = ax7.table(cellText=table_data, colLabels=col_labels, loc='center',
                  cellLoc='center', colColours=['#4477AA']*7)
table.auto_set_font_size(False); table.set_fontsize(7.5)
auto_fit_table(ax7, table, x_scale=1.2)
ax7.set_title('(g) Lifetime Comparison Summary', fontsize=11, fontweight='bold', pad=10)

collapse_str = f'Collapse: W{system_collapse_week}' if system_collapse_week else 'No collapse (>10y)'
fig.suptitle(f'P2 Lifetime Extrapolation (10 years, 2017 data cycled)\n'
             f'Repl(10y): BAT={replacements_a["bat"]} FC={replacements_a["fc"]} EL={replacements_a["el"]} | '
             f'Damage Amplif: BAT={lf_bat:.2f}x FC={lf_fc:.2f}x EL={lf_el:.2f}x | {collapse_str}',
             fontsize=12, fontweight='bold', y=0.98)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  图表已保存: {OUT_FIG}")

# ================================================================
print("\n" + "=" * 70)
print("P2 Lifetime Extrapolation 完成")
print("=" * 70)
print(f"  策略A(EOL更换): BAT={replacements_a['bat']}次 FC={replacements_a['fc']}次 EL={replacements_a['el']}次")
print(f"  平均更换周期: BAT={MAX_YEARS/max(replacements_a['bat'],1):.2f}y "
      f"FC={MAX_YEARS/max(replacements_a['fc'],1):.2f}y EL={MAX_YEARS/max(replacements_a['el'],1):.2f}y")
print(f"  BOL线性寿命: BAT={bol_eol_bat:.2f}y FC={bol_eol_fc:.2f}y EL={bol_eol_el:.2f}y")
print(f"  损伤放大因子: BAT={lf_bat:.3f}x FC={lf_fc:.3f}x EL={lf_el:.3f}x")
if system_collapse_week:
    print(f"  策略B系统崩溃: Week {system_collapse_week} (Yr {system_collapse_week//WEEKS_PER_YEAR+1})")
else:
    print(f"  策略B: 10年内未崩溃")
print(f"  输出: {OUT_CSV_TRAJ}")
print(f"        {OUT_CSV_SUMMARY}")
print(f"        {OUT_FIG}")
print("=" * 70)
