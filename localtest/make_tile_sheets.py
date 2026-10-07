# -*- coding: utf-8 -*-
"""给新截图钉 GT：把检出的手牌逐枚裁出来拼成"只编号、不带标签"的图，供人工读图定标。

纪律（docs/new_platform_onboarding.md 第 3 步：任何自动标注不可信）：
  图块上**只写序号，不写检测器的标签与分数**——否则读图时会被自己的答案带偏，
  量出来的"准确率"退化成"检测器和自己一致"，失去意义。检测器的输出只在
  eval_new_material.py 里才登场。
  之所以按检测器的 rect 裁（而不是整条手牌带直接放大）：牌与牌在 mask 里是粘连的，
  逐枚裁开才能看清每张牌的完整牌面；粘连导致的"少一张/多一张"由 eval 脚本的
  张数对比负责，两件事分开测。

裁片左右各扩 12%（可看到相邻牌边缘，便于判断切片是否吃掉了牌面）；
统一放大到高 300px，**每行最多 7 块、一行一帧**：14 块横排会宽到 2400px，
查看时被等比缩小后筒子点数就不够分辨了；而拆成两张表会让“钉一帧 GT”变成两次
看图 + 两次合并，上百帧时这个成本不可忽略。所以排成 2 行×7 列的一帧一表：
宽度仍≈1600px（每块实得 220px+），一屏读完一帧。

运行：py -3.10 -X utf8 localtest/make_tile_sheets.py 17,22,31,49
      py -3.10 -X utf8 localtest/make_tile_sheets.py --src public/1 --platform zj_sichuan
输出：build/ann[_平台]/sheet_NN.png（派生物）+ rects_NN.json（读完图再看）
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

SRC = os.path.join(REPO, "public", "0")
ROW_H = 300
PAD = 12
PER_ROW = 7          # 每行块数：一行 7 块 ≈ 1600px 宽，一帧一表


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("idxs", nargs="?", default="",
                    help="逗号分隔的帧序号（按目录内排序）；与 --platform 二选一")
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--platform", default=None,
                    help="按 localtest/assign_platforms.py 的平台真值选帧（只取未收割、"
                         "未钉 GT 的可用帧），并自动把表头标上平台名")
    ap.add_argument("--map", default=None,
                    help="frame_platforms_<tag>.json 路径，配合 --platform 使用")
    ap.add_argument("--out", default=None, help="默认 build/ann[_平台]")
    ap.add_argument("--band", action="store_true",
                    help="额外导出整条手牌带（原分辨率 + 把序号画在框上）。逐枚裁片能看清牌面，"
                         "但看不出“这一行到底几枚、有没有副露”——检出数不是 13/14 的帧必须靠"
                         "整条带人工数一遍才能定 GT 张数（否则 GT 会跟着检测器漏检走）。")
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    if not os.path.isdir(src):
        print(f"截图目录不存在：{src}\n（新素材未就位时本脚本无事可做）")
        return 2
    all_files = sorted(f for f in os.listdir(src) if f.lower().endswith((".jpg", ".png")))
    if a.platform:
        mp = a.map or os.path.join(REPO, "build",
                                   f"frame_platforms_{os.path.basename(src)}.json")
        if not os.path.exists(mp):
            print(f"平台真值表不存在：{mp}（先跑 localtest/assign_platforms.py）")
            return 2
        with open(mp, encoding="utf-8") as fp:
            tbl = json.load(fp)
        names = [f for f in all_files
                 if tbl.get(f, {}).get("platform") == a.platform
                 and not tbl.get(f, {}).get("dup_kind")]
        if not names:
            print(f"平台 {a.platform} 没有可用新帧")
            return 2
    elif a.idxs:
        names = [all_files[int(x) - 1] for x in a.idxs.split(",")]
    else:
        print(f"共 {len(all_files)} 帧，用法：make_tile_sheets.py 17,22"
              f" 或 --platform zj_sichuan")
        return 0
    out = a.out or os.path.join(REPO, "build", "ann" + (f"_{a.platform}" if a.platform else ""))
    os.makedirs(out, exist_ok=True)
    det = TencentGridDetector()
    for i, name in enumerate(names, 1):
        img = cv2.imread(os.path.join(src, name))
        h, w = img.shape[:2]
        dets = det.detect_hand_strip(img) or []
        dets.sort(key=lambda d: d[0][0])
        tiles = []
        for (x, y, bw, bh), _lbl, _sc in dets:
            ex = int(bw * 0.12)
            crop = img[y:y + bh, max(0, x - ex):min(w, x + bw + ex)]
            if crop.size == 0:
                continue
            sc = ROW_H / crop.shape[0]
            tiles.append(cv2.resize(crop, (max(1, int(round(crop.shape[1] * sc))), ROW_H),
                                    interpolation=cv2.INTER_CUBIC))
        if not tiles:
            print(f"[{i:02d}] {name}: 检出 0 张，跳过（先查 detect_hand_strip）")
            continue
        rows = [tiles[p:p + PER_ROW] for p in range(0, len(tiles), PER_ROW)]
        width = max(sum(t.shape[1] + PAD for t in r) + PAD for r in rows)
        BAND = 46                            # 每行上方的序号带
        canvas_h = 30 + (BAND + ROW_H) * len(rows)
        sheet = np.full((canvas_h + PAD, width, 3), 32, np.uint8)
        cv2.putText(sheet, f"#{i:02d} {name[:16]} n={len(tiles)}", (8, 21),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
        n = 0
        for ri, r in enumerate(rows):
            y_top = 30 + ri * (BAND + ROW_H)
            cx = PAD
            for t in r:
                n += 1
                sheet[y_top + BAND:y_top + BAND + ROW_H, cx:cx + t.shape[1]] = t
                cv2.putText(sheet, str(n), (cx + t.shape[1] // 2 - 16, y_top + 36),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)
                cv2.line(sheet, (cx - PAD // 2, y_top), (cx - PAD // 2, sheet.shape[0]),
                         (90, 90, 90), 1)
                cx += t.shape[1] + PAD
        png = os.path.join(out, f"sheet_{i:02d}.png")
        cv2.imwrite(png, sheet)
        if a.band:
            rr = [d[0] for d in dets]
            # 横向取全宽：只框到“检出的首尾牌”会把行尾的漏检牌直接裁掉，
            # 而整带图的目的就是人工数一遍到底几枚。
            bx0, bx1 = 0, w
            by0 = max(0, min(r[1] for r in rr) - 45)
            by1 = min(h, max(r[1] + r[3] for r in rr) + 45)
            band = img[by0:by1, bx0:bx1].copy()
            for n2, r in enumerate(rr, 1):
                p0 = (r[0] - bx0, r[1] - by0)
                cv2.rectangle(band, p0, (p0[0] + r[2], p0[1] + r[3]), (0, 220, 255), 2)
                cv2.putText(band, str(n2), (p0[0] + 4, p0[1] + 36),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 60, 255), 3)
            cv2.imwrite(os.path.join(out, f"band_{i:02d}.png"), band)
        print(f"[{i:02d}] {name} {w}x{h} {len(tiles)} 张 -> {png}")
        with open(os.path.join(out, f"rects_{i:02d}.json"), "w", encoding="utf-8") as fp:
            json.dump({"frame": i, "file": name, "size": [w, h], "n_tiles": len(tiles),
                       "rects": [[int(v) for v in r] for r, _l, _s in dets]}, fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
