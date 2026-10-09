# -*- coding: utf-8 -*-
"""逐道量牌河检测的门槛链：弃牌到底死在哪一关（掩膜？尺寸？中央排除？分类分不够？）。

`detect_river_discards` 是一条五道门的串联：象牙白掩膜 → 轮廓尺寸窗 → 中央骰子盒
排除 → 手牌顶保护线 → 分类置信度 >= 0.42。它整体返回 [] 时，只说"有一道门拦住了"，
不说哪道、差多少 —— 而每一道的修法都不一样：掩膜不对要改 HSV 窗，尺寸不对要改
面积窗（跟分辨率强相关），中央排除误伤要改比例，分类分不够是模板/风格问题。

所以这里把每道门的通过数量与"被拦下的候选的实际尺寸/分数"打出来。

用法: py -3.10 -X utf8 localtest/probe_river_gates.py [帧名 ...]
"""
from __future__ import annotations

import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
from engine.engine import RIVER_CONF, _classify_tile_fast, _RIVER_ROT_PREF  # noqa: E402
from modes import available_set  # noqa: E402
from platforms import get_river_zones  # noqa: E402

BATCH = os.path.join(HERE, "shots_batch3")
NAMES = sys.argv[1:] or ["zj_play_03.jpg", "zj_play_04.jpg"]

for name in NAMES:
    img = cv2.imread(os.path.join(BATCH, name))
    if img is None:
        print("!! 读不到", name)
        continue
    ih, iw = img.shape[:2]
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: "zj_sichuan"
    E.load_mode = lambda *a, **k: "sc_hz"
    try:
        eng = E.Engine()
        # 必须用 `_river_classifier()`：上一版这里拿 `get_detector()`（= YOLODetector，
        # 没有 classify_tile），于是每一张都是 0.0 分——那不是牌河的错，
        # 是探针跟着生产代码一起传错了对象。量门槛链必须量在生产同一口径上。
        det = eng._river_classifier()
        print(f"\n分类器 = {type(det).__name__ if det is not None else None}")
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
    avail = available_set("sc_hz")
    cache = {}
    print(f"\n=== {name} {iw}x{ih}  门槛链: 尺寸窗/中央排除/手牌顶/分类>={RIVER_CONF['min_conf']}")
    for z in get_river_zones("zj_sichuan"):
        zn, x1, y1, x2, y2 = z[0], int(iw * z[1]), int(ih * z[2]), int(iw * z[3]), int(ih * z[4])
        crop = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        white = ((hsv[:, :, 1] < 65) & (hsv[:, :, 2] > 115)
                 & (crop[:, :, 0] > 65) & (crop[:, :, 1] > 65) & (crop[:, :, 2] > 65))
        beacon = (hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 120)
        white[beacon] = False
        cnts, _ = cv2.findContours(
            cv2.morphologyEx(white.astype('uint8'), cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))),
            cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        sizes = []
        passed = 0
        killed_center = killed_hand = 0
        scores = []
        for c in cnts:
            bx, by, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if bw < 14 or bh < 14 or area < 220 or area > 6000:
                if area >= 220 and len(sizes) < 6:
                    sizes.append((bw, bh, area))
                continue
            gx, gy = x1 + bx, y1 + by
            cx, cy = gx + bw / 2, gy + bh / 2
            cxl, cxh = RIVER_CONF["center_x"]
            cyl, cyh = RIVER_CONF["center_y"]
            if (cxl * iw <= cx <= cxh * iw) and (cyl * ih <= cy <= cyh * ih):
                killed_center += 1
                continue
            if gy + bh >= int(ih * RIVER_CONF["hand_top"]):
                killed_hand += 1
                continue
            passed += 1
            pref = _RIVER_ROT_PREF.get(zn, (None, cv2.ROTATE_90_CLOCKWISE,
                                            cv2.ROTATE_90_COUNTERCLOCKWISE))
            lbl, sc = _classify_tile_fast(det, crop[by:by + bh, bx:bx + bw], avail, pref, cache)
            scores.append((round(float(sc), 3), lbl))
        scores.sort(reverse=True)
        print(f"  {zn:7s} 区=({x1},{y1})-({x2},{y2}) 白底像素={int(white.sum()):6d} "
              f"轮廓={len(cnts)} 过大候选={sizes[:4]} 过尺寸窗={passed} "
              f"中央排除={killed_center} 手牌顶排除={killed_hand}")
        print(f"          分类 top6={scores[:6]}  >=0.42 的有 {sum(1 for s, _ in scores if s >= 0.42)} 个")
