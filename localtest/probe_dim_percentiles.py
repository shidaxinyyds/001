# -*- coding: utf-8 -*-
"""重测「手牌带被压暗」这条判据该量什么：整条带的 V 分位数，而不是牌面均值。

上一版直接取整条 hand_roi 的 V 中位数，结果 6 帧正常帧被误判成「压暗」——因为那条带
里大半是**桌布**（绿色、暗），中位数由桌布决定，跟牌亮不亮没关系（守卫的反向检查
`test_dim_frame_says_why_it_cannot_read` 当场把它抓出来）。

牌是带里最亮的东西，所以该看的是高分位数（p75/p90）。这里把 10 帧的分位数并排放出来，
阈值取在两组之间最宽的空档上；顺手把「桌布占比」也打出来，用它解释为什么中位数不可用。
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
import engine.engine as E  # noqa: E402
from platforms import get_hand_roi  # noqa: E402

for name, pf, md, hand, phase in G.TRUTH:
    img = cv2.imread(os.path.join(G.SHOT_DIR, name))
    if img is None:
        continue
    ih = img.shape[0]
    roi = get_hand_roi(pf)
    y0, y1 = int(ih * roi[0]), int(ih * min(1.0, roi[1]))
    band = img[y0:y1]
    v = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)[:, :, 2].ravel()
    felt = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    felt = ((felt[:, :, 0] >= 50) & (felt[:, :, 0] <= 110) & (felt[:, :, 1] >= 50)).mean()
    print(f"{name:26s} roi=({roi[0]:.2f},{roi[1]:.2f}) 桌布占比={felt:.2f} "
          f"V p50={np.percentile(v, 50):5.0f} p75={np.percentile(v, 75):5.0f} "
          f"p90={np.percentile(v, 90):5.0f} p95={np.percentile(v, 95):5.0f}")
