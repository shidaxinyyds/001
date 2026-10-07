# -*- coding: utf-8 -*-
"""为逐张人工核对 GT 生成「手牌带 + 引擎标签」标注图。

把引擎判出的每张手牌裁出来横向排列，下方标出引擎给的 label，
人眼只需逐格判 ✓/✗，不必再去整帧里对位。同时把该行右侧补上
「ROI 内未被采信」的牌，用于发现漏检。

用法: py -3.10 localtest\viz_hand_tiles.py [idx ...]      默认全部
输出: localtest/shots_calib/hands/NN_platform.png
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
from platforms import get_hand_roi  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")
OUT = os.path.join(SHOTS, "hands")

PLATFORM_OF = {i: "weile" for i in range(1, 13)}
PLATFORM_OF[13] = "shushan"
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"

CELL_H = 120
LABEL_H = 26


def cell(img, rect, label, idx):
    x, y, w, h = rect
    hh, ww = img.shape[:2]
    crop = img[max(0, y):min(hh, y + h), max(0, x):min(ww, x + w)]
    if crop.size == 0:
        crop = np.zeros((10, 10, 3), np.uint8)
    s = CELL_H / float(crop.shape[0])
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), CELL_H),
                      interpolation=cv2.INTER_AREA)
    band = np.full((LABEL_H, crop.shape[1], 3), 30, np.uint8)
    txt = f"{idx}:{label or '?'}"
    cv2.putText(band, txt, (3, LABEL_H - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([crop, band])


def build(idx, platform):
    f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
    if not f:
        return None
    img = cv2.imread(os.path.join(SHOTS, f))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    h = img.shape[0]

    # 必须每图新建 Engine：set_platform 只重置投票器/稳定器/帧缓存，
    # 不清牌河账本与 _match_started，复用会把上一张图的状态漏到下一张。
    eng = Engine()
    eng.set_platform(platform)
    d = None
    for _ in range(4):  # 与 calibrate_shots 一致：喂满多帧让门控成立
        d = json.loads(eng.process(img).result)
        if (d.get("count") or 0) > 0 and d.get("status") not in ("waiting", "no_tiles"):
            break
    tiles = [t for t in (d.get("tiles") or []) if len(t) > 5 and t[5] == "hand"]

    roi = get_hand_roi(platform)
    band = img[int(h * roi[0]):int(h * roi[1]), :]
    cells = [cell(img, t[:4], t[4], i) for i, t in enumerate(sorted(tiles, key=lambda t: t[0]))]
    if not cells:
        cells = [np.full((CELL_H + LABEL_H, 200, 3), 40, np.uint8)]
    wsum = sum(c.shape[1] for c in cells) + 4 * (len(cells) - 1)
    canvas = np.full((CELL_H + LABEL_H, max(wsum, band.shape[1]), 3), 20, np.uint8)
    x = 0
    for c in cells:
        canvas[:c.shape[0], x:x + c.shape[1]] = c
        x += c.shape[1] + 4
    # 下方附整条 hand_roi 原图，用于核对漏检
    bs = (CELL_H + LABEL_H) / float(band.shape[0])
    band = cv2.resize(band, (int(band.shape[1] * bs), CELL_H + LABEL_H))
    out = np.vstack([canvas, np.full((6, canvas.shape[1], 3), 200, np.uint8),
                     np.pad(band, ((0, 0), (0, max(0, canvas.shape[1] - band.shape[1])), (0, 0)))
                     [:, :canvas.shape[1]]])
    p = os.path.join(OUT, f"{idx:02d}_{platform}.png")
    cv2.imwrite(p, out)
    return p, d.get("count"), d.get("hand")


def main():
    os.makedirs(OUT, exist_ok=True)
    args = [int(a) for a in sys.argv[1:]]
    idxs = args or sorted(int(f.split("_")[1][:2]) for f in os.listdir(SHOTS)
                          if f.lower().endswith(".jpg"))
    for idx in idxs:
        r = build(idx, PLATFORM_OF.get(idx, "generic"))
        if r:
            print(f"[{idx:02d}] count={r[1]} {r[2]}  -> {os.path.basename(r[0])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
