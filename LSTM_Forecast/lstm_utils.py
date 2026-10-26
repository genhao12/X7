# -*- coding: utf-8 -*-
"""
共享模块（v9 对齐版）：15 分钟粒度光伏 / 建筑负荷 LSTM 预测
================================================================
v9 §3.1/§3.4/§13 五处改造（相对初版 lstm_utils.py）：
  1) 年度切分：2016 训练 / 2017 验证 / 2018 测试（替代 70/15/15，§3.1）
  2) 5 固定种子训练 + 2017 验证选中位表现 seed + 2018 只评估冻结的 1 个（§13）
  3) 主指标对齐 §3.4：PV=日照时段 nMAE、Load=MASE（seasonal lag=96 点=24h）；
     RMSE/MAE/R² 辅助报告
  4) PV 输出强制 ≥0（clip_nonneg，§3.3）
  5) 归一化参数仅 2016 训练段拟合（防泄漏，与初版一致）
输入过去 seq_len 步（默认 96=1 天），direct multi-output 直接输出未来 horizon 步（默认 96=1 天）。
"""

import json
import os

import matplotlib

matplotlib.use("Agg")  # 无界面后端，纯脚本运行
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import r2_score
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, TensorDataset

# 中文字体支持（Windows 自带的微软雅黑）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 固定 5 个随机种子（v9 §13：2016 训练 5 seeds，2017 选中位，2018 只评 1 个）
DEFAULT_SEEDS = [42, 202, 777, 1337, 20260807]

# ------------------------------------------------------------------
# 数据加载与年度切分
# ------------------------------------------------------------------

def load_series(csv_path: str, target_col: str) -> pd.DataFrame:
    """读取 CSV，返回以时间戳为索引、只含目标列的 DataFrame。
    缺失点用 ffill 补齐（64 号 CSV 含 165 个缺失点，与项目预处理管线
    build_data_pipeline 的 ffill 一致），保持 105,216 行与年度对齐。"""
    df = pd.read_csv(csv_path, usecols=["timestamp_local_civil", target_col])
    df["timestamp_local_civil"] = pd.to_datetime(df["timestamp_local_civil"])
    df = df.set_index("timestamp_local_civil").sort_index()
    df[target_col] = pd.to_numeric(df[target_col], errors="coerce")
    df[target_col] = df[target_col].ffill()   # 缺失点前值填充（保持行数不变）
    df[target_col] = df[target_col].bfill()   # 头部残余缺失回填
    return df


def split_by_year(df: pd.DataFrame, target_col: str):
    """按年度切分（v9 §3.1）：2016 训练 / 2017 验证 / 2018 测试。
    2016 为闰年 35,136 点，2017/2018 各 35,040 点。"""
    years = df.index.year
    train = df.loc[years == 2016, target_col].to_numpy(dtype=np.float32)
    val = df.loc[years == 2017, target_col].to_numpy(dtype=np.float32)
    test = df.loc[years == 2018, target_col].to_numpy(dtype=np.float32)
    assert len(train) == 35136, f"2016 训练点数异常: {len(train)}"
    assert len(val) == 35040 and len(test) == 35040, f"2017/2018 点数异常: {len(val)}/{len(test)}"
    return train, val, test


def make_sequences(values, seq_len, horizon, stride):
    """滑窗构造 (输入序列, 输出序列) 样本对。"""
    X, y = [], []
    for i in range(0, len(values) - seq_len - horizon + 1, stride):
        X.append(values[i:i + seq_len])
        y.append(values[i + seq_len:i + seq_len + horizon])
    return np.asarray(X, dtype=np.float32)[:, :, None], \
        np.asarray(y, dtype=np.float32)


# ------------------------------------------------------------------
# 模型
# ------------------------------------------------------------------

class LSTMForecaster(nn.Module):
    """LSTM 编码最后一步隐状态 -> 全连接直接输出 horizon 步（direct multi-output）。"""

    def __init__(self, input_size=1, hidden_size=64, num_layers=2,
                 dropout=0.1, horizon=96):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, horizon)

    def forward(self, x):
        _, (h, _) = self.lstm(x)      # h: (num_layers, B, hidden)
        return self.fc(h[-1])         # 取最后一层 -> (B, horizon)


# ------------------------------------------------------------------
# 训练（单个 seed）
# ------------------------------------------------------------------

def train_model(model, train_loader, val_loader, epochs, lr, patience,
                device, seed):
    """Adam + MSELoss + ReduceLROnPlateau + 早停（恢复最优权重）。"""
    torch.manual_seed(seed)
    np.random.seed(seed)

    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=3)

    history = {"train": [], "val": []}
    best_val = float("inf")
    best_state = None
    wait = 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * xb.size(0)
        train_loss /= len(train_loader.dataset)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                loss = criterion(model(xb), yb)
                val_loss += loss.item() * xb.size(0)
        val_loss /= len(val_loader.dataset)
        scheduler.step(val_loss)

        history["train"].append(train_loss)
        history["val"].append(val_loss)

        if val_loss < best_val - 1e-4:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                print(f"  [seed {seed}] 早停于 epoch {epoch}")
                break

        if epoch == 1 or epoch % 10 == 0:
            print(f"  [seed {seed}] epoch {epoch:3d} | train {train_loss:.6f} | val {val_loss:.6f}")

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


# ------------------------------------------------------------------
# 评估（不重叠逐日块预测）
# ------------------------------------------------------------------

def predict_blocks(model, values_all, start_idx, seq_len, horizon,
                   device, clip_nonneg=False):
    """从 start_idx 起按不重叠的 horizon 块滚动预测，返回 (actual, pred) 标度值。
    clip_nonneg=True 时预测值 clip ≥0（v9 §3.3：PV 非负）。"""
    model.eval()
    actuals, preds = [], []
    with torch.no_grad():
        start = start_idx
        while start + seq_len + horizon <= len(values_all):
            x = values_all[start:start + seq_len][None, :, None]
            x = torch.from_numpy(x).to(device)
            y = model(x).cpu().numpy().reshape(-1)
            if clip_nonneg:
                y = np.maximum(y, 0.0)
            actuals.append(values_all[start + seq_len:start + seq_len + horizon])
            preds.append(y)
            start += horizon
    return np.concatenate(actuals), np.concatenate(preds)


def seasonal_naive_mae_train(train, lag=96):
    """MASE 基准：训练段 seasonal naive（|y_t − y_{t−lag}|）的 MAE（v9 §3.4，lag=96=24h）。"""
    if len(train) <= lag:
        return 1e-9
    return float(np.mean(np.abs(train[lag:] - train[:-lag])))


def compute_metrics(actual, pred, train=None, lag=96, daytime=True):
    """指标集：主指标 nMAE(日照时段) / MASE + 辅助 RMSE/MAE/R²/MAPE。
    - nmae_daytime：日照时段（actual>5%峰值）内 MAE / 该时段 mean(actual)
    - mase：MAE / 训练段 seasonal naive MAE（lag=96，v9 §3.4）
    """
    m = {}
    m["rmse_kW"] = float(np.sqrt(np.mean((actual - pred) ** 2)))
    m["mae_kW"] = float(np.mean(np.abs(actual - pred)))
    m["r2"] = float(r2_score(actual, pred))

    peak = max(float(actual.max()), 1e-6)
    mask = actual > 0.05 * peak
    if mask.sum() > 0:
        m["mape_pct"] = float(100.0 * np.mean(
            np.abs((actual[mask] - pred[mask]) / actual[mask])))
        m["mape_coverage"] = float(mask.mean())
    else:
        m["mape_pct"] = float("nan")
        m["mape_coverage"] = 0.0

    # PV 主指标：日照时段 nMAE（v9 §3.4）
    if mask.sum() > 0:
        m["nmae_daytime"] = float(
            np.mean(np.abs(actual[mask] - pred[mask])) /
            max(np.mean(actual[mask]), 1e-9))
    else:
        m["nmae_daytime"] = float("nan")

    # Load 主指标：MASE（基准来自训练段，v9 §3.4）
    if train is not None and len(train) > lag:
        m["mase"] = m["mae_kW"] / max(seasonal_naive_mae_train(train, lag), 1e-9)
    else:
        m["mase"] = float("nan")

    return m


# ------------------------------------------------------------------
# 绘图
# ------------------------------------------------------------------

def plot_loss(history, out_path, tag):
    plt.figure(figsize=(8, 5))
    plt.plot(history["train"], label="train")
    plt.plot(history["val"], label="validation (2017)")
    plt.xlabel("epoch")
    plt.ylabel("MSE loss (scaled)")
    plt.title(f"Training / validation loss — {tag}")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_forecast(timestamps, actual, pred, title, out_path, zoom_days=14):
    plt.figure(figsize=(13, 4.5))
    plt.plot(timestamps, actual, label="actual", lw=1.0)
    plt.plot(timestamps, pred, label="predicted (LSTM)", lw=1.0, alpha=0.85)
    plt.legend()
    plt.grid(alpha=0.3)
    plt.xlabel("time")
    plt.ylabel("kW")
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()

    n = zoom_days * 96
    if len(timestamps) > n:
        zoom_path = out_path.replace("_test.png", "_zoom.png")
        plt.figure(figsize=(13, 4.5))
        plt.plot(timestamps[:n], actual[:n], label="actual", lw=1.2)
        plt.plot(timestamps[:n], pred[:n], label="predicted (LSTM)", lw=1.2, alpha=0.85)
        plt.legend()
        plt.grid(alpha=0.3)
        plt.xlabel("time")
        plt.ylabel("kW")
        plt.title(title + f"（前 {zoom_days} 天放大）")
        plt.tight_layout()
        plt.savefig(zoom_path, dpi=150)
        plt.close()


# ------------------------------------------------------------------
# 主流程（v9：年度切分 + 5-seed + 中位 seed 冻结 + 2018 一次性评估）
# ------------------------------------------------------------------

def run(cfg: dict):
    print("=" * 70)
    print(f"LSTM 预测目标列: {cfg['target_col']}  ({cfg['series_name']})")
    print("年度切分: 2016 训练 / 2017 验证(选 seed) / 2018 测试(一次性 held-out)")
    print("=" * 70)

    os.makedirs(cfg["out_dir"], exist_ok=True)

    # 1. 加载 + 年度切分（v9 §3.1）
    df = load_series(cfg["csv_path"], cfg["target_col"])
    print(f"数据范围: {df.index[0]} ~ {df.index[-1]}，共 {len(df)} 条")
    train, val, test = split_by_year(df, cfg["target_col"])
    print(f"切分: 2016 train {len(train)} | 2017 val {len(val)} | 2018 test {len(test)}")

    # 2. 归一化（仅 2016 训练段拟合，v9 §3.1 防泄漏）
    scaler = MinMaxScaler()
    train_s = scaler.fit_transform(train.reshape(-1, 1)).ravel()
    val_s = scaler.transform(val.reshape(-1, 1)).ravel()
    test_s = scaler.transform(test.reshape(-1, 1)).ravel()

    # MASE 基准（训练段 seasonal naive，v9 §3.4）
    naive_mae = seasonal_naive_mae_train(train, cfg.get("lag", 96))

    # 3. 滑窗样本
    seq_len, horizon, stride = cfg["seq_len"], cfg["horizon"], cfg["stride"]
    Xtr, ytr = make_sequences(train_s, seq_len, horizon, stride)
    Xva, yva = make_sequences(val_s, seq_len, horizon, stride)
    print(f"滑窗样本: 2016 train {len(Xtr)} | 2017 val {len(Xva)} "
          f"(输入 {seq_len} 步 -> 输出 {horizon} 步)")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device}")

    train_ds = TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(ytr))
    val_ds = TensorDataset(torch.from_numpy(Xva), torch.from_numpy(yva))
    train_loader = DataLoader(train_ds, batch_size=cfg["batch_size"],
                              shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=cfg["batch_size"],
                            shuffle=False, num_workers=0)

    # 全部标度序列（用于块预测的上下文拼接）
    all_scaled = np.concatenate([train_s, val_s, test_s])
    val_start = len(train_s)          # 2017 块预测起点
    test_start = len(train_s) + len(val_s)  # 2018 块预测起点
    clip = cfg.get("clip_nonneg", False)
    primary = cfg.get("primary_metric", "nmae_daytime")

    # 4. 5-seed 训练 + 2017 验证选中位表现 seed（v9 §13）
    seeds = cfg.get("seeds", DEFAULT_SEEDS)
    print(f"5-seed 训练（seeds={seeds}），2017 验证选择中位表现 seed...")
    val_primary = {}
    histories = {}
    best_models = {}
    for seed in seeds:
        torch.manual_seed(seed)
        model = LSTMForecaster(input_size=1, hidden_size=cfg["hidden_size"],
                               num_layers=cfg["num_layers"],
                               dropout=cfg["dropout"], horizon=horizon).to(device)
        model, history = train_model(model, train_loader, val_loader,
                                     cfg["epochs"], cfg["lr"], cfg["patience"],
                                     device, seed)
        # 2017 验证块预测
        act_s, pred_s = predict_blocks(model, all_scaled, val_start,
                                       seq_len, horizon, device, clip)
        act = scaler.inverse_transform(act_s.reshape(-1, 1)).ravel()
        pred = scaler.inverse_transform(pred_s.reshape(-1, 1)).ravel()
        mm = compute_metrics(act, pred, train, cfg.get("lag", 96))
        val_primary[seed] = mm.get(primary, mm["mae_kW"])
        histories[seed] = history
        best_models[seed] = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        print(f"  seed {seed}: 2017 {primary} = {val_primary[seed]:.4f}")

    arr = np.array([val_primary[s] for s in seeds])
    chosen_seed = seeds[int(np.argsort(arr)[len(seeds) // 2])]  # 中位表现 seed
    print(f"\n2017 5-seed {primary}: mean={arr.mean():.4f} std={arr.std():.4f} "
          f"→ 选中位 seed = {chosen_seed}")

    # 5. 用中位 seed 冻结模型，2018 一次性评估（v9 §13）
    model = LSTMForecaster(input_size=1, hidden_size=cfg["hidden_size"],
                           num_layers=cfg["num_layers"],
                           dropout=cfg["dropout"], horizon=horizon).to(device)
    model.load_state_dict(best_models[chosen_seed])
    torch.save(model.state_dict(),
               os.path.join(cfg["out_dir"], f"{cfg['series_name']}_lstm.pt"))
    np.savez(os.path.join(cfg["out_dir"], f"{cfg['series_name']}_scaler.npz"),
             data_min=scaler.data_min_, scale=scaler.scale_)
    plot_loss(histories[chosen_seed],
              os.path.join(cfg["out_dir"], f"{cfg['series_name']}_loss.png"),
              cfg["series_name"])

    act_s, pred_s = predict_blocks(model, all_scaled, test_start,
                                   seq_len, horizon, device, clip)
    act = scaler.inverse_transform(act_s.reshape(-1, 1)).ravel()
    pred = scaler.inverse_transform(pred_s.reshape(-1, 1)).ravel()

    m = compute_metrics(act, pred, train, cfg.get("lag", 96))
    print("\n2018 测试集评估（一次性 held-out，冻结 seed=%d）:" % chosen_seed)
    for k, v in m.items():
        print(f"  {k}: {v:.4f}")

    # 6. 导出 2018 预测 CSV
    t0 = df.index[test_start + seq_len]
    t_index = pd.date_range(t0, periods=len(act), freq=cfg["freq"])
    out_df = pd.DataFrame({"timestamp": t_index,
                           "actual_kW": act,
                           "predicted_kW": pred})
    pred_csv = os.path.join(cfg["out_dir"], f"{cfg['series_name']}_predictions.csv")
    out_df.to_csv(pred_csv, index=False)

    # 7. 绘图
    title = f"{cfg['title']} — LSTM 逐日预测 vs 实际（2018 测试集）"
    plot_forecast(t_index, act, pred, title,
                  os.path.join(cfg["out_dir"], f"{cfg['series_name']}_forecast_test.png"),
                  zoom_days=14)

    # 8. 汇总 JSON（含 5-seed 汇总 + 中位 seed + 2018 指标）
    summary = {
        "split": {"train_2016": len(train), "val_2017": len(val), "test_2018": len(test)},
        "seeds": {str(s): {"val_primary": float(val_primary[s])} for s in seeds},
        "seed_summary": {"primary": primary, "mean": float(arr.mean()),
                         "std": float(arr.std()), "chosen_seed": chosen_seed},
        "metrics_2018": m,
        "naive_mae_train_lag96": float(naive_mae),
        "history_chosen": histories[chosen_seed],
        "cfg": cfg,
    }
    with open(os.path.join(cfg["out_dir"], f"{cfg['series_name']}_metrics.json"),
              "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n输出目录: {os.path.abspath(cfg['out_dir'])}")
    print(f"  - {cfg['series_name']}_predictions.csv  2018 逐日块预测结果")
    print(f"  - {cfg['series_name']}_forecast_test.png / _zoom.png")
    print(f"  - {cfg['series_name']}_loss.png")
    print(f"  - {cfg['series_name']}_lstm.pt / _scaler.npz（中位 seed 冻结模型）")
    print(f"  - {cfg['series_name']}_metrics.json（5-seed 汇总 + 2018 指标）")
    print("=" * 70)
