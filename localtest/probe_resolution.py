# -*- coding: utf-8 -*-
"""分辨率对照：同一帧缩到不同尺寸跑引擎，看手牌张数会不会随分辨率崩掉。

动机：真机面板报「手牌 (4张)/(5张)」，而离线按 2000x899 原帧跑 HEAD 能读到 13~14 张。
两者唯一的系统性差别是「喂给引擎的那一帧到底多大」——截图是用户从相册导出的原图，
采集链路送给 Python 的帧未必同尺寸。若张数随缩小崩塌，用户看到的漏识别就不是
「模型不行」而是「格网检测对分辨率敏感」，修法完全不同。

用法: py -3.10 localtest/probe_resolution.py [--sizes 2000,1600,1280,1080,800]
"""
import argparse
import contextlib
import io
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
sys.path.insert(0, REPO)   # 让 `from localtest.xxx import` 走得通（命名空间包）
from engine.engine import Engine  # noqa: E402
from localtest.diag_multiplatform import FRAMES, SHOT_DIR  # noqa: E402


def run(img, platform, mode):
    eng = Engine()
    eng.set_platform(platform)
    eng.set_mode(mode)
    with contextlib.redirect_stdout(io.StringIO()):
        res = eng.process(img)
    return json.loads(res.result)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="2000,1600,1280,1080,800")
    args = ap.parse_args()
    widths = [int(x) for x in args.sizes.split(",")]

    print(f"{'帧':26s} {'平台':13s} " + " ".join(f"{w:>7d}" for w in widths))
    print("-" * 110)
    for name, platform, mode in FRAMES:
        img = cv2.imread(os.path.join(SHOT_DIR, name))
        if img is None:
            continue
        h0, w0 = img.shape[:2]
        cells = []
        for w in widths:
            s = w / float(w0)
            im = img if abs(s - 1.0) < 1e-6 else cv2.resize(
                img, (w, max(1, int(round(h0 * s)))),
                interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            d = run(im, platform, mode)
            cells.append(f"{d.get('count')}/{d.get('status')[:2]}")
        print(f"{name:26s} {platform:13s} " + " ".join(f"{c:>7s}" for c in cells))


if __name__ == "__main__":
    main()
