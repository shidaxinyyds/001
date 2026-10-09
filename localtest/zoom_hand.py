# -*- coding: utf-8 -*-
"""把某一帧的手牌裁片按 x 区间放大，用于人眼逐张定牌（整行放大后仍看不清时用它）。

用法: py -3.10 -X utf8 localtest/zoom_hand.py tuyou_swap_01 --x0 0.10 --x1 0.62 --scale 3
"""
import argparse
import os
import sys

import cv2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROP_DIR = os.path.join(REPO, "localtest", "shots_multi", "crops")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frame", help="帧名前缀，如 tuyou_swap_01")
    ap.add_argument("--x0", type=float, default=0.0)
    ap.add_argument("--x1", type=float, default=1.0)
    ap.add_argument("--scale", type=int, default=3)
    a = ap.parse_args()

    names = [f for f in os.listdir(CROP_DIR)
             if f.endswith("_hand.jpg") and a.frame in f]
    if len(names) != 1:
        print(f"帧名 {a.frame} 匹配到 {len(names)} 个裁片：{names[:5]}")
        return 2
    img = cv2.imread(os.path.join(CROP_DIR, names[0]))
    if img is None:
        return 2
    h, w = img.shape[:2]
    crop = img[:, int(w * a.x0):int(w * a.x1)]
    out = cv2.resize(crop, None, fx=a.scale, fy=a.scale, interpolation=cv2.INTER_CUBIC)
    path = os.path.join(REPO, "build", f"zoom_{a.frame}_{int(a.x0 * 100)}_{int(a.x1 * 100)}.png")
    cv2.imwrite(path, out)
    print(f"放大图：{path}  {out.shape[1]}x{out.shape[0]}（原区间 {crop.shape[1]}x{crop.shape[0]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
