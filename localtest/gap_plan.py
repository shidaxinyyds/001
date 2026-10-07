# -*- coding: utf-8 -*-
"""统计 GT 里「每个平台每个类出现在几帧」，把素材缺口按性价比排序。

为什么要这个：LOFO 评测里那 92 张"结构性缺类"（本帧是唯一含该牌的帧，剔完就没
模板）不是算法失误，任何阈值/算法改动都救不了它——唯一的修法是让每个类至少来自
**两帧以上**。

注意它只能列出**已钉 GT 部分**的缺口：未读的帧在人工读图前根本没有标签，所以
“读哪一帧能消掉最多缺口”没法离线算出来（想要这个就得先跑检测器拿伪标签，
而那正是本工具要防止的自证）。它能回答的是：“现在哪几类只住在一帧里、那帧
被剔会连带多少张”——这就是读新帧时的验收标准：新帧得带来这些类才算没白读。

运行：py -3.10 -X utf8 localtest/gap_plan.py localtest/gt/shots_b1.json
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main():
    gt = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "localtest", "gt", "shots_b1.json")
    with open(os.path.join(REPO, gt), encoding="utf-8") as fp:
        shots = json.load(fp)["shots"]

    # (style, label) -> 含该类的帧数
    frames_of = collections.defaultdict(set)
    for e in shots:
        for lab in e["hand"]:
            if lab == "?":
                continue
            frames_of[(e["style"], lab)].add(e["frame"])

    uniq = {k: sorted(v) for k, v in frames_of.items() if len(v) == 1}
    by_style = collections.Counter(k[0] for k in uniq)
    # “单一来源的类”到底涉及多少张：同一个类在一帧里可能有两三张（比如两个 5s），
    # 只数类名会低估缺口，而评测的分母是按张算的，所以这里按张数还原。
    tiles = collections.Counter()
    for (st, lab), frs in uniq.items():
        e = next(x for x in shots if x["style"] == st and x["frame"] == frs[0])
        tiles[st] += e["hand"].count(lab)

    lines = []
    lines.append(f"== GT {gt}：{len(shots)} 帧 / {sum(len(e['hand']) for e in shots)} 张 ==")
    lines.append("每平台：类数、只出现在 1 帧的类数（LOFO 下必然没有模板）、这些类合计张数")
    styles = sorted({e["style"] for e in shots})
    for st in styles:
        n_lab = len([k for k in frames_of if k[0] == st])
        lines.append(f"  {st:9} 类={n_lab:2d}  单一来源类={by_style[st]:2d}  "
                     f"涉及张数={tiles[st]:3d}")
    lines.append("\n单一来源的类（修法：再找含它的帧，或接受它不进评测）")
    for st in styles:
        ks = sorted([k for k in uniq if k[0] == st], key=lambda k: (k[1][1:], k[1][0]))
        if ks:
            lines.append(f"  {st}: " + " ".join(k[1] for k in ks))

    # 已经钉过 GT 但尚未构成"多帧来源"的帧：读新帧时的目标清单
    lines.append("\n每张单一来源类目前落在哪一帧（该帧被剔就没模板）")
    for st in styles:
        agg = collections.defaultdict(list)
        for k in sorted([k for k in uniq if k[0] == st], key=lambda k: k[1]):
            agg[uniq[k][0]].append(k[1])
        for fr in sorted(agg):
            e = next(x for x in shots if x["style"] == st and x["frame"] == fr)
            n = sum(e["hand"].count(l) for l in agg[fr])
            lines.append(f"  {st}#{fr:02d}（{e['src']}）: {' '.join(agg[fr])}  "
                         f"合计 {n} 张")
    text = "\n".join(lines) + "\n"
    out = os.path.join(REPO, "build", "gap_plan.txt")
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text, end="")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
