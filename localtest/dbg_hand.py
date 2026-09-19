"""调试单张截图的手牌带检测内部过程。用法: py -3.10 localtest/dbg_hand.py <img>"""
import os
import sys
import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

path = sys.argv[1]
img = cv2.imread(path)
ih, iw = img.shape[:2]
det = TencentGridDetector()

y_min, y_max = int(ih * 0.68), int(ih * 0.99)
strip = img[y_min:y_max, :]
sh, sw = strip.shape[:2]
hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
is_felt = (hsv[:, :, 0] >= 50) & (hsv[:, :, 0] <= 110) & (hsv[:, :, 1] >= 50)
is_tile = ~is_felt & (hsv[:, :, 2] > 75) & (strip[:, :, 0] > 110) & (strip[:, :, 1] > 110) & (strip[:, :, 2] > 110)
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
mask = cv2.morphologyEx(is_tile.astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel)
contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

print(f"img {iw}x{ih} strip {sw}x{sh} std_tw={0.0547*iw:.1f}")
print(f"tile-pixel ratio in strip: {np.mean(is_tile):.3f}")
boxes = []
for c in contours:
    x, y, bw, bh = cv2.boundingRect(c)
    keep = bh > sh * 0.35 and x < sw * 0.95 and bw >= 25 and x >= sw * 0.05
    if keep:
        boxes.append((x, y + y_min, bw, bh))
boxes.sort()
print(f"kept boxes: {len(boxes)}")
for b in boxes[:30]:
    print("   ", b, "w/tw=%.2f" % (b[2] / (0.0547 * iw)))

res = det.detect_hand_strip(img)
print(f"\ndetect_hand_strip -> {len(res)} tiles, top_score={det.last_top_score:.3f}")
for r, lbl, sc in res:
    print("   ", lbl, round(sc, 3), r)
