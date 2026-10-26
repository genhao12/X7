"""Round15 综合修复 10 项硬错误。"""
import docx
from pathlib import Path

ROOT = Path(r"d:\matlab\C")

def set_para(p, new):
    for r in p.runs: r.text = ""
    if p.runs: p.runs[0].text = new
    else: p.add_run(new)

def set_cell(c, new):
    c.text = new

def replace_para_substr(p, old, new):
    if old in p.text:
        full = "".join(r.text for r in p.runs)
        if old in full:
            full = full.replace(old, new)
            for r in p.runs: r.text = ""
            if p.runs: p.runs[0].text = full
            else: p.add_run(full)
            return True
    return False

# ───────── 主报告 ─────────
p = ROOT / "09_docs" / "工作区全面总结报告.docx"
d = docx.Document(str(p))

# 1) T22 L4 基线 3 处修正（0.0090→0.0081、0.0083→0.0068、0.0070→0.0101）
t22 = d.tables[22]
fixes = {
    "最紧凑": ("0.0090", "0.0081"),
    "平衡型": ("0.0083", "0.0068"),
    "氢主导": ("0.0070", "0.0101"),
}
for row in t22.rows[1:]:
    role = row.cells[0].text.strip()
    if role in fixes:
        old, new = fixes[role]
        if row.cells[1].text.strip() == old:
            set_cell(row.cells[1], new)
            print(f"T22 {role} 基线 FC total: {old} -> {new}")
d.save(str(p))
print("T22 L4 基线修正")

# 重新打开（保存后）
d = docx.Document(str(p))

# 2) T14 CAPEX 5 处更新
t14 = d.tables[14]
capex_map = {
    "最紧凑": "19584", "平衡型": "20752", "氢主导": "25977",
    "电池主导": "21099", "临界型": "22333",
}
for row in t14.rows[1:]:
    role = row.cells[0].text.strip()
    if role in capex_map:
        old = row.cells[1].text.strip()
        new = capex_map[role]
        if old != new:
            set_cell(row.cells[1], new)
            print(f"T14 {role} CAPEX: {old} -> {new}")
d.save(str(p))
print("T14 CAPEX 修正")

# 重新打开
d = docx.Document(str(p))

# 3) T13 BAT 行"含安装"→"不含安装"
t13 = d.tables[13]
for row in t13.rows:
    if row.cells[0].text.strip() == "BAT":
        if "含安装" in row.cells[5].text:
            set_cell(row.cells[5], "电芯+BMS+Pack（设备本体，不含安装；安装按设备 subtotal 20% 另计）")
            print("T13 BAT 含安装 -> 不含安装")
        break
d.save(str(p))
print("T13 BAT 修正")

# 重新打开
d = docx.Document(str(p))

# 4) "量级大于"加"点估计上"限定
for para in d.paragraphs:
    t = para.text
    if ("α₁/β₁ 量级大于" in t or "量级大于跨设备路径" in t) and "点估计上" not in t:
        replace_para_substr(para, "α₁/β₁ 量级大于跨设备路径", "α₁/β₁ 在点估计上量级大于跨设备路径")
        print(f"P[{para.text[:30]}...] 量级大于加点估计上限定")
    elif "α₁/β₁ 量级大" in t and "点估计" not in t:
        replace_para_substr(para, "α₁/β₁ 量级大", "α₁/β₁ 在点估计上量级大")
        print("量级大加点估计上限定")

# 5) LPSP 换算定义（P18 追加）
for para in d.paragraphs:
    if "一级最优LPSP归一化违背度=13.271070" in para.text:
        new = para.text.rstrip("。") + ("。换算定义（stage2b_capacity_gate.normalized_violation）："
            "LPSP 归一化违背度 = max(0, 年最大 LPSP/1% − 1, 三年聚合 LPSP/1% − 1)；"
            "最优候选 tier1__sobol0689 的三年聚合 LPSP=8.0%（T9 口径），"
            "2016 年最大年 LPSP=14.27% 触发违背度 = 14.27%/1% − 1 = 13.271070，"
            "故归一化违背度由 2016 年（季节储能缺口年）主导。")
        set_para(para, new)
        print("P18 LPSP 换算定义补充")
        break

d.save(str(p))
print("=== 主报告保存 ===")

# ───────── 汇总报告 ─────────
p = ROOT / "09_docs" / "工作区运行结果汇总.docx"
d = docx.Document(str(p))

# 6) P8 量级大于加点估计上限定
for para in d.paragraphs:
    if "α₁/β₁ 量级大于跨设备路径" in para.text and "点估计上" not in para.text:
        replace_para_substr(para, "α₁/β₁ 量级大于跨设备路径", "α₁/β₁ 在点估计上量级大于跨设备路径")
        print("汇总 P8 量级大于加点估计上限定")
        break

# 7) P45/P46 关键发现 legacy 标注（旧 P1 结果）
for i, para in enumerate(d.paragraphs):
    if i in (45, 46) and para.text.strip():
        if "legacy" not in para.text:
            new = para.text.rstrip("。") + "（legacy P1 结果，legacy_results_paper_citable=False，仅作历史审计附录参考，不作为论文结论）。"
            set_para(para, new)
            print(f"汇总 P{i} 关键发现加 legacy 标注")

# 8) P47 量级大于确认已有"点估计"（P47 已含"点估计量级小"，但 α₁/β₁ 句需"点估计上"）
for para in d.paragraphs:
    if "统一单位下 α₁/β₁（BAT 源路径）量级大于跨设备路径" in para.text and "点估计上" not in para.text:
        replace_para_substr(para, "统一单位下 α₁/β₁（BAT 源路径）量级大于跨设备路径",
                            "统一单位下 α₁/β₁（BAT 源路径）在点估计上量级大于跨设备路径")
        print("汇总 P47 α₁/β₁ 加点估计上")
        break

# 9) P52 量级大于加限定
for para in d.paragraphs:
    if "统一口径下「α₁/β₁ 量级大于跨设备路径」成立" in para.text and "点估计上" not in para.text:
        replace_para_substr(para, "统一口径下「α₁/β₁ 量级大于跨设备路径」成立",
                            "统一口径下「α₁/β₁ 在点估计上量级大于跨设备路径」成立")
        print("汇总 P52 量级大于加限定")
        break

d.save(str(p))
print("=== 汇总报告保存 ===")

# ───────── 台账 ─────────
p = ROOT / "09_docs" / "统一结果台账.docx"
d = docx.Document(str(p))
# 10) 台账 S8/S10 量级大于加限定
for row in d.tables[1].rows:
    for ci, c in enumerate(row.cells):
        if "量级大于" in c.text and "点估计上" not in c.text:
            c.text = c.text.replace("α₁/β₁ 量级大于跨设备路径", "α₁/β₁ 在点估计上量级大于跨设备路径")
            print(f"台账 S 行 C{ci} 量级大于加限定")
# 11) Table3 追加 round15 修正记录
t3 = d.tables[3]
row = t3.add_row()
row.cells[0].text = "round15 修正：①T22 L4 基线 FC total 3 处（0.0090/0.0083/0.0070 → 0.0081/0.0068/0.0101，权威 l4_ablation.json）②T14 CAPEX 5 处（最紧凑 20068→19584 等，round14 成本口径修复后重算）③T13 BAT 行\"含安装\"→\"不含安装\"（与 round14 统一）④\"量级大于\"全部限定为\"点估计上\"⑤LPSP 换算定义补充（三年聚合 8.0% vs 2016 年最大 14.27%→违背度 13.271070）⑥汇总关键发现 P1 legacy 标注⑦三年块统一称\"首尾容差闭合\""
row.cells[1].text = "理由：消除前后表述不一致与数据硬错误，结果与 JSON 一致"
row.cells[2].text = "l4_ablation.json baseline fc_total 0.008111/0.006832/0.010073；degradation_summary total_capex；stage2b tier1__sobol0689 metrics"
d.save(str(p))
print("=== 台账保存 ===")
print("Round15 综合修复完成")
