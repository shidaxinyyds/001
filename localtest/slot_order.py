# -*- coding: utf-8 -*-
"""打印每帧「引擎检出的手牌槽」数量与位置序标签，用于把物理 GT 对齐成收割 GT。

为什么需要两套 GT：
  * 评分用的 GT 是**物理牌序**（左→右真实每张牌），分母是物理张数——漏检必须
    算成丢失，否则"根本没看见"会被算成"少一张题"，指标虚高。
  * 收割模板用的 GT 是**引擎槽序**：harvest 是按引擎检出的 rect 逐格裁片的，
    第 i 格裁片配第 i 个 label。若某帧漏检了物理第 3 张，那引擎槽里就没有它，
    硬按物理序写收割 GT 会让后面所有格子的 label 整体左移一位——
    **等于把错标签烧进模板库**，代价远大于少收几张样本。

用法: py -3.10 localtest\slot_order.py 1 2 3 5 8 10
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))

import ab_pitch_classify as AB  # noqa: E402

OUT = os.path.join(HERE, "slot_order.txt")


def main():
    args = [int(a) for a in sys.argv[1:] if a.isdigit()]
    idxs = sorted(args or [1, 2, 3, 5, 6, 7, 8, 10])
    lines = []
    for idx in idxs:
        f = next((x for x in os.listdir(AB.SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            lines.append(f"[{idx:02d}] 缺帧")
            continue
        img = cv2.imread(os.path.join(AB.SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        lbls, _nc, d = AB.run_img(img, idx, "prod")
        lbls = lbls or []
        gt = (AB.GT.get(idx) or "").split()
        lines.append(
            f"[{idx:02d}] {AB.PLATFORM_OF.get(idx,'?'):11} 引擎槽={len(lbls):2} 物理GT位={len(gt):2} "
            f"status={(d or {}).get('status')}\n"
            f"     引擎位置序 = {' '.join(lbls)}\n"
            f"     物理 GT    = {' '.join(gt) if gt else '（尚未标注）'}")
    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"共 {len(lines)} 帧 -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
