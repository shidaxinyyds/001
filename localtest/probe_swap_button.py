# -*- coding: utf-8 -*-
"""量一件事：一块「白底近圆的确认按钮」能不能当跨平台的换三张判据。

背景：现有 `is_swap_phase` 只认腾讯那块低带金色扁圆盘（41 帧实测标的），蜀山/指尖的
换牌按钮是**右侧中部的白色圆盘 + 黑字**，完全不在那条取区里 → 用户两帧换三张全漏判
（台账 `test_report_frames_guard.KNOWN_PHASE_DEFECTS`）。

为什么先量再写：任何"为了过这两帧"而放宽的判据，都必须先回答「另外 60 多帧会不会被
它误报成换三张」。所以这里把每一帧的白色近圆候选全打出来，标上该帧真实阶段，
先看分布，再决定判据长什么样（而不是先写判据再祈祷）。

用法: py -3.10 -X utf8 localtest/probe_swap_button.py
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

# 帧 -> 真实阶段（与 test_report_frames_guard 的 TRUTH 同源，别在这里另编一套）
import test_report_frames_guard as G  # noqa: E402

BASE_GT = os.path.join(HERE, "gt", "shots.json")


def white_discs(img):
    """返回画面里「低饱和、高亮、近圆、尺寸落在按钮量级」的候选。"""
    hsv, sw, sh, y_off, scale = _phase_view(img)
    if hsv is None:
        return []
    ih, iw = img.shape[:2]
    inv = 1.0 / scale
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = ((s <= 70) & (v >= 190)).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        area = cv2.contourArea(c)
        x, y, bw, bh = cv2.boundingRect(c)
        if bw <= 0 or bh <= 0 or area < 120:
            continue
        wn = bw * inv / iw
        ar = bw / float(bh)
        fill = cv2.contourArea(cv2.convexHull(c)) / float(bw * bh)
        if not (0.03 <= wn <= 0.12) or not (0.6 <= ar <= 1.6) or fill < 0.62:
            continue
        out.append(dict(x=round((x + bw / 2.0) * inv / iw, 3),
                        y=round(y_off + (y + bh / 2.0) * inv / ih, 3),
                        w=round(wn, 3), area=int(area * inv * inv), fill=round(fill, 2)))
    return sorted(out, key=lambda d: -d["area"])[:4]


def main():
    print("=== 报障 10 帧（含真实阶段）")
    for name, pf, md, hand, phase in G.TRUTH:
        img = cv2.imread(os.path.join(G.SHOT_DIR, name))
        if img is None:
            continue
        print(f"  {name:26s} {phase:8s} {white_discs(img)}")

    print("\n=== 上一批真机 20 帧（阶段取自 eval_phase_layout.MULTI_TRUTH）")
    try:
        import eval_phase_layout as EP
    except Exception as e:
        print("  拿不到 eval_phase_layout：", e)
        return 0
    multi = getattr(EP, "MULTI_TRUTH", {})
    for name in sorted(os.listdir(os.path.join(HERE, "shots_multi"))):
        if not name.endswith(".jpg"):
            continue
        img = cv2.imread(os.path.join(HERE, "shots_multi", name))
        if img is None:
            continue
        print(f"  {name:26s} {multi.get(name, '?'):8s} {white_discs(img)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
