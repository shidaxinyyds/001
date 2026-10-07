# -*- coding: utf-8 -*-
"""探 YOLODetector.detect_strip 实际收到的条带长什么样（为空白护栏定标）。

detect_all_rows 内部会先按 HSV 牌面掩码定位手牌带，定位失败时回退到
「底部 30% 整块」——所以真正喂给模型的条带未必等于 hand_roi 裁片。
本脚本 monkeypatch 记录每次调用的条带统计，不改动生产代码。

用法: py -3.10 localtest\probe_strip_stats.py 09 20 01 13
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition import yolo_detector as YD  # noqa: E402
from engine.engine import Engine  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")
PLATFORM_OF = {i: "weile" for i in range(1, 13)}
PLATFORM_OF[13] = "shushan"
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"

LOG = []
_ORIG = YD.YOLODetector.detect_strip


def patched(self, strip_bgr, offset_x=0, offset_y=0):
    g = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2HSV)
    ivory = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 170))
    rec = {
        "shape": (int(strip_bgr.shape[1]), int(strip_bgr.shape[0])),
        "mean": round(float(g.mean()), 1),
        "std": round(float(g.std()), 1),
        "gt120": round(float((g > 120).mean()) * 100, 2),
        "ivory": round(float(ivory.mean()) * 100, 2),
    }
    out = _ORIG(self, strip_bgr, offset_x, offset_y)
    rec["n_out"] = len(out)
    LOG.append(rec)
    return out


def main():
    YD.YOLODetector.detect_strip = patched
    for arg in sys.argv[1:]:
        idx = int(arg)
        platform = PLATFORM_OF.get(idx, "generic")
        f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            continue
        img = cv2.imread(os.path.join(SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        LOG.clear()
        eng = Engine()
        eng.set_platform(platform)
        import json
        d = json.loads(eng.process(img).result)
        print(f"\n=== calib_{idx:02d} {platform} -> count={d.get('count')} "
              f"status={d.get('status')} ===")
        for r in LOG:
            print(f"  strip {r['shape'][0]}x{r['shape'][1]} mean={r['mean']:5} "
                  f"std={r['std']:5} gt120={r['gt120']:6}% ivory={r['ivory']:6}% "
                  f"-> {r['n_out']} 框")
    return 0


if __name__ == "__main__":
    sys.exit(main())
