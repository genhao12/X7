# -*- coding: utf-8 -*-
"""Round-12 fixes across main/summary/gate-doc after full evidence verification.

Key data sources: uncertainty_propagation.json (EFC LHS), degradation_summary.json
(EFC economics), cost_sensitivity.json (EFC baseline), l4_m2_m3_comparison.csv
(legacy M3 TE), compute_equipment_capex (power-electronics 15% + installation 20%).
"""
from __future__ import annotations

from pathlib import Path

import docx

ROOT = Path(r"d:\matlab\C")


def set_para(doc, needle, new_text):
    for para in doc.paragraphs:
        if needle in para.text:
            for run in para.runs:
                run.text = ""
            para.runs[0].text = new_text
            print(f"[PARA] {needle[:22]!r} replaced")
            return True
    print(f"[WARN] para not found: {needle!r}")
    return False


def set_cell(doc, table_head, row_needle, col, new_text):
    for tb in doc.tables:
        if not tb.rows or not tb.rows[0].cells[0].text.startswith(table_head):
            continue
        for row in tb.rows:
            if row_needle in row.cells[0].text:
                row.cells[col].text = new_text
                print(f"[CELL] {table_head!r}/{row_needle!r} C{col}")
                return True
        return False
    return False


def add_para_after(doc, needle, text):
    for i, para in enumerate(doc.paragraphs):
        if needle in para.text:
            new_p = docx.oxml.OxmlElement("w:p")
            para._element.addnext(new_p)
            np_ = docx.text.paragraph.Paragraph(new_p, para._parent)
            np_.add_run(text)
            print(f"[PARA+] inserted after {needle[:20]!r}")
            return True
    print(f"[WARN] anchor not found: {needle!r}")
    return False


def main() -> int:
    # ============ 主报告 ============
    mp = ROOT / "09_docs" / "工作区全面总结报告.docx"
    d = docx.Document(str(mp))

    # P103 LCOE CI width & min config
    set_para(d, "LCOE CI宽度约0.5-0.7 CNY/kWh（~12%相对宽度）",
             "LCOE 128 点 LHS 经验 90%CI 宽度 1.188~1.411 CNY/kWh（相对宽度 19.8%~23.0%）；"
             "最紧凑配置 LCOE 最低（median 5.639）且 CI 最窄（1.188），氢主导最高（median 7.137）")

    # P76 EFC linear extrapolation limitation
    set_para(d, "寿命/NPC 均基于 EFC 重算（degradation_summary.json economics）。",
             "寿命/NPC 均基于 EFC 重算（degradation_summary.json economics）。"
             "标定限制：a_d 由 280Ah 实测 700 满循环 7.1% 容量损失（Q 1.0→0.929）线性外推至 SoH_EOL=0.8 的 20% 损失，"
             "隐含循环容量损失与 EFC 线性成正比、且外推区间（>700 循环）标定不确定性未量化的假设；"
             "未计入深度循环加速（Wöhler k=1 为线性保守上界）。")

    # P88 power electronics / installation wording
    set_para(d, "缺失项：电力电子（逆变器/整流器/DC-DC）单独成本未计入",
             "成本构成口径（compute_equipment_capex）：设备单价为设备本体 CAPEX（PV 含组件+支架、"
             "BAT 含电芯+BMS+Pack、FC/EL 含电堆+BOP、H₂ 含储罐），电力电子（逆变器/整流器/DC-DC）"
             "按设备 CAPEX 的 15% 另计（power_electronics_fraction=0.15），安装按设备 CAPEX 的 20% 另计"
             "（installation_fraction=0.20），不与单行单价重复；")

    # Table13 PV row: includes exclude inverter/installation
    t13 = d.tables[13]
    for row in t13.rows:
        if row.cells[0].text.strip() == "PV":
            row.cells[5].text = "组件+支架（电力电子与安装按设备 CAPEX 15%/20% 另计，见注）"
            print("[CELL] Table13 PV includes fixed")
            break
    # Table13 note
    add_para_after(d, "H₂罐 | 725",
                   "注：本表单价为设备本体（components）CAPEX；电力电子（含逆变器/整流器/DC-DC）与安装"
                   "统一按设备 subtotal 的 15%/20% 另计（npc_lite.compute_equipment_capex），不重复计入单行。")

    # Sobol parameter list + calendar note (insert after P92 参数范围)
    sobol_txt = (
        "外层 Sobol 参数清单（6 项，run_double_layer_propagation.py PARAM_DEFS）："
        "BAT_EFC_A_D [2.5e-4, 1.0e-3] D/EFC、BAT_EFC_K [0.7, 1.5]、"
        "FC_K_RUN_H [1.2e-4, 3.6e-4] /h、FC_K_START [1.0e-4, 4.0e-4] /event、"
        "EL_K_RUN_H [1.0e-4, 3.0e-4] /h、EL_K_START [8.0e-5, 3.2e-4] /event。"
        "BAT 日历退化 BAT_K_CAL_H 固定为 5%/年（文献 2-5%/年上界，未纳入 LHS，因其为确定性成分）；"
        "故 BAT 认知方差仅反映 EFC 循环参数，日历项不贡献认知方差。"
    )
    add_para_after(d, "参数范围：", sobol_txt)

    # Learning-curve checkpoint statement (insert after double-layer conclusion)
    lc_txt = (
        "蒙特卡洛收敛说明：预注册规格（rectification_v2_stage2c.json double_layer_uncertainty）要求"
        "连续两个学习曲线检查点（inner_checkpoints=[1000, 2000, 4000]）稳定后才判定收敛；"
        "当前 256×4000 单次执行满足 minimum_inner_samples=4000，但尚未展示连续两检查点的逐点收敛判据。"
        "全方差闭合残差≤1.6e-16 仅证明方差分解恒等式成立，不等同于蒙特卡洛收敛；"
        "收敛性判定需在论文中补充 checkpoints 逐点结果（计划待补）。"
    )
    add_para_after(d, "认知占比 BAT 99.9%/FC 64-78%/EL 86-95%", lc_txt)

    d.save(str(mp))
    print("main saved")

    # ============ 汇总报告 ============
    sp = ROOT / "09_docs" / "工作区运行结果汇总.docx"
    d2 = docx.Document(str(sp))

    # Table9 6.1 LHS compact values
    for tb in d2.tables:
        if len(tb.rows) > 1 and "run_degradation_uncertainty" in tb.rows[1].cells[1].text:
            for row in tb.rows:
                if row.cells[0].text.strip() == "主要结果":
                    row.cells[1].text = (
                        "compact 配置：BAT 寿命 7.70±2.04 年（90%CI [4.9, 11.7]）、FC 9.59±2.71 年、"
                        "EL 6.22±1.55 年；LCOE 5.647±0.370 CNY/kWh（90%CI [5.139, 6.327]）"
                    )
                    print("[CELL] summary Table9 LHS updated")
            break

    # Table11 cost baseline
    for tb in d2.tables:
        if len(tb.rows) > 1 and "run_cost_sensitivity" in tb.rows[1].cells[1].text:
            for row in tb.rows:
                if row.cells[0].text.strip() == "主要结果":
                    row.cells[1].text = (
                        "基线 LCOE 5.55~6.90 CNY/kWh（EFC 重算：compact 5.55 / hydrogen_dominant 6.90）；"
                        "PEMEL 单价是 4/5 配置第一敏感项（per +30% → LCOE +8.5~12.4%）；"
                        "BOP 剥离降 LCOE 19.9~21.2%；全设备同步缩放 NPC 严格线性"
                    )
                    print("[CELL] summary Table11 baseline updated")
            break

    # P29 L4 isolation statement unify (legacy M3 TE)
    set_para(d2, "旧 MVP 单配置（C1）M3 L4 EMS 为「正向」结果（M3 TE=+6.42% vs M2 +6.59%",
             "隔离声明（L4 结果，legacy，不可引用）：旧 MVP 单配置（C1）M3 L4 EMS 为「正向」结果"
             "（l4_m2_m3_comparison.csv：M3 TE=-37.79%、FC 损伤 -43.65%、EL -60.27%，L4 大幅降低耦合误差；"
             "l4_crossyear 口径 M3 TE=+6.5%~6.6% 仍低于 M2，两种口径均为 legacy 版本差异，配置域=单配置 battery_first）；"
             "而 Stage2c 五个代表配置上的 L4 消融为「否定性」结果")

    d2.save(str(sp))
    print("summary saved")

    # ============ 门控文档 ============
    gp = ROOT / "09_docs" / "门控通过路径与BAT-EFC升级方案.docx"
    d3 = docx.Document(str(gp))
    # Table1 legacy current value True -> False
    t1 = d3.tables[1]
    for row in t1.rows:
        if "legacy_results_paper_citable" in row.cells[0].text:
            row.cells[1].text = "False"
            row.cells[2].text = "永久 False，不翻转"
            print("[CELL] gate-doc Table1 legacy -> False")
            break
    # unit unified wording
    for para in d3.paragraphs:
        if "不得断言 α₁/β₁ 与跨设备路径的量级主导（单位未统一）" in para.text:
            for run in para.runs:
                run.text = ""
            para.runs[0].text = "• 六路径单位已统一（effect_pct），量级比较以统一口径为准；legacy 结果不作论文结论。"
            print("[PARA] gate-doc unit unified wording fixed")
            break
    d3.save(str(gp))
    print("gate-doc saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
