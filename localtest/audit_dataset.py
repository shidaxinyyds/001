# -*- coding: utf-8 -*-
"""收割素材的数据审计：类别 × 平台 × 帧 的分布，以及帧级 train/val 划分可行性。

为什么必须先做这个而不是直接训模型：
  1) **泄漏**：tiles/ 里的裁片大部分来自我用来评分的那批帧。拿它们训练、再在
     同一批帧上评分，得到的准确率毫无意义。文件名带帧号（f<帧>_<列>_<牌>.png），
     所以必须按**帧**划分而不是按牌划分——同一帧的牌共享光照/桌布/尺度，
     按牌随机划会让验证集几乎等于训练集。
  2) **每类样本数**：分类器能不能训，取决于「每类几张」而不是「总共几张」。
     309 张听上去不少，摊到 34 类 × 4 平台可能就是每类 2 张，训不出来。
  3) **跨平台 vs 分平台**：决定模型形态。分平台训练保住牌风区分但数据被切碎；
     合并训练数据多一个量级，但同一张牌在不同牌风下外观不同，需要额外输入。

用法: py -3.10 localtest\audit_dataset.py
输出: localtest/dataset_audit.txt
"""
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
TILES = os.path.join(HERE, "tiles")

ALL_TILES = [f"{n}{s}" for s in "mps" for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)]


def parse(fn):
    """f<帧>_<列>_<牌>.png -> (frame, col, label)；不合规返回 None。"""
    stem = fn[:-4]
    parts = stem.split("_")
    if len(parts) != 3 or not parts[0].startswith("f"):
        return None
    try:
        return int(parts[0][1:]), int(parts[1]), parts[2]
    except ValueError:
        return None


def main():
    lines = []
    by_style = {}
    for style in sorted(os.listdir(TILES)):
        src = os.path.join(TILES, style)
        if not os.path.isdir(src):
            continue
        recs = []
        for fn in sorted(os.listdir(src)):
            if not fn.endswith(".png"):
                continue
            p = parse(fn)
            if p:
                recs.append(p)
        by_style[style] = recs

    lines.append("=== 每平台素材规模 ===")
    lines.append(f"{'style':10} {'张数':>5} {'类数':>5} {'帧数':>5} {'张/类':>7} {'张/帧':>7}")
    for style, recs in sorted(by_style.items()):
        n = len(recs)
        if not n:
            lines.append(f"{style:10} {0:5d}     0     0       -       -")
            continue
        ncls = len({r[2] for r in recs})
        nfr = len({r[0] for r in recs})
        lines.append(f"{style:10} {n:5d} {ncls:5d} {nfr:5d} {n / ncls:7.2f} {n / nfr:7.1f}")

    total = sum(len(v) for v in by_style.values())
    lines.append(f"\n合计裁片 {total} 张")

    lines.append("\n=== 合并后每类样本数（跨平台统一训一个分类器的口径）===")
    cls_n = defaultdict(int)
    for recs in by_style.values():
        for _f, _c, lab in recs:
            cls_n[lab] += 1
    have = [(lab, cls_n[lab]) for lab in ALL_TILES if cls_n.get(lab)]
    missing = [lab for lab in ALL_TILES if not cls_n.get(lab)]
    counts = sorted(c for _l, c in have)
    lines.append(f"  覆盖 {len(have)}/{len(ALL_TILES)} 类，缺类 {len(missing)}: {' '.join(missing) or '—'}")
    lines.append(f"  每类张数：min={counts[0]} 中位={counts[len(counts)//2]} max={counts[-1]} "
                 f"均值={total/len(have):.1f}")
    thin = [f"{lab}x{c}" for lab, c in have if c < 6]
    lines.append(f"  样本 <6 张的类（{len(thin)}）: {' '.join(thin) or '—'}")
    lines.append("  直方图: " + "  ".join(f"{c}" for c in counts))

    lines.append("\n=== 帧级划分：每平台留出 2 帧做 val 后的规模 ===")
    for style, recs in sorted(by_style.items()):
        frames = sorted({r[0] for r in recs})
        if len(frames) < 3:
            lines.append(f"  {style:10} 只有 {len(frames)} 帧 -> 无法帧级划分（val 会抽空训练集）")
            continue
        # 交错取帧，避免 val 全落在同一局（光照相近）
        val = frames[::max(1, len(frames) // 2)][:2]
        vset = set(val)
        n_tr = sum(1 for r in recs if r[0] not in vset)
        n_va = len(recs) - n_tr
        tr_cls = len({r[2] for r in recs if r[0] not in vset})
        va_only = sorted({r[2] for r in recs if r[0] in vset}
                         - {r[2] for r in recs if r[0] not in vset})
        lines.append(f"  {style:10} 帧={len(frames)} val={val} 训练={n_tr} 张/{tr_cls} 类，"
                     f"验证={n_va} 张；val 独有类 {len(va_only)}: {' '.join(va_only) or '—'}")

    # 全局：所有平台合并后，val 独有类是硬伤（分类器从没见过这个类）
    lines.append("\n=== 结论要点 ===")
    worst = min((len({r[0] for r in recs}) for recs in by_style.values() if recs), default=0)
    lines.append(f"  单平台最少帧数 = {worst}；分平台训练时 val 帧一留出，"
                 f"每类样本会再掉 ~{100 * 2 // max(worst, 3)}%")
    lines.append(f"  合并训练每类均值 = {total/max(len(have),1):.1f} 张 —— "
                 f"这是能否训 CNN 的决定性数字")
    text = "\n".join(lines) + "\n"
    out = os.path.join(HERE, "dataset_audit.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
