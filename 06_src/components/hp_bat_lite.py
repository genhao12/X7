# -*- coding: utf-8 -*-
"""
M2 hp_bat_lite — MVP BAT健康-性能映射模块
功能：从280Ah HPPC数据拟合BAT健康-性能映射函数D->(Q, R0, P_max)
依赖：M1 data_lite
输入：data_dict, mvp_bol_parameters.json
输出：hp_bat_map (D -> (Q_fraction, R0_Ohm, P_max_kW))

证据等级与数据来源分离:
    - 280Ah HPPC (LIC_hppc_analysis.csv): A级主约束 — R0(T,SoC) BOL + R0(cycle)老化
    - 280Ah aging (LIC_aging_analysed.csv): A级 — Q(cycle)容量退化 + R0(cycle)阻抗增长, 15时点
    - 280Ah entropy (LIC_entropy_analysis.csv): A级 — OCV(SoC) 5温度点
    - 250Ah BLAST-Lite: A级容量约束 — q(t,EFC)容量衰减模型(无阻抗输出)
    - Sony/Murata 3Ah BLAST: 仅先验 — r归一化参考(不用于R(D)推断)
    - A123 OCV: 仅BOL — R(T,SoC)参考(不含老化)

D定义: D = 1 - (SoH - SoH_EOL)/(1 - SoH_EOL), SoH_EOL=0.8
    D=0 -> BOL (SoH=1.0)
    D=1.0 -> EOL (SoH=0.8)
"""
import json
import csv
import numpy as np
from typing import Dict, Any, Tuple, Callable
from pathlib import Path
from scipy.interpolate import interp1d


# === 数据路径 ===
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LFP_DATA_DIR = PROJECT_ROOT / "数据文献" / "280 Ah方形LFP表征与700循环老化数据（）" / "_extracted"
HPPC_CSV = LFP_DATA_DIR / "capacity_hppc_entropy" / "LIC_hppc_analysis.csv"
CAPACITY_CSV = LFP_DATA_DIR / "capacity_hppc_entropy" / "LIC_capacity_analysis.csv"
ENTROPY_CSV = LFP_DATA_DIR / "capacity_hppc_entropy" / "LIC_entropy_analysis.csv"
AGING_CSV = LFP_DATA_DIR / "LIC_aging" / "LIC_aging_analysed.csv"
BOL_PARAMS_PATH = PROJECT_ROOT / "05_config" / "mvp_bol_parameters.json"

# BLAST-Lite路径
BLAST_LITE_PATH = PROJECT_ROOT / "数据文献" / "BLAST-Lite-main"

# 退化参数
# SOH_EOL从配置文件读取, 回退默认0.8 (mvp_c1_config.json battery_constraints.soh_eol)
def _load_soh_eol_from_config() -> float:
    """从mvp_c1_config.json读取soh_eol, 失败时回退0.8。"""
    try:
        config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return float(cfg['battery_constraints']['soh_eol'])
    except Exception:
        return 0.8

SOH_EOL = _load_soh_eol_from_config()


def _load_280ah_data() -> Dict[str, Any]:
    """加载280Ah LFP全部表征与老化数据。"""
    data = {}

    # 1. BOL HPPC: R0(T, SoC) — 5温度×10SoC = 50点
    hppc_rows = []
    with open(HPPC_CSV, 'r') as f:
        for r in csv.DictReader(f):
            hppc_rows.append({
                'T_C': float(r['Temperature_C']),
                'SoC': float(r['SoC']),
                'R0_Ohm': float(r['R0_Ohm']),
                'R1_Ohm': float(r['R1_Ohm']),
                'C1_F': float(r['C1_F']),
            })
    data['bol_hppc'] = hppc_rows

    # 2. 容量-温度: Capacity(T) — 5温度点
    cap_rows = []
    with open(CAPACITY_CSV, 'r') as f:
        for r in csv.DictReader(f):
            cap_rows.append({
                'T_C': float(r['Temperature_C']),
                'Capacity_Ah': float(r['Capacity_Ah']),
            })
    data['capacity_temp'] = cap_rows

    # 3. OCV-SoC: OCV(SoC, T) — 5SoC×5温度
    entropy_rows = []
    with open(ENTROPY_CSV, 'r') as f:
        for r in csv.DictReader(f):
            entropy_rows.append({
                'SoC': float(r['SoC']),
                'dU_dT_V_K': float(r['Entropy_coefficient_V_K']),
                'OCV_45C_V': float(r['Voltage_at_45C_V']),
                'OCV_35C_V': float(r['Voltage_at_35C_V']),
                'OCV_25C_V': float(r['Voltage_at_25C_V']),
                'OCV_15C_V': float(r['Voltage_at_15C_V']),
                'OCV_5C_V': float(r['Voltage_at_5C_V']),
            })
    data['ocv_soc'] = entropy_rows

    # 4. 老化数据: Q(cycle), R0(cycle) — 15时点(0~700 cycles)
    aging_rows = []
    with open(AGING_CSV, 'r') as f:
        for r in csv.DictReader(f):
            cycles = float(r['Total_cycles'])
            cap_ah = float(r['Capacity_Ah'])
            r0 = float(r['Ohmic_res_Ohm'])
            soh = float(r['SoH'])
            aging_rows.append({
                'cycles': cycles,
                'Capacity_Ah': cap_ah,
                'R0_Ohm': r0,
                'SoH': soh,
            })
    data['aging'] = aging_rows

    # 5. BOL参考值
    data['bol_capacity_ah'] = aging_rows[0]['Capacity_Ah']  # 301.58 Ah @ 0 cycles
    data['bol_r0_ohm'] = aging_rows[0]['R0_Ohm']            # 0.000299 Ohm @ 0 cycles
    data['bol_soh'] = aging_rows[0]['SoH']                  # 1.0

    return data


def _soh_to_d(soh: float, soh_eol: float = SOH_EOL) -> float:
    """SoH -> D 退化程度映射。D=1-(SoH-SoH_EOL)/(1-SoH_EOL)"""
    return 1.0 - (soh - soh_eol) / (1.0 - soh_eol)


def _d_to_soh(d: float, soh_eol: float = SOH_EOL) -> float:
    """D -> SoH 逆映射。SoH = SoH_EOL + (1-SoH_EOL)*(1-D)"""
    return soh_eol + (1.0 - soh_eol) * (1.0 - d)


def fit_hppc(hppc_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从280Ah HPPC数据拟合OCV-SoC和R0(T, SoC, D)映射。

    Args:
        hppc_data: 280Ah HPPC数据, 包含
            - capacity_curve: (cycle, capacity_fraction, Ohmic_res_Ohm) — 16老化时点
            - bol_hppc: (SoC, OCV_V, R0_Ohm) — BOL 5温度x10SoC
            - aging_hppc: (cycle, SoC, OCV_V, R0_Ohm) — 老化时点HPPC

    Returns:
        fit_result: dict, 包含
            - ocv_soc_func: callable, SoC -> OCV (V)
            - r0_func: callable, (T, SoC, D) -> R0 (Ohm)
            - capacity_decay_func: callable, D -> Q_fraction
            - r2_ocv: float, OCV拟合R²
            - r2_r0: float, R0拟合R²
            - n_aging_points: int, 老化时点数

    Pass标准: R² >= 0.8
    """
    # 如果传入空dict, 自动加载280Ah数据
    if not hppc_data:
        hppc_data = _load_280ah_data()

    # === 1. OCV(SoC) 拟合 — 用25°C数据 ===
    ocv_data = hppc_data['ocv_soc']
    soc_pts = np.array([d['SoC'] for d in ocv_data])
    ocv_pts = np.array([d['OCV_25C_V'] for d in ocv_data])
    # 线性插值 (5个SoC点, 范围0.1~0.9)
    ocv_soc_func = interp1d(soc_pts, ocv_pts, kind='linear',
                            fill_value=(ocv_pts[0], ocv_pts[-1]),
                            bounds_error=False)
    # R²: 插值在数据点上精确, R²=1.0
    ocv_pred = ocv_soc_func(soc_pts)
    ss_res = np.sum((ocv_pts - ocv_pred) ** 2)
    ss_tot = np.sum((ocv_pts - np.mean(ocv_pts)) ** 2)
    r2_ocv = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    # === 2. R0(T, SoC) BOL 拟合 — 二维插值 ===
    hppc = hppc_data['bol_hppc']
    T_vals = sorted(set(d['T_C'] for d in hppc))       # [15,20,25,30,35]
    soc_vals = sorted(set(d['SoC'] for d in hppc))      # [0.05,...,0.95]
    # 构建 R0 矩阵 (T_idx, SoC_idx)
    R0_grid = np.zeros((len(T_vals), len(soc_vals)))
    for d in hppc:
        ti = T_vals.index(d['T_C'])
        si = soc_vals.index(d['SoC'])
        R0_grid[ti, si] = d['R0_Ohm']

    # BOL R0 在 25°C, SoC=0.5 的参考值 (用于老化缩放)
    t_ref_idx = T_vals.index(25.0)
    soc_ref_idx = min(range(len(soc_vals)), key=lambda i: abs(soc_vals[i] - 0.5))
    r0_bol_ref = R0_grid[t_ref_idx, soc_ref_idx]

    def r0_bol_func(T_C, SoC):
        """BOL R0(T, SoC) 二维线性插值: 先对温度插值, 再对SoC插值。"""
        # 对每个SoC列, 在温度维度插值得到 R0(T_C, :)
        r0_at_T = np.array([np.interp(T_C, T_vals, R0_grid[:, j])
                            for j in range(len(soc_vals))])
        # 再在SoC维度插值
        return float(np.interp(SoC, soc_vals, r0_at_T))
    # R²: 插值在数据点上精确
    r2_r0_bol = 1.0

    # === 3. Q(D) 容量衰减拟合 — 从aging数据 ===
    aging = hppc_data['aging']
    cycles_arr = np.array([d['cycles'] for d in aging])
    cap_arr = np.array([d['Capacity_Ah'] for d in aging])
    soh_arr = np.array([d['SoH'] for d in aging])
    r0_aging_arr = np.array([d['R0_Ohm'] for d in aging])

    # 转换为D坐标
    d_arr = np.array([_soh_to_d(s) for s in soh_arr])
    q_frac_arr = soh_arr / soh_arr[0]  # 容量保持率 (相对于BOL)

    # Q(D) 线性拟合: Q_frac = 1 - beta_Q * D
    # 280Ah数据: D范围 0~0.354, 外推到D=1.0时 Q_frac应接近SoH_EOL=0.8
    # 线性回归
    coeffs_q = np.polyfit(d_arr, q_frac_arr, 1)
    beta_q = -coeffs_q[0]  # Q(D) = 1 - beta_q * D

    def capacity_decay_func(D):
        """D -> Q_fraction (容量保持率)。"""
        D = np.clip(D, 0.0, 1.0)
        return 1.0 - beta_q * D

    # R² for Q(D) fit
    q_pred = capacity_decay_func(d_arr)
    ss_res_q = np.sum((q_frac_arr - q_pred) ** 2)
    ss_tot_q = np.sum((q_frac_arr - np.mean(q_frac_arr)) ** 2)
    r2_q = 1.0 - ss_res_q / ss_tot_q if ss_tot_q > 0 else 1.0

    # === 4. R0(D) 阻抗增长拟合 — 从aging数据 ===
    # R0_growth(D) = R0(D) / R0_BOL
    # 注意: 280Ah数据呈现浴缸曲线 — 0~150 cycles阻抗先降(活化期), 200+ cycles上升(退化期)
    # 仅用退化期(200+ cycles)拟合斜率, 保证单调递增且BOL=1.0
    r0_growth_arr = r0_aging_arr / r0_aging_arr[0]

    # 筛选退化期数据 (cycles >= 200, 即 R0 开始单调上升的段)
    aging_mask = cycles_arr >= 200
    d_aging = d_arr[aging_mask]
    r0_growth_aging = r0_growth_arr[aging_mask]

    # 线性拟合退化期: R0_growth = 1 + alpha_R * D
    # 强制过 (0, 1.0): alpha_R = mean((r0_growth-1)/D) for D>0
    alpha_r = float(np.mean((r0_growth_aging - 1.0) / d_aging))

    def r0_growth_func(D):
        """D -> R0增长因子 (相对于BOL, 仅退化期斜率)。"""
        D = np.clip(D, 0.0, 1.0)
        return 1.0 + alpha_r * D

    # R² for R0(D) fit (仅退化段)
    r0_pred_aging = r0_growth_func(d_aging)
    ss_res_r0 = np.sum((r0_growth_aging - r0_pred_aging) ** 2)
    ss_tot_r0 = np.sum((r0_growth_aging - np.mean(r0_growth_aging)) ** 2)
    r2_r0 = 1.0 - ss_res_r0 / ss_tot_r0 if ss_tot_r0 > 0 else 1.0

    # === 5. 组合 R0(T, SoC, D) ===
    def r0_func(T_C, SoC, D):
        """
        R0(T, SoC, D) = R0_BOL(T, SoC) * R0_growth(D)
        BOL阻抗由280Ah HPPC 5温度×10SoC插值
        老化增长由280Ah aging 15时点线性拟合
        """
        r0_bol = r0_bol_func(T_C, SoC)
        growth = r0_growth_func(D)
        return r0_bol * growth

    return {
        'ocv_soc_func': ocv_soc_func,
        'r0_func': r0_func,
        'r0_bol_func': r0_bol_func,
        'r0_growth_func': r0_growth_func,
        'capacity_decay_func': capacity_decay_func,
        'r2_ocv': round(r2_ocv, 4),
        'r2_r0': round(r2_r0, 4),
        'r2_q': round(r2_q, 4),
        'r2_r0_bol': round(r2_r0_bol, 4),
        'n_aging_points': len(aging),
        'beta_q': round(beta_q, 4),
        'alpha_r': round(alpha_r, 4),
        'r0_bol_ref_ohm': round(r0_bol_ref, 8),
        'bol_capacity_ah': round(hppc_data['bol_capacity_ah'], 2),
        'd_range_data': [round(float(d_arr[0]), 4), round(float(d_arr[-1]), 4)],
        'extrapolation_note': f'280Ah数据覆盖D={d_arr[0]:.3f}~{d_arr[-1]:.3f}, '
                              f'D>{d_arr[-1]:.3f}为线性外推',
    }


def interpolate_hp(fit_result: Dict[str, Any],
                   bol_params: Dict[str, Any]) -> Callable[[float], Tuple[float, float, float]]:
    """
    构建健康-性能映射函数 D -> (Q_fraction, R0_Ohm, P_max_kW)。

    Args:
        fit_result: fit_hppc()的输出
        bol_params: BOL参数(来自mvp_bol_parameters.json的bat相关部分)

    Returns:
        hp_func: callable, D(0~1) -> (Q_frac, R0_Ohm, P_max_kW)
            Q_frac: 容量保持率(1.0 at BOL, 0.8 at EOL)
            R0_Ohm: 内阻(Ohm, BOL值+退化增量)
            P_max_kW: 最大可用功率(kW, 容量缩减后)

    注意:
            - D=0时返回BOL值(Q=1.0, R0=BOL_R0, P_max=rated)
            - D=1.0时返回EOL值(Q=0.8, R0=BOL_R0*(1+alpha_R), P_max=rated*0.8)
            - 中间D值线性插值
    """
    # 从fit_result提取拟合函数
    capacity_decay_func = fit_result['capacity_decay_func']
    r0_growth_func = fit_result['r0_growth_func']
    r0_bol_func = fit_result['r0_bol_func']
    alpha_r = fit_result['alpha_r']
    beta_q = fit_result['beta_q']

    # BOL参数: 25°C, SoC=0.5 的参考阻抗
    r0_bol = r0_bol_func(25.0, 0.5)

    # BAT额定参数 (来自c1_config, 由调用方传入)
    bat_kwh = bol_params.get('bat_kwh', 135.0)
    bat_voltage = bol_params.get('bat_voltage_nominal', 3.2 * 48)  # 48串LFP ~153.6V
    bat_rated_kw = bol_params.get('bat_rated_kw', bat_kwh * 0.5)   # 0.5C = 67.5kW

    def hp_func(D: float) -> Tuple[float, float, float]:
        """
        健康-性能映射: D -> (Q_frac, R0_Ohm, P_max_kW)

        Args:
            D: 退化程度 [0, 1], 0=BOL, 1=EOL

        Returns:
            (Q_frac, R0_Ohm, P_max_kW)
        """
        D = float(np.clip(D, 0.0, 1.0))

        # 容量保持率: Q(D) = 1 - beta_q * D
        q_frac = float(capacity_decay_func(D))

        # 阻抗: R0(D) = R0_BOL * (1 + alpha_r * D)
        r0_ohm = float(r0_bol * r0_growth_func(D))

        # 最大可用功率: P_max(D) = P_rated * Q(D)
        # (容量缩减后, 可持续放电功率按比例降低)
        p_max_kw = float(bat_rated_kw * q_frac)

        return (q_frac, r0_ohm, p_max_kw)

    # 附加元数据
    hp_func.bol_params = {
        'r0_bol_ohm': round(r0_bol, 8),
        'bat_rated_kw': bat_rated_kw,
        'bat_kwh': bat_kwh,
        'bat_voltage_nominal': bat_voltage,
    }
    hp_func.fit_params = {
        'beta_q': beta_q,
        'alpha_r': alpha_r,
        'soh_eol': SOH_EOL,
        'q_at_eol': round(1.0 - beta_q * 1.0, 4),
        'r0_growth_at_eol': round(1.0 + alpha_r * 1.0, 4),
    }

    return hp_func


def validate_hp_func(hp_func: Callable, bol_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证健康-性能映射函数的边界行为。

    Args:
        hp_func: interpolate_hp()返回的函数
        bol_params: BOL参数

    Returns:
        report: dict, 包含
            - bol_check: bool, D=0时Q=1.0
            - eol_check: bool, D=1.0时Q≈0.8
            - monotonic: bool, Q(D)单调递减
            - pass: bool, 全部检查通过
    """
    fit = getattr(hp_func, 'fit_params', {})
    bol = getattr(hp_func, 'bol_params', {})

    # 1. BOL检查: D=0
    q_bol, r0_bol, pmax_bol = hp_func(0.0)
    bol_check = abs(q_bol - 1.0) < 1e-6
    bol_r0_check = abs(r0_bol - bol.get('r0_bol_ohm', r0_bol)) < 1e-6
    bol_pmax_check = abs(pmax_bol - bol.get('bat_rated_kw', pmax_bol)) < 1e-6

    # 2. EOL检查: D=1.0
    q_eol, r0_eol, pmax_eol = hp_func(1.0)
    q_eol_expected = 1.0 - fit.get('beta_q', 0.2) * 1.0
    eol_check = abs(q_eol - q_eol_expected) < 1e-6
    # Q(EOL)应接近SoH_EOL=0.8 (线性外推时)
    eol_q_in_range = 0.75 <= q_eol <= 0.85

    # 3. 单调性检查: Q(D)单调递减, R0(D)单调递增
    d_test = np.linspace(0, 1, 21)
    q_trace = np.array([hp_func(d)[0] for d in d_test])
    r0_trace = np.array([hp_func(d)[1] for d in d_test])
    q_monotonic = np.all(np.diff(q_trace) <= 1e-10)
    r0_monotonic = np.all(np.diff(r0_trace) >= -1e-10)

    # 4. 联盟OFF检查: D=0时g_i(0)应返回BOL值(恒等)
    coalition_off_check = bol_check and bol_r0_check

    # 5. 中间值检查: D=0.5
    q_mid, r0_mid, pmax_mid = hp_func(0.5)
    q_mid_expected = 1.0 - fit.get('beta_q', 0.2) * 0.5
    mid_check = abs(q_mid - q_mid_expected) < 1e-6

    # 6. 比例关系检查: P_max(D) = P_rated * Q(D)
    pmax_proportional = abs(pmax_bol * q_mid / q_bol - pmax_mid) < 1e-4

    report = {
        'bol_check': bol_check,
        'bol_r0_check': bol_r0_check,
        'bol_pmax_check': bol_pmax_check,
        'eol_check': eol_check,
        'eol_q_in_range': eol_q_in_range,
        'q_monotonic_decreasing': bool(q_monotonic),
        'r0_monotonic_increasing': bool(r0_monotonic),
        'coalition_off_check': coalition_off_check,
        'mid_check': mid_check,
        'pmax_proportional': pmax_proportional,
        'pass': bool(bol_check and eol_check and eol_q_in_range
                      and q_monotonic and r0_monotonic
                      and coalition_off_check and mid_check
                      and pmax_proportional),
        'values': {
            'D=0': {'Q': round(q_bol, 6), 'R0_Ohm': round(r0_bol, 8), 'P_max_kW': round(pmax_bol, 4)},
            'D=0.5': {'Q': round(q_mid, 6), 'R0_Ohm': round(r0_mid, 8), 'P_max_kW': round(pmax_mid, 4)},
            'D=1.0': {'Q': round(q_eol, 6), 'R0_Ohm': round(r0_eol, 8), 'P_max_kW': round(pmax_eol, 4)},
            'q_eol_expected': round(q_eol_expected, 6),
        },
    }
    return report


if __name__ == "__main__":
    print("=" * 60)
    print("M2 hp_bat_lite — BAT健康-性能映射模块")
    print("=" * 60)

    # 1. 加载280Ah数据
    print("\n[1] 加载280Ah LFP表征与老化数据...")
    hppc_data = _load_280ah_data()
    print(f"  BOL HPPC: {len(hppc_data['bol_hppc'])}点 (5温度×10SoC)")
    print(f"  容量-温度: {len(hppc_data['capacity_temp'])}点")
    print(f"  OCV-SoC: {len(hppc_data['ocv_soc'])}点 (5SoC×5温度)")
    print(f"  老化数据: {len(hppc_data['aging'])}时点 (0~700 cycles)")
    print(f"  BOL容量: {hppc_data['bol_capacity_ah']} Ah")
    print(f"  BOL阻抗: {hppc_data['bol_r0_ohm']} Ohm")

    # 2. 拟合HPPC
    print("\n[2] 拟合HPPC...")
    fit_result = fit_hppc(hppc_data)
    print(f"  OCV(SoC) R²: {fit_result['r2_ocv']}")
    print(f"  R0(T,SoC) BOL R²: {fit_result['r2_r0_bol']}")
    print(f"  Q(D) R²: {fit_result['r2_q']}")
    print(f"  R0(D) R²: {fit_result['r2_r0']}")
    print(f"  beta_Q (容量退化率): {fit_result['beta_q']}")
    print(f"  alpha_R (阻抗增长率): {fit_result['alpha_r']}")
    print(f"  数据D范围: {fit_result['d_range_data']}")
    print(f"  外推说明: {fit_result['extrapolation_note']}")
    pass_r2 = (fit_result['r2_ocv'] >= 0.8 and fit_result['r2_q'] >= 0.8
               and fit_result['r2_r0'] >= 0.8)
    print(f"  R²>=0.8: {'PASS' if pass_r2 else 'FAIL'}")

    # 3. 构建健康-性能映射
    print("\n[3] 构建健康-性能映射函数...")
    # 读取BOL参数
    with open(BOL_PARAMS_PATH, 'r', encoding='utf-8') as f:
        bol_full = json.load(f)
    # 构造BAT BOL参数
    bat_bol = {
        'bat_kwh': 135.0,
        'bat_voltage_nominal': 3.2 * 48,  # 48串LFP
        'bat_rated_kw': 135.0 * 0.5,      # 0.5C = 67.5kW
    }
    hp_func = interpolate_hp(fit_result, bat_bol)
    print(f"  BOL R0: {hp_func.bol_params['r0_bol_ohm']} Ohm")
    print(f"  BAT额定功率: {hp_func.bol_params['bat_rated_kw']} kW (0.5C)")
    print(f"  Q(EOL): {hp_func.fit_params['q_at_eol']}")
    print(f"  R0增长(EOL): {hp_func.fit_params['r0_growth_at_eol']}x BOL")

    # 4. 验证
    print("\n[4] 验证健康-性能映射函数...")
    report = validate_hp_func(hp_func, bat_bol)
    print(f"  BOL检查 (D=0, Q=1.0): {'PASS' if report['bol_check'] else 'FAIL'}")
    print(f"  EOL检查 (D=1.0): {'PASS' if report['eol_check'] else 'FAIL'}")
    print(f"  EOL Q范围 [0.75,0.85]: {'PASS' if report['eol_q_in_range'] else 'FAIL'}")
    print(f"  Q单调递减: {'PASS' if report['q_monotonic_decreasing'] else 'FAIL'}")
    print(f"  R0单调递增: {'PASS' if report['r0_monotonic_increasing'] else 'FAIL'}")
    print(f"  联盟OFF (D=0恒等): {'PASS' if report['coalition_off_check'] else 'FAIL'}")
    print(f"  中间值 (D=0.5): {'PASS' if report['mid_check'] else 'FAIL'}")
    print(f"  P_max∝Q: {'PASS' if report['pmax_proportional'] else 'FAIL'}")
    print(f"  总体验证: {'PASS' if report['pass'] else 'FAIL'}")

    # 5. 打印映射表
    print("\n[5] 健康-性能映射表:")
    print(f"  {'D':>6s}  {'Q_frac':>8s}  {'R0_Ohm':>12s}  {'P_max_kW':>10s}  {'SoH':>6s}")
    print(f"  {'-'*6}  {'-'*8}  {'-'*12}  {'-'*10}  {'-'*6}")
    for d in [0.0, 0.1, 0.2, 0.3, 0.354, 0.5, 0.7, 0.9, 1.0]:
        q, r0, pmax = hp_func(d)
        soh = _d_to_soh(d)
        marker = ' <-数据边界' if abs(d - 0.354) < 0.01 else ''
        print(f"  {d:6.3f}  {q:8.4f}  {r0:12.8f}  {pmax:10.4f}  {soh:6.4f}{marker}")

    print(f"\n状态: M2 hp_bat_lite 函数体已实现")
