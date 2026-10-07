# -*- coding: utf-8 -*-
"""按人工核对的逐帧 GT 割「真机牌面样本」，喂给风格模板库。

沿用 harvest_shushan.py 的范式（人眼定标 → 按 rect 裁原始分辨率），
但 rect 不再手抄：直接取引擎检出的手牌框——本次校准已证明**定位是对的，
错的只是 label**（41 帧里 count 与物理张数一致、框能圈住牌），
所以用引擎的框 + 人眼的 label 才是省力的正解。

牌面先经 face_align（象牙白掩码外接框）抠紧，避免把相邻牌的边带进来。

GT 里写 `?` 表示“这一格人眼读不准”（被相邻牌/手指/UI 遮挡，或条子根数数不
确定）：它仍然占位，用来保证整帧对齐，但不会落盘——模板库被半张牌污染
的代价远大于少一个样本。

用法: py -3.10 localtest\harvest_style_tiles.py
输出: localtest/tiles/<style>/f<帧>_<列>_<label>.png  + 覆盖度报告
"""
import os
import sys
from itertools import combinations

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from montage_for_gt import hand_tiles  # noqa: E402
from style_harvest import face_align  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

from ab_pitch_classify import GT as GT_BY_FRAME  # noqa: E402

# 收割清单：(平台 key, 风格名, 参与收割的帧号)。
# GT **一律从 ab_pitch_classify.GT 取**，不在这里维护第二份：上一轮就是因为
# 两边各写一份、评分侧已改成物理 14 张而收割侧还是旧的 12 张，差点把错标签
# 按错位的列序烧进模板库。
JOBS = [
    # 雀神：19/21 存在列序歧义，暂不参与收割。
    ("gd_queshen", "queshen", [14, 15, 16, 17, 18]),
    # 途游：万子用大写数字（伍萬/陸萬）YOLO 不认，条子数根失稳。
    ("tuyou", "tuyou", [26, 33, 34, 35, 36, 38, 39, 40]),
    # JJ：23/24 是换三张界面（牌面带 3D 透视且裁片混入邻牌），排除。
    ("jj", "jj", [28, 29, 30, 31, 32]),
    # 微乐：牌风完全不在任何已有 bank 里。首批只收 4 帧时共 49 张 / 28 类
    # ≈ 每类 1.75 张，数根型条子靠这个量根本分不出根数（实测条子几乎每帧都错），
    # 所以把有 GT 的对局帧全部纳入收割。
    ("weile", "weile", [1, 2, 3, 5, 6, 7, 8, 10]),
]


def _align_by_step(ts, n_gt):
    """用中心步进空洞还原物理列序（只能发现槽**之间**的漏检）。"""
    cx = [t[0] + t[2] / 2.0 for t in ts]
    if len(cx) < 3:
        return list(ts) if len(ts) == n_gt else None
    steps = sorted(cx[i + 1] - cx[i] for i in range(len(cx) - 1))
    pitch = steps[len(steps) // 2]
    if pitch <= 0:
        return None
    seq = []
    for i, t in enumerate(ts):
        seq.append(t)
        if i + 1 >= len(cx):
            continue
        step = cx[i + 1] - cx[i]
        k = int(round(step / pitch)) - 1
        if k >= 1 and step >= 1.45 * pitch:
            seq.extend([None] * k)
    return seq if len(seq) == n_gt else None


def _align_by_agreement(ts, labs):
    """步进法失效时，穷举漏检位，取“引擎自己预测与真值一致数最多”的对齐。

    为什么需要它：步进法只能在槽**之间**找空洞，行首/行尾的漏检根本没有
    步进可测（实测微乐帧 05 漏的是最后一张、途游帧 26 同理，两帧以 12vs13、
    13vs14 被白白地放弃）。

    为什么不是循环论证：这里拿引擎预测去比对齐，而不是去定标签——如果漏检位
    放错了，整行会错位，一致数会显著下降，因此用 agree/m 阈值卡住。它仍然能收到
    判错的槽（只要整体自洽率达标），不像单纯内容对齐那样只能收到判对的。
    """
    m, n = len(ts), len(labs)
    if m == 0 or m > n or not all(len(t) > 4 for t in ts):
        return None
    best, best_agree = None, -1
    for holes in combinations(range(n), n - m):
        hs = set(holes)
        seq = [None if i in hs else ts[i - sum(1 for h in holes if h < i)] for i in range(n)]
        agree = sum(1 for i, t in enumerate(seq) if t is not None and t[4] == labs[i])
        if agree > best_agree:
            best_agree, best = agree, seq
    # 0.8 是“对得起来”的下限：低于它说明漏检位推不准，宁可不收。
    if best is None or best_agree / m < 0.8:
        return None
    return best


def align_to_gt(ts, labs):
    """把检出槽展开成与物理张数等长的序列，漏检位填 None；对不上返回 None。

    为什么不用内容对齐定标签：那样只有“引擎本来就判对”的槽才会对上，而
    最需要补模板的恰恰是判错的槽，永远收不到。
    多检（牌河/被换出的牌混进条带）直接整帧放弃：步进推不出该删哪个槽，
    而宁可少收样本，也不能把牌河里的牌当手牌烧进模板库。
    """
    n_gt = len(labs)
    if not ts or n_gt <= 0 or len(ts) > n_gt:
        return None
    return _align_by_step(ts, n_gt) or _align_by_agreement(ts, labs)


def main():
    for platform, style, frames in JOBS:
        out = os.path.join(HERE, "tiles", style)
        os.makedirs(out, exist_ok=True)
        seen = {}
        n_img = 0
        skipped = 0
        for idx in sorted(frames):
            gt = GT_BY_FRAME.get(idx)
            if not gt:
                print(f"[{idx:02d}] 无 GT -> 跳过")
                continue
            labs = gt.split()
            img, ts = hand_tiles(idx)
            if img is None:
                print(f"[{idx:02d}] 读图失败")
                continue
            ts = sorted(ts, key=lambda t: t[0])
            seq = align_to_gt(ts, labs)
            if seq is None:
                print(f"[{idx:02d}] 无法对齐 检出={len(ts)} GT={len(labs)} -> 跳过")
                continue
            n_miss = sum(1 for s in seq if s is None)
            if n_miss:
                print(f"[{idx:02d}] 漏检 {n_miss} 位，按步进占位对齐")
            hh, ww = img.shape[:2]
            for col, (t, lab) in enumerate(zip(seq, labs)):
                if lab == "?":
                    skipped += 1
                    continue
                if t is None:
                    # 漏检位没有裁片可收：占位只用来保证整帧对齐
                    continue
                x, y, w, h = t[:4]
                crop = img[max(0, y):min(hh, y + h), max(0, x):min(ww, x + w)]
                if crop.size == 0:
                    continue
                face = face_align(crop)
                p = os.path.join(out, f"f{idx:02d}_{col:02d}_{lab}.png")
                cv2.imwrite(p, face)
                seen.setdefault(lab, []).append(os.path.basename(p))
                n_img += 1
        # 先清掉本轮没再产出的旧裁片：列序对齐方式一变，同一张牌的 col 号就会
        # 漂移，旧命名的残留文件会带着**错标签**继续被 build_platform_bank 收进库
        # （实测风险：旧 12 位 GT 下 f34_09_3p.png 标的是第 9 格，而第 9 格物理上是
        # 6m）——模板库被错标签污染的代价远大于少几个样本。
        keep = {name for files in seen.values() for name in files}
        stale = [f for f in sorted(os.listdir(out)) if f.endswith(".png") and f not in keep]
        for f in stale:
            os.remove(os.path.join(out, f))
        if stale:
            print(f"  清理对齐漂移的旧裁片 {len(stale)} 张："
                  f"{' '.join(stale[:6])}{' ...' if len(stale) > 6 else ''}")
        print(f"\n=== {style}: 落盘 {n_img} 张（跳过读不准 {skipped} 格），覆盖 {len(seen)}/34 类 ===")
        for lab in sorted(seen):
            print(f"  {lab:3} x{len(seen[lab]):2d}  {seen[lab][0]}")
        miss = [c for c in
                [f"{n}{s}" for s in "mpsz" for n in (range(1, 10) if s != "z" else range(1, 8))]
                if c not in seen]
        print(f"  缺类({len(miss)}): {' '.join(miss)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
