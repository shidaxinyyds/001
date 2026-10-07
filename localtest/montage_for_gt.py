# -*- coding: utf-8 -*-
"""生成「逐帧手牌裁片编号拼图」，用于人工独立读 GT。

刻意**不**在格子上标引擎给的 label——避免读图时被引擎的错误答案带偏
（锚定效应）。只标序号，人眼按序号报牌面，GT 与引擎输出才是独立两路。

用法: py -3.10 localtest\montage_for_gt.py [--cell-h 150] [--per-row 8] [idx ...]
输出: localtest/shots_calib/gtm/NN_platform.png
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")
OUT = os.path.join(SHOTS, "gtm")

PLATFORM_OF = {i: "weile" for i in range(1, 13)}
PLATFORM_OF[13] = "shushan"
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"

CELL_H = 150
TAG_H = 24
PER_ROW = 8


def parse_opts(argv):
    """读 cell 高度与每行枚数。默认值保持旧行为，只对窄牌平台传大值。

    窄牌平台（途游）按 8 枚一行拼出的整图超过 1800px，被查看器缩放后
    反而比原裁片更糊，筒子/条子的点数数不清——读不了 GT。
    """
    cell_h, per_row = CELL_H, PER_ROW
    if "--cell-h" in argv:
        cell_h = int(argv[argv.index("--cell-h") + 1])
    if "--per-row" in argv:
        per_row = max(1, int(argv[argv.index("--per-row") + 1]))
    return cell_h, per_row


# 选项值也是纯数字，不能交给下面的 isdigit() 当帧号解析
# （--cell-h 220 会多跑一个不存在的 220 帧，--per-row 4 会误跑第 4 帧）。
OPTIONS_WITH_VALUE = ("--cell-h", "--per-row")


def shot_idxs(argv):
    pos = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in OPTIONS_WITH_VALUE:
            skip = True
            continue
        if a.isdigit():
            pos.append(int(a))
    return pos


def hand_tiles(idx):
    f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
    if not f:
        return None, None
    img = cv2.imread(os.path.join(SHOTS, f))
    if img is None:
        return None, None
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    # 每图新建 Engine：set_platform 不清牌河账本/_match_started，复用会串状态
    eng = Engine()
    eng.set_platform(PLATFORM_OF.get(idx, "generic"))
    d = None
    for _ in range(4):
        d = json.loads(eng.process(img).result)
        if (d.get("count") or 0) > 0 and d.get("status") not in ("waiting", "no_tiles"):
            break
    ts = sorted([t for t in (d.get("tiles") or []) if len(t) > 5 and t[5] == "hand"],
                key=lambda t: t[0])
    return img, ts


def main():
    os.makedirs(OUT, exist_ok=True)
    argv = sys.argv[1:]
    cell_h, per_row = parse_opts(argv)
    args = shot_idxs(argv)
    idxs = args or sorted(int(f.split("_")[1][:2]) for f in os.listdir(SHOTS)
                          if f.lower().endswith(".jpg"))
    for idx in idxs:
        img, ts = hand_tiles(idx)
        if img is None:
            print(f"[{idx:02d}] 读图失败")
            continue
        if not ts:
            print(f"[{idx:02d}] 无手牌检出（待修帧）")
            continue
        cells = []
        for i, t in enumerate(ts):
            x, y, w, h = t[:4]
            hh, ww = img.shape[:2]
            crop = img[max(0, y):min(hh, y + h), max(0, x):min(ww, x + w)]
            if crop.size == 0:
                continue
            sc = cell_h / float(crop.shape[0])
            # 放大必须用 CUBIC：INTER_AREA 是为缩小设计的，拿它放大会把牌面
            # 墨迹糊成一团，筒子的圈与条子的根直接数不出来。
            interp = cv2.INTER_CUBIC if sc > 1 else cv2.INTER_AREA
            crop = cv2.resize(crop, (max(1, int(crop.shape[1] * sc)), cell_h),
                              interpolation=interp)
            cw = max(crop.shape[1], 70)
            canvas = cv2.copyMakeBorder(crop, TAG_H, 4, 4, 4, cv2.BORDER_CONSTANT,
                                        value=(255, 255, 255))
            cv2.rectangle(canvas, (0, 0), (cw + 8, TAG_H), (40, 40, 40), -1)
            cv2.putText(canvas, str(i), (6, TAG_H - 7), cv2.FONT_HERSHEY_SIMPLEX,
                        0.62, (255, 255, 255), 2, cv2.LINE_AA)
            cells.append(canvas)
        rows = []
        for r0 in range(0, len(cells), per_row):
            chunk = cells[r0:r0 + per_row]
            gap = np.full((chunk[0].shape[0], 8, 3), 255, np.uint8)
            line = []
            for c in chunk:
                line.append(c)
                line.append(gap)
            rows.append(np.hstack(line))
        W = max(r.shape[1] for r in rows)
        rows = [np.pad(r, ((0, 0), (0, W - r.shape[1]), (0, 0)),
                       constant_values=255) if r.shape[1] < W else r for r in rows]
        name = f"{idx:02d}_{PLATFORM_OF.get(idx,'generic')}.png"
        cv2.imwrite(os.path.join(OUT, name), np.vstack(rows))
        print(f"[{idx:02d}] n={len(ts)} -> gtm/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
