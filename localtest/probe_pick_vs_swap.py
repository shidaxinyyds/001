# -*- coding: utf-8 -*-
"""改阶段判序前的必要一问：换三张帧上「候选牌」能不能被读出来？

想把 `is_swap_phase` 的误报（选牌弹窗被判成换三张）修掉，最自然的方案是「pick
证据优先于 swap」——前提是**真换三张画面读不出候选牌**。这个前提不能假设：换三张
也要玩家从手牌里挑 3 张，中央同样可能出现可读的牌面。若那边也读得出 >=2 张候选，
把 pick 提前就是拿误报换漏报，比现状更糟。

所以对 12 帧同时量三样：现有 swap 判据、现有 pick 判据、候选牌张数。
只有出现「swap 帧候选=0 且 pick 帧候选>=2」这种干净分离，才允许改判序。

用法: py -3.10 -X utf8 localtest/probe_pick_vs_swap.py
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
from recognition.yolo_detector import YOLODetector  # noqa: E402
from platforms import set_platform_explicit  # noqa: E402

CASES = [
    ("shots_batch3/jj_swap_01.jpg", "jj", "swap"),
    ("shots_multi/jj_swap_01.jpg", "jj", "swap"),
    ("shots_multi/tencent_swap_01.jpg", "tencent", "swap"),
    ("shots_multi/tuyou_swap_01.jpg", "tuyou", "swap"),
    ("shots_report/shushan_swap_01.jpg", "shushan", "swap"),
    ("shots_report/shushan_swap_02.jpg", "shushan", "swap"),
    ("shots_batch3/zj_swap_03.jpg", "zj_sichuan", "swap"),
    ("shots_multi/tencent_pick_01.jpg", "tencent", "pick"),
    ("shots_multi/tencent_pick_02.jpg", "tencent", "pick"),
    ("shots_multi/tencent_pick_03.jpg", "tencent", "pick"),
    ("shots_multi/tuyou_pick_01.jpg", "tuyou", "pick"),
    ("shots_tuyou_select/s1_select_cd17.jpg", "tuyou", "pick"),
    ("shots_multi/queshen_play_01.jpg", "gd_queshen", "play"),
    ("shots_batch3/jj_play_03.jpg", "jj", "play"),
]

yolo, grid = YOLODetector(), TencentGridDetector()
print(f"{'帧':38s} {'真值':5s} {'swap?':6s} {'pick?':6s} {'候选张数':>6s}  分离性")
sep_ok = True
for rel, platform, truth in CASES:
    p = os.path.join(HERE, rel)
    img = cv2.imread(p)
    if img is None:
        print(f"{rel:38s} {truth:5s} 缺素材")
        continue
    set_platform_explicit(platform)
    try:
        sw = bool(grid.is_swap_phase(img))
    except Exception:
        sw = False
    try:
        pk = bool(grid.is_pick_phase(img))
    except Exception:
        pk = False
    try:
        n = len(grid.detect_pick_candidates(img) or [])
    except Exception:
        n = -1
    note = ""
    if truth == "swap" and n >= 2:
        note = "← 换三张帧也读得出候选：pick 优先会把真换牌判错！"
        sep_ok = False
    if truth == "pick" and n < 2:
        note = "← 选牌帧读不出候选：pick 判据本身在这帧失效"
    print(f"{rel:38s} {truth:5s} {str(sw):6s} {str(pk):6s} {n:6d}  {note}")

print("\n能否用「候选张数」做 swap/pick 的分判：" + ("可以（干净分离）" if sep_ok else "不可以"))
