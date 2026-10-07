# -*- coding: utf-8 -*-
"""为 YOLODetector.detect_strip 的空白裁片护栏定标：量各帧手牌带的亮像素占比。

护栏阈值必须严格低于「真实有牌帧」的最低占比、又高于「纯黑裁片」的占比，
否则要么误杀真牌、要么拦不住幻觉。本脚本按引擎同样的 hand_roi 裁切后量。

用法: py -3.10 localtest\blank_guard_calib.py
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from platforms import get_hand_roi  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")

PLATFORM_OF = {i: "weile" for i in range(1, 13)}
PLATFORM_OF[13] = "shushan"
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"


def bright_frac(strip, v=120):
    g = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)[::4, ::4]
    return float(np.count_nonzero(g > v)) / g.size if g.size else 0.0


def main():
    rows = []
    for f in sorted(x for x in os.listdir(SHOTS) if x.lower().endswith(".jpg")):
        idx = int(f.split("_")[1][:2])
        platform = PLATFORM_OF.get(idx, "generic")
        img = cv2.imread(os.path.join(SHOTS, f))
        if img is None:
            continue
        h = img.shape[0]
        roi = get_hand_roi(platform)
        y0, y1 = int(h * roi[0]), int(h * roi[1])
        strip = img[y0:y1, :] if y1 - y0 >= 80 else img
        rows.append((idx, platform, bright_frac(strip)))

    print(f"{'idx':>3} {'platform':11} {'bright_frac':>11}")
    for idx, p, fr in rows:
        print(f"{idx:3d} {p:11} {fr*100:10.2f}%")

    vals = sorted(fr for _i, _p, fr in rows)
    print(f"\n最低 5 个: {['%.2f%%' % (v*100) for v in vals[:5]]}")
    print(f"有牌帧最低占比 = {vals[0]*100:.2f}%")
    # 纯黑裁片基线
    black = np.zeros((261, 899, 3), dtype=np.uint8)
    print(f"纯黑裁片占比 = {bright_frac(black)*100:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
