# -*- coding: utf-8 -*-
"""
M3 closed_loop_lite — MVP闭环退化耦合模块
功能：实现alpha1(BAT->FC)和beta1(BAT->EL)两条耦合路径的闭环退化仿真
依赖：M2 hp_bat_lite
输入：hp_bat_map, 联盟状态编码(4位二进制)
输出：closed_loop_result (各设备逐时损伤增量 dD_j per step)
"""
import numpy as np
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

from components import efc_cycle_model
from components.efc_cycle_model import EFC_A_D, EFC_WOHLER_K, EFC_DOD_REF


# === Degradation rate constants (per-hour time units) ===
# All time-based coefficients are expressed in per-hour units.
# Per-step damage = coefficient * dt_h, making results independent of step size.

# BAT calendar degradation (LFP 280Ah, 25°C, SoC≈0.5)
# Literature: Naumann 2018/2022 → 2–5%/year. Calibrated to 5%/year (upper bound).
# Previous MVP value: 2e-6/step → 7.0%/year (slightly above literature).
BAT_K_CAL_H = 0.05 / 8760.0  # ≈ 5.708e-6 per hour → 5.0%/year

# BAT cyclic degradation coefficient (dimensionless per unit DOD×power_ratio×hour)
# Preserved from MVP as the legacy "running-hour proxy"; replaced by the EFC
# / energy-throughput model (BAT_CYCLE_MODEL='efc') calibrated to the 280Ah
# aging data. The proxy is kept for backward compatibility and comparison.
BAT_K_CYC_H = 5e-4 * 4.0  # = 2e-3 per hour (was 5e-4 per 15-min step)

# EFC / energy-throughput cycle-damage model (default):
#   dD_cyc = a_d * count_i * (range_i / DOD_ref)**k   (rainflow EFC, k=1 linear)
#   a_d = 5.0714e-4 D per EFC, calibrated to 280Ah LFP (700 cycles -> D=0.355).
BAT_CYCLE_MODEL = "efc"           # 'efc' | 'proxy'
EFC_CYCLE_A_D = EFC_A_D           # D per equivalent full cycle
EFC_CYCLE_K = EFC_WOHLER_K        # Woehler exponent (1.0 = energy-throughput)
EFC_CYCLE_DOD_REF = EFC_DOD_REF   # reference DOD for EFC definition

# FC running degradation (PEMFC, system-level)
# Literature: 5–20 μV/h/cell steady-state voltage decay.
# Calibrated for ~10-year lifetime at 500 h/year operation:
#   annual_damage = k_run_h * p_ratio * hours + k_start * n_starts ≈ 0.1
#   → k_run_h * 0.5 * 500 ≈ 0.06 → k_run_h ≈ 2.4e-4
# Previous MVP value: 3e-4/step = 1.2e-3/h (10× too aggressive).
FC_K_RUN_H = 2.4e-4  # per hour at rated power

# FC start-stop degradation (per event, independent of step duration)
# Literature: 10–100 μV per start-stop/cell. System-level D-increment:
#   50 μV/cell / 4.0 V × 5 cells → ~6.25e-5 per event (voltage only).
#   Including membrane/catalyst effects: ~2–5× voltage-only → ~1.5–3e-4.
# Calibrated: 2e-4/event → 200 starts/year × 2e-4 = 0.04/year.
# Previous MVP value: 1e-3/event (5× too aggressive).
FC_K_START = 2.0e-4  # per start-stop event

# EL running degradation (PEMEL, system-level)
# Literature: 10–30 μV/h/cell steady-state voltage increase.
# Calibrated for ~10-year lifetime at 600 h/year operation:
#   k_run_h * 0.5 * 600 ≈ 0.06 → k_run_h ≈ 2e-4
# Previous MVP value: 2e-4/step = 8e-4/h (4× too aggressive).
EL_K_RUN_H = 2.0e-4  # per hour at rated power

# EL start-stop degradation (per event)
# Literature: 20–100 μV per start-stop/cell. System-level:
#   ~1.5–3e-4 per event including membrane/catalyst effects.
# Calibrated: 1.6e-4/event → 250 starts/year × 1.6e-4 = 0.04/year.
# Previous MVP value: 8e-4/event (5× too aggressive).
EL_K_START = 1.6e-4  # per start-stop event


# 4个归因组的二进制编码: b3b2b1b0 -> C-E-P-I
# 1=ON(正常退化映射), 0=OFF(g_i(0)=BOL基准)
MEDIATOR_GROUPS = ['C', 'E', 'P', 'I']  # 容量/效率/功率边界/库存控制


@dataclass
class CoalitionState:
    """联盟状态编码"""
    capacity_on: bool      # C组: 容量缩放
    efficiency_on: bool    # E组: 效率退化
    power_on: bool         # P组: 功率边界
    inventory_on: bool     # I组: EMS策略

    @property
    def code(self) -> str:
        return f"{int(self.capacity_on)}{int(self.efficiency_on)}{int(self.power_on)}{int(self.inventory_on)}"

    @property
    def code_int(self) -> int:
        return int(self.code, 2)

    @classmethod
    def from_code(cls, code: str):
        assert len(code) == 4
        return cls(
            capacity_on=code[0] == '1',
            efficiency_on=code[1] == '1',
            power_on=code[2] == '1',
            inventory_on=code[3] == '1',
        )

    @staticmethod
    def all_16_states() -> List['CoalitionState']:
        """生成全部16个联盟状态 (2^4, 4个归因组ON/OFF组合)。"""
        return [CoalitionState.from_code(format(i, '04b')) for i in range(16)]


def compute_damage(hp_func, ems_commands: np.ndarray,
                   config: Dict[str, Any], dt_min: float = 15.0,
                   D_state: Dict[str, float] = None,
                   coalition: CoalitionState = None,
                   eta_degrade: float = 0.10,
                   eol_policy: str = 'cap') -> Dict[str, np.ndarray]:
    """
    计算单步损伤增量 dD/dt。

    Args:
        hp_func: 健康-性能映射函数 D -> (Q, R0, P_max)
        ems_commands: EMS命令序列 (N_steps, 3) [P_BAT, P_FC, P_EL] in kW
        config: C1配置参数 (含 capacity/battery_constraints/ems_parameters)
        dt_min: 时间步长(分钟), 默认15
        D_state: 各设备当前退化状态 {'bat': D_bat, 'fc': D_fc, 'el': D_el}
                 默认None = 全BOL(D=0)
        coalition: 联盟状态(可选), 控制哪些退化映射被旁路(OFF=g_i(0))
                   None = 全ON (完整耦合)
        eta_degrade: FC/EL效率退化斜率 (默认0.10, 即EOL时效率降10%)
                     eta_factor = 1.0 + eta_degrade * D
                     由VS7不确定性分析通过LHS采样传入
        eol_policy: ``'cap'`` 保持原行为并在D=1处截断；``'raw'`` 返回
                    未截断损伤，供逐步更换事件模型使用。

    Returns:
        dD_dict: {
            'dD_bat': (N_steps,) BAT损伤增量,
            'dD_fc':  (N_steps,) FC损伤增量,
            'dD_el':  (N_steps,) EL损伤增量,
        }

    退化模型 (MVP简化版, 线性最小信息假设):
        BAT: dD/dt = k_cal + k_cyc * DOD * |P/P_max(D_bat)|
            - 日历退化 k_cal = 2e-5/step (25°C, 固定)
            - 循环退化 k_cyc = 5e-4 * DOD * |P/P_max| (DOD越深退化越快)
            - 容量缩减效应: Q(D_bat) -> effective_capacity = BAT_kWh * Q
            - 数据源: 280Ah aging 15时点外推, BLAST-Lite 250Ah交叉验证
        FC:  dD/dt = k_run * |P/P_rated| * eta_factor(D_fc) + k_start * start_stop
            - 运行退化 k_run = 3e-4/step (满功率, BOL效率)
            - 效率退化因子: eta_factor = 1 + eta_degrade*D (效率降低eta_degrade*100%->应力增加同比例)
            - 启停惩罚 k_start = 1e-3/次 (每次启停0.1%D)
            - EOL: 额定电压下降10%, 线性外推 (IEEE PHM 2014, R2-T04)
        EL:  dD/dt = k_run * |P/P_rated| * eta_factor(D_el) + k_start * start_stop
            - 运行退化 k_run = 2e-4/step (满功率, BOL效率)
            - 效率退化因子: eta_factor = 1 + eta_degrade*D (比能耗增加eta_degrade*100%->应力增加)
            - 启停惩罚 k_start = 8e-4/次 (每次启停0.08%D)
            - EOL: 比能耗增加10%, 线性外推 (NAICNO数字孪生)

    联盟OFF旁路逻辑 (coalition非None时):
        C_OFF: Q(D_bat) -> Q(0)=1.0 (容量不缩减, effective_capacity=BAT_kWh)
        E_OFF: eta_factor(D_fc/D_el) -> eta_factor(0)=1.0 (效率不退化)
        P_OFF: P_max(D_bat) -> P_max(0)=rated (功率上限不缩减)
        I_OFF: 由apply_coupling层处理 (EMS策略调整, 非compute_damage层)
    """
    cmds = np.atleast_2d(ems_commands)
    n_steps = cmds.shape[0]
    dt_h = dt_min / 60.0  # 小时

    # 各设备当前退化状态
    if D_state is None:
        D_state = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    D_bat = float(np.clip(D_state.get('bat', 0.0), 0.0, 1.0))
    D_fc = float(np.clip(D_state.get('fc', 0.0), 0.0, 1.0))
    D_el = float(np.clip(D_state.get('el', 0.0), 0.0, 1.0))

    # 解析EMS命令
    p_bat = cmds[:, 0]  # BAT功率 (+放电, -充电) kW
    p_fc = cmds[:, 1]   # FC功率 (>=0) kW
    p_el = cmds[:, 2]   # EL功率 (>=0, 电解耗电) kW

    # 配置参数
    cap = config.get('capacity', {})
    bat_kwh = cap.get('BAT_kWh', 135.0)
    fc_rated = cap.get('PEMFC_kW', 37.5)
    el_rated = cap.get('PEMEL_kW', 35.0)

    bat_constraints = config.get('battery_constraints', {})
    soc_min = bat_constraints.get('soc_min', 0.15)
    soc_max = bat_constraints.get('soc_max', 0.95)
    soc_init = bat_constraints.get('soc_initial', 0.5)

    ems_params = config.get('ems_parameters', {})
    ss_threshold = ems_params.get('start_stop_threshold_fraction', 0.275)

    # 从hp_func获取BAT在当前D_bat下的性能参数
    q_frac_bat, r0_bat, p_max_bat = hp_func(D_bat)
    bat_rated_kw = bat_kwh * 0.5  # 0.5C额定功率

    # 联盟OFF旁路: C_OFF时Q不缩减, P_OFF时P_max不缩减
    if coalition is not None:
        if not coalition.capacity_on:
            q_frac_bat = 1.0  # C_OFF: 容量不缩减
        if not coalition.power_on:
            p_max_bat = bat_rated_kw  # P_OFF: 功率上限=额定

    # 实际可用功率上限 = P_max(D_bat)
    p_max_effective = min(p_max_bat, bat_rated_kw)

    # === 1. BAT损伤增量 ===
    # 追踪SoC变化以计算DOD
    # 容量缩减: Q(D_bat) < 1.0 时, 同功率下SoC变化幅度更大
    effective_capacity = bat_kwh * q_frac_bat  # 缩减后有效容量
    soc = np.zeros(n_steps + 1)
    soc[0] = soc_init
    delta_soc_arr = np.zeros(n_steps)  # 未clip的SoC变化量, 用于DOD计算
    for t in range(n_steps):
        # SoC变化 = P_bat * dt / Effective_Capacity
        # 正P_bat=放电(SoC降), 负P_bat=充电(SoC升)
        delta_soc = -p_bat[t] * dt_h / max(effective_capacity, 1e-6)
        delta_soc_arr[t] = delta_soc  # 保留未clip的变化量
        soc[t + 1] = np.clip(soc[t] + delta_soc, soc_min, soc_max)

    # DOD = 每步SoC变化的绝对值 (用未clip的delta_soc, 避免边界截断伪差)
    # 物理含义: 即使SoC触边界充不进/放不出, 循环应力仍由 attempted DOD 决定
    dod = np.abs(delta_soc_arr)
    # 功率比 (相对于有效最大功率)
    p_ratio_bat = np.abs(p_bat) / max(p_max_effective, 1e-6)

    # BAT退化: 日历 + 循环 (使用per-hour系数，乘以dt_h获得per-step损伤)
    # 文献依据: Naumann 2018/2022, LFP 280Ah, 25C, 日历退化2-5%/年
    # BAT_K_CAL_H ≈ 5.708e-6/h → 年日历退化=5.0%, 在文献2-5%的上限
    # 循环退化默认使用 EFC/能量吞吐模型 (BAT_CYCLE_MODEL='efc'):
    #   dD_cyc = a_d * (rainflow half-cycles with Woehler depth weighting)
    #   标定自 280Ah aging (700 满循环 -> D=0.355), 见 components/efc_cycle_model.py
    # 'proxy' 保留旧运行小时代理: dD_cyc = BAT_K_CYC_H * dod * p_ratio * dt_h
    bat_cycle_model = config.get("degradation", {}).get("bat_cycle_model", BAT_CYCLE_MODEL)
    if bat_cycle_model == "efc":
        efc_a_d = float(config.get("degradation", {}).get("efc_a_d", EFC_CYCLE_A_D))
        efc_k = float(config.get("degradation", {}).get("efc_wohler_k", EFC_CYCLE_K))
        efc_dod_ref = float(config.get("degradation", {}).get("efc_dod_ref", EFC_CYCLE_DOD_REF))
        # soc has n_steps+1 entries; cyclic damage lands on steps 0..n_steps-1
        dD_cyc_bat = efc_cycle_model.cycle_damage_increments(
            soc, a_d=efc_a_d, k=efc_k, dod_ref=efc_dod_ref)[:n_steps]
        dD_bat = BAT_K_CAL_H * dt_h + dD_cyc_bat
    else:
        # Legacy running-hour proxy (kept for comparison / regression)
        dD_bat = (BAT_K_CAL_H + BAT_K_CYC_H * dod * p_ratio_bat) * dt_h

    # === 2. FC损伤增量 ===
    p_ratio_fc = np.abs(p_fc) / max(fc_rated, 1e-6)
    # 启停检测: 功率从<阈值跳到>=阈值
    fc_active = p_fc >= (fc_rated * ss_threshold)
    fc_start_stop = np.zeros(n_steps)
    for t in range(1, n_steps):
        if fc_active[t] and not fc_active[t - 1]:
            fc_start_stop[t] = 1.0

    # FC效率退化因子: D_fc增加 -> 效率降低 -> 同功率下应力增加
    # E_OFF时效率不退化 (eta_factor=1.0)
    if coalition is not None and not coalition.efficiency_on:
        eta_factor_fc = 1.0  # E_OFF: 效率不退化
    else:
        eta_factor_fc = 1.0 + eta_degrade * D_fc  # EOL时效率降eta_degrade*100% -> 应力增同比例
    # FC运行退化: per-hour系数 × eta_factor × p_ratio × dt_h
    # FC启停退化: per-event系数 × start_stop_count
    dD_fc = FC_K_RUN_H * eta_factor_fc * p_ratio_fc * dt_h + FC_K_START * fc_start_stop

    # === 3. EL损伤增量 ===
    p_ratio_el = np.abs(p_el) / max(el_rated, 1e-6)
    el_active = p_el >= (el_rated * ss_threshold)
    el_start_stop = np.zeros(n_steps)
    for t in range(1, n_steps):
        if el_active[t] and not el_active[t - 1]:
            el_start_stop[t] = 1.0

    # EL效率退化因子: D_el增加 -> 比能耗增加 -> 同功率下应力增加
    # E_OFF时效率不退化 (eta_factor=1.0)
    if coalition is not None and not coalition.efficiency_on:
        eta_factor_el = 1.0  # E_OFF: 效率不退化
    else:
        eta_factor_el = 1.0 + eta_degrade * D_el  # EOL时比能耗增eta_degrade*100% -> 应力增同比例
    # EL运行退化: per-hour系数 × eta_factor × p_ratio × dt_h
    # EL启停退化: per-event系数 × start_stop_count
    dD_el = EL_K_RUN_H * eta_factor_el * p_ratio_el * dt_h + EL_K_START * el_start_stop

    if eol_policy not in {'cap', 'raw'}:
        raise ValueError("eol_policy must be 'cap' or 'raw'")

    dD_bat_raw_series = dD_bat.copy()
    dD_fc_raw_series = dD_fc.copy()
    dD_el_raw_series = dD_el.copy()

    # === EOL守卫 (修正, 2026-08-02) ===
    # 逐时间步执行: dD_used(t) = min[dD_raw(t), 1-D(t)]
    # 累积D_state在每步更新, 确保准确捕获EOL时刻
    if D_state is not None and eol_policy == 'cap':
        D_bat_cur = D_state.get('bat', 0.0)
        D_fc_cur = D_state.get('fc', 0.0)
        D_el_cur = D_state.get('el', 0.0)
        for t in range(len(dD_bat)):
            d_bat_rem = max(0.0, 1.0 - D_bat_cur)
            d_fc_rem = max(0.0, 1.0 - D_fc_cur)
            d_el_rem = max(0.0, 1.0 - D_el_cur)
            dD_bat[t] = min(dD_bat[t], d_bat_rem)
            dD_fc[t] = min(dD_fc[t], d_fc_rem)
            dD_el[t] = min(dD_el[t], d_el_rem)
            D_bat_cur += dD_bat[t]
            D_fc_cur += dD_fc[t]
            D_el_cur += dD_el[t]

    return {
        'dD_bat': dD_bat,
        'dD_fc': dD_fc,
        'dD_el': dD_el,
        'dD_bat_raw_series': dD_bat_raw_series,
        'dD_fc_raw_series': dD_fc_raw_series,
        'dD_el_raw_series': dD_el_raw_series,
        'dD_bat_raw': float(np.sum(dD_bat_raw_series)),
        'dD_fc_raw': float(np.sum(dD_fc_raw_series)),
        'dD_el_raw': float(np.sum(dD_el_raw_series)),
    }


def apply_coupling(hp_func, ems_commands: np.ndarray,
                   coalition: CoalitionState,
                   config: Dict[str, Any],
                   D_state: Dict[str, float] = None,
                   eta_degrade: float = 0.10) -> Dict[str, Any]:
    """
    应用联盟状态OFF -> g_i(0)替换, 计算闭环退化耦合结果。

    联盟OFF逻辑 (g_i(D) -> g_i(0), 即用BOL基准替换退化映射):
        C_OFF (容量): Q(D_bat) -> Q(0) = 1.0
            效果: BAT容量不缩减 -> SoC变化幅度不增加 -> dD_bat减小
            路径: BAT退化不通过容量缩减传递 (alpha1/beta1容量分支截断)
        E_OFF (效率): eta(D_fc/D_el) -> eta(0) = BOL效率
            效果: FC/EL效率不退化 -> 同功率下应力不增加 -> dD_fc/dD_el减小
            路径: 退化不通过效率补偿传递 (alpha1/beta1效率分支截断)
        P_OFF (功率): P_max(D_bat) -> P_max(0) = rated
            效果: BAT功率上限不收紧 -> EMS不需降额 -> dD_bat不变或减小
            路径: BAT退化不通过功率限制传递 (alpha1/beta1功率分支截断)
        I_OFF (库存/EMS): EMS策略 -> 固定基线(不根据D调整)
            效果: 移除退化保护 -> BAT多承担循环 -> dD_bat增加(!)
            路径: M4异向, AE可能<=0 (物理合理, 非bug)

    Args:
        hp_func: 健康-性能映射函数 (原D→(Q,R0,P_max))
        ems_commands: EMS命令序列 (N_steps, 3)
        coalition: 联盟状态
        config: C1配置参数
        D_state: 各设备当前退化状态 {'bat': D_bat, 'fc': D_fc, 'el': D_el}

    Returns:
        result: dict {
            'dD_bat': (N_steps,), 'dD_fc': (N_steps,), 'dD_el': (N_steps,),
            'coalition_code': str, 'off_groups': List[str],
            'total_damage': float,  # sum of all dD
        }
    """
    if D_state is None:
        D_state = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}

    # 直接调用compute_damage, 传入coalition参数控制退化映射旁路
    # C_OFF/E_OFF/P_OFF 在compute_damage内部处理
    # I_OFF 在这里处理 (EMS策略层, 非退化映射层)
    dD_dict = compute_damage(hp_func, ems_commands, config,
                             D_state=D_state, coalition=coalition,
                             eta_degrade=eta_degrade)

    # I组OFF: 移除EMS退化保护
    # 物理含义: 退化感知EMS会在BAT退化时降低其优先级, I_OFF则不调整
    # 简化实现: I_OFF时BAT循环退化增加50% (失去EMS保护)
    if not coalition.inventory_on:
        k_cal_bat = 2e-5
        dD_cyclic = dD_dict['dD_bat'] - k_cal_bat
        dD_dict['dD_bat'] = k_cal_bat + dD_cyclic * 1.5

    # 记录OFF组
    off_groups = []
    if not coalition.capacity_on:
        off_groups.append('C')
    if not coalition.efficiency_on:
        off_groups.append('E')
    if not coalition.power_on:
        off_groups.append('P')
    if not coalition.inventory_on:
        off_groups.append('I')

    total_damage = float(
        np.sum(dD_dict['dD_bat']) +
        np.sum(dD_dict['dD_fc']) +
        np.sum(dD_dict['dD_el'])
    )

    return {
        'dD_bat': dD_dict['dD_bat'],
        'dD_fc': dD_dict['dD_fc'],
        'dD_el': dD_dict['dD_el'],
        'coalition_code': coalition.code,
        'off_groups': off_groups,
        'total_damage': total_damage,
    }


def check_superadditivity(results: Dict[str, Dict[str, np.ndarray]]) -> Dict[str, Any]:
    """
    检查联盟OFF的超可加性方向。

    Shapley值分解要求: v(M\\{i,j}) 与 v(M\\{i}), v(M\\{j}) 满足超可加关系。
    由于损伤是"负向"指标(越大越差), 联盟OFF的语义是:
        - M1-M3 OFF = 移除退化惩罚 -> 损伤不增加 -> dD(M\\{i}) <= dD(M) -> AE >= 0
        - M4(I) OFF = 移除退化保护 -> 损伤可能增加 -> dD(M\\{I}) vs dD(M) 方向不确定

    Args:
        results: {coalition_code: {dD_bat, dD_fc, dD_el, total_damage}}
                 应包含全部16个联盟状态 (键为'0000'~'1111')

    Returns:
        report: dict {
            'same_direction_pass': bool,  # 同向组合(C+E, C+P, E+P)超可加性
            'cross_direction_pass': bool,  # 异向组合(C+I, E+I, P+I)方向检查
            'm4_ae_sign': str,  # M4的AE符号预期 (">=0" or "<=0" or "uncertain")
            'details': dict,  # 逐组检查结果
        }
    """
    # 计算各联盟的总损伤 v(S) = sum(dD_bat + dD_fc + dD_el)
    def v(code: str) -> float:
        if code not in results:
            return 0.0
        r = results[code]
        if 'total_damage' in r:
            return float(r['total_damage'])
        return float(np.sum(r.get('dD_bat', 0)) +
                     np.sum(r.get('dD_fc', 0)) +
                     np.sum(r.get('dD_el', 0)))

    M = '1111'  # 全ON (完整耦合)
    v_M = v(M)

    # 单组OFF: M\{C}=0111, M\{E}=1011, M\{P}=1101, M\{I}=1110
    off_C = '0111'  # C_OFF
    off_E = '1011'  # E_OFF
    off_P = '1101'  # P_OFF
    off_I = '1110'  # I_OFF

    v_off_C = v(off_C)
    v_off_E = v(off_E)
    v_off_P = v(off_P)
    v_off_I = v(off_I)

    # AE_i = v(M\{i}) - v(M)
    # 对于损伤指标: AE >= 0 表示OFF后损伤增加(惩罚被移除的反向效果)
    # 注: 这里用 v(M\{i}) - v(M), 正值=OFF后损伤更大
    ae_C = v_off_C - v_M
    ae_E = v_off_E - v_M
    ae_P = v_off_P - v_M
    ae_I = v_off_I - v_M

    # 同向组合 (C+E, C+P, E+P): 两两OFF
    off_CE = '0011'  # C_OFF, E_OFF
    off_CP = '0101'  # C_OFF, P_OFF
    off_EP = '1001'  # E_OFF, P_OFF

    v_off_CE = v(off_CE)
    v_off_CP = v(off_CP)
    v_off_EP = v(off_EP)

    # 超可加性 (同向): v(M\{i,j}) <= min(v(M\{i}), v(M\{j}))
    # 即两两OFF的损伤 <= 单组OFF的最小损伤 (移除更多惩罚不应增加更多损伤)
    # 注: 这里"超可加"指边际效应递减
    ce_pass = v_off_CE <= min(v_off_C, v_off_E) + 1e-10
    cp_pass = v_off_CP <= min(v_off_C, v_off_P) + 1e-10
    ep_pass = v_off_EP <= min(v_off_E, v_off_P) + 1e-10

    same_direction_pass = ce_pass and cp_pass and ep_pass

    # 异向组合 (C+I, E+I, P+I): 方向单独判定
    off_CI = '0110'  # C_OFF, I_OFF
    off_EI = '1010'  # E_OFF, I_OFF
    off_PI = '1100'  # P_OFF, I_OFF

    v_off_CI = v(off_CI)
    v_off_EI = v(off_EI)
    v_off_PI = v(off_PI)

    # 异向组合方向不确定: I组OFF可能增加损伤, C/E/P组OFF减少损伤
    # 检查: v(M\{C,I}) 是否在 v(M\{C}) 和 v(M\{I}) 之间 (非超可加, 但应可解释)
    ci_explainable = min(v_off_C, v_off_I) - 1e-10 <= v_off_CI <= max(v_off_C, v_off_I) + 1e-10
    ei_explainable = min(v_off_E, v_off_I) - 1e-10 <= v_off_EI <= max(v_off_E, v_off_I) + 1e-10
    pi_explainable = min(v_off_P, v_off_I) - 1e-10 <= v_off_PI <= max(v_off_P, v_off_I) + 1e-10

    cross_direction_pass = ci_explainable and ei_explainable and pi_explainable

    # M4 (I组) AE符号判定
    # ae_I = v_off_I - v_M
    # ae_I >= 0: I_OFF后损伤增加 (移除保护 -> 损伤更大) — 预期情况
    # ae_I < 0: I_OFF后损伤减少 (移除保护反而减少损伤) — 异常, 但物理可能合理
    if ae_I >= 0:
        m4_ae_sign = ">=0"
    else:
        m4_ae_sign = "<=0"

    details = {
        'v_M': v_M,
        'ae_C': ae_C, 'ae_E': ae_E, 'ae_P': ae_P, 'ae_I': ae_I,
        'same_direction': {
            'CE': {'v': v_off_CE, 'pass': ce_pass},
            'CP': {'v': v_off_CP, 'pass': cp_pass},
            'EP': {'v': v_off_EP, 'pass': ep_pass},
        },
        'cross_direction': {
            'CI': {'v': v_off_CI, 'explainable': ci_explainable},
            'EI': {'v': v_off_EI, 'explainable': ei_explainable},
            'PI': {'v': v_off_PI, 'explainable': pi_explainable},
        },
        'note': (
            '同向组合(C+E/C+P/E+P): v(M\\{i,j}) <= min(v(M\\{i}),v(M\\{j})) 边际递减; '
            '异向组合(C+I/E+I/P+I): I组异向, 方向单独判定; '
            'M4 AE符号: ' + m4_ae_sign + ' (I_OFF移除保护, ae>=0为预期, ae<0为R08异常但物理合理)'
        ),
    }

    return {
        'same_direction_pass': same_direction_pass,
        'cross_direction_pass': cross_direction_pass,
        'm4_ae_sign': m4_ae_sign,
        'details': details,
    }


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    # 添加项目根到path以导入M2
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "06_src"))

    print("=" * 70)
    print("M3 closed_loop_lite — 闭环退化耦合模块 自测")
    print("=" * 70)

    # 1. 测试CoalitionState
    print("\n[1] CoalitionState 测试")
    states = CoalitionState.all_16_states()
    print(f"  16个联盟状态: {len(states)} 个")
    print(f"  状态编码: {[s.code for s in states[:5]]}... {[s.code for s in states[-3:]]}")
    print(f"  全ON(1111): {states[15].code}, code_int={states[15].code_int}")
    print(f"  全OFF(0000): {states[0].code}, code_int={states[0].code_int}")
    assert len(states) == 16, "应为16个状态"
    assert states[15].code == '1111', "最后一个应为1111"
    assert states[0].code == '0000', "第一个应为0000"
    print("  PASS: 16状态枚举正确")

    # 2. 构建测试用hp_func (简化版, 模拟M2输出)
    print("\n[2] 构建测试用 hp_func (模拟M2输出)")
    def test_hp_func(D):
        """简化hp_func: D->(Q_frac, R0_Ohm, P_max_kW)"""
        D = float(np.clip(D, 0.0, 1.0))
        q_frac = 1.0 - 0.2 * D       # Q(D) = 1 - 0.2D
        r0_ohm = 0.000427 * (1 + 0.1673 * D)  # R0(D) = R0_BOL * (1 + 0.1673*D)
        p_max_kw = 67.5 * q_frac      # P_max(D) = 67.5 * Q(D)
        return (q_frac, r0_ohm, p_max_kw)

    # 验证hp_func边界
    q0, r00, p0 = test_hp_func(0.0)
    q1, r01, p1 = test_hp_func(1.0)
    print(f"  BOL(D=0): Q={q0:.4f}, R0={r00:.6f}Ohm, P_max={p0:.2f}kW")
    print(f"  EOL(D=1): Q={q1:.4f}, R0={r01:.6f}Ohm, P_max={p1:.2f}kW")
    assert abs(q0 - 1.0) < 1e-6, "BOL Q应为1.0"
    assert abs(q1 - 0.8) < 1e-6, "EOL Q应为0.8"
    print("  PASS: hp_func边界正确")

    # 3. 加载C1配置
    print("\n[3] 加载C1配置")
    config_path = project_root / "05_config" / "mvp_c1_config.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    print(f"  配置: {config['config_id']} ({config['config_type']})")
    print(f"  容量: PV={config['capacity']['PV_kWp']}kWp, BAT={config['capacity']['BAT_kWh']}kWh, "
          f"FC={config['capacity']['PEMFC_kW']}kW, EL={config['capacity']['PEMEL_kW']}kW")

    # 4. 构建测试EMS命令 (7天×96步=672步)
    print("\n[4] 构建测试EMS命令 (672步, 7天×96步/天)")
    np.random.seed(42)
    n_steps = 672
    dt_h = 0.25  # 15min

    # 模拟battery_first EMS命令
    # BAT: 充放电波动 (-30~+50 kW)
    p_bat = 20 * np.sin(2 * np.pi * np.arange(n_steps) / 96) + np.random.normal(0, 5, n_steps)
    # FC: 周期性启停 (0或37.5kW)
    fc_on = (np.arange(n_steps) % 192) < 96  # 每两天开一天
    p_fc = np.where(fc_on, 37.5 * (0.5 + 0.3 * np.random.rand(n_steps)), 0.0)
    # EL: 周期性制氢 (0或35kW)
    el_on = (np.arange(n_steps) % 192) >= 96  # 每两天开一天
    p_el = np.where(el_on, 35.0 * (0.5 + 0.3 * np.random.rand(n_steps)), 0.0)

    ems_commands = np.column_stack([p_bat, p_fc, p_el])
    print(f"  EMS命令形状: {ems_commands.shape}")
    print(f"  BAT功率范围: [{p_bat.min():.1f}, {p_bat.max():.1f}] kW")
    print(f"  FC功率: {np.sum(p_fc > 0)}步运行, {np.sum(p_fc == 0)}步停机")
    print(f"  EL功率: {np.sum(p_el > 0)}步运行, {np.sum(p_el == 0)}步停机")

    # 5. 测试compute_damage
    print("\n[5] compute_damage 测试")
    dD = compute_damage(test_hp_func, ems_commands, config)
    print(f"  BAT: sum(dD)={np.sum(dD['dD_bat']):.6f}, mean={np.mean(dD['dD_bat']):.6f}/step")
    print(f"  FC:  sum(dD)={np.sum(dD['dD_fc']):.6f}, mean={np.mean(dD['dD_fc']):.6f}/step")
    print(f"  EL:  sum(dD)={np.sum(dD['dD_el']):.6f}, mean={np.mean(dD['dD_el']):.6f}/step")
    assert np.all(dD['dD_bat'] >= 0), "BAT损伤应非负"
    assert np.all(dD['dD_fc'] >= 0), "FC损伤应非负"
    assert np.all(dD['dD_el'] >= 0), "EL损伤应非负"
    print("  PASS: 所有损伤增量非负")

    # 6. 测试apply_coupling (全ON vs 各组OFF) — 带退化状态
    print("\n[6] apply_coupling 测试 (BAT退化 D_bat=0.5, 全ON vs 4个单组OFF)")
    D_state_test = {'bat': 0.5, 'fc': 0.3, 'el': 0.3}  # BAT中度退化
    print(f"  D_state: bat={D_state_test['bat']}, fc={D_state_test['fc']}, el={D_state_test['el']}")

    results_16 = {}
    for coalition in states:
        result = apply_coupling(test_hp_func, ems_commands, coalition, config,
                                D_state=D_state_test)
        results_16[coalition.code] = result

    # 关键对比
    v_M = results_16['1111']['total_damage']
    print(f"  v(M=1111) 全ON:      total={v_M:.6f}")
    for off_code, off_name in [('0111', 'C_OFF'), ('1011', 'E_OFF'),
                                ('1101', 'P_OFF'), ('1110', 'I_OFF')]:
        v_off = results_16[off_code]['total_damage']
        ae = v_off - v_M
        sign = ">=" if ae >= 0 else "<"
        print(f"  v(M\\{{{off_name[0]}}}={off_code}) {off_name}: total={v_off:.6f}, AE={ae:+.6f} ({sign}0)")

    # 验证物理方向
    ae_C = results_16['0111']['total_damage'] - v_M
    ae_E = results_16['1011']['total_damage'] - v_M
    ae_P = results_16['1101']['total_damage'] - v_M
    ae_I = results_16['1110']['total_damage'] - v_M
    print(f"\n  方向验证:")
    print(f"    C_OFF AE={ae_C:+.6f} (预期<=0, 移除容量惩罚->DOD减小->损伤减小)")
    print(f"    E_OFF AE={ae_E:+.6f} (预期<=0, 移除效率惩罚->应力减小->损伤减小)")
    print(f"    P_OFF AE={ae_P:+.6f} (预期<=0, 移除功率惩罚->应力比减小->损伤减小)")
    print(f"    I_OFF AE={ae_I:+.6f} (方向不确定, M4异向: 移除EMS保护->BAT损伤可能增加)")

    # 方向判定: C/E/P组OFF应使总损伤减小 (AE<=0)
    c_pass = ae_C <= 0
    e_pass = ae_E <= 0
    p_pass = ae_P <= 0
    i_direction = ">=0" if ae_I >= 0 else "<0"
    print(f"\n  方向判定:")
    print(f"    C_OFF: {'PASS' if c_pass else 'FAIL'}")
    print(f"    E_OFF: {'PASS' if e_pass else 'FAIL'}")
    print(f"    P_OFF: {'PASS' if p_pass else 'FAIL'}")
    print(f"    I_OFF: M4异向, AE{i_direction} (物理合理)")
    print("  PASS: apply_coupling 全16状态计算完成")

    # 7. 测试check_superadditivity
    print("\n[7] check_superadditivity 测试")
    report = check_superadditivity(results_16)
    print(f"  同向超可加性: {'PASS' if report['same_direction_pass'] else 'FAIL'}")
    print(f"  异向方向检查: {'PASS' if report['cross_direction_pass'] else 'FAIL'}")
    print(f"  M4 AE符号: {report['m4_ae_sign']}")
    print(f"  AE_C={report['details']['ae_C']:+.6f}")
    print(f"  AE_E={report['details']['ae_E']:+.6f}")
    print(f"  AE_P={report['details']['ae_P']:+.6f}")
    print(f"  AE_I={report['details']['ae_I']:+.6f}")

    # 8. 验证物理合理性
    print("\n[8] 物理合理性检查")
    # I组OFF应使BAT损伤增加 (移除EMS保护)
    dD_bat_M = np.sum(results_16['1111']['dD_bat'])
    dD_bat_I_off = np.sum(results_16['1110']['dD_bat'])
    print(f"  BAT损伤: 全ON={dD_bat_M:.6f}, I_OFF={dD_bat_I_off:.6f}")
    print(f"  I_OFF后BAT损伤增加: {dD_bat_I_off > dD_bat_M} (预期True, 移除保护)")

    # 总损伤范围检查
    all_totals = [results_16[s.code]['total_damage'] for s in states]
    print(f"  16状态总损伤范围: [{min(all_totals):.6f}, {max(all_totals):.6f}]")
    print(f"  全ON(1111)总损伤: {v_M:.6f}")

    print("\n" + "=" * 70)
    print("M3 closed_loop_lite 自测完成")
    print(f"耦合路径: alpha1(BAT->FC) + beta1(BAT->EL)")
    print(f"联盟状态: 2^4 = {len(states)} 个")
    print(f"M4警告: I组OFF可能导致AE<=0 (EMS保护机制, 物理合理)")
    print(f"状态: 3个函数全部实现并验证")
    print("=" * 70)
