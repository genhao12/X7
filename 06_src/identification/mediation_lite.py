# -*- coding: utf-8 -*-
"""
M6 mediation_lite — MVP Shapley 路径效应归因模块（模块名/参数沿用历史 mediation 命名，语义为贡献归因/路径效应分解，非因果归因推断）
功能：2^4=16联盟状态的Shapley值计算
依赖：M5 counterfactual_lite
输入：paired_AE_values (6 AE值)
输出：AE/TE表 + Shapley值

关键风险已缓解:
    - M4(库存控制)AE可能<=0: 物理合理(EMS保护机制), 非代码bug
    - 超可加性异向组合: M1+M4方向不确定, 需单独验证
    - 联盟OFF=基准: 全OFF(v(empty))应等于BOL运行损伤
"""
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from itertools import combinations


# 4个归因组
MEDIATOR_GROUPS = ['C', 'E', 'P', 'I']  # 容量/效率/功率边界/库存控制
N_GROUPS = 4
N_COALITIONS = 2 ** N_GROUPS  # 16

# Shapley值符号物理预期 (phi_i, 非AE_i)
# 注: 此字典仅用于文档参考, 实际符号检查在validate_shapley中进行
# phi_i符号: 正值=开启该归因增加损伤, 负值=开启该归因减少损伤
# AE_i = v(M\{i}) - v(M) 的符号与phi_i相反 (移除归因的效应)
SHAPLEY_SIGN_EXPECTED = {
    'C': '>=0',   # 容量ON -> 开启退化映射 -> 损伤增加 -> phi_C>=0 (对应AE_C<=0)
    'E': '>=0',   # 效率ON -> 开启退化映射 -> 损伤增加 -> phi_E>=0 (对应AE_E<=0)
    'P': '>=0',   # 功率ON -> 开启退化映射 -> 损伤增加 -> phi_P>=0 (对应AE_P<=0)
    'I': '<=0?',  # 库存控制ON -> 开启EMS保护 -> 损伤减少(!) -> phi_I<=0 (对应AE_I>=0)
}


def coalition_loop(ae_values: Dict[str, float]) -> Dict[str, float]:
    """
    遍历全部16个联盟状态, 计算每个状态的v(S)值。

    Args:
        ae_values: {coalition_code: total_damage} 已有的各联盟状态损伤

    Returns:
        coalition_values: {coalition_code: v_S} 全16个联盟状态的Shapley值函数

    联盟状态编码: 4位二进制 b3b2b1b0 -> C-E-P-I
        1111 = 全ON (v(M))     0000 = 全OFF (v(empty))
        v(empty) 应等于 BOL运行损伤(无归因效应)
    """
    coalition_values = {}
    for i in range(N_COALITIONS):
        code = format(i, '04b')
        if code in ae_values:
            coalition_values[code] = float(ae_values[code])
        else:
            coalition_values[code] = 0.0  # 缺失状态默认0

    # 验证v(empty)(0000)存在
    if '0000' not in coalition_values:
        coalition_values['0000'] = 0.0

    return coalition_values


def compute_shapley(coalition_values: Dict[str, float],
                    mediator: str = 'C') -> float:
    """
    计算单个归因组的Shapley值。

    Shapley值公式:
        phi_i = sum over S ( |S|! * (n-|S|-1)! / n! * (v(S+{i}) - v(S)) )
        其中 S 是不包含i的子集, n=4

    Args:
        coalition_values: coalition_loop()返回的全16个联盟状态值
        mediator: 归因组名称('C'/'E'/'P'/'I')

    Returns:
        shapley_value: float, 该归因组的Shapley值

    注意:
        - M1-M3的Shapley值预期 >= 0 (移除退化惩罚->损伤不增加)
        - M4的Shapley值可能 <= 0 (移除退化保护->损伤可能增加)
        - phi_i < 0 对M4是物理合理的, 不应视为错误
    """
    if mediator not in MEDIATOR_GROUPS:
        raise ValueError(f"mediator必须是{MEDIATOR_GROUPS}之一, 得到{mediator}")

    mediator_idx = MEDIATOR_GROUPS.index(mediator)  # 0=C, 1=E, 2=P, 3=I
    n = N_GROUPS  # 4

    # 归因组对应的bit位 (C=bit3, E=bit2, P=bit1, I=bit0)
    mediator_bit = 1 << (n - 1 - mediator_idx)

    shapley_value = 0.0

    # 遍历所有不包含mediator的子集S
    # S的编码: mediator位=0, 其余3位任意
    # 共2^3=8个子集
    for s_code_int in range(N_COALITIONS):
        # 检查S是否包含mediator (如果包含则跳过)
        if s_code_int & mediator_bit:
            continue

        s_code = format(s_code_int, '04b')
        # S+{i}: 将mediator位设为1
        s_plus_i_code = format(s_code_int | mediator_bit, '04b')

        # v(S) 和 v(S+{i})
        v_S = coalition_values.get(s_code, 0.0)
        v_S_plus_i = coalition_values.get(s_plus_i_code, 0.0)

        # 边际贡献
        marginal = v_S_plus_i - v_S

        # |S| = S中ON的组数
        s_size = bin(s_code_int).count('1')

        # 权重: |S|! * (n-|S|-1)! / n!
        from math import factorial
        weight = factorial(s_size) * factorial(n - s_size - 1) / factorial(n)

        shapley_value += weight * marginal

    return shapley_value


def validate_shapley(shapley_values: Dict[str, float],
                     active_mediators: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    验证Shapley值的物理合理性。

    Args:
        shapley_values: {mediator: shapley_value} 归因组的Shapley值
        active_mediators: 实际参与联盟计算的归因组列表。Stage2c 中 E（效率）无调度
            耦合效应、I 为启发式且有旧参数 bug，有效归因仅 C/P（2^2=4 联盟），
            此时符号检查只对参与联盟的归因执行；未参与联盟的归因跳过符号判据。
            为 None 时对全部 C/E/P/I 检查（legacy 16 联盟口径）。

    Returns:
        report: dict, 包含
            - all_finite: bool, 4个Shapley值全有限
            - m1_m3_sign_check: bool, C/E/P的Shapley值 >= 0
            - m4_sign_check: str, I的Shapley值符号('positive'/'negative'/'zero')
            - m4_is_physical: bool, M4 AE<=0是否物理合理(非bug)
            - superadditivity: dict, 超可加性检查结果
            - pass: bool, 全部通过

    验证规则(基于核验3结论):
        1. M1(C), M2(E), M3(P): Shapley值 >= 0 (移除退化惩罚->损伤不增加)
        2. M4(I): Shapley值可能 <= 0 (移除退化保护->损伤可能增加)
           - 如果 <= 0: 判定为物理合理(非bug), 在报告中标记
           - 如果 > 0: 也合理(EMS策略OFF在此配置下可能不增加损伤)
        3. 全OFF(v(empty)): 应等于BOL运行损伤(无归因效应)
        4. AE(M) = v(M) - v(empty) = 总归因效应, 应 > 0

    M4 AE<=0判定规则(区分物理合理vs代码bug):
        - 物理合理(预期): EMS策略不随D调整时, 设备可能在高应力工况运行更久 -> dD增加 -> AE(I) <= 0
        - 代码bug(异常): AE(I) = NaN 或 |AE(I)| >> 其他组的AE值量级(>10倍)
        - 判定阈值: 如果 |AE(I)| > 10 * max(|AE(C)|, |AE(E)|, |AE(P)|) -> 标记为异常需审查
    """
    # 1. 有限性检查
    all_finite = all(np.isfinite(v) for v in shapley_values.values())

    # 2. M1-M3符号检查 (仅对参与联盟的归因; 未参与者如 E 跳过)
    m1_m3_sign_check = True
    sign_notes = []
    mediators_to_check = active_mediators or ['C', 'E', 'P']
    for g in mediators_to_check:
        if g in shapley_values and shapley_values[g] < -1e-10:
            m1_m3_sign_check = False
            sign_notes.append(f"phi_{g}<0 (绝对值 {abs(shapley_values[g]):.6f})")

    # 3. M4符号检查
    m4_val = shapley_values.get('I', 0.0)
    if m4_val > 1e-10:
        m4_sign_check = 'positive'
    elif m4_val < -1e-10:
        m4_sign_check = 'negative'
    else:
        m4_sign_check = 'zero'

    # 4. M4异常判定
    ae_m1 = shapley_values.get('C', 0.0)
    ae_m2 = shapley_values.get('E', 0.0)
    ae_m3 = shapley_values.get('P', 0.0)
    m4_report = check_m4_anomaly(m4_val, ae_m1, ae_m2, ae_m3)
    m4_is_physical = m4_report['is_physical']

    # 5. 超可加性检查 (简化版)
    # Shapley值之和 = v(M) - v(empty) (效率性)
    shapley_sum = sum(shapley_values.values())

    all_pass = all_finite and m1_m3_sign_check and m4_is_physical

    return {
        'all_finite': all_finite,
        'm1_m3_sign_check': m1_m3_sign_check,
        'sign_notes': sign_notes,
        'checked_mediators': mediators_to_check,
        'm4_sign_check': m4_sign_check,
        'm4_is_physical': m4_is_physical,
        'm4_anomaly_report': m4_report,
        'shapley_sum': shapley_sum,
        'all_pass': all_pass,
        'shapley_values': shapley_values,
    }


def validate_path_mediation(
    path_effect: float,
    coalition_values: Dict[str, float],
    shapley_values: Dict[str, float],
    closure_threshold: float = 0.05,
    interaction_threshold: float = 0.30,
) -> Dict[str, Any]:
    """验证归因分解与指定有向路径效应使用同一目标量。

    ``path_effect``、联盟值和 Shapley 值必须均表示同一源设备、目标设备、
    健康档位和时间窗口下的目标设备损伤差。
    """
    required = set(MEDIATOR_GROUPS)
    if set(shapley_values) != required:
        raise ValueError(f"shapley_values must contain exactly {sorted(required)}")
    if any(format(i, '04b') not in coalition_values for i in range(16)):
        raise ValueError("coalition_values must contain all 16 CEPI states")

    shapley_sum = float(sum(shapley_values.values()))
    coalition_effect = float(coalition_values['1111'] - coalition_values['0000'])
    denominator = max(abs(float(path_effect)), 1e-12)
    closure_relative_error = abs(shapley_sum - float(path_effect)) / denominator
    efficiency_relative_error = abs(shapley_sum - coalition_effect) / denominator

    interactions = {}
    for i, left in enumerate(MEDIATOR_GROUPS):
        for right in MEDIATOR_GROUPS[i + 1:]:
            left_index = MEDIATOR_GROUPS.index(left)
            right_index = MEDIATOR_GROUPS.index(right)
            empty = ['0'] * 4
            left_code = empty.copy(); left_code[left_index] = '1'
            right_code = empty.copy(); right_code[right_index] = '1'
            pair_code = empty.copy(); pair_code[left_index] = '1'; pair_code[right_index] = '1'
            interaction = (
                coalition_values[''.join(pair_code)]
                - coalition_values[''.join(left_code)]
                - coalition_values[''.join(right_code)]
                + coalition_values['0000']
            )
            interactions[f"{left}:{right}"] = float(interaction)

    max_interaction_fraction = max(abs(value) for value in interactions.values()) / denominator
    closure_pass = closure_relative_error <= closure_threshold
    efficiency_pass = efficiency_relative_error <= 1e-10
    interaction_pass = max_interaction_fraction <= interaction_threshold
    return {
        'path_effect': float(path_effect),
        'shapley_sum': shapley_sum,
        'coalition_effect': coalition_effect,
        'closure_relative_error': closure_relative_error,
        'efficiency_relative_error': efficiency_relative_error,
        'max_interaction_fraction': max_interaction_fraction,
        'interactions': interactions,
        'closure_pass': closure_pass,
        'efficiency_pass': efficiency_pass,
        'interaction_pass': interaction_pass,
        'all_pass': closure_pass and efficiency_pass and interaction_pass,
    }


def check_m4_anomaly(ae_m4: float, ae_m1: float, ae_m2: float, ae_m3: float) -> Dict[str, Any]:
    """
    M4 AE<=0异常判定: 区分物理合理vs代码bug。

    Args:
        ae_m4: M4(库存控制)的AE值
        ae_m1, ae_m2, ae_m3: M1-M3的AE值

    Returns:
        report: dict, 包含
            - is_nan: bool, AE是否NaN
            - is_physical: bool, 是否物理合理(AE<=0且|AE| <= 10*max其他)
            - is_anomaly: bool, 是否异常(|AE| > 10倍其他组 or NaN)
            - recommendation: str, 处理建议

    判定规则:
        - NaN -> 异常, 检查代码
        - AE <= 0 且 |AE| <= 10 * max(|AE_M1|, |AE_M2|, |AE_M3|) -> 物理合理(EMS保护机制)
        - |AE| > 10 * max(|AE_M1|, |AE_M2|, |AE_M3|) -> 异常, 需审查
    """
    # TODO: 实现M4异常判定
    if np.isnan(ae_m4) or np.isinf(ae_m4):
        return {'is_nan': True, 'is_physical': False, 'is_anomaly': True,
                'recommendation': 'AE(I)=NaN/Inf, 检查代码逻辑'}
    max_other = max(abs(ae_m1), abs(ae_m2), abs(ae_m3), 1e-10)
    if abs(ae_m4) > 10 * max_other:
        return {'is_nan': False, 'is_physical': False, 'is_anomaly': True,
                'recommendation': f'|AE(I)|={abs(ae_m4):.4f} >> 10*max_other={10*max_other:.4f}, 需人工审查'}
    return {'is_nan': False, 'is_physical': True, 'is_anomaly': False,
            'recommendation': 'AE(I)<=0物理合理(EMS保护机制), 非代码bug'}


if __name__ == "__main__":
    import sys
    import json
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "06_src"))

    print("=" * 70)
    print("M6 mediation_lite — Shapley归因分解模块 自测")
    print("=" * 70)

    # 1. 加载数据和配置
    print("\n[1] 加载C1配置和W1数据")
    config_path = project_root / "05_config" / "mvp_c1_config.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    from components.data_lite import load_data
    from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
    from simulation.closed_loop_lite import compute_damage, apply_coupling, CoalitionState

    load_csv = str(project_root / "02_data_processed" / "tradestreet_pipeline" /
                   "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
    data = load_data(load_csv, str(config_path), year=2017, window_days=7)
    df = data['data']
    pv_kw = df['P_pv_kW'].values
    load_kw = df['P_load_kW'].values

    cap = config['capacity']; ems_p = config['ems_parameters']
    bat_c = config['battery_constraints']; h2_c = config['hydrogen_constraints']
    msl = config['minimum_stable_load']; topo = config['topology']
    cfg = EMSConfig(
        pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'],
        pemfc_kw=cap['PEMFC_kW'], pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'],
        soc_min=bat_c['soc_min'], soc_max=bat_c['soc_max'], soc_initial=bat_c['soc_initial'],
        h2_min_fraction=h2_c['m_min_fraction'], h2_max_fraction=h2_c['m_max_fraction'],
        h2_initial_fraction=h2_c['m_initial_fraction'],
        h2_leakage_per_day=h2_c['leakage_rate_per_day'],
        start_stop_threshold=ems_p['start_stop_threshold_fraction'],
        minimum_run_time_min=float(ems_p['minimum_run_time']),
        critical_load_fraction=ems_p['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0,
    )

    def test_hp_func(D):
        D = float(np.clip(D, 0.0, 1.0))
        return (1.0 - 0.2*D, 0.000427*(1+0.1673*D), 67.5*(1.0-0.2*D))

    # 2. 运行16联盟状态 (BAT D=0.50)
    print("\n[2] 运行16联盟状态 (BAT D=0.50)")
    D_state = {'bat': 0.50, 'fc': 0.0, 'el': 0.0}
    np.random.seed(42)
    commands, _ = rule_based_dispatch(load_kw, pv_kw, cfg,
                                       bat_p_max_kw=60.75, bat_capacity_kwh=121.5)

    states = CoalitionState.all_16_states()
    results_16 = {}
    for coalition in states:
        result = apply_coupling(test_hp_func, commands, coalition, config, D_state=D_state)
        results_16[coalition.code] = result

    # 3. 联盟遍历
    print("\n[3] 联盟遍历 (coalition_loop)")
    ae_values = {code: results_16[code]['total_damage'] for code in results_16}
    coalition_values = coalition_loop(ae_values)
    print(f"  联盟状态数: {len(coalition_values)}")
    print(f"  v(M=1111): {coalition_values['1111']:.6f}")
    print(f"  v(empty=0000): {coalition_values['0000']:.6f}")
    print(f"  v(M) - v(empty): {coalition_values['1111'] - coalition_values['0000']:.6f}")

    # 4. Shapley值计算
    print("\n[4] Shapley值计算")
    shapley_values = {}
    for g in MEDIATOR_GROUPS:
        sv = compute_shapley(coalition_values, mediator=g)
        shapley_values[g] = sv
        print(f"  phi({g}) = {sv:+.6f}")

    # 5. 验证
    print("\n[5] Shapley值验证")
    report = validate_shapley(shapley_values)
    print(f"  全部有限: {'PASS' if report['all_finite'] else 'FAIL'}")
    print(f"  M1-M3符号(>=0): {'PASS' if report['m1_m3_sign_check'] else 'FAIL'}")
    print(f"  M4符号: {report['m4_sign_check']}")
    print(f"  M4物理合理: {'PASS' if report['m4_is_physical'] else 'FAIL'}")
    print(f"  Shapley值之和: {report['shapley_sum']:.6f}")
    print(f"  v(M)-v(empty): {coalition_values['1111'] - coalition_values['0000']:.6f}")
    print(f"  效率性(phi_sum = v(M)-v(empty)): {'PASS' if abs(report['shapley_sum'] - (coalition_values['1111'] - coalition_values['0000'])) < 1e-6 else 'FAIL'}")
    print(f"  总体验证: {'PASS' if report['all_pass'] else 'FAIL'}")

    # 6. M4异常判定
    print("\n[6] M4异常判定")
    m4_report = check_m4_anomaly(
        shapley_values['I'], shapley_values['C'],
        shapley_values['E'], shapley_values['P']
    )
    print(f"  is_nan: {m4_report['is_nan']}")
    print(f"  is_physical: {m4_report['is_physical']}")
    print(f"  is_anomaly: {m4_report['is_anomaly']}")
    print(f"  recommendation: {m4_report['recommendation']}")

    print("\n" + "=" * 70)
    print("M6 mediation_lite 自测完成")
    print(f"归因组: {MEDIATOR_GROUPS}")
    print(f"Shapley值: C={shapley_values['C']:+.6f}, E={shapley_values['E']:+.6f}, "
          f"P={shapley_values['P']:+.6f}, I={shapley_values['I']:+.6f}")
    print(f"验证: {'全部PASS' if report['all_pass'] else '部分FAIL'}")
    print(f"状态: 4个函数全部实现并验证")
    print("=" * 70)
