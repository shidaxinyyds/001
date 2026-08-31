"""跨样式牌面分类基准：直接用 _classify_face 对三种风格的 34 张牌打分。

样式：
  - tiles/screenshot : 原始游戏截图裁剪（13 张，不全）
  - tiles/duma520    : 斗麻 520 风格（34 张）
  - real_tiles       : 维基百科/Wikimedia 真实牌面（34 张）
"""
import os
import sys
import glob
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import cv2
import numpy as np

from recognition.structural import StructuralDetector, _HONOR_GLYPH  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# duma520 文件名与真实标签的修正：5z 文件实际为白板，7z 文件实际为中
def truth_for_duma(fname):
    lab = os.path.basename(fname)[:-4]
    if lab == "5z":
        return "7z"
    if lab == "7z":
        return "5z"
    return lab


def to_bgr(img):
    """RGBA → 白底 BGR，与 structural.py 一致。"""
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3].astype(np.float32) / 255.0
        white = np.ones((img.shape[0], img.shape[1], 3), np.uint8) * 255
        f = img[:, :, :3].astype(np.float32)
        out = (f * a[:, :, None] + white * (1.0 - a)[:, :, None]).astype(np.uint8)
        return out
    return img


def classify_face(det, img, inset=0.06):
    fh, fw = img.shape[:2]
    ix, iy = int(fw * inset), int(fh * inset)
    face = img[iy:fh - iy, ix:fw - ix]
    return det._classify_face(face)


def test_style(name, paths, truth_fn):
    det = StructuralDetector()
    results = []
    for p in sorted(paths):
        img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        img = to_bgr(img)
        got, conf = classify_face(det, img)
        truth = truth_fn(p)
        results.append((os.path.basename(p), truth, got, conf))
    ok = sum(1 for _, t, g, _ in results if t == g)
    print(f"\n=== {name} ({len(results)} 张) ===")
    print(f"{'file':10s} {'truth':5s} {'got':5s} {'conf':6s}")
    for fn, t, g, c in results:
        mark = "OK" if t == g else ">>"
        print(f"{fn:10s} {t:5s} {str(g):5s} {c:5.2f} {mark}")
    print(f"正确率: {ok}/{len(results)} = {ok / max(1, len(results)):.1%}")
    return ok, len(results), results


def main():
    screenshot = glob.glob(os.path.join(HERE, "tiles", "screenshot", "*.png"))
    duma = glob.glob(os.path.join(HERE, "tiles", "duma520", "*.png"))
    real = glob.glob(os.path.join(HERE, "real_tiles", "*.png"))

    def truth_screenshot(p):
        # screenshot/00_3m.png -> 3m
        return os.path.basename(p)[:-4].split("_", 1)[1]

    def truth_real(p):
        return os.path.basename(p)[:-4]

    totals_ok = totals = 0
    for name, paths, tf in [
        ("screenshot", screenshot, truth_screenshot),
        ("duma520", duma, truth_for_duma),
        ("real_tiles", real, truth_real),
    ]:
        o, n, _ = test_style(name, paths, tf)
        totals_ok += o
        totals += n
    print(f"\n=== 总正确率 === {totals_ok}/{totals} = {totals_ok / max(1, totals):.1%}")


if __name__ == "__main__":
    main()
