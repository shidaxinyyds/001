"""可视化牌河检测：画出四方扫描区、白掩码、检出框。
用法: py -3.10 localtest/viz_river.py <img> <out.png>"""
import os
import sys
import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from engine.engine import detect_river_discards  # noqa: E402

path, out = sys.argv[1], sys.argv[2]
img = cv2.imread(path)
ih, iw = img.shape[:2]
det = TencentGridDetector()

zones = [
    ('bottom', int(iw*0.32), int(ih*0.52), int(iw*0.68), int(ih*0.72)),
    ('top', int(iw*0.32), int(ih*0.16), int(iw*0.68), int(ih*0.36)),
    ('left', int(iw*0.22), int(ih*0.30), int(iw*0.44), int(ih*0.64)),
    ('right', int(iw*0.56), int(ih*0.30), int(iw*0.78), int(ih*0.64)),
]
canvas = img.copy()
colors = {'bottom': (0,0,255), 'top': (0,255,0), 'left': (255,0,0), 'right': (0,255,255)}
for name, x1, y1, x2, y2 in zones:
    cv2.rectangle(canvas, (x1, y1), (x2, y2), colors[name], 2)
    cv2.putText(canvas, name, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colors[name], 2)

hr = det.detect_hand_strip(img)
for r, lbl, sc in hr:
    cv2.rectangle(canvas, (r[0], r[1]), (r[0]+r[2], r[1]+r[3]), (255,0,255), 1)
    cv2.putText(canvas, lbl, (r[0], r[1]-3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,0,255), 1)

riv = detect_river_discards(img, det, mode='sc_hz', hand_row=hr)
print("river:", riv)
cv2.imwrite(out, canvas)
print("saved", out, "hand", len(hr))
