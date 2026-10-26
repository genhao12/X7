"""Round14 文档修正（不依赖重算结果的部分）：
1. γ"均非零"→微小点估计（主 P135、T24R4；汇总 T5R3、T12R4）
2. VS4 符号约束（汇总 T5R4）
3. 62 缺失点插补统一为 ffill/bfill（主报告"线性填充"）
4. 口径矛盾：主 P72/P73 改回 BOL dispatch 开环年损伤；P80 参数传播措辞
"""
import docx
from pathlib import Path

ROOT = Path(r"d:\matlab\C")

def set_cell(c, new):
    c.text = new

# ───────── 主报告 ─────────
p = ROOT / "09_docs" / "工作区全面总结报告.docx"
d = docx.Document(str(p))

# 1) P72/P73 口径统一：BOL dispatch 开环年损伤（10.1）vs 退化调度（10.2 EOL 事件）
for para in d.paragraphs:
    if "退化调度（含 BAT 循环损伤反馈）下计算三年开环年损伤增量" in para.text:
        new = ("使用 closed_loop_lite.py 的退化模型（BAT日历+循环、FC运行+启停、EL运行+启停），"
               "对5个代表配置在 BOL 调度（健康状态命令）下计算三年开环年损伤增量"
               "（10.1 开环口径：BOL dispatch → 损伤模型，无退化反馈迭代；"
               "10.2 寿命估计改用退化调度：EOL 事件模型逐年逐步累积损伤并处理更换）。")
        for r in para.runs: r.text = ""
        if para.runs: para.runs[0].text = new
        else: para.add_run(new)
        print("P72/P73 口径统一为 BOL dispatch")
        break

# 2) P80 参数传播措辞（不确定性传播已完成，非"完成前"）
for para in d.paragraphs:
    if "在参数不确定性传播完成前仅为情景参考" in para.text:
        for r in para.runs: r.text = ""
        new = ("这些数值基于 EFC 循环损伤模型（280Ah 校准）与 FC/EL 启停参数的不确定性区间（±50-100%），"
               "不确定性传播（单层 LHS 128 点 + 嵌套双层 256×4000）已完成；"
               "但寿命与经济仍为情景结果（基于合成时序、线性 EFC 外推、未完整成本边界），非工程经济评估依据。")
        if para.runs: para.runs[0].text = new
        else: para.add_run(new)
        print("P80 参数传播措辞修正")
        break

# 3) 主 P135 γ 均非零 → 微小点估计
for para in d.paragraphs:
    if "结论：六条退化耦合路径均非零" in para.text:
        for r in para.runs: r.text = ""
        new = ("结论：六条退化耦合路径在 cross_device_paths.json 中均有点估计，"
               "但跨设备路径（α₂/β₂/γ₁/γ₂，尤其 γ₁/γ₂）仅为微小点估计（|effect_pct|<0.1%）且无不确定区间，"
               "不能据此断言\u201c均非零\u201d或\u201c可忽略\u201d；α₁/β₁ 量级大于跨设备路径"
               "（α₁ 最大 +24.33% @ hydrogen_dominant D=0.7）。")
        if para.runs: para.runs[0].text = new
        else: para.add_run(new)
        print("P135 γ 均非零修正")
        break

# 4) 主 T24R4C2 效应均非零
for tb in d.tables:
    for row in tb.rows:
        for ci, c in enumerate(row.cells):
            if "效应均非零；单位已统一" in c.text:
                set_cell(c, "六路径均有点估计；跨设备（尤其 γ₁/γ₂）仅微小点估计（|effect_pct|<0.1%）无区间；单位已统一（effect_pct，D∈{0.3,0.5,0.7}），α₁/β₁ 量级大于跨设备路径（α₁ 最大 +24.33%）；不能断言可忽略")
                print("T24R4 效应均非零修正")
                break

# 5) 62 缺失点插补统一：线性填充 → ffill/bfill
for para in d.paragraphs:
    if "62 个负荷缺失点插补敏感性说明" in para.text:
        for r in para.runs: r.text = ""
        new = ("62 个负荷缺失点插补敏感性说明：02_data_processed 中 64_TradeStreet CSV 含 62 个负荷缺失点（<0.2%），"
               "采用前向-后向填充（ffill/bfill，annual_input_contract.py），并非线性插值；"
               "当前实现未给出插补敏感性分析（即未对比插补前后的容量准入/EOL 寿命/LCOE 差异），"
               "属已知数据限制——论文定稿前须补充对比基准情景（不插补 vs 插补）的容量准入门控与寿命估计差异，"
               "或在论文中明确声明该插补对结论的影响未量化。")
        if para.runs: para.runs[0].text = new
        else: para.add_run(new)
        print("62 缺失点插补方法统一为 ffill/bfill")
        break

d.save(str(p))
print("=== 主报告保存 ===")

# ───────── 汇总报告 ─────────
p = ROOT / "09_docs" / "工作区运行结果汇总.docx"
d = docx.Document(str(p))

# 6) 汇总 T5R3C2/C3 跨设备均非零
for tb in d.tables:
    if len(tb.rows) > 0 and tb.rows[0].cells[0].text.strip() == "验证项":
        for row in tb.rows:
            if row.cells[0].text.strip().startswith("VS3"):
                for ci, c in enumerate(row.cells):
                    if "跨设备路径均非零" in c.text:
                        set_cell(c, c.text.replace("跨设备路径均非零、|effect_pct|≤~3%",
                            "跨设备路径点估计量级小（|effect_pct|≤~3%，γ₁/γ₂<0.1%）但缺阈值与区间"))
                # 结果列 C3
                for ci, c in enumerate(row.cells):
                    if "统一单位下 α₁/β₁ 量级大于跨设备路径" in c.text and "均非零" in c.text:
                        set_cell(c, c.text.replace("跨设备路径均非零（|effect_pct|≤~3%，α₂ −1.71%~+2.97%）",
                            "跨设备路径点估计量级小（|effect_pct|≤~3%，γ₁/γ₂<0.1%）、缺阈值与区间"))
                print("T5R3 跨设备均非零修正")
        break

# 7) 汇总 T5R4C2 VS4 符号约束
for tb in d.tables:
    if len(tb.rows) > 0 and tb.rows[0].cells[0].text.strip() == "验证项":
        for row in tb.rows:
            if row.cells[0].text.strip().startswith("VS4"):
                for ci, c in enumerate(row.cells):
                    if "C/P 闭合残差 0；C/E/P 符号 ≥0；交互比 ≤30%" in c.text:
                        set_cell(c, "C/P 闭合残差 0；符号检查仅对参与联盟归因（C/P，E 未参与跳过）；phi_P<0 记为物理可解释；交互比 ≤30%")
                        print("T5R4 VS4 符号约束修正")
                break
        break

# 8) 汇总 T12R4C1（L4 相关或六路径表）检查并修正均非零
for tb in d.tables:
    for ri, row in enumerate(tb.rows):
        for ci, c in enumerate(row.cells):
            if "六路径单位已统一" in c.text and "均非零" in c.text:
                set_cell(c, c.text.replace("均非零", "点估计量级小（γ₁/γ₂<0.1%）、缺区间"))
                print(f"T{ri}R{ci} 均非零修正")
            elif "均非零" in c.text and ("α₂" in c.text or "跨设备" in c.text):
                set_cell(c, c.text.replace("均非零", "点估计量级小、缺区间"))
                print(f"T{ri}R{ci} 跨设备均非零修正")

d.save(str(p))
print("=== 汇总报告保存 ===")
