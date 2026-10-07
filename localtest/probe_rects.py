# -*- coding: utf-8 -*-
"""打印引擎手牌行每个检出框的 rect，用来定"立牌 vs 混入的弃牌"的几何判据。

背景：手牌条带是从 0.70*h 起做掩码投影定的，残局手牌少的时候，自家牌河会
和手牌在 y 上重叠而被圈进来（实测微乐帧 10 物理 10 张、引擎出 12 槽，多出的
就是左侧 3 张斜放弃牌）。要剔掉它们需要一个不误伤「摸牌位」的判据：摸牌只是
抬高，牌高不变；斜放弃牌被透视压扁，牌高明显小。阈值必须实测，不能拍。

用法: py -3.10 localtest\probe_rects.py 10 1 26 41
输出: localtest/rects_probe.txt
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))

import ab_pitch_classify as AB  # noqa: E402

OUT = os.path.join(HERE, "rects_probe.txt")


def main():
    idxs = sorted(int(a) for a in sys.argv[1:] if a.isdigit()) or [10, 1, 26, 41]
    lines = []
    for idx in idxs:
        f = next((x for x in os.listdir(AB.SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            lines.append(f"[{idx:02d}] 缺帧")
            continue
        img = cv2.imread(os.path.join(AB.SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        lbls, _nc, d = AB.run_img(img, idx, "prod")
        tiles = sorted([t for t in ((d or {}).get("tiles") or []) if len(t) > 5 and t[5] == "hand"],
                       key=lambda t: t[0])
        gt = (AB.GT.get(idx) or "").split()
        lines.append(f"[{idx:02d}] {AB.PLATFORM_OF.get(idx,'?')} 图 {w}x{h} "
                     f"手牌槽={len(tiles)} 物理GT={len(gt)} band={(d or {}).get('detected_band')}")
        hs = sorted(t[3] for t in tiles)
        med = hs[len(hs) // 2] if hs else 0
        for t in tiles:
            x, y, ww, hh = t[0], t[1], t[2], t[3]
            ratio = hh / med if med else 0
            lines.append(f"    x={int(x):4} y={int(y):4} w={int(ww):3} h={int(hh):3} "
                         f"h/中位={ratio:4.2f} bot={int(y + hh):4} lbl={t[4]}")
        if hs:
            lines.append(f"    高度：min={hs[0]} 中位={med} max={hs[-1]}  "
                         f"最矮/中位={hs[0] / med:.2f}")
    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"{len(lines)} 行 -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
