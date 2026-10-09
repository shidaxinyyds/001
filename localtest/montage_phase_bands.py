# -*- coding: utf-8 -*-
"""把每一帧的「阶段证据带」（画面中央那块文字/按钮区）拼成一张联屏，人眼定阶段。

为什么还要再走一遍人眼：阶段真值是我上一轮**看整图**填进守卫表的，结果把蜀山一帧
「选择三张同花色手牌」标成了定缺（它的色盘带里根本没有盘 —— 那帧是换三张）。
真值错了，后面所有「漏判/误判」的结论都会跟着错，所以先把证据带单独放大重看一遍。
"""
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(REPO, "localtest", "shots_report")

names = [f for f in sorted(os.listdir(SHOT)) if f.endswith(".jpg") and f != "material_bank_01.jpg"]
if len(sys.argv) > 1:
    names = [n for n in names if any(a in n for a in sys.argv[1:])]

rows = []
for n in names:
    img = cv2.imread(os.path.join(SHOT, n))
    ih, iw = img.shape[:2]
    # 中央证据带：y 0.50~0.78（定缺盘/换牌按钮/桌面提示语都在这），x 全宽
    band = img[int(ih * 0.50):int(ih * 0.78), :]
    band = cv2.resize(band, (1000, int(band.shape[0] * 1000.0 / band.shape[1])))
    cv2.putText(band, n, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 255), 2)
    rows.append(band)
out = os.path.join(REPO, "build", "phase_bands.png")
cv2.imwrite(out, np.vstack(rows))
print("写出", out, sum(r.shape[0] for r in rows), "行高")
