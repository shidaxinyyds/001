"""渲染手牌带检测调试图：画出手牌带、tile掩码、kept boxes、均分网格。
用法: py -3.10 localtest/viz_hand.py <img> <out.png>"""
import os
import sys
import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

path, out = sys.argv[1], sys.argv[2]
img = cv2.imread(path)
ih, iw = img.shape[:2]
det = TencentGridDetector()

y_min = int(ih * 0.68)
strip = img[y_min:int(ih * 0.99), :]
sh, sw = strip.shape[:2]
hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
is_felt = (hsv[:, :, 0] >= 50) & (hsv[:, :, 0] <= 110) & (hsv[:, :, 1] >= 50)
is_tile = ~is_felt & (hsv[:, :, 2] > 75) & (strip[:, :, 0] > 110) & (strip[:, :, 1] > 110) & (strip[:, :, 2] > 110)

canvas = img.copy()
# 掩码可视化（红）
overlay = canvas.copy()
mm = (is_tile.astype(np.uint8) * 255)
mm_full = np.zeros((ih, iw), np.uint8)
mm_full[y_min:y_min + sh, :] = mm
overlay[mm_full > 0] = (0, 0, 255)
canvas = cv2.addWeighted(canvas, 0.75, overlay, 0.25, 0)

# 列投影，找真实牌边界
col = np.sum(is_tile, axis=0)
cv2.rectangle(canvas, (0, y_min), (iw, int(ih*0.99)), (255, 255, 0), 1)
# 均分网格 std_tw
std_tw = 0.0547 * iw
for i in range(int(iw // std_tw) + 1):
    x = int(i * std_tw)
    cv2.line(canvas, (x, y_min), (x, int(ih*0.99)), (0, 255, 255), 1)

cv2.imwrite(out, canvas)
# 列投影文本图（每20px一个字符）
print("col profile (每格=20px, 数字=该列tile像素数/20):")
row = ""
for x in range(0, sw, 20):
    v = int(col[x:x+20].mean() / 20)
    row += str(min(v, 9))
print(row)
print("saved", out)
