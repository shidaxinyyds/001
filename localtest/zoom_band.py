# -*- coding: utf-8 -*-
"""把某一帧的手牌带按原分辨率放大导出，用于人工读图时的二次确认。

为什么需要它：make_tile_sheets.py 的 sheet 是把每张牌单独放大到高 300px 拼表，
一屏能读完一帧，但**牌与牌的相对关系、以及"七/八"这类顶部笔画差异小的字牌**
在 300px 高下常常判不实；而 band 是原分辨率整带（牌只有 ~100px 高），看清了
相对位置却看不清牌面。这个脚本取两者中间：按 rects 裁出整带后统一放大到指定
高度（默认 620），一次只看一帧，慢一点但不会把"猜"当成"读"。

它只做放大，不做检测——检测器给出的标签在这里同样不出现（读图纪律）。

运行：py -3.10 -X utf8 localtest/zoom_band.py weile 02
      py -3.10 -X utf8 localtest/zoom_band.py zj_sichuan 15 --h 800 --idx 8,9,10
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("platform")
    ap.add_argument("frame", help="帧号，如 02")
    ap.add_argument("--src", default="public/1")
    ap.add_argument("--h", type=int, default=620, help="放大后的带高（px）")
    ap.add_argument("--idx", default=None, help="只看这些序号（1 基，逗号分隔）")
    a = ap.parse_args()

    fr = int(a.frame)
    rp = os.path.join(REPO, "build", f"ann_{a.platform}", f"rects_{fr:02d}.json")
    with open(rp, encoding="utf-8") as fp:
        r = json.load(fp)
    img = cv2.imread(os.path.join(REPO, a.src, r["file"]))
    if img is None:
        raise SystemExit(f"读不到 {a.src}/{r['file']}")
    rr = sorted(r["rects"], key=lambda x: x[0])
    hh, ww = img.shape[:2]

    def up(crop):
        return cv2.resize(crop, (max(1, int(crop.shape[1] * a.h / crop.shape[0])), a.h),
                          interpolation=cv2.INTER_CUBIC)

    if a.idx:
        # 只看指定序号：等高后并排拼一条（牌高不一致时直接 hstack 会崩），
        # 放大倍数比整带高得多，用于对“七/八”这类笔画差异小的牌做二次确认。
        strips = []
        for i in a.idx.split(","):
            x, y, w, h = rr[int(i) - 1]
            crop = img[max(0, y):min(hh, y + h), max(0, x):min(ww, x + w)]
            if crop.size == 0:
                raise SystemExit(f"序号 {i} 裁出来是空的（rect 越界？）")
            strips.append(up(crop))
            strips.append(np.full((a.h, 8, 3), 40, np.uint8))
        out = np.hstack(strips[:-1])
        tag = "_".join(a.idx.split(","))
    else:
        y0 = max(0, min(x[1] for x in rr) - 8)
        y1 = min(hh, max(x[1] + x[3] for x in rr) + 8)
        out = up(img[y0:y1, 0:ww])
        tag = "full"
    dst = os.path.join(REPO, "build", f"ann_{a.platform}", f"zoom_{fr:02d}_{tag}.png")
    cv2.imwrite(dst, out)
    print(f"[ok] {out.shape[1]}x{out.shape[0]} n={len(rr)} -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
