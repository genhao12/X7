# -*- coding: utf-8 -*-
"""
C2: L4原52周实验的raw口径复核 (run_l4_final.py 的双记账重跑)

问题: 原M3/L4结论(FC降损8.07%、TE -6.59%/-41.64%等)基于 dD_* (cap截断)
累计。若FC/EL在年内触及EOL(D=1), cap截断会掩盖损伤差异(伪影)。
本脚本在完全相同的配置/数据/EMS下, 每周同时累计两种口径:
    cap:  sum(dD_*)        (原口径)
    raw:  sum(dD_*_raw)    (不截断)
并跟踪 D_max 判断是否发生饱和。

判定规则:
    - 若 D_max < 1 全程成立 -> cap从未激活 -> 原结论不是cap伪影
    - 若发生饱和 -> 对比两种口径的M2/M3差异方向是否反转
"""
import sys, json, csv
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from ems.ems_l4_lite import dispatch_l4, L4Config
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

cfg_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(cfg_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)
cap = c1['capacity']; ep = c1['ems_parameters']; bc = c1['battery_constraints']
hc = c1['hydrogen_constraints']; msl = c1['minimum_stable_load']; topo = c1['topology']
ems_cfg = EMSConfig(pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'], pemfc_kw=cap['PEMFC_kW'],
    pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'], soc_min=bc['soc_min'], soc_max=bc['soc_max'],
    soc_initial=bc['soc_initial'], h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=hc['m_initial_fraction'], h2_leakage_per_day=hc['leakage_rate_per_day'],
    start_stop_threshold=ep['start_stop_threshold_fraction'],
    minimum_run_time_min=float(ep['minimum_run_time']),
    critical_load_fraction=ep['critical_load_fraction'],
    p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
bat_rated_kw = cap['BAT_kWh'] * 0.5; bat_cap_kwh = cap['BAT_kWh']
config_dict = {'capacity': cap, 'battery_constraints': bc, 'ems_parameters': ep,
    'hydrogen_constraints': hc, 'minimum_stable_load': msl, 'topology': topo}
hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
                    {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2 * 48,
                     'bat_rated_kw': bat_rated_kw})
coal = CoalitionState.from_code('1111')

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df = raw[raw['local_civil_year'] == 2017].copy()
df = df.rename(columns={'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
                        'p_building_load_reconstructed_kW': 'P_load_kW'})
df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
pv_full = df['P_pv_kW'].values; load_full = df['P_load_kW'].values
SPW = 672; n_weeks = min(52, len(pv_full) // SPW)

print("=" * 70)
print("C2: L4 52周实验 cap/raw 双口径复核 (mvp_c1, 2017)")
print("=" * 70)


def run_full(use_l4=True, l4_config=None):
    """与run_l4_final.run_full完全一致, 仅增加raw口径累计与D_max跟踪。"""
    # BOL基线 (cap与raw在D_state=0时数值相同, 仍双记账以自洽)
    D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    bol = {k: 0.0 for k in ('bat', 'fc', 'el')}
    bol_raw = {k: 0.0 for k in ('bat', 'fc', 'el')}
    for w in range(n_weeks):
        s, e = w * SPW, (s_end := (w + 1) * SPW)
        np.random.seed(42)
        if use_l4:
            cmds, _ = dispatch_l4(load_full[s:e], pv_full[s:e], ems_cfg,
                                  D_bat=0.0, l4_config=l4_config)
        else:
            cmds, _ = rule_based_dispatch(load_full[s:e], pv_full[s:e], ems_cfg)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                            D_state=D_bol, coalition=coal)
        for k in ('bat', 'fc', 'el'):
            bol[k] += float(np.sum(dD[f'dD_{k}']))
            bol_raw[k] += float(dD[f'dD_{k}_raw'])

    # Feedback链 (与原脚本一致: cap累计, D封顶不复位)
    D = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    fb = {k: 0.0 for k in ('bat', 'fc', 'el')}
    fb_raw = {k: 0.0 for k in ('bat', 'fc', 'el')}
    D_max = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    fc_starts = 0
    for w in range(n_weeks):
        s, e = w * SPW, (w + 1) * SPW
        D_bat = D['bat']
        if D_bat > 0:
            q, r0, p_max = hp_bat(D_bat); bat_p = bat_rated_kw * q; bat_c = bat_cap_kwh * q
        else:
            bat_p = None; bat_c = None
        np.random.seed(42)
        if use_l4:
            cmds, _ = dispatch_l4(load_full[s:e], pv_full[s:e], ems_cfg, D_bat=D_bat,
                                  bat_p_max_kw=bat_p, bat_capacity_kwh=bat_c,
                                  l4_config=l4_config)
        else:
            cmds, _ = rule_based_dispatch(load_full[s:e], pv_full[s:e], ems_cfg,
                                          bat_p_max_kw=bat_p, bat_capacity_kwh=bat_c)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                            D_state=D, coalition=coal)
        for k in ('bat', 'fc', 'el'):
            d_cap = float(np.sum(dD[f'dD_{k}']))
            D[k] = min(D[k] + d_cap, 1.0)
            D_max[k] = max(D_max[k], D[k])
            fb[k] += d_cap
            fb_raw[k] += float(dD[f'dD_{k}_raw'])
        fc_a = cmds[:, 1] >= ems_cfg.pemfc_kw * ems_cfg.start_stop_threshold
        fc_starts += np.sum(np.diff(np.concatenate([[0], fc_a.astype(int)])) > 0)

    def te(acc_bol, acc_fb):
        b = sum(acc_bol.values()); f_ = sum(acc_fb.values())
        return (f_ - b) / abs(b) if abs(b) > 1e-10 else 0.0

    return {
        'cap': {'fb': fb, 'te': te(bol, fb), 'fc': fb['fc'], 'el': fb['el'],
                'bat': fb['bat'], 'total': sum(fb.values())},
        'raw': {'fb': fb_raw, 'te': te(bol_raw, fb_raw), 'fc': fb_raw['fc'],
                'el': fb_raw['el'], 'bat': fb_raw['bat'], 'total': sum(fb_raw.values())},
        'D_max': D_max, 'D_eoy': dict(D), 'fc_starts': int(fc_starts),
    }


modes = [
    ('M2', False, None),
    ('M3_L4_default', True, L4Config()),
    ('M3_L4_optimal', True, L4Config(soc_band_narrow_k=0.0, fc_min_run_extend_k=1.0,
                                     el_ramp_limit_k=10.0, d_bat_threshold=0.0)),
]

results = {}
rows = []
for name, use_l4, l4cfg in modes:
    print(f"\n-> {name} ...", flush=True)
    r = run_full(use_l4, l4cfg)
    results[name] = r
    for conv in ('cap', 'raw'):
        c = r[conv]
        rows.append({
            'mode': name, 'accounting': conv,
            'FC_damage': round(c['fc'], 6), 'EL_damage': round(c['el'], 6),
            'BAT_damage': round(c['bat'], 6), 'total': round(c['total'], 6),
            'TE_pct': round(c['te'] * 100, 2),
            'fc_starts': r['fc_starts'],
            'D_fc_max': round(r['D_max']['fc'], 4), 'D_el_max': round(r['D_max']['el'], 4),
            'D_bat_max': round(r['D_max']['bat'], 4),
        })
    print(f"   cap: FC={r['cap']['fc']:.6f} EL={r['cap']['el']:.6f} "
          f"TE={r['cap']['te']*100:+.2f}%  | raw: FC={r['raw']['fc']:.6f} "
          f"EL={r['raw']['el']:.6f} TE={r['raw']['te']*100:+.2f}%")
    print(f"   D_max: {r['D_max']}  FC_starts={r['fc_starts']}")

# ---- 判定 ----
sat = any(max(r['D_max'].values()) >= 0.999 for r in results.values())
m2 = results['M2']
out_csv = PROJECT_ROOT / "07_results" / "gap_closure" / "c2_l4_rawcheck.csv"
out_csv.parent.mkdir(parents=True, exist_ok=True)
with open(out_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

print("\n" + "=" * 70)
print("C3 判定")
print("=" * 70)
print(f"  [饱和检查] 52周内 D_max(FC/EL/BAT) = "
      f"{ {k: max(r['D_max'][k] for r in results.values()) for k in ('bat','fc','el')} }")
if not sat:
    print("  -> D全程<1, cap截断从未激活: cap与raw口径数值完全一致")
    print("  -> 原M3/L4结论(FC降损/TE下降)不是cap伪影, 在本配置下成立")
    print("  -> gap2简化链中观察到的反转发生在富余配置(FC一年内触EOL), 属regime差异")
else:
    for name in results:
        d_fc = (results[name]['cap']['fc'] - m2['cap']['fc'])
        d_fc_raw = (results[name]['raw']['fc'] - m2['raw']['fc'])
        print(f"  {name}: dFC cap={d_fc:+.6f} raw={d_fc_raw:+.6f} "
              f"{'方向一致' if d_fc * d_fc_raw > 0 else '方向反转!'}")
print(f"\n[输出] {out_csv}")
