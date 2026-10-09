# -*- coding: utf-8 -*-
"""量牌河为什么一条都没读到：把每家的取区、区内检出的框、以及"框落在取区外多远"打出来。

`discards=""` 有两种完全不同的成因，修法也完全不同：
  ① 取区（river_zones）画错了地方 —— 那是配置错，改坐标；
  ② 取区对、但区内一个框都没检出（或检出后被丢弃）—— 那是识别/阈值问题。
所以这里既打"取区矩形内检到几张"，也打"全画面检到几张、其中有多少落在所有取区之外"。
后者不为零就说明取区偏了，前者不为零就说明是识别丢了。

用法: py -3.10 -X utf8 localtest/probe_river_zones.py [帧名 ...]
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
from platforms import get_river_zones  # noqa: E402

BATCH = os.path.join(HERE, "shots_batch3")
NAMES = sys.argv[1:] or ["zj_play_03.jpg", "zj_play_04.jpg", "shushan_dingque_03.jpg"]


def in_zone(rect, zone, iw, ih):
    """zone = (name, x0, y0, x1, y1) 相对坐标（引擎 1599 行就按这个顺序展开）。

    这里不能再猜：platforms.py 里 `hand_roi` 的注释是 (top,bottom,left,right)，
    而 `river_zones` 是 (x0,y0,x1,y1)——两个字段同文件、同形状、不同语义，
    拿错就是「取区看起来对、实际量看错了地方」。
    """
    x, y, w, h = rect[:4]
    cx, cy = (x + w / 2.0) / iw, (y + h / 2.0) / ih
    return zone[1] <= cx <= zone[3] and zone[2] <= cy <= zone[4]


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
        det = eng.get_detector()
        zones = get_river_zones("zj_sichuan")
        rows = det.detect_all_rows(img, classify=True, allow_rotation=False)
        boxes = [t for r in rows for t in r]
        print(f"\n=== {name}  {iw}x{ih}  全画面检出框={len(boxes)}")
        for z in zones:
            inside = [b for b in boxes if in_zone(b[0], z, iw, ih)]
            print(f"   {z[0]:8s} 取区(x0,y0,x1,y1)="
                  f"({z[1]:.2f},{z[2]:.2f},{z[3]:.2f},{z[4]:.2f}) 区内框={len(inside)} "
                  f"有label={sum(1 for b in inside if b[1])}")
        outside = [b for b in boxes if not any(in_zone(b[0], z, iw, ih) for z in zones)]
        print(f"   落在所有取区之外的框={len(outside)}")
        for b in outside[:12]:
            x, y, w, h = b[0][:4]
            print(f"      ({x},{y},{w}x{h}) 相对=({x / iw:.2f},{y / ih:.2f}) label={b[1]}")
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
