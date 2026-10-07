# -*- coding: utf-8 -*-
"""按平台统计手牌输出的「结构性异常」，判断哪些平台还需要补风格 bank。

没有 GT 也能发现硬错误——两类是物理/规则上不可能出现的：
  1) 同牌 >4 枚：麻将每种牌只有 4 张。detect_strip 里有 <=4 刚性守卫，
     但它作用在 YOLO 原始 label 上，**等距重切 + 模板覆盖是在守卫之后**，
     所以覆盖层仍可能制造出 5 张同牌（实测微乐 [04] 有 5 张 2s）。
  2) 立牌未理牌（花色乱序）：真机手牌恒为按 万/筒/条/字 排序，
     乱序说明 label 序列是拼错的，而不是玩家没理牌。

用法: py -3.10 localtest\audit_quality.py [report.jsonl]
"""
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SUIT = {"m": 0, "p": 1, "s": 2, "z": 3}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "calib_report_trim2.jsonl")
    rows = load(path)
    agg = defaultdict(Counter)
    detail = defaultdict(list)
    for r in rows:
        plat = r.get("platform") or "?"
        labs = [l for l in (r.get("labels") or []) if isinstance(l, str)]
        agg[plat]["n"] += 1
        bad = [l for l in labs if len(l) < 2 or l[-1] not in SUIT]
        if bad:
            agg[plat]["怪标签"] += 1
            detail[plat].append(f"  [{r['idx']:02d}] 怪标签 {bad!r}")
        labs = [l for l in labs if len(l) >= 2 and l[-1] in SUIT]
        if not labs:
            agg[plat]["空手牌"] += 1
            continue
        dup = {k: v for k, v in Counter(labs).items() if v > 4}
        if dup:
            agg[plat]["同牌>4"] += 1
            detail[plat].append(f"  [{r['idx']:02d}] 同牌>4 {dup}  {''.join(labs)}")
        # 花色乱序：按 suit 序号看是否非递减（同花色内部数字乱序不算，牌桌只按花色分块）
        seq = [SUIT.get(l[-1], 9) for l in labs]
        if any(seq[i + 1] < seq[i] for i in range(len(seq) - 1)):
            agg[plat]["花色乱序"] += 1
            detail[plat].append(f"  [{r['idx']:02d}] 花色乱序  {''.join(labs)}")

    print(f"{'平台':14} {'帧':>3} {'同牌>4':>7} {'花色乱序':>9} {'空手牌':>7} {'怪标签':>7}")
    for plat in sorted(agg):
        a = agg[plat]
        print(f"{plat:14} {a['n']:3d} {a['同牌>4']:7d} {a['花色乱序']:9d}"
              f" {a['空手牌']:7d} {a['怪标签']:7d}")
    for plat in sorted(detail):
        if detail[plat]:
            print(f"\n{plat}:")
            for line in detail[plat]:
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
