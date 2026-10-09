# -*- coding: utf-8 -*-
"""量 B：弹窗压暗那一帧，手牌带的像素到底过不过「牌面」掩膜的三条线。

`detect_hand_strip` 判"这是牌"的掩膜是
    ~felt & V>75 & B>110 & G>110 & R>110
（tencent_grid_detector.py:1663-1664）。全屏弹窗会把整张图乘一个 <1 的系数，
于是牌面的象牙白可能整体掉到 110 以下 —— 那就是"几何层 0 框"的直接原因。

但**不能**因为这一帧掉到 100 就把阈值改成 100：那样等于把"非牌局背景"也放进来，
用户会看到桌面木纹被当成牌。所以这里同时量两侧：
  · 该帧手牌带的实际 BGR/V 分布（决定要放宽到哪）；
  · 全部 67 帧里"非牌局"区域（桌面中央空地）在同一判据下的通过率（决定放宽的代价）。
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import test_report_frames_guard as G  # noqa: E402


def stats(img, y0, y1, x0, x2, label):
    ih, iw = img.shape[:2]
    roi = img[int(ih * y0):int(ih * y1), int(iw * x0):int(iw * x2)]
    if roi.size == 0:
        return
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    b, g, r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    v = hsv[:, :, 2]
    felt = (hsv[:, :, 0] >= 50) & (hsv[:, :, 0] <= 110) & (hsv[:, :, 1] >= 50)
    cur = ~felt & (v > 75) & (b > 110) & (g > 110) & (r > 110)
    # 逐条拆开看是哪一条卡住的
    only_v = ~felt & (v > 75)
    all_hi = ~felt & (v > 75) & (b > 90) & (g > 90) & (r > 90)
    print(f"    {label:12s} V中位={np.median(v):5.0f} B中位={np.median(b):5.0f} "
          f"R中位={np.median(r):5.0f} | 现判据通过={cur.mean():.3f} "
          f"只卡 V>75={only_v.mean():.3f} 三色降到 90={all_hi.mean():.3f}")


print("=== 报障帧：手牌带（y 0.80~0.98）")
for name, pf, md, hand, phase in G.TRUTH:
    img = cv2.imread(os.path.join(G.SHOT_DIR, name))
    if img is None:
        continue
    print(f"  {name} [{phase}]")
    stats(img, 0.80, 0.98, 0.10, 0.90, "手牌带")

print("\n=== 对照：桌面中央空地（不是牌，判据必须一直把它挡在外面）")
for name in ("zj_popup_01.jpg", "zj_play_02.jpg", "shushan_swap_02.jpg"):
    img = cv2.imread(os.path.join(G.SHOT_DIR, name))
    if img is None:
        continue
    print(f"  {name}")
    stats(img, 0.40, 0.55, 0.30, 0.70, "桌面空地")
