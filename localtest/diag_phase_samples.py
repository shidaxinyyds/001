# -*- coding: utf-8 -*-
"""换三张 / 非牌局 两条判据的全素材总览：正样本是否命中、负样本是否误报。

为什么这张表是前提：C16（换三张漏判）与 A6（清了屏不彻底）之前各自被我用**局部
证据**下过结论——C16 说过"判据两边都不会"（只测了 3 帧），A6 说过"缺负样本"
（其实 `ad_screen_01.jpg` 一直在仓库里）。一次性把全部正/负样本摆平，才有资格
说"这条判据行/不行"。

判据按真值列打：swap 帧应 is_swap_phase=True；ad/大厅帧应 _is_mahjong_table=False。
误报同样要数出来：任何 play 帧被判成 swap，或牌桌帧被判成非牌桌，都计入。

用法: py -3.10 -X utf8 localtest/diag_phase_samples.py
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

# (路径, 平台, 真值)  —— truth: swap / play / ad / dingque
CASES = [
    ("shots_batch3/jj_swap_01.jpg", "jj", "swap"),
    ("shots_batch3/ad_screen_01.jpg", "generic", "ad"),
    ("shots_batch3/jj_play_03.jpg", "jj", "play"),
    ("shots_batch3/jj_play_04.jpg", "jj", "play"),
    ("shots_multi/tencent_swap_01.jpg", "tencent", "swap"),
    ("shots_multi/tencent_pick_01.jpg", "tencent", "pick"),
    ("shots_multi/tuyou_swap_01.jpg", "tuyou", "swap"),
    ("shots_multi/tuyou_dingque_01.jpg", "tuyou", "dingque"),
    ("shots_multi/tuyou_pick_01.jpg", "tuyou", "pick"),
    ("shots_multi/jj_swap_01.jpg", "jj", "swap"),
    ("shots_multi/jj_dingque_01.jpg", "jj", "dingque"),
    ("shots_multi/queshen_play_01.jpg", "gd_queshen", "play"),
    ("shots_report/shushan_swap_01.jpg", "shushan", "swap"),
    ("shots_report/shushan_swap_02.jpg", "shushan", "swap"),
    ("shots_report/shushan_dingque_01.jpg", "shushan", "dingque"),
    ("shots_report/material_bank_01.jpg", "generic", "ad"),
    ("shots_report/zj_popup_01.jpg", "zj_sichuan", "popup"),
    ("shots_report/zj_anomaly_01.jpg", "zj_sichuan", "ad"),
    ("shots_batch3/zj_swap_03.jpg", "zj_sichuan", "swap"),
    ("shots_batch3/zj_play_03.jpg", "zj_sichuan", "play"),
    ("shots_tuyou_select/s1_select_cd17.jpg", "tuyou", "pick"),
    ("shots_tuyou_select/s4_after_swap_cd04.jpg", "tuyou", "play"),
]

yolo = YOLODetector()
grid = TencentGridDetector()

print(f"{'帧':40s} {'真值':8s} {'swap: yolo':10s} {'grid':10s} {'牌桌探针':6s}")
hit = miss = fp = 0
for rel, platform, truth in CASES:
    p = os.path.join(HERE, rel)
    img = cv2.imread(p) if os.path.exists(p) else None
    if img is None:
        print(f"{rel:40s} {'--':8s} 缺素材")
        continue
    set_platform_explicit(platform)
    try:
        ys = bool(yolo.is_swap_phase(img))
    except Exception as e:
        ys = f"err"
    try:
        gs = bool(grid.is_swap_phase(img))
    except Exception:
        gs = "err"
    try:
        tbl = bool(grid._is_mahjong_table(img)) if hasattr(grid, "_is_mahjong_table") else None
    except Exception:
        tbl = "err"
    if truth == "swap":
        if ys or gs:
            hit += 1
            tag = "命中"
        else:
            miss += 1
            tag = "漏判"
    elif truth == "ad":
        if tbl is False:
            hit += 1
            tag = "正确识别为非牌桌"
        else:
            fp += 1
            tag = "误判为牌桌"
    else:
        if ys or gs:
            fp += 1
            tag = "误报成换牌"
        else:
            hit += 1
            tag = "ok"
    print(f"{rel:40s} {truth:8s} {str(ys):10s} {str(gs):10s} {str(tbl):6s} {tag}")

print(f"\n正样本命中 {hit}  漏判/误报 {miss + fp}")
