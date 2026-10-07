# -*- coding: utf-8 -*-
"""bank 事实现读器：每个风格 bank 有多少核 / 多少类，以及哪些平台已挂本家 bank。

为什么要有这个脚本（而不是在 README 里写一行常量）：README 的「模板条目共 281」
在两轮补库之间已经错过两次——bank 数量是随收割增长的活数据，任何静态副本都会过期，
而过期的 README 会让人以为某个平台还没适配（或反之）。这条命令永远给当前树内的真值。

用法：py -3.10 -X utf8 localtest/bank_facts.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import (  # noqa: E402
    TencentGridDetector, banked_platforms)


def main() -> int:
    d = TencentGridDetector()
    n: Counter = Counter()
    lab: dict = defaultdict(set)
    for lbl, style, _btn, _plain, _gb, _gp in d._cores:
        n[style] += 1
        lab[style].add(lbl)
    print(f"模板核总数 {sum(n.values())}（含主库；一张牌可以有 #2/#3 变体核）")
    for k in sorted(n, key=lambda s: (-n[s], s)):
        print(f"  {k:9s} 核 {n[k]:3d}   类 {len(lab[k]):2d}")
    print("已挂本家 bank 的平台（`banked_platforms()`，手牌走 NCC 通道的清单）：",
          sorted(banked_platforms()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
