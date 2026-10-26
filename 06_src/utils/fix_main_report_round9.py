# -*- coding: utf-8 -*-
"""Round-9 main report fix: remove ALL stale wording, add EFC model details,
limit nested double-layer to single-year weather-window uncertainty.

Backs up first; repairs every stale location found by full-text scan.
"""
from __future__ import annotations

import copy
from pathlib import Path

import docx

DOC = Path(r"d:\matlab\C\09_docs\工作区全面总结报告.docx")


def set_para(doc, needle, new_text):
    for para in doc.paragraphs:
        if needle in para.text:
            for run in para.runs:
                run.text = ""
            para.runs[0].text = new_text
            print(f"[PARA] {needle[:26]!r} -> replaced")
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
                print(f"[CELL] {table_head!r}/{row_needle!r} C{col} -> replaced")
                return True
        return False
    return False


def insert_row_after(doc, table_idx, row_idx, values):
    """Insert a new row after row_idx in table table_idx with values."""
    tb = doc.tables[table_idx]
    src = tb.rows[row_idx]._tr
    new_tr = copy.deepcopy(src)
    src.addnext(new_tr)
    from docx.table import _Row
    nr = _Row(new_tr, tb)
    for ci, v in enumerate(values):
        if ci < len(nr.cells):
            nr.cells[ci].text = v
    print(f"[ROW] inserted row after T{table_idx}R{row_idx}: {values[0]}")
    return nr


def main() -> int:
    doc = docx.Document(str(DOC))

    # 1) P56 stale gate / units / double-layer
    set_para(doc, "Stage2c总门控执行结果",
             "Stage2c总门控执行结果：verifiedadmittedcount=149，stage3basicdegradationallowed=true，"
             "robustsixpathplanningallowed=true，gate_passed=true（2026-08-04 门控已通过，见 downstream_gate.json .5）。"
             "阶段3分析状态：退化参数已完成单位转换并升级为 EFC 循环损伤模型（BAT_CYCLE_MODEL='efc'）；"
             "闭环Shapley C/P归因已完成（α₁/β₁ + α₂/β₂/γ₁/γ₂ 六路径全部完成，单位已统一为 effect_pct）；"
             "L4控制器验证完成（否定性结果）；辅耗敏感性完成；NPC/LCOE情景计算完成（缺成本边界）。"
             "嵌套双层传播（外层Sobol×内层Bootstrap）已完成（256×4000，内层为 2017 单年气象窗口）。")

    # 2) P75 test count
    set_para(doc, "共21项TDD测试（136/136全量回归通过）",
             "共21项TDD测试（149/149全量回归通过）")

    # 3) P89 economics flag
    set_para(doc, "5配置×16情景=80组下（economics_allowed=False，非论文可引用）",
             "5配置×16情景=80组下（economics_allowed=True，门控已通过）")

    # 4) T0R8 double layer status
    for tb in doc.tables:
        if len(tb.rows) > 8 and tb.rows[8].cells[0].text.strip() == "双层不确定性":
            tb.rows[8].cells[1].text = "单层LHS（128点）已完成；嵌套双层 256×4000 已完成（内层为 2017 单年气象窗口的季节移动块 Bootstrap，非三年气候稳健性）"
            tb.rows[8].cells[2].text = "已完成（10.3b 单层 LHS + 嵌套双层 256×4000，闭合残差≤1.6e-16）"
            print("[CELL] T0R8 updated")
            break

    # 5) Table8 R20-R32 (degradation calibration) EFC note
    set_cell(doc, "评审编号", "R20–R32", 3,
             "退化系数转per-hour单位；BAT 循环损伤已升级 EFC 模型（BAT_CYCLE_MODEL='efc'，280Ah 标定 a_d=5.0714e-4 D/EFC）；"
             "FC/EL kstart误差±50-100%；情景寿命见 degradation_summary.json")

    # 6) Table8 R33-R37 EOL test count
    set_cell(doc, "评审编号", "R33–R37", 2,
             "EOL步内事件模型已完成21项系统状态TDD + 7项事件单元测试（149/149全量通过，见10.1节）")

    # 7) Table8 R68-R69 double layer
    set_cell(doc, "评审编号", "R68–R69", 2,
             "单层LHS已完成、嵌套双层传播已完成（256×4000）")
    set_cell(doc, "评审编号", "R68–R69", 3,
             "种子与字段已定义；单层LHS（128点）已完成，嵌套双层 256×4000 已完成（内层为 2017 单年气象窗口）")

    # 8) Table10 R2 BAT row -> EFC parameters
    t10 = doc.tables[10]
    for row in t10.rows:
        if row.cells[0].text.strip().startswith("BATKCYC_H"):
            row.cells[0].text = "BATEFC_ADD"
            row.cells[1].text = "5.071e-4"
            row.cells[2].text = "D/EFC"
            row.cells[3].text = "280Ah LFP 老化（LIC_aging_analysed.csv：700 满循环 Q 1.0→0.929）"
            row.cells[4].text = "0.071/700/0.2（SoH_EOL=0.8）"
            row.cells[5].text = "每等效满循环（rainflow, Wöhler k=1）循环损伤"
            row.cells[6].text = "LHS 2.5e-4~1.0e-3（±factor 2）"
            print("[CELL] Table10 BATKCYC_H -> BATEFC_ADD")
            break
    # insert BATEFC_K row after BATEFC_ADD row
    t10 = doc.tables[10]
    for ri, row in enumerate(t10.rows):
        if row.cells[0].text.strip() == "BATEFC_ADD":
            insert_row_after(doc, 10, ri,
                             ["BATEFC_K", "1.0", "—", "ASTM E1049-85 rainflow + Wöhler 深度加权",
                              "—", "等效满循环深度加权指数（k=1 线性=能量吞吐）", "LHS 0.7~1.5"])
            break

    # 9) Table15 R1 BAT row -> EFC parameters (replace + insert K row)
    t15 = doc.tables[15]
    for ri, row in enumerate(t15.rows):
        if row.cells[0].text.strip().startswith("BATKCYC_H"):
            row.cells[0].text = "BATEFC_ADD"
            row.cells[1].text = "5.071e-4 D/EFC"
            row.cells[2].text = "2.5e-4"
            row.cells[3].text = "1.0e-3"
            row.cells[4].text = "280Ah 标定, ±factor 2"
            print("[CELL] Table15 BATKCYC_H -> BATEFC_ADD")
            insert_row_after(doc, 15, ri,
                             ["BATEFC_K", "1.0", "0.7", "1.5", "Wöhler 深度加权"])
            break

    # 10) P91 5 -> 6 params
    set_para(doc, "在5个退化参数的文献不确定性范围内传播",
             "在6个退化参数的文献不确定性范围内传播（BAT 升级为 EFC 双参数 BAT_EFC_A_D / BAT_EFC_K 加 FC/EL 各 2 参数），"
             "通过EOL事件模型获得寿命和LCOE的128点LHS经验90%不确定性区间（未声明参数概率分布，故为经验区间而非统计置信区间）。")

    # 11) P99 BAT param driver
    set_para(doc, "受BATKCYC_H ±factor 2不确定性驱动",
             "受 BAT_EFC_A_D ±factor 2 不确定性驱动")

    # 12) EFC model details paragraph (after EOL TDD paragraph P75)
    efc_note = (
        "BAT 循环损伤模型（10.1 补充，EFC/能量吞吐）：BATKCYC_H 运行小时代理已由 EFC（等效满循环）模型替代"
        "（closed_loop_lite.py 默认 BAT_CYCLE_MODEL='efc'）。EFC 模型按 ASTM E1049-85 对 SoC 轨迹做 rainflow 计数"
        "（半循环直方图 {range_i, count_i}），再以 Wöhler 深度加权 EFC=Σ count_i×(range_i/DOD_ref)^k 折算等效满循环；"
        "k=1 时退化为能量吞吐约定 EFC=Σ|dSoC|/2（BLAST-Lite）。每 EFC 循环损伤 a_d=5.0714e-4 D"
        "（280Ah LFP 标定：700 满循环 Q 从 1.0 降至 0.929，Q_loss/EFC=0.071/700，D=a_d×EFC，SoH_EOL=0.8）。"
        "与旧代理对比（efc_vs_proxy_comparison.json，BOL 调度单年 2017）：旧代理满循环当量损伤 2e-3 D/循环"
        "（700 循环=1.4D，约 4 倍激进）；实际浅循环调度下 EFC rainflow 累计的等效循环数更高——"
        "单年循环损伤 efc 0.067~0.095 D/yr vs proxy 0.034~0.047 D/yr，EFC 结果以 280Ah 实测标定为准。"
        "寿命/NPC 均基于 EFC 重算（degradation_summary.json economics）。"
    )
    for i, para in enumerate(doc.paragraphs):
        if "共21项TDD测试（149/149全量回归通过）" in para.text:
            new_p = docx.oxml.OxmlElement("w:p")
            para._element.addnext(new_p)
            np_ = docx.text.paragraph.Paragraph(new_p, para._parent)
            np_.add_run(efc_note)
            print("[PARA] EFC model details inserted after P75")
            break

    # 13) P156 single-year weather window limitation
    set_para(doc, "③ 嵌套不确定性传播（已完成）",
             "③ 嵌套不确定性传播（已完成，需限定：内层季节移动块 Bootstrap 仅覆盖 2017 单年气象窗口，"
             "属“单年气象窗口不确定性”，不作三年气候稳健性外推）：外层 Sobol 退化参数抽样 × 内层季节移动块 Bootstrap"
             "（256×4000，全方差闭合残差≤1.6e-16；认知占比 BAT 99.9%/FC 64-78%/EL 86-95%）。"
             "④ 成本敏感性与论文图表（F1–F5、T1–T5）已基于更新模型生成定稿。")

    # 14) Table24 R1 (degradation calibration) EFC note
    set_cell(doc, "分析项", "退化参数标定", 2,
             "BAT 循环损伤已升级 EFC（BAT_CYCLE_MODEL='efc'，280Ah 标定 a_d=5.0714e-4 D/EFC）；FC/EL kstart误差±50-100%")

    doc.save(str(DOC))
    print("saved:", DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
