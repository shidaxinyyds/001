# -*- coding: utf-8 -*-
"""量一件事：蜀山那两帧被大字动画压住的牌，**单张**匹配分数是否明显低于其余牌。

为什么非要单张分数：行平均置信度不是遮挡信号（实测 queshen 两帧读数 100% 正确，
行均却只有 0.76/0.79；蜀山错读的两帧行均反而有 0.91/0.94）。拿一个分不开好坏的
数去触发「降级提示」，就是把假测量写进面板——比不给提示更糟。

用法: py -3.10 -X utf8 localtest/probe_tile_conf.py [帧名前缀 ...]
"""
import contextlib
import io
import json
import os
import sys

import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import engine.engine as E  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

SHOT_DIR = os.path.join(HERE, "shots_multi")
GT = {e["file"]: e for e in json.load(open(os.path.join(HERE, "gt", "shots_multi.json"),
                                           encoding="utf-8"))["shots"]}
FRAMES = [
    ("shushan_play_01.jpg", "shushan", "sc_hz"),
    ("shushan_play_02.jpg", "shushan", "sc_hz"),
    ("shushan_play_03.jpg", "shushan", "sc_hz"),
    ("shushan_dingque_01.jpg", "shushan", "sc_hz"),
    ("queshen_play_01.jpg", "gd_queshen", "gd_hz"),
    ("queshen_play_02.jpg", "gd_queshen", "sc_hz"),
    ("jj_play_01.jpg", "jj", "sc_hz"),
    ("tencent_pick_01.jpg", "tencent", "sc_hz"),
    ("tuyou_swap_01.jpg", "tuyou", "sc_hz"),
]


def probe(img, platform, mode):
    """返回本帧每次单张打分的 (top 分, top2 差距) 列表，按调用顺序。"""
    rec = []
    orig = TencentGridDetector._score_face

    def spy(self, face, avail=None, styles=None):
        out = orig(self, face, avail, styles)
        scores = out[1] or {}
        if scores:
            vals = np.array(sorted(scores.values(), reverse=True))
            order = sorted(scores, key=scores.get, reverse=True)
            label = str(order[0])
            second = str(order[1]) if len(order) > 1 else "-"
            rec.append((label, float(vals[0]),
                        float(vals[0] - vals[1]) if len(vals) > 1 else 1.0, second))
        return out

    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    TencentGridDetector._score_face = spy
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
    finally:
        TencentGridDetector._score_face = orig
        E.load_platform, E.load_mode = orig_lp, orig_lm
    return rec, d


def main():
    keys = sys.argv[1:]
    frames = [f for f in FRAMES if not keys or any(k in f[0] for k in keys)]
    for name, pf, md in frames:
        p = os.path.join(SHOT_DIR, name)
        img = cv2.imread(p)
        if img is None:
            print(f"!! 读不到 {p}")
            continue
        rec, d = probe(img, pf, md)
        e = GT.get(name, {})
        gt = sorted(e.get("hand", []))
        got = sorted((d.get("hand") or "")[i:i + 2] for i in range(0, len(d.get("hand") or ""), 2))
        exact = got == gt
        print(f"\n{name}  打分次数={len(rec)}  手牌读数{'==真值' if exact else '!=真值'}"
              f"  差集缺{sorted(set(gt) - set(got))} 多{sorted(set(got) - set(gt))}")
        print("  逐张 (label, top, margin, 亚军)，按 top 升序取前 6：")
        for lab, t, m, sec in sorted(rec, key=lambda x: x[1])[:6]:
            print(f"    {lab:3s} top={t:.3f} margin={m:.3f} 亚军={sec}")


if __name__ == "__main__":
    main()
