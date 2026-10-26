# -*- coding: utf-8 -*-
"""
M1 data_lite — MVP数据加载与校验模块
功能：加载UCSD 7天15min数据 + 冻结参数，输出data_dict
依赖：无（链路起点）
输入：load_15min.csv, mvp_c1_config.json
输出：data_dict (15min x 672步)
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Any


def load_data(load_csv_path: str, config_json_path: str,
              year: int = 2017, window_days: int = 7) -> Dict[str, Any]:
    """
    加载UCSD TradeStreet 7天15min数据 + C1配置参数。

    数据源: 64号CSV (PV + building_load, 105216行/3年)
    W1窗口: 该年第1天起7天 × 96步/天 = 672步

    Args:
        load_csv_path: TradeStreet 64号CSV路径
        config_json_path: mvp_c1_config.json 文件路径
        year: 评价年, 默认2017 (2018用于跨年验证)
        window_days: 窗口天数, 默认7 (MVP W1)

    Returns:
        data_dict: 包含以下键
            - data: pd.DataFrame, 列=[timestamp, P_pv_kW, P_load_kW, P_net_kW], 672行
                    P_net_kW = P_load_kW - P_pv_kW (正=亏缺, 负=富余)
            - config: dict, C1配置参数
            - meta: dict, 元数据(时间范围/分辨率/数据点数/数据源/年份)

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 数据点数不等于预期或时间分辨率非15min
    """
    # 1. 读取CSV
    csv_path = Path(load_csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV文件不存在: {csv_path}")

    raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])

    # 2. 读取C1配置
    config_path = Path(config_json_path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 3. 筛选评价年 + 提取W1窗口
    df_year = raw[raw['local_civil_year'] == year].copy()
    if len(df_year) == 0:
        raise ValueError(f"CSV中无{year}年数据, 可用年份: {sorted(raw['local_civil_year'].unique())}")

    expected_points = window_days * 96  # 96步/天 (15min)
    df_window = df_year.head(expected_points).copy()

    if len(df_window) != expected_points:
        raise ValueError(f"W1窗口数据点数={len(df_window)}, 期望={expected_points}")

    # 4. 列名映射 + 计算净负荷
    df_window = df_window.rename(columns={
        'timestamp_local_civil': 'timestamp',
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW',
    })
    df_window['P_net_kW'] = df_window['P_load_kW'] - df_window['P_pv_kW']

    data = df_window[['timestamp', 'P_pv_kW', 'P_load_kW', 'P_net_kW']].copy()
    data.reset_index(drop=True, inplace=True)

    # 5. 验证时间戳连续性
    ts_report = validate_timestamps(data, expected_freq_min=15, expected_points=expected_points)
    if not ts_report['pass']:
        raise ValueError(f"时间戳校验失败: {ts_report}")

    # 6. 验证功率范围
    pv_rated = config.get('capacity', {}).get('PV_kWp', 120.0)
    pv_report = validate_power_range(data, pv_rated, power_column='P_pv_kW',
                                      allow_negative=False)
    load_report = validate_power_range(data, pv_rated * 2, power_column='P_load_kW',
                                       allow_negative=False)

    # 7. 组装返回
    data_dict = {
        'data': data,
        'config': config,
        'meta': {
            'source': 'UCSD TradeStreet pipeline (64号CSV)',
            'year': year,
            'window': f'W1 ({window_days}天)',
            'start': str(data['timestamp'].iloc[0]),
            'end': str(data['timestamp'].iloc[-1]),
            'n_points': len(data),
            'freq_minutes': 15,
            'pv_rated_kWp': pv_rated,
            'pv_max_kW': round(data['P_pv_kW'].max(), 2),
            'load_max_kW': round(data['P_load_kW'].max(), 2),
            'net_min_kW': round(data['P_net_kW'].min(), 2),
            'net_max_kW': round(data['P_net_kW'].max(), 2),
            'surplus_fraction': round((data['P_net_kW'] < 0).sum() / len(data), 4),
            'timestamp_report': ts_report,
            'pv_power_report': pv_report,
            'load_power_report': load_report,
        }
    }
    return data_dict


def validate_timestamps(df: pd.DataFrame, expected_freq_min: int = 15,
                        expected_points: int = 672) -> Dict[str, Any]:
    """
    校验时间戳连续性和分辨率。

    Args:
        df: 含timestamp列的DataFrame
        expected_freq_min: 期望时间分辨率(分钟), 默认15
        expected_points: 期望数据点数, 默认672(7天x96步)

    Returns:
        report: dict, 包含
            - is_continuous: bool, 时间戳是否连续无缺失
            - gap_count: int, 缺失时间步数
            - gap_fraction: float, 缺失比例
            - freq_minutes: int, 实际时间分辨率
            - pass: bool, 是否通过(gap_fraction <= 0.0001)

    Pass标准: gap_fraction <= 0.01% (时间对齐 <= 0.01%)
    """
    ts = pd.to_datetime(df['timestamp'])

    # 计算时间差(分钟)
    diffs = ts.diff().dropna()
    diff_minutes = diffs.dt.total_seconds() / 60.0

    # 实际分辨率(众数)
    freq_minutes = int(diff_minutes.mode().iloc[0]) if len(diff_minutes) > 0 else 0

    # 统计非15min的时间差
    irregular = (diff_minutes != expected_freq_min).sum()
    gap_count = int(irregular)

    # 如果行数不足, 缺失=期望-实际
    actual_points = len(df)
    if actual_points < expected_points:
        gap_count += (expected_points - actual_points)

    gap_fraction = gap_count / expected_points if expected_points > 0 else 1.0

    report = {
        'is_continuous': gap_count == 0,
        'gap_count': gap_count,
        'gap_fraction': round(gap_fraction, 6),
        'freq_minutes': freq_minutes,
        'actual_points': actual_points,
        'expected_points': expected_points,
        'pass': gap_fraction <= 0.0001,
    }
    return report


def validate_power_range(df: pd.DataFrame, rated_power_kw: float,
                         power_column: str = 'P_load_kW',
                         allow_negative: bool = True) -> Dict[str, Any]:
    """
    校验功率范围合理性。

    Args:
        df: 含功率列的DataFrame
        rated_power_kw: 额定功率(kW), 来自C1配置
        power_column: 功率列名, 默认P_load_kW
        allow_negative: 是否允许负值(如PV夜间=0, 净负荷可为负)

    Returns:
        report: dict, 包含
            - is_valid: bool, 是否通过
            - out_of_range_count: int, 超范围数据点数
            - max_value: float, 最大值
            - min_value: float, 最小值
            - nan_count: int, NaN数量
            - rated_power_kw: float, 额定功率
    """
    values = df[power_column]

    nan_count = int(values.isna().sum())
    max_value = float(values.max())
    min_value = float(values.min())

    # 超范围判定: 超过2倍额定 或 (不允许负值时出现负值)
    over_rated = (values.abs() > 2 * rated_power_kw).sum()
    if not allow_negative:
        negative_count = (values < 0).sum()
        out_of_range_count = int(over_rated + negative_count)
    else:
        out_of_range_count = int(over_rated)

    is_valid = (nan_count == 0) and (out_of_range_count == 0)

    report = {
        'is_valid': is_valid,
        'out_of_range_count': out_of_range_count,
        'max_value': round(max_value, 3),
        'min_value': round(min_value, 3),
        'nan_count': nan_count,
        'rated_power_kw': rated_power_kw,
    }
    return report


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    csv_path = project_root / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    config_path = project_root / "05_config" / "mvp_c1_config.json"

    print("M1 data_lite — MVP数据加载模块")
    print(f"CSV: {csv_path.name}")
    print(f"Config: {config_path.name}")
    print()

    result = load_data(str(csv_path), str(config_path))

    data = result['data']
    meta = result['meta']

    print(f"年份: {meta['year']}  窗口: {meta['window']}")
    print(f"时间范围: {meta['start']} ~ {meta['end']}")
    print(f"数据点数: {meta['n_points']} (期望672)")
    print(f"分辨率: {meta['freq_minutes']}min")
    print()
    print(f"PV: max={meta['pv_max_kW']}kW, rated={meta['pv_rated_kWp']}kWp")
    print(f"Load: max={meta['load_max_kW']}kW")
    print(f"Net: [{meta['net_min_kW']}, {meta['net_max_kW']}]kW")
    print(f"富余比例: {meta['surplus_fraction']*100:.1f}%")
    print()
    print(f"时间戳校验: {'PASS' if meta['timestamp_report']['pass'] else 'FAIL'}")
    print(f"  连续={meta['timestamp_report']['is_continuous']}, gap={meta['timestamp_report']['gap_count']}")
    print(f"PV功率校验: {'PASS' if meta['pv_power_report']['is_valid'] else 'FAIL'}")
    print(f"  max={meta['pv_power_report']['max_value']}, NaN={meta['pv_power_report']['nan_count']}")
    print(f"Load功率校验: {'PASS' if meta['load_power_report']['is_valid'] else 'FAIL'}")
    print(f"  max={meta['load_power_report']['max_value']}, NaN={meta['load_power_report']['nan_count']}")
    print()
    print("前5行数据:")
    print(data.head().to_string(index=False))
    print()
    print(f"状态: M1 data_lite 函数体已实现")
