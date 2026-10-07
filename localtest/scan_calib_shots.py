# -*- coding: utf-8 -*-
"""对 shots_calib 全量截图做元数据盘点：分辨率、长宽比、手牌带位置估计。

手牌带位置用「底部 HSV 白牌面掩码的行投影」粗估，用来核对各平台预设的
hand_roi 是否真的框住了手牌行——不依赖任何识别模型，纯几何。

用法: py -3.10 localtest/scan_calib_shots.py
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots_calib")


def hand_band_rows(img):
    """返回牌面（高亮近白）像素行占比 > 阈值的连续行区间（图像坐标）。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 牌面：高亮度 + 低饱和（ivory 白）
    m = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 170)).astype(np.uint8)
    rows = m.sum(axis=1).astype(np.float32)
    w = img.shape[1]
    frac = rows / max(1, w)
    thr = 0.22
    bands, y = [], 0
    while y < len(frac):
        if frac[y] >= thr:
            y0 = y
            while y < len(frac) and frac[y] >= thr:
                y += 1
            if y - y0 >= 30:
                bands.append((y0, y, float(frac[y0:y].max())))
        else:
            y += 1
    return bands


def main():
    files = sorted(f for f in os.listdir(SHOTS) if f.lower().endswith(".jpg"))
    print(f"{'file':28} {'WxH':>11} {'ar':>5}  bottom-band(top,bottom as frac of H)")
    for f in files:
        img = cv2.imread(os.path.join(SHOTS, f))
        if img is None:
            print(f"{f:28} READ FAIL")
            continue
        h, w = img.shape[:2]
        bands = hand_band_rows(img)
        # 只看屏幕下半部（手牌一定在下方），取最靠下的一条
        low = [b for b in bands if b[1] > h * 0.55]
        tag = "none"
        if low:
            y0, y1, pk = low[-1]
            tag = f"{y0/h:.3f}~{y1/h:.3f} (peak {pk:.2f})"
        print(f"{f:28} {w}x{h:<6} {w/h:5.2f}  {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
