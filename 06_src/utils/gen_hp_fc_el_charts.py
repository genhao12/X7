# -*- coding: utf-8 -*-
"""
gen_hp_fc_el_charts.py
生成 hp_fc_mapping.png 和 hp_el_mapping.png — FC/EL健康-性能映射图
遵循M1/M2出版标准: 4子图、标签外置右方、300DPI、ColorBrewer配色
"""
import sys
import json
import numpy as np
from pathlib import Path

import matplotlib

# ===== 中文显示修复（统一注入） =====
import matplotlib as _mpl
_mpl.rcParams['font.family'] = 'sans-serif'
_mpl.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans']
_mpl.rcParams['axes.unicode_minus'] = False
# ===== end 中文显示修复 =====
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc
from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el

OUT_DIR = PROJECT_ROOT / "07_results" / "figures"
OUT_FC = OUT_DIR / "hp_fc_mapping.png"
OUT_EL = OUT_DIR / "hp_el_mapping.png"

# --- ColorBrewer 配色 ---
C_DATA = '#3288BD'       # 蓝(实测数据)
C_FIT = '#D73027'        # 红(拟合)
C_EXTRAP = '#FDAE61'     # 橙(外推)
C_ETA = '#66C2A5'        # 青绿(效率)
C_R = '#FC8D62'          # 橙(阻抗)
C_PMAX = '#ABDDA4'       # 浅绿(功率)
C_REF = '#999999'        # 灰(参考线)
C_EIS = '#7570B3'        # 紫(EIS)


def gen_fc_chart():
    """生成FC健康-性能映射图 (4子图)。"""
    print("[FC] 加载数据...")
    fc_data = _load_fc1_data()
    fit = fit_fc_aging(fc_data)

    with open(PROJECT_ROOT / "05_config" / "mvp_bol_parameters.json", 'r', encoding='utf-8') as f:
        bol_full = json.load(f)
    fc_bol = {'stack_config': bol_full['pemfc_bol']['stack_config'], 'fc_system_kw': 37.5}
    hp_func = interp_fc(fit, fc_bol)

    # 提取绘图数据
    traj = fc_data['voltage_trajectory']
    t_traj = traj['time_h']
    u_traj = traj['voltage_V']
    # 降采样用于绘图 (每1000点取1个)
    step = max(1, len(t_traj) // 500)
    t_plot = t_traj[::step]
    u_plot = u_traj[::step]

    eis = fc_data['eis_r_hfr']
    t_eis = eis['time_h']
    r_eis = eis['r_hfr_ohm'] * 1000  # mOhm
    d_eis = t_eis / fit['t_eol_h']
    r_eis_norm = r_eis / r_eis[0]

    pola = fc_data['bol_polarization']
    i_pola = pola['I_A']
    u_pola = pola['U_cell_V']

    # D范围
    d_range = np.linspace(0, 1, 100)
    eta_trace = np.array([hp_func(d)[0] for d in d_range])
    r_trace = np.array([hp_func(d)[1] * 1000 for d in d_range])  # mOhm
    pmax_trace = np.array([hp_func(d)[2] for d in d_range])

    fig = plt.figure(figsize=(14, 8))
    gs = GridSpec(2, 2, hspace=0.35, wspace=0.35, left=0.08, right=0.78, top=0.90, bottom=0.10)

    # (a) FC1电压轨迹 + 线性拟合
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.scatter(t_plot, u_plot, s=2, c=C_DATA, alpha=0.3, label='FC1 measured (70A)', rasterized=True)
    t_fit = np.linspace(0, max(t_traj), 100)
    u_fit = fit['u_bol_V'] - fit['k_v_mV_per_h'] / 1000 * t_fit
    ax1.plot(t_fit, u_fit, '-', color=C_FIT, lw=2, label=f'Linear fit (R²={fit["r2_voltage"]})')
    ax1.axvline(x=fit['t_eol_h'], color=C_REF, ls='--', lw=1, label=f't_EOL={fit["t_eol_h"]:.0f}h')
    ax1.set_xlabel('Time (h)')
    ax1.set_ylabel('Mean cell voltage (V)')
    ax1.set_title('(a) FC1 voltage degradation trajectory', fontsize=10, fontweight='bold')
    ax1.legend(loc='upper right', fontsize=7, framealpha=0.8)
    ax1.set_xlim(0, max(t_traj) * 1.1)
    ax1.grid(True, alpha=0.3)

    # (b) EIS R_hfr演化 + 退化期拟合
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.scatter(t_eis, r_eis, s=60, c=C_EIS, zorder=5, label='EIS R_hfr (70A)', edgecolors='k', linewidths=0.5)
    # 退化期拟合线 (r_eis已为mΩ, 直接使用)
    t_degr_fit = np.linspace(500, fit['t_eol_h'], 50)
    d_degr_fit = t_degr_fit / fit['t_eol_h']
    r_degr_fit = r_eis[0] * (1 + fit['alpha_r'] * d_degr_fit)
    ax2.plot(t_degr_fit, r_degr_fit, '-', color=C_FIT, lw=2,
             label=f'Degradation fit (α_R={fit["alpha_r"]})')
    ax2.axvline(x=500, color=C_REF, ls=':', lw=1, label='Degradation onset')
    ax2.axvline(x=fit['t_eol_h'], color=C_REF, ls='--', lw=1, label=f't_EOL={fit["t_eol_h"]:.0f}h')
    ax2.set_xlabel('Time (h)')
    ax2.set_ylabel('R_hfr (mΩ)')
    ax2.set_title('(b) EIS high-frequency resistance evolution', fontsize=10, fontweight='bold')
    ax2.legend(loc='upper left', fontsize=7, framealpha=0.8)
    ax2.grid(True, alpha=0.3)

    # (c) FC健康-性能映射 D -> (eta, R_hfr, P_max)
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.plot(d_range, eta_trace, '-', color=C_ETA, lw=2, label='η_frac (efficiency)')
    ax3.plot(d_range, r_trace / r_trace[0], '-', color=C_R, lw=2, label='R_hfr / R_BOL')
    ax3.plot(d_range, pmax_trace / pmax_trace[0], '-', color=C_PMAX, lw=2, label='P_max / P_rated')
    ax3.axvline(x=0.349, color=C_REF, ls=':', lw=1, label='Data boundary (D=0.349)')
    ax3.set_xlabel('D (degradation degree)')
    ax3.set_ylabel('Normalized value')
    ax3.set_title('(c) FC health-performance mapping D→(η, R, P)', fontsize=10, fontweight='bold')
    ax3.legend(loc='upper center', fontsize=7, framealpha=0.8, ncol=2)
    ax3.set_xlim(0, 1)
    ax3.set_ylim(0.85, 1.12)
    ax3.grid(True, alpha=0.3)

    # (d) BOL极化曲线 + EOL对比
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.plot(i_pola, u_pola, 'o-', color=C_DATA, lw=2, ms=4, label='BOL (T=0h)')
    # EOL: 电压降10%
    u_eol = u_pola * 0.90
    ax4.plot(i_pola, u_eol, 's--', color=C_FIT, lw=2, ms=4, label='EOL (D=1.0, -10%)')
    ax4.axhline(y=fit['u_bol_V'], color=C_REF, ls=':', lw=0.8, alpha=0.5)
    ax4.axhline(y=fit['u_bol_V'] * 0.90, color=C_REF, ls=':', lw=0.8, alpha=0.5)
    ax4.set_xlabel('Current (A)')
    ax4.set_ylabel('Cell voltage (V)')
    ax4.set_title('(d) BOL polarization curve & EOL shift', fontsize=10, fontweight='bold')
    ax4.legend(loc='upper right', fontsize=7, framealpha=0.8)
    ax4.grid(True, alpha=0.3)

    fig.suptitle('PEMFC Health-Performance Mapping (hp_fc_lite)\n'
                 f'Data: IEEE PHM 2014 FC1 | 1154h aging | 8 EIS time-points | Evidence: A',
                 fontsize=11, fontweight='bold', y=0.97)

    fig.savefig(OUT_FC, dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(f"[FC] Saved: {OUT_FC} ({OUT_FC.stat().st_size // 1024} KB)")


def gen_el_chart():
    """生成EL健康-性能映射图 (4子图)。"""
    print("[EL] 加载数据...")
    el_data = _load_el_bol_data()
    fit = fit_el_aging(el_data)

    el_bol = {'el_system_kw': 35.0, 'active_area_cm2': 50.0}
    hp_func = interp_el(fit, el_bol)

    # 提取绘图数据
    d_range = np.linspace(0, 1, 100)
    eta_frac = np.array([hp_func(d)[0] for d in d_range])
    r_ohm = np.array([hp_func(d)[1] for d in d_range])
    pmax = np.array([hp_func(d)[2] for d in d_range])
    v_cell = 1.75 * (1 + 0.10 * d_range)
    eta_abs = 1.253 / v_cell

    # BOL I-V曲线 (简化线性模型)
    j_range = np.linspace(0, 2.0, 50)  # A/cm²
    slope_bol = (1.75 - 1.253) / 0.15
    v_bol_iv = 1.253 + slope_bol * j_range
    v_eol_iv = v_bol_iv * 1.10  # EOL: 电压增10%

    # NAICNO训练后参数对比
    trained = el_data['trained_parameters'].get('parameters', {})

    fig = plt.figure(figsize=(14, 8))
    gs = GridSpec(2, 2, hspace=0.35, wspace=0.35, left=0.08, right=0.78, top=0.90, bottom=0.10)

    # (a) BOL I-V曲线 + 工作点
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(j_range, v_bol_iv, '-', color=C_DATA, lw=2, label='BOL I-V (linear model)')
    ax1.plot(j_range, v_eol_iv, '--', color=C_FIT, lw=2, label='EOL I-V (+10%)')
    ax1.axhline(y=1.253, color=C_REF, ls=':', lw=1, label='V_rev_LHV=1.253V')
    ax1.axhline(y=1.481, color=C_REF, ls=':', lw=1, alpha=0.5, label='V_rev_HHV=1.481V')
    ax1.plot(0.15, 1.75, 'o', color=C_ETA, ms=8, zorder=5, label='BOL operating point')
    ax1.set_xlabel('Current density (A/cm²)')
    ax1.set_ylabel('Cell voltage (V)')
    ax1.set_title('(a) EL I-V curve (NAICNO digital twin BOL)', fontsize=10, fontweight='bold')
    ax1.legend(loc='upper right', fontsize=7, framealpha=0.8)
    ax1.set_xlim(0, 2.0)
    ax1.set_ylim(1.0, 2.2)
    ax1.grid(True, alpha=0.3)

    # (b) V_cell(D) 和 eta(D) 退化模型
    ax2 = fig.add_subplot(gs[0, 1])
    ax2_twin = ax2.twinx()
    l1, = ax2.plot(d_range, v_cell, '-', color=C_DATA, lw=2, label='V_cell (V)')
    l2, = ax2_twin.plot(d_range, eta_abs, '-', color=C_ETA, lw=2, label='η_LHV (absolute)')
    ax2.axhline(y=1.75, color=C_REF, ls=':', lw=0.8)
    ax2.axhline(y=1.925, color=C_REF, ls=':', lw=0.8, alpha=0.5)
    ax2.set_xlabel('D (degradation degree)')
    ax2.set_ylabel('V_cell (V)', color=C_DATA)
    ax2_twin.set_ylabel('η_LHV', color=C_ETA)
    ax2.tick_params(axis='y', labelcolor=C_DATA)
    ax2_twin.tick_params(axis='y', labelcolor=C_ETA)
    ax2.set_title('(b) EL voltage growth & efficiency decay', fontsize=10, fontweight='bold')
    lines = [l1, l2]
    labels = [l.get_label() for l in lines]
    ax2.legend(lines, labels, loc='upper center', fontsize=7, framealpha=0.8)
    ax2.set_xlim(0, 1)
    ax2.grid(True, alpha=0.3)

    # (c) EL健康-性能映射 D -> (eta_frac, R_ohm, P_max)
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.plot(d_range, eta_frac, '-', color=C_ETA, lw=2, label='η_frac (efficiency)')
    ax3.plot(d_range, r_ohm / r_ohm[0], '-', color=C_R, lw=2, label='R_ohm / R_BOL')
    ax3.plot(d_range, pmax / pmax[0], '-', color=C_PMAX, lw=2, label='P_max / P_rated')
    ax3.set_xlabel('D (degradation degree)')
    ax3.set_ylabel('Normalized value')
    ax3.set_title('(c) EL health-performance mapping D→(η, R, P)', fontsize=10, fontweight='bold')
    ax3.legend(loc='lower left', fontsize=7, framealpha=0.8)
    ax3.set_xlim(0, 1)
    ax3.set_ylim(0.88, 1.12)
    ax3.grid(True, alpha=0.3)

    # (d) FC vs EL退化对比
    ax4 = fig.add_subplot(gs[1, 1])
    # FC: eta_frac = 1 - 0.10*D (linear)
    fc_eta = 1.0 - 0.10 * d_range
    # EL: eta_frac = 1/(1+0.10*D) (nonlinear)
    el_eta = 1.0 / (1.0 + 0.10 * d_range)
    ax4.plot(d_range, fc_eta, '-', color=C_DATA, lw=2, label='FC: η=1-0.10·D (linear)')
    ax4.plot(d_range, el_eta, '-', color=C_FIT, lw=2, label='EL: η=1/(1+0.10·D) (nonlinear)')
    ax4.fill_between(d_range, fc_eta, el_eta, alpha=0.15, color=C_REF, label='Deviation band')
    ax4.set_xlabel('D (degradation degree)')
    ax4.set_ylabel('η_frac (normalized efficiency)')
    ax4.set_title('(d) FC vs EL efficiency degradation model', fontsize=10, fontweight='bold')
    ax4.legend(loc='lower left', fontsize=7, framealpha=0.8)
    ax4.set_xlim(0, 1)
    ax4.set_ylim(0.88, 1.02)
    ax4.grid(True, alpha=0.3)

    fig.suptitle('PEMEL Health-Performance Mapping (hp_el_lite)\n'
                 f'Data: NAICNO digital twin | BOL V=1.75V | EOL=+10% specific energy | Evidence: A (BOL) / B (degradation)',
                 fontsize=11, fontweight='bold', y=0.97)

    fig.savefig(OUT_EL, dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(f"[EL] Saved: {OUT_EL} ({OUT_EL.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    gen_fc_chart()
    gen_el_chart()
    print("\nDone. Both charts generated.")
