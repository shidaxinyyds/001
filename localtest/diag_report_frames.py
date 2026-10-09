# -*- coding: utf-8 -*-
"""把这轮用户新拍的 10 帧牌局图逐帧跑一遍，打印引擎自己说的那套事实。

为什么要先跑：用户报的 4 条（漏识别 / 阶段错乱 / 关键阶段不显示 / 字牌认不出）
全都指向同一类问题——「屏上有什么」与「面板说什么」不一致。不一致必须逐帧量化，
否则改哪儿都是凭印象。平台与玩法按**面板标题上写着的那个**填（用户当时就这么配的）。
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
import layer_cost as lc  # noqa: E402

SHOT_DIR = os.path.join(HERE, "shots_report")

# 帧 -> (平台, 玩法)。玩法一律取面板标题上写着的那一条（截图里全是「血流红中」）。
FRAMES = [
    ("shushan_dingque_01.jpg", "shushan", "sc_hz"),
    ("shushan_swap_01.jpg", "shushan", "sc_hz"),
    ("shushan_dingque_02.jpg", "shushan", "sc_hz"),
    ("jj_play_02.jpg", "jj", "sc_hz"),
    ("queshen_play_03.jpg", "gd_queshen", "sc_hz"),
    ("queshen_play_04.jpg", "gd_queshen", "sc_hz"),
    ("queshen_play_05.jpg", "gd_queshen", "sc_hz"),
    ("zj_swap_01.jpg", "zj_sichuan", "sc_hz"),
    ("zj_anomaly_01.jpg", "zj_sichuan", "sc_hz"),
    ("zj_play_02.jpg", "zj_sichuan", "sc_hz"),
]


def run(img, platform, mode):
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm


def main() -> int:
    only = sys.argv[1:] or None
    for name, pf, mode in FRAMES:
        if only and not any(o in name for o in only):
            continue
        p = os.path.join(SHOT_DIR, name)
        img = cv2.imread(p)
        if img is None:
            print(f"!! 读不出来：{p}")
            continue
        d = run(img, pf, mode)
        hand = lc.canon_mpsz(d.get("hand", ""))
        ph = d.get("match_phase") or {}
        rows = (d.get("diag") or {}).get("rows") or []
        print(f"\n=== {name}  [{pf}/{mode}]")
        print(f"  status={d.get('status')} phase={ph.get('key')!r} "
              f"label={d.get('phase_label')!r} badge={d.get('tactical_badge')!r} "
              f"dingque={d.get('dingque_name')!r}")
        print(f"  count={d.get('count')} hand({len(hand)})={' '.join(hand)}")
        print(f"  uncertain={d.get('hand_uncertain')} mode_off={d.get('mode_off_catalog')}")
        print(f"  message={d.get('message')!r}")
        if rows:
            print(f"  rows={rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
