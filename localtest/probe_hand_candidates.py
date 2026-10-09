# -*- coding: utf-8 -*-
"""手牌行「张数候选」成本剖析：一次切几整行、失手重扫命中几次，量出来再动刀。

动机（build/cost_split.txt 实测）：单帧均值 443.0ms 里「手牌行检测+分类」占
211.3ms（47.7%），最慢帧 s_fa87 2083.1ms。`detect_hand_strip` 的成本结构是
「每档候选切**完整一行**并全量打分」，所以真正要回答的是三个数：

  1. 一帧实际切了几整行？（平台查表分支给 2 档 → 约 26 枚）
  2. 失手重扫门（`best_standing_mean < 0.55`）在 41 帧上命中几次？命中那帧
     会再切 6 档 × 13 枚，是均值之外的尖峰来源。
  3. 两套节距模型 c_adj / c_tec 的 top-2 候选重合度有多高？重合就不该各切一行。

做法是包住 `detect_hand_strip` 与 `_classify_batch`，只记不判：每次整行提交
的枚数、候选集是否为 None（None 只出现在失手重扫里，见 1313-1316 注释），
以及节距候选留档 `last_pitch_candidates`。跑的是生产口径（tencent/sc_hz）。

用法：
  py -3.10 -X utf8 localtest/probe_hand_candidates.py
输出：build/probe_hand_candidates.txt
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from collections import Counter, defaultdict

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import engine.engine as EE  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector as DET  # noqa: E402

GT_PATH = os.path.join(HERE, "gt", "shots.json")
SHOT_DIR = os.path.join(HERE, "shots")
REAL_DIR = os.path.join(HERE, "shots_tuyou_select")
REAL_FILES = ("s1_select_cd17.jpg", "s2_select_cd07.jpg",
              "s3_select_cd01.jpg", "s4_after_swap_cd04.jpg")
OUT = os.path.join(REPO, "build", "probe_hand_candidates.txt")

# 当前这次 detect_hand_strip 内的提交记录：[(枚数, 候选集是否 None)]
SUB: list = []
ROWS: list = []          # 本帧 detect_hand_strip 的归因结果
ALL: list = []           # 全量留档（聚合口径用它，不能用 ROWS——ROWS 每帧清空）
SCORE_FACE = [0]


def load_frames():
    with open(GT_PATH, encoding="utf-8") as fp:
        gt = json.load(fp)["shots"]
    out = []
    for s in gt:
        if not s.get("verified"):
            continue
        img = cv2.imread(os.path.join(SHOT_DIR, s["file"]))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        out.append(("gt/" + s["file"], cv2.imdecode(buf, cv2.IMREAD_COLOR)))
    for f in REAL_FILES:
        img = cv2.imread(os.path.join(REAL_DIR, f))
        if img is None:
            continue
        out.append(("real/" + f, img))
    return out


def instrument():
    orig_strip = DET.detect_hand_strip
    orig_batch = DET._classify_batch
    orig_face = DET._score_face

    def strip(self, image_bgr, *a, **kw):
        SUB.clear()
        n0 = SCORE_FACE[0]
        t0 = __import__("time").perf_counter()
        dets = orig_strip(self, image_bgr, *a, **kw)
        dt = (__import__("time").perf_counter() - t0) * 1000.0
        c_adj, c_tec = getattr(self, "last_pitch_candidates", ([], []))
        runs = list(getattr(self, "last_count_runs", []))
        first = runs[0] if runs else []
        ROWS.append({
            "batches": list(SUB),
            "tiles": sum(n for n, _ in SUB),
            "rescan": any(is_none for _n, is_none in SUB),
            "branch": getattr(self, "last_layout_branch", "?"),
            "grid": getattr(self, "last_hand_grid", None),
            "n_out": len(dets),
            "c_adj": list(c_adj), "c_tec": list(c_tec),
            "overlap": bool(set(c_adj) & set(c_tec)),
            "run": first,
            "cands": [k for k, _m in first],
            "win_idx": (next((i for i, (k, _m) in enumerate(first)
                              if k == (getattr(self, "last_hand_grid", None) or (0, 0, 0))[2]), -1)),
            "margin": (first[0][1] - first[1][1]) if len(first) >= 2 else 99.0,
            "gap": ((sorted((m for _k, m in first), reverse=True)[0]
                     - sorted((m for _k, m in first), reverse=True)[1])
                    if len(first) >= 2 else 99.0),
            "ms": dt,
            "faces": SCORE_FACE[0] - n0,
            "mean_top": float(max([d[2] for d in dets], default=0.0)),
        })
        return dets

    def batch(self, crops, styles, *a, **kw):
        SUB.append((len(crops), styles is None))
        return orig_batch(self, crops, styles, *a, **kw)

    def face(self, *a, **kw):
        SCORE_FACE[0] += 1
        return orig_face(self, *a, **kw)

    DET.detect_hand_strip = strip
    DET._classify_batch = batch
    DET._score_face = face


def main() -> int:
    frames = load_frames()
    instrument()
    for name, img in frames:
        eng = EE.Engine()
        eng.set_platform("tencent")
        eng.set_mode("sc_hz")
        SUB.clear()
        del ROWS[:]
        t0 = __import__("time").perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            eng.process(img)
        total = (__import__("time").perf_counter() - t0) * 1000.0
        ALL.extend(ROWS)
        hand = [r for r in ROWS if r["branch"] != "-"]
        hms = sum(r["ms"] for r in hand)
        print("%-40s 总%7.1fms 手牌行%6.1fms 提交%s 枚%d 重扫%d 档adj%s/tec%s 出%d" % (
            name[:40], total, hms,
            ",".join(str(n) for n, _ in [r for r in hand for r in r["batches"]]),
            sum(r["tiles"] for r in hand),
            sum(1 for r in hand if r["rescan"]),
            hand[0]["c_adj"] if hand else "-", hand[0]["c_tec"] if hand else "-",
            hand[0]["n_out"] if hand else 0))

    strips = [r for r in ALL if r["branch"] != "-"]
    lines = ["帧数 %d，detect_hand_strip 调用 %d 次" % (len(frames), len(strips)), ""]
    n_b = Counter(len(r["batches"]) for r in strips)
    lines.append("每次 strip 的整行提交次数分布：%s" % dict(sorted(n_b.items())))
    lines.append("重扫命中：%d/%d 次（候选集 None 的那次提交即重扫）" % (
        sum(1 for r in strips if r["rescan"]), len(strips)))
    lines.append("每次 strip 提交的枚数分布：%s" % dict(sorted(Counter(r["tiles"] for r in strips).items())))
    lines.append("总分枚数 %d，其中重扫路占 %d（%.1f%%）" % (
        sum(r["tiles"] for r in strips),
        sum(n for r in strips for n, is_none in r["batches"] if is_none),
        100.0 * sum(n for r in strips for n, i2 in r["batches"] if i2) /
        max(sum(r["tiles"] for r in strips), 1)))
    lines.append("c_adj∩c_tec 非空：%d/%d；c_adj==c_tec：%d/%d" % (
        sum(1 for r in strips if r["overlap"]), len(strips),
        sum(1 for r in strips if r["c_adj"] == r["c_tec"]), len(strips)))
    pair = Counter(tuple(sorted(r["c_adj"])) + tuple(sorted(r["c_tec"])) for r in strips)
    lines.append("节距候选组合 top：%s" % pair.most_common(8))
    lines.append("分支：%s" % dict(Counter(r["branch"] for r in strips)))
    win = Counter(tuple(r["grid"][2:] if r["grid"] else ()) for r in strips)
    lines.append("胜出档位（张数）：%s" % win.most_common(8))
    lines.append("")
    lines.append("── 第一趟（不重扫）的候选仲裁：第二档到底赢过几次 ──")
    two = [r for r in strips if len(r["cands"]) >= 2]
    lines.append("双档 strip：%d/%d；单档（候选去重后）：%d" % (
        len(two), len(strips), len(strips) - len(two)))
    idx = Counter(r["win_idx"] for r in two)
    lines.append("胜出者在候选里的序号：%s" % dict(sorted(idx.items())))
    lines.append("胜出者不是第一档的帧：")
    for r in two:
        if r["win_idx"] != 0:
            lines.append("  %7.1fms 重扫%d cands%s run%s grid%s" % (
                r["ms"], sum(1 for _n, i2 in r["batches"] if i2), r["cands"],
                [(k, round(m, 3)) for k, m in r["run"]], r["grid"]))
    lines.append("第一档均分 - 第二档均分（有符号，负=第二档更好）："
                 "中位 %.3f 最小 %.3f 最大 %.3f" % (
                     float(np.median([r["margin"] for r in two])),
                     min(r["margin"] for r in two), max(r["margin"] for r in two)))
    for th in (0.0, 0.02, 0.05, 0.10, 0.15, 0.20):
        keep = [r for r in two if r["margin"] < th]
        lines.append("  第一档领先 < %.2f 才需继续双档：%d/%d（其中第二档真赢 %d）" % (
            th, len(keep), len(two), sum(1 for r in keep if r["win_idx"] != 0)))
    lines.append("")
    adj_win = sum(1 for r in strips if r["grid"] and set(r["grid"][2:3]) <= set(r["c_adj"]))
    lines.append("胜出张数落在 c_adj 前 2 档：%d/%d" % (adj_win, len(strips)))
    lines.append("手牌行墙钟：均值 %.1fms 中位 %.1fms 最差 %.1fms" % (
        float(np.mean([r["ms"] for r in strips])), float(np.median([r["ms"] for r in strips])),
        max(r["ms"] for r in strips)))
    lines.append("其中 _score_face 次数：均值 %.1f 最差 %d" % (
        float(np.mean([r["faces"] for r in strips])), max(r["faces"] for r in strips)))
    slow = sorted(strips, key=lambda r: -r["ms"])[:8]
    lines.append("")
    lines.append("最慢 8 次 strip（ms / 提交 / 重扫 / adj / tec / 胜出档）：")
    for r in slow:
        lines.append("  %7.1f  %s 重扫%d adj%s tec%s grid%s 枚%d 打分%d" % (
            r["ms"], r["branch"], sum(1 for _n, i2 in r["batches"] if i2),
            r["c_adj"], r["c_tec"], r["grid"], r["tiles"], r["faces"]))
    by_rescan = defaultdict(list)
    for r in strips:
        by_rescan[r["rescan"]].append(r["ms"])
    for k, v in sorted(by_rescan.items()):
        lines.append("  重扫=%s 的 strip：n=%d 均值 %.1fms" % (k, len(v), float(np.mean(v))))
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
