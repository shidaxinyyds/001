# -*- coding: utf-8 -*-
"""新素材入库前的两道门：平台归因 + 质量校验（SOP 第 1 步的可复现工具）。

为什么必须是持久化脚本而不是当场写一次性代码：入库结论（“这 78 帧里 6 个已知
平台、没有新平台”）如果只存在于一次对话里，下次谁也无法复核，也无法在改完探针
后重跑同一份数据看归因有没有变。这里把两件事钉在一起：

1) 平台归因：用**生产同一个** `_probe_style`（多枚代表牌取平均）判定每帧的牌面
   美术归属，而不是我看缩略图猜。猜的代价已经踩过——把蜀山当成雀神去收割 bank，
   整套模板就废了。
2) 质量校验：清晰度（拉普拉斯方差）、亮度（HSV V 均值）、有效性（竖屏/全黑/
   读图失败）、重复图（字节 md5 + 手牌带 16x9 均值哈希）。
   「遮挡」无法在无人工标注时自动判定，所以这里只给出**需要人工复核的标记**
   （手牌带检出张数不在 13/14、或模板均分偏低），不假装能测遮挡。

口径提醒（同 docs/new_platform_onboarding.md）：本脚本输出的均分只反映“与已挂
bank 的匹配度”，**不是准确率**；没有逐图人工钉 GT 就没有准确率。

用法：
    py -3.10 -X utf8 localtest/probe_platforms.py                  # 默认 public/0
    py -3.10 -X utf8 localtest/probe_platforms.py --src DIR --n-crops 3
输出：build/platform_probe.txt（派生物，不入库）
"""
import argparse
import collections
import hashlib
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import EXTRA_BANKS, TencentGridDetector  # noqa: E402

OUT = os.path.join(REPO, "build", "platform_probe.txt")

# 质量门阈值：给出数值而不是“看起来清楚就行”，否则同一批素材两次跑出不同结论。
BLUR_MIN = 40.0        # 手牌带拉普拉斯方差，低于它=糊（缩略图放大/录码率低）
DARK_MEAN = 24.0       # 带内灰度均值，低于它=全黑/未被点亮
HAND_COUNT_OK = (13, 14)


def rect_xywh(r):
    if hasattr(r, "x"):
        return (int(r.x), int(r.y), int(r.w), int(r.h))
    return (int(r[0]), int(r[1]), int(r[2]), int(r[3]))


def as_boxes(strip):
    """`detect_hand_strip` 返的是 (rect, label, score) 三元组，这里只拆 rect 并统一成
    (x,y,w,h)；写成 r[0] 直接下标会在第一个非空帧就抛 TypeError（已踩过）。"""
    return [rect_xywh(t[0]) for t in strip]


def band_crop(img, boxes):
    """手牌带：有检出框就按框的并集裁，没有就退回画面下方 22%（手牌永远在那）。"""
    h, w = img.shape[:2]
    if boxes:
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        return img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    return img[int(h * 0.78):h, 0:w]


def perceptual_hash(band):
    """16x9 灰度均值哈希：只用来抓“同一张图被存了两遍/几乎一样的连拍”，
    不做牌面识别，因此不配当准确率证据。"""
    if band is None or band.size == 0:
        return ""
    small = cv2.resize(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), (16, 9),
                       interpolation=cv2.INTER_AREA)
    m = float(small.mean())
    return "".join("1" if v >= m else "0" for v in small.ravel())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO, "public", "0"))
    ap.add_argument("--out", default=None,
                    help="报告路径。默认按素材目录名落 build/platform_probe_<目录名>.txt，"
                         "避免新一批把上一批的归因结论覆盖掉（多批对比是本工具的常见用法）。")
    ap.add_argument("--n-crops", type=int, default=3,
                    help="参与风格探针的代表牌枚数（生产 _probe_style 也是多枚取平均）")
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    out = a.out or os.path.join(REPO, "build",
                                f"platform_probe_{os.path.basename(src) or 'root'}.txt")
    files = sorted(f for f in os.listdir(src) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if not files:
        print(f"素材目录空：{src}")
        return 2

    det = TencentGridDetector()
    banks = ["tencent"] + [s for _m, s in EXTRA_BANKS]
    lines = [f"素材目录：{src}（{len(files)} 帧）",
             f"已挂 bank 风格：{banks}",
             f"模板条目总数：{len(det._cores)}",
             f"探针代表牌枚数：{a.n_crops}（与生产同口径：多枚风格分取平均）", ""]

    rows, by_style = [], collections.Counter()
    dup_bytes, dup_vis = collections.defaultdict(list), collections.defaultdict(list)
    res_clusters = collections.Counter()

    for i, f in enumerate(files, 1):
        path = os.path.join(src, f)
        with open(path, "rb") as fh:
            dup_bytes[hashlib.md5(fh.read()).hexdigest()].append(f)
        img = cv2.imread(path)
        if img is None:
            rows.append((i, f, 0, 0.0, "<读图失败>", ["读图失败"]))
            by_style["<读图失败>"] += 1
            lines.append(f"[{i:02d}] {f}  读图失败")
            continue
        h, w = img.shape[:2]
        res_clusters[f"{w}x{h}"] += 1

        strip = det.detect_hand_strip(img) or []
        boxes = as_boxes(strip)
        scores = [float(t[2]) for t in strip]
        n = len(strip)
        mean = sum(scores) / n if n else 0.0

        band = band_crop(img, boxes)
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.size else np.zeros((1, 1), np.uint8)
        blur = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        lum = float(gray.mean())
        dup_vis[perceptual_hash(band)].append(f)

        # 病态标记按优先级排：无效帧不该再被写成“某平台的样本”
        flags = []
        if h > w:
            flags.append("竖屏(非生产捕获)")
        if lum < DARK_MEAN:
            flags.append(f"带内过暗({lum:.0f})")
        if blur < BLUR_MIN:
            flags.append(f"带内糊(清晰度{blur:.0f})")
        if n == 0:
            flags.append("手牌带无检出")
        elif n not in HAND_COUNT_OK:
            flags.append(f"张数{n}需人工复核")
        if mean < 0.60:
            flags.append(f"模板均分低({mean:.2f})")

        style = None
        if not flags or "张数" in " ".join(flags) or "均分低" in " ".join(flags):
            crops = []
            for (x, y, cw, ch) in boxes[:a.n_crops]:
                c = img[max(0, y):y + ch, max(0, x):x + cw]
                if c.size:
                    crops.append(c)
            if crops:
                style = det._probe_style(crops[0], extra_crops=crops[1:])
        verdict = style or ("<探针未路由>" if not flags else ";".join(flags))
        by_style[verdict] += 1
        rows.append((i, f, n, mean, verdict, flags))
        lines.append(f"[{i:02d}] {w}x{h}  手牌{n:>2}张 均分{mean:.3f} "
                     f"清晰度{blur:>7.0f} 亮度{lum:>3.0f}  归属={verdict}"
                     + ("" if not flags else f"   标记={flags}"))
        print(f"{i:02d}/{len(files)}", end="\r")

    lines += ["", "== 汇总 =="]
    # 待复核口径只看标记，不看归属：一帧可以同时“属于 jj”且“张数要人工看”，
    # 拿 verdict 字符串去猜会把这类帧漏掉（它们恰恰是最该被复核的）
    bad = sum(1 for r in rows if r[5])
    lines.append(f"总帧 {len(rows)}，其中带无效/待复核标记 {bad} 帧")
    for k, v in by_style.most_common():
        lines.append(f"  {k}: {v} 帧")
    lines.append(f"\n分辨率簇（{len(res_clusters)} 个）：")
    for k, v in res_clusters.most_common():
        lines.append(f"  {k}: {v} 帧")
    b_dup = {k: v for k, v in dup_bytes.items() if len(v) > 1}
    v_dup = {k: v for k, v in dup_vis.items() if len(v) > 1}
    lines.append(f"字节完全重复：{sum(len(v) for v in b_dup.values())} 帧 "
                 + " | ".join(",".join(v) for v in list(b_dup.values())[:5]))
    lines.append(f"手牌带视觉近似重复：{sum(len(v) for v in v_dup.values())} 帧 "
                 + " | ".join(",".join(v) for v in list(v_dup.values())[:5]))
    lines.append("\n注：均分=模板匹配度，不是准确率。本表只用于「按平台分组 + 剔除无效帧」，"
                 "\n    逐平台准确率必须先在 localtest/gt/new_shots.json 人工钉 GT 后"
                 "\n    由 localtest/eval_new_material.py 计算。")

    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
