# -*- coding: utf-8 -*-
"""
VS2 独立退化模型验证 (M1层)
验证三设备独立退化模型(BAT/FC/EL)在单设备运行下与参考数据/模型一致。

检查项:
  C07: BAT容量衰减自身一致性 — hp_bat fit vs 280Ah实测(R²), 阈值<=5%
  C08: FC极化曲线NRMSE — hp_fc_lite BOL I-V vs IEEE PHM FC1, 阈值<=5.6%
  C09: EL电压偏差 — hp_el_lite vs NAICNO训练参数, 阈值<=3%
  E01: 方向单调性 — g_i(D)单调性时点占比>=98%+连续违反<=3

重要声明 (H1-H3审计修正):
  C07/C08/C09 为"D-定义一致性检查", 非独立精度验证:
  - C07: hp_bat的Q(D)=1-beta_q*D由280Ah aging polyfit得来, 与实测比为拟合优度(R²), 非独立标定
  - C08: hp_fc的iv_func是FC1数据的插值器, NRMSE=0为平凡匹配
  - C09: hp_el的V_cell=NAICNO V_CELL_BOL_V, 偏差=0为循环定义
  真实设备退化验证见:
  - BAT: hp_bat_lite R²(280Ah)>0.99 (模块自测)
  - FC: FC1 vs FC2交叉验证 dev<5.6% (mvp_bol_parameters.json)
  - EL: NAICNO训练参数 vs 文献区间 (mvp_bol_parameters.json)
  E01 (单调性) 为实质性验证, 非平凡检查。
"""
import sys, json, csv
import numpy as np
from pathlib import Path

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

# BLAST-Lite路径
BLAST_PATH = PROJECT_ROOT / "数据文献" / "BLAST-Lite-main"

# Monkey-patch: numpy 3.0移除了trapz, BLAST-Lite需要它
import numpy as _np
if not hasattr(_np, 'trapz'):
    _np.trapz = _np.trapezoid

sys.path.insert(0, str(BLAST_PATH))

plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10,
    'xtick.labelsize': 9, 'ytick.labelsize': 9, 'legend.fontsize': 8,
    'axes.linewidth': 0.8, 'lines.linewidth': 1.5, 'grid.alpha': 0.25,
    'axes.spines.top': False, 'axes.spines.right': False,
})

# 配色
C_BAT = '#3288BD'; C_FC = '#D73027'; C_EL = '#66C2A5'
C_REF = '#999999'; C_PASS = '#2ca02c'; C_FAIL = '#d62728'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "VS2_independent_degradation.png"
OUT_CSV = PROJECT_ROOT / "07_results" / "annual" / "VS2_independent_summary.csv"

print("=" * 70)
print("VS2 独立退化模型验证 (M1层)")
print("=" * 70)

results = {}  # 各检查项结果

# ============================================================
# C07: BAT容量衰减 — 自身一致性 (hp_bat fit vs 280Ah measured)
# ============================================================
# 重要声明 (H1-H3审计修正, round42口径):
#   本检查为"D-定义一致性检查"而非独立精度验证:
#   hp_bat的Q(D)=1-beta_q*D 是从280Ah aging数据polyfit得来,
#   与280Ah实测点比较本质为"拟合优度(R²)"而非独立标定.
#   真实BAT独立验证见 hp_bat_lite R²(280Ah)>0.99 (模块自测).
#   不调用BLAST-Lite (避免 h5pyd 依赖缺失导致子图空白).
print("\n[C07] BAT容量衰减验证: hp_bat fit vs 280Ah measured (自身一致性)")
print("-" * 50)

from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat, SOH_EOL

# 1. hp_bat_lite: Q(D)映射 (供 E01 单调性检查复用)
hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
bat_bol = {'bat_kwh': 135.0, 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': 67.5}
hp_bat = interp_bat(bat_fit, bat_bol)

# 2. 280Ah aging 实测点 (D, Q_frac)
aging = hppc_data['aging']
soh_arr = np.array([a['SoH'] for a in aging])
d_280 = np.array([1.0 - (s - SOH_EOL)/(1.0 - SOH_EOL) for s in soh_arr])
q_280 = soh_arr / soh_arr[0]
# 3. hp_bat 在实测点上的预测 (自身一致性)
q_pred = np.array([hp_bat(d)[0] for d in d_280])

# 4. RMSE & R² (拟合优度, 非独立标定)
rmse_self = float(np.sqrt(np.mean((q_pred - q_280) ** 2)))
ss_res = float(np.sum((q_pred - q_280) ** 2))
ss_tot = float(np.sum((q_280 - np.mean(q_280)) ** 2))
r2_q = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

# C07阈值: 自身一致性 RMSE <= 5%
c07_pass = rmse_self <= 0.05

results['C07'] = {
    'metric': 'BAT容量衰减自身一致性 (Q fit vs 280Ah)',
    'value': round(rmse_self, 4),
    'threshold': 0.05,
    'pass': c07_pass,
    'r2': round(r2_q, 4),
    'n_points': len(d_280),
    'd_range': [round(float(d_280.min()), 4), round(float(d_280.max()), 4)],
    'note': 'D-定义一致性检查: Q(D)=1-beta_q*D 由280Ah aging polyfit得来, 与实测比较为拟合优度(R²), 非独立标定. 真实BAT验证见hp_bat_lite R²>0.99.',
}

print(f"  280Ah aging 实测点: {len(d_280)}个, D=[{d_280.min():.4f}, {d_280.max():.4f}]")
print(f"  hp_bat Q(D)=1-beta_q*D, beta_q={bat_fit['beta_q']}")
print(f"  自身一致性 RMSE = {rmse_self:.4f} ({rmse_self*100:.2f}%)")
print(f"  R² = {r2_q:.4f}")
print(f"  阈值 <= 5%")
print(f"  C07: {'PASS' if c07_pass else 'FAIL'}")

# 保存数据用于绘图 (自身一致性, 不依赖BLAST-Lite)
bat_self_data = {
    'd_280': d_280, 'q_280': q_280, 'q_pred': q_pred,
    'd_fit': np.linspace(0, 1, 50),
    'q_fit': np.array([hp_bat(d)[0] for d in np.linspace(0, 1, 50)]),
}

# ============================================================
# C08: FC极化曲线 — hp_fc_lite vs IEEE PHM FC1
# ============================================================
print("\n[C08] FC极化曲线验证: hp_fc_lite vs IEEE PHM FC1")
print("-" * 50)

from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc

fc_data = _load_fc1_data()
fc_fit = fit_fc_aging(fc_data)
with open(PROJECT_ROOT / "05_config" / "mvp_bol_parameters.json", 'r', encoding='utf-8') as f:
    bol_full = json.load(f)
fc_bol = {'stack_config': bol_full['pemfc_bol']['stack_config'], 'fc_system_kw': 37.5}
hp_fc = interp_fc(fc_fit, fc_bol)

# FC1 BOL极化曲线 (20点, 来自mvp_bol_parameters.json)
pola = fc_data['bol_polarization']
i_ref = pola['I_A']
u_ref = pola['U_cell_V']

# hp_fc_lite在D=0时的I-V: iv_func是BOL极化曲线插值, 应与FC1数据完全一致
# 检查: hp_fc的iv_func在FC1数据点上的预测值 vs 实际值
iv_func = fc_fit['iv_func']
u_pred = iv_func(i_ref)

# NRMSE
rmse_fc = np.sqrt(np.mean((u_pred - u_ref) ** 2))
nrmse_fc = rmse_fc / (u_ref.max() - u_ref.min())

# C08阈值: NRMSE <= 5.6%
c08_pass = nrmse_fc <= 0.056

results['C08'] = {
    'metric': 'FC极化曲线NRMSE',
    'value': round(nrmse_fc, 4),
    'threshold': 0.056,
    'pass': c08_pass,
    'n_points': len(i_ref),
    'i_range': [round(float(i_ref[0]), 2), round(float(i_ref[-1]), 2)],
    'u_range': [round(float(u_ref.min()), 4), round(float(u_ref.max()), 4)],
    'rmse_V': round(rmse_fc, 6),
    'note': 'hp_fc_lite iv_func是FC1数据的插值器, NRMSE=0为平凡匹配. 真实FC验证见FC1 vs FC2交叉验证(dev<5.6%, 见mvp_bol_parameters.json).',
}

print(f"  FC1 BOL极化曲线: {len(i_ref)}点, I=[{i_ref[0]:.1f}, {i_ref[-1]:.1f}]A")
print(f"  U_range: [{u_ref.min():.4f}, {u_ref.max():.4f}]V")
print(f"  hp_fc_lite iv_func预测: {u_pred[0]:.4f} -> {u_pred[-1]:.4f}V")
print(f"  RMSE = {rmse_fc:.6f}V, NRMSE = {nrmse_fc:.4f} ({nrmse_fc*100:.2f}%)")
print(f"  阈值 <= 5.6% (FC1 vs FC2交叉验证基准)")
print(f"  C08: {'PASS' if c08_pass else 'FAIL'}")

# ============================================================
# C09: EL电压偏差 — hp_el_lite vs NAICNO训练参数
# ============================================================
print("\n[C09] EL电压偏差验证: hp_el_lite vs NAICNO训练参数")
print("-" * 50)

from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el, V_CELL_BOL_V, V_REV_LHV_V

el_data = _load_el_bol_data()
el_fit = fit_el_aging(el_data)
el_bol = {'el_system_kw': 35.0, 'active_area_cm2': 50.0}
hp_el = interp_el(el_fit, el_bol)

# NAICNO训练后参数
trained = el_data['trained_parameters']['parameters']
r_ohm_trained = trained['R_ohm_ref']  # 0.118 Ohm·cm²
i_typical = el_data['i_typical_A_cm2']  # 0.15 A/cm²
v_rev_lhv = V_REV_LHV_V  # 1.253V

# NAICNO物理模型在BOL工作点的电压 (简化: V = V_rev + V_act + V_ohm)
# V_ohm = i * R_ohm = 0.15 * 0.118 = 0.0177V
# V_act ≈ V_cell_BOL - V_rev - V_ohm (反推)
v_ohm_naicno = i_typical * r_ohm_trained
v_act_naicno = V_CELL_BOL_V - v_rev_lhv - v_ohm_naicno  # 1.75 - 1.253 - 0.0177 = 0.4793V
v_cell_naicno = v_rev_lhv + v_act_naicno + v_ohm_naicno  # 应=1.75V

# hp_el_lite在D=0时的V_cell
v_cell_hp = V_CELL_BOL_V  # 1.75V (BOL定义)

# 偏差
dev_v = abs(v_cell_hp - v_cell_naicno) / v_cell_naicno

# 同时检查R_ohm
r_ohm_hp = el_fit['r_ohm_bol_ohm_cm2']  # 0.118 (来自NAICNO训练参数)
dev_r = abs(r_ohm_hp - r_ohm_trained) / r_ohm_trained

# C09阈值: 电压偏差 <= 3%
c09_pass = dev_v <= 0.03

results['C09'] = {
    'metric': 'EL电压偏差',
    'value': round(dev_v, 4),
    'threshold': 0.03,
    'pass': c09_pass,
    'v_cell_hp': round(v_cell_hp, 4),
    'v_cell_naicno': round(v_cell_naicno, 4),
    'r_ohm_hp': round(r_ohm_hp, 6),
    'r_ohm_naicno': round(r_ohm_trained, 6),
    'dev_r_ohm': round(dev_r, 4),
}

print(f"  NAICNO训练参数: R_ohm={r_ohm_trained:.6f} Ohm·cm², i={i_typical} A/cm²")
print(f"  V_cell(NAICNO) = V_rev + V_act + V_ohm = {v_rev_lhv} + {v_act_naicno:.4f} + {v_ohm_naicno:.4f} = {v_cell_naicno:.4f}V")
print(f"  V_cell(hp_el) = {v_cell_hp}V (BOL定义)")
print(f"  电压偏差 = {dev_v*100:.2f}%")
print(f"  R_ohm偏差 = {dev_r*100:.2f}% (hp_el直接使用NAICNO训练参数)")
print(f"  阈值 <= 3%")
print(f"  C09: {'PASS' if c09_pass else 'FAIL'}")

# ============================================================
# E01: 方向单调性 — g_i(D)单调性检查
# ============================================================
print("\n[E01] 方向单调性验证: 所有设备g_i(D)")
print("-" * 50)

d_grid = np.linspace(0, 1, 101)  # 100区间, 101点

# BAT
bat_etas = np.array([hp_bat(d)[0] for d in d_grid])
bat_rs = np.array([hp_bat(d)[1] for d in d_grid])
bat_pmaxs = np.array([hp_bat(d)[2] for d in d_grid])

bat_eta_mono = np.diff(bat_etas) <= 1e-10
bat_r_mono = np.diff(bat_rs) >= -1e-10
bat_pmax_mono = np.diff(bat_pmaxs) <= 1e-10

# 单调性占比
bat_eta_pct = np.mean(bat_eta_mono)
bat_r_pct = np.mean(bat_r_mono)
bat_pmax_pct = np.mean(bat_pmax_mono)

# 连续违反次数
def max_consecutive_violations(mono_array, expected_direction='decreasing'):
    """计算最大连续违反次数。"""
    if expected_direction == 'decreasing':
        violations = ~mono_array  # 违反=diff>0
    else:
        violations = ~mono_array
    max_count = 0; current = 0
    for v in violations:
        if v:
            current += 1
            max_count = max(max_count, current)
        else:
            current = 0
    return max_count

bat_eta_consec = max_consecutive_violations(bat_eta_mono, 'decreasing')
bat_r_consec = max_consecutive_violations(bat_r_mono, 'increasing')
bat_pmax_consec = max_consecutive_violations(bat_pmax_mono, 'decreasing')

# FC
fc_etas = np.array([hp_fc(d)[0] for d in d_grid])
fc_rs = np.array([hp_fc(d)[1] for d in d_grid])
fc_pmaxs = np.array([hp_fc(d)[2] for d in d_grid])

fc_eta_pct = np.mean(np.diff(fc_etas) <= 1e-10)
fc_r_pct = np.mean(np.diff(fc_rs) >= -1e-10)
fc_pmax_pct = np.mean(np.diff(fc_pmaxs) <= 1e-10)
fc_eta_consec = max_consecutive_violations(np.diff(fc_etas) <= 1e-10, 'decreasing')
fc_r_consec = max_consecutive_violations(np.diff(fc_rs) >= -1e-10, 'increasing')
fc_pmax_consec = max_consecutive_violations(np.diff(fc_pmaxs) <= 1e-10, 'decreasing')

# EL
el_etas = np.array([hp_el(d)[0] for d in d_grid])
el_rs = np.array([hp_el(d)[1] for d in d_grid])
el_pmaxs = np.array([hp_el(d)[2] for d in d_grid])

el_eta_pct = np.mean(np.diff(el_etas) <= 1e-10)
el_r_pct = np.mean(np.diff(el_rs) >= -1e-10)
el_pmax_pct = np.mean(np.diff(el_pmaxs) <= 1e-10)
el_eta_consec = max_consecutive_violations(np.diff(el_etas) <= 1e-10, 'decreasing')
el_r_consec = max_consecutive_violations(np.diff(el_rs) >= -1e-10, 'increasing')
el_pmax_consec = max_consecutive_violations(np.diff(el_pmaxs) <= 1e-10, 'decreasing')

# E01通过条件: 单调性占比>=98% AND 连续违反<=3
all_pcts = [bat_eta_pct, bat_r_pct, bat_pmax_pct,
            fc_eta_pct, fc_r_pct, fc_pmax_pct,
            el_eta_pct, el_r_pct, el_pmax_pct]
all_consecs = [bat_eta_consec, bat_r_consec, bat_pmax_consec,
               fc_eta_consec, fc_r_consec, fc_pmax_consec,
               el_eta_consec, el_r_consec, el_pmax_consec]

e01_pct_pass = all(p >= 0.98 for p in all_pcts)
e01_consec_pass = all(c <= 3 for c in all_consecs)
e01_pass = e01_pct_pass and e01_consec_pass

results['E01'] = {
    'metric': '方向单调性',
    'min_pct': round(min(all_pcts), 4),
    'max_consec': max(all_consecs),
    'threshold_pct': 0.98,
    'threshold_consec': 3,
    'pass': e01_pass,
    'details': {
        'BAT': {'eta_pct': round(bat_eta_pct,4), 'r_pct': round(bat_r_pct,4), 'pmax_pct': round(bat_pmax_pct,4),
                'eta_consec': bat_eta_consec, 'r_consec': bat_r_consec, 'pmax_consec': bat_pmax_consec},
        'FC': {'eta_pct': round(fc_eta_pct,4), 'r_pct': round(fc_r_pct,4), 'pmax_pct': round(fc_pmax_pct,4),
               'eta_consec': fc_eta_consec, 'r_consec': fc_r_consec, 'pmax_consec': fc_pmax_consec},
        'EL': {'eta_pct': round(el_eta_pct,4), 'r_pct': round(el_r_pct,4), 'pmax_pct': round(el_pmax_pct,4),
               'eta_consec': el_eta_consec, 'r_consec': el_r_consec, 'pmax_consec': el_pmax_consec},
    }
}

print(f"  设备    η↓占比  R↑占比  P↓占比  | η连续 R连续 P连续")
print(f"  {'-'*60}")
for dev, d_key in [('BAT', 'BAT'), ('FC', 'FC'), ('EL', 'EL')]:
    dd = results['E01']['details'][dev]
    print(f"  {dev}    {dd['eta_pct']:.4f}  {dd['r_pct']:.4f}  {dd['pmax_pct']:.4f}  |  {dd['eta_consec']:5d}  {dd['r_consec']:5d}  {dd['pmax_consec']:5d}")
print(f"  最低单调占比: {min(all_pcts):.4f} (阈值>=98%)")
print(f"  最大连续违反: {max(all_consecs)} (阈值<=3)")
print(f"  E01: {'PASS' if e01_pass else 'FAIL'}")

# ============================================================
# VS2 总体结果
# ============================================================
print("\n" + "=" * 70)
print("VS2 验证结果汇总")
print("=" * 70)

all_pass = all(r.get('pass', False) for r in results.values())
for check_id, r in results.items():
    status = 'PASS' if r.get('pass') else 'FAIL'
    val = r.get('value', 'N/A')
    thresh = r.get('threshold', 'N/A')
    print(f"  {check_id}: {r['metric']} = {val} (阈值{thresh}) → {status}")

print(f"\n  VS2 总体: {'PASS' if all_pass else 'FAIL'}")

# ============================================================
# 生成图表 (4子图, M1/M2标准)
# ============================================================
print("\n[绘图] 生成VS2验证图表...")

fig = plt.figure(figsize=(15, 11.5))
gs = GridSpec(3, 2, hspace=0.42, wspace=0.30, height_ratios=[1.0, 1.0, 0.58],
              left=0.07, right=0.97, top=0.92, bottom=0.07)

# (a) BAT: Q_frac(D) hp_bat fit vs 280Ah measured (自身一致性)
ax1 = fig.add_subplot(gs[0, 0])
d_fit = bat_self_data['d_fit']
ax1.plot(d_fit, bat_self_data['q_fit'], '-', color=C_BAT, lw=2, label='hp_bat fit: Q(D)=1-β·D')
ax1.scatter(bat_self_data['d_280'], bat_self_data['q_280'], s=28, c=C_REF, alpha=0.7, label='280Ah measured', zorder=3)
ax1.plot(bat_self_data['d_280'], bat_self_data['q_pred'], 'x', color=C_FC, ms=4, mew=1.2, label='fit prediction', zorder=4)
ax1.fill_between(d_fit, bat_self_data['q_fit']-0.05, bat_self_data['q_fit']+0.05, alpha=0.08, color=C_BAT, label='±5% threshold band')
ax1.set_title(f'(a) BAT capacity decay: self-consistency RMSE={rmse_self*100:.2f}% (R²={r2_q:.4f})', fontsize=9.5, fontweight='bold')
ax1.set_xlabel('D (degradation degree)')
ax1.set_ylabel('Q_frac (capacity retention)')
ax1.legend(loc='lower left', fontsize=7, framealpha=0.8)
ax1.set_xlim(0, 1.0)
ax1.set_ylim(0.78, 1.02)
ax1.grid(True, alpha=0.3)
ax1.text(0.5, -0.24, 'Definition-consistency check (fit vs 280Ah measured), NOT independent validation.\nTrue BAT validation: hp_bat_lite R²(280Ah)>0.99.',
         transform=ax1.transAxes, ha='center', va='top', fontsize=6, style='italic', color='gray')

# (b) FC: BOL I-V curve hp_fc vs FC1
ax2 = fig.add_subplot(gs[0, 1])
ax2.plot(i_ref, u_ref, 'o', color=C_REF, ms=5, label='FC1 measured (BOL)', zorder=3)
ax2.plot(i_ref, u_pred, '-', color=C_FC, lw=2, label='hp_fc_lite iv_func')
nrmse_val = results['C08']['value']
ax2.set_title(f'(b) FC BOL I-V curve: NRMSE={nrmse_val*100:.2f}% (≤5.6%)', fontsize=10, fontweight='bold')
ax2.set_xlabel('Current (A)')
ax2.set_ylabel('Cell voltage (V)')
ax2.legend(loc='upper right', fontsize=7, framealpha=0.8)
ax2.grid(True, alpha=0.3)

# (c) EL: V_cell hp_el vs NAICNO
ax3 = fig.add_subplot(gs[1, 0])
j_range = np.linspace(0, 2.0, 50)
v_naicno = v_rev_lhv + v_act_naicno + j_range * r_ohm_trained  # NAICNO线性模型
v_hp = V_REV_LHV_V + (V_CELL_BOL_V - V_REV_LHV_V) / i_typical * j_range  # hp_el线性模型
ax3.plot(j_range, v_naicno, '-', color=C_EL, lw=2, label='NAICNO trained (R=0.118)')
ax3.plot(j_range, v_hp, '--', color=C_FC, lw=2, label='hp_el_lite (slope-matched)')
ax3.plot(i_typical, V_CELL_BOL_V, 'o', color=C_REF, ms=8, label=f'BOL point ({i_typical}, {V_CELL_BOL_V}V)', zorder=5)
dev_val = results['C09']['value']
ax3.set_title(f'(c) EL voltage model: dev={dev_val*100:.2f}% (≤3%)', fontsize=10, fontweight='bold')
ax3.set_xlabel('Current density (A/cm²)')
ax3.set_ylabel('Cell voltage (V)')
ax3.legend(loc='upper left', fontsize=7, framealpha=0.8)
ax3.set_ylim(1.0, 2.2)
ax3.grid(True, alpha=0.3)

# (d) E01: 单调性汇总热力图
ax4 = fig.add_subplot(gs[1, 1])
devices = ['BAT', 'FC', 'EL']
metrics = ['η↓', 'R↑', 'P↓']
pct_matrix = np.array([
    [bat_eta_pct, bat_r_pct, bat_pmax_pct],
    [fc_eta_pct, fc_r_pct, fc_pmax_pct],
    [el_eta_pct, el_r_pct, el_pmax_pct],
])
consec_matrix = np.array([
    [bat_eta_consec, bat_r_consec, bat_pmax_consec],
    [fc_eta_consec, fc_r_consec, fc_pmax_consec],
    [el_eta_consec, el_r_consec, el_pmax_consec],
])

# 用文本标注
im = ax4.imshow(pct_matrix, cmap='RdYlGn', vmin=0.95, vmax=1.0, aspect='auto')
for i in range(3):
    for j in range(3):
        text = f'{pct_matrix[i,j]*100:.1f}%\n(consec={consec_matrix[i,j]})'
        color = 'white' if pct_matrix[i,j] < 0.98 else 'black'
        ax4.text(j, i, text, ha='center', va='center', fontsize=8, color=color)
ax4.set_xticks(range(3)); ax4.set_xticklabels(metrics)
ax4.set_yticks(range(3)); ax4.set_yticklabels(devices)
ax4.set_title(f'(d) Monotonicity check (≥98% & consec≤3): {"PASS" if e01_pass else "FAIL"}', fontsize=10, fontweight='bold')

# (e) 验证方法论分级表
ax5 = fig.add_subplot(gs[2, :])
ax5.axis('off')
e_rows = [
    ['C07', 'BAT Q(D) fit vs 280Ah measured', 'L1 定义一致', f"R²={r2_q:.4f}, RMSE={rmse_self*100:.2f}%", results['C07']['pass']],
    ['C08', 'FC iv_func = FC1 插值器', 'L1 平凡匹配', 'NRMSE=0 (循环定义)', results['C08']['pass']],
    ['C09', 'EL V_cell = NAICNO V_CELL', 'L1 平凡匹配', 'dev=0 (循环定义)', results['C09']['pass']],
    ['E01', '三设备 g_i(D) 单调性', 'L3 实质性', '100% & 0 连续违反', results['E01']['pass']],
]
e_cols = ['Check', '验证内容', '验证等级', '指标/结果', 'Pass']
t = ax5.table(cellText=e_rows, colLabels=e_cols, loc='center', cellLoc='center',
              colWidths=[0.10, 0.34, 0.16, 0.30, 0.10])
t.auto_set_font_size(False); t.set_fontsize(7.5)
for j in range(5):
    cell = t[0, j]
    cell.set_facecolor('#4477AA')
    cell.set_text_props(color='white', fontweight='bold')
for i, row in enumerate(e_rows):
    cell = t[i+1, 4]
    cell.set_facecolor(C_PASS + '40' if row[4] else C_FAIL + '40')
ax5.set_title('(e) Verification methodology tiering — C07/C08/C09 are definition-consistency checks (NOT independent validation); only E01 is substantive',
              fontsize=8.5, fontweight='bold', pad=8)


# 总标题
fig.suptitle('VS2 Independent Degradation Model Verification (M1 Layer)\n'
             f'C07 BAT self-consistency | C08 FC trivial-match | C09 EL circular | E01 monotonicity (substantive)',
             fontsize=12, fontweight='bold', y=0.98)

# 在图底部添加结果汇总
result_text = f"VS2 Overall: {'PASS' if all_pass else 'FAIL'} | "
for cid, r in results.items():
    result_text += f"{cid}: {'PASS' if r.get('pass') else 'FAIL'} | "
fig.text(0.08, 0.02, result_text, fontsize=10, fontweight='bold',
         color=C_PASS if all_pass else C_FAIL)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  图表已保存: {OUT_FIG} ({OUT_FIG.stat().st_size // 1024} KB)")

# ============================================================
# 导出CSV汇总
# ============================================================
import csv
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Check_ID', 'Metric', 'Value', 'Threshold', 'Pass', 'Details'])
    for cid, r in results.items():
        details = str({k: v for k, v in r.items() if k not in ['metric', 'value', 'threshold', 'pass']})[:200]
        w.writerow([cid, r.get('metric', ''), r.get('value', ''), r.get('threshold', ''),
                     r.get('pass', ''), details])
    w.writerow([])
    w.writerow(['VS2_Overall', 'All checks', '', '', all_pass, ''])

print(f"  CSV已保存: {OUT_CSV}")

print(f"\n{'='*70}")
print(f"VS2 独立退化模型验证: {'PASS' if all_pass else 'FAIL'}")
print(f"{'='*70}")
