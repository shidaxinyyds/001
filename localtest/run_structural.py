"""结构识别器验证：已知边界逐张分类 + 全流程 detect()。

用法: python localtest/run_structural.py [截图路径]
"""
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition.structural import StructuralDetector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SHOT = os.path.join(HERE, "screenshot.jpg")

B = [160, 310, 457, 614, 756, 904, 1052, 1200, 1348, 1500, 1648, 1795, 1943, 2093]
Y1, Y2 = 1015, 1195
GT = ["3m", "9m", "1s", "6s", "7s", "9s", "9s", "1p", "3p", "3p", "4p", "4p", "5p"]


def test_faces(img):
    print("=== 逐张分类（已知边界） ===")
    det = StructuralDetector()
    ok = 0
    for i in range(len(B) - 1):
        face = img[Y1 + 6:Y2 - 6, B[i] + 5:B[i + 1] - 5]
        label, conf = det._classify_face(face)
        mark = "OK " if label == GT[i] else ">>> "
        print(f"[{i:2d}] GT={GT[i]:3s} got={str(label):4s} conf={conf:.2f} {mark}")
        if label == GT[i]:
            ok += 1
    print(f"正确: {ok}/{len(GT)}")
    return ok


def test_full(img):
    print("\n=== 全流程 detect() ===")
    det = StructuralDetector()
    stage = det.detect(img)
    print(f"detections={len(stage.result)}")
    for i, (rect, label) in enumerate(stage.result):
        c = det.last_conf[i] if i < len(det.last_conf) else -1
        print(f"   {label}: x={rect[0]} y={rect[1]} w={rect[2]} h={rect[3]} conf={c:.2f}")
    vis = img.copy()
    for i, (rect, label) in enumerate(stage.result):
        x, y, w, h = rect
        c = det.last_conf[i] if i < len(det.last_conf) else 0.5
        color = (0, 200, 0) if c >= 0.30 else (0, 140, 255)
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, 3)
        cv2.putText(vis, str(label), (x, y - 8), 1, 1.5, color, 3)
    out = os.path.join(HERE, "structural_vis.jpg")
    cv2.imwrite(out, vis)
    print(f"saved: {out}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SHOT
    image = cv2.imread(path)
    assert image is not None, f"cannot read {path}"
    # 模拟设备端 JPEG50
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 50])
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    test_faces(image)
    test_full(image)


if __name__ == "__main__":
    main()
