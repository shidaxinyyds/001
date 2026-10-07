# -*- coding: utf-8 -*-
"""把指定帧的整条手牌带原样导出，用于**独立于引擎检出框**人工数牌。

为什么需要它：现在的 GT 是从 montage_for_gt 的裁片拼图上读的，而裁片来自
引擎检出的框——若 YOLO 漏检一张，拼图就只有 12 格，人眼按格读数出的 GT
也少一张，于是"漏检"被静默吸收成"识别错"，指标含义完全失真。
途游帧 34/36 的 x 步进里出现 268/240/230/258px（正常 pitch 约 140px），
说明真有漏检，必须先看清物理张数才能判定这两帧的真实准确率。

用法: py -3.10 localtest\dump_strip.py 34 36
输出: localtest/shots_calib/strip_<idx>.png
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS  # noqa: E402
from audit_bank_fit import locate_strip  # noqa: E402


def main():
    idxs = [int(a) for a in sys.argv[1:] if a.isdigit()] or [34]
    for idx in idxs:
        f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            print(f"[{idx:02d}] 无此帧")
            continue
        img = cv2.imread(os.path.join(SHOTS, f))
        h = img.shape[0]
        y_top, y_bot, x_left, x_right = locate_strip(img)
        strip = img[max(0, y_top - 20):min(h, y_bot + 20), :]
        out = os.path.join(SHOTS, f"strip_{idx:02d}.png")
        cv2.imwrite(out, strip)
        print(f"[{idx:02d}] 条带 y {y_top}~{y_bot} 尺寸 {strip.shape[1]}x{strip.shape[0]} -> {out}")
        # 列投影：牌与牌之间有桌布缝隙，暗列就是分隔，可用来独立数张数
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        col = (gray > 150).sum(axis=0).astype(float)
        col /= max(1.0, strip.shape[0] * 0.5)
        runs, in_run, start = [], False, 0
        for x, v in enumerate(col):
            if v >= 0.5 and not in_run:
                in_run, start = True, x
            elif v < 0.5 and in_run:
                in_run = False
                if x - start > 30:
                    runs.append((start, x))
        print(f"        列投影独立数到 {len(runs)} 个亮块: "
              + " ".join(f"{a}-{b}({b - a})" for a, b in runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
