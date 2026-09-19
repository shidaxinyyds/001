"""牌河识别改进实验：对比不同形态学闭核/尺寸归一化策略对牌河检出数与置信度的影响。
用法: py -3.10 localtest/exp_river.py
"""
import os
import sys
import glob
import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from engine.engine import mpsz_to_tile34_index, available_set  # noqa: E402

det = TencentGridDetector()
AVAIL = available_set("sc_hz")


def zones_of(ih, iw):
    return [
        ('bottom', int(iw*0.32), int(ih*0.52), int(iw*0.68), int(ih*0.72)),
        ('top', int(iw*0.32), int(ih*0.16), int(iw*0.68), int(ih*0.36)),
        ('left', int(iw*0.22), int(ih*0.30), int(iw*0.44), int(ih*0.64)),
        ('right', int(iw*0.56), int(ih*0.30), int(iw*0.78), int(ih*0.64)),
    ]


def river_v2(img, close_k=9, min_side=28, thr=0.42):
    ih, iw = img.shape[:2]
    out = []
    for name, x1, y1, x2, y2 in zones_of(ih, iw):
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        white = (hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 115) & (crop[:, :, 0] > 65) & (crop[:, :, 1] > 65) & (crop[:, :, 2] > 65)
        is_beacon = (hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 120)
        white[is_beacon] = 0
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (close_k, close_k))
        mask = cv2.morphologyEx(white.astype('uint8'), cv2.MORPH_CLOSE, k)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw < min_side or bh < min_side:
                continue
            asp = bw / float(bh)
            if not (0.55 <= asp <= 1.25):   # 牌河单张牌近似方形略竖
                continue
            t = crop[by:by+bh, bx:bx+bw]
            lbl, sc = det.classify_tile(t)
            try:
                if lbl and sc >= thr and mpsz_to_tile34_index(lbl) in AVAIL:
                    out.append((x1+bx, y1+by, lbl, round(sc, 3)))
            except Exception:
                pass
    # NMS
    out.sort(key=lambda d: -d[3])
    fin = []
    for gx, gy, lbl, sc in out:
        if any(abs(gx-ex[0]) < 24 and abs(gy-ex[1]) < 24 for ex in fin):
            continue
        fin.append((gx, gy, lbl, sc))
    return [f[2] for f in fin]


def main():
    files = sorted(glob.glob(os.path.join(REPO, "localtest", "shots", "*.jpg")))
    tot_v2 = 0
    for f in files:
        img = cv2.imread(f)
        v2 = river_v2(img)
        tot_v2 += len(v2)
        print(os.path.basename(f)[2:14], "v2:", len(v2), v2[:12])
    print("TOTAL v2 river tiles:", tot_v2)


if __name__ == "__main__":
    main()
