# -*- coding: utf-8 -*-
"""诊断：用现行产 TencentGridDetector 跑蜀山四川麻将截图，定位失败环节。

输出：
1) 每张图 detect_hand_strip 的逐张结果（rect/label/score）与门槛值；
2) 标注图 diag_sN.jpg（检出框+标签）；
3) 底部手牌带原图裁切 strip_sN.jpg（供人工核对 GT）。

用法: py -3.10 localtest/diag_shushan.py
"""
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots_shushan")


def main():
    det = TencentGridDetector()
    print(f"templates={len(det.templates_bgr)} available={det.is_available}")
    for name in sorted(os.listdir(SHOTS)):
        if not name.endswith(".jpg") or name.startswith(("diag_", "strip_")):
            continue
        path = os.path.join(SHOTS, name)
        img = cv2.imread(path)
        if img is None:
            print(f"!! cannot read {path}")
            continue
        h, w = img.shape[:2]
        dets = det.detect_hand_strip(img)
        print(f"\n=== {name} ({w}x{h}) top_score={det.last_top_score:.3f} n={len(dets)}")
        for (x, y, bw, bh), lbl, sc in dets:
            print(f"   {lbl:3s} sc={sc:.3f} rect=({x},{y},{bw},{bh})")
        # 标注图
        ann = img.copy()
        for (x, y, bw, bh), lbl, sc in dets:
            cv2.rectangle(ann, (x, y), (x + bw, y + bh), (0, 255, 255), 2)
            cv2.putText(ann, f"{lbl} {sc:.2f}", (x, max(0, y - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        cv2.imwrite(os.path.join(SHOTS, f"diag_{name}"), ann)
        # 底部带原图（y 60%~100%）
        strip = img[int(h * 0.60):h, :]
        cv2.imwrite(os.path.join(SHOTS, f"strip_{name}"), strip)


if __name__ == "__main__":
    main()
