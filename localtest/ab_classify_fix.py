# -*- coding: utf-8 -*-
"""A/B：YOLO 通道 honor classify=False 的前后对比（同一进程内交替跑）。

为什么必须同进程：CHANGELOG 记下过一次测量可信度事故 —— 同配置重复压测的 p50
给出 280.7ms 和 646.8ms，跨进程比 p50 的结论一律作废。所以这里按
old → new → old 三个 pass 跑同一条 91 帧流，最后一趟 old 用来给「本轮 p50 本身
的抖动」定幅度，否则降了多少说不清。

两条断言：
1) 保真：每帧引擎产出的 hand/count/status/drawing_tile 必须逐字相同 —— 这次改动
   只该让牌桌实证少花钱，不该让面板少一张牌。
2) 省时：牌桌实证（_verify_hand_evidence）的每帧开销与帧总耗时。

用法：py -3.10 -X utf8 localtest/ab_classify_fix.py [--limit N]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import engine.engine as E  # noqa: E402
from engine.engine import Engine  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402

from diag_live import (DEFAULT_GT, codes_of, gt_known, load_shots, matches,  # noqa: E402
                       pct, quiet)

FIELDS = ("hand", "count", "status", "drawing_tile", "phase_label")


def make_feed(platform_holder):
    """喂一帧并返回 (ms, payload dict, phases)。平台/玩法由调用方固定。"""
    def go(eng, img, platform, mode):
        orig_lp, orig_lm = E.load_platform, E.load_mode
        E.load_platform = lambda *a, **k: platform
        E.load_mode = lambda *a, **k: mode
        t0 = __import__("time").perf_counter()
        try:
            res = quiet(eng.process, img)
        finally:
            E.load_platform, E.load_mode = orig_lp, orig_lm
        ms = (__import__("time").perf_counter() - t0) * 1000.0
        d = json.loads(res.result) if res is not None else {}
        return ms, d, {}
    return go


feed = make_feed(None)


def force_old_behavior(on):
    """把 YOLO 的 detect_all_rows 在「旧行为（无视 classify）」与现行之间切换。

    必须始终从 ORIG_DAR 出发重新包：上一 pass 的补丁若被当成原函数再包一层，
    就会把新行为又顶回去，A/B 两边量的其实都是旧行为。
    """
    if on:
        def patched(self, image, classify=True, *a, **k):
            return ORIG_DAR(self, image, classify=True, *a, **k)
        YOLODetector.detect_all_rows = patched
    else:
        YOLODetector.detect_all_rows = ORIG_DAR


def run_pass(shots, old_behavior, plat_of, mode_of):
    """按 GT 顺序连续喂同一条流（新建引擎），返回逐帧记录。"""
    force_old_behavior(old_behavior)
    eng = Engine()
    quiet(eng.reset_match)
    recs = []
    for e in shots:
        img = cv2.imread(os.path.join(REPO, e["src"], e["file"]))
        if img is None:
            continue
        plat, mode = plat_of(e), mode_of(e)
        ms, d, _ph = feed(eng, img, plat, mode)
        recs.append({"frame": e["frame"], "style": e["style"], "plat": plat,
                     "ms": ms, "payload": {k: d.get(k) for k in FIELDS}})
    return recs


ORIG_DAR = YOLODetector.detect_all_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=DEFAULT_GT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(REPO, "build", "ab_classify.txt"))
    a = ap.parse_args()

    shots = load_shots(a.gt)
    if a.limit:
        shots = shots[:a.limit]

    from eval_new_material import STYLE_OF  # noqa: E402
    from layer_cost import mode_for  # noqa: E402
    plat_mode = {}

    def plat_of(e):
        return STYLE_OF.get(e["style"], "tencent")

    def mode_of(e):
        p = plat_of(e)
        if p not in plat_mode:
            plat_mode[p] = mode_for(p, sorted(set(e["hand"])))[0]
        return plat_mode[p]

    out = []

    def say(s=""):
        out.append(s)
        print(s)

    say(f"A/B 口径：classify=False 是否真跳过模板分类 | {len(shots)} 帧 × 3 pass(old,new,old)")
    old1 = run_pass(shots, True, plat_of, mode_of)
    new1 = run_pass(shots, False, plat_of, mode_of)
    old2 = run_pass(shots, True, plat_of, mode_of)

    # ===== 保真：逐帧 payload 必须完全一致 =====
    diffs = []
    for r_old, r_new in zip(old1, new1):
        if r_old["payload"] != r_new["payload"]:
            diffs.append((r_old["frame"], r_old["plat"], r_old["payload"], r_new["payload"]))
    say("")
    say("== 保真（引擎产出逐字段对比）==")
    say(f"  比对口径：{', '.join(FIELDS)}")
    say(f"  不一致帧数：{len(diffs)}/{len(new1)}")
    for f, p, o, n in diffs[:12]:
        say(f"    {p}#{f}  old={o}")
        say(f"    {' ' * len(str(p))} #{' ' * len(str(f))}  new={n}")

    # ===== 真值符合度（与本次改动无关，但要确认没变差）=====
    def acc(recs):
        bad = 0
        for r, e in zip(recs, shots):
            want, unk = gt_known(e["hand"])
            got = sorted(codes_of((r["payload"].get("hand") or "")))
            if not matches(got, want, unk):
                bad += 1
        return bad

    say("")
    say("== 对 GT 的符合度（三趟应同向，用来看改动有没有把精度带走）==")
    say(f"  old#1 不符 {acc(old1)}   new 不符 {acc(new1)}   old#2 不符 {acc(old2)}")

    # ===== 耗时：分平台 p50/p95 =====
    def group(recs):
        g = collections.defaultdict(list)
        for r in recs:
            g[r["plat"]].append(r["ms"])
        return g

    go1, gn1, go2 = group(old1), group(new1), group(old2)
    say("")
    say("== 每帧总耗时（ms，Python 侧；同一进程内 old→new→old）==")
    say(f"  {'平台':<13}{'帧':>3}  {'old#1':>18}  {'new':>18}  {'old#2':>18}  降幅")
    for p in sorted(go1):
        def fmt(v):
            return f"p50={pct(v,50):7.1f} p95={pct(v,95):7.1f}"
        d = (1.0 - pct(gn1[p], 50) / max(1e-9, pct(go1[p], 50))) * 100.0
        say(f"  {p:<13}{len(go1[p]):>3}  {fmt(go1[p]):>18}  {fmt(gn1[p]):>18}  "
            f"{fmt(go2[p]):>18}  {d:5.1f}%")
    all_old = [r["ms"] for r in old1]
    all_new = [r["ms"] for r in new1]
    all_old2 = [r["ms"] for r in old2]
    drift = abs(pct(all_old2, 50) - pct(all_old, 50))
    say("")
    say(f"  全流 p50：old#1={pct(all_old,50):.1f}  new={pct(all_new,50):.1f}  "
        f"old#2={pct(all_old2,50):.1f}")
    say(f"  同进程内 old 自身漂移 |old#2-old#1|={drift:.1f}ms —— 降幅小于这个数就不算结论")
    say(f"  p95：old#1={pct(all_old,95):.1f}  new={pct(all_new,95):.1f}  old#2={pct(all_old2,95):.1f}")

    with open(a.out, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out) + "\n")
    print(f"[written {os.path.relpath(a.out, REPO)}]")


if __name__ == "__main__":
    main()
