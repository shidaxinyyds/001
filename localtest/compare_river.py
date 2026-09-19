# -*- coding: utf-8 -*-
"""E · 牌河双路径离线对比实验：轮廓法(生产) vs YOLO 条带法(候选)。

对同一批真实截图并跑两条牌河识别路径，逐帧报告张数/标签多重集差异，
汇总一致率，可选输出 YOLO 框可视化（--vis）供肉眼核验。
只做离线评测，不改任何生产链路。

用法:
  py -3.10 localtest/compare_river.py                       # 跑 localtest/shots
  py -3.10 localtest/compare_river.py --dir localtest/river_real
  py -3.10 localtest/compare_river.py --vis localtest/river_vis
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from engine.engine import detect_river_discards  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402


def canon(labels):
    return Counter(l for l in labels if l)


def draw(img, dets, color, tag):
    for (x, y, w, h), lbl, conf in dets:
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        cv2.putText(img, f"{tag}{lbl}{conf:.2f}", (x, max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(REPO, "localtest", "shots"),
                    help="真实截图目录（jpg）")
    ap.add_argument("--vis", default="", help="可选：可视化输出目录")
    ap.add_argument("--conf", type=float, default=0.35)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "*.jpg"))) + \
        sorted(glob.glob(os.path.join(args.dir, "*.png")))
    if not files:
        print(f"[compare] 目录无截图: {args.dir}")
        return 2

    tgd = TencentGridDetector()
    yolo = YOLODetector(conf_thresh=args.conf)
    if not yolo.is_available:
        print("[compare] YOLO 模型不可用")
        return 2
    if args.vis:
        os.makedirs(args.vis, exist_ok=True)

    n_frames = agree_frames = 0
    ct_total = yl_total = inter_total = 0
    rows = []
    for p in files:
        img = cv2.imread(p)
        if img is None:
            continue
        # 与生产同基准：方向归一由引擎做，这里对原始截图直接双路径对比
        contour = detect_river_discards(img, tgd, mode="sc_hz")
        yd = yolo.detect_river_strips(img)
        yl = [d[1] for d in yd if d[1]]
        cc, yc = canon(contour), canon(yl)
        inter = sum((cc & yc).values())
        same = bool(cc) and cc == yc
        n_frames += 1
        agree_frames += 1 if same else 0
        ct_total += len(contour)
        yl_total += len(yl)
        inter_total += inter
        rows.append((os.path.basename(p)[:14], len(contour), len(yl),
                     inter, "AGREE" if same else ("BOTH0" if not cc and not yc
                                                  else "diff")))
        if args.vis:
            v = img.copy()
            # 轮廓法只回标签无坐标，可视化画 YOLO 框 + 轮廓法结果文字侧栏
            draw(v, yd, (0, 255, 0), "Y:")
            cv2.putText(v, f"contour({len(contour)}): {''.join(sorted(contour))}",
                        (10, v.shape[0] - 26), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 200, 255), 2, cv2.LINE_AA)
            cv2.imwrite(os.path.join(args.vis, os.path.basename(p)), v)

    print(f"{'shot':16s} 轮廓 条带 交集 判定")
    for r in rows:
        print(f"{r[0]:16s} {r[1]:4d} {r[2]:4d} {r[3]:4d}  {r[4]}")
    print("=" * 52)
    print(f"帧数 {n_frames} | 轮廓总张数 {ct_total} | 条带总张数 {yl_total}")
    print(f"标签交集总张数 {inter_total}")
    if ct_total or yl_total:
        # 两路径逐帧多重集一致（含双 0 帧不计入分母时的"有效一致率"）
        both0 = sum(1 for r in rows if r[4] == "BOTH0")
        eff = agree_frames - both0
        eff_n = n_frames - both0
        print(f"多重集完全一致(含双0) {agree_frames}/{n_frames}; "
              f"有效帧一致率 {eff}/{eff_n} = "
              f"{(eff / eff_n * 100) if eff_n else 0:.1f}%")
    else:
        print("两条路径在所有帧上均检出 0 张（当前截图池牌河本就不可见）")
    if args.vis:
        print(f"可视化输出: {args.vis}")
    report = {"frames": n_frames, "contour_total": ct_total,
              "yolo_total": yl_total, "inter_total": inter_total,
              "agree_frames": agree_frames}
    print("REPORT " + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
