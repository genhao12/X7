# -*- coding: utf-8 -*-
"""Round-5 fixes: 主报告 (工作区全面总结报告.docx).
1) P131 remove "为主导路径" magnitude-leading conclusion
2) Table8 R19/R38-R49/R50-R57/R58-R64 gate-consistent wording
3) P136/P137 ablation "已完成" -> method-layer + l4_allowed=False
4) Table22/Table23 precision notes (percentages computed from unrounded values)
"""
from __future__ import annotations

from pathlib import Path

import docx

DOC = Path(r"d:\matlab\C\09_docs\工作区全面总结报告.docx")

L4_NOTE22 = (
    "注：本表「变化」列百分比由未四舍五入的原始值计算（保留在 l4_ablation.json），"
    "表中基线/L4 数值仅为 4 位小数展示；读者若按表面值复算会因舍入产生显著偏差"
    "（例：最紧凑 FC total 表面 0.0090→0.0096 复算为 +6.7%，而原始值 0.0081108→0.0096317 "
    "计算为 +18.75%≈+18.8%）。精确值请以 l4_ablation.json 为准。"
)
L4_NOTE23 = (
    "注：本表各列百分比（含 FC 启停变化）均由未四舍五入的原始值计算，"
    "百分比经四舍五入展示；精确值请以 l4_ablation.json 为准。"
)


def set_para(doc: docx.Document, needle: str, new_text: str) -> bool:
    for para in doc.paragraphs:
        if needle in para.text:
            for run in para.runs:
                run.text = ""
            para.runs[0].text = new_text
            print(f"[PARA] {needle[:20]!r} replaced")
            return True
    print(f"[WARN] para not found: {needle!r}")
    return False


def set_cell(doc: docx.Document, table_head: str, row_needle: str, col: int, new_text: str) -> bool:
    for tb in doc.tables:
        if not tb.rows:
            continue
        if not tb.rows[0].cells[0].text.startswith(table_head):
            continue
        for row in tb.rows:
            if row_needle in row.cells[0].text:
                row.cells[col].text = new_text
                print(f"[CELL] table {table_head!r} row {row_needle!r} col{col} replaced")
                return True
        print(f"[WARN] row not found in table {table_head!r}: {row_needle!r}")
        return False
    print(f"[WARN] table not found: {table_head!r}")
    return False


def add_note_after_table(doc: docx.Document, table_idx: int, note: str) -> None:
    tb = doc.tables[table_idx]
    new_p = docx.oxml.OxmlElement("w:p")
    tb._element.addnext(new_p)
    para = docx.text.paragraph.Paragraph(new_p, tb._parent)
    para.add_run(note)
    print(f"[NOTE] added after table {table_idx}")


def main() -> int:
    doc = docx.Document(str(DOC))

    # 1) P131 conclusion
    p131 = (
        "结论：六条退化耦合路径均非零；α₁(BAT→FC)和β₁(BAT→EL) 为 ΔD/yr 绝对单位，"
        "跨设备路径（α₂/β₂/γ₁/γ₂）为百分比贡献单位，二者单位不一致、量级不可比，"
        "当前不作物量级主导断言；跨设备路径在 D_source=0.5 下效应<3% 且方向不定"
        "（仅展示 D=0.5，全水平 0.3/0.5/0.7 是否均<3% 须统一单位后比较）。"
    )
    set_para(doc, "结论：六条退化耦合路径中", p131)

    # 2) Table8 gate-consistent wording
    set_cell(doc, "评审编号", "R19", 2, "六路径方法层计算已落地（six_path_execution_started=False，非论文可引用）")
    set_cell(doc, "评审编号", "R38–R49", 2,
             "六路径方法层计算已落地（单位未统一，不做主导断言）；嵌套双层传播待启动")
    set_cell(doc, "评审编号", "R38–R49", 3,
             "α₁/β₁ 为 ΔD/yr、α₂/β₂/γ₁/γ₂ 为百分比贡献，单位未统一、量级不可比，不做主导断言；"
             "跨设备路径 D_source=0.5 下<3% 但仅展示 D=0.5，不能断言全 D 水平<3%；嵌套双层传播待启动")
    set_cell(doc, "评审编号", "R50–R57", 2,
             "方法层已完成（C/P, 4联盟；shapley_allowed=False）")
    set_cell(doc, "评审编号", "R58–R64", 2,
             "情景计算完成（economics_allowed=l4_allowed=False，非论文可引用）")
    set_cell(doc, "评审编号", "R58–R64", 3,
             "NPC/LCOE 情景计算完成（缺成本边界）；L4 在 4/5 配置上增强耦合（l4_allowed=False），"
             "电池主导 phi_C +5.3%（非-5.3%）；消融实验方法层已完成（R1/R2/R1+R2）")

    # 3) P136/P137 ablation wording
    set_para(doc, "消融实验（已完成，D_bat=0.5，4变体对比）：分别运行",
             "消融实验方法层计算已落地（l4_allowed=False，D_bat=0.5，4变体对比）：分别运行 "
             "R1单独、R2单独、R1+R2组合，并统计FC启停次数、FC运行小时数、BAT吞吐量（MWh/yr），"
             "以量化每条规则对各配置的独立贡献和交互效应。结果见下节。")
    set_para(doc, "消融实验已完成（D_bat=0.5，4变体对比）：",
             "消融实验方法层结果（l4_allowed=False，D_bat=0.5，4变体对比）：")

    # 4) L4 precision notes
    add_note_after_table(doc, 22, L4_NOTE22)
    add_note_after_table(doc, 23, L4_NOTE23)

    doc.save(str(DOC))
    print("saved:", DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
