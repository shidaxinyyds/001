# -*- coding: utf-8 -*-
"""守恒硬门误判的定位：把「4筒 已见 5 张」这 5 张拆到每一路来源。

现象：一张读得清手牌的途游局中帧被守恒硬门判脏并拒答（advice=0、best=''）。
两个花色同时"已见 5 张"不像牌局事实，更像重复计数。但"像"不算证据——账本里
一个型的"已见"由四路合成：

  本家手牌 + 视觉牌河（含新上的事件源与后台轮廓扫描两路合并） + 副露 + 手牌差分推断

只有把每一路各自的张数打出来，才能判断是「事件源把同一张弃牌按不同框重复计入」
（那要修事件源）还是「硬门口径把不该相加的路相加了」（那要修门）。这两种修法相反，
猜一个动手就是再赌一次。

用法: py -3.10 -X utf8 localtest/probe_ledger_breakdown.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402

FRAME = os.path.join(HERE, "shots_multi", "tuyou_swap_01.jpg")

img = cv2.imread(FRAME)
if img is None:
    sys.exit(f"读不到夹具 {FRAME}，本判定不成立")

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "tuyou"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    with contextlib.redirect_stdout(io.StringIO()):
        d = json.loads(eng.process(img).result)

    led = d.get("tile_ledger") or {}
    hand = d.get("hand") or ""
    from collections import Counter
    hand_cnt = Counter(hand[i:i + 2] for i in range(0, len(hand) - 1, 2))

    print(f"status={d.get('status')}  advice={len(d.get('advice') or [])}  "
          f"violations={len(led.get('violations') or [])}")
    print(f"river_zones={json.dumps(d['diag'].get('river_zones'), ensure_ascii=False)}  "
          f"river_events={d['diag'].get('river_events')}  "
          f"river_submits={d['diag'].get('river_submits')}  "
          f"river_consumes={d['diag'].get('river_consumes')}")
    print("\nviolations 明细：")
    for v in (led.get("violations") or []):
        print("  ", v)

    print("\n逐型分解（只列已见 >= 4 的型，5 张的必然在这里）：")
    print(f"{'型':5s} {'手牌':>4s} {'视觉牌河':>8s} {'推断牌河':>8s} {'副露':>4s} {'账本已见':>8s}")
    vis = getattr(eng, "_visual_discards", {}) or {}
    inf = getattr(eng, "_inferred_discards", {}) or {}
    meld = getattr(eng, "_meld_counts_34", []) or []
    mono = dict(getattr(eng, "_monotonic_discards", {}) or {})
    labels = set(hand_cnt) | set(vis) | set(inf)
    for lab in sorted(labels, key=lambda x: -(mono.get(x, 0) + hand_cnt.get(x, 0))):
        seen = mono.get(lab, 0) + hand_cnt.get(lab, 0)
        if seen < 4:
            continue
        t34 = None
        try:
            from modes import mpsz_to_tile34_index
            t34 = mpsz_to_tile34_index(lab)
        except Exception:
            pass
        print(f"{lab:5s} {hand_cnt.get(lab, 0):4d} {vis.get(lab, 0):8d} "
              f"{inf.get(lab, 0):8d} {(meld[t34] if t34 is not None and t34 < len(meld) else 0):4d} "
              f"{seen:8d}")

    print("\n牌河事件源本帧交出的条目（前 20 条，看有没有同型重复）：")
    with contextlib.redirect_stdout(io.StringIO()):
        ev = eng._river_from_events(img)
    evc = Counter(l for l, _z in ev)
    print("  ", dict(evc))
    dup = {k: v for k, v in evc.items() if v > 4}
    print(f"  单帧内同一型 >4 张的：{dup or '无'}  ← 若有，就是事件源把非弃牌当成了弃牌")
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm
