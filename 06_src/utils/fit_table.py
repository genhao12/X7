# -*- coding: utf-8 -*-
"""
表格自适应缩放工具
====================
matplotlib 的 ax.table() 在行数较多或 scale 较大时，表格总高可能超出
所在子图（axes）的可用高度，导致表格与相邻子图/画布重叠（视觉"溢出"）。

本工具在渲染后按子图实际高度自动调整 table 的 y 向 scale，使表格总高
约为子图高度的 target_frac（默认 75%），既保证文字垂直空间充足，
又不超出子图边界。

用法（在创建表格之后、savefig 之前调用）：
    from utils.fit_table import auto_fit_table
    table = ax.table(...)
    table.auto_set_font_size(False); table.set_fontsize(7.0)
    k = auto_fit_table(ax, table)          # k 为最终 y 向 scale
    fig.savefig(...)

注意：
- 本函数会重置 table 的 x/y scale 后重新计算（覆盖之前手写的 scale 调用），
  因此请将脚本中已有的 table.scale(1.0, xx) 替换为本函数调用。
- 必须在 fig.canvas.draw() 可用的环境下调用（Agg 后端即可）。
"""
from __future__ import annotations


def auto_fit_table(ax, table, target_frac: float = 0.75,
                   min_scale: float = 0.5, max_scale: float = 4.0,
                   x_scale: float = 1.0):
    """将表格垂直 scale 调整为子图高度的 target_frac 倍。

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        表格所在坐标轴（其高度作为基准）。
    table : matplotlib.table.Table
        已创建并设置好字体大小的表格对象。
    target_frac : float
        表格总高占子图高度的目标比例（默认 0.75）。
    min_scale, max_scale : float
        y 向 scale 的钳制范围，防止极端值。
    x_scale : float
        水平方向的固定 scale（默认 1.0；表格列宽不足时可传 >1 加宽）。

    Returns
    -------
    k : float 或 None
        最终应用的 y 向 scale；失败时返回 None。
    """
    fig = ax.figure
    if fig is None:
        return None
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    # 重置 scale（覆盖脚本中可能的旧 scale 调用，保留指定 x 向）
    table.scalex = x_scale
    table.scaley = 1.0
    fig.canvas.draw()

    cells = table.get_celld()
    if not cells:
        return None

    # 表格总高（显示坐标像素）
    ys = []
    for cell in cells.values():
        bb = cell.get_bbox()
        d0 = ax.transData.transform((bb.x0, bb.y0))
        d1 = ax.transData.transform((bb.x1, bb.y1))
        ys.append(d0[1])
        ys.append(d1[1])
    cur_h = abs(max(ys) - min(ys))
    if cur_h <= 1e-9:
        return None

    ax_h = ax.get_window_extent(renderer).height
    if ax_h <= 1e-9:
        return None

    k = ax_h * target_frac / cur_h
    k = min(max(k, min_scale), max_scale)
    table.scale(1.0, k)
    fig.canvas.draw()
    return k
