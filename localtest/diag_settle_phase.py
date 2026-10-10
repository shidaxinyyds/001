# -*- coding: utf-8 -*-
"""A4 复验：结算/空闲画面上，当前引擎还会不会报「定缺阶段」。

用户报的 A4 是「已经自摸 256 倍结算，面板还报定缺阶段」。本会话已经吃过四次
"拿旧截图的现象当现状修" 的亏，所以第一步永远是：**在当前引擎上重跑那张图**。

素材（都是真机帧，仓库里本来就有）：
  shushan_settle_01.jpg  蜀山结算画面
  zj_idle_01.jpg         指尖未开局/空闲
  shushan_dingque_03.jpg 蜀山定缺
  zj_dingque_02.jpg      指尖定缺

后两张是**正例**：它们报定缺阶段是对的。只有 settle/idle 两张报出定缺才是 A4。
先喂一帧真牌局建立"已在局中"，再喂目标帧 —— 结算画面的误判只有在"刚从局中切过来"
时才会暴露，单帧新建引擎测不到。

用法: py -3.10 -X utf8 localtest/diag_settle_phase.py
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

CASES = [
    ("shushan_settle_01.jpg", "shushan", "结算画面：不该报定缺阶段"),
    ("zj_idle_01.jpg", "zj_sichuan", "空闲/未开局：不该报定缺阶段"),
    ("shushan_dingque_03.jpg", "shushan", "正例：确实该报定缺"),
    ("zj_dingque_02.jpg", "zj_sichuan", "正例：确实该报定缺"),
]


def load(name):
    for d in ("shots_batch3", "shots_report", "shots_multi"):
        p = os.path.join(HERE, d, name)
        if os.path.exists(p):
            img = cv2.imread(p)
            if img is not None:
                return img
    return None


for name, platform, note in CASES:
    img = load(name)
    if img is None:
        print(f"{name:26s} 缺素材")
        continue
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: "sc_hz"
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        warm = load("zj_play_03.jpg") if platform == "zj_sichuan" else load("shushan_dingque_03.jpg")
        # 先喂一帧真牌局，模拟"刚从局中切到结算/空闲"
        if warm is not None and warm is not img:
            with contextlib.redirect_stdout(io.StringIO()):
                eng.process(warm)
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
    print(f"{name:26s} status={str(d.get('status')):9s} "
          f"dq_phase={str(bool(d.get('dingque_phase'))):5s} "
          f"swap={str(bool(d.get('swap_phase'))):5s} "
          f"count={d.get('count')}  advice={len(d.get('advice') or [])}  "
          f"phase={str(d.get('phase_label'))[:18]:18s} | {note}")
