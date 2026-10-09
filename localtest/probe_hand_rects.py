# -*- coding: utf-8 -*-
"""打印每帧手牌行的**逐张 rect + 标签 + 分数**（收割模板要用精确坐标）。

为什么需要它：补字牌模板必须裁得准（多一条边就会把相邻牌的黑边带进模板，
实测过这种模板反而把整行分数拉低）。而面板只给最终手牌串，不给坐标。

用法: py -3.10 -X utf8 localtest/probe_hand_rects.py [帧名前缀 ...]
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
import diag_report_frames as DR  # noqa: E402


def probe(img, platform, mode):
    """跑一帧，返回 (手牌行 rect 列表, payload)。rect 取自 _reconcile_hand_tiles 的入参。"""
    seen = {}
    orig_rc = E._reconcile_hand_tiles

    def spy(stable, row):
        # 只记「看起来就是手牌行」的那一次：行内元素都是 (rect,label,conf) 三元组，
        # 且 rect 高>宽（竖牌，实测 112x151）。不筛会把牌河/副露的行也当手牌印出来。
        def ok(rw):
            return bool(rw) and all(len(t) == 3 and t[0][3] > t[0][2] for t in rw)
        if ok(row) and (not seen.get("row") or len(row) > len(seen["row"])):
            seen["row"] = list(row)
        return orig_rc(stable, row)

    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    E._reconcile_hand_tiles = spy
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
    finally:
        E._reconcile_hand_tiles = orig_rc
        E.load_platform, E.load_mode = orig_lp, orig_lm
    return seen.get("row") or [], d


def main() -> int:
    keys = sys.argv[1:]
    for name, pf, md in DR.FRAMES:
        if keys and not any(k in name for k in keys):
            continue
        img = cv2.imread(os.path.join(DR.SHOT_DIR, name))
        if img is None:
            print(f"!! 读不到 {name}")
            continue
        row, d = probe(img, pf, md)
        print(f"\n=== {name} [{pf}/{md}]  框数={len(row)}  "
              f"面板张数={len((d.get('hand') or '')) // 2}")
        for i, item in enumerate(row, 1):
            # 不拆箱写死三元组：拿到什么形状就印什么（上一版在这里直接抛异常，
            # 探针自已在测空时最该说的是「结构变了」，而不是堆栈）
            try:
                r, lab, conf = item
                x, y, w, h = [int(v) for v in r]
                print(f"  {i:2d}  rect=({x},{y},{w},{h})  label={lab}  conf={float(conf):.3f}")
            except Exception as e:
                print(f"  {i:2d}  形状异常 {type(item)} {item!r} ({e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
