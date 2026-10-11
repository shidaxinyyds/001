# -*- coding: utf-8 -*-
"""B12 的正确验证：用**引擎自己的牌框**测「遮掉缺角标后五筒能否读对」。

上一版（`probe_badge_occlusion.py`）为什么没有信息量：它在探针里用简化轮廓自己切牌，
相邻牌粘连成一整块，只切到 4 个框 —— **测量工具比被测对象更弱，于是测不到东西**。
这次直接取引擎 payload 的 `tiles`（引擎自己切好、自己分类过的那一排框），逐张对照。

台账钉着的真值（`test_stream_guard.KNOWN_STREAM_DEFECTS`）：
    wrong_read:zj_play_04.jpg  = ["5p", "8p"]   # 该读 5p 的位置读成了 8p
    phantom_tile:zj_play_04.jpg = ["8p"]        # 屏上根本没有 8p
用户原话：带「缺」角标的五筒被读成八筒。

假设：角标盖住牌面左上角，遮掉筒子的一个点，五筒（梅花五点）被遮后与八筒的点位
分布更接近 ⇒ 把角标区域遮掉再分类应能读回 5p。

用法: py -3.10 -X utf8 localtest/probe_badge_occlusion2.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
from modes import available_set  # noqa: E402

FRAME = os.path.join(HERE, "shots_batch3", "zj_play_04.jpg")
img = cv2.imread(FRAME)
if img is None:
    sys.exit(f"读不到夹具 {FRAME}")

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    det = eng.get_hand_detector()
    with contextlib.redirect_stdout(io.StringIO()):
        d = json.loads(eng.process(img).result)
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm

tiles = d.get("tiles") or []
print(f"引擎自己的牌框：{len(tiles)} 张   读数：{d.get('hand')}")
avail = sorted(available_set("sc_hz"))

if not hasattr(det, "classify_tile"):
    print("该检测器没有 classify_tile，无法做单张对照实验 —— 如实报告，不硬做。")
    sys.exit(0)


def mask_badge(crop, h_frac, w_frac):
    """把左上角疑似角标区域填成牌面底色（用右半部分的中位色，不引入新色）。"""
    m = crop.copy()
    mh = max(4, int(m.shape[0] * h_frac))
    mw = max(4, int(m.shape[1] * w_frac))
    ref = crop[:, m.shape[1] // 2:].reshape(-1, 3)
    m[:mh, :mw] = np.median(ref, axis=0).astype(m.dtype)
    return m


print(f"\n{'#':>2s} {'引擎读数':>8s} {'原样重读':>10s} " +
      " ".join(f"遮{int(h*100)}x{int(w*100)}" for h, w in
               ((0.22, 0.40), (0.30, 0.50), (0.38, 0.60))))
flips = 0
for i, t in enumerate(tiles):
    x, y, w, h = int(t[0]), int(t[1]), int(t[2]), int(t[3])
    lbl = t[4] if len(t) > 4 else ""
    crop = img[y:y + h, x:x + w]
    if crop.size == 0 or crop.shape[0] < 12 or crop.shape[1] < 12:
        continue
    base, _bs = det.classify_tile(crop, avail=avail)
    cells = []
    for hh, ww in ((0.22, 0.40), (0.30, 0.50), (0.38, 0.60)):
        ml, _ms = det.classify_tile(mask_badge(crop, hh, ww), avail=avail)
        cells.append(ml or "-")
        if lbl == "8p" and ml == "5p":
            flips += 1
    star = "  ← 引擎读成 8p（台账说该读 5p）" if lbl == "8p" else ""
    print(f"{i+1:2d} {lbl or '-':>8s} {base or '-':>10s} " +
          " ".join(f"{c:>9s}" for c in cells) + star)

print(f"\n遮角标后出现 8p→5p 的次数 = {flips}")
print("只有 >0 才值得把遮角标接进生产链路；=0 说明假设不成立（或角标不在左上角）。")
