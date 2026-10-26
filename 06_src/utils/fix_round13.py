"""Round13 综合修复 11 项问题（主报告 + 汇总 + 门控文档 + 台账）。"""
import docx
from pathlib import Path
import json

ROOT = Path(r"d:\matlab\C")
DOCS = {
    "main":   ROOT / "09_docs" / "工作区全面总结报告.docx",
    "summary": ROOT / "09_docs" / "工作区运行结果汇总.docx",
    "gate":   ROOT / "09_docs" / "门控通过路径与BAT-EFC升级方案.docx",
    "ledger": ROOT / "09_docs" / "统一结果台账.docx",
}

def set_para(p, new):
    for r in p.runs: r.text = ""
    if p.runs: p.runs[0].text = new
    else: p.add_run(new)

def replace_substring_para(p, old, new):
    """Replace substring in a paragraph, joining run fragments first."""
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
p = DOCS["main"]
d = docx.Document(str(p))

# 1) P160 蒙特卡洛收敛声明（已部分声明，补"连续两检查点尚未展示"明确表述）
for para in d.paragraphs:
    if "蒙特卡洛收敛说明" in para.text and "预注册规格" in para.text:
        new = ("蒙特卡洛收敛说明：预注册规格（rectification_v2_stage2c.json double_layer_uncertainty）"
               "要求连续两个学习曲线检查点（inner_checkpoints=[1000, 2000, 4000]）稳定后才判定收敛；"
               "当前 256×4000 单次执行满足 minimum_inner_samples=4000 且全方差闭合残差≤1.6e-16，"
               "但**连续两个学习曲线检查点的逐点收敛判据尚未在论文中展示**——"
               "闭合残差仅证明方差分解恒等式成立，不等同于蒙特卡洛收敛。"
               "论文定稿前须补充 1000→2000→4000 三个检查点的方差估计序列与相邻两检查点的相对偏差<5%判据。")
        set_para(para, new)
        print("P160 收敛声明已加强")
        break

# 2) P72 BOL 调度 → 统一为"退化调度"口径（与 P77 一致）
for para in d.paragraphs:
    if "BOL调度下计算三年开环年损伤增量" in para.text:
        replace_substring_para(para, "BOL调度下计算三年开环年损伤增量",
            "退化调度（含 BAT 循环损伤反馈）下计算三年开环年损伤增量（与 10.2 节寿命估计同口径）")
        print("P72 BOL调度→退化调度")
        break

# 3) P128 γ 路径四舍五入为 0 → 改为"JSON 中存在微小点估计"
for para in d.paragraphs:
    if "γ₁/γ₂（FC/EL→BAT）百分比贡献" in para.text:
        new = ("γ₁/γ₂（FC/EL→BAT）在 cross_device_paths.json 中存在微小点估计"
               "（effect_pct 数值范围 -0.068% ~ +0.008%，绝对值 <0.1%）；"
               "由于缺少效应阈值与不确定区间，且部分数值在表格中四舍五入显示为 0.00，"
               "仅可表述为「JSON 中存在微小点估计」，不能断言「均非零」或「可忽略」。"
               "跨设备路径 |effect_pct| 最大仍 ≤~3%（α₂ −1.71%~+2.97%），同样缺少阈值与不确定区间。"
               "建议论文表述：「跨设备耦合效应在点估计上量级较小（<3%），但缺少效应阈值与不确定区间，"
               "不构成可忽略的统计判定」。")
        set_para(para, new)
        print("P128 γ 路径表述修正")
        break

# 4) LCOE<8 → 经验 90% 区间上界 <8（搜索含相关表述）
for para in d.paragraphs:
    if "LCOE<8" in para.text or "LCOE < 8" in para.text or "所有配置" in para.text and "LCOE" in para.text and "<8" in para.text:
        if replace_substring_para(para, "LCOE<8", "经验 90% 区间上界 LCOE<8"):
            print("LCOE<8 → 经验 90% 区间上界 (sub)")
        elif replace_substring_para(para, "所有配置在参数范围内 LCOE<8", "所有配置经验 90% 区间上界 LCOE<8"):
            print("LCOE<8 → 经验 90% 区间上界 (sub)")

# 5) P88/P89 BOP 15% 储氢罐分离
for para in d.paragraphs:
    if "成本构成口径（compute_equipment_capex）" in para.text:
        new = ("成本构成口径（compute_equipment_capex）：设备单价为设备本体 CAPEX"
               "（PV 含组件+支架、BAT 含电芯+BMS+Pack、FC/EL 含电堆+BOP、H₂ 含储罐），"
               "电力电子（逆变器/整流器/DC-DC）按设备 subtotal 的 15% 另计（power_electronics_fraction=0.15），"
               "安装按设备 subtotal 的 20% 另计（installation_fraction=0.20），"
               "**注意：当前实现中 15%/20% 是按 PV+BAT+PEMFC+PEMEL+H₂_tank 全设备 subtotal 计算，"
               "即 15% 电力电子附加项也施加于 H₂ 储罐（实际储罐不需要逆变器/整流器，存在轻度高估）**；"
               "FC/EL 的 BOP 已含在自身单价中（电堆+BOP），不重复计入 15%；"
               "运维按各自 CAPEX 比例估算（非实际 O&M 合同）；"
               "无税费、无保险、无土地成本；残值设为 0（BAT 梯次利用、H₂ 罐 30 年残值均未计）。")
        set_para(para, new)
        print("P89 BOP 15% 储氢罐分离已声明")
        break

# 6) 主报告 BAT 认知方差日历表述（如有）
for para in d.paragraphs:
    if "BAT 认知" in para.text and "日历" in para.text:
        replace_substring_para(para, "日历项主导",
            "BAT_K_CAL_H 固定 5%/年（不进入 LHS），BAT 认知方差仅反映 EFC 循环参数 BAT_EFC_A_D/K 的不确定性")
        print("主报告 BAT 认知方差日历表述修正")

# 7) 寿命/经济持续情景标注：在 P77 标题后追加情景声明
for para in d.paragraphs:
    if para.text.startswith("情景寿命估计（EFC 循环损伤模型 + EOL 事件模型，退化调度"):
        if "合成时序" not in para.text:
            new = para.text.rstrip("：") + "（基于合成时序、线性 EFC 外推、未完整成本边界的情景结果，非工程经济评估依据）："
            set_para(para, new)
            print("P77 寿命情景标注已加")
            break

d.save(str(p))
print("=== 主报告保存 ===")

# ───────── 汇总报告 ─────────
p = DOCS["summary"]
d = docx.Document(str(p))

# 8) Table10 R4 BAT 认知方差日历项主导 → 修正
for tb in d.tables:
    if len(tb.rows) > 0 and tb.rows[0].cells[0].text.strip() == "维度":
        for row in tb.rows:
            if "BAT 认知" in row.cells[1].text or "BAT 认知" in row.cells[0].text:
                for ci, c in enumerate(row.cells):
                    if "日历项主导" in c.text:
                        c.text = c.text.replace("日历项主导",
                            "BAT_K_CAL_H 固定 5%/年不进入 LHS，BAT 认知方差仅反映 EFC 循环参数 BAT_EFC_A_D/K 不确定性")
                        print(f"汇总 Table10 BAT 认知方差修正")
        break

# 9) Table6 旧 C1 L4 EMS legacy 标注 + Table7 M4 经济 legacy 标注
for tb in d.tables:
    if len(tb.rows) > 1 and "ems_l4_lite" in tb.rows[1].cells[1].text:
        # 标题行
        for row in tb.rows:
            if row.cells[0].text.strip() == "是否通过":
                if "legacy" not in row.cells[1].text:
                    row.cells[1].text = row.cells[1].text + "（legacy_results_paper_citable=False，仅作历史审计附录参考；Stage2c L4 否定性结果见 6.4/Table12）"
                    print("汇总 Table6 加 legacy 标注")
        break

for tb in d.tables:
    if len(tb.rows) > 1 and "npc_lite" in tb.rows[1].cells[1].text:
        for row in tb.rows:
            if row.cells[0].text.strip() == "是否通过":
                if "legacy" not in row.cells[1].text:
                    row.cells[1].text = row.cells[1].text + "（legacy MVP 单配置情景值；Stage2c EFC 经济见 6.3/Table11）"
                    print("汇总 Table7 M4 经济加 legacy 标注")
            if row.cells[0].text.strip() == "主要结果":
                if "legacy" not in row.cells[1].text:
                    row.cells[1].text = "【历史审计附录】" + row.cells[1].text + "（legacy MVP 单配置 bat=300kWh；Stage2c 5 配置 EFC 经济：NPC 31.5~39.3M、LCOE 5.55~6.90）"
                    print("汇总 Table7 主要结果加 legacy 标注")
        break

# 10) P26 4.1 M3 L4 EMS 标题加 legacy 标注
for para in d.paragraphs:
    if "4.1　M3 耦合感知 EMS（L4" in para.text:
        if "（legacy" not in para.text:
            new = para.text.rstrip("）") + "，legacy_results_paper_citable=False，仅作历史审计附录参考）"
            set_para(para, new)
            print("P26 L4 EMS 标题加 legacy")
            break

# 11) 4.1/4.2 节标题前加"历史审计附录"
for para in d.paragraphs:
    if para.text == "4　功能模块（M1–M4）":
        set_para(para, "4　功能模块（M1–M4，历史审计附录，legacy_results_paper_citable=False）")
        print("汇总 第4节标题加历史审计附录")
        break

# 12) P3 流水线说明加 legacy 标注
for para in d.paragraphs:
    if "本报告系统梳理工作区全部运行结果" in para.text:
        if "legacy" not in para.text:
            new = para.text.rstrip("。") + "。其中 P1（单年度仿真）、M3 L4 EMS、M4 经济为 legacy MVP 单配置结果（legacy_results_paper_citable=False），仅作历史审计附录参考；论文可引用结果以 Stage2b/2c/Stage3 为准。"
            set_para(para, new)
            print("P3 流水线说明加 legacy 标注")
            break

d.save(str(p))
print("=== 汇总报告保存 ===")

# ───────── 门控文档 ─────────
p = DOCS["gate"]
d = docx.Document(str(p))

# 13) 末页治理版本号 .4/.3/.3 → .5/.4/.4
for para in d.paragraphs:
    if "downstream_gate.json(.4)" in para.text and "run_manifest.json(.3)" in para.text:
        new = para.text.replace("downstream_gate.json(.4)", "downstream_gate.json(.5)").replace("run_manifest.json(.3)", "run_manifest.json(.4)").replace("hash_record.json(.3)", "hash_record.json(.4)")
        set_para(para, new)
        print("门控文档末页版本号统一为 .5/.4/.4")
        break

# 14) "禁止引用已获准的 LHS/成本敏感性/L4 结果"修正
for para in d.paragraphs:
    if "不得在门控通过前启动" in para.text or "结果重算已随门控通过启动并完成" in para.text:
        new = "• 结果重算已随门控通过启动并完成（①-⑤）：LHS（uncertainty_propagation.json）、成本敏感性（cost_sensitivity.json，80 组）、L4 否定性结果（l4_ablation.json）均已获准论文引用（shapley_allowed=economics_allowed=l4_allowed=True）；仅 legacy_results_paper_citable=False 永久保留（旧 P1/C1 L4/M4 单配置结果不可引用）。"
        set_para(para, new)
        print("门控文档禁引用→已获准")
        break

# 15) 单位未统一残留清除
for para in d.paragraphs:
    if "单位未统一" in para.text or "不得比较" in para.text:
        replace_substring_para(para, "单位未统一", "单位已统一")
        replace_substring_para(para, "不得比较", "可比较（已统一 effect_pct）")
        print("门控文档单位未统一→已统一")

# 16) Table1 legacy 行再次确认 False
for tb in d.tables:
    for row in tb.rows:
        if "legacy_results_paper_citable" in row.cells[0].text:
            if row.cells[1].text != "False":
                row.cells[1].text = "False"
                row.cells[2].text = "永久 False（旧 C1 等结果已标记 historical invalid，不翻转）"
                print("门控 Table1 legacy=False 再次确认")
            break

d.save(str(p))
print("=== 门控文档保存 ===")

# ───────── 台账 ─────────
p = DOCS["ledger"]
d = docx.Document(str(p))

# 17) S9 双层方差备注：补"BAT 认知方差仅反映 EFC 循环参数"
for row in d.tables[1].rows:
    if row.cells[0].text.strip().startswith("S9"):
        old = row.cells[7].text
        if "BAT_K_CAL_H 固定" not in old:
            row.cells[7].text = old.rstrip("）") + "；BAT_K_CAL_H 固定 5%/年不进入 LHS，BAT 认知方差仅反映 EFC 循环参数 BAT_EFC_A_D/K 不确定性）"
            print("台账 S9 BAT 认知方差备注补充")
        break

# 18) Table3 追加 round13 修正记录
t3 = d.tables[3]
new_row = t3.add_row()
new_row.cells[0].text = "round13 修正：①蒙特卡洛收敛声明加强（连续两检查点尚未展示）②汇总 BAT 认知方差日历项主导→EFC 循环参数 ③门控方案禁引用→已获准 ④末页版本号 .4/.3/.3→.5/.4/.4 ⑤开环/BOL 调度→退化调度口径统一 ⑥六路径 γ 表述为 JSON 微小点估计 ⑦LCOE<8→经验90%区间上界 ⑧62 缺失点插补敏感性待补 ⑨BOP 15% 储氢罐分离声明 ⑩旧 P1/C1 L4/M4 移至历史审计附录 ⑪寿命经济持续情景标注"
new_row.cells[1].text = "理由：消除三份文档阻断性不一致，达到论文定稿条件"
new_row.cells[2].text = "蒙特卡洛收敛连续两检查点判据待补；62 缺失点插补敏感性待补；摘要/最终结论/双层不确定性结论/工程经济结论须在补齐后定稿"
print("台账 Table3+round13 修正记录")

d.save(str(p))
print("=== 台账保存 ===")
print()
print("Round13 综合修复完成")
