"""项目数据来源与可信度台账 - 内容定义。"""
from docx.shared import RGBColor

INTRO_PARAS = [
    {'text': '本台账按设备/系统分类，逐项记录当前已获取并核验的全部数据来源。每项数据标注：①来源（DOI、文献名、作者、年份）便于论文引用；②数据内容性质（原始数据集 / 机理模型 / 额定点 / 综述范围 / 目标值）；③在本项目中的具体用途；④可信度等级及限定原因。所有数据均已在 base data/ 目录下的 66–75 号审计台账中完成逐点核验。'},
    {'text': '可信度评级标准：', 'bold': True, 'size': 10.5},
    {'text': '• 高：公开原始数据集或厂家官方数据表，化学体系/边界/口径与项目匹配，可直接用于参数标定或引用。', 'size': 10},
    {'text': '• 中（条件通过）：数据/模型结构可用，但对象尺度、边界或工况与项目不完全匹配；可用于模型结构、情景范围或敏感性，不可直接作为项目设备绝对参数。', 'size': 10},
    {'text': '• 低（仅参考）：对象与项目口径差异显著，或文件命名与正文不符；仅作背景/方法参考，不得进入参数链。', 'size': 10},
    {'text': '• 不可用/缺口：经核验与项目需求不符或文件对象错误，或尚无任何公开资料满足；拒绝进入证据链。', 'size': 10},
    {'text': '重要声明：', 'bold': True, 'size': 10.5, 'color': RGBColor(0xC0, 0x00, 0x00)},
    {'text': '本项目定位为"基于公开数据、文献机理模型与显式不确定性情景的离网 PV–H₂–LFP 微电网容量优化与退化感知能量管理研究"。所有非实测设备参数须标注证据等级；关键缺口须以区间情景而非单点假设处理；结果不得表述为真实站点长期统计、厂家保证寿命、工程定型成本或采购报价。', 'size': 10},
]

from docx.shared import RGBColor

SECTIONS = [
    # ========== 二、PV-建筑负荷 ==========
    {
        'title': '二、PV–建筑负荷数据（Trade Street 站点）',
        'page_break_after': False,
        'sub': [
            {
                'subtitle': '2.1 原始公开数据集',
                'rows': [
                    {
                        'source': 'Silwal et al., "Open-source multi-year power generation, consumption, and storage data from a commercial building microgrid", J. Renewable Sustainable Energy 13(2):025301, 2021.\nDOI: 10.1063/5.0038650\n数据仓库: https://github.com/sushilsilwal3/UCSD-Microgrid-Database',
                        'content': '原始数据集（CSV）。含 UCSD Trade Street 离网校区微电网的 PV 发电、建筑总负荷、BESS 功率多年 15 min 序列；附带 PythonPVGenerator.py 清洗脚本。时间按 America/Los_Angeles 本地民用时，秋季 DST 回拨 fold=0。',
                        'purpose': '项目原始数据源；构建 15 min 离网 EMS 与容量优化的 PV/负荷输入；逐点审计与清洗的基线。',
                        'credibility': '中',
                        'credibility_reason': '数据公开且可追溯，但经逐点审计确认公开包不存在三个完整同期原生 15 min 民用年（PV、Total、BESS 同时连续覆盖）。因此只能用于算法、约束与敏感性研究，不能用于正式 20 年工程统计结论。'
                    }
                ]
            },
            {
                'subtitle': '2.2 合成重构评价层（项目派生）',
                'rows': [
                    {
                        'source': '本项目构建（base data/62–65 号文件）。\n构建脚本: build_trade_street_synthetic_reconstructed_pv_load_years.py, build_trade_street_synthetic_building_load_evaluation_years.py\n方法: 同月、同类日、同刻供体重建；逐点保留供体来源标签。',
                        'content': '派生数据集（CSV）。2016–2018 共 105,216 个 15 min 点的合成 PV—建筑负荷评价层；建筑负荷含同步闭合值和供体重建值；PV 逐点保留来源。BESS 历史未伪造，作为未来 EMS 控制变量。',
                        'purpose': 'Phase A 算法验证、容量优化、约束与敏感性研究的评价序列。',
                        'credibility': '中',
                        'credibility_reason': '具有逐点供体溯源，透明可复现；但本质为合成重构，非原生观测。必须标注为"合成评价层"，禁止表述为真实站点多年统计。'
                    }
                ]
            },
        ]
    },
    # ========== 三、LFP 电池 ==========
    {
        'title': '三、LFP 电池数据',
        'page_break_after': False,
        'sub': [
            {
                'subtitle': '3.1 LFP 日历老化（已通过 Phase A 准入）',
                'rows': [
                    {
                        'source': 'Naumann et al., "Analysis and modeling of calendar aging of a LiFePO4/graphite cell", Journal of Energy Storage 17 (2018) 153–169.\nDOI: 10.1016/j.est.2018.01.019\n配套数据: DischargeCapacity.xlsx, ResistanceR_DC10s.xlsx, ElectrochemicalImpedanceSpectroscopy.xlsx, DifferentialVoltageAnalysis.xlsx',
                        'content': '公开原始数据集 + 机理模型。对象：Sony/Murata US26650FTC1 商业 3 Ah 26650 LiFePO4/石墨电芯。17 个恒定温度-SOC 静置点（0/10/25/40/60°C × 0–100% SOC），存储时长 0–21241 h（885 d），每点 3 只电芯。含放电容量、DC 内阻、EIS、差分电压分析。论文另有 15 个动态温度/SOC 验证点，容量损失模型误差 <2.2%。',
                        'purpose': 'LFP 日历老化模型标定；拟合容量损失与时间、温度、SOC 关系；构建项目温度与平均 SOC 的保守/基准/乐观情景。',
                        'credibility': '高',
                        'credibility_reason': '化学体系明确为 LFP/石墨，机器可读原始数据完整，温度-SOC-时间-容量字段齐全，含独立动态验证。唯一限定：电芯型号与候选 EverCore 整包不同，须保留电芯标签并做 pack_to_cell 不确定性乘子。'
                    }
                ]
            },
            {
                'subtitle': '3.2 LFP 循环老化（已通过 Phase A 准入）',
                'rows': [
                    {
                        'source': 'Severson et al., "Data-driven prediction of battery cycle life before capacity degradation", Nature Energy 4 (2019) 383–391.\nDOI: 10.1038/s41560-019-0356-8\n数据仓库: https://data.matr.io/1/',
                        'content': '公开原始数据集。对象：A123 APR18650M1A 商业 LFP/石墨 18650 电芯，124 只。30°C；72 种快充策略；0–80% SOC 快充、80–100% 1C CC-CV；4C 放电至 2.0 V；EOL=80% 标称容量；约 96700 循环。原始循环、容量、充电协议数据公开。',
                        'purpose': 'LFP 循环老化/RUL 数据处理；快充 C-rate 敏感性；SOH=80% 循环寿命与算法验证；循环损伤项标定。',
                        'credibility': '高',
                        'credibility_reason': '商业 LFP/石墨电芯，公开原始数据，循环协议与容量记录完整。限定：电芯型号与 Naumann 日历数据不同，两者须分别标定，不得拼接为同一电芯联合记录；C-rate 范围偏高，需对项目 0.25–0.45P 工况做外推风险敏感性。'
                    },
                    {
                        'source': 'Wang et al., "Cycle-life model for graphite-LiFePO4 cells", Journal of Power Sources 196 (2011) 3942–3948.\nDOI: 10.1016/j.jpowsour.2010.11.134',
                        'content': '论文模型 + 图表曲线。对象：A123 2.2 Ah 26650 LiFePO4/碳负极。温度 -30/0/15/25/45/60°C；DOD 10/20/50/80/90%；放电 C/2/2C/6C/10C。容量损失模型 Qloss=f(time, T, DOD, rate)，以 Ah 吞吐量/Arrhenius/幂律表述。',
                        'purpose': 'LFP 循环老化模型结构与试验工况范围交叉核验；图表数字化比对。',
                        'credibility': '中',
                        'credibility_reason': '模型结构与参数范围有参考价值，但非机器可读原始数据；电芯为 26650 且含高倍率工况。仅作模型形式与参数范围交叉核验，不得当作开放原始数据集。'
                    }
                ]
            },
            {
                'subtitle': '3.3 LFP 电芯效率（仅参考）',
                'rows': [
                    {
                        'source': 'LFP 电芯效率图文献.pdf（用户提供，文件未含明确 DOI 题名）',
                        'content': '论文图表。LFP/石墨电芯充放电能量效率与 C-rate 关系；结论：低于约 0.5C 时充放电能量效率均可超过 99%。',
                        'purpose': 'LFP 电芯级效率与"优先低倍率、避免高倍率"策略的设计依据。',
                        'credibility': '低',
                        'credibility_reason': '支持电芯级效率趋势，但不含 BMS/PCS/热管理/SOC 窗口/老化/辅助负荷，不能把 >99% 写成整包往返效率。仅作电芯层效率情景。'
                    }
                ]
            },
            {
                'subtitle': '3.4 候选 BESS 整包设备',
                'rows': [
                    {
                        'source': 'SolisStorage EverCore 数据表 V1.6（2026-02-10，用户提供）',
                        'content': '厂家数据表（额定点）。LFP；额定/可用容量 100.5/99.3 kWh；连续功率 30 kW 或 35 kW（按型号）；89% 往返效率（25±2°C、0.5P 充放电、220/230 Vac）；8000 循环至 SOH=70%（25±2°C、0.5P）。',
                        'purpose': 'BESS 容量、功率、往返效率点与循环寿命证据；Phase A 显式条件参数。',
                        'credibility': '中',
                        'credibility_reason': '厂家官方数据表，容量/功率匹配项目搜索区间。但 89% 仅在特定 AC 电压/温度/0.5P 条件下成立；8000 循环至 SOH=70% 不能直接推导项目 SOH=80%。仍缺 SOC 上下限、效率图、BMS/PCS 分项损耗、吞吐量质保、价格。'
                    },
                    {
                        'source': 'Huawei LUNA2000-97KWH-1H1 官方数据表 + C&I ESS 质保政策 Issue 04（2025-01-17）',
                        'content': '厂家数据表（额定点）。LFP；最大系统容量 96.8 kWh；最大充/放电功率 ≤100/92 kW（配 LUNA2000-100KTL-M1 PCS）；Smart Rack Controller 最大效率 ≥98.5%；运行温度 -30~55°C。',
                        'purpose': 'LFP 容量/温度/架构参考；条件性 30–45 kW 限功率运行候选。',
                        'credibility': '低',
                        'credibility_reason': '额定功率超出项目 25–45 kW 搜索区间；≥98.5% 为 Smart Rack Controller 最大效率，非整包往返效率；需华为书面确认 25–45 kW 连续运行、效率、SOC、质保。仅作参考。'
                    },
                    {
                        'source': 'EPC Energy E30100（CEC 太阳能设备清单检索结果）',
                        'content': '认证清单条目。100 kWh；30 kW；480 Vac；LFP；含 135 kW PV 输入。',
                        'purpose': '候选设备识别记录。',
                        'credibility': '低',
                        'credibility_reason': '仅为认证清单条目，非厂家完整数据表；缺可用能量定义、连续/峰值时长、SOC 限制、效率图、BMS/热限制、质保、退化数据、价格。仅作候选识别。'
                    },
                    {
                        'source': 'Zhou et al., "Online energy management optimization of hybrid energy storage microgrid with reversible solid oxide cell", Journal of Cleaner Production 414 (2023) 138663.\nDOI: 10.1016/j.jclepro.2023.138663',
                        'content': '论文模型。240 kWh、最大 10 kW 锂离子 BESS 与 rSOC 微电网；化学体系未明确为 LFP。日历+循环老化分解结构 A_BESS=A_cal+A_cyc；示例效率 90%、SOC 0.2–0.8。',
                        'purpose': '日历+循环损伤分解、SOC 状态更新、退化与 EMS 目标耦合的实现参考。',
                        'credibility': '中',
                        'credibility_reason': '提供模型形式参考，但对象为 rSOC 系统且化学体系未证实 LFP；参数与候选 BESS 功率容量不匹配。仅作模型形式，系数须由 Naumann+Severson 标定。'
                    }
                ]
            },
        ]
    },
    # ========== 四、PEMEL ==========
    {
        'title': '四、PEMEL（质子交换膜电解槽）数据',
        'page_break_after': False,
        'sub': [
            {
                'subtitle': '4.1 PEMEL 电化学模型（条件通过）',
                'rows': [
                    {
                        'source': 'Brezak, Kovač, Firak, "MATLAB/Simulink simulation of low-pressure PEM electrolyzer stack", International Journal of Hydrogen Energy 48 (2023) 17001–17015.\nDOI: 10.1016/j.ijhydene.2022.03.092',
                        'content': '论文机理模型（Simulink）。低压 PEMEL 电化学/系统模型：U=U_rev+U_pressure+U_activation+U_diffusion+U_ohmic；Faraday 常数 96485 C/mol；输入温度与功率，输出单池电压、堆电流、氢氧水流量、储罐压力；含泵、分离器、储罐与简化电子控制。PEMEL 公开效率范围约 67–82%。',
                        'purpose': '建立 PEMEL 电化学子模型；温度/压力/电流密度/产氢关系；Faraday 产氢计算；30 bar 质量库存接口。',
                        'credibility': '中',
                        'credibility_reason': '电化学骨架完整可用，但为低压、小功率、简化 BOP 模型，部分参数为假设；67–82% 效率仅作初始区间。不可直接标定 Nel H4 或作为其分负荷/待机/动态实测曲线。效率口径须统一 HHV/LHV。'
                    }
                ]
            },
            {
                'subtitle': '4.2 PEMEL 间歇运行退化（条件通过）',
                'rows': [
                    {
                        'source': 'Weiß et al., "Impact of Intermittent Operation on Lifetime and Performance of a PEM Water Electrolyzer", Journal of The Electrochemical Society 166 (2019) F1537.\nDOI: 10.1149/2.0421908jes  (CC BY 4.0)',
                        'content': '公开实验数据 + 机理。PEM 水电解单电池 AST；高 3 A/cm²、低 0.1 A/cm² 和 OCV 空闲交替。OCV-AST 中 718 次循环后 HFR 增至 1.6 倍；OCV 循环引起显著性能恶化；对照试验以 1.3 V 保持替代 OCV 时未观察到同类恶化。',
                        'purpose': '将 PEMEL 运行/低负荷待机/OCV 停机状态拆分；记录 OCV 次数和时长；EMS 中惩罚频繁 OCV 并比较保护性待机策略。',
                        'credibility': '中',
                        'credibility_reason': '机理明确且 CC BY 4.0 开放，但为 5 cm² 量级单电池、80°C、10 bar/1 bar 加速试验。不得将 718 循环、1.6 倍 HFR 或单电池电压变化线性换算为 40 kW 系统小时退化率、启动时间或寿命。'
                    }
                ]
            },
            {
                'subtitle': '4.3 PEMEL 综述与目标（范围参考）',
                'rows': [
                    {
                        'source': 'Buttler, Spliethoff, "Current status of water electrolysis for energy storage, grid balancing and sector coupling", Renewable and Sustainable Energy Reviews 82 (2018) 2440–2454.\nDOI: 10.1016/j.rser.2017.09.003',
                        'content': '综述。PEM/AEL/SOEL 市场、项目报告与文献汇总。PEMEL 典型温度 60–90°C；商业 PEM 系统约 5.0–6.5 kWh/Nm³（含整流/公用工程，外压缩不含）；FCH JU 柔性运行退化范围 5.4–10.7 µV/h；部分 KPI 氢输出已按 30 bar 修正。',
                        'purpose': '对 Nel H4 额定 7.0 kWh/Nm³ 及 30 bar 边界做合理性检查；建立 PEMEL 效率/整流损耗/柔性退化的保守-基准-乐观范围。',
                        'credibility': '中',
                        'credibility_reason': '综述范围有参考价值，但不同数据规模/压力/LHV·HHV/含不含 BOP/年份不同。不得把 MW/GW 成本、寿命、启停时间或 5.4–10.7 µV/h 直接当作 40 kW Nel H4 实测参数。'
                    },
                    {
                        'source': 'Carmo et al., "A comprehensive review on PEM water electrolysis", International Journal of Hydrogen Energy 38 (2013) 4901–4934.\nDOI: 10.1016/j.ijhydene.2013.01.151',
                        'content': '综述。PEM 水电解材料、膜、电极、极化与建模机理；电化学反应、极化/扩散/欧姆损失与温度、压力、电流密度、膜和交叉渗透的机理。',
                        'purpose': '核对 PEMEL 极化模型结构、温度/压力约束和高压运行的物理机制；支持高压 PEMEL 及安全边界分析。',
                        'credibility': '中',
                        'credibility_reason': '机理权威但文献年代较早（2013），非特定商用设备性能测试。不得将 2013 年综述的效率/成本/寿命/材料参数直接作为 2026 年 Nel H4 系统规格。'
                    },
                    {
                        'source': 'DOE PEM Electrolyzer Technical Targets（用户提供图片）\n来源: https://www.energy.gov/cmei/fuels/technical-targets-proton-exchange-membrane-electrolysis',
                        'content': '官方技术目标图。分 stack 与 system 两层。2022 现状：堆级 51 kWh/kgH₂(LHV,65%)、平均退化 4.8 mV/kh 或 0.25%/1000h、寿命 40000 h、资本成本 450 美元/kW；系统级 55 kWh/kgH₂(LHV,61%)、未安装资本成本 1000 美元/kW。2026 与 ultimate 列为目标值。',
                        'purpose': '公开系统/堆级能耗、寿命、退化、成本的合理性区间锚点；明确 LHV 和堆级/系统级边界；2026 目标仅做乐观敏感性。',
                        'credibility': '中',
                        'credibility_reason': '官方公开目标，但目标值非现状实测；堆级 51 与系统级 55 不可混用；美元/kW 不可直接用于项目正式 NPC。须明确 LHV/HHV 与价格年。'
                    }
                ]
            },
            {
                'subtitle': '4.4 候选 PEMEL 设备 Nel H4',
                'rows': [
                    {
                        'source': 'Nel Hydrogen H-Series 数据表 PD-0600-0062 Rev L（用户提供）\n产品页: https://nelhydrogen.com/',
                        'content': '厂家数据表（额定点）。PEM；30 barg 可选交付压力；额定产氢 4 Nm³/h、8.63 kg/24 h；BOL 比能耗 7.0 kWh/Nm³；最大所需电气视在功率 38 kVA；380/400/415 Vac 50 Hz 或 480 Vac 60 Hz 三相；0–100% 净交付调节；自动远程启停；最大产氢时水耗 3.66 L/h；ASTM Type II 去离子水。',
                        'purpose': 'PEMEL 额定产氢、AC 视在功率上限、BOL 比能耗点、调节范围、水需求与 30 barg 边界的 Phase A 候选情景参数。',
                        'credibility': '中',
                        'credibility_reason': '厂家官方数据表，额定点与项目边界匹配。但 38 kVA 为视在功率，非有功 kW 曲线；7.0 kWh/Nm³ 的 LHV/HHV 基准未明确；缺分负荷 AC 有功-产氢曲线、功率因数、待机、启停/爬坡、寿命、退化系数、价格。须显式加入 AC-DC 整流边界。'
                    }
                ]
            },
        ]
    },
]
