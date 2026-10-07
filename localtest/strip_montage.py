# -*- coding: utf-8 -*-
"""把多帧的手牌条带纵向堆成一张图，让人一次能读多帧的物理牌面。

为什么需要：要判断识别率必须先有可信 GT，而 GT 必须**独立于引擎检出框**
（从裁片拼图读会把漏检静默吸收成"GT 少一张"，这个坑已经踩过一次）。
逐帧 Read 一张条带图要一次调用，22 帧就是 22 次；堆成一张图后一次能核 6~8 帧。

每行左侧烧进帧号与平台，行高按原始条带保留，避免缩放后条子根数数不清。
用法: py -3.10 localtest\strip_montage.py 1 2 3 5 6 7 8 10
输出: localtest/shots_calib/strip_montage.png
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS  # noqa: E402
from ab_pitch_classify import PLATFORM_OF  # noqa: E402
from audit_bank_fit import locate_strip  # noqa: E402

LABEL_W = 150
GAP = 8


def main():
    args = [int(a) for a in sys.argv[1:] if a.isdigit()]
    idxs = sorted(args or range(1, 13))
    strips = []
    for idx in idxs:
        f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            print(f"[{idx:02d}] 缺帧")
            continue
        img = cv2.imread(os.path.join(SHOTS, f))
        y_top, y_bot, _xl, _xr = locate_strip(img)
        strip = img[max(0, y_top - 6):y_bot + 6, :].copy()
        canvas = np.full((strip.shape[0], LABEL_W + strip.shape[1], 3), 30, np.uint8)
        canvas[:, LABEL_W:] = strip
        cv2.putText(canvas, f"{idx:02d}", (8, strip.shape[0] // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, PLATFORM_OF.get(idx, "?")[:8], (8, strip.shape[0] // 2 + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        strips.append(canvas)
        print(f"[{idx:02d}] {PLATFORM_OF.get(idx,'?')} 条带高 {strip.shape[0]}")

    if not strips:
        print("没有可拼的帧")
        return 1
    # 宽度统一到最宽的一条，窄的右侧填灰，保证可以 vconcat
    wmax = max(s.shape[1] for s in strips)
    pads = []
    for s in strips:
        if s.shape[1] < wmax:
            p = np.full((s.shape[0], wmax - s.shape[1], 3), 30, np.uint8)
            s = np.hstack([s, p])
        pads.append(s)
        pads.append(np.full((GAP, wmax, 3), 30, np.uint8))
    out = os.path.join(SHOTS, "strip_montage.png")
    cv2.imwrite(out, np.vstack(pads))
    print(f"\n拼了 {len(strips)} 帧 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
