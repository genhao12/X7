# -*- coding: utf-8 -*-
"""生成W1实施计划阶段产出物文档"""
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

doc = Document()
style = doc.styles['Normal']
style.font.name = '宋体'; style.font.size = Pt(10.5)
style._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
for hname, hsize in [('Heading 1', 16), ('Heading 2', 13), ('Heading 3', 11)]:
    hs = doc.styles[hname]; hs.font.name = '黑体'; hs.font.size = Pt(hsize)
    hs.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    hs._element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')

def add_para(text, bold=False, color=None, size=None):
    p = doc.add_paragraph(); r = p.add_run(text); r.bold = bold
    if color: r.font.color.rgb = RGBColor(*color)
    if size: r.font.size = Pt(size)
    return p

def add_verify(text):
    p = doc.add_paragraph()
    r = p.add_run('【需人工核验】' + text)
    r.italic = True; r.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B); r.font.size = Pt(10)

def add_note(text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.color.rgb = RGBColor(0x2C, 0x3E, 0x50); r.font.size = Pt(9.5)

def add_table(headers, rows, col_widths=None):
    t = doc.add_table(rows=1+len(rows), cols=len(headers))
    t.style = 'Light Grid Accent 1'; t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ci, h in enumerate(headers):
        cell = t.rows[0].cells[ci]; cell.text = h
        for p in cell.paragraphs:
            for r in p.runs: r.bold = True; r.font.size = Pt(9.5)
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            cell = t.rows[ri+1].cells[ci]; cell.text = str(val)
            for p in cell.paragraphs:
                for r in p.runs: r.font.size = Pt(9)
    if col_widths:
        for ri in range(len(t.rows)):
            for ci, w in enumerate(col_widths):
                t.rows[ri].cells[ci].width = Inches(w)
    return t

# 标题页
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('离网光伏–氢–电池混合储能微电网'); r.bold = True
r.font.size = Pt(20); r.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('W1实施计划阶段产出物'); r.bold = True
r.font.size = Pt(18); r.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('实施计划 ｜ 原型功能清单 ｜ 流程文档 ｜ 测试准备材料')
r.font.size = Pt(11); r.font.color.rgb = RGBColor(0x6A, 0x7A, 0x8A)
add_para('')
add_note('基于文档：05_阶段产出物/04_方法模型方案设计.docx（354段21表6项产出物）')
add_note('每项内容标注依据、用途和需人工核验内容，可直接进入W1执行阶段使用。')
add_note('整理日期：2026-07-30')
doc.add_page_break()
print("Base done")

# ============ 产出物一：实施计划 ============
doc.add_heading('产出物一：实施计划', level=1)
add_para('W1执行分为7个阶段，每阶段设Go/No-Go门控。以下按阶段列出任务、资源、输入输出、时间、风险和备选方案。')
add_note('依据：产出物三§3.7 12周执行计划 + 产出物四§4.7 CP1-CP10检查点 + 产出物六风险缓解措施')
add_note('用途：为W1执行提供逐阶段任务清单和门控标准，确保前一阶段通过后才进入下一阶段')
add_verify('每个阶段结束时逐条核对Go/No-Go门控，未通过则执行备选方案而非强行推进。')

# 阶段总览表
doc.add_heading('阶段总览', level=2)
add_table(
    ['阶段', '时间', '核心任务', '门控检查点', 'Go条件'],
    [
        ['P1 数据冻结与环境', '第1周', '数据哈希/BLAST-Lite兼容/性能基准/NC1预测试', 'CP1+CP2+CP3', '46条数据DOI可达+哈希匹配; BLAST-Lite可运行; 性能达标; NC1残差<10⁻⁵'],
        ['P2 健康-性能映射', '第2-3周', 'BAT/FC/EL函数形式拟合+Bootstrap区间', 'CP4', '方向单调≥98%+连续违反≤3; 同源NRMSE≤10%; 留出95%覆盖≥90%'],
        ['P3 退化模型与EMS', '第4周', '独立退化+15min闭环+3种EMS伪代码', 'CP5', '功率/氢守恒残差≤额定×10⁻⁶; 边界无越界'],
        ['P4 反事实引擎', '第5周', '配对运行+NC1/NC2/NC3+路径分离+字段校验', 'CP6+CP7', 'NC1残差≤10⁻⁴ EOL/年; 配对字段哈希一致'],
        ['P5 路径筛选与归因', '第6-7周', '336窗口筛选→N_eff初步→V2路径效应分解', 'CP8+CP9', '六路径三分类; N_eff确定; Shapley闭合≤5%'],
        ['P6 不确定性与年度', '第8-10周', 'V3a/V3b LHS+Bootstrap+V4年度验证', 'CP10', 'AE/TE区间+方向一致率; 年度方向复现'],
        ['P7 结果与论文', '第11-12周', '图表/消融/限制声明/复现包/论文初稿', '—', '主结果冻结; 可投稿初稿'],
    ],
    col_widths=[1.3, 1.0, 2.5, 1.2, 2.5]
)

# P1 详细
doc.add_heading('P1：数据冻结与环境验证（第1周）', level=2)
add_table(
    ['项目', '内容'],
    [
        ['任务', '①数据台账46条哈希冻结 ②BLAST-Lite兼容性烟雾测试(trapz→trapezoid) ③20场景性能基准 ④NC1数值精度预测试 ⑤NAICNO仓库验证 ⑥requirements.lock保存'],
        ['所需资源', '数据文献/586文件; 02_data_processed/tradestreet_pipeline/76CSV; BLAST-Lite Sony/Murata+250Ah; NAICNO/wp7-UC2 GitHub仓库; Python环境'],
        ['输入', '数据文献台账46条; frozen_parameters_v2.2.json; UCSD 76CSV; BLAST-Lite Python库'],
        ['输出', 'data_registry.json(哈希+DOI+证据等级); requirements.lock; benchmark_report.csv; nc1_precision_test.json; naicno_verification.json'],
        ['关键风险', 'NAICNO仓库不可访问(中→低,替代方案:手动构建BOL面); BLAST-Lite基准值偏差>10%(中,需查版本); NC1残差>10⁻⁵(中,需float128/Kahan)'],
        ['备选方案', 'NAICNO不可用→PEMEL电压/EIS原始数据手动构建BOL面(2-3周); NC1精度不足→float128或Kahan补偿求和; 性能不达标→缓存/向量化/延迟写盘'],
        ['依据', '产出物四§4.1数据资源 + §4.4软件计算 + §4.7 CP1/CP2/CP3'],
        ['用途', '冻结数据版本确保可追溯; 验证环境可运行; 确立性能基准和数值精度基线'],
    ]
)
add_verify('逐条核对46条数据DOI/URL可达且哈希匹配; BLAST-Lite Sony 5.22%/250Ah 2.04%基准值实测偏差<10%; NC1残差量级<10⁻⁵或降级方案确定; NAICNO仓库可访问或替代方案确定。')

# P2 详细
doc.add_heading('P2：健康-性能映射（第2-3周）', level=2)
add_table(
    ['项目', '内容'],
    [
        ['任务', '①BAT E_usable(D)分段线性+R(D)幂律拟合+P_max边界 ②FC极化方程V(j,D)+η_stack,rel ③EL V(j,T,p,D)+EIS修正 ④Bootstrap 95%区间 ⑤Sony/Murata方向交叉验证(风险1缓解)'],
        ['所需资源', '280Ah LFP 16时点容量+HPPC; BLAST-Lite 250Ah+Sony/Murata; IEEE PHM FC1/FC2; HyFaB Loadcycle; NAICNO数字孪生; PEMEL EIS退化数据'],
        ['输入', '01_data_raw/degradation/下的原始退化数据; frozen_parameters_v2.2.json'],
        ['输出', 'health_performance_bat.json(E_usable/R/P_max参数+CI); health_performance_fc.json; health_performance_el.json; fitting_report.csv(NRMSE+方向单调性)'],
        ['关键风险', 'FC线性假设D>0.5高估(中→低,D=0.50主证据+保守非线性检查); BAT阻抗单一来源(中→低,Sony/Murata交叉验证); EL NAICNO不可用(已在P1处理)'],
        ['备选方案', 'FC: D>0.5用二次外推保守检查; BAT: Sony/Murata r作方向验证; EL: 手动构建BOL面(如P1替代方案启动)'],
        ['依据', '产出物二模块4函数形式表(9行) + 产出物四§4.2健康-性能接口 + 产出物六风险1/风险2缓解'],
        ['用途', '将设备健康D映射为可用容量/阻抗/效率/功率的相对变化,为M1/M2提供性能参数输入'],
    ]
)
add_verify('BAT容量由280Ah+250Ah BLAST约束; 阻抗由280Ah HPPC优先辨识且Sony/Murata方向一致; FC极化方程跨源NRMSE≤15%; EL EIS修正c>0,d>0; 方向单调性≥98%时点满足AND连续违反≤3。')

# P3 详细
doc.add_heading('P3：退化模型与EMS（第4周）', level=2)
add_table(
    ['项目', '内容'],
    [
        ['任务', '①三设备独立损伤累计模型(启停/爬坡/负荷停留/吞吐) ②PV/BAT/FC/EL/H₂ 15min闭环运行模型 ③3种EMS伪代码实现(battery_first/hydrogen_first/balanced) ④Ramp_max约束实现'],
        ['所需资源', 'P2输出的health_performance_*.json; UCSD TradeStreet 15min数据; frozen_parameters_v2.2.json; 产出物二模块5 EMS伪代码'],
        ['输入', 'health_performance_bat/fc/el.json; scenario_config.json(容量/EMS/健康/窗口); UCSD 76CSV'],
        ['输出', 'component_models.py; degradation_independent.py; ems_rules.py; 守恒验证报告conservation_check.csv'],
        ['关键风险', 'EMS实现歧义(低,伪代码已明确); 守恒残差超标(低,10⁻⁶阈值合理); 边界越界(低,约束已定义)'],
        ['备选方案', '守恒残差超标→查代码数值积分方案; 边界越界→增加clip约束; 性能不达标→向量化+缓存'],
        ['依据', '产出物二模块2/3/5 + 产出物四§4.5关键参数 + 产出物五创新点1(参数化进入EMS)'],
        ['用途', '建立可运行的15min闭环仿真系统,为V1路径筛选提供基础运行平台'],
    ]
)
add_verify('功率残差≤额定功率×10⁻⁶; 氢质量残差≤累计氢吞吐×10⁻⁶; SOC∈[SOC_min,SOC_max]; P_ch/dis≤P_max(D); 爬坡≤Ramp_max; EMS规则在M2层不随D变化。')

# P4 详细
doc.add_heading('P4：反事实引擎（第5周）', level=2)
add_table(
    ['项目', '内容'],
    [
        ['任务', '①配对运行引擎(观测vs反事实) ②源设备性能恢复机制(g_i(D)→g_i(0)) ③NC1健康映射旁路 ④NC2命令回放 ⑤NC3目标损伤旁路 ⑥7运行×6路径提取矩阵实现 ⑦自动字段哈希校验'],
        ['所需资源', 'P3输出的component_models/degradation_independent/ems_rules; P2的health_performance参数; 产出物二模块6路径提取矩阵'],
        ['输入', 'scenario_config.json; health_performance_*.json; 随机种子'],
        ['输出', 'counterfactual_engine.py; damage.csv(AE/TE); nc1_check.csv; nc2_check.csv; nc3_check.csv; pairing_hash.json'],
        ['关键风险', 'NC1残差>10⁻⁴(中,需查代码泄漏或float128); 配对字段不一致(低,哈希校验); 路径分离错误(低,提取矩阵已定义)'],
        ['备选方案', 'NC1不达标→float128/Kahan求和; 配对不一致→查种子/初始状态; 路径分离错误→对照提取矩阵逐Run核对'],
        ['依据', '产出物二模块6 + 产出物三§3.4 7运行复用 + 产出物五创新点3(配对反事实)'],
        ['用途', '通过"只恢复源设备性能"识别路径效应AE,按目标设备分离提取六路径'],
    ]
)
add_verify('NC1残差≤10⁻⁴ EOL/年(或float128/Kahan后达标); 配对字段哈希完全一致(气象/负荷/SOC/储氢/容量/EMS/种子); 7运行复用覆盖六路径且提取公式正确。')

# P5 详细
doc.add_heading('P5：路径筛选与路径效应分解（第6-7周）', level=2)
add_table(
    ['项目', '内容'],
    [
        ['任务', '①V1: 12配置×4窗口×7健康=336次路径筛选 ②V1负对照: 72次NC1+NC2 ③N_eff初步分类(点估计) ④V2: N_eff×6锚点×16联盟路径效应分解 ⑤Shapley值φ_m+交互指数+闭合误差 ⑥物理可解释性检查(风险4缓解) ⑦应力测试场景(风险5缓解)'],
        ['所需资源', 'P4反事实引擎; P3闭环模型; 产出物三§3.2 V2锚点定义; 产出物二模块7联盟状态规范'],
        ['输入', 'scenario_config.json(12配置×4窗口×7健康); counterfactual_engine.py; 联盟状态ON/OFF定义表'],
        ['输出', 'v1_screening.csv(336+72次); path_classification.json(有效/条件性/零路径+N_eff); v2_mediation.csv(16联盟值+φ_m+交互指数); shapley_closure.csv'],
        ['关键风险', 'N_eff=0零路径(中→低,应力测试+学术重构); Shapley闭合>5%(低,解析值应≈0); 物理可解释性不符(低,检查后只报总效应)'],
        ['备选方案', 'N_eff=0→增加应力测试场景(D=0.90+C4+低氢储备); 闭合>5%→查联盟值估计噪声; 物理不符→只报总效应不分解'],
        ['依据', '产出物三§3.1计算量+§3.2锚点+§3.6 N_eff两阶段 + 产出物二模块7 + 产出物六风险4/5缓解'],
        ['用途', '识别有效退化耦合路径并分解为四归因组贡献,识别主导传导机制'],
    ]
)
add_verify('六路径三分类(有效/条件性/零路径)完成; N_eff写入V2计划且为初步值; Shapley闭合≤5%(解析值应≈0,否则查噪声); 交互占比分级完成(≤30%/30-50%/>50%); 主导归因组物理可解释性检查通过。')

# P6 详细
doc.add_heading('P6：不确定性与年度验证（第8-10周）', level=2)
add_table(
    ['项目', '内容'],
    [
        ['任务', '①V3a: 50组LHS/Bootstrap+初步PRCC(第8周) ②N_eff最终确认(CI判据) ③V3b: 补充50组+Spearman+敏感性排序(第9周前半) ④V4: 2017/2018固定D年度验证6配置×2年×7健康=84次(第9-10周) ⑤M1/M2偏差量化 ⑥辅机效率端点鲁棒性测试(风险3缓解)'],
        ['所需资源', 'P5路径分类+路径效应分解结果; LHS 10参数空间; 2017/2018 UCSD年度数据; 辅机效率区间[0.85,0.92]/[0.88,0.95]'],
        ['输入', 'path_classification.json; v2_mediation.csv; lhsspace.json(10参数); 2017/2018年度数据'],
        ['输出', 'v3a_bootstrap.csv; v3b_spearman.csv; sensitivity_ranking.json; v4_annual.csv(84次); bias_x_report.csv; endpoint_robustness.csv'],
        ['关键风险', 'V3计算量偏差(低,N_eff=4时800次已明确); 年度方向不一致(低,2017/2018独立年); 辅机端点AE翻转(中→低,标记为条件性)'],
        ['备选方案', '年度方向不一致→报告两年分别结果不合并; 辅机端点翻转→路径标记条件性; V3性能不足→减少LHS组数或并行化'],
        ['依据', '产出物三§3.5 LHS参数+§3.6 N_eff两阶段 + 产出物二模块8 + 产出物六风险3/6/7缓解'],
        ['用途', '量化路径效应不确定性区间,验证跨年度方向稳定性,量化M1独立退化决策偏差'],
    ]
)
add_verify('AE/TE Bootstrap 95%区间+方向一致率≥90%; N_eff最终确认(CI判据); 2017/2018方向相同且至少一年95%不跨0; Bias_X=(X_M1−X_M2)/max(|X_M2|,10⁻⁶); 辅机端点AE符号稳定性评估完成。')

# P7 详细
doc.add_heading('P7：结果整理与论文（第11-12周）', level=2)
add_table(
    ['项目', '内容'],
    [
        ['任务', '①图表自动生成 ②消融实验(M0/M1/M2对比) ③限制声明撰写 ④复现包打包 ⑤扩展可行性评估E1-E4 ⑥论文方法/结果/讨论初稿 ⑦内部复核'],
        ['所需资源', 'P6全部结果文件; 产出物五创新点+产出物六风险缓解; 论文模板'],
        ['输入', 'v1-v4全部CSV/JSON结果; bias_x_report.csv; sensitivity_ranking.json'],
        ['输出', '论文初稿.docx; figures/(自动生成图表); reproducibility_package.zip; extension_assessment.json(E1-E4)'],
        ['关键风险', '20年外推过度推断(高→中,外推假设清单+核心/扩展分离); UCSD单站泛化(中→低,气候参数+可迁移性条件); 固定EMS偏差(中→低,下界解释)'],
        ['备选方案', 'E1-E4不满足→仅报年度结果+文献区间; 论文初稿不完整→延期1周提交'],
        ['依据', '产出物五创新点 + 产出物六风险6/7/8缓解 + 产出物一§1.4扩展时间线'],
        ['用途', '将方法设计转化为可投稿的论文初稿,明确贡献和限制'],
    ]
)
add_verify('主结果冻结且不可修改; 论文限制声明包含:温度域/规模迁移/固定EMS/年度外推/UCSD单站/联盟状态数学构造; E1-E4评估报告完成; 复现包含requirements.lock+数据哈希+随机种子。')

print("Section 1 done")

doc.add_page_break()

# ============ 产出物二：原型功能清单 ============
doc.add_heading('产出物二：原型功能清单', level=1)
add_para('以下列出W1需实现的9个核心模块+4个支撑工具,每个含功能描述、输入输出、依赖和测试要点。')
add_note('依据：产出物二模块1-9 + 产出物四§4.3输出文件清单')
add_note('用途：为开发团队提供模块级功能规格和接口定义,确保模块间数据流正确衔接')
add_verify('每个模块实现后需通过对应单元测试(见产出物四测试准备材料)后方可集成。')

# 核心模块表
doc.add_heading('核心模块（9个）', level=2)
add_table(
    ['模块', '功能描述', '输入文件', '输出文件', '依赖模块', '关键函数'],
    [
        ['1 data_registry', '文件哈希/DOI/证据等级/训练域管理', '数据文献台账46条; 原始数据文件', 'data_registry.json(哈希+DOI+等级+训练域)', '—', 'compute_hash(); verify_doi(); register_dataset()'],
        ['2 component_models', 'PV/BAT/FC/EL/H₂ 15min时步运行', 'scenario_config.json; UCSD 76CSV; health_performance_*.json', 'trajectory.parquet(15min功率/SOC/H₂/启停/爬坡)', '模块4', 'run_pv(); run_bat(); run_fc(); run_el(); run_h2()'],
        ['3 degradation_independent', '三设备独立损伤累计(启停/爬坡/停留/吞吐)', 'trajectory.parquet; health_performance_*.json', 'damage_independent.csv(三设备D_i时序)', '模块2/4', 'accumulate_stress(); compute_D()'],
        ['4 health_performance', 'D→性能参数映射+Bootstrap CI', '280Ah HPPC; BLAST-Lite; IEEE PHM; NAICNO', 'health_performance_bat/fc/el.json', '—', 'fit_E_usable(); fit_R(); fit_V_polarization(); bootstrap_ci()'],
        ['5 ems_rules', '3种EMS调度(battery_first/hydrogen_first/balanced)', 'scenario_config.json; P_res; SOC; H₂_level', 'ems_commands.json(每时步充放电/制氢/发电指令)', '模块2', 'battery_first(); hydrogen_first(); balanced(); check_constraints()'],
        ['6 counterfactual_engine', '配对运行+源设备性能恢复+NC1/NC2/NC3', 'scenario_config.json; health_performance_*.json; ems_rules', 'damage.csv(AE/TE); nc1/nc2/nc3_check.csv; pairing_hash.json', '模块2/3/4/5', 'run_pair(); restore_source_perf(); extract_AE(); verify_pairing_hash()'],
        ['7 mediation', '16联盟Shapley+四归因组φ_m+交互指数', 'path_classification.json; counterfactual_engine', 'v2_mediation.csv(16联盟值+φ_m+I_mn); shapley_closure.csv', '模块6', 'compute_coalition_value(); shapley_value(); interaction_index(); check_closure()'],
        ['8 annual_validation', '2017/2018固定D年度复现', '年度UCSD数据; scenario_config.json(C1+C4×3EMS)', 'v4_annual.csv(84次年度结果); bias_x_report.csv', '模块2/3/4/5/6', 'run_annual(); compute_bias_x(); check_direction()'],
        ['9 扩展模块', 'XGBoost代理/L4 EMS/20年NPC(核心通过后)', 'v1-v4全部结果; path_classification.json', 'surrogate_model.json; ems_l4_config.json; economics.csv', '模块1-8', 'train_surrogate(); run_l4_ems(); compute_npc()'],
    ],
    col_widths=[1.2, 1.8, 1.5, 1.5, 0.8, 1.5]
)

# 支撑工具表
doc.add_heading('支撑工具（4个）', level=2)
add_table(
    ['工具', '功能描述', '输入', '输出', '依赖', '关键函数'],
    [
        ['A lhs_sampler', 'LHS 10参数采样+Bootstrap重采样', 'lhsspace.json(10参数分布/范围)', 'lhs_samples.csv(100组参数); bootstrap_results.csv', '—', 'latin_hypercube(); bootstrap_resample()'],
        ['B performance_benchmark', '20场景7天运行时间基准', 'scenario_config.json(20场景)', 'benchmark_report.csv(中位/P95/年度时间)', '模块2', 'run_benchmark(); compute_stats()'],
        ['C conservation_checker', '功率/氢守恒残差检查', 'trajectory.parquet', 'conservation_check.csv(残差时序)', '模块2', 'check_power_balance(); check_h2_balance()'],
        ['D figure_generator', '论文图表自动生成', 'v1-v4全部CSV/JSON', 'figures/*.png/pdf', '—', 'plot_path_classification(); plot_shapley(); plot_annual(); plot_bias()'],
    ],
    col_widths=[1.0, 1.8, 1.5, 1.5, 0.8, 1.5]
)

# 模块依赖关系
doc.add_heading('模块依赖关系', level=2)
add_para('开发顺序（严格依赖链）：', bold=True, size=10.5)
for line in [
    '第1层(无依赖): 模块1 data_registry + 模块4 health_performance + 工具A lhs_sampler + 工具B benchmark',
    '第2层(依赖4): 模块2 component_models + 工具C conservation_checker',
    '第3层(依赖2/4): 模块3 degradation_independent + 模块5 ems_rules',
    '第4层(依赖2/3/4/5): 模块6 counterfactual_engine',
    '第5层(依赖6): 模块7 mediation',
    '第6层(依赖2-6): 模块8 annual_validation',
    '第7层(依赖1-8): 模块9 扩展 + 工具D figure_generator',
]:
    add_para(line, size=10)
add_verify('模块开发严格按依赖链顺序进行,不可跳层。第1层模块可并行开发。')

print("Section 2 done")

doc.add_page_break()

# ============ 产出物三：流程文档 ============
doc.add_heading('产出物三：流程文档', level=1)

# 端到端执行流程
doc.add_heading('端到端执行流程', level=2)
add_para('以下为W1从数据冻结到论文初稿的端到端执行流程,每步含输入/输出/门控。', size=10.5)
add_note('依据：产出物三§3.7 12周执行计划 + 产出物四§4.7 CP1-CP10')
add_note('用途：为执行团队提供逐步操作指南,确保每步通过门控后才进入下一步')
add_verify('每个门控检查点(CP)未通过时,执行对应备选方案,不可跳过门控强行推进。')

flow = [
    ('Step 1: 数据冻结(P1第1周)', '数据文献台账46条+原始数据', 'data_registry.json(哈希+DOI+等级)', 'CP1: 46条DOI可达+哈希匹配+NAICNO验证'),
    ('Step 2: 环境验证(P1第1周)', 'BLAST-Lite库+Python环境', 'requirements.lock+benchmark_report.csv', 'CP2+CP3: BLAST-Lite可运行+性能达标'),
    ('Step 3: NC1预测试(P1第1周)', '1次7天BOL场景', 'nc1_precision_test.json', 'CP3: NC1残差<10⁻⁵或降级方案确定'),
    ('Step 4: BAT健康-性能(P2第2周)', '280Ah HPPC+BLAST-Lite', 'health_performance_bat.json', 'CP4: 方向单调≥98%+NRMSE≤10%'),
    ('Step 5: FC健康-性能(P2第2-3周)', 'IEEE PHM FC1/FC2+HyFaB', 'health_performance_fc.json', 'CP4: 跨源NRMSE≤15%+线性假设标注'),
    ('Step 6: EL健康-性能(P2第3周)', 'NAICNO数字孪生+EIS', 'health_performance_el.json', 'CP4: c>0,d>0+NRMSE≤10%'),
    ('Step 7: 闭环模型+EMS(P3第4周)', 'health_performance_*.json+UCSD数据', 'component_models.py+ems_rules.py', 'CP5: 守恒残差≤10⁻⁶+边界无越界'),
    ('Step 8: 反事实引擎(P4第5周)', '闭环模型+EMS+性能参数', 'damage.csv+nc1/nc2/nc3_check.csv', 'CP6+CP7: NC1≤10⁻⁴+配对哈希一致'),
    ('Step 9: V1路径筛选(P5第6周)', '12配置×4窗口×7健康=336次', 'v1_screening.csv+path_classification.json', 'CP8: 三分类+N_eff初步确定'),
    ('Step 10: V2路径效应分解(P5第7周)', 'N_eff×6锚点×16联盟', 'v2_mediation.csv+shapley_closure.csv', 'CP9: 闭合≤5%+物理可解释性检查'),
    ('Step 11: V3a不确定性(P6第8周)', '50组LHS+Bootstrap+PRCC', 'v3a_bootstrap.csv+sensitivity_ranking.json', 'N_eff最终确认(CI判据)'),
    ('Step 12: V3b补充(P6第9周前半)', '补充50组+Spearman', 'v3b_spearman.csv', '主导不确定性参数识别完成'),
    ('Step 13: V4年度验证(P6第9-10周)', '6配置×2年×7健康=84次', 'v4_annual.csv+bias_x_report.csv', 'CP10: 年度方向复现+偏差量化'),
    ('Step 14: 结果整理(P7第11周)', 'V1-V4全部结果', 'figures/+reproducibility_package.zip', '主结果冻结'),
    ('Step 15: 论文初稿(P7第12周)', '全部结果+创新点+限制声明', '论文初稿.docx', '可投稿初稿'),
]
add_table(
    ['步骤', '输入', '输出', '门控/通过标准'],
    [list(item) for item in flow],
    col_widths=[1.5, 2.0, 2.0, 2.2]
)

# 数据流图（文字版）
doc.add_heading('数据流图（文字版）', level=2)
add_para('原始数据 → [模块1冻结] → data_registry.json', size=10)
add_para('  → [模块4拟合] → health_performance_*.json', size=10)
add_para('    → [模块2运行] → trajectory.parquet', size=10)
add_para('      → [模块3累计] → damage_independent.csv', size=10)
add_para('      → [模块5调度] → ems_commands.json', size=10)
add_para('    → [模块6配对] → damage.csv(AE/TE) + nc1/nc2/nc3_check.csv', size=10)
add_para('      → [模块7分解] → v2_mediation.csv(φ_m) + shapley_closure.csv', size=10)
add_para('    → [模块8年度] → v4_annual.csv + bias_x_report.csv', size=10)
add_para('      → [模块9扩展] → surrogate/l4/economics (条件满足时)', size=10)
add_para('    → [工具D出图] → figures/*.png/pdf', size=10)
add_note('依据：产出物二模块1-9依赖关系 + 产出物四§4.3输出文件清单')
add_note('用途：确保模块间数据流衔接正确,无断链或循环依赖')
add_verify('核对每个箭头对应的数据文件格式(JSON/CSV/Parquet)和字段一致性。')

# 检查点门控流程
doc.add_heading('检查点门控流程', level=2)
add_para('G1门控(数据与子模型) → G2门控(路径引擎) → G3门控(方法核心) → G4门控(扩展)', bold=True, size=10.5)
add_table(
    ['门控', '检查点', '时机', '通过标准', '未通过处理'],
    [
        ['G1', 'CP1+CP2+CP3', '第1周', '数据冻结+BLAST-Lite兼容+性能达标+NC1预测试', '不进入P2,执行备选方案'],
        ['G2', 'CP4+CP5+CP6+CP7', '第2-5周', '方向单调+守恒+NC1达标+配对一致', '不进入P5,回查代码/数据'],
        ['G3', 'CP8+CP9+CP10', '第6-10周', '路径分类+Shapley闭合+年度复现', '不进入P7,执行降级方案'],
        ['G4', 'E1-E4评估', '第11周', '扩展启动条件满足', '仅报年度结果+文献区间'],
    ],
    col_widths=[0.5, 1.5, 1.0, 2.5, 1.8]
)
add_note('依据：产出物三§3.7 12周计划 + 产出物四§4.7 CP清单')
add_note('用途：确保每阶段质量达标后才进入下一阶段,防止错误传播')
add_verify('每个门控未通过时,需记录未通过原因、执行备选方案、重新检查后方可放行。')

print("Section 3 done")

doc.add_page_break()

# ============ 产出物四：测试准备材料 ============
doc.add_heading('产出物四：测试准备材料', level=1)
add_para('以下列出W1执行期间需要的单元测试、集成测试、回归测试和NC控制试验规格。')
add_note('依据：产出物四§4.6验收门槛(13项) + 产出物二模块6 NC1/NC2/NC3 + 产出物六风险缓解')
add_note('用途：为开发团队提供明确的测试用例、测试数据和通过标准,确保代码质量')
add_verify('每个模块集成前需通过全部对应单元测试;每次代码修改后需运行回归测试确保无退化。')

# 单元测试
doc.add_heading('单元测试清单', level=2)
add_table(
    ['模块', '测试用例', '测试数据', '预期结果', '通过标准'],
    [
        ['模块1', 'test_hash_consistency: 对同一文件两次哈希', '280Ah aging.zip', '两次哈希值相同', 'SHA256完全一致'],
        ['模块1', 'test_doi_reachable: 检查46条DOI可达性', '台账46条DOI列表', '全部返回HTTP 200', '100%可达或标注替代'],
        ['模块4', 'test_E_usable_monotone: BAT容量随D非增', '280Ah 16时点容量数据', 'E_usable(D)非增函数', '≥98%时点满足+连续违反≤3'],
        ['模块4', 'test_R_positive: BAT阻抗R(D)=R₀(1+aD^b), a>0,b>0', '280Ah HPPC 16时点', '拟合参数a>0,b>0', 'NRMSE≤10%+a>0+b>0'],
        ['模块4', 'test_FC_polarization: FC极化方程跨源NRMSE', 'IEEE PHM FC1/FC2', '拟合曲线与数据吻合', '跨源NRMSE≤15%'],
        ['模块4', 'test_EL_EIS: EL R_ohm/R_ct随D增长', 'PEMEL EIS退化数据', 'c>0,d>0', 'c>0+d>0+NRMSE≤10%'],
        ['模块5', 'test_battery_first: P_res>0时先充BAT', '1天BOL场景', 'BAT优先充电至满再EL', '优先级链正确'],
        ['模块5', 'test_hydrogen_first: P_res>0时先制氢', '1天BOL场景', 'EL优先制氢至满再BAT', '优先级链正确'],
        ['模块5', 'test_balanced: 权重函数SOC依赖', '1天BOL场景', '低SOC时w_BAT>0.5', '权重方向正确'],
        ['模块5', 'test_ramp_constraint: 爬坡≤Ramp_max', '1天高波动场景', '单步功率变化≤Ramp_max', '无爬坡越界'],
        ['模块6', 'test_NC1_zero: g_i(D_i)=g_i(0)时ΔD_j≈0', '7天BOL场景', '残差在10⁻⁷~10⁻⁵量级', '≤10⁻⁴ EOL/年(或float128后)'],
        ['模块6', 'test_source_restore: 反事实源设备恢复D=0', '1天D_BAT=0.50场景', 'BAT性能恢复BOL,其他不变', '性能参数=E_usable(0)/R(0)/P_max(0)'],
        ['模块6', 'test_AE_extraction: Run2提取α₁和β₁', 'Run1(BOL)+Run2(BAT D=0.50)', 'AE(α₁)=ΔD_FC,Run2−ΔD_FC,Run1', '公式与提取矩阵一致'],
        ['模块7', 'test_shapley_closure: φ_1+φ_2+φ_3+φ_4=总效应', '1条有效路径的16联盟值', 'Shapley之和=AE', '闭合误差≤5%(解析值≈0)'],
        ['模块7', 'test_coalition_OFF: OFF归因组g_i(D)→g_i(0)', '1联盟状态', 'OFF归因组性能恢复BOL', '实现正确'],
        ['模块8', 'test_annual_fixed_D: 年度内D不随月更新', '2017年1月-12月', 'D恒定', '月间D值相同'],
    ],
    col_widths=[0.8, 2.0, 1.5, 1.8, 1.8]
)

# 集成测试
doc.add_heading('集成测试清单', level=2)
add_table(
    ['测试用例', '模块组合', '测试场景', '预期结果', '通过标准'],
    [
        ['test_closed_loop', '模块2+4+5', '1天BOL场景15min闭环运行', '功率平衡+SOC/H₂在范围内', '守恒残差≤10⁻⁶+无越界'],
        ['test_degradation_accum', '模块2+3+4', '7天D_BAT=0.50场景', 'BAT损伤累计正确', 'D_BAT随时间单调增长'],
        ['test_counterfactual_pair', '模块2+3+4+5+6', 'Run1(BOL)+Run2(BAT D=0.50)配对', 'AE(α₁)>0且AE(β₁)>0', 'AE方向正确+NC1≤10⁻⁴'],
        ['test_mediation_full', '模块6+7', '1有效路径×6锚点×16联盟', '16联盟值+φ_m+闭合', '闭合≤5%+物理可解释'],
        ['test_v1_screening', '模块2-6', '1配置×1窗口×7健康=7次', '7次运行+6路径AE/TE', '路径AE提取正确'],
        ['test_v2_mediation', '模块6+7', '1有效路径×6锚点×16联盟=96次', 'Shapley值+交互指数', '闭合≤5%+交互分级'],
        ['test_v3_bootstrap', '模块6+工具A', '10组LHS×1配对×2=20次', 'AE/TE Bootstrap区间', '方向一致率≥90%'],
        ['test_v4_annual', '模块2-6+8', '1配置×1年×7健康=7次年度', '年度路径方向', '方向与短窗口一致'],
        ['test_bias_x', '模块3+6+8', 'M1(独立)vs M2(耦合)年度', 'Bias_X量化', 'Bias_X=(X_M1−X_M2)/max(|X_M2|,10⁻⁶)'],
    ],
    col_widths=[1.5, 1.5, 1.8, 1.8, 1.8]
)

# NC控制试验
doc.add_heading('NC控制试验规格', level=2)
add_para('三类负对照控制试验是方法核心的物理验证,必须与Shapley数学反事实互补。', size=10.5)
add_table(
    ['试验', '原理', '操作', '预期', '通过标准', '未通过处理'],
    [
        ['NC1 健康映射旁路', 'D_i设定但g_i(D_i)=g_i(0),性能恢复BOL,损伤应≈0',
         '运行1次7天BOL场景,在代码中强制g_i(D_i)=g_i(0)',
         'ΔD_j残差≈0(10⁻⁷~10⁻⁵量级)',
         '残差≤10⁻⁴ EOL/年',
         '停止查代码泄漏;float128/Kahan;>10⁻⁴查非预期健康映射泄漏'],
        ['NC2 命令回放', '观测运行的EMS命令在反事实中回放,量化EMS介导份额',
         '保存观测EMS命令→反事实运行中回放相同命令',
         'AE_NC2 < AE_full(部分效应通过EMS介导)',
         'AE_NC2/AE_full∈[0,1]',
         '若AE_NC2>AE_full→检查命令回放完整性'],
        ['NC3 目标损伤旁路', '目标设备损伤累计旁路,检查累计效应',
         '在反事实运行中跳过目标设备损伤累计',
         'AE_NC3≈0(损伤累计被旁路)',
         '残差≤10⁻⁴ EOL/年',
         '若AE_NC3>0→检查损伤累计模型是否有旁路泄漏'],
    ],
    col_widths=[1.0, 1.8, 1.8, 1.5, 1.2, 1.8]
)
add_verify('NC1在第1周预测试(性能基准同步)且在第5周正式执行; NC2/NC3在第5周反事实引擎完成后执行。三类试验均通过后方可进入P5路径筛选。')

# 回归测试
doc.add_heading('回归测试清单', level=2)
add_table(
    ['测试场景', '触发时机', '测试内容', '通过标准'],
    [
        ['BOL守恒回归', '每次代码修改后', '1天BOL场景功率/氢守恒', '残差≤10⁻⁶'],
        ['配对哈希回归', '每次EMS/配置修改后', '观测vs反事实字段哈希', '完全一致'],
        ['NC1残差回归', '每次health_performance修改后', 'g_i(D_i)=g_i(0)残差', '≤10⁻⁴ EOL/年'],
        ['Shapley闭合回归', '每次mediation修改后', 'φ_1+φ_2+φ_3+φ_4=总效应', '闭合≤5%'],
        ['性能回归', '每次核心模块修改后', '20场景7天运行时间', '中位≤10s+P95≤15s'],
    ],
    col_widths=[1.5, 1.8, 2.2, 1.8]
)

# 测试数据准备
doc.add_heading('测试数据准备', level=2)
add_table(
    ['测试类型', '所需数据', '数据来源', '准备时机', '备注'],
    [
        ['单元测试', '280Ah 16时点容量/HPPC; IEEE PHM FC1/FC2; BLAST-Lite Sony/Murata', '数据文献台账A级数据', 'P1第1周', '使用已有A级数据子集'],
        ['集成测试', 'UCSD TradeStreet 15min数据(1天+7天)', '02_data_processed/tradestreet_pipeline/', 'P1第1周', '76CSV中选取W1-W4代表日'],
        ['V1路径筛选', 'UCSD 4窗口×7天(低PV/高PV/高爬坡/典型)', '同上,算法选取互不重叠窗口', 'P5第6周', '窗口选取算法在P1验证'],
        ['V4年度验证', 'UCSD 2017+2018全年15min数据', '同上', 'P6第9-10周', '年度数据完整性在P1确认'],
        ['NC1预测试', '1次7天BOL场景数据', '同上', 'P1第1周', '与性能基准同步执行'],
        ['辅机端点测试', 'γ₁/β₂/α₂/γ₂路径+η_aux低/中/高', 'P4反事实引擎输出', 'P6第4-5周', '风险3缓解措施'],
        ['应力测试', 'D=0.90+C4+低氢储备(H₂_min=5%)', 'frozen_parameters_v2.2.json修改', 'P5第6周', '风险5缓解措施'],
    ],
    col_widths=[1.2, 2.0, 1.8, 1.0, 1.5]
)
add_verify('UCSD 76CSV覆盖2016-2018年15min数据完整性在P1第1周确认; 4窗口选取算法在P1验证后不可修改; 年度数据缺失段在P1标注并评估影响。')

# 预期结果与通过标准汇总
doc.add_heading('预期结果与通过标准汇总', level=2)
add_table(
    ['指标', '预期值', '通过标准', '对应CP', '未通过影响'],
    [
        ['功率守恒残差', '~10⁻⁷额定功率', '≤额定×10⁻⁶', 'CP5', '不进入P4,查代码'],
        ['氢守恒残差', '~10⁻⁷吞吐', '≤吞吐×10⁻⁶', 'CP5', '同上'],
        ['NC1残差', '10⁻⁷~10⁻⁵ EOL/年', '≤10⁻⁴ EOL/年', 'CP3+CP6', 'float128/Kahan或查泄漏'],
        ['BAT方向单调', '≥99%时点', '≥98%+连续≤3', 'CP4', '查数据/拟合'],
        ['同源NRMSE', '<5%', '≤10%', 'CP4', '重新拟合或降级'],
        ['Shapley闭合', '解析值≈0', '≤5%(建议0.1%)', 'CP9', '查联盟值噪声'],
        ['AE方向一致率', '≥95%', 'Bootstrap同号≥90%', 'CP8', '降级为条件路径'],
        ['年度方向复现', '2017=2018方向', '相同+至少一年95%不跨0', 'CP10', '分别报告两年'],
        ['性能(中位)', '~5-8s', '≤10s', 'CP3', '缓存/向量化'],
        ['性能(P95)', '~10-13s', '≤15s', 'CP3', '同上'],
    ],
    col_widths=[1.5, 1.5, 1.8, 0.8, 1.8]
)

# 保存
output_path = r'D:\matlab\C\09_docs\05_阶段产出物/01_W1实施计划.docx'
doc.save(output_path)
print(f'Saved: {output_path}')
print(f'Paragraphs: {len(doc.paragraphs)}, Tables: {len(doc.tables)}')
