# -*- coding: utf-8 -*-
"""把 20 张真机帧的「底部手牌行裁片」拼成带编号的联屏，供人眼钉手牌真值。

为什么要人眼钉：识别精度的评测必须有独立于引擎的真值。引擎自己报的 hand
不能当答案（那是待测对象），面板上写的张数同样不能——它也是引擎输出的渲染。
唯一可信的来源是截图里的牌面本身。

编号用 ASCII 而不是中文：cv2 的 putText 画不出汉字，而联屏上的标签只是索引，
真正的对应关系打印在终端表里（idx -> 文件名 -> 引擎 hand）。

用法: py -3.10 localtest/montage_hands.py [--cols 2] [--width 2600] [--pick queshen,shushan]
"""
import argparse
import json
import os

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROP_DIR = os.path.join(REPO, "localtest", "shots_multi", "crops")
OUT = os.path.join(REPO, "build", "montage_hands.png")
DIAG = os.path.join(REPO, "build", "multi_diag.jsonl")


def tile_row(img, idx, name):
    """在裁片左上角贴一块不透明底，写上编号（牌行本身不能被压）。"""
    cv2.rectangle(img, (0, 0), (190, 34), (0, 0, 0), -1)
    cv2.putText(img, f"{idx:02d} {name[:22]}", (4, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--width", type=int, default=2600, help="联屏总宽")
    ap.add_argument("--pick", default="", help="只拼帧名含这些子串的（逗号分隔）")
    ap.add_argument("--tag", default="hands", help="输出文件名后缀，避免分批互相覆盖")
    args = ap.parse_args()

    OUT2 = os.path.join(REPO, "build", f"montage_{args.tag}.png")
    names = sorted(f for f in os.listdir(CROP_DIR) if f.endswith("_hand.jpg"))
    if args.pick:
        keys = [s.strip() for s in args.pick.split(",") if s.strip()]
        names = [n for n in names if any(k in n for k in keys)]
    if not names:
        raise SystemExit(f"没有裁片：{CROP_DIR}（先跑 diag_multiplatform.py --crop）")

    hands = {}
    if os.path.exists(DIAG):
        with open(DIAG, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                p = r.get("payload") or {}
                hands[r["file"]] = (p.get("hand") or "", p.get("count"))

    cell_w = max(1, args.width // args.cols)
    cells = []
    for i, n in enumerate(names):
        img = cv2.imread(os.path.join(CROP_DIR, n))
        if img is None:
            print(f"!! 读不到 {n}")
            continue
        s = (cell_w - 8) / img.shape[1]
        img = cv2.resize(img, (cell_w - 8, max(1, int(img.shape[0] * s))),
                         interpolation=cv2.INTER_LINEAR)
        cells.append(tile_row(img, i, n.replace("_hand.jpg", "")))

    rows = (len(cells) + args.cols - 1) // args.cols
    pad = 34
    rh = max(c.shape[0] for c in cells) + pad
    canvas = np.zeros((rh * rows, args.width, 3), dtype=np.uint8)
    for i, c in enumerate(cells):
        r, k = divmod(i, args.cols)
        y = r * rh + (rh - pad - c.shape[0])
        canvas[y:y + c.shape[0], k * cell_w:k * cell_w + c.shape[1]] = c
    os.makedirs(os.path.dirname(OUT2), exist_ok=True)
    cv2.imwrite(OUT2, canvas)

    print(f"{'idx':>4s}  {'帧名':26s} {'引擎报张数':>8s}  引擎 hand")
    print("-" * 110)
    for i, n in enumerate(names):
        stem = n.replace("_hand.jpg", "") + ".jpg"
        h, cnt = hands.get(stem, ("?", "?"))
        print(f"{i:>4d}  {stem:26s} {str(cnt):>8s}  {h}")
    print(f"\n联屏：{OUT2}  ({canvas.shape[1]}x{canvas.shape[0]})")


if __name__ == "__main__":
    main()
