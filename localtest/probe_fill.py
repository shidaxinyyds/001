# -*- coding: utf-8 -*-
"""极限实验：完全不信 YOLO 的定位，只用「条带几何 + 模板分类」能不能读对手牌。

动机（实测得出，不是猜的）：
  * 途游帧 34/36 物理各 14 张，YOLO 只出 12 框，且所有框 conf 都在 0.95~1.00、
    框宽无异常（中位 155，无 >1.45x 合并框）——把 conf_thresh 从 0.40 一路降到
    0.20，检出数纹丝不动。所以漏检不是置信度问题，也不是合并框问题，而是
    **抬高牌位置上根本没有 proposal**。
  * 因此 sweep_gate 显示这两帧在所有放行线下恒为 3/14、5/14：分类器再准也
    读不到没切出来的牌。漏检占当前总损失的 54%（20/37 位）。

本脚本按「检出框中位宽 = pitch」把整条手牌带均匀切成 round(跨度/pitch) 个槽，
逐槽送 classify_tile，与 GT 逐位比对。若命中率显著高于生产链路，就说明
补检（而不是补模板、也不是调阈值）才是途游的正解。

用法: py -3.10 localtest\probe_fill.py [idx ...]
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS, PLATFORM_OF  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402
from ab_pitch_classify import GT, score  # noqa: E402
from audit_bank_fit import locate_strip  # noqa: E402

OUT = os.path.join(HERE, "fill_probe.txt")


def slots_from_geometry(raw, x_left, x_right, occupied=None):
    """只在异常步进处插槽，**原框一律保留**。

    上一版在这里做了“用全局 pitch 重排整行”，实测把本来正确的行也切坏了
    （帧 40 从 11/12 掉到 5/12）：途游抬高区与立牌区的间距本来就不同，
    用单一 pitch 外推整行必然漂。所以补检只能是“局部插空”，不能是“全局重排”。

    为什么不用框间空隙（gap）：实测帧 36 的漏检空档被左右宽框吃掉，gap 仅
    81px，不够 0.55x 框宽，靠 gap 会漏补。步进类判据不受此影响。

    occupied(cx) 是防误插的物理闸门（实测单独不够用，见下）。

    步进一律用**框中心**差，不用框起点差：帧 40 误插就是起点判据造成的——
    它最左一枚带“赖”角标、框宽 206（其它牌约 150），起点步进被抻到 197
    而触发误插，把 13 张的行补成 14 槽、整行右移一位（11/12 崩成 6/12）。
    而牌面占比闸门区分不了它：误插点落在真牌上（相邻框外扩但仍盖着牌）。
    中心差对框宽抖动免疫：帧 40 中心步进降到 173=1.27x（不触发），
    而帧 34/36 的真漏检中心步进仍为 259/228/242 = 1.8x/1.6x/1.8x（照常触发）。
    """
    order = sorted(raw, key=lambda d: d[0][0])
    cs = [d[0][0] + d[0][2] / 2.0 for d in order]
    if len(cs) < 3:
        return []
    csteps = sorted(cs[i + 1] - cs[i] for i in range(len(cs) - 1))
    pitch = csteps[len(csteps) // 2]
    if pitch <= 0:
        return []
    centers = []
    for i in range(len(order) - 1):
        centers.append(cs[i])
        step = cs[i + 1] - cs[i]
        k = int(round(step / pitch)) - 1
        # 只在步进明显过大（≥ 1.45x）时补槽，避免把正常抖动当漏检
        if k >= 1 and step >= 1.45 * pitch:
            for j in range(1, k + 1):
                cx = cs[i] + step * j / (k + 1.0)
                if occupied is not None and not occupied(cx):
                    continue
                centers.append(cx)
    centers.append(cs[-1])
    return centers, pitch, len(raw)


def main():
    idxs = [int(a) for a in sys.argv[1:] if a.isdigit()] or [34, 36, 40, 33, 30, 14]
    helper = TencentGridDetector()
    lines = []
    for idx in idxs:
        f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            continue
        img = cv2.imread(os.path.join(SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        h = img.shape[0]
        y_top, y_bot, x_left, x_right = locate_strip(img)
        strip = img[y_top:y_bot, x_left:x_right]

        det = YOLODetector()
        det.set_platform_styles(PLATFORM_OF.get(idx))
        helper.set_platform_styles(PLATFORM_OF.get(idx))
        raw = det.detect_strip(strip, offset_x=x_left, offset_y=y_top)

        # 牌面占比：用“不是桌布色”的像素比例。桌布在 HSV 上是高饱和的
        # 绿/蓝紫，牌面是低饱和高亮度的米白，两者能分开。
        band_y1, band_y2 = max(0, y_top), min(img.shape[0], y_bot)
        hsv = cv2.cvtColor(img[band_y1:band_y2, :], cv2.COLOR_BGR2HSV)
        face_m = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 130)).astype(np.float32)

        def ratio(cx):
            a, b = int(max(0, cx - 25)), int(min(face_m.shape[1], cx + 25))
            return float(face_m[:, a:b].mean()) if b > a else 0.0

        ref = [ratio(d[0][0] + d[0][2] / 2.0) for d in raw]
        ref_med = float(np.median(ref)) if ref else 0.0
        occupied = (lambda cx: ratio(cx) >= 0.6 * ref_med) if ref_med > 0.05 else None

        geo = slots_from_geometry(raw, x_left, x_right, occupied)
        if not geo:
            continue
        centers, pitch, n_raw = geo

        ys = sorted(d[0][1] for d in raw)
        med_y, med_h = ys[len(ys) // 2], int(np.median([d[0][3] for d in raw]))
        labels, confs = [], []
        for cx in centers:
            x1, x2 = int(round(cx - pitch / 2)), int(round(cx + pitch / 2))
            # 抬高牌用整行中位 y 会切错，这里同样按"最近框中心"取纵向
            band = YOLODetector._slot_band(raw, cx, med_y, med_y + med_h)
            patch = img[band[0]:band[0] + band[1], max(0, x1):min(img.shape[1], x2)]
            if patch.size == 0:
                labels.append("?")
                confs.append(0.0)
                continue
            lbl, cf = helper.classify_tile(patch)
            labels.append(lbl or "?")
            confs.append(round(float(cf or 0.0), 2))

        gt = GT.get(idx)
        line = (f"[{idx:02d}] {PLATFORM_OF.get(idx,'?')} 检出框={n_raw} 补槽后={len(centers)} "
                f"pitch={pitch} GT位={len(gt.split()) if gt else '?'} 命中={score(labels, gt)}\n"
                f"     补槽 labels = {' '.join(labels)}\n"
                f"     conf         = {confs}\n"
                f"     GT           = {gt}")
        print(line)
        lines.append(line)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(lines) + "\n")
    print(f"\n结果已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
