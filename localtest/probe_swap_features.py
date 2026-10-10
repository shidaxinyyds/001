# -*- coding: utf-8 -*-
"""换三张判据重做前的量化：8 张帧（5 漏判 + 3 误报 + 2 已命中）在同一段代码里的原始量。

现有判据是「低带里一个金色扁圆盘」，为腾讯的金色「换牌」按钮调的。全素材矩阵显示它
漏 5 张、误报 3 张。修之前先回答一个前置问题：**这些帧里到底有没有一个共用的可分
信号？** 如果没有，任何新判据都只是把误报挪到别处，那就不该写。

量三组与位置无关的候选特征（全部在整屏比例坐标下取，避免又钉死某个平台的像素位）：

  gold / cyan  低带里的金色、青色占比（现有判据的两个原始量）
  disc_ar      低带最大金色连通域宽高比
  fill         该连通域的外接框填充率
  h_band       该连通域高 / 带高
  center_ui    屏幕中央横向条带里「亮色非桌布」像素占比（换三张与选牌弹窗都会盖住中部）
  title_strip  中央偏上条带的亮色占比（换三张标题「选择三张…」通常在这里）

用法: py -3.10 -X utf8 localtest/probe_swap_features.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# (相对路径, 平台, 真值标签)
CASES = [
    ("shots_batch3/jj_swap_01.jpg", "jj", "swap-hit"),
    ("shots_multi/jj_swap_01.jpg", "jj", "swap-hit"),
    ("shots_multi/tencent_swap_01.jpg", "tencent", "swap-miss"),
    ("shots_multi/tuyou_swap_01.jpg", "tuyou", "swap-miss"),
    ("shots_report/shushan_swap_01.jpg", "shushan", "swap-miss"),
    ("shots_report/shushan_swap_02.jpg", "shushan", "swap-miss"),
    ("shots_batch3/zj_swap_03.jpg", "zj_sichuan", "swap-miss"),
    ("shots_multi/tencent_pick_01.jpg", "tencent", "pick-FP"),
    ("shots_multi/tuyou_pick_01.jpg", "tuyou", "pick-FP"),
    ("shots_tuyou_select/s1_select_cd17.jpg", "tuyou", "pick-FP"),
    ("shots_multi/queshen_play_01.jpg", "gd_queshen", "play"),
    ("shots_batch3/jj_play_03.jpg", "jj", "play"),
]

# 低带：手牌行正上方（沿用现有判据的实测窗口，只作对照用）
LOW = (0.74, 0.88, 0.64, 0.78)
# 中央横向条带（弹窗遮罩通常盖住中部），与偏上的标题条
MID = (0.30, 0.62, 0.22, 0.78)
TITLE = (0.10, 0.30, 0.25, 0.75)


def _cut(img, band):
    """按 (y0, y1, x0, x1) 取条带——与现有判据的窗口写法一致。

    上一版把元组当成 (x0, y0, x1, x1) 用，导致 x1 > x2、整块切为空，所有颜色占比
    统一报 0——那种“全都等于 0”的表本身就该引起警觉：不是信号不存在，是探针坏了。
    """
    y0, y1, x0, x1 = band
    return img[int(img.shape[0] * y0):int(img.shape[0] * y1),
               int(img.shape[1] * x0):int(img.shape[1] * x1)]


def color_ratio(img, band, lo, hi):
    crop = _cut(img, band)
    if crop.size == 0:
        # 必须交回单通道空掩膜：直接返 crop 会让下游 findContours 拿到 CV_8UC3 而报错。
        return 0.0, np.zeros((1, 1), dtype=np.uint8)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    m = (h >= lo) & (h <= hi) & (s > 90) & (v > 110)
    return float(m.sum()) / float(m.size), m.astype('uint8')


def biggest(mask):
    if mask is None or mask.ndim != 2 or mask.size == 0:
        return 0.0, 0.0, 0.0
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return 0.0, 0.0, 0.0
    best = max(cnts, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(best)
    if w <= 0 or h <= 0:
        return 0.0, 0.0, 0.0
    return (w / float(h)), (cv2.contourArea(best) / float(w * h)), (h / float(mask.shape[0]))


def bright_share(img, band):
    """条带里「比桌布亮且不太饱和」的像素占比：弹窗面板通常比牌桌亮。"""
    crop = _cut(img, band)
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = (hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 140)
    return float(m.sum()) / float(m.size)


print(f"{'帧':38s} {'真值':9s} {'gold':6s} {'cyan':6s} {'ar':5s} {'fill':5s} "
      f"{'h/band':6s} {'mid':6s} {'title':6s}")
for rel, platform, truth in CASES:
    p = os.path.join(HERE, rel)
    img = cv2.imread(p)
    if img is None:
        print(f"{rel:38s} {truth:9s} 缺素材")
        continue
    g, gm = color_ratio(img, LOW, 15, 35)
    c, _ = color_ratio(img, LOW, 85, 105)
    ar, fill, hb = biggest(gm)
    print(f"{rel:38s} {truth:9s} {g:6.4f} {c:6.4f} {ar:5.2f} {fill:5.3f} "
          f"{hb:6.3f} {bright_share(img, MID):6.4f} {bright_share(img, TITLE):6.4f}")
