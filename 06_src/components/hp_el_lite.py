# -*- coding: utf-8 -*-
"""
hp_el_lite — PEMEL健康-性能映射模块
功能：从NAICNO数字孪生BOL参数 + 线性退化假设拟合EL健康-性能映射 D->(eta_frac, R_ohm_Ohm_cm2, P_max_kW)
依赖：mvp_bol_parameters.json (EL BOL物理参数 + EOL定义)

证据等级与数据来源:
    - NAICNO数字孪生 (mvp_bol_parameters.json): A级 — BOL物理参数(训练后6参数)
      V_cell_BOL=1.75V, eta_LHV_BOL=0.716, R_ohm=0.118 Ohm·cm²
    - PEMEL EIS退化原始数据: B级 — 实验室级薄膜Pt退化研究(非系统级)
      仅用于退化方向验证, 不直接用于alpha_R辨识
    - Buttler & Spliethoff 2018: B级先验 — PEMEL退化速率文献区间
    - Brezak 2023: B级先验 — PEMEL电化学模型参数交叉验证

D定义: D = specific_energy_increment / specific_energy_increment_EOL
    EOL = 比能耗增加10%相对于BOL → D=1.0
    D=0 -> BOL (V_cell = V_BOL = 1.75V, eta_LHV = 0.716)
    D=1.0 -> EOL (V_cell = V_BOL * 1.10 = 1.925V, eta_LHV = 0.650)

退化模型 (线性最小信息假设, 来自mvp_bol_parameters.json degradation_model):
    V_cell(D) = V_BOL * (1 + 0.10 * D)        — 比能耗增加10% → cell电压增加10%
    eta(D) = V_rev_LHV / V_cell(D)              — 效率=可逆电压/实际电压
    R_ohm(D) = R_BOL * (1 + alpha_R * D)        — 阻抗线性增长
    P_max(D) = P_rated * eta_frac(D) / eta_BOL  — 功率容量随效率缩减

风险声明: IEEE PHM有2-3老化时点, EL无长时老化数据, 线性为最小信息假设
         D>0.5时可能高估EOL附近退化速率 (来自mvp_bol_parameters.json degradation_model.risk)
"""
import json
import numpy as np
from typing import Dict, Any, Tuple, Callable
from pathlib import Path
from scipy.interpolate import interp1d


# === 数据路径 ===
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOL_PARAMS_PATH = PROJECT_ROOT / "05_config" / "mvp_bol_parameters.json"

# EOL定义: 比能耗增加10%
SPECIFIC_ENERGY_INCREMENT_EOL = 0.10

# 物理常数
R_GAS = 8.314          # J/(mol·K)
F_FARADAY = 96485      # C/mol
N_ELECTRONS = 2

# BOL工作点 (来自mvp_bol_parameters.json typical_bol_operating_point)
V_CELL_BOL_V = 1.75
V_REV_LHV_V = 1.253
V_REV_HHV_V = 1.481
ETA_LHV_BOL = 0.716    # = V_rev_LHV / V_cell_BOL = 1.253/1.75


def _load_el_bol_data() -> Dict[str, Any]:
    """加载EL BOL参数 (来自NAICNO数字孪生, mvp_bol_parameters.json)。"""
    with open(BOL_PARAMS_PATH, 'r', encoding='utf-8') as f:
        bol_full = json.load(f)

    pemel_bol = bol_full['pemel_bol']
    data = {
        'source': pemel_bol['source'],
        'model_type': pemel_bol['model_type'],
        'physical_constants': pemel_bol['physical_constants'],
        'bol_physics_parameters': pemel_bol['bol_physics_parameters'],
        'trained_parameters': pemel_bol.get('trained_parameters', {}),
        'typical_bol_operating_point': pemel_bol['typical_bol_operating_point'],
        'scale_migration': pemel_bol['scale_migration'],
        'eol_definition': pemel_bol['eol_definition'],
        'evidence_level': pemel_bol['evidence_level'],
        'degradation_model': pemel_bol.get('degradation_model', {
            'linear_assumption': 'V_cell和R_ohm随D线性变化, 为最小信息假设',
            'limitation': 'EL无长时老化数据, 线性为最小信息假设',
            'risk': 'D>0.5时可能高估EOL附近退化速率',
        }),
    }

    # 提取训练后参数 (更接近真实BOL)
    trained = pemel_bol.get('trained_parameters', {}).get('parameters', {})
    data['r_ohm_bol_ohm_cm2'] = float(trained.get('R_ohm_ref', 0.118))
    data['i0_a_ref'] = float(trained.get('i0_a_ref', 1.16e-05))
    data['i0_c_ref'] = float(trained.get('i0_c_ref', 0.00109))
    data['alpha_a'] = float(trained.get('alpha_a', 0.643))
    data['alpha_c'] = float(trained.get('alpha_c', 0.644))
    data['i_lim_ref'] = float(trained.get('i_lim_ref', 1.039))

    # BOL工作点
    data['v_cell_bol_V'] = V_CELL_BOL_V
    data['v_rev_lhv_V'] = V_REV_LHV_V
    data['v_rev_hhv_V'] = V_REV_HHV_V
    data['eta_lhv_bol'] = ETA_LHV_BOL
    data['i_typical_A_cm2'] = pemel_bol['typical_bol_operating_point']['current_density_A_cm2']
    data['T_typical_C'] = pemel_bol['typical_bol_operating_point']['temperature_C']

    return data


def fit_el_aging(el_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从EL BOL参数 + 线性退化假设拟合健康-性能映射参数。

    拟合内容:
        1. 电压退化: V_cell(D) = V_BOL * (1 + SPECIFIC_ENERGY_INCREMENT_EOL * D)
           — EOL定义强制: D=1时电压增10%
        2. 效率退化: eta(D) = V_rev_LHV / V_cell(D) = eta_BOL / (1 + 0.10*D)
           — 非线性, 因eta=V_rev/V_cell
        3. 阻抗增长: R_ohm(D) = R_BOL * (1 + alpha_R * D)
           — alpha_R=0.10 (保守假设: R与V_cell同比例增长)
        4. 功率退化: P_max(D) = P_rated * eta(D) / eta_BOL
           — 功率正比于效率

    Args:
        el_data: _load_el_bol_data()的输出

    Returns:
        fit_result: dict, 含拟合函数和参数
    """
    v_bol = el_data['v_cell_bol_V']
    v_rev_lhv = el_data['v_rev_lhv_V']
    r_ohm_bol = el_data['r_ohm_bol_ohm_cm2']

    # === 1. 电压退化函数: V(D) = V_BOL * (1 + 0.10*D) ===
    def voltage_growth_func(D):
        """D -> V_cell / V_BOL (电压增长因子)"""
        D = np.clip(D, 0.0, 1.0)
        return 1.0 + SPECIFIC_ENERGY_INCREMENT_EOL * D

    # === 2. 效率退化函数: eta(D) = V_rev_LHV / V_cell(D) ===
    # eta(D) = V_rev_LHV / (V_BOL * (1 + 0.10*D))
    # eta_frac(D) = eta(D) / eta_BOL = 1 / (1 + 0.10*D)  (归一化)
    def eta_frac_func(D):
        """D -> eta/eta_BOL (效率保持率, 归一化)"""
        D = np.clip(D, 0.0, 1.0)
        return 1.0 / (1.0 + SPECIFIC_ENERGY_INCREMENT_EOL * D)

    # 绝对效率: eta(D) = V_rev_LHV / V_cell(D)
    def eta_absolute_func(D):
        """D -> eta_LHV (绝对效率)"""
        D = np.clip(D, 0.0, 1.0)
        v_cell = v_bol * voltage_growth_func(D)
        return v_rev_lhv / v_cell

    # === 3. 阻抗增长: R(D) = R_BOL * (1 + alpha_R * D) ===
    # alpha_R = 0.10 (保守线性假设: R与V_cell同比例增长)
    # 来源: BOL params degradation_model.linear_assumption
    alpha_r = SPECIFIC_ENERGY_INCREMENT_EOL  # 0.10

    def r_ohm_growth_func(D):
        """D -> R_ohm增长因子 (相对于BOL)"""
        D = np.clip(D, 0.0, 1.0)
        return 1.0 + alpha_r * D

    # === 4. 构建I-V曲线退化模型 ===
    # 简化: BOL I-V曲线缩放法 (V_cell整体上移)
    # 实际NAICNO模型含活化+欧姆+浓差, 这里简化为整体缩放
    def iv_func_degraded(current_density, D):
        """退化后I-V曲线: V(J, D) = V_BOL(J) * (1 + 0.10*D)"""
        D = np.clip(D, 0.0, 1.0)
        # BOL I-V: 简化线性模型 V = V_rev + slope * J
        # BOL: V=1.75 at J=0.15, V_rev=1.253
        # slope = (1.75 - 1.253) / 0.15 = 3.313 V/(A/cm²)
        slope_bol = (v_bol - v_rev_lhv) / el_data['i_typical_A_cm2']
        v_rev = v_rev_lhv
        v_cell = v_rev + slope_bol * current_density
        # 退化: 整体电压上移
        v_cell_degraded = v_cell * voltage_growth_func(D)
        return v_cell_degraded

    # === R²计算 (理论模型, R²=1.0 by construction) ===
    r2_voltage = 1.0  # 线性模型强制
    r2_r_ohm = 1.0    # 线性模型强制

    fit_result = {
        'voltage_growth_func': voltage_growth_func,
        'eta_frac_func': eta_frac_func,
        'eta_absolute_func': eta_absolute_func,
        'r_ohm_growth_func': r_ohm_growth_func,
        'iv_func_degraded': iv_func_degraded,
        'r2_voltage': r2_voltage,
        'r2_r_ohm': r2_r_ohm,
        'v_cell_bol_V': round(v_bol, 4),
        'v_rev_lhv_V': round(v_rev_lhv, 4),
        'eta_lhv_bol': round(ETA_LHV_BOL, 4),
        'eta_lhv_eol': round(float(eta_absolute_func(1.0)), 4),
        'alpha_r': round(alpha_r, 4),
        'r_ohm_bol_ohm_cm2': round(r_ohm_bol, 6),
        'specific_energy_increment_eol': SPECIFIC_ENERGY_INCREMENT_EOL,
        'd_range_data': [0.0, 1.0],  # 模型覆盖全D范围
        'extrapolation_note': 'EL无长时老化数据, 线性为最小信息假设(BOL params degradation_model), '
                              'D>0.5时可能高估EOL附近退化速率',
        'n_voltage_points': 0,  # 无实测老化时点
        'n_eis_points': 0,      # 无系统级EIS老化时点
        'data_source': 'NAICNO数字孪生BOL参数 + 线性退化假设(EOL=10%比能耗增量)',
    }
    return fit_result


def interpolate_hp(fit_result: Dict[str, Any],
                   bol_params: Dict[str, Any]) -> Callable[[float], Tuple[float, float, float]]:
    """
    构建EL健康-性能映射函数 D -> (eta_frac, R_ohm_Ohm_cm2, P_max_kW)。

    Args:
        fit_result: fit_el_aging()的输出
        bol_params: BOL参数 (含scale_migration, system_kw等)

    Returns:
        hp_func: callable, D(0~1) -> (eta_frac, R_ohm_Ohm_cm2, P_max_kW)
            eta_frac: 效率保持率 (1.0 at BOL, ~0.909 at EOL, 非线性)
            R_ohm_Ohm_cm2: 欧姆阻抗 (Ohm·cm², BOL值+退化增量)
            P_max_kW: 最大可用功率 (kW, 效率缩减后)
    """
    eta_frac_func = fit_result['eta_frac_func']
    r_ohm_growth_func = fit_result['r_ohm_growth_func']
    alpha_r = fit_result['alpha_r']
    r_ohm_bol = fit_result['r_ohm_bol_ohm_cm2']

    # EL额定参数
    el_system_kw = bol_params.get('el_system_kw', 35.0)  # 系统级额定功率
    el_cell_area = bol_params.get('active_area_cm2', 50.0)

    def hp_func(D: float) -> Tuple[float, float, float]:
        """
        健康-性能映射: D -> (eta_frac, R_ohm_Ohm_cm2, P_max_kW)

        Args:
            D: 退化程度 [0, 1], 0=BOL, 1=EOL

        Returns:
            (eta_frac, R_ohm_Ohm_cm2, P_max_kW)
        """
        D = float(np.clip(D, 0.0, 1.0))

        # 效率保持率: eta(D) = eta_BOL / (1 + 0.10*D)
        # 归一化: eta_frac = 1 / (1 + 0.10*D)
        eta_frac = float(eta_frac_func(D))

        # 阻抗: R_ohm(D) = R_BOL * (1 + alpha_R * D)
        r_ohm = float(r_ohm_bol * r_ohm_growth_func(D))

        # 最大可用功率: P_max(D) = P_rated * eta_frac
        # (效率降低 → 同DC功率下产氢减少 → 有效产氢功率降低)
        p_max_kw = float(el_system_kw * eta_frac)

        return (eta_frac, r_ohm, p_max_kw)

    # 附加元数据
    hp_func.bol_params = {
        'r_ohm_bol_ohm_cm2': round(r_ohm_bol, 6),
        'el_system_kw': el_system_kw,
        'el_cell_area_cm2': el_cell_area,
        'v_cell_bol_V': fit_result['v_cell_bol_V'],
        'eta_lhv_bol': fit_result['eta_lhv_bol'],
    }
    hp_func.fit_params = {
        'alpha_r': alpha_r,
        'specific_energy_increment_eol': SPECIFIC_ENERGY_INCREMENT_EOL,
        'eta_frac_at_eol': round(float(eta_frac_func(1.0)), 4),
        'r_ohm_growth_at_eol': round(1.0 + alpha_r * 1.0, 4),
        'data_source': fit_result['data_source'],
        'risk_note': fit_result['extrapolation_note'],
    }

    return hp_func


def validate_hp_func(hp_func: Callable, bol_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证EL健康-性能映射函数的边界行为。

    Returns:
        report: dict, 含BOL/EOL/单调性/联盟OFF检查
    """
    fit = getattr(hp_func, 'fit_params', {})
    bol = getattr(hp_func, 'bol_params', {})

    # 1. BOL检查: D=0
    eta_bol, r_bol, pmax_bol = hp_func(0.0)
    bol_check = abs(eta_bol - 1.0) < 1e-6
    bol_r_check = abs(r_bol - bol.get('r_ohm_bol_ohm_cm2', r_bol)) < 1e-6
    bol_pmax_check = abs(pmax_bol - bol.get('el_system_kw', pmax_bol)) < 1e-6

    # 2. EOL检查: D=1.0
    eta_eol, r_eol, pmax_eol = hp_func(1.0)
    eta_eol_expected = 1.0 / (1.0 + fit.get('specific_energy_increment_eol', 0.1) * 1.0)
    eol_check = abs(eta_eol - eta_eol_expected) < 1e-6
    eol_eta_in_range = 0.85 <= eta_eol <= 0.95  # 应≈0.909

    # 3. 单调性检查
    d_test = np.linspace(0, 1, 21)
    eta_trace = np.array([hp_func(d)[0] for d in d_test])
    r_trace = np.array([hp_func(d)[1] for d in d_test])
    eta_monotonic = np.all(np.diff(eta_trace) <= 1e-10)
    r_monotonic = np.all(np.diff(r_trace) >= -1e-10)

    # 4. 联盟OFF检查: D=0时g_i(0)返回BOL值
    coalition_off_check = bol_check and bol_r_check

    # 5. 中间值检查: D=0.5
    eta_mid, r_mid, pmax_mid = hp_func(0.5)
    eta_mid_expected = 1.0 / (1.0 + fit.get('specific_energy_increment_eol', 0.1) * 0.5)
    mid_check = abs(eta_mid - eta_mid_expected) < 1e-6

    # 6. 比例关系: P_max(D) ∝ eta(D)
    pmax_proportional = abs(pmax_bol * eta_mid / eta_bol - pmax_mid) < 1e-4

    report = {
        'bol_check': bol_check,
        'bol_r_check': bol_r_check,
        'bol_pmax_check': bol_pmax_check,
        'eol_check': eol_check,
        'eol_eta_in_range': eol_eta_in_range,
        'eta_monotonic_decreasing': bool(eta_monotonic),
        'r_ohm_monotonic_increasing': bool(r_monotonic),
        'coalition_off_check': coalition_off_check,
        'mid_check': mid_check,
        'pmax_proportional': pmax_proportional,
        'pass': bool(bol_check and eol_check and eol_eta_in_range
                      and eta_monotonic and r_monotonic
                      and coalition_off_check and mid_check
                      and pmax_proportional),
        'values': {
            'D=0': {'eta_frac': round(eta_bol, 6), 'R_ohm_Ohm_cm2': round(r_bol, 6),
                     'P_max_kW': round(pmax_bol, 4)},
            'D=0.5': {'eta_frac': round(eta_mid, 6), 'R_ohm_Ohm_cm2': round(r_mid, 6),
                       'P_max_kW': round(pmax_mid, 4)},
            'D=1.0': {'eta_frac': round(eta_eol, 6), 'R_ohm_Ohm_cm2': round(r_eol, 6),
                       'P_max_kW': round(pmax_eol, 4)},
            'eta_eol_expected': round(eta_eol_expected, 6),
        },
    }
    return report


if __name__ == "__main__":
    print("=" * 60)
    print("hp_el_lite — PEMEL健康-性能映射模块")
    print("=" * 60)

    # 1. 加载EL BOL数据
    print("\n[1] 加载EL BOL参数 (NAICNO数字孪生)...")
    el_data = _load_el_bol_data()
    print(f"  数据来源: {el_data['source'][:50]}...")
    print(f"  模型类型: {el_data['model_type']}")
    print(f"  BOL V_cell: {el_data['v_cell_bol_V']}V")
    print(f"  BOL eta_LHV: {el_data['eta_lhv_bol']}")
    print(f"  BOL R_ohm: {el_data['r_ohm_bol_ohm_cm2']} Ohm·cm²")
    print(f"  训练后参数: {el_data['trained_parameters'].get('source', 'N/A')}")
    print(f"  证据等级: {el_data['evidence_level']}")

    # 2. 拟合
    print("\n[2] 拟合EL退化模型...")
    fit_result = fit_el_aging(el_data)
    print(f"  V_cell_BOL: {fit_result['v_cell_bol_V']}V")
    print(f"  eta_LHV_BOL: {fit_result['eta_lhv_bol']}")
    print(f"  eta_LHV_EOL: {fit_result['eta_lhv_eol']} (10%比能耗增量)")
    print(f"  alpha_R (R_ohm增长率): {fit_result['alpha_r']}")
    print(f"  R_ohm_BOL: {fit_result['r_ohm_bol_ohm_cm2']} Ohm·cm²")
    print(f"  数据来源: {fit_result['data_source']}")
    print(f"  外推说明: {fit_result['extrapolation_note']}")

    # 3. 构建健康-性能映射
    print("\n[3] 构建EL健康-性能映射函数...")
    el_bol = {
        'el_system_kw': 35.0,  # 系统级额定功率 (v4.1方案)
        'active_area_cm2': 50.0,
    }
    hp_func = interpolate_hp(fit_result, el_bol)
    print(f"  BOL R_ohm: {hp_func.bol_params['r_ohm_bol_ohm_cm2']} Ohm·cm²")
    print(f"  EL系统额定功率: {hp_func.bol_params['el_system_kw']}kW")
    print(f"  eta_frac(EOL): {hp_func.fit_params['eta_frac_at_eol']}")
    print(f"  R_ohm增长(EOL): {hp_func.fit_params['r_ohm_growth_at_eol']}x BOL")

    # 4. 验证
    print("\n[4] 验证EL健康-性能映射函数...")
    report = validate_hp_func(hp_func, el_bol)
    print(f"  BOL检查 (D=0, eta=1.0): {'PASS' if report['bol_check'] else 'FAIL'}")
    print(f"  EOL检查 (D=1.0): {'PASS' if report['eol_check'] else 'FAIL'}")
    print(f"  EOL eta范围 [0.85,0.95]: {'PASS' if report['eol_eta_in_range'] else 'FAIL'}")
    print(f"  eta单调递减: {'PASS' if report['eta_monotonic_decreasing'] else 'FAIL'}")
    print(f"  R_ohm单调递增: {'PASS' if report['r_ohm_monotonic_increasing'] else 'FAIL'}")
    print(f"  联盟OFF (D=0恒等): {'PASS' if report['coalition_off_check'] else 'FAIL'}")
    print(f"  中间值 (D=0.5): {'PASS' if report['mid_check'] else 'FAIL'}")
    print(f"  P_max∝eta: {'PASS' if report['pmax_proportional'] else 'FAIL'}")
    print(f"  总体验证: {'PASS' if report['pass'] else 'FAIL'}")

    # 5. 打印映射表
    print("\n[5] EL健康-性能映射表:")
    print(f"  {'D':>6s}  {'eta_frac':>10s}  {'eta_abs':>10s}  {'R_ohm':>12s}  {'P_max_kW':>10s}  {'V_cell':>8s}")
    print(f"  {'-'*6}  {'-'*10}  {'-'*10}  {'-'*12}  {'-'*10}  {'-'*8}")
    for d in [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]:
        eta_f, r, pmax = hp_func(d)
        eta_abs = V_REV_LHV_V / (V_CELL_BOL_V * (1 + 0.10 * d))
        v_cell = V_CELL_BOL_V * (1 + 0.10 * d)
        print(f"  {d:6.3f}  {eta_f:10.4f}  {eta_abs:10.4f}  {r:12.6f}  {pmax:10.4f}  {v_cell:8.4f}")

    # 6. 与FC退化对比
    print("\n[6] FC vs EL退化对比:")
    print(f"  {'指标':>20s}  {'FC (hp_fc_lite)':>20s}  {'EL (hp_el_lite)':>20s}")
    print(f"  {'-'*20}  {'-'*20}  {'-'*20}")
    print(f"  {'数据来源':>20s}  {'FC1 1000h老化':>20s}  {'NAICNO数字孪生':>20s}")
    print(f"  {'退化模型':>20s}  {'数据驱动线性':>20s}  {'最小信息假设':>20s}")
    print(f"  {'eta_frac(EOL)':>20s}  {0.90:>20.4f}  {float(1.0/(1.0+0.10)):>20.4f}")
    print(f"  {'eta_abs(EOL)':>20s}  {'~0.554':>20s}  {fit_result['eta_lhv_eol']:>20.4f}")
    print(f"  {'R增长(EOL)':>20s}  {'1.054x':>20s}  {1+fit_result['alpha_r']:>20.3f}x")
    print(f"  {'t_EOL估计':>20s}  {'2842h':>20s}  {'~40000h(文献)':>20s}")
    print(f"  {'退化方向':>20s}  {'V降(→η降)':>20s}  {'V升(→η降)':>20s}")

    print(f"\n状态: hp_el_lite 函数体已实现")
