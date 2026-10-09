# -*- coding: utf-8 -*-
"""量一件事：手牌行里「闸门内读不出/读得勉强」的那些框，放开到全 34 面重打分会怎样。

背景（实测，`localtest/build/gate_compare.py` 的结论）：用户在广东雀神上挂着川麻
玩法（血流红中，牌集只放开 7z）打广东麻将，屏上的 東/北/北 在**分类之前**就被闸门
挤出去 → 面板 14 张只报 11 张，其中 2 个框空 label、1 张被贴成 7z。同一帧换成全牌
玩法 14/14 全对 —— 模板够用，缺的是闸门外的牌面。

要放开闸门就必须先量清两件事，否则就是拿一个新错换一个旧错：
  ① 闸门外那张**读对时**的分数分布（决定接受门槛）；
  ② 本来读对的牌在全 34 打分下会不会被一张闸门外的高分牌顶掉（误接受 = 面板凭空
     多出字牌，比少报更坑，因为用户看不出来）。
所以这里对每一帧、每一张牌同时记两份分数：闸门内 top1 与全 34 top1，并按人眼真值
判 accept 是否正确。门槛是这么定出来的，不是拍的。

用法: py -3.10 -X utf8 localtest/probe_gate_rescue.py
"""
from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
import layer_cost as lc  # noqa: E402
from recognition.tencent_grid_detector import resolve_candidate_tiles  # noqa: E402

ALL34 = [f"{n}{s}" for s in "mps" for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)]

GT_MULTI = os.path.join(HERE, "gt", "shots_multi.json")
GT_BASE = os.path.join(HERE, "gt", "shots.json")
REPORT = os.path.join(HERE, "shots_report")


def _gt_codes(v):
    """真值表里 hand 有两种形状：mpsz 串（shots.json）与 code 列表（shots_multi.json）。"""
    if isinstance(v, list):
        return sorted(v)
    return sorted(v[i:i + 2] for i in range(0, len(v), 2))


def load_cases():
    """[(帧路径, 平台, 玩法, 人眼真值 or None)]"""
    out = []
    import diag_report_frames as DR
    for name, pf, md in DR.FRAMES:
        out.append((os.path.join(REPORT, name), pf, md, None))
    if os.path.exists(GT_MULTI):
        for e in json.load(open(GT_MULTI, encoding="utf-8"))["shots"]:
            out.append((os.path.join(HERE, "shots_multi", e["file"]),
                        e["platform"], e.get("effective_mode") or e["mode"],
                        _gt_codes(e["hand"])))
    return out


def score_pair(det, crop, gate):
    """同一枚牌面在「闸门内」与「全 34」下的 top1 (label, score)。"""
    res = {}
    for tag, avail in (("gate", None), ("full", ALL34)):
        face, scores, _ = det._score_crop(crop, avail)
        if not scores:
            res[tag] = (None, 0.0)
            continue
        best = max(scores, key=scores.get)
        res[tag] = (best, float(scores[best]))
    return res["gate"], res["full"]


def main() -> int:
    rows = []
    for path, pf, md, gt in load_cases():
        img = cv2.imread(path)
        if img is None:
            print(f"!! 读不到 {path}")
            continue
        orig_lp, orig_lm = E.load_platform, E.load_mode
        E.load_platform = lambda *a, **k: pf
        E.load_mode = lambda *a, **k: md
        try:
            eng = E.Engine()
            det = eng.get_hand_detector()
            gate = resolve_candidate_tiles(None, det._mode_tiles, det.full_honors)
            tiles = det.detect_all_rows(img, classify=False, allow_rotation=False)
            hand = max(tiles, key=len) if tiles else []
            for (r, _l, _c) in sorted(hand, key=lambda d: d[0][0]):
                x, y, w, h = [int(v) for v in r]
                crop = img[y:y + h, x:x + w]
                if crop.size == 0:
                    continue
                (gl, gs), (fl, fs) = score_pair(det, crop, gate)
                rows.append(dict(shot=os.path.basename(path), mode=md,
                                 gate_label=gl, gate_score=gs,
                                 full_label=fl, full_score=fs,
                                 outside=(fl not in gate)))
        finally:
            E.load_platform, E.load_mode = orig_lp, orig_lm

    outside = [r for r in rows if r["outside"]]
    print(f"共 {len(rows)} 枚，其中「全 34 的最优在闸门外」的 {len(outside)} 枚")
    print("\n闸门内分数 vs 全 34 分数（只看门外候选）：")
    for r in sorted(outside, key=lambda x: -x["full_score"])[:40]:
        print(f"  {r['shot']:26s} {r['mode']:8s} 闸内={r['gate_label']}/{r['gate_score']:.3f}"
              f"  全={r['full_label']}/{r['full_score']:.3f}")

    # 关键分布：门外候选被接受时，它在闸门内的分数有多低（决定「什么时候该重打分」）
    if outside:
        gi = np.array([r["gate_score"] for r in outside])
        fo = np.array([r["full_score"] for r in outside])
        print(f"\n门外候选：闸内分 min/med/max = {gi.min():.3f}/{np.median(gi):.3f}/{gi.max():.3f}")
        print(f"          全 34 分 min/med/max = {fo.min():.3f}/{np.median(fo):.3f}/{fo.max():.3f}")
    ins = [r for r in rows if not r["outside"]]
    if ins:
        ii = np.array([r["full_score"] for r in ins])
        print(f"门内候选（对照组）全 34 分 min/med = {ii.min():.3f}/{np.median(ii):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
