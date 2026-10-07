# -*- coding: utf-8 -*-
"""把同一平台的多帧手牌裁片拼成一张网格图：行=帧号，列=该帧从左到右的手牌。

为什么要拼：逐帧一张图共 40 张，读图核对零碎且看不出牌风内的类别覆盖缺口；
按平台拼成网格后一次读图即可同时得到（a）该平台各帧的完整 GT，
（b）34 类牌面在真机牌风下的覆盖情况——后者决定风格模板库要补哪些张。

用法: py -3.10 localtest\gt_grid.py [weile|gd_queshen|jj|tuyou|shushan ...]
输出: localtest/shots_calib/gtm/grid_<platform>.png
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from montage_for_gt import PLATFORM_OF, hand_tiles, OUT, SHOTS  # noqa: E402

CELL_H = 140
TAG_W = 46


def main():
    all_idx = sorted(int(f.split("_")[1][:2]) for f in os.listdir(SHOTS)
                     if f.lower().endswith(".jpg"))
    plats = sys.argv[1:] or sorted(set(PLATFORM_OF.get(i, "generic") for i in all_idx))
    for plat in plats:
        idxs = [i for i in all_idx if PLATFORM_OF.get(i, "generic") == plat]
        rows = []
        for idx in idxs:
            img, ts = hand_tiles(idx)
            rows.append((idx, img, ts or []))
            print(f"[{idx:02d}] n={len(ts or [])}")
        ncol = max((len(t) for _i, _g, t in rows), default=1)
        cells_w = []
        for idx, img, ts in rows:
            line = [np.full((CELL_H + 6, TAG_W, 3), 30, np.uint8)]
            cv2.putText(line[0], str(idx), (4, 18), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (255, 255, 255), 2, cv2.LINE_AA)
            for ci, t in enumerate(ts):
                x, y, w, h = t[:4]
                if img is None:
                    crop = np.zeros((CELL_H, 60, 3), np.uint8)
                else:
                    hh, ww = img.shape[:2]
                    crop = img[max(0, y):min(hh, y + h), max(0, x):min(ww, x + w)]
                    if crop.size == 0:
                        crop = np.zeros((10, 10, 3), np.uint8)
                    sc = CELL_H / float(crop.shape[0])
                    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * sc)), CELL_H),
                                      interpolation=cv2.INTER_AREA)
                # 标列号：读图时报「帧:列→牌面」才能无歧义定位（之前整行无编号，
                # 数格子极易错位，GT 直接失真）。
                cv2.rectangle(crop, (0, CELL_H - 22), (26, CELL_H), (255, 255, 255), -1)
                cv2.putText(crop, str(ci), (4, CELL_H - 5), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (0, 0, 0), 2, cv2.LINE_AA)
                line.append(crop)
            for _ in range(ncol - len(ts)):
                line.append(np.full((CELL_H, 20, 3), 90, np.uint8))
            gap = np.full((CELL_H + 6, 4, 3), 255, np.uint8)
            body = []
            for c in line:
                pad = np.zeros((6, c.shape[1], 3), np.uint8) if c.shape[0] < CELL_H + 6 else None
                body.append(np.vstack([c, pad]) if pad is not None else c)
                body.append(gap)
            cells_w.append(np.hstack(body))
        if not cells_w:
            print(f"{plat}: 无帧")
            continue
        W = max(r.shape[1] for r in cells_w)
        cells_w = [np.pad(r, ((0, 0), (0, W - r.shape[1]), (0, 0)), constant_values=255)
                   for r in cells_w]
        grid = np.vstack(cells_w)
        p = os.path.join(OUT, f"grid_{plat}.png")
        cv2.imwrite(p, grid)
        print(f"-> {os.path.basename(p)}  {grid.shape[1]}x{grid.shape[0]}  帧数={len(idxs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
