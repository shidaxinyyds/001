# -*- coding: utf-8 -*-
"""数一件事：非牌局帧里 `_reset_game_state` / warmup 重装配 到底被谁在本帧内调用。

上一轮量到熔丝瞬间是 `curr_raw_n=0, streak=1, warmup=1`，两个前置条件同时不满足；
而喂完整帧后 warmup 又回到 0。这个组合只有一个解释：**本帧后半段有人把状态重置了**
（重置会清 streak 并重新装 warmup）。谁调、调几次，是修这条的唯一前提——不然就是
在猜。

做法：给几个会改动这套状态的方法装上计数器，跑 牌局3帧 -> 非牌局10帧，看谁在跳。

用法: py -3.10 -X utf8 localtest/diag_reset_calls.py
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


CALLS = {"_reset_game_state": 0, "_clear_discard_ledgers": 0, "warmup_rearm": 0}

orig_reset = E.Engine._reset_game_state
orig_clear = E.Engine._clear_discard_ledgers

WARMUP_DEFAULT = None


def reset_spy(self):
    CALLS["_reset_game_state"] += 1
    before = (getattr(self, "_empty_hand_streak", None),
              getattr(self, "_warmup_left", None))
    r = orig_reset(self)
    after = (getattr(self, "_empty_hand_streak", None),
             getattr(self, "_warmup_left", None))
    print(f"   [_reset_game_state #{CALLS['_reset_game_state']}] "
          f"(streak,warmup) {before} -> {after}")
    return r


def clear_spy(self):
    CALLS["_clear_discard_ledgers"] += 1
    print(f"   [_clear_discard_ledgers #{CALLS['_clear_discard_ledgers']}]")
    return orig_clear(self)


E.Engine._reset_game_state = reset_spy
E.Engine._clear_discard_ledgers = clear_spy

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    g = load("zj_play_04.jpg")
    n = load("material_bank_01.jpg")

    def one(img, tag, i):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            d = json.loads(eng.process(img).result)
        dg = d.get("diag") or {}
        lines = [l for l in buf.getvalue().splitlines() if "_reset_game_state" in l
                 or "_clear_discard_ledgers" in l]
        print(f"{tag} #{i:<2d} status={str(d.get('status')):9s} "
              f"count={int(d.get('count') or 0):2d} "
              f"fuse={json.dumps(dg.get('fuse') or {}, ensure_ascii=False)}")
        for l in lines:
            print("      " + l)

    print("=== 牌局帧")
    for i in range(3):
        one(g, "牌局", i + 1)
    print("\n=== 非牌局帧（看谁在本帧内重置状态）")
    for i in range(6):
        one(n, "非牌局", i + 1)
    print("\n合计:", CALLS)
finally:
    E.Engine._reset_game_state = orig_reset
    E.Engine._clear_discard_ledgers = orig_clear
    E.load_platform, E.load_mode = orig_lp, orig_lm
