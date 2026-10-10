# -*- coding: utf-8 -*-
"""定 A5/A6 的方向：非牌局画面里，熔丝的三个前置条件各自卡在哪儿。

上一轮已经复现「连喂 10 帧非牌局画面，手牌仍挂 11 张」，但没查明熔丝为什么不响。
`_empty_hand_fuse` 要同时满足三件事：`_match_started`、`_warmup_left <= 0`、
`_empty_hand_streak >= EMPTY_HAND_RESET_FRAMES(=5)`。同时 pick 阶段的成立还要求
「候选牌 >= 2」。这几件全是引擎实例上的属性，喂完帧直接读就行——不需要猜。

两种结果对应两种相反的修法：
  - streak 到不了 5  → 说明非牌局画面**确实读出了牌**（素材卡被当成牌），那是识别层
    收紧 pick 判据的事，不能去动熔丝；
  - streak 到 5 却没重置 → 说明 pick 那条路绕过了熔丝（或被别处清零），那是状态层。

用法: py -3.10 -X utf8 localtest/diag_fuse_state.py
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
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402


def load(name):
    for d in ("shots_batch3", "shots_report"):
        p = os.path.join(HERE, d, name)
        if os.path.exists(p):
            img = cv2.imread(p)
            if img is not None:
                return img
    raise AssertionError(f"找不到夹具 {name}")


orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    g = load("zj_play_04.jpg")
    n = load("material_bank_01.jpg")

    def one(img, tag, i):
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
        dg = d.get("diag") or {}
        print(f"{tag} #{i:<2d} status={str(d.get('status')):9s} "
              f"raw_hand={int(dg.get('raw_hand') or 0):2d} count={int(d.get('count') or 0):2d} "
              f"pick={str(bool(d.get('pick_phase'))):5s} swap={str(bool(d.get('swap_phase'))):5s} "
              f"dq={str(bool(d.get('dingque_phase'))):5s} | "
              f"streak={int(getattr(eng, '_empty_hand_streak', -1)):2d} "
              f"warmup={int(getattr(eng, '_warmup_left', -1)):2d} "
              f"started={int(bool(getattr(eng, '_match_started', False)))} "
              f"fuse={eng._empty_hand_fuse()} "
              f"non_table={int(getattr(eng, '_non_table_frames', -1))} "
              f"| 熔丝输入={json.dumps(dg.get('fuse') or {}, ensure_ascii=False)}")

    print("=== 牌局帧（建立状态）")
    for i in range(3):
        one(g, "牌局", i + 1)
    print("\n=== 非牌局帧（看熔丝为什么不响）")
    for i in range(10):
        one(n, "非牌局", i + 1)
    print("\n=== 关键属性快照")
    print("  _pending_reset_buf 存在:", hasattr(eng, "_pending_reset_buf"))
    print("  EMPTY_HAND_RESET_FRAMES =", E.EMPTY_HAND_RESET_FRAMES)
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm
