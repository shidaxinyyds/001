# -*- coding: utf-8 -*-
"""反事实核对：腾讯底座 37 帧在「手牌走 YOLO」与「走网格 NCC」两条通道下的逐张准确率。

目的：把 README/报告里那句「腾讯底座逐张 78.40%（YOLO 旧通道）vs 100%（NCC 新通道）」
钉死成**随时可复现**的事实，而不是"来历不明的数字"。口径完全照抄 `localtest/eval_base.py`：
逐张 = 多重集交集 / max(GT张数, 检出张数)（旧通道会幻牌，所以分母取 max）。

为什么这个脚本从 `build/` 搬进 `localtest/`：README 曾直接引用 `build/_counterfactual.py`，
而 `build/` 是 gitignore 的派生物目录 —— clone 到新机器上那条命令根本不存在，
文档里写一条跑不出来的命令等于造假。这里**不叫 `eval_*.py` 也不叫 `test_*.py`**，
因为 `run_all_tests.py` 会自动发现这两类并把它们当门禁；本脚本是归因工具，
它自己不做红绿判定（达标与否由人读输出）。

运行：py -3.10 -X utf8 localtest/counterfactual_channels.py
输出：build/counterfactual.txt
"""
import contextlib
import io
import json
import os
import sys
from collections import Counter

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
from engine.engine import Engine  # noqa: E402

SHOT_DIR = os.path.join(REPO, "localtest", "shots")
GT_PATH = os.path.join(REPO, "localtest", "gt", "shots.json")
OUT = os.path.join(REPO, "build", "counterfactual.txt")


def codes(s):
    return [s[i:i + 2] for i in range(0, len(s or ""), 2)]


def run(img, hand_grid):
    # 每帧新建引擎：`hand_grid` 是引擎级开关，同引擎翻开关会带着上一帧的稳定器状态。
    e = Engine()
    e.set_config("hand_grid", hand_grid)
    with contextlib.redirect_stdout(io.StringIO()):
        return json.loads(e.process(img).result)


def main():
    rows = [x for x in json.load(open(GT_PATH, encoding="utf-8"))["shots"]
            if x.get("verified")]
    lines = [f"已校验帧：{len(rows)}"]
    for hand_grid in (True, False):
        hit = tot = exact = 0
        per_frame = []
        for e in rows:
            img = cv2.imread(os.path.join(SHOT_DIR, e["file"]))
            if img is None:
                lines.append(f"读图失败 {e['file']}")
                continue
            d = run(img, hand_grid)
            g, h = Counter(codes(e.get("hand"))), Counter(codes(d.get("hand")))
            n_gt = len(codes(e.get("hand")))
            n_det = len(codes(d.get("hand")))
            inter = sum((g & h).values())
            if n_gt:
                hit += inter
                tot += max(n_gt, n_det)
            if sorted(g.elements()) == sorted(h.elements()):
                exact += 1
            per_frame.append((e["file"], n_gt, n_det, inter))
        lines.append("")
        lines.append(f"hand_grid={hand_grid}（{'网格 NCC 通道' if hand_grid else '主检测器 YOLO 通道'}）")
        lines.append(f"  逐张 {hit}/{tot} = {100.0 * hit / max(tot, 1):.2f}%   整帧精确 {exact}/{len(rows)}")
        worst = sorted(per_frame, key=lambda x: (x[3] - x[1]))[:4]
        lines.append("  最差帧：" + "; ".join(f"{f} gt{a}/det{b} 交{c}" for f, a, b, c in worst))
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)


if __name__ == "__main__":
    main()
