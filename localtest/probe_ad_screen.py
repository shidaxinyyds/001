# -*- coding: utf-8 -*-
"""A6 的定位：广告页连喂时，究竟是谁在把旧手牌发上屏——跳帧回放还是真识别。

当前引擎实测（`diag_clear_screen.py zj_play_03.jpg ad_screen_01.jpg`）：广告页连喂
10 帧，面板一直是 手牌=10 / 建议=4 / 最优=2m / status=ok，只有 message 里诚实写了
「沿用上一帧手牌」。要修必须先分清两条完全不同的路：

  A) 走跳帧回放（画面完全相同 → 复用上一 payload）：那修法是「回放前复核牌桌」；
  B) 走真识别但识别器在广告页上仍框出"牌"：那是识别层，复核牌桌没用。

同时把场景探针对这三张非牌局帧的判定一起打出来——上一版我加过牌桌复核却"逐字未变"，
当时据此否证了假设；现在知道那次测量本身被 warmup 卡死污染过，所以必须重测。

用法: py -3.10 -X utf8 localtest/probe_ad_screen.py
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

GAME = os.path.join(HERE, "shots_batch3", "zj_play_03.jpg")
ADS = ["ad_screen_01.jpg", "material_bank_01.jpg", "zj_anomaly_01.jpg"]

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    g = cv2.imread(GAME)
    if g is None:
        sys.exit(f"读不到 {GAME}")
    with contextlib.redirect_stdout(io.StringIO()):
        eng.process(g)

    print("=== 广告页连喂 6 帧：每帧的 frame_skipped / 计数 / 探针 ===")
    ad = cv2.imread(os.path.join(HERE, "shots_batch3", ADS[0]))
    if ad is None:
        sys.exit(f"读不到 {ADS[0]}")
    for i in range(6):
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(ad).result)
        print(f"  帧{i + 1}  frame_skipped={d.get('frame_skipped')}  "
              f"count={d.get('count')}  tiles={len(d.get('tiles') or [])}  "
              f"advice={len(d.get('advice') or [])}  status={d.get('status')}  "
              f"carried={d.get('hand_carried_over')}")

    print("\n=== 场景探针对三张非牌局帧的判定（同一引擎、同平台） ===")
    for name in ADS:
        p = (os.path.join(HERE, "shots_batch3", name)
             if os.path.exists(os.path.join(HERE, "shots_batch3", name))
             else os.path.join(HERE, "shots_report", name))
        img = cv2.imread(p)
        if img is None:
            print(f"  {name}: 缺素材")
            continue
        try:
            v = eng._is_mahjong_table(img)
        except Exception as e:
            v = f"异常 {type(e).__name__}"
        print(f"  _is_mahjong_table({name}) = {v}")
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm
