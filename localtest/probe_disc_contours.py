# -*- coding: utf-8 -*-
"""逐轮廓问：定缺色盘到底卡在哪一条判据上（尺寸窗 / 宽高比 / 凸包占比 / 面积地板）。

为什么不能只看 `_find_phase_discs` 的返回值：它把四道判据串在一起，返回 0 只说明
"有一道没过"，不说明是哪道、差多少。上一轮 蜀山 dingque_02 找到 0 个圆盘而 dingque_01
找到 3 个，两个数字之间没有任何可操作的信息 —— 这里把每张轮廓在每个判据上的实测值
和淘汰原因逐条打出来。
"""
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from recognition.tencent_grid_detector import (  # noqa: E402
    _PHASE_DISC_ASPECT, _PHASE_DISC_HULL_FILL, _PHASE_DISC_MIN_AREA,
    _PHASE_DISC_W, _PHASE_HUES, _phase_hue_mask, _phase_view)

SHOTS = os.path.join(REPO, "localtest", "shots_report")
for name in (sys.argv[1:] or ["shushan_dingque_01.jpg", "shushan_dingque_02.jpg"]):
    img = cv2.imread(os.path.join(SHOTS, name))
    if img is None:
        print("!! 读不到", name)
        continue
    hsv, sw, sh, y_off, scale = _phase_view(img)
    ih, iw = img.shape[:2]
    inv = 1.0 / scale
    px_lo, px_hi = iw * _PHASE_DISC_W[0] * scale, iw * _PHASE_DISC_W[1] * scale
    min_area = _PHASE_DISC_MIN_AREA * (iw * ih) / float(2000 * 899) * scale * scale
    print(f"\n=== {name}  子图={sw}x{sh} scale={scale:.3f} "
          f"尺寸窗=({px_lo:.1f},{px_hi:.1f})px 面积地板={min_area:.1f}px")
    for hue in _PHASE_HUES:
        mask = _phase_hue_mask(hsv, hue)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        big = sorted([c for c in cnts if cv2.contourArea(c) > min_area * 0.15],
                     key=cv2.contourArea, reverse=True)[:6]
        print(f"  [{hue}] 非零像素={int(np.count_nonzero(mask))} "
              f"过 15% 地板的轮廓数={len(big)}")
        for c in big:
            area = cv2.contourArea(c)
            x, y, bw, bh = cv2.boundingRect(c)
            ar = bw / float(bh) if bh else 0.0
            fill = cv2.contourArea(cv2.convexHull(c)) / float(bw * bh) if bw and bh else 0.0
            why = []
            if area < min_area:
                why.append(f"面积 {area:.0f}<{min_area:.0f}")
            if not (px_lo <= bw <= px_hi):
                why.append(f"宽 {bw} 不在 ({px_lo:.0f},{px_hi:.0f})")
            if not (px_lo <= bh <= px_hi):
                why.append(f"高 {bh} 不在 ({px_lo:.0f},{px_hi:.0f})")
            if not (_PHASE_DISC_ASPECT[0] <= ar <= _PHASE_DISC_ASPECT[1]):
                why.append(f"宽高比 {ar:.2f} 不在 {_PHASE_DISC_ASPECT}")
            if fill < _PHASE_DISC_HULL_FILL:
                why.append(f"凸包占比 {fill:.2f}<{_PHASE_DISC_HULL_FILL}")
            cy = y_off + (y + bh / 2.0) * inv / ih
            cx = (x + bw / 2.0) * inv / iw
            print(f"    ({x},{y},{bw}x{bh}) 中心=({cx:.3f},{cy:.3f}) area={area:.0f} "
                  f"ar={ar:.2f} fill={fill:.2f}  {'✓通过' if not why else '✗ ' + '; '.join(why)}")
