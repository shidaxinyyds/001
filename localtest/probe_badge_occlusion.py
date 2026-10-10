# -*- coding: utf-8 -*-
"""B12 的可验证尝试：把「缺」角标区域遮掉，被遮的五筒能不能读对。

台账里钉着一条实测错值（`test_stream_guard.KNOWN_STREAM_DEFECTS`）：
    wrong_read:zj_play_04.jpg  = ["5p", "8p"]     # 该读 5p 的位置读成了 8p
    phantom_tile:zj_play_04.jpg = ["8p"]          # 屏上根本没有 8p
用户报的原话是「带『缺』角标的五筒被读成八筒」—— 角标盖在牌面左上角，遮掉了
筒子的一个点，五筒(梅花形 5 点)被遮后与八筒的点位分布更接近。

这里不猜：对同一批框分别用「原图」与「遮掉角标区域」两种方式分类，把读数与分数
并排打出来。只有遮掉后 5p 真的赢过 8p，才值得改生产链路。

用法: py -3.10 -X utf8 localtest/probe_badge_occlusion.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from platforms import set_platform_explicit  # noqa: E402
from modes import available_set  # noqa: E402

FRAME = os.path.join(HERE, "shots_batch3", "zj_play_04.jpg")
img = cv2.imread(FRAME)
if img is None:
    sys.exit(f"读不到夹具 {FRAME}")

set_platform_explicit("zj_sichuan")
det = TencentGridDetector()
avail = sorted(available_set("sc_hz"))

# 手牌带区域：按牌面高度找底部一排（与引擎同口径的简化版）
ih, iw = img.shape[:2]
band = img[int(ih * 0.76):ih, :]
gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
_, th = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
boxes = []
for c in cnts:
    x, y, w, h = cv2.boundingRect(c)
    if h < 40 or w < 20 or w > h:
        continue
    boxes.append((x, y + int(ih * 0.76), w, h))
boxes.sort(key=lambda b: b[0])
print(f"底部手牌带找到 {len(boxes)} 个框")


def classify(crop, tag):
    lbl, sc = det.classify_tile(crop, avail=avail)
    return f"{tag}: {lbl} ({sc:.3f})"


rows = []
for i, (x, y, w, h) in enumerate(boxes):
    crop = img[y:y + h, x:x + w]
    if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10:
        continue
    orig = classify(crop, "原图")
    # 角标遮挡：游戏把「缺」角标画在牌面左上角，约占宽 45% × 高 30%
    masked = crop.copy()
    mh = max(6, int(masked.shape[0] * 0.30))
    mw = max(6, int(masked.shape[1] * 0.45))
    masked[:mh, :mw] = np.median(
        masked.reshape(-1, 3), axis=0).astype(masked.dtype)
    mres = classify(masked, "遮角标")
    rows.append((i + 1, orig, mres))

for i, a, b in rows:
    flag = "   <== 读数被遮角标改变" if a.split(":")[1].strip() != b.split(":")[1].strip() else ""
    print(f"第{i:2d}框  {a:22s}  {b:24s}{flag}")

print("\n对照：台账里这帧的错值是「该读 5p 的位置读成 8p」。")
print("若上面出现 8p→5p 的转变，才值得把遮角标接进生产链路。")
