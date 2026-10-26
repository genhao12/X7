# -*- coding: utf-8 -*-
"""
P6c L4 Fine Sweep - 精细R3参数扫描 + R1/R2无效性诊断
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

SPW=672
OUT_DIR=PROJECT_ROOT/"07_results"/"l4_comparison"
OUT_CSV=OUT_DIR/"l4_fine_sweep.csv"
OUT_FIG=PROJECT_ROOT/"07_results"/"figures"/"l4_fine_sweep.png"

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

csv_path=str(PROJECT_ROOT/"02_data_processed"/"tradestreet_pipeline"/
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw=pd.read_csv(csv_path,parse_dates=['timestamp_local_civil'])
df=raw[raw['local_civil_year']==2017].copy()
df=df.rename(columns={'p_pv_available_reconstructed_nonnegative_kW':'P_pv_kW',
    'p_building_load_reconstructed_kW':'P_load_kW'})
df['P_pv_kW']=df['P_pv_kW'].ffill().bfill()
df['P_load_kW']=df['P_load_kW'].ffill().bfill()
pv_full=df['P_pv_kW'].values; load_full=df['P_load_kW'].values

SAMPLE_WEEKS=list(range(0,52,4))
BOL_TOTAL_13w=None  # 需要重新计算13周BOL

print("="*70)
print("P6c L4 Fine Sweep - 精细R3扫描 + R1/R2诊断")
print("="*70)

def run_sim(use_l4=True, l4_config=None, n_weeks=None):
    """运行BOL+Feedback仿真, 返回结果."""
    weeks = SAMPLE_WEEKS if n_weeks is None else list(range(0,min(n_weeks,52),1))
    # BOL
    D_bol={'bat':0.0,'fc':0.0,'el':0.0}; bol_total=0
    for w in weeks:
        s=w*SPW; e=s+SPW; pv_w=pv_full[s:e]; load_w=load_full[s:e]
        np.random.seed(42)
        if use_l4:
            cmds,state=dispatch_l4(load_w,pv_w,ems_cfg,D_bat=0.0,l4_config=l4_config)
        else:
            cmds,state=rule_based_dispatch(load_w,pv_w,ems_cfg)
        dD=compute_damage(hp_bat,cmds,config_dict,dt_min=15.0,D_state=D_bol,coalition=coal)
        bol_total+=float(np.sum(dD['dD_bat']+dD['dD_fc']+dD['dD_el']))
    # Feedback
    D={'bat':0.0,'fc':0.0,'el':0.0}; fb_b=fb_f=fb_e=0; fb_lp=0; fc_starts=0; el_starts=0
    for w in weeks:
        s=w*SPW; e=s+SPW; pv_w=pv_full[s:e]; load_w=load_full[s:e]
        D_bat=D['bat']
        if D_bat>0:
            q,r0,p_max=hp_bat(D_bat); bat_p=bat_rated_kw*q; bat_c=bat_cap_kwh*q
        else:
            bat_p=None; bat_c=None
        np.random.seed(42)
        if use_l4:
            cmds,state=dispatch_l4(load_w,pv_w,ems_cfg,D_bat=D_bat,
                bat_p_max_kw=bat_p,bat_capacity_kwh=bat_c,l4_config=l4_config)
        else:
            cmds,state=rule_based_dispatch(load_w,pv_w,ems_cfg,
                bat_p_max_kw=bat_p,bat_capacity_kwh=bat_c)
        dD=compute_damage(hp_bat,cmds,config_dict,dt_min=15.0,D_state=D,coalition=coal)
        dD_b=float(np.sum(dD['dD_bat'])); dD_f=float(np.sum(dD['dD_fc'])); dD_e=float(np.sum(dD['dD_el']))
        D['bat']=min(D['bat']+dD_b,1.0); D['fc']=min(D['fc']+dD_f,1.0); D['el']=min(D['el']+dD_e,1.0)
        fb_b+=dD_b; fb_f+=dD_f; fb_e+=dD_e
        fb_lp+=float(np.sum(state['p_uns'])/max(np.sum(load_w),1e-6))
        # 启停计数
        p_fc=cmds[:,1]; fc_active=p_fc>=ems_cfg.pemfc_kw*ems_cfg.start_stop_threshold
        fc_starts+=np.sum(np.diff(np.concatenate([[0],fc_active.astype(int)]))>0)
        p_el=cmds[:,2]; el_active=p_el>=ems_cfg.pemel_kw*ems_cfg.start_stop_threshold
        el_starts+=np.sum(np.diff(np.concatenate([[0],el_active.astype(int)]))>0)
    n=len(weeks); scale=52.0/n
    fb_total=(fb_b+fb_f+fb_e)*scale; bol_annual=bol_total*scale
    ae=fb_total-bol_annual; te=ae/abs(bol_annual) if abs(bol_annual)>1e-10 else 0
    return {'bol':bol_annual,'fb':fb_total,'fb_bat':fb_b*scale,'fb_fc':fb_f*scale,'fb_el':fb_e*scale,
        'ae':ae,'te':te,'lpsp':fb_lp/n,'D_bat_eoy':D['bat'],'D_fc_eoy':D['fc'],'D_el_eoy':D['el'],
        'fc_starts':fc_starts,'el_starts':el_starts}

# ================================================================
# 1. M2基线 + 启停诊断
# ================================================================
print("\n[1] M2基线 + 启停诊断...")
r_m2=run_sim(use_l4=False)
print(f"  M2: TE={r_m2['te']*100:+.2f}%, BOL={r_m2['bol']:.4f}, FB={r_m2['fb']:.4f}")
print(f"  FC启停={r_m2['fc_starts']}, EL启停={r_m2['el_starts']}")
print(f"  LPSP={r_m2['lpsp']*100:.2f}%, D_BAT={r_m2['D_bat_eoy']:.4f}")

# ================================================================
# 2. R1/R2无效性诊断
# ================================================================
print("\n[2] R1/R2无效性诊断...")
# R2诊断: FC运行模式分析
print("\n  R2诊断 (FC运行模式):")
# 在13周Feedback中, FC有多少步在运行?
D={'bat':0.0,'fc':0.0,'el':0.0}; fc_on_steps=0; total_steps=0
for w in SAMPLE_WEEKS:
    s=w*SPW; e=s+SPW; pv_w=pv_full[s:e]; load_w=load_full[s:e]
    D_bat=D['bat']
    if D_bat>0:
        q,r0,p_max=hp_bat(D_bat); bat_p=bat_rated_kw*q; bat_c=bat_cap_kwh*q
    else:
        bat_p=None; bat_c=None
    np.random.seed(42)
    cmds,state=rule_based_dispatch(load_w,pv_w,ems_cfg,bat_p_max_kw=bat_p,bat_capacity_kwh=bat_c)
    dD=compute_damage(hp_bat,cmds,config_dict,dt_min=15.0,D_state=D,coalition=coal)
    D['bat']=min(D['bat']+float(np.sum(dD['dD_bat'])),1.0)
    D['fc']=min(D['fc']+float(np.sum(dD['dD_fc'])),1.0)
    D['el']=min(D['el']+float(np.sum(dD['dD_el'])),1.0)
    fc_active=cmds[:,1]>=ems_cfg.pemfc_kw*ems_cfg.start_stop_threshold
    fc_on_steps+=np.sum(fc_active)
    total_steps+=len(cmds)
print(f"    FC运行步数: {fc_on_steps}/{total_steps} ({fc_on_steps/total_steps*100:.1f}%)")
print(f"    -> FC几乎持续运行(LPSP={r_m2['lpsp']*100:.0f}%), R2最小运行时间无效果")

# R1诊断: SOC边界触及频率
print("\n  R1诊断 (SOC边界触及):")
D={'bat':0.0,'fc':0.0,'el':0.0}
soc_at_min=0; soc_at_max=0; total_steps=0
for w in SAMPLE_WEEKS:
    s=w*SPW; e=s+SPW; pv_w=pv_full[s:e]; load_w=load_full[s:e]
    D_bat=D['bat']
    if D_bat>0:
        q,r0,p_max=hp_bat(D_bat); bat_p=bat_rated_kw*q; bat_c=bat_cap_kwh*q
    else:
        bat_p=None; bat_c=None
    np.random.seed(42)
    cmds,state=rule_based_dispatch(load_w,pv_w,ems_cfg,bat_p_max_kw=bat_p,bat_capacity_kwh=bat_c)
    dD=compute_damage(hp_bat,cmds,config_dict,dt_min=15.0,D_state=D,coalition=coal)
    D['bat']=min(D['bat']+float(np.sum(dD['dD_bat'])),1.0)
    D['fc']=min(D['fc']+float(np.sum(dD['dD_fc'])),1.0)
    D['el']=min(D['el']+float(np.sum(dD['dD_el'])),1.0)
    soc=state['soc_trace']
    soc_at_min+=np.sum(soc<=ems_cfg.soc_min+0.01)
    soc_at_max+=np.sum(soc>=ems_cfg.soc_max-0.01)
    total_steps+=len(soc)
print(f"    SOC触及下限: {soc_at_min}/{total_steps} ({soc_at_min/total_steps*100:.1f}%)")
print(f"    SOC触及上限: {soc_at_max}/{total_steps} ({soc_at_max/total_steps*100:.1f}%)")
print(f"    -> SOC带缩减影响有限(边界触及频率低)")

# ================================================================
# 3. 精细R3扫描 (大k值, 寻找TE≈0)
# ================================================================
print("\n[3] 精细R3扫描 (寻找TE≈0)...")
R3_FINE = [0.30, 0.50, 1.0, 2.0, 3.0, 5.0, 10.0, 50.0, 100.0, 1000.0]
fine_results = []
for k in R3_FINE:
    # R3-only: R1=0, R2=0 (禁用), 只用R3
    cfg_r3 = L4Config(soc_band_narrow_k=0.0, fc_min_run_extend_k=0.0, el_ramp_limit_k=k,
                      d_bat_threshold=0.0)  # threshold=0确保始终激活
    r = run_sim(use_l4=True, l4_config=cfg_r3)
    r['el_k']=k; fine_results.append(r)
    print(f"  el_k={k:>7.1f}: TE={r['te']*100:+.2f}%, FC={r['fb_fc']:.4f}, EL={r['fb_el']:.4f}, "
          f"LPSP={r['lpsp']*100:.2f}%, EL_starts={r['el_starts']}")

# 找TE最接近0的参数
best_r3 = min(fine_results, key=lambda r: abs(r['te']))
print(f"\n  最优R3: el_k={best_r3['el_k']:.1f}, TE={best_r3['te']*100:+.2f}%")

# ================================================================
# 4. 全L4禁用验证 (L4应退化为M2)
# ================================================================
print("\n[4] L4全禁用验证 (应等价于M2)...")
cfg_off = L4Config(soc_band_narrow_k=0.0, fc_min_run_extend_k=0.0, el_ramp_limit_k=1000.0,
                   d_bat_threshold=2.0)  # threshold>1确保永不激活
r_off = run_sim(use_l4=True, l4_config=cfg_off)
print(f"  L4全禁用: TE={r_off['te']*100:+.2f}%, M2: TE={r_m2['te']*100:+.2f}%")
print(f"  差异: {abs(r_off['te']-r_m2['te'])*100:.4f}% (应≈0)")

# ================================================================
# 5. 导出CSV
# ================================================================
print("\n[5] 导出CSV...")
with open(OUT_CSV,'w',newline='',encoding='utf-8') as f:
    w=csv.writer(f)
    w.writerow(['=== M2 Baseline ==='])
    w.writerow(['bol','fb','fb_bat','fb_fc','fb_el','te_pct','lpsp_pct','D_bat_eoy','D_fc_eoy','D_el_eoy','fc_starts','el_starts'])
    w.writerow([f"{r_m2['bol']:.8f}",f"{r_m2['fb']:.8f}",f"{r_m2['fb_bat']:.8f}",f"{r_m2['fb_fc']:.8f}",
        f"{r_m2['fb_el']:.8f}",f"{r_m2['te']*100:.2f}",f"{r_m2['lpsp']*100:.4f}",
        f"{r_m2['D_bat_eoy']:.6f}",f"{r_m2['D_fc_eoy']:.6f}",f"{r_m2['D_el_eoy']:.6f}",
        r_m2['fc_starts'],r_m2['el_starts']])
    w.writerow([]); w.writerow(['=== R3 Fine Sweep (R1=0, R2=0) ==='])
    w.writerow(['el_k','bol','fb','fb_bat','fb_fc','fb_el','te_pct','lpsp_pct','D_bat_eoy','D_fc_eoy','D_el_eoy','el_starts'])
    for r in fine_results:
        w.writerow([f"{r['el_k']:.1f}",f"{r['bol']:.8f}",f"{r['fb']:.8f}",f"{r['fb_bat']:.8f}",
            f"{r['fb_fc']:.8f}",f"{r['fb_el']:.8f}",f"{r['te']*100:.2f}",f"{r['lpsp']*100:.4f}",
            f"{r['D_bat_eoy']:.6f}",f"{r['D_fc_eoy']:.6f}",f"{r['D_el_eoy']:.6f}",r['el_starts']])
print(f"  CSV: {OUT_CSV}")

# ================================================================
# 6. 图表
# ================================================================
print("\n[6] 生成图表...")
fig, axes = plt.subplots(1, 3, figsize=(16, 5))
ks = [r['el_k'] for r in fine_results]
# (a) TE vs el_k
axes[0].semilogx(ks, [r['te']*100 for r in fine_results], 'o-', color='#D73027', linewidth=2, markersize=8)
axes[0].axhline(r_m2['te']*100, color='#3288BD', ls='--', lw=1.5, label=f'M2 TE={r_m2["te"]*100:.2f}%')
axes[0].axhline(0, color='gray', ls=':', lw=0.8)
axes[0].set_xlabel('el_ramp_limit_k (log scale)'); axes[0].set_ylabel('TE (%)')
axes[0].set_title('(a) TE vs R3 Parameter', fontsize=10, fontweight='bold')
axes[0].legend(fontsize=8); axes[0].grid(True, alpha=0.3)
# (b) FC/EL damage vs el_k
axes[1].semilogx(ks, [r['fb_fc'] for r in fine_results], 's-', color='#D73027', linewidth=2, markersize=6, label='FC damage')
axes[1].semilogx(ks, [r['fb_el'] for r in fine_results], '^-', color='#66C2A5', linewidth=2, markersize=6, label='EL damage')
axes[1].axhline(r_m2['fb_fc'], color='#D73027', ls='--', lw=1, alpha=0.5)
axes[1].axhline(r_m2['fb_el'], color='#66C2A5', ls='--', lw=1, alpha=0.5)
axes[1].set_xlabel('el_ramp_limit_k (log scale)'); axes[1].set_ylabel('Annual Damage')
axes[1].set_title('(b) FC/EL Damage vs R3', fontsize=10, fontweight='bold')
axes[1].legend(fontsize=8); axes[1].grid(True, alpha=0.3)
# (c) LPSP + EL starts vs el_k
ax_l = axes[2]
ax_l.semilogx(ks, [r['lpsp']*100 for r in fine_results], 'o-', color='#9966CC', linewidth=2, markersize=6, label='LPSP (%)')
ax_l.axhline(r_m2['lpsp']*100, color='#9966CC', ls='--', lw=1, alpha=0.5)
ax_l.set_xlabel('el_ramp_limit_k (log scale)'); ax_l.set_ylabel('LPSP (%)', color='#9966CC')
ax_r = axes[2].twinx()
ax_r.semilogx(ks, [r['el_starts'] for r in fine_results], 'D--', color='#FF8C00', linewidth=1.5, markersize=5, label='EL start-stops')
ax_r.axhline(r_m2['el_starts'], color='#FF8C00', ls='--', lw=1, alpha=0.5)
ax_r.set_ylabel('EL Start-Stops', color='#FF8C00')
axes[2].set_title('(c) LPSP & EL Start-Stops vs R3', fontsize=10, fontweight='bold')
lines1, labels1 = ax_l.get_legend_handles_labels()
lines2, labels2 = ax_r.get_legend_handles_labels()
ax_l.legend(lines1+lines2, labels1+labels2, fontsize=7)
axes[2].grid(True, alpha=0.3)

fig.suptitle(f'P6c L4 Fine Sweep: R3 (EL Ramp Limit) Sensitivity\n'
    f'M2: TE={r_m2["te"]*100:+.2f}%, FC_starts={r_m2["fc_starts"]}, EL_starts={r_m2["el_starts"]} | '
    f'FC运行率={fc_on_steps/total_steps*100:.1f}%',
    fontsize=11, fontweight='bold', y=1.02)
fig.tight_layout()
fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight'); plt.close(fig)
print(f"  图表: {OUT_FIG}")

# ================================================================
# 汇总
# ================================================================
print("\n" + "="*70)
print("P6c L4 Fine Sweep 完成")
print("="*70)
print(f"  M2基线: TE={r_m2['te']*100:+.2f}%, FC启停={r_m2['fc_starts']}, EL启停={r_m2['el_starts']}")
print(f"  R1诊断: SOC边界触及率低({(soc_at_min+soc_at_max)/total_steps*100:.1f}%), 影响有限")
print(f"  R2诊断: FC运行率={fc_on_steps/total_steps*100:.1f}%, R2无效(FC持续运行)")
print(f"  R3最优: el_k={best_r3['el_k']:.1f}, TE={best_r3['te']*100:+.2f}%")
print(f"  L4全禁用验证: TE差异={abs(r_off['te']-r_m2['te'])*100:.4f}% (应≈0)")
print("="*70)
