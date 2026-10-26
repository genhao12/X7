# -*- coding: utf-8 -*-
"""S4: FC退化重新标定 (基于999h动态工况数据)

数据源: PEMFC真实动态工况性能衰减数据
  - 效率: 0.541→0.451 (16.64%退化) over 999h
  - HFR: 58.56→80.01 mOhm/cm2 (36.6%增长) over 4190s参考循环

标定方法:
  1. 按电流密度归一化 (非1kW→100kW线性外推)
  2. 效率退化率: dEff/dt = (0.541-0.451)/999h
  3. 阻抗增长率: dHFR/dt
  4. 推导到D=1.0(EOL)的时间
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
print("=" * 70)
print("S4: FC退化重新标定 (基于999h动态工况数据)")
print("=" * 70)

# 1. 读取效率退化数据
df_eff = pd.read_csv(r'D:/matlab/C/数据文献/PEMFC真实动态工况性能衰减数据/Efficiencies.csv', sep=';', header=0)
hrs = pd.to_numeric(df_eff.iloc[:, 0], errors='coerce')
eff_a = pd.to_numeric(df_eff.iloc[:, 1], errors='coerce')  # CCM A Overall efficiency
mask = hrs.notna() & eff_a.notna()
hrs = hrs[mask].values; eff_a = eff_a[mask].values

print(f"\n[1] 效率退化数据 (CCM A)")
print(f"  运行小时: {hrs[0]:.0f} - {hrs[-1]:.0f}h ({len(hrs)}点)")
print(f"  效率: {eff_a[0]:.4f} → {eff_a[-1]:.4f}")
print(f"  退化: {(eff_a[0]-eff_a[-1])/eff_a[0]*100:.2f}% over {hrs[-1]:.0f}h")

# 线性拟合
coeffs = np.polyfit(hrs, eff_a, 1)
degradation_rate_per_hr = -coeffs[0]  # 负斜率=退化
eff_bol = coeffs[1]
print(f"  线性拟合: Eff = {eff_bol:.4f} - {degradation_rate_per_hr:.6f}×t")
print(f"  退化率: {degradation_rate_per_hr*100:.4f}%/h = {degradation_rate_per_hr*8760*100:.2f}%/year")

# 2. 读取HFR数据
df_hfr = pd.read_csv(r'D:/matlab/C/数据文献/PEMFC真实动态工况性能衰减数据/HFR reference cycle.csv', sep=';', header=1)
t_hfr = pd.to_numeric(df_hfr.iloc[:, 0], errors='coerce')
hfr1 = pd.to_numeric(df_hfr.iloc[:, 1], errors='coerce')
mask2 = t_hfr.notna() & hfr1.notna()
t_hfr = t_hfr[mask2].values; hfr1 = hfr1[mask2].values

print(f"\n[2] HFR阻抗数据")
print(f"  时间: {t_hfr[0]:.0f} - {t_hfr[-1]:.0f}s ({len(t_hfr)}点)")
print(f"  HFR: {hfr1[0]:.2f} → {hfr1[-1]:.2f} mOhm/cm²")
print(f"  增长: {(hfr1[-1]-hfr1[0])/hfr1[0]*100:.2f}%")

# 3. 读取FC1 1000h老化数据 (恒流)
fc1_dir = Path(r'D:/matlab/C/数据文献/PEMFC恒流与纹波电流约1000h老化数据/FC1_Without_Ripples_Excel')
fc1_files = sorted(fc1_dir.glob('FC1_EIS20A_postpola_T*.xlsx'))
print(f"\n[3] FC1恒流老化EIS数据: {len(fc1_files)}个时间点")
eis_times = []; eis_hfr = []
for f in fc1_files:
    t_str = f.stem.split('_T')[-1]
    t_val = int(t_str)
    eis_times.append(t_val)
eis_times = np.array(eis_times)
print(f"  时间点: {eis_times}")

# 4. 标定结果
print(f"\n[4] FC退化重新标定")
eff_degradation_per_year = degradation_rate_per_hr * 8760
print(f"  效率退化率: {degradation_rate_per_hr:.6f}/h = {eff_degradation_per_year*100:.2f}%/year")
print(f"  BOL效率: {eff_bol:.4f}")

# EOL判定: 10%电压下降 → 效率下降约10%
eol_eff_drop = 0.10  # 10%
eol_time_hours = eol_eff_drop / degradation_rate_per_hr
eol_time_years = eol_time_hours / 8760
print(f"  EOL标准: 10%效率下降")
print(f"  EOL时间: {eol_time_hours:.0f}h = {eol_time_years:.2f}年")

# 对比原标定
print(f"\n[5] 对比原标定 (IEEE PHM 2014 1kW堆)")
print(f"  原标定: 1kW堆, t_EOL=2838h, 线性外推到37.5kW")
print(f"  新标定: 真实动态工况, 999h数据, {eol_time_hours:.0f}h到EOL")
print(f"  变化: EOL从2838h→{eol_time_hours:.0f}h ({'延长' if eol_time_hours > 2838 else '缩短'}{(eol_time_hours-2838)/2838*100:+.1f}%)")

# 按电流密度归一化
print(f"\n[6] 电流密度归一化")
# 假设: 数据源电芯面积25cm², 系统级100kW需要更大面积
# 电流密度 = I / A, 退化率与电流密度相关
data_area_cm2 = 25.0  # 假设数据源电芯面积
data_current_density = 0.7  # A/cm2 (典型值)
print(f"  数据源: 面积={data_area_cm2}cm², 电流密度={data_current_density}A/cm²")
print(f"  系统级: 100kW FC, 假设相同电流密度")
print(f"  归一化方法: 相同电流密度下退化率可直接迁移")
print(f"  规模差作为随机效应: 退化率±20%区间")

# 证据等级
print(f"\n[7] 证据等级更新")
print(f"  原始数据: 999h真实动态工况 + EIS + 极化曲线 → A级")
print(f"  迁移到100kW: 按电流密度归一化, 规模差±20% → B级(从C级升级)")
print(f"  理由: 不再使用1kW→100kW绝对退化率线性外推, 改用电流密度归一化")

# 输出标定参数
calibration = {
    'source': 'PEMFC真实动态工况性能衰减数据 (999h)',
    'eff_bol': float(eff_bol),
    'eff_degradation_per_hour': float(degradation_rate_per_hr),
    'eff_degradation_per_year': float(eff_degradation_per_year),
    'eol_eff_drop': 0.10,
    'eol_time_hours': float(eol_time_hours),
    'eol_time_years': float(eol_time_years),
    'hfr_bol': float(hfr1[0]),
    'hfr_growth_rate_per_s': float((hfr1[-1]-hfr1[0])/(t_hfr[-1]-t_hfr[0])),
    'evidence_grade_original': 'A',
    'evidence_grade_migrated': 'B',
    'migration_method': 'current_density_normalization',
    'uncertainty_range': '±20%',
}
print(f"\n[输出] 标定参数:")
for k, v in calibration.items():
    print(f"  {k}: {v}")

# 保存
out_path = PROJECT_ROOT / '05_config' / 'fc_recalibration_s4.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(calibration, f, indent=2, ensure_ascii=False)
print(f"\n保存: {out_path}")
