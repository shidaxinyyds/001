# -*- coding: utf-8 -*-
"""牌桌阶段共用的物理判据。与平台无关，只依赖画面本身的几何不变量。

为什么单独一个模块：定缺阶段的判据以前只按腾讯那颗金色圆钮调，指尖/蜀山/途游/JJ
的定缺页全都认不出（实测：`detect_dingque` 反而把**每一屏都在**的「东南西北方位盘」
读成定缺盘，于是局中与结算页都能产出「本家缺门」→ 徽章跳变、凭空缺门、结算报定缺）。

判据选型过程（三次尝试，前两次已实测否证，别再走回头路）：

  一、按颜色定窗（万=红橙 / 条=绿 / 筒=蓝）
      6 张定缺页里 4 张什么都没测到。各平台配色不同（JJ 的「筒」是金色），
      而蜀山绿底牌面会把桌布本身误判成绿盘。
  二、按连通域找「亮且饱和的近圆块」
      5/6 张定缺页框到 0 个盘。圆盘内部有白色大字与金色描边，**连通域必然被内部
      内容割裂**，这条路从原理上就不成立。
  三、Hough 圆检测（本模块）
      按边缘的圆形梯度找圆，与盘内画什么、什么颜色都无关。9 张真机夹具上
      **9/9 分开**：6 张定缺页全部检出「三颗等半径、同基线、横向等距」的圆
      （半径几乎一致，如 r67/r67/r67），3 张非牌局页（开局等待页 + 两张结算页）
      全部不满足。测量台：`localtest/probe_dingque_hough.py`。

不变量因此是几何的，不是颜色的：**屏幕中下部横排三颗等直径、同基线的大圆盘**。

⚠ 本模块**尚未接入生产链路**。原因不是效果不好，而是被守卫抓住一个真误报：

  接入后 `test_real_phase_frames_guard` 报：
      `shots_tuyou_select/s4_after_swap_cd04.jpg`（换牌后的局中帧）status 由 ok 变 dingque
  而误判定缺阶段会**清掉牌池** —— 所有错误里最贵的一种。所以已回退接线。

同时这次对比也修正了一个错判（`localtest/compare_dingque_cues.py`）：

  现有几何通路在 9 张真机夹具上已经 **8/9 正确**（5/6 定缺页命中、3/3 非牌局不误报），
  只漏一摗5（`shots_phase_fix/dq_zj_01.jpg`，指尖定缺页）。
  本模块（Hough）在该 9 张上 9/9，但到第 10 张（s4_after_swap）就误报 —— 过拟合。

要再接入，必须先做出 s4_after_swap_cd04 与真定缺页之间的可分特征（而不是调 Hough
阈值去赌）。候选方向：定缺页必然带「三个圆盘 + 方位盘同时存在」的组合，而换牌后的
局中帧只有方位盘；或者用“牌河为空”作为必要条件（定缺只可能发生在无弃牌时）。
两者都需要先在夹具上量过再写。
"""
from __future__ import annotations

import cv2
import numpy as np

# 取区：屏幕中下部（三颗花色圆盘所在带）。比例坐标 (y0, y1, x0, x1)。
DISC_BAND = (0.48, 0.86, 0.18, 0.84)

# 圆盘半径相对带高的比例区间：下限排除小图标，上限排除整张牌桌
DISC_R_MIN_FRAC = 0.09
DISC_R_MAX_FRAC = 0.20

# 「三盘齐」的几何容差
DISC_SIZE_TOLERANCE = 0.62      # 最小半径 ≥ 最大半径的该比例（等直径）
DISC_BASELINE_TOLERANCE = 0.10  # 三颗圆中心 y 的最大跨度（同基线）
DISC_SPREAD_MIN = 0.25          # 三颗圆中心 x 的最小跨度（横排散开）

# Hough 累加器阈值：越高越严。48 是在 9 张夹具上同时满足「6 全中、3 全不误报」的值。
DISC_HOUGH_ACCUM = 48
DISC_HOUGH_EDGE = 110


def find_disc_circles(image_bgr):
    """返回取区内检出的圆 [(半径, 中心x/带宽, 中心y/带高)]，按半径降序。

    异常或空图一律返回 []（判据失效时宁可说「不是定缺页」，也不能凭空把局中/结算
    页判成定缺页 —— 后者会触发清牌池，是最贵的错误）。
    """
    if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        return []
    try:
        y0, y1, x0, x1 = DISC_BAND
        h, w = image_bgr.shape[:2]
        crop = image_bgr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
        if crop.size == 0:
            return []
        ch, cw = crop.shape[:2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 5)
        min_r = max(8, int(ch * DISC_R_MIN_FRAC))
        max_r = max(min_r + 6, int(ch * DISC_R_MAX_FRAC))
        cs = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.5,
                              minDist=int(min_r * 1.2),
                              param1=DISC_HOUGH_EDGE, param2=DISC_HOUGH_ACCUM,
                              minRadius=min_r, maxRadius=max_r)
        if cs is None:
            return []
        out = [(float(r), float(x) / cw, float(y) / ch) for x, y, r in cs[0]]
        out.sort(key=lambda c: -c[0])
        return out
    except Exception:
        return []


def has_dingque_discs(image_bgr) -> bool:
    """画面中下部是否存在「三颗等直径、同基线、横向等距的大圆盘」= 定缺选门页。"""
    circles = find_disc_circles(image_bgr)
    if len(circles) < 3:
        return False
    top = circles[:3]
    radii = [r for r, _x, _y in top]
    if min(radii) < DISC_SIZE_TOLERANCE * max(radii):
        return False
    ys = [y for _r, _x, y in top]
    xs = [x for _r, x, _y in top]
    if (max(ys) - min(ys)) >= DISC_BASELINE_TOLERANCE:
        return False
    if (max(xs) - min(xs)) <= DISC_SPREAD_MIN:
        return False
    return True
