# -*- coding: utf-8 -*-
"""把每个无监督分组的「平台水印条」按原分辨率裁出来拼成一张表，供人工读字定名。

为什么单独做这一步：cluster_frames.py 能分组但叫不出名字，而名字只能来自画面里的
字样（水印/桌布标题）。在 480px 缩略联系表上中文字常常糊到看不清，于是“这组是雀神
还是蜀山”就变成猜——猜错代价已经踩过（把蜀山当雀神收割会废掉整套模板）。这里按
**原分辨率**裁水印带再放大显示，字是清晰的，判断才有依据。

裁切区域是相对坐标：这批横屏素材的桌布水印都落在画面中部偏下（牌河与底分之间）。
不同平台水印位置略有差异，所以取一条较宽的带（x 24%~56%，y 52%~82%）覆盖全部，
而不是一格一格调。

用法：
    py -3.10 -X utf8 localtest/watermark_sheet.py --src public/1 \
        --groups build/frame_clusters_1/groups.txt
输出：build/frame_clusters_<tag>/wm_NN.png
"""
import argparse
import os
import re
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# 水印带（相对坐标）：横屏桌布游戏的“XX麻将”字样基本都在这条带里
X0, X1, Y0, Y1 = 0.24, 0.56, 0.52, 0.82
LINE = re.compile(r"^(G\d+): (\d+) 帧.*代表 #(\d+) (\S+)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO, "public", "1"))
    ap.add_argument("--groups", required=True)
    ap.add_argument("--width", type=int, default=1000)
    ap.add_argument("--per-sheet", type=int, default=12)
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    rows = []
    with open(a.groups, encoding="utf-8") as fp:
        for ln in fp:
            m = LINE.match(ln.strip())
            if m:
                rows.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)))
    if not rows:
        print("groups.txt 里没解析到任何组")
        return 2

    out_dir = os.path.dirname(os.path.abspath(a.groups))
    sheets, n = [], 0
    for start in range(0, len(rows), a.per_sheet):
        chunk = rows[start:start + a.per_sheet]
        # 先量后拼：行高 = 本屏最高的裁条（水印带在不同画幅下高宽比不同，
        # 写死行高会在某个分辨率上把字切掉）
        strips = []
        for gid, cnt, idx, fname in chunk:
            img = cv2.imread(os.path.join(src, fname))
            if img is None:
                strips.append((gid, cnt, idx, None))
                continue
            h, w = img.shape[:2]
            crop = img[int(h * Y0):int(h * Y1), int(w * X0):int(w * X1)]
            ch, cw = crop.shape[:2]
            rz = cv2.resize(crop, (a.width, max(1, int(round(ch * a.width / float(cw))))),
                            interpolation=cv2.INTER_CUBIC)
            strips.append((gid, cnt, idx, rz))
        row_h = (max([s[3].shape[0] for s in strips if s[3] is not None] or [200]) + 26)
        canvas = np.full((len(chunk) * row_h, a.width, 3), 30, np.uint8)
        for i, (gid, cnt, idx, rz) in enumerate(strips):
            y0 = i * row_h
            cv2.putText(canvas, f"{gid} n={cnt} #{idx}", (6, y0 + 19),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 255), 2)
            if rz is None:
                continue
            canvas[y0 + 24:y0 + 24 + rz.shape[0], :rz.shape[1]] = rz
            cv2.line(canvas, (0, y0 + row_h - 2), (a.width, y0 + row_h - 2),
                     (90, 90, 90), 1)
        n += 1
        out = os.path.join(out_dir, f"wm_{n:02d}.png")
        cv2.imwrite(out, canvas)
        sheets.append(out)
    for s in sheets:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
