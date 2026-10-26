# -*- coding: utf-8 -*-
"""
plot_style — 出版级matplotlib样式配置
所有模块生成图表前调用 apply_pub_style() 即可统一风格
标准: 字号>=10pt / ColorBrewer配色 / 300DPI / 无装饰 / 色盲友好
"""
import matplotlib
import matplotlib.pyplot as plt
from pathlib import Path

# ColorBrewer Qualitative Set2 (色盲安全, 期刊常用)
PALETTE = {
    'pv':        '#FC8D62',  # 橙
    'load':      '#66C2A5',  # 青绿
    'net_surplus': '#ABDDA4',  # 浅绿(富余)
    'net_deficit': '#D73027',  # 红(亏缺)
    'bat':       '#3288BD',  # 蓝(电池)
    'fc':        '#FDAE61',  # 橙黄(燃料电池)
    'el':        '#74ADD1',  # 浅蓝(电解槽)
    'reference': '#999999',  # 灰(参考线)
    'primary':   '#1A1A1A',  # 黑(主文字)
    'secondary': '#666666',  # 灰(次要文字)
}

# Sequential (退化曲线/热力图用)
SEQUENTIAL = ['#FFFFCC', '#A1DAB4', '#41B6C4', '#2C7FB8', '#253494']


def apply_pub_style():
    """应用出版级matplotlib全局样式"""
    plt.style.use('seaborn-v0_8-whitegrid')
    plt.rcParams.update({'axes.unicode_minus': False, 
        # 字体
        'font.family': 'sans-serif',
        'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
        'font.size': 11,
        'axes.titlesize': 13,
        'axes.labelsize': 12,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10,
        'legend.fontsize': 10,
        # 线条
        'axes.linewidth': 0.8,
        'lines.linewidth': 1.5,
        'lines.markersize': 4,
        # 网格
        'grid.alpha': 0.25,
        'grid.linewidth': 0.5,
        # 图例
        'legend.frameon': True,
        'legend.framealpha': 0.9,
        'legend.edgecolor': '#CCCCCC',
        # 保存
        'savefig.dpi': 300,
        'savefig.bbox': 'tight',
        'savefig.pad_inches': 0.08,
        # 去掉顶部/右侧脊柱
        'axes.spines.top': False,
        'axes.spines.right': False,
    })


def save_fig(fig, filepath, dpi=300):
    """保存图表到07_results/figures/"""
    p = Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(p), dpi=dpi, bbox_inches='tight', pad_inches=0.08)
    plt.close(fig)
    print(f"Figure saved: {p.name} ({p.stat().st_size // 1024}KB)")


def add_caption(fig, fig_num, caption_text):
    """添加图编号和说明文字(论文格式)"""
    fig.text(0.5, -0.02, f"Fig. {fig_num}. {caption_text}",
             ha='center', va='top', fontsize=9, style='italic',
             color='#666666', wrap=True)
