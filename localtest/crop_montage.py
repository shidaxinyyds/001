# -*- coding: utf-8 -*-
"""把 tiles/<style>/ 里的裁片按标签抽样拼成网格图，供人眼核对标签是否正确。

为什么需要它：tiles/tencent_happy 有 589 张带标签裁片，但仓库里没有任何脚本引用
这个命名格式（`{label}_media_{ts}_{idx}.png`），**标签来源不可追溯**。不可追溯的
标签直接拿去训练是危险的：如果它们是引擎自己标注的，就等于拿模型的错误当训练
目标，错误会被固化且之后再也查不出来。所以要先用眼睛验货，再决定是否入训练集。

读图约定：每格左上角烧的是「文件名里声称的 label」。如果我看到的牌面与它不符，
该批素材就不可信。

用法:
  py -3.10 localtest/crop_montage.py tencent_happy
  py -3.10 localtest/crop_montage.py duma520 -n 1
  py -3.10 localtest/crop_montage.py weile -n 2
输出: localtest/shots_montage/<style>.png（并在 stdout 打印每格明细，便于对照）
"""
import argparse
import collections
import os
import random
import re
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TILES = os.path.join(HERE, "tiles")
OUT_DIR = os.path.join(HERE, "shots_montage")

CELL_H = 132          # 单格高度（原始裁片约 55~145 高，统一到 132 便于肉眼数根）
LABEL_BAR = 16        # 格顶文字条
TILE_RE = re.compile(r"[1-9][mps]|[1-7]z")   # 合法 MPSZ 标签；不合规的一律不算样本


def load_groups(style, per_class):
    src = os.path.join(TILES, style)
    groups = collections.defaultdict(list)
    skipped = []
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".png"):
            continue
        stem = fn[:-4]
        # 两种命名：新收割 f<帧>_<列>_<牌>.png；旧素材 <牌>_... 或 <牌>.png
        parts = stem.split("_")
        if parts[0].startswith("f") and parts[0][1:].isdigit() and len(parts) == 3:
            lab = parts[2]
        else:
            lab = parts[0]
        # 严格校验：解析不出合法牌面的文件必须显式跳过，不能靠字符集合蒙混
        if not TILE_RE.fullmatch(lab):
            skipped.append(fn)
            continue
        groups[lab].append(fn)
    picked = {}
    for lab, files in groups.items():
        picked[lab] = sorted(random.Random(20261003).sample(files, min(per_class, len(files))))
    return picked, skipped


def cell(fn, lab, src):
    im = cv2.imread(os.path.join(src, fn))
    if im is None:
        im = np.zeros((100, 70, 3), np.uint8)
    w = max(1, int(round(im.shape[1] * CELL_H / im.shape[0])))
    im = cv2.resize(im, (w, CELL_H), interpolation=cv2.INTER_AREA)
    bar = np.full((LABEL_BAR, max(w, 60), 3), 30, np.uint8)
    cv2.putText(bar, f"{lab} {fn[:26]}", (2, 12), cv2.FONT_HERSHEY_SIMPLEX,
                0.34, (255, 255, 255), 1, cv2.LINE_AA)
    out = np.vstack([bar, im])
    if out.shape[1] < bar.shape[1]:
        out = cv2.copyMakeBorder(out, 0, 0, 0, bar.shape[1] - out.shape[1],
                                 cv2.BORDER_CONSTANT, value=(30, 30, 30))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("style")
    ap.add_argument("-n", "--per-class", type=int, default=2)
    ap.add_argument("--cols", type=int, default=8)
    a = ap.parse_args()

    src = os.path.join(TILES, a.style)
    if not os.path.isdir(src):
        print(f"no such style dir: {src}")
        return 1
    picked, skipped = load_groups(a.style, a.per_class)
    if not picked:
        print(f"no parseable tiles under {src}")
        return 1

    order = sorted(picked)
    cells = [cell(fn, lab, src) for lab in order for fn in picked[lab]]
    cw = max(c.shape[1] for c in cells)
    cells = [cv2.copyMakeBorder(c, 0, 0, 0, cw - c.shape[1], cv2.BORDER_CONSTANT,
                                value=(30, 30, 30)) for c in cells]
    cols = min(a.cols, len(cells))
    rows = (len(cells) + cols - 1) // cols
    while len(cells) < rows * cols:
        cells.append(np.full((cells[0].shape[0], cw, 3), 30, np.uint8))
    band = [np.hstack(cells[cols * r:cols * (r + 1)]) for r in range(rows)]
    grid = np.vstack(band)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{a.style}.png")
    cv2.imwrite(out, grid)

    print(f"style={a.style} 类数={len(order)} 每类<= {a.per_class} 共 {len(cells)} 格")
    print("每类张数: " + " ".join(f"{lab}x{len(picked[lab])}" for lab in order))
    if skipped:
        print(f"[跳过 {len(skipped)} 个标签不合规的文件] 例: {' '.join(skipped[:8])}")
    print(f"-> {out}  shape={grid.shape}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
