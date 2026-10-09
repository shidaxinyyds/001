# -*- coding: utf-8 -*-
"""门槛测量：`HAND_UNREADABLE_CONF` 到底会不会把「读对的帧」也标成不可信。

口径（先把话说清楚，再决定要不要动手）：
  - 20 帧真机（`localtest/shots_multi/` + `gt/shots_multi.json`）；
  - 37 帧已校验腾讯 GT（`localtest/shots/` + `gt/shots.json`）。
每帧跑引擎，取 payload 的 `hand_uncertain`（哪几张的 top 模板分低于门槛），
再与「这帧的面板读数是否等于真值」并排列出来 —— 只有当**错读的帧被标、
读对的帧不被标**时，这条门槛才配写进面板。

用法: py -3.10 -X utf8 localtest/measure_hand_uncertain.py [--conf 0.55]
"""
import argparse
import contextlib
import io
import json
import os
import sys

import cv2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import engine.engine as E  # noqa: E402
import layer_cost as lc  # noqa: E402

MULTI = os.path.join(HERE, "shots_multi")
MULTI_GT = os.path.join(HERE, "gt", "shots_multi.json")
SHOTS = os.path.join(HERE, "shots")
SHOTS_GT = os.path.join(HERE, "gt", "shots.json")
# 已知缺陷帧（读数确实错，见 test_multi_hand_guard.KNOWN_DEFECTS）：它们不该算 FP/FN
DEFECT = {"shushan_play_01.jpg", "shushan_play_02.jpg"}


def _gt_codes(h):
    """两份真值表的 `hand` 形状不同（list of codes / 一个 mpsz 串），统一成排序码表。"""
    return sorted(h) if isinstance(h, list) else lc.canon_mpsz(h)


def frames():
    with open(MULTI_GT, encoding="utf-8") as f:
        for e in json.load(f)["shots"]:
            yield e["file"], os.path.join(MULTI, e["file"]), e["platform"], e["mode"], \
                _gt_codes(e["hand"]), "multi"
    with open(SHOTS_GT, encoding="utf-8") as f:
        for e in json.load(f)["shots"]:
            if not e.get("verified"):
                continue
            yield e["file"], os.path.join(SHOTS, e["file"]), "tencent", "sc_hz", \
                _gt_codes(e["hand"]), "tencent"


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", type=float, default=0.55)
    a = ap.parse_args()
    E.HAND_UNREADABLE_CONF = a.conf

    n_flagged_correct = n_flagged_wrong = n_missed_wrong = 0
    rows = []
    for name, path, pf, md, gt, tag in frames():
        img = cv2.imread(path)
        if img is None:
            print(f"!! 读不到 {path}")
            continue
        d = run(img, pf, md)
        got = lc.canon_mpsz(d.get("hand", ""))
        uncertain = d.get("hand_uncertain") or []
        correct = (got == gt)
        if uncertain and correct:
            n_flagged_correct += 1
        elif uncertain and not correct:
            n_flagged_wrong += 1
        elif not uncertain and not correct and name not in DEFECT:
            n_missed_wrong += 1
        rows.append((name, tag, correct, uncertain, got, gt))

    print(f"\n门槛 conf={a.conf}  ——  读对却被标={n_flagged_correct}  "
          f"读错且被标={n_flagged_wrong}  读错却没标(非已知缺陷)={n_missed_wrong}")
    print("\n只有下列帧被标为不可信：")
    for name, tag, correct, uncertain, got, gt in rows:
        if not uncertain:
            continue
        print(f"  {name:26s} {tag:8s} 读数{'==真值' if correct else '!=真值'} "
              f"标了{uncertain}")
    print("\n读数不等于真值的帧（含已知缺陷）：")
    for name, tag, correct, uncertain, got, gt in rows:
        if not correct:
            print(f"  {name:26s} 缺{sorted(set(gt) - set(got))} 多{sorted(set(got) - set(gt))} "
                  f"{'被标' if uncertain else '未标'} {'[已知缺陷]' if name in DEFECT else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
