# -*- coding: utf-8 -*-
"""量 `detect_dingque` 在 9 张真机夹具上的读数：它是不是把方位盘读成了定缺盘。

背景：A4/A7/A8 的"结算页/局中页也报本家缺门"，上一轮已排除阶段判据（现有几何通路
8/9 正确），剩下的嫌疑就是 `detect_dingque`。它每 4 帧无条件重读一次，所以只要它在
非定缺屏上也返回花色，整局就会不断产出（还会变的）缺门。

要回答的问题很具体：
  - 6 张真定缺页：应返回某个花色（这是它该做的事）
  - 3 张非牌局页（开局等待 / 两张结算）：应返回 None
  - 顺带看旧素材里那几张"名字带 dingque 实为局中"的帧返回什么

如果非定缺屏也返回花色，修法就有两个候选，都要先看数据再选：
  A) 只在 is_dingque_phase 为真时才采纳 detect_dingque 的结果（用阶段做上下文门）
  B) 读出后按"一局只发生一次"锁存，并给锁存一个可靠的释放条件

用法: py -3.10 -X utf8 localtest/probe_detect_dingque.py
"""
from __future__ import annotations

import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from engine.engine import detect_dingque, DINGQUE_SUIT_NAMES  # noqa: E402
from platforms import set_platform_explicit  # noqa: E402

SUIT_NAMES = {0: "万", 1: "筒", 2: "条", None: "—"}

CASES = []
FIX = os.path.join(HERE, "shots_phase_fix")
for n in sorted(os.listdir(FIX)):
    if n.endswith(".jpg"):
        CASES.append((os.path.join(FIX, n), "定缺页" if n.startswith("dq_") else "非牌局"))
# 旧素材：文件名带 dingque/swap 但已核实为局中/结算，用来验证"结算页也报缺门"
for rel, truth in (("shots_batch3/shushan_dingque_03.jpg", "局中(误名)"),
                   ("shots_batch3/zj_dingque_02.jpg", "局中(误名)"),
                   ("shots_report/shushan_dingque_01.jpg", "定缺?"),
                   ("shots_report/zj_anomaly_01.jpg", "非牌局"),
                   ("shots_batch3/ad_screen_01.jpg", "非牌局")):
    p = os.path.join(HERE, rel)
    if os.path.exists(p):
        CASES.append((p, truth))

g = TencentGridDetector()
print(f"{'夹具':44s} {'detect_dingque':>14s}  {'is_dingque_phase':>16s}  [真值]")
bad = 0
for path, truth in CASES:
    img = cv2.imread(path)
    if img is None:
        continue
    set_platform_explicit("zj_sichuan")
    try:
        suit = detect_dingque(img)[0]
    except Exception as e:
        suit = f"err:{type(e).__name__}"
    try:
        ph = bool(g.is_dingque_phase(img))
    except Exception:
        ph = None
    name = os.path.relpath(path, HERE)
    # 非牌局/局中屏却读出花色 = 误读（这正是 A4/A7/A8 的来源）
    if truth != "定缺页" and isinstance(suit, int):
        bad += 1
        mark = "  ← 非定缺屏读出缺门"
    elif truth == "定缺页" and not isinstance(suit, int):
        bad += 1
        mark = "  ← 定缺屏读不出缺门"
    else:
        mark = ""
    print(f"{name:44s} {SUIT_NAMES.get(suit, str(suit)):>14s}  {str(ph):>16s}  [{truth}]{mark}")

print(f"\n误读/漏读合计: {bad} / {len(CASES)}")
