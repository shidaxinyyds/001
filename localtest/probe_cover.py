# -*- coding: utf-8 -*-
"""绕过引擎，直接看 YOLODetector 覆盖层给出的手牌 label 分布。

假设：新版 queshen bank 让等距重切后的 14 个 patch 被判成「同一张牌 >=5 枚」，
于是引擎按物理不可能（每种牌最多 4 张）拒掉整手，表现为 status=waiting / count=0
——而不是「识别成了错的牌」。若成立，说明风格 bank 的质量问题会以「整手消失」
的形式暴露，比误识别更严重。

用法: py -3.10 localtest\probe_cover.py 14
"""
import os
import sys
from collections import Counter

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS, PLATFORM_OF  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    plat = PLATFORM_OF.get(idx, "generic")
    f = next(x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_"))
    img = cv2.imread(os.path.join(SHOTS, f))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    det = YOLODetector()
    det.set_platform_styles(plat)
    rows = det.detect_all_rows(img)
    if not rows:
        print("detect_all_rows 没有返回任何行")
        return 1
    hand = rows[0]
    labs = [t[1] for t in hand]
    print(f"帧 {idx} 平台 {plat} 手牌行 {len(hand)} 枚")
    print(f"  labels = {' '.join(str(x) for x in labs)}")
    dup = {k: v for k, v in Counter(labs).items() if v >= 4}
    print(f"  同牌 >=4 的: {dup or '无'}")
    print(f"  drawn={det.last_drawn_tile} top={det.last_top_score}")

    # 等距假设体检：rows[0] 已经是重切后的等距框，看不出真相，
    # 必须拿 YOLO 的原始框。下面这段与 detect_all_rows 的条带定位一致。
    h, w = img.shape[:2]
    y1 = int(0.70 * h)
    roi = img[y1:h, :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 130)).astype(np.uint8)
    col_counts = mask.sum(axis=0)
    valid_x = np.where(col_counts > (h - y1) * 0.15)[0]
    if len(valid_x) > 50:
        x_min, x_max = int(valid_x[0]), int(valid_x[-1])
        row_counts = mask[:, x_min:x_max].sum(axis=1)
        valid_y = np.where(row_counts > (x_max - x_min) * 0.10)[0]
        y_top = y1 + max(0, int(valid_y[0]) - 12) if len(valid_y) > 0 else y1
        y_bot = min(h, y1 + int(valid_y[-1]) + 12) if len(valid_y) > 0 else h
        x_left, x_right = max(0, x_min - 12), min(w, x_max + 12)
    else:
        y_top, y_bot, x_left, x_right = y1, h, 0, w
    raw = det.detect_strip(img[y_top:y_bot, x_left:x_right],
                           offset_x=x_left, offset_y=y_top)
    print(f"\n  YOLO 原始框 {len(raw)} 枚（条带 y {y_top}~{y_bot}, x {x_left}~{x_right}）")
    rx = [d[0][0] for d in raw]
    ry = [d[0][1] for d in raw]
    rw = [d[0][2] for d in raw]
    order = np.argsort(rx)
    print("   x=" + " ".join(str(int(rx[i])) for i in order))
    print("   y=" + " ".join(str(int(ry[i])) for i in order))
    print("   w=" + " ".join(str(int(rw[i])) for i in order))
    steps = [int(rx[order[i + 1]] - rx[order[i]]) for i in range(len(order) - 1)]
    if steps:
        med = float(np.median(steps))
        print(f"   步进中位={med:.1f} 最大偏差={max(abs(s - med) for s in steps):.1f}px  "
              f"y 极差={max(ry) - min(ry)}px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
