# -*- coding: utf-8 -*-
"""因果验证：分别放开 aspect 下限 / 取消 hand_roi 二次裁切，看失效帧能否恢复。

失效链路假设（两条互相掩盖）：
  A) 引擎先按 hand_roi 裁一条 → YOLODetector.detect_all_rows 内部又硬编码
     只看裁片底部 30%（y1 = 0.70*h）→ 立牌被纵向截断 → 检出数掉到 10 张以下
     → engine 的 `len(raw_labels) in hsizes or >= 10` 门不过 → _match_started
     始终 False → 末尾把整副手牌清空。
  B) 若不裁切（整屏识别），立牌以真实比例被检出，但雀神/JJ 立牌 aspect≈0.475
     低于全局 MIN_TILE_ASPECT=0.52 → _apply_conf 逐张判死 → 同样 raw_labels 空。

本脚本只改测试进程内的常量/入参，不落任何生产代码改动。

用法: py -3.10 localtest\causality_test.py 14 20 24 34 36
"""
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import engine.engine as E  # noqa: E402
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

ORIG_ASPECT_MIN = E.MIN_TILE_ASPECT


def run(img, platform, roi, aspect_min=None):
    if aspect_min is not None:
        E.MIN_TILE_ASPECT = aspect_min
    try:
        eng = Engine()
        eng.set_platform(platform)
        if roi is not None:
            eng._roi = roi
        d = json.loads(eng.process(img).result)
        tiles = [t for t in (d.get("tiles") or []) if len(t) > 5 and t[5] == "hand"]
        return d.get("count"), d.get("status"), "".join(t[4] for t in tiles if t[4])
    finally:
        E.MIN_TILE_ASPECT = ORIG_ASPECT_MIN


def main():
    idxs = [int(a) for a in sys.argv[1:]] or sorted(
        int(f.split("_")[1][:2]) for f in os.listdir(SHOTS) if f.endswith(".jpg"))
    print(f"{'idx':>3} {'platform':11} {'预设':>14} {'仅放aspect0.42':>16} "
          f"{'仅不裁切':>14} {'两者都放开':>16}")
    for idx in idxs:
        platform = PLATFORM_OF.get(idx, "generic")
        f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            continue
        img = cv2.imread(os.path.join(SHOTS, f))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        a = run(img, platform, None)
        b = run(img, platform, None, aspect_min=0.42)
        c = run(img, platform, (0.0, 1.0))
        d = run(img, platform, (0.0, 1.0), aspect_min=0.42)
        print(f"{idx:3d} {platform:11} "
              f"{str(a[0]) + '/' + a[1]:>14} "
              f"{str(b[0]) + '/' + b[1]:>16} "
              f"{str(c[0]) + '/' + c[1]:>14} "
              f"{str(d[0]) + '/' + d[1]:>16}")
        if d[0] and d[0] > 0:
            print(f"      放开后手牌: {d[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
