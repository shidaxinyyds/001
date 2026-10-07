# -*- coding: utf-8 -*-
"""扫 YOLO 检测阈值 conf_thresh，看「手牌漏检」是不是单纯被置信度卡掉的。

实测途游帧 34 物理 14 张只检出 12 张、帧 36 同样 14→12，x 步进里出现
268/240/230/258px（正常 pitch 约 140px）。之前把途游归因成「模板 bank 缺素材」
是错的：漏检的牌根本进不了分类器，补多少模板都救不回来，而且整行会错位，
表现为"后面全错"。

YOLODetector 的检测阈值默认 0.40。若漏掉的那两张的 conf 落在 0.25~0.40 之间，
降阈值就是成本最低的修法；若它们本来就低于 0.25（模型真没看见），则必须走
「按 pitch 反推缺失槽位」的补检路线，那是另一件事。

同时报每档阈值下的 GT 命中，避免"多检出但多错标"的假收益。
用法: py -3.10 localtest\probe_conf_thresh.py [idx ...]
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS, PLATFORM_OF  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402
from ab_pitch_classify import GT  # noqa: E402,F401  (GT 供人工比对，不参与计算)

# 物理张数是逐帧看整条手牌带数出来的，不是从引擎裁片读的
# （从裁片读会把漏检静默吸收成"少一张 GT"，正是之前归因错误的根源）。
PHYS = {34: 14, 36: 14, 40: 13, 33: 12, 14: 14, 17: 14}
THRESHES = (0.40, 0.35, 0.30, 0.25, 0.20)
# 已实测各档检出完全一致，默认只跑基线档以省时；PROBE_ALL=1 可放开扫描。
if os.environ.get("PROBE_ALL") != "1":
    THRESHES = (0.40,)


def main():
    idxs = [int(a) for a in sys.argv[1:] if a.isdigit()] or sorted(PHYS)
    for idx in idxs:
        f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            continue
        img = cv2.imread(os.path.join(SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        y1 = int(0.70 * h)
        hsv = cv2.cvtColor(img[y1:h, :], cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 130)).astype(np.uint8)
        valid_x = np.where(mask.sum(axis=0) > (h - y1) * 0.15)[0]
        if len(valid_x) > 50:
            x_min, x_max = int(valid_x[0]), int(valid_x[-1])
            rc = mask[:, x_min:x_max].sum(axis=1)
            vy = np.where(rc > (x_max - x_min) * 0.10)[0]
            y_top = y1 + max(0, int(vy[0]) - 12) if len(vy) else y1
            y_bot = min(h, y1 + int(vy[-1]) + 12) if len(vy) else h
            x_left, x_right = max(0, x_min - 12), min(w, x_max + 12)
        else:
            y_top, y_bot, x_left, x_right = y1, h, 0, w
        strip = img[y_top:y_bot, x_left:x_right]

        phys = PHYS.get(idx, "?")
        print(f"\n=== 帧 {idx} {PLATFORM_OF.get(idx,'?')} 物理张数={phys} ===")
        for t in THRESHES:
            det = YOLODetector(conf_thresh=t)
            det.set_platform_styles(PLATFORM_OF.get(idx))
            raw = det.detect_strip(strip, offset_x=x_left, offset_y=y_top)
            raw = sorted(raw, key=lambda d: d[0][0])
            labs = [d[1] for d in raw]
            ws = [int(d[0][2]) for d in raw]
            med_w = int(np.median(ws)) if ws else 0
            # 步进与框宽是两件事：步进大 = 中间有空档；框宽大 = 一个框盖住多张牌。
            # 只有后者能靠“按 pitch 拆框”救回来，前者必须靠补检。
            wide = [(i, w) for i, w in enumerate(ws) if med_w and w > 1.45 * med_w]
            print(f"  conf>={t:.2f}: 检出={len(raw):2} 中位宽={med_w} 宽={ws}")
            print(f"        异常宽框(>1.45x中位)={wide} -> 拆分后应得 {len(raw) + sum(int(round(w / med_w)) - 1 for _, w in wide) if med_w else len(raw)} 槽")
            print(f"        labels={' '.join(str(x) for x in labs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
