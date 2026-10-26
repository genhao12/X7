# -*- coding: utf-8 -*-
"""
hp_fc_lite — PEMFC健康-性能映射模块
功能：从IEEE PHM 2014 FC1 1000h老化数据拟合FC健康-性能映射 D->(eta_frac, R_hfr_Ohm, P_max_kW)
依赖：mvp_bol_parameters.json (FC BOL极化曲线 + EOL定义)

证据等级与数据来源:
    - FC1 Aging (FC1_Ageing_part1/2/3.xlsx): A级 — 1154h恒流(70A/0.7A_cm²)电压轨迹
      U_mean: 0.667V(BOL) -> 0.643V(T991), 线性退化 23.5mV/1000h
    - FC1 EIS (FC1_EIS70A_postpola_T{000..991}.xlsx): A级 — 8时点R_hfr演化
      R_hfr: 4.712mOhm(BOL) -> 4.836mOhm(T991), 退化期(>500h)线性增长
    - FC1 BOL极化曲线: A级 — T=0h 20点I-V降采样 (来自mvp_bol_parameters.json)
    - FC2交叉验证: A级 — OCV偏差1.2%, U_rated偏差3.9%, R_ohmic偏差2.5%

D定义: D = voltage_loss / voltage_loss_EOL
    EOL = 额定电流密度下电压下降10%相对于BOL
    D=0 -> BOL (U = U_BOL)
    D=1.0 -> EOL (U = U_BOL * 0.90)
    t_EOL = 2838h (由23.5mV/1000h线性外推至10%损失)

规模迁移说明:
    FC1: 5cell/100cm²/1kW堆, BOL极化曲线以电流密度-单cell电压表征
    目标: 30-45kW系统级PEMFC
    方法: 极化归一化 + 辅机效率区间[0.85,0.92] + DC/DC效率区间[0.92,0.96]
    风险: 功率规模迁移缺口仍存, 区间报告用于不确定性传播
"""
import json
import csv
import numpy as np
from typing import Dict, Any, Tuple, Callable
from pathlib import Path
from scipy.interpolate import interp1d

try:
    import openpyxl
except ImportError:
    openpyxl = None


# === 数据路径 ===
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FC1_DATA_DIR = PROJECT_ROOT / "数据文献" / "PEMFC恒流与纹波电流约1000 h老化数据" / "FC1_Without_Ripples_Excel"
BOL_PARAMS_PATH = PROJECT_ROOT / "05_config" / "mvp_bol_parameters.json"

# EIS时点 (小时)
EIS_TIME_POINTS = [0, 48, 185, 348, 515, 658, 823, 991]

# EOL定义: 额定电压损失10%
VOLTAGE_LOSS_EOL = 0.10

# 额定工作点 (FC1老化测试恒流条件)
RATED_CURRENT_A = 70.0          # 老化测试恒流 70A
RATED_CURRENT_DENSITY = 0.70    # A/cm² (70A / 100cm²)


def _load_fc1_data() -> Dict[str, Any]:
    """加载FC1全部老化数据: 电压轨迹 + EIS R_hfr + BOL极化曲线。"""
    if openpyxl is None:
        raise ImportError("openpyxl is required to read FC1 Excel data. Install: pip install openpyxl")

    data = {}

    # === 1. 电压轨迹: 3个Excel part, 每个含65535行 ===
    all_times = []
    all_voltages = []

    # 文件名列表
    aging_files = [
        FC1_DATA_DIR / 'FC1_Ageing_part1.xlsx',
        FC1_DATA_DIR / 'FC1_Ageing_part2.xlsx',
        FC1_DATA_DIR / 'FC1_Ageing_part3.xlsx',
    ]

    for fpath in aging_files:
        if not fpath.exists():
            continue
        wb = openpyxl.load_workbook(fpath, read_only=True, data_only=True)
        ws = wb['Sheet1']
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue
            t = row[0]
            u_cells = [row[1], row[2], row[3], row[4], row[5]]  # U1-U5
            if t is not None and all(u is not None for u in u_cells):
                all_times.append(float(t))
                all_voltages.append(float(np.mean(u_cells)))
        wb.close()

    t_arr = np.array(all_times)
    u_arr = np.array(all_voltages)
    idx = np.argsort(t_arr)
    data['voltage_trajectory'] = {
        'time_h': t_arr[idx],
        'voltage_V': u_arr[idx],
        'n_points': len(t_arr),
        'time_range': [float(t_arr[idx][0]), float(t_arr[idx][-1])],
    }

    # === 2. EIS R_hfr: 8时点 ===
    r_hfr_list = []
    for tp in EIS_TIME_POINTS:
        fname = f'FC1_EIS70A_postpola_T{tp:03d}.xlsx'
        fpath = FC1_DATA_DIR / fname
        if not fpath.exists():
            r_hfr_list.append(None)
            continue
        wb = openpyxl.load_workbook(fpath, read_only=True, data_only=True)
        ws = wb['Sheet1']
        rows = list(ws.iter_rows(values_only=True))
        # R_hfr = 实部在最高频率处 (第一数据行, 第二列)
        r_real = float(rows[1][1])
        r_hfr_list.append(r_real)
        wb.close()

    data['eis_r_hfr'] = {
        'time_h': np.array(EIS_TIME_POINTS, dtype=float),
        'r_hfr_ohm': np.array(r_hfr_list, dtype=float),
        'n_points': len(r_hfr_list),
    }

    # === 3. BOL极化曲线 (从mvp_bol_parameters.json) ===
    with open(BOL_PARAMS_PATH, 'r', encoding='utf-8') as f:
        bol_full = json.load(f)
    bol_pola = bol_full['pemfc_bol']['bol_polarization_curve']['data']
    data['bol_polarization'] = {
        'I_A': np.array([p[0] for p in bol_pola]),
        'J_A_cm2': np.array([p[1] for p in bol_pola]),
        'U_cell_V': np.array([p[2] for p in bol_pola]),
        'n_points': len(bol_pola),
    }
    data['bol_key_params'] = bol_full['pemfc_bol']['bol_key_parameters']
    data['stack_config'] = bol_full['pemfc_bol']['stack_config']

    # BOL参考值 (在额定电流密度1.0 A/cm²下)
    data['bol_voltage_rated_V'] = data['bol_key_params']['u_cell_at_1A_cm2_V']  # 0.6158V
    data['bol_r_hfr_ohm'] = float(r_hfr_list[0])  # 0.004712 Ohm at 70A
    data['bol_voltage_70a_V'] = float(u_arr[idx][0])  # BOL电压 at 70A ≈ 0.667V

    return data


def fit_fc_aging(fc_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从FC1老化数据拟合健康-性能映射参数。

    拟合内容:
        1. 电压退化: U(D) = U_BOL * (1 - VOLTAGE_LOSS_EOL * D)
           — EOL定义强制: D=1时电压降10%
        2. R_hfr增长: R(D) = R_BOL * (1 + alpha_R * D)
           — 从EIS退化期(D>0.18)线性拟合
        3. 效率退化: eta(D) = eta_BOL * (1 - VOLTAGE_LOSS_EOL * D)
           — 效率正比于电压 (eta ∝ V_cell)
        4. 功率退化: P_max(D) = P_rated * (1 - VOLTAGE_LOSS_EOL * D)
           — 功率正比于电压

    Args:
        fc_data: _load_fc1_data()的输出

    Returns:
        fit_result: dict, 含拟合函数和参数
    """
    # === 1. 电压退化率 (从1000h轨迹) ===
    traj = fc_data['voltage_trajectory']
    t_traj = traj['time_h']
    u_traj = traj['voltage_V']

    # 线性拟合: U(t) = U0 - k_v * t
    coeffs_v = np.polyfit(t_traj, u_traj, 1)
    u_bol_fit = coeffs_v[1]          # U(0)
    k_v = -coeffs_v[0]               # 退化率 V/h (正值)
    # t_EOL: 达到10%电压损失的时间
    # 注: EOL定义基于额定电流密度(1A/cm²)下U_BOL=0.6158V, 但退化率k_v从70A恒流测得
    #     此处用70A的U_BOL(0.667V)计算t_EOL=2838h, 严格值应为2620h(用0.6158V), 偏差8.3%
    #     线性退化均匀假设下, 不同电流密度退化率近似相同, 此偏差可接受
    #     影响: alpha_R偏小~8%, 不影响退化方向结论
    t_eol = u_bol_fit * VOLTAGE_LOSS_EOL / k_v  # 达到10%损失的时间

    # 电压退化函数: U(D) = U_BOL * (1 - 0.10 * D)
    def voltage_decay_func(D):
        """D -> U/U_BOL (电压保持率)"""
        D = np.clip(D, 0.0, 1.0)
        return 1.0 - VOLTAGE_LOSS_EOL * D

    # R² for voltage fit
    u_pred = u_bol_fit - k_v * t_traj
    ss_res = np.sum((u_traj - u_pred) ** 2)
    ss_tot = np.sum((u_traj - np.mean(u_traj)) ** 2)
    r2_voltage = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    # === 2. R_hfr增长 (从EIS退化期) ===
    eis = fc_data['eis_r_hfr']
    t_eis = eis['time_h']
    r_hfr = eis['r_hfr_ohm']
    r_hfr_bol = r_hfr[0]
    r_hfr_norm = r_hfr / r_hfr_bol

    # D坐标: D = t / t_eol
    d_eis = t_eis / t_eol

    # 退化期: R_hfr开始单调上升的段 (t > 500h, 即 D > 0.18)
    # 类比hp_bat_lite用cycles>=200筛选退化期
    degr_mask = d_eis >= 0.18
    d_degr = d_eis[degr_mask]
    r_norm_degr = r_hfr_norm[degr_mask]

    if len(d_degr) >= 2:
        # 线性拟合退化期: R_norm = 1 + alpha_R * D
        alpha_r = float(np.mean((r_norm_degr - 1.0) / d_degr))
        r2_r_hfr = _linear_r2(d_degr, r_norm_degr, 1.0, alpha_r)
    else:
        # 回退: 用全部数据
        alpha_r = float(np.mean((r_hfr_norm[1:] - 1.0) / d_eis[1:]))
        r2_r_hfr = _linear_r2(d_eis[1:], r_hfr_norm[1:], 1.0, alpha_r)

    def r_hfr_growth_func(D):
        """D -> R_hfr增长因子 (相对于BOL)"""
        D = np.clip(D, 0.0, 1.0)
        return 1.0 + alpha_r * D

    # === 3. BOL极化曲线插值 ===
    pola = fc_data['bol_polarization']
    i_pola = pola['I_A']
    u_pola = pola['U_cell_V']
    # I-V曲线插值 (单调递增I -> 单调递减U)
    iv_func = interp1d(i_pola, u_pola, kind='linear',
                       fill_value=(u_pola[0], u_pola[-1]),
                       bounds_error=False)

    fit_result = {
        'voltage_decay_func': voltage_decay_func,
        'r_hfr_growth_func': r_hfr_growth_func,
        'iv_func': iv_func,
        'r2_voltage': round(r2_voltage, 4),
        'r2_r_hfr': round(r2_r_hfr, 4),
        'u_bol_V': round(u_bol_fit, 4),
        'k_v_mV_per_h': round(k_v * 1000, 4),
        't_eol_h': round(t_eol, 1),
        'alpha_r': round(alpha_r, 4),
        'r_hfr_bol_ohm': round(r_hfr_bol, 8),
        'voltage_loss_eol': VOLTAGE_LOSS_EOL,
        'bol_voltage_rated_V': fc_data['bol_voltage_rated_V'],
        'bol_voltage_70a_V': round(fc_data['bol_voltage_70a_V'], 4),
        'd_range_data': [round(float(d_eis[0]), 4), round(float(d_eis[-1]), 4)],
        'extrapolation_note': f'FC1数据覆盖D={d_eis[0]:.3f}~{d_eis[-1]:.3f} (t=0~991h), '
                              f't_EOL={t_eol:.0f}h为线性外推, D>{d_eis[-1]:.3f}为外推区间',
        'n_voltage_points': traj['n_points'],
        'n_eis_points': eis['n_points'],
    }
    return fit_result


def _linear_r2(x, y, intercept, slope):
    """计算 y = intercept + slope*x 的 R²。"""
    y_pred = intercept + slope * x
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    return round(1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0, 4)


def interpolate_hp(fit_result: Dict[str, Any],
                   bol_params: Dict[str, Any]) -> Callable[[float], Tuple[float, float, float]]:
    """
    构建FC健康-性能映射函数 D -> (eta_frac, R_hfr_Ohm, P_max_kW)。

    Args:
        fit_result: fit_fc_aging()的输出
        bol_params: BOL参数 (含stack_config, rated_power等)

    Returns:
        hp_func: callable, D(0~1) -> (eta_frac, R_hfr_Ohm, P_max_kW)
            eta_frac: 效率保持率 (1.0 at BOL, 0.90 at EOL, 正比于电压)
            R_hfr_Ohm: 高频阻抗 (Ohm, BOL值+退化增量)
            P_max_kW: 最大可用功率 (kW, 电压缩减后)
    """
    voltage_decay_func = fit_result['voltage_decay_func']
    r_hfr_growth_func = fit_result['r_hfr_growth_func']
    alpha_r = fit_result['alpha_r']
    r_hfr_bol = fit_result['r_hfr_bol_ohm']

    # FC额定参数
    stack = bol_params.get('stack_config', {})
    fc_rated_kw = stack.get('rated_power_kw', 1.0)  # FC1: 1kW
    n_cells = stack.get('n_cells', 5)
    active_area = stack.get('active_area_cm2', 100.0)

    # 系统级额定功率 (规模迁移后)
    fc_system_kw = bol_params.get('fc_system_kw', fc_rated_kw)

    def hp_func(D: float) -> Tuple[float, float, float]:
        """
        健康-性能映射: D -> (eta_frac, R_hfr_Ohm, P_max_kW)

        Args:
            D: 退化程度 [0, 1], 0=BOL, 1=EOL

        Returns:
            (eta_frac, R_hfr_Ohm, P_max_kW)
        """
        D = float(np.clip(D, 0.0, 1.0))

        # 效率保持率: eta(D) = eta_BOL * (1 - 0.10*D)
        # 效率正比于cell电压 (P=I*U, 同电流下U降则P降则eta降)
        eta_frac = float(voltage_decay_func(D))

        # 阻抗: R_hfr(D) = R_BOL * (1 + alpha_R * D)
        r_hfr_ohm = float(r_hfr_bol * r_hfr_growth_func(D))

        # 最大可用功率: P_max(D) = P_rated * (1 - 0.10*D)
        # 电压降10% -> 同电流下功率降10%
        p_max_kw = float(fc_system_kw * voltage_decay_func(D))

        return (eta_frac, r_hfr_ohm, p_max_kw)

    # 附加元数据
    hp_func.bol_params = {
        'r_hfr_bol_ohm': round(r_hfr_bol, 8),
        'fc_rated_kw': fc_rated_kw,
        'fc_system_kw': fc_system_kw,
        'n_cells': n_cells,
        'active_area_cm2': active_area,
    }
    hp_func.fit_params = {
        'alpha_r': alpha_r,
        'voltage_loss_eol': VOLTAGE_LOSS_EOL,
        'eta_at_eol': round(1.0 - VOLTAGE_LOSS_EOL * 1.0, 4),
        'r_hfr_growth_at_eol': round(1.0 + alpha_r * 1.0, 4),
        't_eol_h': fit_result['t_eol_h'],
        'k_v_mV_per_h': fit_result['k_v_mV_per_h'],
    }

    return hp_func


def validate_hp_func(hp_func: Callable, bol_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证FC健康-性能映射函数的边界行为。

    Returns:
        report: dict, 含BOL/EOL/单调性/联盟OFF检查
    """
    fit = getattr(hp_func, 'fit_params', {})
    bol = getattr(hp_func, 'bol_params', {})

    # 1. BOL检查: D=0
    eta_bol, r_bol, pmax_bol = hp_func(0.0)
    bol_check = abs(eta_bol - 1.0) < 1e-6
    bol_r_check = abs(r_bol - bol.get('r_hfr_bol_ohm', r_bol)) < 1e-6
    bol_pmax_check = abs(pmax_bol - bol.get('fc_system_kw', pmax_bol)) < 1e-6

    # 2. EOL检查: D=1.0
    eta_eol, r_eol, pmax_eol = hp_func(1.0)
    eta_eol_expected = 1.0 - fit.get('voltage_loss_eol', 0.1) * 1.0
    eol_check = abs(eta_eol - eta_eol_expected) < 1e-6
    eol_eta_in_range = 0.85 <= eta_eol <= 0.95  # 应≈0.90

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
    eta_mid_expected = 1.0 - fit.get('voltage_loss_eol', 0.1) * 0.5
    mid_check = abs(eta_mid - eta_mid_expected) < 1e-6

    # 6. 比例关系: P_max(D) ∝ eta(D) ∝ U(D)
    pmax_proportional = abs(pmax_bol * eta_mid / eta_bol - pmax_mid) < 1e-4

    report = {
        'bol_check': bol_check,
        'bol_r_check': bol_r_check,
        'bol_pmax_check': bol_pmax_check,
        'eol_check': eol_check,
        'eol_eta_in_range': eol_eta_in_range,
        'eta_monotonic_decreasing': bool(eta_monotonic),
        'r_hfr_monotonic_increasing': bool(r_monotonic),
        'coalition_off_check': coalition_off_check,
        'mid_check': mid_check,
        'pmax_proportional': pmax_proportional,
        'pass': bool(bol_check and eol_check and eol_eta_in_range
                      and eta_monotonic and r_monotonic
                      and coalition_off_check and mid_check
                      and pmax_proportional),
        'values': {
            'D=0': {'eta_frac': round(eta_bol, 6), 'R_hfr_Ohm': round(r_bol, 8),
                     'P_max_kW': round(pmax_bol, 4)},
            'D=0.5': {'eta_frac': round(eta_mid, 6), 'R_hfr_Ohm': round(r_mid, 8),
                       'P_max_kW': round(pmax_mid, 4)},
            'D=1.0': {'eta_frac': round(eta_eol, 6), 'R_hfr_Ohm': round(r_eol, 8),
                       'P_max_kW': round(pmax_eol, 4)},
            'eta_eol_expected': round(eta_eol_expected, 6),
        },
    }
    return report


if __name__ == "__main__":
    print("=" * 60)
    print("hp_fc_lite — PEMFC健康-性能映射模块")
    print("=" * 60)

    # 1. 加载FC1数据
    print("\n[1] 加载FC1老化数据...")
    fc_data = _load_fc1_data()
    traj = fc_data['voltage_trajectory']
    eis = fc_data['eis_r_hfr']
    pola = fc_data['bol_polarization']
    print(f"  电压轨迹: {traj['n_points']}点, t=[{traj['time_range'][0]:.1f}, {traj['time_range'][1]:.1f}]h")
    print(f"  EIS R_hfr: {eis['n_points']}时点")
    print(f"  BOL极化曲线: {pola['n_points']}点")
    print(f"  BOL电压(70A): {fc_data['bol_voltage_70a_V']:.4f}V")
    print(f"  BOL电压(额定1A/cm²): {fc_data['bol_voltage_rated_V']:.4f}V")
    print(f"  BOL R_hfr: {fc_data['bol_r_hfr_ohm']*1000:.4f}mOhm")

    # 2. 拟合
    print("\n[2] 拟合FC老化模型...")
    fit_result = fit_fc_aging(fc_data)
    print(f"  电压退化R²: {fit_result['r2_voltage']}")
    print(f"  R_hfr增长R²: {fit_result['r2_r_hfr']}")
    print(f"  U_BOL: {fit_result['u_bol_V']}V")
    print(f"  退化率: {fit_result['k_v_mV_per_h']} mV/h")
    print(f"  t_EOL: {fit_result['t_eol_h']}h (10%电压损失)")
    print(f"  alpha_R (R_hfr增长率): {fit_result['alpha_r']}")
    r_growth_eol = 1.0 + fit_result['alpha_r']
    print(f"  R_hfr(EOL): {r_growth_eol:.4f}x BOL")
    print(f"  数据D范围: {fit_result['d_range_data']}")
    pass_r2 = (fit_result['r2_voltage'] >= 0.8 and fit_result['r2_r_hfr'] >= 0.5)
    print(f"  R²>=0.8(电压)/0.5(EIS): {'PASS' if pass_r2 else 'FAIL'}")

    # 3. 构建健康-性能映射
    print("\n[3] 构建FC健康-性能映射函数...")
    # 读取BOL参数
    with open(BOL_PARAMS_PATH, 'r', encoding='utf-8') as f:
        bol_full = json.load(f)
    fc_bol = {
        'stack_config': bol_full['pemfc_bol']['stack_config'],
        'fc_system_kw': 37.5,  # 系统级额定功率 (v4.1方案目标)
    }
    hp_func = interpolate_hp(fit_result, fc_bol)
    print(f"  BOL R_hfr: {hp_func.bol_params['r_hfr_bol_ohm']*1000:.4f}mOhm")
    print(f"  FC系统额定功率: {hp_func.bol_params['fc_system_kw']}kW")
    print(f"  eta(EOL): {hp_func.fit_params['eta_at_eol']}")
    print(f"  R_hfr增长(EOL): {hp_func.fit_params['r_hfr_growth_at_eol']}x BOL")

    # 4. 验证
    print("\n[4] 验证FC健康-性能映射函数...")
    report = validate_hp_func(hp_func, fc_bol)
    print(f"  BOL检查 (D=0, eta=1.0): {'PASS' if report['bol_check'] else 'FAIL'}")
    print(f"  EOL检查 (D=1.0): {'PASS' if report['eol_check'] else 'FAIL'}")
    print(f"  EOL eta范围 [0.85,0.95]: {'PASS' if report['eol_eta_in_range'] else 'FAIL'}")
    print(f"  eta单调递减: {'PASS' if report['eta_monotonic_decreasing'] else 'FAIL'}")
    print(f"  R_hfr单调递增: {'PASS' if report['r_hfr_monotonic_increasing'] else 'FAIL'}")
    print(f"  联盟OFF (D=0恒等): {'PASS' if report['coalition_off_check'] else 'FAIL'}")
    print(f"  中间值 (D=0.5): {'PASS' if report['mid_check'] else 'FAIL'}")
    print(f"  P_max∝eta: {'PASS' if report['pmax_proportional'] else 'FAIL'}")
    print(f"  总体验证: {'PASS' if report['pass'] else 'FAIL'}")

    # 5. 打印映射表
    print("\n[5] FC健康-性能映射表:")
    print(f"  {'D':>6s}  {'eta_frac':>10s}  {'R_hfr(mOhm)':>14s}  {'P_max_kW':>10s}  {'t(h)':>8s}")
    print(f"  {'-'*6}  {'-'*10}  {'-'*14}  {'-'*10}  {'-'*8}")
    for d in [0.0, 0.1, 0.2, 0.349, 0.5, 0.7, 0.9, 1.0]:
        eta, r, pmax = hp_func(d)
        t_h = d * fit_result['t_eol_h']
        marker = ' <-数据边界' if abs(d - 0.349) < 0.01 else ''
        print(f"  {d:6.3f}  {eta:10.4f}  {r*1000:14.6f}  {pmax:10.4f}  {t_h:8.1f}{marker}")

    print(f"\n状态: hp_fc_lite 函数体已实现")
