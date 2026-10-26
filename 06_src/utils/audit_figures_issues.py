# -*- coding: utf-8 -*-
"""批量审计 figures 目录图片：
1) 豆腐块检测（中文字体缺失时 matplotlib 渲染的规则空心/实心小方框）
2) 墨迹密度统计（辅助判断文字是否存在）
只读，不修改任何图片。
"""
import os
import numpy as np
from PIL import Image
from scipy import ndimage

FIG_DIR = r"D:/matlab/C/07_results/figures"
Image.MAX_IMAGE_PIXELS = None


def detect_missing_glyph_boxes(png_path, ink_thr=170):
    """检测疑似缺字形方框（豆腐块）。
    豆腐块特征：连通域 bbox 近似正方形(8~60px)，填充率呈现空心框(0.12~0.55)或实心(>0.78)。
    返回 (候选数, 候选列表)
    """
    im = Image.open(png_path).convert("L")
    a = np.asarray(im, dtype=np.uint8)
    ink = a < ink_thr
    labels, n = ndimage.label(ink)
    if n == 0:
        return 0, []
    objs = ndimage.find_objects(labels)
    cands = []
    for i, sl in enumerate(objs):
        hgt = sl[0].stop - sl[0].start
        wid = sl[1].stop - sl[1].start
        if not (8 <= hgt <= 60 and 8 <= wid <= 60):
            continue
        area = (labels[sl] == i + 1).sum()
        fill = area / (hgt * wid)
        # 空心框：边框型填充；实心方块：高填充
        if (0.12 <= fill <= 0.55 and abs(wid - hgt) / max(wid, hgt) < 0.35) or (fill > 0.78 and abs(wid - hgt) / max(wid, hgt) < 0.2):
            cands.append((sl[0].start, sl[1].start, hgt, wid, round(fill, 2)))
    return len(cands), cands[:8]


def ink_density(png_path, ink_thr=170):
    im = Image.open(png_path).convert("L")
    a = np.asarray(im, dtype=np.uint8)
    return round((a < ink_thr).mean() * 100, 2)


def main():
    files = sorted(f for f in os.listdir(FIG_DIR) if f.lower().endswith(".png"))
    print(f"{'图片':<44}{'墨迹%':>7}{'豆腐块数':>9}")
    results = []
    for f in files:
        p = os.path.join(FIG_DIR, f)
        try:
            n, cands = detect_missing_glyph_boxes(p)
            d = ink_density(p)
            flag = "  <<< 豆腐块" if n >= 8 else ""
            print(f"{f:<44}{d:>7}{n:>9}{flag}")
            results.append((f, n, d, cands))
        except Exception as e:
            print(f"{f:<44}  检测失败: {type(e).__name__}: {e}")
    print("\n== 豆腐块数量 >= 8 的图（需人工确认） ==")
    for f, n, d, cands in results:
        if n >= 8:
            print(f"  {f}: {n} 个候选, 前几个位置(行,列,高,宽,填充): {cands[:5]}")
    print("审计完成")


if __name__ == "__main__":
    main()
