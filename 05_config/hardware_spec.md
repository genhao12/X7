# 硬件规格与资源需求

## 1. 计算量评估

| 阶段 | 场景 | 仿真步数 | 预计总步数 | 预计耗时 |
|------|------|----------|-----------|----------|
| MVP | 1配置×1窗口×3运行×2路径×16联盟 | 672步/次×22次 | 14,784 | <5min |
| W1-V1 | 336窗口筛选+72负对照 | 672步/次×408次 | 274,176 | ~2h |
| W1-V2 | N_eff×6×16(预估N_eff=4) | 672步/次×384次 | 258,048 | ~2h |
| W1-V3a/b | 100 LHS×2×4窗口 | 672步/次×800次 | 537,600 | ~4h |
| W1-V4 | 6配置×3EMS×4窗口+12 | 672步/次×84次 | 56,448 | ~30min |
| **合计** | | | **~5.1M步** | **~8-10h** |

## 2. 硬件规格

### 2.1 最低规格(MVP + W1-V1可运行)

| 资源 | 规格 | 说明 |
|------|------|------|
| CPU | >=8核 x86_64 | 单线程性能优先(仿真为串行) |
| 内存 | >=16GB | UCSD 3年数据+中间结果驻留 |
| 磁盘 | >=50GB SSD | 原始数据(5GB)+处理数据(10GB)+结果(20GB)+代码(5GB) |
| GPU | 不需要 | MVP/W1核心算法为CPU计算 |
| 网络 | 不需要 | 离线运行(数据已本地化) |

### 2.2 推荐规格(W1全量高效运行)

| 资源 | 规格 | 说明 |
|------|------|------|
| CPU | >=16核 x86_64 | 多进程并行(V1-V4场景可并行化) |
| 内存 | >=32GB | 大规模LHS采样+Bootstrap中间结果 |
| 磁盘 | >=100GB NVMe SSD | 快速I/O(parquet读写+临时文件) |
| GPU | 可选(RTX 3060+) | 仅扩展阶段XGBoost训练加速 |
| 网络 | 不需要 | 离线运行 |

### 2.3 已验证环境

| 组件 | 版本 | 状态 |
|------|------|------|
| Python | 3.13.12 (managed) | ✓ 已验证 |
| Node.js | 22.22.2 (managed) | ✓ 已验证 |
| OS | Windows 11 | ✓ 已验证 |
| BLAST-Lite | Sony/Murata + 250Ah | ✓ 需trapz→trapezoid补丁 |
| NAICNO | best_12param.pt | ✓ 已绕过PyTorch直接解析 |

## 3. 软件依赖

### 3.1 Python依赖(详见requirements.txt)

| 包 | 最低版本 | 用途 | MVP必需 |
|----|---------|------|---------|
| numpy | 1.26.0 | 数值计算 | 是 |
| scipy | 1.12.0 | 统计/优化 | 是 |
| pandas | 2.2.0 | 时间序列 | 是 |
| openpyxl | 3.1.0 | xlsx读取 | 是 |
| matplotlib | 3.8.0 | 图表 | 是 |
| python-docx | 1.1.0 | Word报告 | 是 |
| pytest | 8.0.0 | 测试 | 是 |
| scikit-learn | 1.4.0 | Spearman | 否(V3阶段) |
| torch | 2.2.0 | NAICNO训练 | 否(已绕过) |

### 3.2 BLAST-Lite兼容层

```python
# 在BLAST-Lite导入前添加:
import numpy as np
if not hasattr(np, 'trapz'):
    np.trapz = np.trapezoid
```

### 3.3 Mock依赖(无需安装)

```python
# h5pyd mock (BLAST-Lite HPC模式, MVP不使用):
try:
    import h5pyd
except ImportError:
    h5pyd = None  # 代码中用if h5pyd is None: fallback to local

# geopy mock (BLAST-Lite地理编码, MVP不使用):
try:
    from geopy.geocoders import Nominatim
except ImportError:
    Nominatim = None  # 代码中用if Nominatim is None: skip geocoding
```