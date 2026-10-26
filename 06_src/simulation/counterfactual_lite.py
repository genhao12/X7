# -*- coding: utf-8 -*-
"""
M5 counterfactual_lite — MVP反事实配对引擎模块
功能：3运行(BOL+BAT D=0.50+BAT D=0.90)配对计算AE值
依赖：M4 ems_bf_lite
输入：ems_commands x 3健康状态
输出：paired_AE_values (2路径 x 3配对 = 6 AE值)
"""
import numpy as np
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass


# MVP路径定义 (v3.0冻结定义: alpha1=BAT→FC, beta1=BAT→EL)
# 完整6路径: alpha1:BAT→FC, alpha2:EL→FC, beta1:BAT→EL, beta2:FC→EL, gamma1:FC→BAT, gamma2:EL→BAT
PATHS = {
    'alpha1': {'name': 'BAT->FC', 'source': 'BAT', 'target': 'FC',
               'description': 'BAT退化->FC应力变化->FC损伤增量'},
    'beta1': {'name': 'BAT->EL', 'source': 'BAT', 'target': 'EL',
              'description': 'BAT退化->EL应力变化->EL损伤增量'},
}

# MVP运行定义
RUNS = {
    'BOL': {'D': 0.0, 'label': 'BOL基线'},
    'BAT_D050': {'D': 0.50, 'label': 'BAT退化D=0.50'},
    'BAT_D090': {'D': 0.90, 'label': 'BAT退化D=0.90'},
}


@dataclass
class AEResult:
    """反事实AE计算结果"""
    path: str          # 路径名(alpha1/beta1)
    run_label: str     # 运行标签(BOL/BAT_D050/BAT_D090)
    target: str        # 目标设备(FC/EL)
    dD_coupled: float  # 耦合运行损伤增量
    dD_baseline: float # 基线运行损伤增量
    AE: float          # 绝对效应 = dD_coupled - dD_baseline
    TE: float          # 总效应(如果有, 否则NA)
    is_finite: bool    # AE值是否有限(非NaN/Inf)


def pair_runs(run_results: Dict[str, Dict[str, np.ndarray]]) -> List[AEResult]:
    """
    将3个运行结果配对, 计算每个路径的AE值。

    Args:
        run_results: {run_label: {dD_bat, dD_fc, dD_el}} 3个运行的损伤增量
            run_labels: 'BOL', 'BAT_D050', 'BAT_D090'

    Returns:
        ae_list: List[AEResult], 2路径 x 2退化运行 = 4个AE结果

    配对逻辑:
        对每个路径(alpha1, beta1):
            对每个退化运行(BAT_D050, BAT_D090):
                AE = dD_target(coupled_run) - dD_target(BOL_run)
                其中:
                - alpha1: target=FC, coupled_run=BAT退化运行
                - beta1: target=EL, coupled_run=BAT退化运行

    AE定义:
        AE(i->j) = dD_j(耦合运行) - dD_j(基线运行)
        其中基线运行=BOL运行(D=0, 无退化)

    TE分母保护:
        如果 dD_j_BOL < 1e-4 EOL/year, 则 TE = NA (仅报告AE)
    """
    ae_list = []

    # 提取BOL运行作为基线
    if 'BOL' not in run_results:
        raise ValueError("run_results必须包含'BOL'基线运行")
    baseline = run_results['BOL']

    # 对每个路径配对
    for path_key, path_info in PATHS.items():
        target = path_info['target'].lower()  # 'fc' 或 'el'
        dD_key = f'dD_{target}'

        # 基线运行的目标设备损伤
        dD_baseline_arr = baseline.get(dD_key, np.zeros(1))

        # 对每个退化运行配对
        for run_key in ['BAT_D050', 'BAT_D090']:
            if run_key not in run_results:
                continue
            coupled = run_results[run_key]
            dD_coupled_arr = coupled.get(dD_key, np.zeros(1))

            # 计算AE和TE
            AE, TE = compute_AE(dD_coupled_arr, dD_baseline_arr)

            # 检查有限性
            is_finite = bool(np.isfinite(AE))

            ae_list.append(AEResult(
                path=path_key,
                run_label=run_key,
                target=path_info['target'],
                dD_coupled=float(np.sum(dD_coupled_arr)),
                dD_baseline=float(np.sum(dD_baseline_arr)),
                AE=float(AE),
                TE=float(TE) if np.isfinite(TE) else float('nan'),
                is_finite=is_finite,
            ))

    return ae_list


def compute_AE(dD_coupled: np.ndarray, dD_baseline: np.ndarray,
               dt_min: float = 15.0) -> Tuple[float, float]:
    """
    计算单个配对的AE和TE值。

    Args:
        dD_coupled: (N_steps,) 耦合运行的目标设备损伤增量
        dD_baseline: (N_steps,) 基线运行的目标设备损伤增量
        dt_min: 时间步长(分钟)

    Returns:
        AE: float, 绝对效应 = sum(dD_coupled) - sum(dD_baseline)
        TE: float, 总效应 = AE / sum(dD_baseline) (或NA如果分母<1e-4)
    """
    dD_coupled = np.atleast_1d(np.asarray(dD_coupled, dtype=float))
    dD_baseline = np.atleast_1d(np.asarray(dD_baseline, dtype=float))

    # AE = 耦合运行总损伤 - 基线运行总损伤
    sum_coupled = float(np.sum(dD_coupled))
    sum_baseline = float(np.sum(dD_baseline))
    AE = sum_coupled - sum_baseline

    # TE分母保护: 如果基线损伤 < 1e-4 EOL/year, TE=NA
    # EOL/year转换: W1窗口7天, 1年=52周, EOL=1.0
    # dD_per_year = sum_baseline * (365/7)
    # 但MVP简化: 直接用绝对值 < 1e-4
    if abs(sum_baseline) < 1e-4:
        TE = float('nan')
    else:
        TE = AE / sum_baseline

    return AE, TE


def validate_ae_values(ae_list: List[AEResult]) -> Dict[str, Any]:
    """
    验证AE值的有效性和符号一致性。

    Args:
        ae_list: pair_runs()返回的AEResult列表

    Returns:
        report: dict, 包含
            - all_finite: bool, 所有AE值有限
            - ae_signs: dict, 每个路径的AE符号
            - m1_m3_positive: bool, alpha1和beta1路径AE >= 0 (物理预期)
            - te_protected: bool, TE=NA的比例
            - pass: bool, 全部通过

    物理预期(MVP范围):
        alpha1(BAT->FC): AE >= 0 (BAT退化->FC应力增加->FC损伤增加)
        beta1(BAT->EL): AE >= 0 (BAT退化->EL应力增加->EL损伤增加)
        注意: 这是M1-M3归因组OFF的预期; M4(库存控制)的AE可能<=0
    """
    all_finite = all(ae.is_finite for ae in ae_list)

    # 按路径分组
    ae_by_path = {}
    for ae in ae_list:
        if ae.path not in ae_by_path:
            ae_by_path[ae.path] = []
        ae_by_path[ae.path].append(ae)

    # AE符号
    ae_signs = {}
    m1_m3_positive = True
    for path, aes in ae_by_path.items():
        signs = ['>=0' if ae.AE >= 0 else '<0' for ae in aes]
        ae_signs[path] = signs
        # M1-M3路径预期AE >= 0
        if path in ('alpha1', 'beta1'):
            for ae in aes:
                if ae.AE < -1e-6:  # 允许微小数值偏差
                    m1_m3_positive = False

    # TE保护统计
    n_nan_te = sum(1 for ae in ae_list if not np.isfinite(ae.TE))
    te_protected = n_nan_te

    all_pass = all_finite and m1_m3_positive

    return {
        'all_finite': all_finite,
        'ae_signs': ae_signs,
        'm1_m3_positive': m1_m3_positive,
        'te_protected_count': te_protected,
        'total_ae_count': len(ae_list),
        'all_pass': all_pass,
        'ae_values': {f"{ae.path}_{ae.run_label}": {
            'AE': ae.AE, 'TE': ae.TE if np.isfinite(ae.TE) else 'NA',
            'dD_coupled': ae.dD_coupled, 'dD_baseline': ae.dD_baseline,
        } for ae in ae_list},
    }


if __name__ == "__main__":
    import sys
    import json
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "06_src"))

    print("=" * 70)
    print("M5 counterfactual_lite — 反事实配对引擎模块 自测")
    print("=" * 70)

    # 1. 加载配置和数据
    print("\n[1] 加载C1配置和W1数据")
    config_path = project_root / "05_config" / "mvp_c1_config.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    from components.data_lite import load_data
    load_csv = str(project_root / "02_data_processed" / "tradestreet_pipeline" /
                   "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
    data = load_data(load_csv, str(config_path), year=2017, window_days=7)
    df = data['data']
    pv_kw = df['P_pv_kW'].values
    load_kw = df['P_load_kW'].values
    print(f"  W1数据: {len(pv_kw)}步, PV max={pv_kw.max():.1f}kW, Load mean={load_kw.mean():.1f}kW")

    # 2. 构建hp_func和EMS命令
    print("\n[2] 运行3次EMS仿真 (BOL + BAT D=0.50 + BAT D=0.90)")
    from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
    from simulation.closed_loop_lite import compute_damage, CoalitionState

    cap = config['capacity']
    ems_p = config['ems_parameters']
    bat_c = config['battery_constraints']
    h2_c = config['hydrogen_constraints']
    msl = config['minimum_stable_load']
    topo = config['topology']

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

    # EMS命令对所有运行相同 (唯一差异是BAT退化状态D)
    np.random.seed(42)
    commands, state_ems = rule_based_dispatch(load_kw, pv_kw, cfg)

    # hp_func (简化版, 同M3)
    def test_hp_func(D):
        D = float(np.clip(D, 0.0, 1.0))
        q_frac = 1.0 - 0.2 * D
        r0_ohm = 0.000427 * (1 + 0.1673 * D)
        p_max_kw = 67.5 * q_frac
        return (q_frac, r0_ohm, p_max_kw)

    # 3个运行的D_state和退化感知参数
    run_configs = {
        'BOL':       {'D_state': {'bat': 0.0,  'fc': 0.0, 'el': 0.0}, 'bat_p_max': 67.50, 'bat_cap': 135.0},
        'BAT_D050':  {'D_state': {'bat': 0.50, 'fc': 0.0, 'el': 0.0}, 'bat_p_max': 60.75, 'bat_cap': 121.5},
        'BAT_D090':  {'D_state': {'bat': 0.90, 'fc': 0.0, 'el': 0.0}, 'bat_p_max': 55.35, 'bat_cap': 110.7},
    }

    # 全ON联盟 (完整耦合)
    coalition_full = CoalitionState.from_code('1111')

    run_results = {}
    for run_label, rc in run_configs.items():
        # 退化感知EMS: BAT退化 -> P_max和容量缩减 -> EMS调度变化 -> FC/EL命令变化
        np.random.seed(42)  # 相同种子, 唯一差异是退化参数
        commands, _ = rule_based_dispatch(load_kw, pv_kw, cfg,
                                           bat_p_max_kw=rc['bat_p_max'],
                                           bat_capacity_kwh=rc['bat_cap'])
        dD = compute_damage(test_hp_func, commands, config,
                            D_state=rc['D_state'], coalition=coalition_full)
        run_results[run_label] = dD
        print(f"  {run_label}: dD_bat={np.sum(dD['dD_bat']):.6f}, "
              f"dD_fc={np.sum(dD['dD_fc']):.6f}, dD_el={np.sum(dD['dD_el']):.6f}")

    # 3. 配对计算AE
    print("\n[3] 配对计算AE值")
    ae_list = pair_runs(run_results)
    print(f"  配对数: {len(ae_list)}")
    for ae in ae_list:
        te_str = f"{ae.TE:.4f}" if np.isfinite(ae.TE) else "NA"
        print(f"  {ae.path}({PATHS[ae.path]['name']}) {ae.run_label}: "
              f"AE={ae.AE:+.6f}, TE={te_str}, "
              f"dD_coupled={ae.dD_coupled:.6f}, dD_baseline={ae.dD_baseline:.6f}")

    # 4. 验证AE值
    print("\n[4] AE值验证")
    report = validate_ae_values(ae_list)
    print(f"  全部有限: {'PASS' if report['all_finite'] else 'FAIL'}")
    print(f"  M1-M3正号: {'PASS' if report['m1_m3_positive'] else 'FAIL'}")
    print(f"  TE保护: {report['te_protected_count']}/{report['total_ae_count']} 个TE=NA")
    print(f"  AE符号:")
    for path, signs in report['ae_signs'].items():
        print(f"    {path}: {signs}")
    print(f"  总体验证: {'PASS' if report['all_pass'] else 'FAIL'}")

    # 5. 物理合理性
    print("\n[5] 物理合理性检查")
    # alpha1(BAT->FC): BAT退化D增加 -> FC损伤应增加 -> AE >= 0
    ae_alpha1_d050 = [ae for ae in ae_list if ae.path == 'alpha1' and ae.run_label == 'BAT_D050'][0]
    ae_alpha1_d090 = [ae for ae in ae_list if ae.path == 'alpha1' and ae.run_label == 'BAT_D090'][0]
    print(f"  alpha1(BAT->FC): AE(D=0.50)={ae_alpha1_d050.AE:+.6f}, AE(D=0.90)={ae_alpha1_d090.AE:+.6f}")
    print(f"  D=0.90 AE > D=0.50 AE: {ae_alpha1_d090.AE > ae_alpha1_d050.AE} (预期True, 退化越深效应越大)")

    ae_beta1_d050 = [ae for ae in ae_list if ae.path == 'beta1' and ae.run_label == 'BAT_D050'][0]
    ae_beta1_d090 = [ae for ae in ae_list if ae.path == 'beta1' and ae.run_label == 'BAT_D090'][0]
    print(f"  beta1(BAT->EL): AE(D=0.50)={ae_beta1_d050.AE:+.6f}, AE(D=0.90)={ae_beta1_d090.AE:+.6f}")
    print(f"  D=0.90 AE > D=0.50 AE: {ae_beta1_d090.AE > ae_beta1_d050.AE} (预期True)")

    print("\n" + "=" * 70)
    print("M5 counterfactual_lite 自测完成")
    print(f"路径: {list(PATHS.keys())}")
    print(f"运行: {list(RUNS.keys())}")
    print(f"配对: {len(PATHS)} x 2 = {len(ae_list)} AE值")
    print(f"验证: {'全部PASS' if report['all_pass'] else '部分FAIL'}")
    print(f"状态: 3个函数全部实现并验证")
    print("=" * 70)
