# -*- coding: utf-8 -*-
"""打印引擎 result 里 tiles 字段的真实结构，定位 hand_tiles 过滤条件为何失效。

harvest_style_tiles 依赖 `len(t) > 5 and t[5] == "hand"`，帧 14 突然检出 0 张，
必须先看清 tiles 元素到底长什么样，而不是继续猜。

用法: py -3.10 localtest\probe_tiles.py 14
"""
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))

from engine.engine import Engine  # noqa: E402
from montage_for_gt import PLATFORM_OF, SHOTS  # noqa: E402


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    f = next(x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_"))
    img = cv2.imread(os.path.join(SHOTS, f))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    eng = Engine()
    eng.set_platform(PLATFORM_OF.get(idx, "generic"))
    for n in range(1, 5):
        d = json.loads(eng.process(img).result)
        print(f"\n--- frame {n} ---")
        print(f"status={d.get('status')} count={d.get('count')} hand={d.get('hand')!r}")
        print(f"keys={sorted(d.keys())}")
        ts = d.get("tiles")
        print(f"tiles type={type(ts).__name__} len={len(ts) if ts else 0}")
        for t in (ts or [])[:3]:
            print(f"   elem type={type(t).__name__} len={len(t)} -> {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
