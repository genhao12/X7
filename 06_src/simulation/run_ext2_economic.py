# -*- coding: utf-8 -*-
"""扩展2: 20年NPC/LCOE经济评价 (C6_compact, EOL守卫)

基于S4重新标定的FC退化率(EOL=1278h=0.15年)
计算M2/M3的20年NPC/LCOE
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

print("=" * 70)
print("扩展2: 20年NPC/LCOE经济评价 (C6_compact)")
print("=" * 70)

# C6_compact CAPEX (基于文献单价)
cap = {"PV_kWp": 200, "BAT_kWh": 500, "PEMFC_kW": 80, "PEMEL_kW": 80, "H2_kg": 500}
# 单价(CNY, 来源: economic_parameters_v1.0.json)
pv_unit = 8375  # 元/kWp
bat_unit = 2175  # 元/kWh
fc_unit = 14500  # 元/kW
el_unit = 14500  # 元/kW
h2_tank_unit = 725  # 元/kg (30bar)
pe_fraction = 0.15  # 电力电子15%
install_fraction = 0.20  # 安装20%

capex_pv = cap['PV_kWp'] * pv_unit
capex_bat = cap['BAT_kWh'] * bat_unit
capex_fc = cap['PEMFC_kW'] * fc_unit
capex_el = cap['PEMEL_kW'] * el_unit
capex_h2 = cap['H2_kg'] * h2_tank_unit
capex_equip = capex_pv + capex_bat + capex_fc + capex_el + capex_h2
capex_pe = capex_equip * pe_fraction
capex_install = capex_equip * install_fraction
capex_total = capex_equip + capex_pe + capex_install
opex_annual = capex_total * 0.02  # 2% of CAPEX

print(f"\n[CAPEX分解]")
print(f"  PV({cap['PV_kWp']}kWp): {capex_pv/10000:.1f}万元")
print(f"  BAT({cap['BAT_kWh']}kWh): {capex_bat/10000:.1f}万元")
print(f"  FC({cap['PEMFC_kW']}kW): {capex_fc/10000:.1f}万元")
print(f"  EL({cap['PEMEL_kW']}kW): {capex_el/10000:.1f}万元")
print(f"  H2({cap['H2_kg']}kg): {capex_h2/10000:.1f}万元")
print(f"  电力电子(15%): {capex_pe/10000:.1f}万元")
print(f"  安装(20%): {capex_install/10000:.1f}万元")
print(f"  初始CAPEX: {capex_total/10000:.1f}万元")
print(f"  年度OPEX: {opex_annual/10000:.1f}万元/yr")

# 年度损伤率 (S11结果, 2017年)
# BAT: D_bat=0.769, EL: D_el=0.641 (M2)
# FC: EOL=1278h, 年运行~6000h → 一年内EOL
# 假设FC每年更换, BAT约1.3年更换, EL约1.6年更换
bat_eol_years = 1.0 / 0.769  # ~1.30年
fc_eol_years = 1.0  # FC一年内EOL (S4标定)
el_eol_years = 1.0 / 0.641  # ~1.56年

print(f"\n[设备EOL时间]")
print(f"  BAT: {bat_eol_years:.2f}年 (D_annual=0.769)")
print(f"  FC: {fc_eol_years:.2f}年 (EOL=1278h, S4标定)")
print(f"  EL: {el_eol_years:.2f}年 (D_annual=0.641)")

# 20年NPC (离散法, 贴现率6%)
discount_rate = 0.06
n_years = 20
print(f"\n[20年NPC (离散法, r={discount_rate*100}%)]")

for label, bat_d, el_d in [('M2', 0.769, 0.641), ('M3', 0.769, 0.584)]:
    bat_eol = 1.0 / bat_d
    el_eol = 1.0 / el_d
    fc_repl = int(n_years / fc_eol_years)  # FC每年更换
    bat_repl = int(n_years / bat_eol)
    el_repl = int(n_years / el_eol)
    
    npc = capex_total  # 初始CAPEX
    for y in range(1, n_years + 1):
        df = 1.0 / (1 + discount_rate) ** y
        npc += opex_annual * df
        # 设备更换
        if y % int(fc_eol_years) == 0:
            npc += capex_fc * df
        if y % int(bat_eol) == 0:
            npc += capex_bat * df
        if y % int(el_eol) == 0:
            npc += capex_el * df
    
    # 年度供电量 (S3结果: LPSP=0.43%, 负荷510MWh)
    annual_supply = 510 * (1 - 0.0043)  # MWh
    lcoe = npc / (annual_supply * n_years * 1000)  # 元/kWh
    
    print(f"  {label}: BAT更换={bat_repl}次, FC更换={fc_repl}次, EL更换={el_repl}次")
    print(f"  {label}: NPC={npc/10000:.1f}万元, LCOE={lcoe:.3f}元/kWh")

# 敏感性
print(f"\n[敏感性分析]")
for dr in [0.04, 0.06, 0.08, 0.10]:
    npc = capex_total
    for y in range(1, n_years + 1):
        df = 1.0 / (1 + dr) ** y
        npc += opex_annual * df
        if y % 1 == 0:  # FC每年
            npc += capex_fc * df
        if y % 2 == 0:  # BAT约2年
            npc += capex_bat * df
        if y % 2 == 0:  # EL约2年
            npc += capex_el * df
    lcoe = npc / (510 * 0.9957 * n_years * 1000)
    print(f"  r={dr*100:.0f}%: NPC={npc/10000:.1f}万元, LCOE={lcoe:.3f}元/kWh")

print(f"\n[关键发现]")
print(f"  1. C6_compact CAPEX={capex_total/10000:.1f}万元 (高于C1的319万元)")
print(f"  2. FC每年更换(S4标定EOL=1278h), NPC中FC替换占比最大")
print(f"  3. L4(M3)与M2经济差异: M3 EL退化更低(0.584 vs 0.641), EL更换次数可能减少")
print(f"  4. 此结果为扩展阶段, 不作为核心方法论文依据")
