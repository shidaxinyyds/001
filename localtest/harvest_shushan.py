# -*- coding: utf-8 -*-
"""收割蜀山四川麻将牌面样本：手牌行(已知GT) + 明牌区(待人工标注)。

输出:
- localtest/harvest_shushan/hand/<idx>_<label>.png  手牌行高清样本
- localtest/harvest_shushan/cand/montage.png        明牌区候选拼图(人工读图定标)

用法: py -3.10 localtest/harvest_shushan.py
"""
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots_shushan")
OUT = os.path.join(HERE, "harvest_shushan")
HAND = os.path.join(OUT, "hand")
CAND = os.path.join(OUT, "cand")

# 手牌行 GT（rect 来自 diag_shushan.py 实测输出；label 为逐图人工核对结果）
HAND_JOBS = {
    "s1.jpg": [
        ((522, 739, 112, 151), "7z"),   # 赖标红中
        ((634, 741, 111, 149), "8m"),
        ((968, 739, 112, 151), "9m"),
        ((1080, 739, 112, 151), "7p"),
        ((1526, 739, 112, 151), "9p"),
    ],
    "s2.jpg": [
        ((1526, 739, 112, 151), "7s_que"),  # 带蓝色「缺」角标的7条（角标变体样本）
        ((1661, 741, 111, 149), "7p"),
    ],
    "s5.jpg": [
        ((201, 739, 112, 151), "7z"),
        ((313, 741, 111, 149), "2m"),
        ((536, 741, 111, 149), "3m"),
        ((759, 739, 112, 151), "4m"),
        ((1094, 739, 112, 151), "5m"),   # 伍萬：被旧模板误读为3m的本尊
        ((1317, 739, 112, 151), "7s"),   # 七条：被旧模板误读为5s的本尊
    ],
}

# 明牌区候选（全图坐标粗框，先裁出来拼montage再人工定标）
CAND_JOBS = {
    "s1.jpg": [
        (1430, 600, 1500, 690),   # 黄框弃牌
        (1150, 200, 1215, 270),   # 中央弃牌
        (715, 215, 785, 270),     # 中央弃牌
        (755, 490, 825, 560),     # 中央弃牌
    ],
    "s4.jpg": [
        (760, 25, 830, 95), (825, 25, 895, 95), (890, 25, 960, 95),
        (955, 25, 1025, 95), (1020, 25, 1090, 95), (1085, 25, 1155, 95),
        (1150, 25, 1220, 95), (1215, 25, 1285, 95),
        (1115, 610, 1185, 690), (1180, 610, 1250, 690), (1245, 610, 1315, 690),
        (1310, 610, 1380, 690), (1375, 610, 1445, 690), (1440, 610, 1510, 690),
    ],
    "s7.jpg": [
        (1610, 465, 1700, 545), (1610, 520, 1700, 600),
    ],
}


def main():
    os.makedirs(HAND, exist_ok=True)
    os.makedirs(CAND, exist_ok=True)
    # 1. 手牌行高清样本
    for fname, jobs in HAND_JOBS.items():
        img = cv2.imread(os.path.join(SHOTS, fname))
        for i, ((x, y, w, h), label) in enumerate(jobs):
            crop = img[y:y + h, x:x + w]
            out = os.path.join(HAND, f"{fname[:-4]}_{i:02d}_{label}.png")
            cv2.imwrite(out, crop)
    # 2. 明牌候选 montage（统一缩放到高120 横排，格子上标序号）
    tiles = []
    for fname, boxes in CAND_JOBS.items():
        img = cv2.imread(os.path.join(SHOTS, fname))
        for (x1, y1, x2, y2) in boxes:
            tiles.append((f"{fname[:-4]}:{x1},{y1}", img[y1:y2, x1:x2]))
    rows, per_row = [], 7
    H = 130
    for r0 in range(0, len(tiles), per_row):
        chunk = tiles[r0:r0 + per_row]
        row_imgs = []
        for j, (tag, t) in enumerate(chunk):
            sc = H / t.shape[0]
            t2 = cv2.resize(t, (int(t.shape[1] * sc), H), interpolation=cv2.INTER_AREA)
            canvas = np.zeros((H + 20, max(t2.shape[1], 90) + 6, 3), np.uint8)
            canvas[20:, 3:3 + t2.shape[1]] = t2
            cv2.putText(canvas, f"{r0 + j}:{tag}", (3, 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            row_imgs.append(canvas)
        rows.append(np.hstack(row_imgs))
    W = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, W - r.shape[1]), (0, 0))) for r in rows]
    cv2.imwrite(os.path.join(CAND, "montage.png"), np.vstack(rows))
    print("harvest done ->", OUT)


if __name__ == "__main__":
    main()
