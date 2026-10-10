# -*- coding: utf-8 -*-
"""对比：现有跨平台几何通路 vs 新写的 Hough 通路，在 9 张真机夹具上的表现。

为什么要这一层：`TencentGridDetector.is_dingque_phase` 里**早就有一条**跨平台几何
通路（"一行三个同尺寸、异色、共线的圆盘"）。我先前测它用的是旧素材，而那些文件
名字带 dingque、实际却是局中/结算页 —— 于是得出"判据认不出"的错误结论。
新写判据之前必须先量现有实现，否则会留下一份重复且更弱的判据。

用法: py -3.10 -X utf8 localtest/compare_dingque_cues.py
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402
from recognition.phase_cues import has_dingque_discs  # noqa: E402
from platforms import set_platform_explicit  # noqa: E402

FIX = os.path.join(HERE, "shots_phase_fix")
set_platform_explicit("zj_sichuan")
g = TencentGridDetector()
y = YOLODetector()

print("夹具                     grid现有  yolo现有  新Hough   [真值]")
agree = disagree = 0
for name in sorted(os.listdir(FIX)):
    if not name.endswith(".jpg"):
        continue
    img = cv2.imread(os.path.join(FIX, name))
    if img is None:
        continue
    try:
        a = bool(g.is_dingque_phase(img))
    except Exception:
        a = None
    try:
        b = bool(y.is_dingque_phase(img))
    except Exception:
        b = None
    c = has_dingque_discs(img)
    truth = "定缺" if name.startswith("dq_") else "非牌局"
    want = (truth == "定缺")
    verdict = "✓" if (a == want and c == want) else "✗"
    if verdict == "✓":
        agree += 1
    else:
        disagree += 1
    print(f"{name:24s} {str(a):>7s} {str(b):>8s} {str(c):>8s}  [{truth}] {verdict}")

print(f"\n两条通路都对: {agree} / 错: {disagree}")
