# -*- coding: utf-8 -*-
"""第四刀（粗筛+精算）的**上线门禁探针**：在全部 verified GT 帧的真实打分调用上，
逐次比对「现状全量扫」与「两阶段（粗筛排序 → 精算 top-K ∪ 分差带 ∪ 相邻类）」的
最终 (label, score)。

与 `probe_prefilter.py` 的分工：那份只在 130 枚手牌 face 上比 argmax，回答「粗筛
便宜不便宜、排序准不准」；本份跑在**生产真实调用序列**上（手牌行 / 牌河 / 副露 /
救援重扫都要经过 `_score_face`），并且比的是**过完 `_decide` 的最终输出**。

为什么必须过 `_decide` 而不是只看 argmax：`_decide` 会在分数表上读**相邻类**的分数
做结构裁决（2m↔3m、2s↔3s、2p↔3p、4p↔5p、6p↔8p、6s↔9s、4s↔5s 共 7 条），粗筛只留
前 K 名会把裁决的另一方挤掉，`scores.get("3m", 0.0)` 于是读成 0、整条判决被跳过。
所以精算集必须对「相邻类」做闭包（`ADJACENT_PAIRS`）。

为什么还要看 argmax 召回而不只看最终一致（这是本探针唯一真正的判据）：
  只要**全量扫的 argmax 标签**落进了精算集，它的分数就是精确全尺寸分，于是
  ①精算集内的 argmax 与全量 argmax 同一个、同一个分（集外标签的全量分不可能更高，
  否则它就才是 argmax）；②7 条结构判决的输入分都精确（相邻类已闭包）⇒
  **最终 (label, score) 由构造保证与全量一致**。
  反过来「最终一致 100%」但 argmax 召回 <100% 就是**靠运气**（被挤掉的类恰好被结构
  判决从隔壁捞回来），那种绿不许上线。所以本探针同时量两列，选 combo 的条件是
  argmax 召回 = 100%，最终一致必须 = 100%（否则代码与推理有一处是假的）。

运行：py -3.10 -X utf8 localtest/probe_prefilter_gt.py
输出：build/prefilter_gt.txt
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from collections import Counter

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "prefilter_gt.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

import engine.engine as EE  # noqa: E402
from recognition.tencent_grid_detector import (  # noqa: E402
    TencentGridDetector, resolve_candidate_tiles)

SCALE = 0.5
# (K, Δ)：Δ=0 表示纯 top-K；Δ>0 表示「与粗筛第一名分差 < Δ 的标签全部进精算集」，
# 这条带在粗筛排序本来就分不清（低置信/遮挡/陌生牌风）时会自动把集合撑大，等于
# 自我退化回全量扫 —— 比另起一个"最高分低于 X 就回退"的魔法阈值诚实。
COMBOS = [(2, 0.0), (2, 0.06), (3, 0.0), (3, 0.04), (3, 0.06),
          (5, 0.0), (5, 0.04), (8, 0.0)]

# `_decide` 里成对出现的形近类：任一方进精算集就必须把另一方也拉进来。
ADJACENT_PAIRS = (
    ("2m", "3m"), ("2s", "3s"), ("2p", "3p"), ("4p", "5p"),
    ("6p", "8p"), ("6s", "9s"), ("4s", "5s"),
)
PARTNER: dict = {}
for _a, _b in ADJACENT_PAIRS:
    PARTNER.setdefault(_a, set()).add(_b)
    PARTNER.setdefault(_b, set()).add(_a)


def _half(k):
    if k is None:
        return None
    kh, kw = k.shape[:2]
    return cv2.resize(k, (max(1, int(kw * SCALE)), max(1, int(kh * SCALE))),
                      interpolation=cv2.INTER_AREA)


def prep(face, scale):
    """复刻 `_score_face` 前置（灰/彩、顶标、打分窗口），末尾按 scale 缩图。

    scale=1.0 就是生产的那张打分图，scale=0.5 是粗筛图：两者共用同一段前置，探针
    才敢声称「除了核的尺寸，别的一个字没改」。灰分支必须**先 normalize 再缩**。"""
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    is_grey = float(np.mean(hsv[:, :, 1])) < 35
    has_btn = bool(np.sum((hsv[:40, :, 0] >= 15) & (hsv[:40, :, 0] <= 35)
                          & (hsv[:40, :, 1] > 100)) > 80)
    c_face = face[(38 if has_btn else 10):110, 6:74]
    img = (cv2.normalize(cv2.cvtColor(c_face, cv2.COLOR_BGR2GRAY), None, 0, 255,
                         cv2.NORM_MINMAX) if is_grey else c_face)
    if scale != 1.0:
        ih, iw = img.shape[:2]
        img = cv2.resize(img, (max(1, int(iw * scale)), max(1, int(ih * scale))),
                         interpolation=cv2.INTER_AREA)
    return img, is_grey, has_btn


def core_of(entry, is_grey, has_btn):
    _lbl, _style, core_btn, core_plain, gcore_btn, gcore_plain = entry
    if is_grey:
        return gcore_btn if has_btn else gcore_plain
    return core_btn if has_btn else core_plain


def scan(img, entries, is_grey, has_btn):
    """label -> 最高分（与生产同口径：同标签多变体取 max）。"""
    scores: dict = {}
    for e in entries:
        k = core_of(e, is_grey, has_btn)
        if k is None or k.shape[0] > img.shape[0] or k.shape[1] > img.shape[1]:
            continue
        s = float(cv2.matchTemplate(img, k, cv2.TM_CCOEFF_NORMED).max())
        if s > scores.get(e[0], 0.0):
            scores[e[0]] = s
    return scores


def refine(ranked, k, margin, coarse):
    """精算集标签：粗筛前 K ∪ 与第一名分差 < margin 的带 ∪ 它们的相邻类。"""
    top = {l for l, _ in ranked[:k]}
    if margin > 0.0 and ranked:
        lead = ranked[0][1]
        top |= {l for l, s in coarse.items() if s > lead - margin}
    out = set(top)
    for l in top:
        out |= PARTNER.get(l, set())
    return out & set(coarse.keys())


def main() -> int:
    if not os.path.exists(GT_PATH):
        with open(OUT, "w", encoding="utf-8") as fp:
            fp.write("GT 不存在\n")
        return 1
    with open(GT_PATH, encoding="utf-8") as fp:
        gt = [e for e in json.load(fp)["shots"] if e.get("verified")]

    recorded: list = []
    orig = TencentGridDetector._score_face

    def spy(self, face, avail=None, styles=None):
        # 全量结果由生产本体算：探针不许"自己复刻一遍再自称等价"
        res_face, res_scores, res_valid = orig(self, face, avail, styles)
        recorded.append((self, face, avail, styles, res_scores))
        return res_face, res_scores, res_valid

    TencentGridDetector._score_face = spy
    try:
        for e in gt:
            img = cv2.imread(os.path.join(SHOT_DIR, e["file"]))
            if img is None:
                continue
            with contextlib.redirect_stdout(io.StringIO()):
                EE.Engine().process(img)
    finally:
        TencentGridDetector._score_face = orig

    if not recorded:
        with open(OUT, "w", encoding="utf-8") as fp:
            fp.write("没录到任何打分调用\n")
        return 1

    n = len(recorded)
    agree = Counter()        # (K,Δ) -> 最终 (label,score) 全等次数
    amx = Counter()          # (K,Δ) -> 全量 argmax 标签落进精算集的次数
    n_lbl = Counter()        # (K,Δ) -> 精算集标签数累加
    n_core = Counter()       # (K,Δ) -> 精算集核数累加
    miss = []                # 不一致明细
    amx_miss = []            # argmax 未召回明细
    cores_per_call = Counter()
    t_full, t_two = [], {c: [] for c in COMBOS}
    coarse_top = []          # 每次调用的粗筛最高分（拿来看低分区占比）
    t_coarse = []            # 每次调用的粗筛耗时

    for det, face, avail, styles, full_scores in recorded:
        st = det._resolve_styles(styles)
        valid = resolve_candidate_tiles(avail, det._mode_tiles, det.full_honors)
        entries = [c for c in det._cores
                   if c[0] in valid and (st is None or c[1] in st)]
        cores_per_call[len(entries)] += 1
        if not full_scores:
            continue
        best_full_lbl, best_full_sc = det._decide(face, full_scores)
        amx_lbl = max(full_scores, key=full_scores.get)

        ts = time.perf_counter()
        orig(det, face, avail, styles)          # 全量单价（含前置），对照基准
        t_full.append((time.perf_counter() - ts) * 1000.0)

        img_coarse, is_grey, has_btn = prep(face, SCALE)
        img_full, _, _ = prep(face, 1.0)
        coarse_entries = [(l, s, _half(cb), _half(cp), _half(gb), _half(gp))
                          for (l, s, cb, cp, gb, gp) in entries]
        ts = time.perf_counter()
        coarse = scan(img_coarse, coarse_entries, is_grey, has_btn)
        c_ms = (time.perf_counter() - ts) * 1000.0
        t_coarse.append(c_ms)
        if not coarse:
            continue
        coarse_top.append(max(coarse.values()))
        ranked = sorted(coarse.items(), key=lambda kv: -kv[1])
        for combo in COMBOS:
            k, mg = combo
            keep = refine(ranked, k, mg, coarse)
            picked = [c for c in entries if c[0] in keep]
            n_lbl[combo] += len(keep)
            n_core[combo] += len(picked)
            amx[combo] += int(amx_lbl in keep)
            if amx_lbl not in keep:
                amx_miss.append((combo, amx_lbl, round(full_scores[amx_lbl], 3),
                                 round(coarse.get(amx_lbl, -1.0), 3),
                                 round(ranked[0][1], 3)))
            ts = time.perf_counter()
            refined = scan(img_full, picked, is_grey, has_btn)
            lbl, sc = det._decide(face, refined) if refined else (None, 0.0)
            t_two[combo].append((time.perf_counter() - ts) * 1000.0 + c_ms)
            if lbl != best_full_lbl or sc != best_full_sc:
                miss.append((combo, amx_lbl, best_full_lbl, best_full_sc, lbl, sc))
            else:
                agree[combo] += 1

    lines = ["真实调用序列上的打分次数：%d（verified 帧 %d）" % (n, len(gt)),
             "每次参与扫描的核数：%s" % cores_per_call.most_common(4),
             "粗筛最高分：均值 %.3f｜<0.35 占比 %.1f%%" % (
                 float(np.mean(coarse_top)) if coarse_top else 0.0,
                 100.0 * sum(1 for s in coarse_top if s < 0.35) / max(1, len(coarse_top))),
             "全量扫（生产本体，含前置）单枚均时：%.2fms" % (sum(t_full) / len(t_full))]
    lines.append("")
    lines.append("K  Δ     精算标签 精算核  精算/枚  两阶段/枚  省时     "
                 "argmax召回   最终(label,score)全等")
    base = sum(t_full) / len(t_full)
    coarse_ms_avg = (sum(t_coarse) / len(t_coarse)) if t_coarse else 0.0
    lines.append("粗筛单枚：%.2fms（全量的 %.1f%%）" % (
        coarse_ms_avg, coarse_ms_avg / base * 100))
    for combo in COMBOS:
        avg = sum(t_two[combo]) / max(1, len(t_two[combo]))
        lines.append("%d  %-5.2f %6.1f %7.1f %8.2fms %9.2fms %7.1f%%  %7.2f%%(%3d)  %7.2f%%(%d/%d)" % (
            combo[0], combo[1], n_lbl[combo] / n, n_core[combo] / n,
            avg - coarse_ms_avg, avg, (1 - avg / base) * 100,
            amx[combo] / n * 100, amx[combo],
            agree[combo] / n * 100, agree[combo], n))
    lines.append("")
    lines.append("argmax 未召回明细（前 15）：combo, argmax 标签, 全量分, 粗筛分, 粗筛第一名分")
    for m in amx_miss[:15]:
        lines.append("  %s" % (m,))
    lines.append("最终不一致明细（前 15）：combo, argmax, 全量(label,score), 两阶段(label,score)")
    for m in miss[:15]:
        lines.append("  %s" % (m,))
    lines.append("")
    lines.append("上线判据：argmax 召回 = 100% 且 最终全等 = 100% 的 combo 才可上线；")
    lines.append("  在其中选「两阶段/枚」最小的那个。任一列不是 100% 就加大 K/Δ 或放弃。")
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
