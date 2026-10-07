# -*- coding: utf-8 -*-
"""风格探针为什么选错 bank：把探针的"分数矩阵"摊开，离线比候选裁决规则。

已知事实（localtest/probe_route_eval.py 实测）：路由准确率只有 23/68，
而 `ab_bank_impact.py` 证明选错 bank 会把旧平台从 100% 拖到 86%。
现在的规则是「一枚代表牌 × 全部模板取最高分，胜出风格 ≥0.60 就定路由」——
单枚牌、单点阈值，跨平台相似牌风一撞就翻车。

本脚本不改引擎，只把同一枚代表牌在**每个风格内的最高分**摊成一行，
然后用几种裁决规则离线重放，看哪种能把路由准确率拉起来、代价是拒识多少：

  R0 现状        argmax(风格分) 且 >=0.60
  R1 加领先差    还要比次优风格高出 M（M=0.02/0.05/0.10）
  R2 多枚投票    手牌行取 K 枚各自 argmax，票数 >=3 才定
  R3 均值裁决    K 枚的风格分取平均后 argmax >=0.60

拒识（None）不是失败：它会退到跨全库扫描，慢但不会拿别家字模硬认。
所以评价标准是「命中率」与「错路由数」同时看——只提命中率、把不确定全推给
兜底重扫，等于把耗时问题换个地方藏起来。

    py -3.10 -X utf8 localtest/probe_style_rules.py
输出：build/probe_style_rules.txt
"""
import collections
import json
import os
import statistics
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

STYLES = ["tencent", "shushan", "queshen", "tuyou", "jj", "weile"]
K_CROPS = 5
GATE = 0.60                      # 生产探针门槛
OUT = os.path.join(REPO, "build", "probe_style_rules.txt")


def load_frames():
    """[(真值风格, 显示名, 绝对路径)]：帧集目录名/新素材 GT 即平台真值。"""
    out = []
    for d, truth in (("shots", "tencent"), ("shots_shushan", "shushan"),
                     ("shots_tuyou", "tuyou")):
        folder = os.path.join(HERE, d)
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith((".jpg", ".png")):
                out.append((truth, f"{d}/{f}", os.path.join(folder, f)))
    gt = os.path.join(HERE, "gt", "new_shots.json")
    if os.path.exists(gt):
        with open(gt, encoding="utf-8") as fp:
            for e in json.load(fp)["shots"]:
                p = os.path.join(REPO, "public", "0", e["file"])
                if os.path.exists(p):
                    out.append((e["style"], f"new/{e['file']}", p))
    return out


def style_scores(det, crop):
    """一枚裁片在每个风格内的最高分（用生产 classify_tile，不复制预处理）。"""
    return {s: float(det.classify_tile(crop, styles={s})[1]) for s in STYLES}


def argmax(scores):
    return max(scores, key=scores.get)


def pick_crops(img, dets, probe_crop):
    """代表牌集合：生产探针那一枚 + 手牌行里均匀取的几枚。"""
    crops = [probe_crop] if probe_crop is not None else []
    ds = sorted(dets, key=lambda d: d[0][0])
    if ds:
        step = max(1, len(ds) // K_CROPS)
        for _r, _l, _s in ds[::step][:K_CROPS]:
            x, y, w, h = _r
            crops.append(img[y:y + h, x:x + w])
    return [c for c in crops if c is not None and c.size]


def main():
    det = TencentGridDetector()
    frames = load_frames()
    captured = {}

    _probe = det._probe_style

    def probe_rec(crop, extra=None):
        captured["crop"] = crop
        captured["extra"] = [x for x in (extra or []) if x is not None and x.size]
        return _probe(crop, extra)

    det._probe_style = probe_rec

    rows = []
    for truth, name, path in frames:
        img = cv2.imread(path)
        if img is None:
            continue
        captured.clear()
        dets = det.detect_hand_strip(img) or []
        if not dets:
            continue
        crops = pick_crops(img, dets, captured.get("crop"))
        if not crops:
            continue
        prod = [crops[0]] + list(captured.get("extra") or [])   # 线上实现实际送探针的那几枚
        mats = [style_scores(det, c) for c in crops]
        rows.append((truth, name, mats, [style_scores(det, c) for c in prod]))
        print(f"  {name[:34]:34} 代表牌 {len(mats)} 枚", file=sys.stderr)

    def grade(rule, key=2):
        hit = wrong = refuse = 0
        for row in rows:
            got = rule(row[key])
            if got is None:
                refuse += 1
            elif got == row[0]:
                hit += 1
            else:
                wrong += 1
        return hit, wrong, refuse

    def r_single(mats, margin=0.0):
        """只用第一枚（就是现在线上那一枚代表牌）：margin=0 即 R0 现状。"""
        s = mats[0]
        order = sorted(s.values(), reverse=True)
        b = argmax(s)
        if s[b] < GATE:
            return None
        return b if order[0] - order[1] >= margin else None

    def r_vote(mats, min_votes=3):
        votes = collections.Counter(argmax(s) for s in mats if max(s.values()) >= GATE)
        if not votes:
            return None
        b, n = votes.most_common(1)[0]
        return b if n >= min_votes else None

    def r_mean(mats, gate=GATE):
        mean = {s: statistics.fmean(m[s] for m in mats) for s in STYLES}
        b = argmax(mean)
        return b if mean[b] >= gate else None

    rules = [("R0 现状（单枚 + 0.60）", lambda m: r_single(m)),
             ("R1 单枚+领先差 0.02", lambda m: r_single(m, 0.02)),
             ("R1 单枚+领先差 0.05", lambda m: r_single(m, 0.05)),
             ("R1 单枚+领先差 0.10", lambda m: r_single(m, 0.10)),
             ("R2 多枚投票>=3 票", lambda m: r_vote(m, 3)),
             ("R2 多枚投票>=2 票", lambda m: r_vote(m, 2)),
             ("R3 多枚均值 0.60", lambda m: r_mean(m, 0.60)),
             ("R3 多枚均值 0.70", lambda m: r_mean(m, 0.70)),
             ("R3 多枚均值 0.80", lambda m: r_mean(m, 0.80))]

    lines = [f"可测帧 {len(rows)}（含手牌行且能取到代表牌）", "",
             "列说明：离线 = 中心一枚 + 检出牌 5 枚（能拿到的上限）；"
             "线上 = 中心一枚 + 节距切出的额外代表牌（实际送探针的那几枚）", "",
             f"{'规则':22} {'离线命中':>7} {'离线错':>5} {'离线拒':>5} | "
             f"{'线上命中':>7} {'线上错':>5} {'线上拒':>5}"]
    for title, fn in rules:
        oh, ow, orf = grade(fn, key=2)          # 离线牌集
        ph, pw, prf = grade(fn, key=3)          # 线上实际送的牌集
        lines.append(f"{title:22} {oh:>7} {ow:>5} {orf:>5} | {ph:>7} {pw:>5} {prf:>5}")

    lines.append("")
    lines.append("线上实现选的是 R3 多枚均值 0.60：")
    ph, pw, prf = grade(lambda m: r_mean(m, 0.60), key=3)
    lines.append(f"  命中 {ph}，错路由 {pw}，拒识 {prf}，"
                 f"命中率 {100.0 * ph / max(1, ph + pw + prf):.1f}%"
                 f"（现状单枚为 {100.0 * grade(lambda m: r_single(m), key=2)[0] / max(1, len(rows)):.1f}%）")

    lines.append("")
    lines.append("按真值平台看错法（离线牌集）：")
    per = collections.defaultdict(lambda: [0, 0, 0])
    for truth, _n, mats, _p in rows:
        got = r_single(mats)
        if got is None:
            per[truth][2] += 1
        elif got == truth:
            per[truth][0] += 1
        else:
            per[truth][1] += 1
    for t, (h, w, rf) in sorted(per.items()):
        lines.append(f"  {t:9} 命中{h:>3} 错向别家{w:>3} 拒识{rf:>3}")

    lines.append("")
    lines.append("错路由明细（R0 单枚判成了谁）：")
    for truth, name, mats, _p in rows:
        got = r_single(mats)
        if got is not None and got != truth:
            s = mats[0]
            lines.append(f"  {truth:9} -> {got:9} {name[:30]:30} "
                         + " ".join(f"{k}={s[k]:.2f}" for k in sorted(s, key=s.get, reverse=True)[:3]))
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
