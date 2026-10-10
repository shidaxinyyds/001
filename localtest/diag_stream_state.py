# -*- coding: utf-8 -*-
"""连帧状态总览：A1~A6 每一条在每一帧的实际取值，一次看全。

为什么要这张表：A 层六条都被归给「帧间状态」，但它们各自的成立条件不同，而
v1.7.8 上线事件源牌河之后，`discard_labels`（牌行路）与 `bg_seen`（后台路）的合并
口径已经和量它们那次不一样了。凭旧印象改代码这轮已经烧掉过一次，所以先把每一帧
的读数、沿用标记、阶段、结论来源、对手推断全部摊出来，再决定动哪一条。

用法: py -3.10 -X utf8 localtest/diag_stream_state.py
"""
from __future__ import annotations

import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import engine.engine as E  # noqa: E402
import test_stream_guard as SG  # noqa: E402

BATCH = os.path.join(HERE, "shots_batch3")

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    rows = []
    for (name, truth, phase) in SG.STREAM:
        img = cv2.imread(os.path.join(BATCH, name))
        if img is None:
            print("!! 读不到夹具", name)
            continue
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
        rows.append((name, truth, phase, d))
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm

print(f"{'帧':16s} {'读到手牌':>8s} {'面板手牌':>8s} {'沿用':>4s} {'暗层':>4s} "
      f"{'缺失':>4s} {'status':9s} {'阶段':22s} {'徽标':8s} {'最优':6s} "
      f"{'听牌概率':16s} {'对手断门':10s} {'牌河四区'}")
for name, _truth, _phase, d in rows:
    dg = d.get("diag") or {}
    ranges = d.get("hand_ranges") or []
    probs = [round(float(r.get("tenpai_prob") or 0), 2) for r in ranges]
    zones = dg.get("river_zones") or {}
    zs = "/".join(str(zones.get(k, 0)) for k in ("bottom", "top", "left", "right"))
    print(f"{name:16s} {int(dg.get('raw_hand') or 0):8d} "
          f"{len((d.get('hand') or '')) // 2:8d} "
          f"{'Y' if d.get('hand_carried_over') else '.':>4s} "
          f"{'Y' if d.get('hand_dim') else '.':>4s} "
          f"{int(d.get('hand_missing') or 0):4d} "
          f"{str(d.get('status')):9s} {str(d.get('phase_label'))[:22]:22s} "
          f"{str(d.get('tactical_badge'))[:8]:8s} {str(d.get('best') or '-')[:6]:6s} "
          f"{str(probs):16s} {str(d.get('opponents_dingque') or []):10s} {zs}")

print("\n对照：每帧人眼真值（手牌多重集 / 阶段）")
for name, truth, phase in SG.STREAM:
    print(f"  {name:16s} {phase:6s} {truth}")
