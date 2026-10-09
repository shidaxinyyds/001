# -*- coding: utf-8 -*-
"""把两帧蜀山定缺图的「色盘带」并排裁出来，并打印盘心处的 HSV 实测值。

为什么要看图 + 看数：上一轮只拿到「一帧找到 3 个圆盘、另一帧找到 0 个」，这只能
说明判据在其中一帧上没吃满，不能说明是颜色窗口窄了、还是那帧的盘本来就不在那个位置。
把两帧同一坐标区域的 HSV 并排放出来，差在哪一个通道上一眼可见。
"""
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(REPO, "localtest", "shots_report")

# 三个盘的实测中心（占屏宽/屏高），来自 dingque_01 通过的那批轮廓
CENTERS = [("万盘", 0.406, 0.642), ("条盘", 0.500, 0.640), ("筒盘", 0.616, 0.653)]
R = 55          # 取样半径（全屏像素）

names = sys.argv[1:] or ["shushan_dingque_01.jpg", "shushan_dingque_02.jpg"]
imgs = {}
for n in names:
    img = cv2.imread(os.path.join(SHOT, n))
    imgs[n] = img
    ih, iw = img.shape[:2]
    print(f"\n=== {n}  {iw}x{ih}")
    for tag, cx, cy in CENTERS:
        x, y = int(cx * iw), int(cy * ih)
        x0, y0 = max(0, x - R), max(0, y - R)
        patch = img[y0:min(ih, y + R), x0:min(iw, x + R)]
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[:, :, 0].ravel(), hsv[:, :, 1].ravel(), hsv[:, :, 2].ravel()
        # 各 hue 窗口内像素占比（与 _phase_hue_mask 完全同一套阈值）
        red = (((h <= 10) | (h >= 170)) & (s >= 100) & (v >= 100)).mean()
        grn = ((h >= 35) & (h <= 85) & (s >= 80) & (v >= 80)).mean()
        gld = ((h >= 8) & (h <= 32) & (s >= 75) & (v >= 90)).mean()
        blu = ((h >= 95) & (h <= 130) & (s >= 100) & (v >= 100)).mean()
        print(f"  {tag} 中心=({x},{y}) H中位={np.median(h):.0f} S中位={np.median(s):.0f} "
              f"V中位={np.median(v):.0f}  占比 red={red:.3f} green={grn:.3f} "
              f"gold={gld:.3f} blue={blu:.3f}")
    # 拼一张对比图：色盘带 y 0.55~0.75
    ih, iw = img.shape[:2]
    band = img[int(ih * 0.52):int(ih * 0.78), int(iw * 0.30):int(iw * 0.72)]
    imgs[n + "_band"] = cv2.resize(band, None, fx=0.9, fy=0.9)

stack = np.vstack([imgs[n + "_band"] for n in names])
out = os.path.join(REPO, "build", "disc_band_cmp.png")
cv2.imwrite(out, stack)
print("\n写出", out, stack.shape)
