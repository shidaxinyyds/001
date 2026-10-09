# -*- coding: utf-8 -*-
"""延迟第四刀的可行性实验（纯探针，不改生产）：粗筛 + 精算能不能把「110 核全扫」压成
「110 次便宜的 + K 次贵的」，以及**压完是不是还认得对**。

依据（本段实测，`build/probe_core_scan.txt`）：
  核库 594 核 / 34 标签 / 7 风格；**活跃风格数 = 1.0/枚**（腾讯帧只扫 tencent 那 121 核），
  每枚实际扫描核数 110（候选集 + 风格已砍掉 81.5%），单枚打分 10.24ms ⇒ 每核 ≈0.093ms。
  再想快只剩两条路：少扫几核，或每核便宜。直接少扫会伤精度，所以做成两阶段：
    ① 粗筛：face 与核**一起**缩一半（像素 1/4），对全部核跑一次便宜的 NCC 排序；
    ② 精算：只对粗筛前 K 名跑原来的全尺寸 NCC，最终标签从这 K 个里出。
  为什么必须一起缩：`matchTemplate` 的钱 ≈ 输出像素 × 模板像素。只缩模板不缩图，
  输出反而变大（实测 17×13 → 9×7 的输出是变小，但若图不变而模板减半，输出从
  17×13 涨到 61×57），便宜不了多少 —— 探针按「图与核同比例」算才是真的省。

风险只有一个：真答案被粗筛挤出前 K。所以逐枚核对两件事：
  A「全量扫的最优 == 两阶段的最优」（最终一致率）
  B「全量最优标签落在粗筛前 K 里」（召回率）
A 是 100% 才叫无损；只有 B 是 100% 而 A 不是，说明损失在粗筛排序，得加 K 或换更强的
粗描述子，而不是硬上线。

运行：py -3.10 -X utf8 localtest/probe_prefilter.py
输出：build/probe_prefilter.txt
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "probe_prefilter.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

import engine.engine as EE  # noqa: E402
from recognition.tencent_grid_detector import resolve_candidate_tiles  # noqa: E402

SCALE = 0.5
TOP_KS = (3, 5, 8, 12)


def prep(face, scale):
    """复刻 `_score_face` 的前置：灰/彩分支、顶标分支、打分窗口，最后按 scale 缩图。"""
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    is_grey = float(hsv[:, :, 1].mean()) < 35
    has_btn = bool(((hsv[:40, :, 0] >= 15) & (hsv[:40, :, 0] <= 35)
                    & (hsv[:40, :, 1] > 100)).sum() > 80)
    c_face = face[(38 if has_btn else 10):110, 6:74]
    if is_grey:
        img = cv2.normalize(cv2.cvtColor(c_face, cv2.COLOR_BGR2GRAY),
                            None, 0, 255, cv2.NORM_MINMAX)
    else:
        img = c_face
    if scale != 1.0:
        ih, iw = img.shape[:2]
        img = cv2.resize(img, (max(1, int(iw * scale)), max(1, int(ih * scale))),
                         interpolation=cv2.INTER_AREA)
    return img, is_grey, has_btn


def core_of(entry, is_grey, has_btn):
    lbl, style, core_btn, core_plain, gcore_btn, gcore_plain = entry
    if is_grey:
        return gcore_btn if has_btn else gcore_plain
    return core_btn if has_btn else core_plain


def scan(img, cores, is_grey, has_btn):
    scores = {}
    for e in cores:
        k = core_of(e, is_grey, has_btn)
        if k is None or k.shape[0] > img.shape[0] or k.shape[1] > img.shape[1]:
            continue
        s = float(cv2.matchTemplate(img, k, cv2.TM_CCOEFF_NORMED).max())
        if s > scores.get(e[0], 0.0):
            scores[e[0]] = s
    return scores


def main() -> int:
    eng = EE.Engine()
    eng.set_platform("tencent")
    eng.set_mode("sc_hz")
    det = eng.get_hand_detector() or eng.get_detector()
    if det is None:
        with open(OUT, "w", encoding="utf-8") as fp:
            fp.write("拿不到检测器\n")
        return 1

    valid = resolve_candidate_tiles(None, det._mode_tiles, det.full_honors)
    styles = det._resolve_styles(None)
    scan_set = [c for c in det._cores
                if c[0] in valid and (styles is None or c[1] in styles)]

    t0 = time.perf_counter()
    coarse_set = []
    for lbl, style, cb, cp, gb, gp in scan_set:
        def half(k):
            if k is None:
                return None
            kh, kw = k.shape[:2]
            return cv2.resize(k, (max(1, int(kw * SCALE)), max(1, int(kh * SCALE))),
                              interpolation=cv2.INTER_AREA)
        coarse_set.append((lbl, style, half(cb), half(cp), half(gb), half(gp)))
    build_ms = (time.perf_counter() - t0) * 1000.0

    faces = []
    with open(GT_PATH, encoding="utf-8") as fp:
        gt = json.load(fp)["shots"]
    for s in gt:
        if not s.get("verified"):
            continue
        img = cv2.imread(os.path.join(SHOT_DIR, s["file"]))
        if img is None:
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            res = eng.process(img)
        try:
            tiles = json.loads(res.result).get("tiles") or []
        except Exception:
            tiles = []
        for t in tiles:
            x, y, w, h = int(t[0]), int(t[1]), int(t[2]), int(t[3])
            crop = img[y:y + h, x:x + w]
            if crop.size:
                faces.append(det.extract_face(crop))
        if len(faces) >= 120:
            break
    if not faces:
        with open(OUT, "w", encoding="utf-8") as fp:
            fp.write("拿不到 face\n")
        return 1

    agree = Counter()
    recall = Counter()
    t_full, t_coarse, t_two = [], [], {k: [] for k in TOP_KS}
    mismatch = Counter()
    for f in faces:
        img, is_grey, has_btn = prep(f, 1.0)
        cimg, _, _ = prep(f, SCALE)
        ts = time.perf_counter()
        full = scan(img, scan_set, is_grey, has_btn)
        t_full.append((time.perf_counter() - ts) * 1000.0)
        best_full = max(full, key=full.get) if full else None

        ts = time.perf_counter()
        coarse = scan(cimg, coarse_set, is_grey, has_btn)
        t_coarse.append((time.perf_counter() - ts) * 1000.0)
        ranked = sorted(coarse.items(), key=lambda kv: -kv[1])

        for k in TOP_KS:
            top_lbls = {lbl for lbl, _ in ranked[:k]}
            recall[k] += int(best_full in top_lbls)
            picked = [c for c in scan_set if c[0] in top_lbls]
            ts = time.perf_counter()
            refined = scan(img, picked, is_grey, has_btn)
            best_two = max(refined, key=refined.get) if refined else None
            t_two[k].append(t_coarse[-1] + (time.perf_counter() - ts) * 1000.0)
            if best_two != best_full:
                mismatch[k] += 1

    n = len(faces)
    full_avg = sum(t_full) / n
    coarse_avg = sum(t_coarse) / n
    lines = [
        "样本：%d 枚 face；扫描集 %d 核（库内 %d，活跃风格 %s）" % (
            n, len(scan_set), len(det._cores),
            ",".join(sorted(styles or [])) or "全部"),
        "粗筛核预构建：%.1fms（%d 核，一次性）｜粗筛单枚：%.3fms（全量的 %.1f%%）" % (
            build_ms, len(coarse_set), coarse_avg, coarse_avg / full_avg * 100),
        "全量扫（现状单价）：%.2fms/枚" % full_avg,
        "",
        "K   两阶段/枚   省时      最终一致     粗筛召回(真答案进前K)",
    ]
    for k in TOP_KS:
        avg = sum(t_two[k]) / n
        lines.append("%-3d %8.2fms %8.1f%%  %6.2f%%(%d)  %6.2f%%(%d)" % (
            k, avg, (1 - avg / full_avg) * 100,
            (n - mismatch[k]) / n * 100, n - mismatch[k],
            recall[k] / n * 100, recall[k]))
    lines += [
        "",
        "折算上限：手牌行 94.1ms/帧（`build/cost_split_warm.txt`）里打分是主项，",
        "  按上表省时百分比线性折到端到端 187.1ms/帧 就是这一刀能拿到的量级。",
        "判据：只有「最终一致 100%」的 K 才能上线；上线后仍必须复跑 eval_base "
        "（手牌 37/37、逐张 444/444）与全量守卫。",
    ]
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
