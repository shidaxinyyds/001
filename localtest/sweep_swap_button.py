# -*- coding: utf-8 -*-
"""跨平台换牌按钮的误报扫描：把「桌面带里的白色近圆盘」这一判据放到全部夹具上过一遍。

上一版探针（`probe_swap_button.py`）先量出一个坏消息：白底近圆的候选**首先**是手牌行的
牌面（每帧 y≈0.858 都有一排），拿它当换牌判据等于每帧都判成换三张。真正那个蜀山换牌
按钮在 y≈0.638、fill 只有 0.75（圆盘里有黑字）。所以判据必须限定在「桌面中带」里。

但"在这 30 帧里不误伤"不等于"在手机上不误伤" —— 所以这里把 67 帧（10 报障 + 20 真机 +
37 腾讯 GT）全跑一遍，逐帧打印桌面带内的候选 + 该帧真实阶段。任何一帧 play 出现候选，
都要写进注释里当已知风险，而不是假装看不见。

用法: py -3.10 -X utf8 localtest/sweep_swap_button.py
"""
from __future__ import annotations

import json
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

from recognition.tencent_grid_detector import _phase_view  # noqa: E402

# 桌面中带：定缺盘行实测 0.62~0.66、蜀山换牌按钮实测 0.638；手牌行在 0.83~0.87，
# 上界 0.78 就是为了不把牌面卷进来（那是上一版探针踩到的坑）。
BAND_Y = (0.50, 0.78)
W_NORM = (0.045, 0.10)
FILL = (0.60, 0.88)          # 圆盘里有黑字 → 凸包占比不可能接近 1
MIN_AREA_RATIO = 1.2e-3      # 连通域面积 / 全屏面积


def candidates(img):
    hsv, _sw, _sh, y_off, scale = _phase_view(img)
    if hsv is None:
        return []
    ih, iw = img.shape[:2]
    inv = 1.0 / scale
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    mask = ((s <= 70) & (v >= 190)).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        area = cv2.contourArea(c)
        x, y, bw, bh = cv2.boundingRect(c)
        if bw <= 0 or bh <= 0:
            continue
        cx = (x + bw / 2.0) * inv / iw
        cy = y_off + (y + bh / 2.0) * inv / ih
        if not (BAND_Y[0] <= cy <= BAND_Y[1]):
            continue
        wn = bw * inv / iw
        ar = bw / float(bh)
        fill = cv2.contourArea(cv2.convexHull(c)) / float(bw * bh)
        ar_full = area * inv * inv / float(iw * ih)
        if not (W_NORM[0] <= wn <= W_NORM[1]) or not (0.6 <= ar <= 1.6):
            continue
        if not (FILL[0] <= fill <= FILL[1]) or ar_full < MIN_AREA_RATIO:
            continue
        out.append(dict(x=round(cx, 3), y=round(cy, 3), w=round(wn, 3),
                        fill=round(fill, 2), area_ratio=round(ar_full, 5)))
    return out


def frames():
    import test_report_frames_guard as G
    for name, pf, md, hand, phase in G.TRUTH:
        yield "report", name, phase, os.path.join(G.SHOT_DIR, name)
    import eval_phase_layout as EP
    multi = getattr(EP, "MULTI_TRUTH", {})
    for f in sorted(os.listdir(os.path.join(HERE, "shots_multi"))):
        if f.endswith(".jpg"):
            yield "multi", f, multi.get(f, "?"), os.path.join(HERE, "shots_multi", f)
    gt = json.load(open(os.path.join(HERE, "gt", "shots.json"), encoding="utf-8"))
    for e in gt["shots"]:
        p = e.get("path") or ""
        for cand in (p, os.path.join(HERE, "shots", os.path.basename(p))):
            if os.path.exists(cand):
                yield "base", os.path.basename(cand), e.get("phase", "?"), cand
                break


def main():
    hits = {"swap": 0, "other": 0}
    for group, name, phase, path in frames():
        img = cv2.imread(path)
        if img is None:
            continue
        cs = candidates(img)
        if not cs:
            continue
        tag = "swap帧" if phase == "swap" else f"{phase}帧"
        hits["swap" if phase == "swap" else "other"] += 1
        print(f"[{group:6s}] {name:26s} {tag:8s} 候选={cs}")
    print(f"\n桌面带内有白色近圆候选的帧：换三张 {hits['swap']} 帧、其他阶段 {hits['other']} 帧")
    return 0


if __name__ == "__main__":
    sys.exit(main())
