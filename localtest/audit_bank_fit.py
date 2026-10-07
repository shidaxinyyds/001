# -*- coding: utf-8 -*-
"""无 GT 判据：模板 bank 对各平台牌风的「贴合度」。

为什么不用准确率：微乐 / JJ 没标 GT。但补不补 bank 只取决于一个问题——
现有模板在这两个平台的牌面上能不能拿到放行分。生产覆盖层的放行线是
yolo_detector 里的 ref_conf >= 0.40，所以直接量 ref_conf 的分布：
  * 命中率高（>=0.40 占比大）→ 牌风已在 bank 里，补库没有增益；
  * 命中率低 → 覆盖层全程弃权，标签完全来自 YOLO。雀神补库前就是这个
    状态（帧 14 纯 YOLO 仅 2/14 命中），补 bank 后升到 11/14。
同时统计模板与 YOLO 的 top1 同意率：同意率高说明即使分低，YOLO 也没被
带偏；分歧大 + 分低才是最糟的组合（模板抢不走、YOLO 又不可信）。

patch 一律用 YOLO 原始框，不做等距重切：这里要问的是"这张牌面像不像
bank 里的模板"，重切会把两张牌混进一个 patch，污染度量。
风格集合走生产口径 set_platform_styles，保证量的是"这个平台实际能用的
那部分 bank"。

用法: py -3.10 localtest\audit_bank_fit.py
"""
import os
import sys
from collections import Counter, defaultdict

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS, PLATFORM_OF  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402

GATE = 0.40  # 与 yolo_detector 覆盖层放行线一致


def locate_strip(img):
    """复刻 detect_all_rows 的手牌条带定位（口径必须一致，否则量的是另一回事）。"""
    h, w = img.shape[:2]
    y1 = int(0.70 * h)
    hsv = cv2.cvtColor(img[y1:h, :], cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 130)).astype(np.uint8)
    col_counts = mask.sum(axis=0)
    valid_x = np.where(col_counts > (h - y1) * 0.15)[0]
    if len(valid_x) <= 50:
        return y1, h, 0, w
    x_min, x_max = int(valid_x[0]), int(valid_x[-1])
    row_counts = mask[:, x_min:x_max].sum(axis=1)
    valid_y = np.where(row_counts > (x_max - x_min) * 0.10)[0]
    y_top = y1 + max(0, int(valid_y[0]) - 12) if len(valid_y) > 0 else y1
    y_bot = min(h, y1 + int(valid_y[-1]) + 12) if len(valid_y) > 0 else h
    return y_top, y_bot, max(0, x_min - 12), min(w, x_max + 12)


def main():
    agg = defaultdict(lambda: np.zeros(3))  # platform -> [牌数, 过闸数, 与YOLO同标签数]
    dis = defaultdict(Counter)  # 仅统计过闸且不同
    worst = defaultdict(list)
    for f in sorted(os.listdir(SHOTS)):
        if not f.endswith((".jpg", ".jpeg", ".png")):
            continue
        idx = int(f.split("_")[1])
        plat = PLATFORM_OF.get(idx, "?")
        img = cv2.imread(os.path.join(SHOTS, f))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)

        det = YOLODetector()
        det.set_platform_styles(plat)
        helper = det._phase_helper
        if helper is None:
            print(f"[{idx:02d}] 无 phase_helper，覆盖层不存在")
            continue
        y_top, y_bot, x_left, x_right = locate_strip(img)
        raw = det.detect_strip(img[y_top:y_bot, x_left:x_right],
                               offset_x=x_left, offset_y=y_top)
        if not raw:
            continue
        scores = []
        for rect, ylbl, _c in sorted(raw, key=lambda d: d[0][0]):
            rx, ry, rw, rh = rect
            patch = img[ry:ry + rh, rx:rx + rw]
            if patch.size == 0:
                continue
            lbl, sc = helper.classify_tile(patch)
            sc = float(sc or 0.0)
            scores.append(sc)
            a = agg[plat]
            a[0] += 1
            if sc >= GATE:
                a[1] += 1
                if lbl != ylbl:
                    dis[plat][f"{ylbl}>{lbl}"] += 1
            if lbl == ylbl:
                a[2] += 1
        if scores:
            med = float(np.median(scores))
            if med < 0.55:
                worst[plat].append(f"  [{idx:02d}] 分中位={med:.2f} "
                                   f"过闸={sum(1 for s in scores if s >= GATE)}/{len(scores)}")

    print(f"{'平台':14} {'牌':>4} {'过闸率':>8} {'与YOLO同意':>10}")
    for plat in sorted(agg):
        n, pass_, agree = agg[plat]
        print(f"{plat:14} {int(n):4d} {pass_ / n:7.1%} {agree / n:9.1%}")

    # 分歧集中在哪几张牌：全面不适配（各花色均匀散开）与个别类别系统性混淆
    # （如 2m/3m、红中）是两种完全不同的病，前者补 bank，后者改判决规则。
    for plat in sorted(dis):
        tot = sum(dis[plat].values())
        top = sorted(dis[plat].items(), key=lambda kv: -kv[1])[:6]
        print(f"\n{plat} 覆盖层改判 top（共 {tot} 次）: "
              + "  ".join(f"{k}x{v}" for k, v in top))

    for plat in sorted(worst):
        if worst[plat]:
            print(f"\n{plat} 分中位最低的帧（<0.55）:")
            for line in worst[plat][:8]:
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
