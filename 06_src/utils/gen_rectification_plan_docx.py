# -*- coding: utf-8 -*-
"""生成模型整改与再验证计划 Word 文档"""
from pathlib import Path
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
for section in doc.sections:
    section.top_margin = Cm(2.5); section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.5); section.right_margin = Cm(2.5)
style = doc.styles['Normal']; style.font.name = 'Times New Roman'; style.font.size = Pt(11)
style.paragraph_format.space_after = Pt(6); style.paragraph_format.line_spacing = 1.15
for i in range(1, 4):
    hs = doc.styles[f'Heading {i}']; hs.font.name = 'Times New Roman'
    hs.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

def add_table(headers, rows):
    table = doc.add_table(rows=1, cols=len(headers), style='Light Grid Accent 1')
    for j, h in enumerate(headers):
        table.rows[0].cells[j].text = h
        for p in table.rows[0].cells[j].paragraphs:
            for r in p.runs: r.font.bold = True; r.font.size = Pt(9)
    for row_data in rows:
        row = table.add_row()
        for j, val in enumerate(row_data):
            row.cells[j].text = str(val)
            for p in row.cells[j].paragraphs:
                for r in p.runs: r.font.size = Pt(9)

# 封面
title = doc.add_heading('', level=0); title.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = title.add_run('离网光伏-氢-电池混合储能微电网\n模型整改与再验证计划')
run.font.size = Pt(22); run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.add_run('文档性质：整改备忘录，结果部分尚不能用于论文\n生成日期：2026-08-02\n依据：50项文档层审查意见 + v3.0/v4.2协议').font.size = Pt(12)
doc.add_page_break()

# 第一章
doc.add_heading('第一章 最终判定与优先修正顺序', level=1)
doc.add_paragraph(
    '方法主线（健康-性能接口→六候选路径→Shapley路径效应分解→稀疏激活→决策偏差）可以保留。'
    '当前所有数值结果（C5可行性、年度TE向量、BAT→EL路径信号、六路径分类、Shapley闭合、L4效果、经济评价）'
    '全部属于待验证结果，不能用于论文。'
)
doc.add_heading('1.1 最优先修正顺序', level=2)
add_table(
    ['步骤', '任务', '说明'],
    [
        ['S1', '路径符号统一(v3.0)', 'α₁:BAT→FC, α₂:EL→FC, β₁:BAT→EL, β₂:FC→EL, γ₁:FC→BAT, γ₂:EL→BAT'],
        ['S2', '固定D与动态寿命协议分离', '路径辨识(固定D) vs 寿命评价(动态D+EOL更换)'],
        ['S3', '初末SOC/氢库存闭合验证', 'm_H2(T)≥m_H2(0), SOC闭合, 排除外部能量补贴'],
        ['S4', 'BAT/FC退化重新标定', '含2300h重型数据, BAT降为B级, PEMFC按电流密度归一化'],
        ['S5', 'EOL停机/更换事件实现', 'dD_used=min(dD,1-D_state), 触发停机/更换, 非无限运行'],
        ['S6', '辅机/单位审计+p_aux物理建模', '基于实际辅机负载曲线, 非经验假设'],
        ['S7', '多配置可行域搜索', '含30-45kW目标等级附近临界配置, 非仅超大容量兜底'],
        ['S8', '冻结3-5个代表配置集', '目标等级临界+可行上界, 形成配置集'],
        ['S9', '全反馈六路径与不确定性(固定D)', '区分结构性零路径与统计零路径, Bootstrap区间'],
        ['S10', 'Shapley路径效应分解(EOL前窗口)', '限制在所有联盟均未到EOL的窗口'],
        ['S11', '跨年稳定性(2016/2018)', '冻结配置在多年度的LPSP和TE验证'],
        ['S12', '更新回归测试', '边界/守恒/旧Bug复现/配置哈希'],
        ['扩展', 'L4 EMS + 20年经济评价', 'v4.2已移至扩展阶段, 不阻断核心方法论文'],
    ]
)
doc.add_page_break()

# 第二章
doc.add_heading('第二章 路径符号统一（问题11）', level=1)
doc.add_heading('2.1 v3.0冻结定义', level=2)
add_table(
    ['符号', '路径方向', '归因变量', '默认状态'],
    [
        ['α₁', 'BAT→FC', '母线纹波', '零约束(无可关闭模型)'],
        ['α₂', 'EL→FC', '母线纹波', '零约束'],
        ['β₁', 'BAT→EL', '供氢波动', '零约束'],
        ['β₂', 'FC→EL', '供氢波动', '零约束'],
        ['γ₁', 'FC→BAT', '母线纹波', '零约束'],
        ['γ₂', 'EL→BAT', '母线纹波', '零约束'],
    ]
)
doc.add_paragraph('六路径公式：')
doc.add_paragraph('ΔD*_FC = ΔD_FC·[1 + α₁(θ)D_BAT + α₂(θ)D_EL]', style='Intense Quote')
doc.add_paragraph('ΔD*_EL = ΔD_EL·[1 + β₁(θ)D_BAT + β₂(θ)D_FC]', style='Intense Quote')
doc.add_paragraph('ΔD*_BAT = ΔD_BAT·[1 + γ₁(θ)D_FC + γ₂(θ)D_EL]', style='Intense Quote')
doc.add_paragraph('当前报告将α₂/β₂/γ₁/γ₂错标为FC→BAT/EL→BAT/FC→EL/EL→FC，需统一检查全部CSV、配置和脚本。')
doc.add_page_break()

# 第三章
doc.add_heading('第三章 实验协议分离（问题14-15）', level=1)
add_table(
    ['维度', '路径辨识', '寿命评价'],
    [
        ['源健康状态', '固定D(0/0.5/0.9)', '动态演化'],
        ['年内健康演化', '不允许', '允许'],
        ['EOL处理', '不涉及(截断在EOL前窗口)', '触发停机/更换'],
        ['输出', '路径效应ΔD_target', 'EOL时间、更换次数'],
        ['v4.2协议', '首轮年度验证', 'P2寿命外推'],
    ]
)
doc.add_paragraph(
    '当前报告把两者合并，导致路径效应、EOL饱和和寿命演化相互混杂。'
    'BOL基线改为"动态累计退化"偏离v4.2规定的"年度内固定D=0/0.5/0.9"。'
)
doc.add_page_break()

# 第四章
doc.add_heading('第四章 容量与闭合验证（问题1-6, 32-36）', level=1)
doc.add_heading('4.1 C5定位', level=2)
doc.add_paragraph(
    'C5(PV=300/BAT=800/FC=100/EL=100/H2=1000)只能作为可行性上界。'
    '储氢量1000kg是原上限40kg的25倍，远超原搜索域。'
    '1000kg初始氢若满载将成为外部能量补贴，LPSP=0.23%不能证明年度能量闭合。'
    '需搜索多组可行配置（含30-45kW目标等级附近临界配置）。'
)
doc.add_heading('4.2 缺失的闭合验证', level=2)
add_table(
    ['约束', '当前状态', '要求'],
    [
        ['LPSP≤1%', '已验证0.23%', '保留'],
        ['关键负荷满足率≥99%', '未验证', '新增'],
        ['功率守恒残差', '已验证1.55×10⁻¹⁶', '需在可行配置重验'],
        ['年度氢质量残差', '未验证', '新增: m_H2(T)≥m_H2(0)'],
        ['SOC年末闭合', '未验证', '新增'],
        ['H2年末库存闭合', '未验证', '新增'],
        ['峰值功率覆盖', '未验证', '新增'],
        ['SOC/H2库存边界', '已验证', '需在可行配置重验'],
    ]
)
doc.add_heading('4.3 缺失的工程参数', level=2)
add_table(
    ['参数', '状态', '要求'],
    [
        ['BAT额定功率/P/E比', '未报告', '必须给出'],
        ['弃光率', '未报告', '必须给出'],
        ['EL满负荷小时', '未报告', '必须给出'],
        ['FC运行小时/启停次数', '未报告', '必须给出'],
        ['H2库存最低值/年末值', '未报告', '必须给出'],
        ['100kW FC vs 45kW峰值负荷', '未检查', 'FC>2x峰值,需检查利用率'],
    ]
)
doc.add_page_break()

# 第五章
doc.add_heading('第五章 EOL守卫与退化标定（问题7-10, 26-31）', level=1)
doc.add_heading('5.1 EOL守卫修正', level=2)
doc.add_paragraph(
    '当前守卫(D≥1.0时dD=0)不等于设备寿命模型。设备仍以EOL性能继续无限运行，会高估可靠性。'
    '正确更新式：dD_used = min(dD, 1.0 - D_state)，分别保存raw damage、capped state和EOL时间。'
    '应触发停机、维修或更换，或在路径研究中把EOL后的时段截断。'
)
doc.add_heading('5.2 "FC损伤1.037（封顶）"矛盾', level=2)
doc.add_paragraph(
    '若真正封顶，结果应≤1.0。1.037和1.013说明累计量仍发生时间步越界，或"累计损伤"和"状态D"混用。'
    '必须分别命名：raw cumulative damage vs capped health state。'
)
doc.add_heading('5.3 FC年度TE不可计算', level=2)
doc.add_paragraph(
    'FC在所有模式下一年内达到EOL，两个结果都受上限截断。-2.30%主要是天花板效应。'
    '应改为比较EOL到达时间、EOL前相同暴露期损伤、或固定健康状态下的短窗效应。'
)
doc.add_heading('5.4 BAT退化异常', level=2)
doc.add_paragraph(
    'BAT年度D=0.736对应约14.72%容量损失、SOH约85.28%，约1.36年达到80% EOL。'
    'stationary LFP模型需重新核验单位、时间尺度、循环计数与pack换算。'
    '280Ah电芯级数据迁移到系统级应标B级（不能验证整包热梯度/BMS/变换器影响）。'
    'Q偏差11.9%只验证容量，不覆盖阻抗R(D)和功率P_max(D)。'
)
doc.add_heading('5.5 PEMFC标定方法修正', level=2)
doc.add_paragraph(
    '1kW→100kW线性外推方法本身不合理。应按电流密度、单电池电压、活性面积、单元数和额定功率比例归一化。'
    '未利用项目后续已获得的2300h重型动态循环、极化和EIS数据。'
)
doc.add_heading('5.6 PEMEL评级拆分', level=2)
doc.add_paragraph(
    'B/C级过于模糊。应按参数拆分：BOL电压性能面、阻抗增长、单位制氢能耗增长、Pmax/Pmin、爬坡能力分别评级。'
    '无长期系统数据的动态降额项应保持C或不启用。'
)
doc.add_page_break()

# 第六章
doc.add_heading('第六章 六路径与反事实定义（问题12-13, 16-20）', level=1)
add_table(
    ['问题', '说明', '修正要求'],
    [
        ['TE向量来源不明', '未写明源设备、源D值', '必须标注源设备+D值'],
        ['自效应与跨设备混合', 'BAT的-0.15%是自反馈,非跨设备路径', '分成源设备自效应与跨设备效应'],
        ['EL的+25.10%过强', '单配置单年份,无Bootstrap区间', '只能称候选数值信号'],
        ['BAT→FC的-2.30%不可判定', 'FC已触顶,右删失', '标记为不可判定/右删失'],
        ['全反馈无操作级定义', '未写清FC/EL改变哪些参数', '列出效率/Pmax/Pmin/爬坡/启停'],
        ['结构性vs统计零路径', 'EMS未读取健康参数=结构性零', '确认进入闭环决策后效应含0=统计零'],
        ['激活标准缺失', '只靠TE正负判断', '恢复7项标准:阈值/CI/方向/复现/NC/闭合/交互'],
    ]
)
doc.add_page_break()

# 第七章
doc.add_heading('第七章 Shapley与统计方法（问题21-25）', level=1)
add_table(
    ['问题', '说明', '修正要求'],
    [
        ['EOL守卫引入不连续', '不同联盟暴露期不同', '限制EOL前窗口或改用EOL时间'],
        ['闭合验证不足', '只证明恒等式', '验证归因冻结/守恒保持/无串扰'],
        ['归因变量未定义', '四组字段/OFF操作/联盟状态未列', '必须列出'],
        ['稀疏激活无模型', '只作为名称', '明确阈值分类/稀疏回归/零膨胀后验'],
        ['TE未同步AE', '小分母放大', '同时给AE/TE/区间'],
    ]
)
doc.add_page_break()

# 第八章
doc.add_heading('第八章 文档文字错误修正（问题42-50）', level=1)
add_table(
    ['编号', '错误', '修正'],
    [
        ['42', '"完成第五章残余工作(R1-R5)"', '"完成第四章残余工作(S1-S12+扩展)"'],
        ['43', 'L13/L19/L20均被EOL守卫替代', 'L13/L19已解决, L20保留(守卫不消除模型不确定性)'],
        ['44', '"FC损伤1.037(封顶)"与"D年末FC=1.000"混用', '分别命名raw cumulative damage与capped health state'],
        ['45', '对比表"消除幻影"无法显示修复效果', '比较未封顶/封顶状态/EOL时间/EOL后处理'],
        ['46', '"1.55×10 ¹⁶ ⁻"排版错误', '1.55×10⁻¹⁶'],
        ['47', '"所有审查报告已重定位"缺文件级证明', '列出被更新的旧报告/版本号/状态'],
        ['48', '文档名"全面总结报告"误导', '改为"模型整改与再验证计划"'],
        ['49', 'XGBoost状态不明', '标注为冻结/未验证/扩展状态'],
        ['50', '缺可复现性元数据', '补充配置哈希/代码提交号/数据版本/种子/时间'],
    ]
)
doc.add_page_break()

# 第九章
doc.add_heading('第九章 当前结论状态', level=1)
doc.add_heading('9.1 可保留（方法主线）', level=2)
retained = [
    '健康-性能动态接口设计',
    '六候选闭环反事实路径框架（符号需统一为v3.0定义）',
    'Shapley路径效应分解框架（需重新定义归因变量）',
    '稀疏激活概念（需明确模型）',
]
for r in retained:
    doc.add_paragraph(r, style='List Bullet')

doc.add_heading('9.2 待验证结果（全部）', level=2)
add_table(
    ['结果', '状态', '前置条件'],
    [
        ['C5可行性', '待验证(超大容量兜底)', 'S7多配置搜索'],
        ['年度TE向量', '待验证(FC触顶,基线不可行)', 'S4-S5退化标定+EOL事件'],
        ['BAT→EL路径', '待验证(单配置初步信号)', 'S9全反馈六路径'],
        ['六路径分类', '待验证(符号错误+结构预设)', 'S1符号统一+S9全反馈'],
        ['Shapley闭合', '待验证(EOL不连续)', 'S10 EOL前窗口'],
        ['L4效果', '扩展阶段(v4.2)', 'S12后'],
        ['经济评价', '扩展阶段(v4.2)', 'S12后'],
    ]
)

doc.add_heading('9.3 已撤销的错误结论', level=2)
revoked = [
    '"无附加条件进入论文撰写"',
    '"TE=+6.59%为BAT-only上界"',
    '"BAT TE=+1.13%为主结论"',
    '"Shapley闭合残差1.55×10⁻¹⁶证明方法可信"',
    '"四条零路径为反事实辨识发现"',
]
for r in revoked:
    doc.add_paragraph(r, style='List Bullet')

doc.add_paragraph()
doc.add_paragraph(
    '本文档为整改备忘录，指导后续修正实施。'
    '所有数值结果在完成S1-S12并验证前，不能用于论文。'
).runs[0].font.bold = True

# 保存
out_path = Path(r'D:\matlab\C\09_docs\02_治理与口径/12_整改与再验证计划.docx')
doc.save(str(out_path))
print(f'文档已生成: {out_path}')
print(f'段落数: {len(doc.paragraphs)}, 表格数: {len(doc.tables)}')
