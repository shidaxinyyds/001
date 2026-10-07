# -*- coding: utf-8 -*-
"""补拍清单：精确到「哪个平台、哪一类、还差几张」，并标出评估独立性缺口。

为什么按平台算而不是按全局：生产用的是**单平台 bank**（templates_weile.py /
templates_tuyou.py ...），classify_tile 只会拿该平台的模板去匹配。所以"合并后
每类均值 9.7 张"是个会误导的数字——微乐自己可能只有 1 张。缺口必须逐平台算。

为什么把「独立评估帧」和样本缺口放在同一张表里：本轮已经吃过教训——微乐的 bank
是从那 8 个评估帧收割的，评估帧同时就是训练帧，于是自评 85.9% 而按帧留一只有
65.8%。**如果补来的图仍然全部拿去建 bank，那下一次自评还是虚高的，等于白补。**
每个平台都需要若干完全不进 bank 的帧。

目标张数 TARGET 的取法：NCC 靠模板多样性，实测每类 >=6 张时同平台相邻类
（4s/5s/6s 这类）才开始稳定分开；1~2 张的类几乎必然被邻类抢走。

用法: py -3.10 localtest\gap_report.py
输出: localtest/gap_report.txt
"""
import argparse
import collections
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ab_pitch_classify import GT, PLATFORM_OF          # noqa: E402
from harvest_style_tiles import JOBS                   # noqa: E402

TILES = os.path.join(HERE, "tiles")
TILE_RE = re.compile(r"[1-9][mps]|[1-7]z")
TARGET = 6           # 每类目标张数
HAND_SIZE = 13       # 一帧手牌可读出的张数（用于估算需要几帧）
MIN_EVAL_FRAMES = 3  # 每平台至少要有的完全不进 bank 的评估帧数


def counts(style):
    """-> {label: 张数}，只统计能解析出合法牌面的裁片。"""
    src = os.path.join(TILES, style)
    out = collections.Counter()
    if not os.path.isdir(src):
        return out
    for fn in os.listdir(src):
        if not fn.endswith(".png"):
            continue
        parts = fn[:-4].split("_")
        if len(parts) != 3 or not parts[0].startswith("f"):
            continue
        if TILE_RE.fullmatch(parts[2]):
            out[parts[2]] += 1
    return out


def observed_in_gt(platform):
    """该平台 GT 里真实出现过的类——用来区分「玩法不含」和「没拍到」。"""
    seen = set()
    for idx, s in GT.items():
        if PLATFORM_OF.get(idx) != platform:
            continue
        for tok in s.split():
            if TILE_RE.fullmatch(tok):
                seen.add(tok)
    return seen


def main():
    global TARGET
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=TARGET,
                    help="每类目标张数；3=最小可用档，6=达标档")
    a = ap.parse_args()
    TARGET = a.target
    lines = [f"=== 补拍清单（每类目标 {TARGET} 张，每平台独立评估帧目标 "
             f"{MIN_EVAL_FRAMES} 帧）==="]
    total_gap = 0
    summary = []
    for platform, style, bank_frames in JOBS:
        c = counts(style)
        gt_frames = sorted(i for i in GT if PLATFORM_OF.get(i) == platform)
        free_eval = [i for i in gt_frames if i not in set(bank_frames)]
        gt_seen = observed_in_gt(platform)
        # 在 GT 里出现过、但 bank 里一张模板都没有 -> 结构性必错，优先级最高
        zero_in_bank = sorted(lab for lab in gt_seen if not c.get(lab))
        # 从未在该平台任何 GT 帧/裁片里出现过的类，必须分两种情况说：
        #   * 数牌（万/筒/条 1-9）——任何麻将的牌墙都含它们，未观测**只能是拍得不够**，
        #     不能标成"玩法不含"（上一版就是这么错的：雀神被标成缺 1p/2m/3m，
        #     而真实原因是它只登了 5 帧 GT）。
        #   * 字牌（1z-7z）——川麻（血流/血战）确实不用完整风牌，未观测可能是玩法不含，
        #     只能标成"待确认"，不计入硬缺口。
        all_num = {f"{n}{s}" for s in "mps" for n in range(1, 10)}
        all_honor = {f"{n}z" for n in range(1, 8)}
        unseen = (all_num | all_honor) - gt_seen - set(c)
        number_unseen = sorted(unseen & all_num)
        honor_unseen = sorted(unseen & all_honor)
        gaps = {lab: TARGET - c[lab] for lab in c if c[lab] < TARGET}
        for lab in number_unseen:
            gaps[lab] = TARGET

        lines.append(f"\n[{platform}]  bank 目录 tiles/{style}  "
                     f"共 {sum(c.values())} 张 / {len(c)} 类")
        lines.append(f"  有 GT 的帧 {gt_frames or '—'}")
        lines.append(f"  建 bank 用掉的帧 {sorted(bank_frames)}")
        flag = "OK" if len(free_eval) >= MIN_EVAL_FRAMES else "不足"
        lines.append(f"  独立评估帧（不进 bank）{free_eval or '无'}  -> {flag}")
        if zero_in_bank:
            lines.append(f"  !! GT 中确实出现、但 bank 零模板（识别必错）: {' '.join(zero_in_bank)}")

        if gaps:
            lines.append(f"  每类不足 {TARGET} 张（差额从大到小）:")
            for lab, g in sorted(gaps.items(), key=lambda x: (-x[1], x[0])):
                lines.append(f"      {lab}  现有 {c.get(lab, 0):2d}  还差 {g}")
        else:
            lines.append(f"  已有各类均 >= {TARGET} 张")
        if number_unseen:
            lines.append(f"  !! 数牌零样本（必定是拍得不够，已计入缺口）: {' '.join(number_unseen)}")
        if honor_unseen:
            lines.append(f"  ?  字牌未观测（可能玩法不含，需你确认，未计入缺口）: "
                         f"{' '.join(honor_unseen)}")

        gap_sum = sum(gaps.values()) + len(zero_in_bank) * TARGET
        need = math.ceil(gap_sum / HAND_SIZE * 1.6) if gap_sum else 0
        # 系数 1.6：随机一帧里落在缺口类上的牌只占一部分，1 帧补不满 13 张缺口；
        # 这是经验估计而非精确值，实际以补完后重跑本脚本为准。
        need_eval = max(0, MIN_EVAL_FRAMES - len(free_eval))
        lines.append(f"  => 建议补拍：约 {need} 帧（填样本缺口）+ {need_eval} 帧"
                     f"（只做评估、不建 bank）")
        total_gap += gap_sum
        summary.append((platform, sum(c.values()), len(c), gap_sum, len(free_eval)))

    lines.append("\n=== 汇总 ===")
    lines.append(f"{'平台':12}{'现有张数':>9}{'覆盖类':>8}{'缺口张数':>9}{'独立评估帧':>11}")
    for p, n, k, g, e in summary:
        lines.append(f"{p:12}{n:9d}{k:8d}{g:9d}{e:11d}")
    lines.append(f"\n全平台合计还差 {total_gap} 张模板")
    lines.append("补拍要求（比数量更重要）：")
    lines.append("  1) 换对局、换时段拍：同一局连续截 10 张约等于 1 张的多样性")
    lines.append("  2) 每平台至少 3 帧只用于评估，不参与 harvest 建 bank")
    lines.append("  3) 优先覆盖标了 !! 的类——那些是当前**结构性必错**的牌")
    text = "\n".join(lines) + "\n"
    out = os.path.join(HERE, "gap_report.txt" if TARGET == 6 else f"gap_report_t{TARGET}.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
