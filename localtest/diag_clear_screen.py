# -*- coding: utf-8 -*-
"""A5/A6 实验：牌局帧之后切到非牌局画面，同一个引擎要多久才真的"清空"。

为什么必须连帧喂：
  A6「屏上是别的游戏广告，面板还挂着手牌 2 张 + 摸牌中」
  A5「牌局中却报『等待对局接入 · 实时感知待命』」
两条都是**帧间状态**问题。单帧新建引擎去跑，永远只会看到"这一帧干净"，量不到
残留，也量不到清屏要多少帧 —— 这正是这个项目里"每轮都全绿、装上还是老样子"的
盲区之一。

本实验把三段都量出来：
  1) 牌局帧若干次：应当有手牌、有结论、status 不是 waiting；
  2) 紧接非牌局帧若干次：手牌/牌块/建议/摸牌徽标必须在**有限帧数**内归零，
     且 status 转成 waiting；超过阈值就是 A6；
  3) 回到牌局帧：能不能立刻恢复（防止"清干净了就再也回不来"那种假修复）。

用法: py -3.10 -X utf8 localtest/diag_clear_screen.py [牌局帧] [非牌局帧]
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

GAME = sys.argv[1] if len(sys.argv) > 1 else "zj_play_04.jpg"
NONGAME = sys.argv[2] if len(sys.argv) > 2 else "material_bank_01.jpg"


def load(name):
    for d in ("shots_batch3", "shots_report"):
        p = os.path.join(HERE, d, name)
        if os.path.exists(p):
            img = cv2.imread(p)
            if img is not None:
                return img
    raise AssertionError(f"找不到夹具 {name}（找过 shots_batch3 / shots_report）")


def state(d):
    """把一帧面板会显示的东西压成一行。"""
    tiles = d.get("tiles") or []
    return {
        "status": d.get("status"),
        "count": int(d.get("count") or 0),
        "tiles": len(tiles),
        "advice": len(d.get("advice") or []),
        "best": d.get("best") or "-",
        "drawing": bool(d.get("is_drawing")),
        "badge": d.get("tactical_badge") or "-",
        "msg": str(d.get("message") or "")[:44],
    }


orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    g_img = load(GAME)
    n_img = load(NONGAME)

    def feed(img, tag, n):
        for i in range(n):
            with contextlib.redirect_stdout(io.StringIO()):
                d = json.loads(eng.process(img).result)
            s = state(d)
            print(f"{tag} #{i + 1:<2d} status={s['status']:9s} 手牌={s['count']:2d} "
                  f"牌块={s['tiles']:2d} 建议={s['advice']} 最优={s['best']:<4s} "
                  f"摸牌={'Y' if s['drawing'] else '.':1s} 徽标={s['badge']:<8s} {s['msg']}")

    print("=== 1) 牌局中（应：有手牌、有结论、非 waiting）")
    feed(g_img, "牌局", 4)
    print("\n=== 2) 切到非牌局画面（A6：应在有限帧内归零并转 waiting）")
    feed(n_img, "非牌局", 10)
    print("\n=== 3) 回到牌局（应立刻恢复；否则是'清完回不来'）")
    feed(g_img, "牌局", 3)
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm
