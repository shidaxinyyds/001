# -*- coding: utf-8 -*-
"""把一批截图拼成带编号的联系表（contact sheet），供人工逐屏看图定平台身份。

为什么需要它：风格探针只能回答“这帧的牌面最像哪个已挂 bank”，**答不出“这是哪个
游戏”**。没挂 bank 的平台（如指尖四川）会被归到最像的旧风格里，于是“有没有这个
平台的素材”这个问题光看探针输出无法回答，必须看画面。而 141 帧逐帧打开看代价太
高，拼成 16 格/屏的联系表后，几屏就能扫完，靠桌布颜色、按钮排布、水印字样定身份，
再对可疑帧回到原图确认。

口径：格子里**只写序号与原文件名前 8 位**，不写探针风格——避免我先看到机器答案
再“看图找理由”（确认偏误）。探针结论单独落在 index.txt 里，扫完图再对。

用法：
    py -3.10 -X utf8 localtest/make_contact_sheets.py --src public/1
输出：build/contact_<目录名>/sheet_NN.png + index.txt
"""
import argparse
import collections
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO, "public", "1"))
    ap.add_argument("--cell-w", type=int, default=480, help="每格宽（像素）")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--rows", type=int, default=4)
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    tag = os.path.basename(src) or "root"
    out_dir = os.path.join(REPO, "build", f"contact_{tag}")
    os.makedirs(out_dir, exist_ok=True)

    files = sorted(f for f in os.listdir(src)
                   if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if not files:
        print(f"素材目录空：{src}")
        return 2

    cw = a.cell_w
    per = a.cols * a.rows
    index = []
    cell_h = None

    sheets = []
    for start in range(0, len(files), per):
        chunk = files[start:start + per]
        # 第一格先量高度：按第一帧的宽高比定格高，整屏统一（不同画幅混排时留黑边，
        # 不强行拉伸——拉伸会让“牌看起来更方”，正是我要判断的东西之一）
        probe = cv2.imread(os.path.join(src, chunk[0]))
        if probe is None:
            cell_h = 220
        else:
            cell_h = int(round(cw * probe.shape[0] / probe.shape[1]))
        canvas = np.full((a.rows * cell_h, a.cols * cw, 3), 40, np.uint8)
        for i, f in enumerate(chunk):
            img = cv2.imread(os.path.join(src, f))
            idx = start + i + 1
            if img is None:
                continue
            h, w = img.shape[:2]
            scale = cw / float(w)
            rh = max(1, int(round(h * scale)))
            small = cv2.resize(img, (cw, min(rh, cell_h)))
            r, c = divmod(i, a.cols)
            y0, x0 = r * cell_h, c * cw
            canvas[y0:y0 + small.shape[0], x0:x0 + cw] = small
            # 半透明底条 + 序号：保证黑桌布上序号也可读
            cv2.rectangle(canvas, (x0, y0), (x0 + 150, y0 + 34), (0, 0, 0), -1)
            cv2.putText(canvas, f"#{idx}", (x0 + 6, y0 + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            index.append((idx, f, f"{w}x{h}", os.path.getsize(os.path.join(src, f))))
        out = os.path.join(out_dir, f"sheet_{len(sheets) + 1:02d}.png")
        cv2.imwrite(out, canvas)
        sheets.append(out)

    with open(os.path.join(out_dir, "index.txt"), "w", encoding="utf-8") as fp:
        fp.write("序号\t文件名\t分辨率\t字节\n")
        for idx, f, res, sz in index:
            fp.write(f"{idx}\t{f}\t{res}\t{sz}\n")
        # 分辨率分布：同一平台通常同一画幅，先按它切候选组
        cnt = collections.Counter(r for _i, _f, r, _s in index)
        fp.write("\n分辨率分布：\n")
        for res, n in cnt.most_common():
            fp.write(f"  {res}: {n} 帧\n")
    print(f"{len(files)} 帧 → {len(sheets)} 屏联系表：{out_dir}")
    for s in sheets:
        print("  ", os.path.basename(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
