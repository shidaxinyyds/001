# -*- coding: utf-8 -*-
"""扫覆盖层放行线 COVER_GATE，看放行线该往哪边挪。

放行线现在的语义是「ref_conf >= gate 就无条件夺走 YOLO 标签」，所以它同时
决定两类错误，且方向相反：
  gate 调低 → 覆盖层更敢判 → 救回 YOLO 的错，但也会把本来对的抢错；
  gate 调高 → 覆盖层更保守 → 保住 YOLO 的对，但牌风不适配时整行没人纠偏。
不实测就调是猜。本脚本对**已有 GT 的帧**逐档跑生产链路，报总命中与逐帧变化。

用法: py -3.10 localtest\sweep_gate.py [idx ...]
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))

from recognition.yolo_detector import YOLODetector  # noqa: E402
import ab_pitch_classify as AB  # noqa: E402

GATES = (0.30, 0.35, 0.40, 0.45, 0.50)
# 引擎自身会打大量 stdout 日志，控制台回显会把结果行顶出捕获窗口，
# 所以结果一律落盘。
OUT = os.path.join(HERE, "gate_sweep.txt")


def main():
    args = [int(a) for a in sys.argv[1:] if a.isdigit()]
    idxs = args or sorted(AB.GT)
    # 逐张读一次图并缓存，避免每档 gate 重复解码（同一帧的几何与 gate 无关）
    imgs = {}
    for idx in idxs:
        f = next((x for x in os.listdir(AB.SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            continue
        img = cv2.imread(os.path.join(AB.SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        imgs[idx] = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    print(f"可判帧 {len(imgs)} 张: {' '.join(str(i) for i in sorted(imgs))}")
    lines = [f"可判帧 {len(imgs)} 张: {' '.join(str(i) for i in sorted(imgs))}",
             f"{'gate':>5} {'总命中':>9}  逐帧"]
    base = None
    for g in GATES:
        YOLODetector.COVER_GATE = g
        hits, per = 0, []
        for idx, img in sorted(imgs.items()):
            lbls, _nc, _d = AB.run_img(img, idx, "prod")
            s = AB.score(lbls or [], AB.GT.get(idx))
            if "/" in s:
                a, b = s.split("/")
                hits += int(a)
                per.append(f"{idx}:{a}/{b.strip()}")
        row = f"{g:5.2f} {hits:9}  {' '.join(per)}"
        print(row)
        lines.append(row)
        if abs(g - 0.40) < 1e-9:
            base = hits
    YOLODetector.COVER_GATE = 0.40
    lines.append(f"基线(0.40)={base}；各档相对基线的增减即放行线的净收益。")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"结果已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
