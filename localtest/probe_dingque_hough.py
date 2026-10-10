# -*- coding: utf-8 -*-
"""定缺页判据第三试：用 Hough 圆检测（不是连通域）量 9 张真机夹具。

前两次为什么必然失败（结论已写在 `probe_dingque_discs.py` 头部）：
  一、按颜色定窗 —— 各平台配色不同（JJ 的「筒」是金色），且蜀山绿底会把桌布误判成绿盘；
  二、按连通域找「亮且饱和的近圆块」 —— 圆盘内部有白色大字与金色描边，**连通域一定会
      被内部内容割裂**，所以 5/6 张定缺页框到 0 个盘。

这次换工具：Hough 圆检测按**边缘的圆形梯度**找圆，与盘内画了什么、是什么颜色都无关。
这正好匹配定缺页的物理不变量：屏幕中下部横排三颗**等直径、同基线**的大圆盘。

先量再定阈值：把 9 张夹具检出的圆按直径排序打出来，看「定缺页 vs 非牌局页」是否
真的可分。分不开就如实说不分不开，不硬凑。

用法: py -3.10 -X utf8 localtest/probe_dingque_hough.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "shots_phase_fix")

# 取区：屏幕中下部（三颗圆盘所在带）
BAND = (0.48, 0.86, 0.18, 0.84)       # y0,y1,x0,x1


def circles_in(img):
    y0, y1, x0, x1 = BAND
    crop = img[int(img.shape[0] * y0):int(img.shape[0] * y1),
               int(img.shape[1] * x0):int(img.shape[1] * x1)]
    if crop.size == 0:
        return crop, []
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    # 圆盘直径经验上约为带高的 18%~34%：把 minR/maxR 定在这个区间，
    # 既排除小图标也排除整张牌桌
    min_r = max(8, int(h * 0.09))
    max_r = max(min_r + 6, int(h * 0.20))
    cs = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.5,
                          minDist=int(min_r * 1.2),
                          param1=110, param2=48,
                          minRadius=min_r, maxRadius=max_r)
    out = []
    if cs is not None:
        for x, y, r in cs[0]:
            out.append((float(r), float(x) / w, float(y) / h))
    out.sort(key=lambda c: -c[0])
    return crop, out


def row_of_three(cs):
    """取最大的 3 个圆：直径同一量级、中心 y 同基线、x 明显散开 → 判定缺三盘。"""
    if len(cs) < 3:
        return False, None
    top = cs[:3]
    rmax = max(r for r, _x, _y in top)
    rmin = min(r for r, _x, _y in top)
    same_size = rmin >= 0.62 * rmax
    ys = [y for _r, _x, y in top]
    xs = [x for _r, x, _y in top]
    same_line = (max(ys) - min(ys)) < 0.10
    spread = (max(xs) - min(xs)) > 0.25
    return bool(same_size and same_line and spread), top


print(f"{'夹具':24s} {'检出圆':>6s}  三盘齐  [真值]  判定   最大三圆(半径/中心x/中心y)")
wrong = 0
for name in sorted(os.listdir(FIX)):
    if not name.endswith(".jpg"):
        continue
    img = cv2.imread(os.path.join(FIX, name))
    if img is None:
        continue
    _crop, cs = circles_in(img)
    ok, top = row_of_three(cs)
    truth = "定缺" if name.startswith("dq_") else "非牌局"
    guess = "定缺" if ok else "非定缺"
    # 上一版拿 truth="定缺页" 与 guess="定缺" 直接比字符串，永远不等 → 9 张全标 ✗。
    # 判据本身其实 9/9 全对（看「三盘齐」列）：又一个“测量报告错、让人误判结论错”的样本。
    mark = "✓" if (ok == (truth == "定缺")) else "✗"
    if mark == "✗":
        wrong += 1
    detail = "  ".join(f"r{r:.0f} x{x:.2f} y{y:.2f}" for r, x, y in (top or cs)[:3])
    print(f"{name:24s} {len(cs):6d}  {str(ok):6s}  [{truth}] {guess} {mark}  {detail}")

print(f"\n错判 {wrong} / 9")
