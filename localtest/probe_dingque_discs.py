# -*- coding: utf-8 -*-
"""定缺页判据的测量：三花色圆盘（万/条/筒）在 9 张真机夹具上的原始分布。

为什么先量再写：本会话已经五次「没量就改 → 被守卫抓住 → 回退」。这次先把
「定缺页 vs 非牌局页」在三个颜色窗下的最大圆形连通域面积/宽高比/纵向位置打出来，
阈值由数据定，不靠手感。

夹具（用户提供，已入库 `localtest/shots_phase_fix/`）：
  dq_*            6 张真定缺页，跨 指尖/腾讯/蜀山×2/途游/JJ
  nongame_lobby_*  1 张开局等待页（有方位盘、无花色盘）
  nongame_settle_* 2 张结算页（有方位盘/摊牌，无花色盘）

用法: py -3.10 -X utf8 localtest/probe_dingque_discs.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ 两次尝试均不成立，结论写在下面，不要重踩：
#   尝试一（按颜色）：固定 万=红橙 / 条=绿 / 筒=蓝 三个色窗 —— 6 张定缺页里 4 张
#     什么都没测到。原因：各平台配色不同（JJ 的「筒」是金色），而蜀山绿底牌面会把
#     桌布误判成绿盘。
#   尝试二（与颜色无关的形状）：只要求「亮且饱和 + 近圆」的连通域 —— 5/6 张定缺页
#     框到 0 个圆盘。原因：圆盘内部有白色大字与金色描边，单一亮度/饱和度窗被字面
#     割裂；而不同夹具分辨率下同一比例带对应的实际像素位置也不同。
# 下一步的正确做法：先把三张不同平台的定缺页按**实际像素**把圆盘区域量出来（而不是
# 先验地定一个颜色/亮度窗），再决定用什么不变量。本文件保留作为测量台。
FIX = os.path.join(HERE, "shots_phase_fix")

# 取区：屏幕中下部（圆盘在这一带；方位盘更靠上且是深色，不会落进同一窗）
BAND = (0.50, 0.82, 0.20, 0.80)       # y0,y1,x0,x1


def round_blobs(crop):
    """与颜色无关：在取区里找所有「亮且饱和」的近圆连通域，返回 [(面积占比, 中心x, 中心y)]。

    为什么不能按颜色：上一版固定了 万=红橙/条=绿/筒=蓝 三个色窗，6 张定缺页里 4 张
    什幺都没测到 —— 因为各平台配色不同（JJ 的「筒」是金色），而蜀山绿底牌面还会把
    桌布本身误判成绿盘。**定缺页的不变量是三颗同大、同高、横排的圆盘，不是它们的颜色。**
    """
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 1] > 70) & (hsv[:, :, 2] > 120)).astype('uint8') * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    band_area = float(crop.shape[0] * crop.shape[1])
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w <= 0 or h <= 0:
            continue
        ar = cv2.contourArea(c)
        if ar / band_area < 0.004:
            continue
        aspect = w / float(h)
        fill = ar / float(w * h)
        if 0.7 <= aspect <= 1.45 and fill > 0.72:
            out.append((ar / band_area, (x + w / 2.0) / crop.shape[1],
                        (y + h / 2.0) / crop.shape[0]))
    return out


def three_in_a_row(blobs):
    """三盘齐：≥3 个近圆盘，面积同一量级，中心 y 接近（同一横排）。"""
    if len(blobs) < 3:
        return False, None
    blobs = sorted(blobs, key=lambda b: -b[0])[:6]
    big = [b for b in blobs if b[0] >= 0.6 * blobs[0][0]]
    if len(big) < 3:
        return False, None
    big = sorted(big, key=lambda b: b[1])[:3]
    ys = [b[2] for b in big]
    xs = [b[1] for b in big]
    aligned = (max(ys) - min(ys)) < 0.12 and (max(xs) - min(xs)) > 0.12
    return aligned, big


print(f"{'夹具':24s} {'圆盘数':>5s}  三盘齐  明细（面积% / 中心x / 中心y）")
for name in sorted(os.listdir(FIX)):
    if not name.endswith(".jpg"):
        continue
    img = cv2.imread(os.path.join(FIX, name))
    if img is None:
        continue
    y0, y1, x0, x1 = BAND
    crop = img[int(img.shape[0] * y0):int(img.shape[0] * y1),
               int(img.shape[1] * x0):int(img.shape[1] * x1)]
    blobs = round_blobs(crop)
    ok, big = three_in_a_row(blobs)
    detail = "  ".join(f"{b[0]*100:5.2f}% x{b[1]:4.2f} y{b[2]:4.2f}"
                       for b in sorted(blobs, key=lambda v: v[1])[:5])
    truth = "定缺页" if name.startswith("dq_") else "非牌局"
    verdict = "✓" if (ok == (truth == "定缺页")) else "✗ 错判"
    print(f"{name:24s} {len(blobs):5d}  {str(bool(ok)):6s} [{truth}] {verdict}  {detail}")
