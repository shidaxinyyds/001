# -*- coding: utf-8 -*-
"""量 B/C 两条召回缺陷的共同问题：框到底有没有被**检测**出来。

两条缺陷都表现为"面板少牌"，但根子可能在两层，修法完全不同：
  · 检测层没给框 → 后面任何补打分都救不到（要改的是找牌行的几何/阈值）；
  · 检测给了框、分类给空 label → 是分类/闸门层（v1.7.6 已经能救一部分）。
所以这里对每个 case 都跑 `classify=False` 的纯几何检测，数「几何框」有几张，
再数「分类后有 label」有几张。两个数一比，缺陷在哪一层就定了。

用法: py -3.10 -X utf8 localtest/probe_recall_layers.py
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

import diag_report_frames as DR  # noqa: E402
import engine.engine as E  # noqa: E402

CASES = [
    # (帧, 平台, 玩法, 屏上真实张数, 这一条要回答什么)
    ("zj_popup_01.jpg", "zj_sichuan", "sc_hz", 13, "B 弹窗压暗：几何层有没有框？"),
    ("zj_play_02.jpg", "zj_sichuan", "sc_hz", 13, "B 的对照：同一局没弹窗时几帧差多少"),
    ("queshen_play_03.jpg", "gd_queshen", "sc_hz", 14, "C 漏框：第 14 张在几何层有没有？"),
    ("queshen_play_03.jpg", "gd_queshen", "std_tdh", 14, "C 的对照：全牌玩法下同一帧"),
]


def run(name, pf, md):
    img = cv2.imread(os.path.join(DR.SHOT_DIR, name))
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: pf
    E.load_mode = lambda *a, **k: md
    try:
        eng = E.Engine()
        hand_det = eng.get_hand_detector()
        rows_geo = hand_det.detect_all_rows(img, classify=False, allow_rotation=False)
        d = DR.run(img, pf, md)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
    longest = max(rows_geo, key=len) if rows_geo else []
    # 手牌行取「最靠画面底部」的那一行（牌河在中上部）
    bottom = max(rows_geo, key=lambda r: sum(x[0][1] + x[0][3] / 2 for x in r) / max(1, len(r))) \
        if rows_geo else []
    tiles = d.get("tiles") or []
    labeled = sum(1 for t in tiles if t[4])
    band = [t[1] for t in bottom if t[1] is not None]
    # 手牌行的牌面亮度（弹窗压暗时这个数会掉）
    br = []
    for r, _l, _c in bottom:
        x, y, w, h = [int(v) for v in r]
        p = img[y:y + h, x:x + w]
        if p.size:
            br.append(float(cv2.cvtColor(p, cv2.COLOR_BGR2HSV)[:, :, 2].mean()))
    print(f"\n{name} [{pf}/{md}]")
    print(f"  几何层：行数={len(rows_geo)} 最长行={len(longest)} 最底行={len(bottom)} "
          f"(最底行有标签 {len(band)})")
    print(f"  面板层：tiles={len(tiles)} 有 label={labeled} 手牌串={len((d.get('hand') or '') ) // 2} "
          f"missing={d.get('hand_missing')} uncertain={d.get('hand_uncertain')}")
    if br:
        print(f"  最底行牌面 V 通道：min={min(br):.0f} 中位={sorted(br)[len(br)//2]:.0f} "
              f"max={max(br):.0f}")
    print(f"  status={d.get('status')} message={d.get('message')!r}")


for name, pf, md, real, ask in CASES:
    print(f"--- {ask}")
    run(name, pf, md)
