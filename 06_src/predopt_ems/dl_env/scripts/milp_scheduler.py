# -*- coding: utf-8 -*-
"""
milp_scheduler.py — LSTM+MILP 预测优化型 EMS 核心调度器（v9 §5/§6）
=====================================================================
实现 v9 完整数学模型：
  §5.2 电池：P_ch≥0/P_dis≥0 拆分 + 二进制互斥 u_bat +
            SOC(t+1)=SOC(t)+η_ch·P_ch·Δt/E − P_dis·Δt/(η_dis·E)
            （η_ch=η_dis=√η_rt，v9 意见 6；η_rt 默认 0.90 待 Gate B 冻结）
  §5.3 FC/EL：Gurobi 原生 SOS2 凸组合（P=ΣλP_k、ṁ=Σλṁ_k、Σλ=u、λ≥0、SOS_TYPE2）
            支撑点覆盖 0 点 + P_min→P_rated（K 段），LHV=33.3，常数效率与 ems_stage2b 同式
  §5.6 FC/EL 动态运行约束：u_t−u_{t−1}=y_t−z_t、y+z≤1、最小开/停机（T_on/T_off）、
            P_min·u≤P≤P_max·u、ramp（可选）、窗口起点继承（剩余开停机/上时刻功率）
  §5.5 终端项线性化：d_soc≥|SOC_T−SOC_ref|、d_h2≥|m_T−H₂_ref|（辅助变量）
  §6 三级字典序（三次显式 optimize）：
            L1 min ΣP_uns·Δt → L1_opt
            L2 约束 ΣP_uns·Δt≤L1_opt+ε₁；min d_soc+d_h2/M_H2 → L2_opt
            L3 约束 L2≤L2_opt+ε₂；min 运行成本+弃光（+退化代理，da 模式）
  §17.1 Gurobi 参数：Seed=0、FeasTol=1e-6、OptTol=1e-6、IntFeasTol=1e-5、
            L3 MIPGap=0.01、Method=-1、Threads=1、TimeLimit=60
  求解状态规则：OPTIMAL 接收；INFEASIBLE/INF_OR_UNBD/NUMERIC 判失败；
            TIME_LIMIT 记录 status/incumbent/ObjBound/MIPGap

运行环境：DL 环境（gurobipy 12.0.3 / 13.0.1）
命令（VSCode 终端，自带 smoke test）：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/milp_scheduler.py
"""
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SRC)

from ems.ems_stage2b import LHV_H2_KWH_PER_KG, Stage2BEMSConfig

OUT_JSON = Path(__file__).resolve().parent / ".." / "results" / "milp_scheduler_smoke_result.json"


# ==================================================================
# 数据结构
# ==================================================================

@dataclass
class MPCState:
    """MPC 跨窗口初始状态（v9 §5.6）。"""
    soc: float = 0.50
    h2_kg: float = 0.0
    u_fc: int = 0
    u_el: int = 0
    fc_min_on_remaining: int = 0      # 剩余最小开机步数（强制保持运行）
    el_min_on_remaining: int = 0
    fc_min_off_remaining: int = 0     # 剩余最小停机步数（强制保持停机）
    el_min_off_remaining: int = 0
    p_fc_prev: float = 0.0            # 上一时刻功率（ramp 基准）
    p_el_prev: float = 0.0


@dataclass
class WindowResult:
    """单窗口求解结果。"""
    commands: np.ndarray              # (T, 6): p_ch,p_dis,p_fc,p_el,p_curt,p_uns
    u_fc: np.ndarray
    u_el: np.ndarray
    soc_traj: np.ndarray
    h2_traj: np.ndarray
    l1_opt: float
    l2_opt: float
    l3_opt: float
    status_l1: int
    status_l2: int
    status_l3: int
    solve_time_s: float
    feasible: bool
    soc_conservation_res: float
    h2_conservation_res: float
    mip_gap_l3: float = 0.0        # L3 MIPGap（TIME_LIMIT/SUBOPTIMAL 窗口的收敛缺口，披露用）


# ==================================================================
# 核心调度器
# ==================================================================

class MilpScheduler:
    """三级字典序 MILP 滚动调度器（v9 §5/§6）。"""

    def __init__(self, cfg: Stage2BEMSConfig,
                 eta_rt: float = 0.90,
                 n_seg: int = 4,
                 eps1_kwh: float = 1e-3,
                 eps2: float = 1e-3,
                 w: Optional[Dict[str, float]] = None,
                 ramp_fc: Optional[float] = None,
                 ramp_el: Optional[float] = None,
                 time_limit_s: float = 60.0,
                 thread: int = 1,
                 seed: int = 0,
                 verbose: bool = False):
        # —— 物理参数（来自 Stage2BEMSConfig）——
        self.cfg = cfg
        self.E = cfg.bat_kwh
        self.P_bat_max = cfg.bat_kwh * cfg.bat_c_rate      # 电池功率上限（C-rate）
        self.soc_min, self.soc_max = cfg.soc_min, cfg.soc_max
        self.eta_ch = self.eta_dis = float(np.sqrt(eta_rt))  # v9 意见 6：η_ch·η_dis=η_rt
        self.eta_rt = eta_rt

        self.p_fc_rated = cfg.pemfc_kw
        self.p_el_rated = cfg.pemel_kw
        self.p_fc_min = cfg.pemfc_kw * cfg.min_stable_load_fc
        self.p_el_min = cfg.pemel_kw * cfg.min_stable_load_el
        self.eta_fc, self.eta_el = cfg.eta_fc, cfg.eta_el
        self.t_on = cfg.minimum_run_steps                   # 最小开机步数
        self.t_off = cfg.minimum_run_steps                  # 最小停机步数

        self.h2_min = cfg.h2_kg * cfg.h2_min_fraction
        self.h2_max = cfg.h2_kg * cfg.h2_max_fraction
        self.r_leak_h = cfg.h2_leakage_per_day / 24.0       # h⁻¹ 线性泄漏
        self.dt_h = cfg.dt_h                                # 0.25 h

        self.n_seg = int(n_seg)
        # —— L3 目标相对权重（v9 §5.4：无量纲化加权；占位默认，2017 验证集选择后冻结）——
        self.w = dict(w or {"fc": 1.0, "el": 0.3, "curt": 0.1,
                            "start_fc": 5.0, "start_el": 5.0, "bat_thr": 0.05})
        self.eps1_kwh = eps1_kwh
        self.eps2 = eps2
        self.ramp_fc = ramp_fc      # None=不约束（待 Gate A 冻结，现有参数表无 ramp）
        self.ramp_el = ramp_el
        self.time_limit_s = time_limit_s
        self.thread = thread
        self.seed = seed
        self.verbose = verbose

    # --------------------------------------------------------------
    # 支撑点（v9 §5.3：0 点 + P_min→P_rated 严格递增，SOS2 凸组合）
    # --------------------------------------------------------------
    def _breakpoints(self, pmin: float, prated: float) -> List[float]:
        if self.n_seg <= 1:
            return [0.0, prated]
        pts = list(np.linspace(pmin, prated, self.n_seg))
        # 去重并确保 0 点 + 严格递增
        pts = sorted(set([0.0] + [round(float(p), 6) for p in pts]))
        if pts[-1] < prated:
            pts.append(prated)
        return pts

    def _fc_flow(self, p: float) -> float:
        return p / (self.eta_fc * LHV_H2_KWH_PER_KG)          # kg/h

    def _el_flow(self, p: float) -> float:
        return self.eta_el * p / LHV_H2_KWH_PER_KG            # kg/h

    # --------------------------------------------------------------
    # 单窗口求解（三级字典序，v9 §6）
    # --------------------------------------------------------------
    def solve_window(self, pv_fc: np.ndarray, load_fc: np.ndarray,
                     state: MPCState,
                     soc_ref: Optional[float] = None,
                     h2_ref: Optional[float] = None,
                     mode: str = "normal",
                     degradation: Optional[Dict[str, float]] = None) -> WindowResult:
        T = len(load_fc)
        assert len(pv_fc) == T
        t0 = time.time()
        soc_ref = self.soc_max if soc_ref is None else float(soc_ref)
        h2_ref = self.h2_max if h2_ref is None else float(h2_ref)

        # —— 闭环退化反馈（Gate A ① 接线，v9 §8/§11）——
        # degradation = {"bat": D_bat, "fc": D_fc, "el": D_el}（D_feedback）
        # open-loop：degradation=None → 用 BOL 标称参数（与 closed_loop_lite D=0 同源）
        # closed-loop：按 Gate A 冻结映射重构容量/效率/功率/支撑点
        E = self.E
        P_bat_max = self.P_bat_max
        eta_fc, eta_el = self.eta_fc, self.eta_el
        p_fc_rated, p_el_rated = self.p_fc_rated, self.p_el_rated
        if degradation is not None:
            from degradation_feedback import bat_params_milp, fc_health, el_health
            D_bat = float(degradation.get("bat", 0.0))
            D_fc = float(degradation.get("fc", 0.0))
            D_el = float(degradation.get("el", 0.0))
            bp = bat_params_milp(D_bat, self.cfg)
            E = bp["E_bat_kwh"]
            P_bat_max = min(bp["Pmax_bat_kw"], self.P_bat_max)
            fh = fc_health(D_fc)
            eh = el_health(D_el)
            eta_fc = self.eta_fc * fh["eta_frac"]
            eta_el = self.eta_el * eh["eta_frac"]
            p_fc_rated = self.p_fc_rated * fh["P_max_frac"]
            p_el_rated = self.p_el_rated * eh["P_max_frac"]

        import gurobipy as gp
        env = gp.Env(empty=True)
        env.setParam("OutputFlag", 1 if self.verbose else 0)
        env.setParam("Threads", self.thread)
        env.setParam("Seed", self.seed)
        env.setParam("FeasibilityTol", 1e-6)
        env.setParam("OptimalityTol", 1e-6)
        env.setParam("IntFeasTol", 1e-5)
        env.setParam("Method", -1)
        env.start()

        # ── 变量 ──
        m = gp.Model("ems_window", env=env)
        P_ch = m.addVars(T, lb=0, ub=P_bat_max, name="P_ch")
        P_dis = m.addVars(T, lb=0, ub=P_bat_max, name="P_dis")
        u_bat = m.addVars(T, vtype=gp.GRB.BINARY, name="u_bat")
        P_fc = m.addVars(T, lb=0, ub=p_fc_rated, name="P_fc")
        P_el = m.addVars(T, lb=0, ub=p_el_rated, name="P_el")
        u_fc = m.addVars(T, vtype=gp.GRB.BINARY, name="u_fc")
        u_el = m.addVars(T, vtype=gp.GRB.BINARY, name="u_el")
        y_fc = m.addVars(T, vtype=gp.GRB.BINARY, name="y_fc")   # 启动
        z_fc = m.addVars(T, vtype=gp.GRB.BINARY, name="z_fc")   # 停机
        y_el = m.addVars(T, vtype=gp.GRB.BINARY, name="y_el")
        z_el = m.addVars(T, vtype=gp.GRB.BINARY, name="z_el")
        P_curt = m.addVars(T, lb=0, name="P_curt")               # 弃光
        P_uns = m.addVars(T, lb=0, name="P_uns")                 # 失负荷
        SOC = m.addVars(T + 1, lb=self.soc_min, ub=self.soc_max, name="SOC")
        H2 = m.addVars(T + 1, lb=self.h2_min, ub=self.h2_max, name="H2")
        d_soc = m.addVar(lb=0, name="d_soc")                     # 终端偏差（§5.5）
        d_h2 = m.addVar(lb=0, name="d_h2")
        # FC/EL SOS2 支撑点（v9 §5.3；closed-loop 用退化后 rated/eta 重构）
        fc_pts = self._breakpoints(self.p_fc_min, p_fc_rated)
        el_pts = self._breakpoints(self.p_el_min, p_el_rated)
        lam_fc = m.addVars(T, len(fc_pts), lb=0, name="lam_fc")
        lam_el = m.addVars(T, len(el_pts), lb=0, name="lam_el")
        for t in range(T):
            m.addSOS(gp.GRB.SOS_TYPE2, [lam_fc[t, k] for k in range(len(fc_pts))])
            m.addSOS(gp.GRB.SOS_TYPE2, [lam_el[t, k] for k in range(len(el_pts))])

        # ── 电池（v9 §5.2）──
        for t in range(T):
            m.addConstr(P_ch[t] <= P_bat_max * u_bat[t])          # 充放电互斥（v9 意见：不用 min() big-M）
            m.addConstr(P_dis[t] <= P_bat_max * (1 - u_bat[t]))
            m.addConstr(SOC[t + 1] == SOC[t]
                        + self.eta_ch * P_ch[t] * self.dt_h / E
                        - P_dis[t] * self.dt_h / (self.eta_dis * E))
        m.addConstr(SOC[0] == state.soc)

        # ── FC/EL SOS2 与功率/氢流（v9 §5.3）──
        for t in range(T):
            m.addConstr(gp.quicksum(lam_fc[t, k] for k in range(len(fc_pts))) == u_fc[t])
            m.addConstr(gp.quicksum(lam_el[t, k] for k in range(len(el_pts))) == u_el[t])
            m.addConstr(P_fc[t] == gp.quicksum(fc_pts[k] * lam_fc[t, k] for k in range(len(fc_pts))))
            m.addConstr(P_el[t] == gp.quicksum(el_pts[k] * lam_el[t, k] for k in range(len(el_pts))))
            # 功率上下限（§5.6）
            m.addConstr(P_fc[t] >= self.p_fc_min * u_fc[t])
            m.addConstr(P_el[t] >= self.p_el_min * u_el[t])
            m.addConstr(P_fc[t] <= p_fc_rated * u_fc[t])
            m.addConstr(P_el[t] <= p_el_rated * u_el[t])

        # ── FC/EL 启停逻辑（v9 §5.6）──
        for t in range(T):
            if t == 0:
                # 【修正 2026-08-08】v9 §5.6：继承上一时刻启停状态作为差分基准
                # （u_{-1}=state.u_fc），t=0 可自由启动/停机——不得强制 u[0]==state：
                # 否则滚动 MPC 每窗口只执行 t=0，启动决策永远被推迟到 t≥1 而无法
                # 执行（低 SOC 专项验证暴露：FC 永不启动、电池卡下限、持续失负荷）。
                # 边界违例改由"剩余最小开/停机时间继承" + 段约束保证（见下）。
                m.addConstr(u_fc[0] - float(state.u_fc) == y_fc[0] - z_fc[0])
                m.addConstr(u_el[0] - float(state.u_el) == y_el[0] - z_el[0])
            else:
                m.addConstr(u_fc[t] - u_fc[t - 1] == y_fc[t] - z_fc[t])
                m.addConstr(u_el[t] - u_el[t - 1] == y_el[t] - z_el[t])
            m.addConstr(y_fc[t] + z_fc[t] <= 1)
            m.addConstr(y_el[t] + z_el[t] <= 1)
            # 最小开机/停机（§5.6）
            if t > 0:
                lo = max(0, t - self.t_on + 1)
                m.addConstr(gp.quicksum(y_fc[tau] for tau in range(lo, t + 1)) <= u_fc[t])
                m.addConstr(gp.quicksum(y_el[tau] for tau in range(lo, t + 1)) <= u_el[t])
                lo2 = max(0, t - self.t_off + 1)
                m.addConstr(gp.quicksum(z_fc[tau] for tau in range(lo2, t + 1)) <= 1 - u_fc[t])
                m.addConstr(gp.quicksum(z_el[tau] for tau in range(lo2, t + 1)) <= 1 - u_el[t])
        # 窗口起点继承：剩余最小开/停机（§5.6）
        for t in range(min(self.t_on, T)):
            if state.fc_min_on_remaining > 0 and t < state.fc_min_on_remaining:
                m.addConstr(u_fc[t] == 1)
            if state.el_min_on_remaining > 0 and t < state.el_min_on_remaining:
                m.addConstr(u_el[t] == 1)
            if state.fc_min_off_remaining > 0 and t < state.fc_min_off_remaining:
                m.addConstr(u_fc[t] == 0)
            if state.el_min_off_remaining > 0 and t < state.el_min_off_remaining:
                m.addConstr(u_el[t] == 0)
        # ramp（可选，§5.6；Gate A 冻结前默认不约束）
        if self.ramp_fc:
            for t in range(T):
                prev = state.p_fc_prev if t == 0 else P_fc[t - 1]
                m.addConstr(P_fc[t] - prev <= self.ramp_fc)
                m.addConstr(prev - P_fc[t] <= self.ramp_fc)
        if self.ramp_el:
            for t in range(T):
                prev = state.p_el_prev if t == 0 else P_el[t - 1]
                m.addConstr(P_el[t] - prev <= self.ramp_el)
                m.addConstr(prev - P_el[t] <= self.ramp_el)

        # ── H₂ 动态（v9 §5.3：LHV + 线性泄漏）──
        for t in range(T):
            cons = P_fc[t] * self.dt_h / (eta_fc * LHV_H2_KWH_PER_KG)   # kg（本步）
            prod = P_el[t] * self.dt_h * eta_el / LHV_H2_KWH_PER_KG
            leak = self.r_leak_h * H2[t] * self.dt_h                          # 线性泄漏
            m.addConstr(H2[t + 1] == H2[t] + prod - cons - leak)
        m.addConstr(H2[0] == state.h2_kg)

        # ── 功率平衡（含失负荷/弃光）──
        for t in range(T):
            m.addConstr(pv_fc[t] + P_dis[t] + P_fc[t] + P_uns[t]
                        == load_fc[t] + P_ch[t] + P_el[t] + P_curt[t])

        # ── 终端项线性化（§5.5）──
        m.addConstr(d_soc >= SOC[T] - soc_ref)
        m.addConstr(d_soc >= soc_ref - SOC[T])
        m.addConstr(d_h2 >= H2[T] - h2_ref)
        m.addConstr(d_h2 >= h2_ref - H2[T])
        L2_OBJ = d_soc + d_h2 / self.h2_max

        # ============ 三级字典序（v9 §6，三次显式 optimize）============
        # L1：可靠性
        m.setObjective(gp.quicksum(P_uns[t] * self.dt_h for t in range(T)), gp.GRB.MINIMIZE)
        m.setParam("TimeLimit", self.time_limit_s)
        m.optimize()
        st1 = m.status
        if st1 != gp.GRB.OPTIMAL:
            env.dispose()
            return WindowResult(np.zeros((T, 6)), np.zeros(T), np.zeros(T),
                                np.full(T + 1, np.nan), np.full(T + 1, np.nan),
                                0, 0, 0, st1, 0, 0, time.time() - t0, False, np.nan, np.nan)
        l1_opt = float(m.objVal)
        m.addConstr(gp.quicksum(P_uns[t] * self.dt_h for t in range(T)) <= l1_opt + self.eps1_kwh)

        # L2：季节储能安全（终端偏差）
        m.setObjective(L2_OBJ, gp.GRB.MINIMIZE)
        m.optimize()
        st2 = m.status
        if st2 != gp.GRB.OPTIMAL:
            env.dispose()
            return WindowResult(np.zeros((T, 6)), np.zeros(T), np.zeros(T),
                                np.full(T + 1, np.nan), np.full(T + 1, np.nan),
                                l1_opt, 0, 0, st1, st2, 0, time.time() - t0, False, np.nan, np.nan)
        l2_opt = float(m.objVal)
        m.addConstr(L2_OBJ <= l2_opt + self.eps2)

        # L3：经济/退化（§5.4）
        l3_obj = (self.w["fc"] * gp.quicksum(P_fc[t] * self.dt_h for t in range(T))
                  + self.w["el"] * gp.quicksum(P_el[t] * self.dt_h for t in range(T))
                  + self.w["curt"] * gp.quicksum(P_curt[t] * self.dt_h for t in range(T))
                  + self.w["start_fc"] * gp.quicksum(y_fc[t] for t in range(T))
                  + self.w["start_el"] * gp.quicksum(y_el[t] for t in range(T)))
        if mode == "da":
            # 退化感知：BAT 吞吐代理（v9 §5.4/§11）
            # 【修正 2026-08-08】代理为固定标定函数，系数不含 D——
            # open/closed 使用完全相同的退化代理目标函数（§11）；
            # 退化反馈经 Gate A 的 f_E/f_η/f_P 改变容量/效率/支撑点参数进入 MILP（接口待接），
            # 而非改变代理系数。D_feedback=0 时 open≡closed 由此保证。
            l3_obj += self.w["bat_thr"] * gp.quicksum(
                (P_ch[t] + P_dis[t]) * self.dt_h / E for t in range(T))
        m.setObjective(l3_obj, gp.GRB.MINIMIZE)
        m.setParam("MIPGap", 0.01)          # v9 §17.1：仅 L3 显式 1%（官方默认 1e-4 需覆盖）
        m.optimize()
        st3 = m.status
        # 【修复 2026-08-11】L3 判定放宽：OPTIMAL 及 TIME_LIMIT/SUBOPTIMAL（SolCount>0、
        # 有 incumbent 可行解）均纳入。此前只认 OPTIMAL，把 L3 层 60s 超时未收敛到 1% gap
        # 的窗口误判为"不可行"而整窗丢弃（bstar 全年 20000-25000 区间 5-7 个窗口，
        # 实为超时非无解；L1/L2 层已 OPTIMAL，time-limit incumbent 是工程可行解）。
        feasible = (st3 in (gp.GRB.OPTIMAL, gp.GRB.TIME_LIMIT, gp.GRB.SUBOPTIMAL)
                    and m.SolCount > 0)
        l3_opt = float(m.objVal) if feasible else 0.0
        mip_gap_l3 = float(m.MIPGap) if st3 in (gp.GRB.TIME_LIMIT, gp.GRB.SUBOPTIMAL) else 0.0
        # —— 提取轨迹 ——
        p_ch = np.array([P_ch[t].X for t in range(T)])
        p_dis = np.array([P_dis[t].X for t in range(T)])
        p_fc = np.array([P_fc[t].X for t in range(T)])
        p_el = np.array([P_el[t].X for t in range(T)])
        p_curt = np.array([P_curt[t].X for t in range(T)])
        p_uns = np.array([P_uns[t].X for t in range(T)])
        u_fc_v = np.array([round(u_fc[t].X) for t in range(T)])
        u_el_v = np.array([round(u_el[t].X) for t in range(T)])
        soc_traj = np.array([SOC[t].X for t in range(T + 1)])
        h2_traj = np.array([H2[t].X for t in range(T + 1)])
        env.dispose()

        # —— 守恒残差核验（数学闭合；口径与 v9 §16.1 单元测试一致）——
        # SOC 残差：归一化为无量纲分数（除以 E），阈值 1e-6（与测试1同口径）。
        # 注：早期版本为 kWh 量纲 (ΔSOC·E − ΔE)，会把 solver 的 SOC 分数方程
        # 违反量（~FeasTol=1e-6 级）放大 E 倍到 1e-3 kWh，属口径误配；已修正。
        soc_res = 0.0
        for t in range(T):
            lhs = soc_traj[t + 1] - soc_traj[t]
            rhs = (self.eta_ch * p_ch[t] - p_dis[t] / self.eta_dis) * self.dt_h / E
            soc_res = max(soc_res, abs(lhs - rhs))
        h2_res = 0.0
        for t in range(T):
            cons = p_fc[t] * self.dt_h / (eta_fc * LHV_H2_KWH_PER_KG)
            prod = p_el[t] * self.dt_h * eta_el / LHV_H2_KWH_PER_KG
            leak = self.r_leak_h * h2_traj[t] * self.dt_h
            h2_res = max(h2_res, abs((h2_traj[t + 1] - h2_traj[t]) - (prod - cons - leak)))

        commands = np.stack([p_ch, p_dis, p_fc, p_el, p_curt, p_uns], axis=1)
        return WindowResult(commands, u_fc_v, u_el_v, soc_traj, h2_traj,
                            l1_opt, l2_opt, l3_opt, st1, st2, st3,
                            time.time() - t0, feasible, soc_res, h2_res, mip_gap_l3)


# ==================================================================
# Smoke test（1 窗口 96 步，2018 真实数据段）
# ==================================================================
def _smoke():
    import pandas as pd
    csv = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    df = pd.read_csv(csv)
    d18 = df[df["local_civil_year"] == 2018].reset_index(drop=True)
    cfg = Stage2BEMSConfig(
        pv_kwp=388.2674203719944, bat_kwh=1734.3933384865522,
        pemfc_kw=95.02249870449305, pemel_kw=224.24405589699745,
        h2_kg=4450.2877332270145,
    )
    # 取 2018-06-15（夏季典型日）96 步
    day = d18.iloc[96 * 166:96 * 167]
    pv_raw = day["p_pv_available_reconstructed_nonnegative_kW"].to_numpy(dtype=float)
    load = day["p_building_load_reconstructed_kW"].to_numpy(dtype=float)
    pv = pv_raw * (cfg.pv_kwp / np.max(pv_raw))   # 归一化到配置容量
    state = MPCState(soc=0.60, h2_kg=cfg.h2_kg * 0.5)

    print("=" * 70)
    print("milp_scheduler smoke test：1 窗口 × 96 步（2018-06-15，夏季典型日）")
    print(f"配置: BAT {cfg.bat_kwh:.0f} kWh (Pmax {cfg.bat_kwh*cfg.bat_c_rate:.0f} kW) | "
          f"FC {cfg.pemfc_kw:.0f} kW | EL {cfg.pemel_kw:.0f} kW | H2 {cfg.h2_kg:.0f} kg")
    print("=" * 70)

    sched = MilpScheduler(cfg, verbose=False)
    res = sched.solve_window(pv, load, state)
    print(f"L1/L2/L3 status: {res.status_l1}/{res.status_l2}/{res.status_l3} "
          f"(2=OPTIMAL) | 可行: {res.feasible}")
    print(f"L1_opt={res.l1_opt:.4f} kWh | L2_opt={res.l2_opt:.6f} | L3_opt={res.l3_opt:.4f}")
    print(f"求解时间: {res.solve_time_s:.2f} s")
    print(f"SOC 守恒残差: {res.soc_conservation_res:.3e} (≤1e-6) | "
          f"H2 守恒残差: {res.h2_conservation_res:.3e} kg (≤1e-6)")
    if res.feasible:
        print(f"命令量: P_ch {res.commands[:,0].sum()*0.25:.0f} kWh | P_dis {res.commands[:,1].sum()*0.25:.0f} | "
              f"P_fc {res.commands[:,2].sum()*0.25:.0f} | P_el {res.commands[:,3].sum()*0.25:.0f} | "
              f"弃光 {res.commands[:,4].sum()*0.25:.0f} | 失负荷 {res.commands[:,5].sum()*0.25:.0f}")
        print(f"FC 运行 {res.u_fc.sum()} 步 / EL 运行 {res.u_el.sum()} 步 | "
              f"SOC {res.soc_traj[0]:.3f}→{res.soc_traj[-1]:.3f} | H2 {res.h2_traj[0]:.0f}→{res.h2_traj[-1]:.0f} kg")

    out = {"timestamp": datetime.now().isoformat(),
           "config": {"bat_kwh": cfg.bat_kwh, "pemfc_kw": cfg.pemfc_kw,
                      "pemel_kw": cfg.pemel_kw, "h2_kg": cfg.h2_kg},
           "feasible": res.feasible,
           "status": [res.status_l1, res.status_l2, res.status_l3],
           "l1_opt": res.l1_opt, "l2_opt": res.l2_opt, "l3_opt": res.l3_opt,
           "solve_time_s": res.solve_time_s,
           "soc_conservation_res": res.soc_conservation_res,
           "h2_conservation_res": res.h2_conservation_res,
           "summary": {"pass": int(res.feasible and res.soc_conservation_res <= 1e-6
                                   and res.h2_conservation_res <= 1e-6), "fail": 0}}
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = out["summary"]["pass"] == 1
    print("-" * 70)
    print(("SMOKE TEST PASS ✅（MILP 核心可求解，守恒闭合）" if ok else "SMOKE TEST FAIL ❌"))
    print(f"结果已保存：{OUT_JSON}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _smoke()
