# -*- coding: utf-8 -*-
"""
修复异常分辨率图片：l4_crossyear_fullfeedback.png (753102x2880, 21.7亿像素)
策略：禁用 PIL 像素炸弹检查 -> 分块重采样（规避 8.7GB 内存峰值）-> 拼接输出
输出：l4_crossyear_fullfeedback_fixed.png（不覆盖原文件）
运行：D:/Anaconda/envs/DL/python.exe 06_src/utils/fix_l4_crossyear_figure.py
"""
import os
import math
from PIL import Image

SRC = r"D:\matlab\C\07_results\figures\l4_crossyear_fullfeedback.png"
DST = r"D:\matlab\C\07_results\figures\l4_crossyear_fullfeedback_fixed.png"
TARGET_W = 4800   # 与脚本 figsize=(16,10), dpi=300 的预期宽度一致
CHUNK_W = 10000   # 分块源宽度（每块内存约 10000*2880*3 ≈ 86 MB，安全）

Image.MAX_IMAGE_PIXELS = None  # 禁用 DecompressionBombError

im = Image.open(SRC)
w0, h0 = im.size
print(f"原图尺寸: {w0} x {h0}")

if im.mode in ("RGBA", "LA", "P"):
    im = im.convert("RGB")

scale = TARGET_W / w0
n_chunks = math.ceil(w0 / CHUNK_W)
print(f"分块数: {n_chunks}, 缩放比例: {scale:.6f}")

canvas = Image.new("RGB", (TARGET_W, h0), "white")
for i in range(n_chunks):
    x0 = i * CHUNK_W
    x1 = min(x0 + CHUNK_W, w0)
    block = im.crop((x0, 0, x1, h0))
    tx0 = round(x0 * scale)
    tx1 = round(x1 * scale)
    bw = max(1, tx1 - tx0)
    block = block.resize((bw, h0), Image.LANCZOS)
    canvas.paste(block, (tx0, 0))
    print(f"  块 {i+1}/{n_chunks}: 源[{x0}:{x1}] -> 目标宽 {bw}px")

canvas.save(DST, "PNG")
print(f"修复完成: {DST}")
print(f"输出尺寸: {TARGET_W} x {h0}, 文件 {os.path.getsize(DST)/1024:.0f} KB")
