# -*- coding: utf-8 -*-
"""
P6b L4 Parameter Sweep - L4参数敏感性扫描
单参数扫描: 3参数×5水平=15组 + 3×3×3网格=27组, 共42组年度Feedback仿真
使用13个代表性周(每月1周)加速4倍
"""
import sys, json, csv, time
import numpy as np
import pandas as pd
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
plt.rcParams.update({'axes.unicode_minus': False, 'font.family':'sans-serif','font.sans-serif':['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size':11,'axes.titlesize':10,'axes.labelsize':10,'xtick.labelsize':9,
    'ytick.labelsize':9,'legend.fontsize':8,'axes.linewidth':0.8,'lines.linewidth':1.5,
    'grid.alpha':0.25,'axes.spines.top':False,'axes.spines.right':False})

C_M2='#3288BD'; C_M3='#D73027'
SPW=672; WPY=52
OUT_DIR=PROJECT_ROOT/"07_results"/"l4_comparison"; OUT_DIR.mkdir(parents=True,exist_ok=True)
OUT_CSV=OUT_DIR/"l4_parameter_sweep.csv"
OUT_FIG=PROJECT_ROOT/"07_results"/"figures"/"l4_parameter_sweep.png"

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from ems.ems_l4_lite import dispatch_l4, L4Config
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

cfg_path=PROJECT_ROOT/"05_config"/"mvp_c1_config.json"
with open(cfg_path,'r',encoding='utf-8') as f: c1=json.load(f)
cap=c1['capacity']; ep=c1['ems_parameters']; bc=c1['battery_constraints']
hc=c1['hydrogen_constraints']; msl=c1['minimum_stable_load']; topo=c1['topology']
ems_cfg=EMSConfig(pv_kwp=cap['PV_kWp'],bat_kwh=cap['BAT_kWh'],pemfc_kw=cap['PEMFC_kW'],
    pemel_kw=cap['PEMEL_kW'],h2_kg=cap['H2_kg'],soc_min=bc['soc_min'],soc_max=bc['soc_max'],
    soc_initial=bc['soc_initial'],h2_min_fraction=hc['m_min_fraction'],h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=hc['m_initial_fraction'],h2_leakage_per_day=hc['leakage_rate_per_day'],
    start_stop_threshold=ep['start_stop_threshold_fraction'],minimum_run_time_min=float(ep['minimum_run_time']),
    critical_load_fraction=ep['critical_load_fraction'],p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'],min_stable_load_el=msl['PEMEL'],dt_min=15.0)
bat_rated_kw=cap['BAT_kWh']*0.5; bat_cap_kwh=cap['BAT_kWh']
config_dict={'capacity':cap,'battery_constraints':bc,'ems_parameters':ep,
    'hydrogen_constraints':hc,'minimum_stable_load':msl,'topology':topo}
hp_bat=interp_bat(fit_hppc(_load_280ah_data()),{'bat_kwh':cap['BAT_kWh'],
    'bat_voltage_nominal':3.2*48,'bat_rated_kw':bat_rated_kw})
coal=CoalitionState.from_code('1111')

# 加载数据
csv_path=str(PROJECT_ROOT/"02_data_processed"/"tradestreet_pipeline"/
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw=pd.read_csv(csv_path,parse_dates=['timestamp_local_civil'])
df=raw[raw['local_civil_year']==2017].copy()
df=df.rename(columns={'p_pv_available_reconstructed_nonnegative_kW':'P_pv_kW',
    'p_building_load_reconstructed_kW':'P_load_kW'})
df['P_pv_kW']=df['P_pv_kW'].ffill().bfill()
df['P_load_kW']=df['P_load_kW'].ffill().bfill()
pv_full=df['P_pv_kW'].values; load_full=df['P_load_kW'].values

# 代表性周采样: 每4周取1周, 共13周
SAMPLE_WEEKS = list(range(0, 52, 4))  # [0,4,8,...,48] = 13周
print("="*70)
print("P6b L4 Parameter Sweep - 参数敏感性扫描")
print("="*70)
print(f"  采样: {len(SAMPLE_WEEKS)}周 (每4周取1周, 代表全年)")
print(f"  BOL基线: 3.859229 (已知)")

BOL_TOTAL = 3.859229  # 已知BOL总损伤

def run_feedback(l4_config=None, use_l4=True):
    """运行代表性周Feedback仿真, 返回TE/FC/EL/LPSP等."""
    D={'bat':0.0,'fc':0.0,'el':0.0}
    total_bat=0; total_fc=0; total_el=0; total_lpsp=0; total_curt=0
    total_fc_e=0; total_el_e=0
    for w in SAMPLE_WEEKS:
        s=w*SPW; e=s+SPW; pv_w=pv_full[s:e]; load_w=load_full[s:e]
        D_bat=D['bat']
        if D_bat>0:
            q,r0,p_max=hp_bat(D_bat); bat_p=bat_rated_kw*q; bat_c=bat_cap_kwh*q
        else:
            bat_p=None; bat_c=None
        np.random.seed(42)
        if use_l4 and l4_config is not None:
            cmds,state=dispatch_l4(load_w,pv_w,ems_cfg,D_bat=D_bat,
                bat_p_max_kw=bat_p,bat_capacity_kwh=bat_c,l4_config=l4_config)
        else:
            cmds,state=rule_based_dispatch(load_w,pv_w,ems_cfg,
                bat_p_max_kw=bat_p,bat_capacity_kwh=bat_c)
        dD=compute_damage(hp_bat,cmds,config_dict,dt_min=15.0,D_state=D,coalition=coal)
        dD_b=float(np.sum(dD['dD_bat'])); dD_f=float(np.sum(dD['dD_fc'])); dD_e=float(np.sum(dD['dD_el']))
        D['bat']=min(D['bat']+dD_b,1.0); D['fc']=min(D['fc']+dD_f,1.0); D['el']=min(D['el']+dD_e,1.0)
        total_bat+=dD_b; total_fc+=dD_f; total_el+=dD_e
        total_lpsp+=float(np.sum(state['p_uns'])/max(np.sum(load_w),1e-6))
        total_curt+=float(np.sum(state['p_cur'])*0.25)
        total_fc_e+=float(np.sum(cmds[:,1])*0.25)
        total_el_e+=float(np.sum(cmds[:,2])*0.25)
    n=len(SAMPLE_WEEKS)
    fb_total=total_bat+total_fc+total_el
    # 年度外推: 13周结果×(52/13)=4倍
    fb_annual=fb_total*(52.0/n)
    bol_annual=BOL_TOTAL
    ae=fb_annual-bol_annual
    te=ae/abs(bol_annual) if abs(bol_annual)>1e-10 else 0
    return {
        'fb_total':fb_annual,'fb_bat':total_bat*(52.0/n),'fb_fc':total_fc*(52.0/n),
        'fb_el':total_el*(52.0/n),'ae':ae,'te':te,
        'lpsp':total_lpsp/n,'curt_MWh':total_curt*(52.0/n)/1000,
        'fc_e_MWh':total_fc_e*(52.0/n)/1000,'el_e_MWh':total_el_e*(52.0/n)/1000,
        'D_bat_eoy':D['bat'],'D_fc_eoy':D['fc'],'D_el_eoy':D['el'],
    }

# ================================================================
# 1. M2基线 (无L4)
# ================================================================
print("\n[1] M2基线 (无L4)...")
t0=time.time()
r_m2=run_feedback(use_l4=False)
print(f"  M2: TE={r_m2['te']*100:+.2f}%, FC={r_m2['fb_fc']:.4f}, EL={r_m2['fb_el']:.4f}, LPSP={r_m2['lpsp']*100:.2f}%")
print(f"  耗时: {time.time()-t0:.1f}s")

# ================================================================
# 2. 单参数扫描
# ================================================================
print("\n[2] 单参数扫描...")
# 参数水平
SOC_LEVELS = [0.05, 0.10, 0.15, 0.20, 0.30]
FC_LEVELS  = [0.25, 0.50, 1.00, 1.50, 2.00]
EL_LEVELS  = [0.10, 0.20, 0.30, 0.50, 1.00]

DEFAULT = L4Config()  # soc=0.15, fc=1.0, el=0.30

sweep_results = []

# R1: SOC带缩减
print("\n  R1 (soc_band_narrow_k) 扫描:")
for v in SOC_LEVELS:
    cfg_sw = L4Config(soc_band_narrow_k=v, fc_min_run_extend_k=DEFAULT.fc_min_run_extend_k,
                      el_ramp_limit_k=DEFAULT.el_ramp_limit_k)
    r = run_feedback(l4_config=cfg_sw)
    sweep_results.append({'sweep':'R1_soc', 'value':v, **r})
    print(f"    k={v:.2f}: TE={r['te']*100:+.2f}%, FC={r['fb_fc']:.4f}, EL={r['fb_el']:.4f}, LPSP={r['lpsp']*100:.2f}%")

# R2: FC最小运行延长
print("\n  R2 (fc_min_run_extend_k) 扫描:")
for v in FC_LEVELS:
    cfg_sw = L4Config(soc_band_narrow_k=DEFAULT.soc_band_narrow_k, fc_min_run_extend_k=v,
                      el_ramp_limit_k=DEFAULT.el_ramp_limit_k)
    r = run_feedback(l4_config=cfg_sw)
    sweep_results.append({'sweep':'R2_fc', 'value':v, **r})
    print(f"    k={v:.2f}: TE={r['te']*100:+.2f}%, FC={r['fb_fc']:.4f}, EL={r['fb_el']:.4f}, LPSP={r['lpsp']*100:.2f}%")

# R3: EL爬坡限制
print("\n  R3 (el_ramp_limit_k) 扫描:")
for v in EL_LEVELS:
    cfg_sw = L4Config(soc_band_narrow_k=DEFAULT.soc_band_narrow_k, fc_min_run_extend_k=DEFAULT.fc_min_run_extend_k,
                      el_ramp_limit_k=v)
    r = run_feedback(l4_config=cfg_sw)
    sweep_results.append({'sweep':'R3_el', 'value':v, **r})
    print(f"    k={v:.2f}: TE={r['te']*100:+.2f}%, FC={r['fb_fc']:.4f}, EL={r['fb_el']:.4f}, LPSP={r['lpsp']*100:.2f}%")

# ================================================================
# 3. 3×3×3 网格扫描 (关键区域)
# ================================================================
print("\n[3] 3×3×3 网格扫描...")
SOC_GRID = [0.05, 0.10, 0.15]
FC_GRID  = [0.25, 0.50, 1.00]
EL_GRID  = [0.10, 0.20, 0.30]
grid_results = []
for sv in SOC_GRID:
    for fv in FC_GRID:
        for ev in EL_GRID:
            cfg_g = L4Config(soc_band_narrow_k=sv, fc_min_run_extend_k=fv, el_ramp_limit_k=ev)
            r = run_feedback(l4_config=cfg_g)
            r['soc_k']=sv; r['fc_k']=fv; r['el_k']=ev
            grid_results.append(r)
            tag = f"soc={sv:.2f},fc={fv:.2f},el={ev:.2f}"
            print(f"    {tag}: TE={r['te']*100:+.2f}%, FC={r['fb_fc']:.4f}, EL={r['fb_el']:.4f}")

# ================================================================
# 4. 分析: 找最优参数 (TE最接近0)
# ================================================================
print("\n[4] 最优参数分析...")
# 单参数最优
for sweep_name in ['R1_soc', 'R2_fc', 'R3_el']:
    subset = [r for r in sweep_results if r['sweep']==sweep_name]
    best = min(subset, key=lambda r: abs(r['te']))
    print(f"  {sweep_name}: 最优value={best['value']:.2f}, TE={best['te']*100:+.2f}%")

# 网格最优
best_grid = min(grid_results, key=lambda r: abs(r['te']))
print(f"\n  网格最优: soc={best_grid['soc_k']:.2f}, fc={best_grid['fc_k']:.2f}, el={best_grid['el_k']:.2f}")
print(f"    TE={best_grid['te']*100:+.2f}%, FC={best_grid['fb_fc']:.4f}, EL={best_grid['fb_el']:.4f}")
print(f"    LPSP={best_grid['lpsp']*100:.2f}%, D_BAT={best_grid['D_bat_eoy']:.4f}")

# TE最接近0且LPSP增量<10%的可行解
feasible = [r for r in grid_results if r['lpsp'] < r_m2['lpsp']*1.10]
if feasible:
    best_feasible = min(feasible, key=lambda r: abs(r['te']))
    print(f"\n  可行最优(LPSP<+10%): soc={best_feasible['soc_k']:.2f}, fc={best_feasible['fc_k']:.2f}, el={best_feasible['el_k']:.2f}")
    print(f"    TE={best_feasible['te']*100:+.2f}%, LPSP={best_feasible['lpsp']*100:.2f}%")

# ================================================================
# 5. 导出CSV
# ================================================================
print("\n[5] 导出CSV...")
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    # M2基线
    w.writerow(['=== M2 Baseline ==='])
    w.writerow(['fb_total','fb_bat','fb_fc','fb_el','te_pct','lpsp_pct','curt_MWh','fc_e_MWh','el_e_MWh'])
    w.writerow([f"{r_m2['fb_total']:.8f}",f"{r_m2['fb_bat']:.8f}",f"{r_m2['fb_fc']:.8f}",f"{r_m2['fb_el']:.8f}",
        f"{r_m2['te']*100:.2f}",f"{r_m2['lpsp']*100:.4f}",f"{r_m2['curt_MWh']:.2f}",
        f"{r_m2['fc_e_MWh']:.2f}",f"{r_m2['el_e_MWh']:.2f}"])
    # 单参数扫描
    w.writerow([]); w.writerow(['=== Single-Parameter Sweep ==='])
    w.writerow(['Sweep','Value','fb_total','fb_bat','fb_fc','fb_el','te_pct','lpsp_pct','curt_MWh','fc_e_MWh','el_e_MWh',
        'D_bat_eoy','D_fc_eoy','D_el_eoy'])
    for r in sweep_results:
        w.writerow([r['sweep'],f"{r['value']:.2f}",f"{r['fb_total']:.8f}",f"{r['fb_bat']:.8f}",
            f"{r['fb_fc']:.8f}",f"{r['fb_el']:.8f}",f"{r['te']*100:.2f}",f"{r['lpsp']*100:.4f}",
            f"{r['curt_MWh']:.2f}",f"{r['fc_e_MWh']:.2f}",f"{r['el_e_MWh']:.2f}",
            f"{r['D_bat_eoy']:.6f}",f"{r['D_fc_eoy']:.6f}",f"{r['D_el_eoy']:.6f}"])
    # 网格扫描
    w.writerow([]); w.writerow(['=== 3x3x3 Grid Sweep ==='])
    w.writerow(['soc_k','fc_k','el_k','fb_total','fb_bat','fb_fc','fb_el','te_pct','lpsp_pct','curt_MWh',
        'fc_e_MWh','el_e_MWh','D_bat_eoy','D_fc_eoy','D_el_eoy'])
    for r in grid_results:
        w.writerow([f"{r['soc_k']:.2f}",f"{r['fc_k']:.2f}",f"{r['el_k']:.2f}",
            f"{r['fb_total']:.8f}",f"{r['fb_bat']:.8f}",f"{r['fb_fc']:.8f}",f"{r['fb_el']:.8f}",
            f"{r['te']*100:.2f}",f"{r['lpsp']*100:.4f}",f"{r['curt_MWh']:.2f}",
            f"{r['fc_e_MWh']:.2f}",f"{r['el_e_MWh']:.2f}",
            f"{r['D_bat_eoy']:.6f}",f"{r['D_fc_eoy']:.6f}",f"{r['D_el_eoy']:.6f}"])
print(f"  CSV: {OUT_CSV}")

# ================================================================
# 6. 生成图表
# ================================================================
print("\n[6] 生成参数敏感性图表...")
fig, axes = plt.subplots(2, 3, figsize=(16, 10))

# 单参数扫描图
for i, (sweep_name, label, xlabel) in enumerate([
    ('R1_soc', 'R1: SOC Band Narrow', 'soc_band_narrow_k'),
    ('R2_fc', 'R2: FC Min Run Extend', 'fc_min_run_extend_k'),
    ('R3_el', 'R3: EL Ramp Limit', 'el_ramp_limit_k')]):
    subset = [r for r in sweep_results if r['sweep']==sweep_name]
    vals = [r['value'] for r in subset]
    tes = [r['te']*100 for r in subset]
    fcs = [r['fb_fc'] for r in subset]
    els = [r['fb_el'] for r in subset]
    # TE
    axes[0,i].plot(vals, tes, 'o-', color=C_M3, linewidth=2, markersize=8)
    axes[0,i].axhline(r_m2['te']*100, color=C_M2, ls='--', lw=1.5, label=f'M2 TE={r_m2["te"]*100:.2f}%')
    axes[0,i].axhline(0, color='gray', ls=':', lw=0.8)
    axes[0,i].set_xlabel(xlabel); axes[0,i].set_ylabel('TE (%)')
    axes[0,i].set_title(f'({chr(97+i)}) {label}', fontsize=10, fontweight='bold')
    axes[0,i].legend(fontsize=8); axes[0,i].grid(True, alpha=0.3)
    # FC/EL damage
    ax2 = axes[0,i].twinx()
    ax2.plot(vals, fcs, 's--', color='#D73027', alpha=0.6, markersize=5, label='FC')
    ax2.plot(vals, els, '^--', color='#66C2A5', alpha=0.6, markersize=5, label='EL')
    ax2.set_ylabel('Damage', fontsize=9); ax2.legend(fontsize=7, loc='upper right')
    # LPSP
    lpsps = [r['lpsp']*100 for r in subset]
    axes[1,i].plot(vals, lpsps, 'o-', color='#9966CC', linewidth=2, markersize=8)
    axes[1,i].axhline(r_m2['lpsp']*100, color=C_M2, ls='--', lw=1.5, label=f'M2 LPSP={r_m2["lpsp"]*100:.2f}%')
    axes[1,i].set_xlabel(xlabel); axes[1,i].set_ylabel('LPSP (%)')
    axes[1,i].set_title(f'({chr(100+i)}) LPSP vs {label}', fontsize=10, fontweight='bold')
    axes[1,i].legend(fontsize=8); axes[1,i].grid(True, alpha=0.3)

fig.suptitle('P6b L4 Parameter Sensitivity Sweep (13 representative weeks, 2017)\n'
    f'M2 baseline: TE={r_m2["te"]*100:+.2f}%, LPSP={r_m2["lpsp"]*100:.2f}%',
    fontsize=12, fontweight='bold', y=0.98)
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight'); plt.close(fig)
print(f"  图表: {OUT_FIG}")

# ================================================================
# 7. 网格热图 (TE)
# ================================================================
print("\n[7] 生成网格TE热图...")
# 固定el_k=0.30, 展示soc_k × fc_k 的TE热图
fig2, axes2 = plt.subplots(1, 3, figsize=(18, 5))
for idx, el_val in enumerate(EL_GRID):
    subset = [r for r in grid_results if r['el_k']==el_val]
    te_matrix = np.full((3,3), np.nan)
    for r in subset:
        si = SOC_GRID.index(r['soc_k']); fi = FC_GRID.index(r['fc_k'])
        te_matrix[si, fi] = r['te']*100
    im = axes2[idx].imshow(te_matrix, cmap='RdYlGn_r', aspect='auto', vmin=-40, vmax=10)
    axes2[idx].set_xticks(range(3)); axes2[idx].set_xticklabels([f'{v:.2f}' for v in FC_GRID])
    axes2[idx].set_yticks(range(3)); axes2[idx].set_yticklabels([f'{v:.2f}' for v in SOC_GRID])
    axes2[idx].set_xlabel('fc_min_run_extend_k'); axes2[idx].set_ylabel('soc_band_narrow_k')
    axes2[idx].set_title(f'el_ramp_limit_k={el_val:.2f}', fontsize=10, fontweight='bold')
    for si in range(3):
        for fi in range(3):
            val = te_matrix[si, fi]
            if not np.isnan(val):
                axes2[idx].text(fi, si, f'{val:.1f}%', ha='center', va='center', fontsize=9,
                    color='white' if abs(val)>20 else 'black', fontweight='bold')
    plt.colorbar(im, ax=axes2[idx], label='TE (%)')

fig2.suptitle('L4 Parameter Grid: TE (%) by soc_k × fc_k × el_k\n'
    f'M2 baseline TE = {r_m2["te"]*100:+.2f}%', fontsize=12, fontweight='bold')
fig2.tight_layout(rect=[0, 0, 1, 0.92])
OUT_FIG2 = PROJECT_ROOT/"07_results"/"figures"/"l4_parameter_grid_heatmap.png"
fig2.savefig(OUT_FIG2, dpi=300, bbox_inches='tight'); plt.close(fig2)
print(f"  热图: {OUT_FIG2}")

# ================================================================
# 汇总
# ================================================================
print("\n" + "="*70)
print("P6b L4 Parameter Sweep 完成")
print("="*70)
print(f"  M2基线: TE={r_m2['te']*100:+.2f}%, LPSP={r_m2['lpsp']*100:.2f}%")
print(f"  单参数扫描: {len(sweep_results)}组")
print(f"  网格扫描: {len(grid_results)}组")
print(f"  总运行: {1+len(sweep_results)+len(grid_results)}组")
# 最优
best_single = min(sweep_results, key=lambda r: abs(r['te']))
print(f"  单参数最优: {best_single['sweep']}={best_single['value']:.2f}, TE={best_single['te']*100:+.2f}%")
print(f"  网格最优: soc={best_grid['soc_k']:.2f}, fc={best_grid['fc_k']:.2f}, el={best_grid['el_k']:.2f}, TE={best_grid['te']*100:+.2f}%")
if feasible:
    print(f"  可行最优(LPSP<+10%): soc={best_feasible['soc_k']:.2f}, fc={best_feasible['fc_k']:.2f}, el={best_feasible['el_k']:.2f}, TE={best_feasible['te']*100:+.2f}%")
print(f"  输出: {OUT_CSV}")
print(f"        {OUT_FIG}")
print(f"        {OUT_FIG2}")
print("="*70)
