"""检查风格模板的"跨风格误匹配"风险。

对每个测试牌，分别统计：
  - same  : 与**同风格**模板的最高分（模板来源风格 == 该牌风格）
  - cross : 与**其它风格**模板的最高分
若 cross 分数经常 >= MIN_STYLE_SCORE(0.74)，说明未注册的新风格会被
错误地高置信匹配成别的牌，必须提高门槛或改用别的度量。
用法: python localtest/check_style_leak.py
"""
import os
import sys
import glob

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import cv2
import numpy as np
from recognition.structural import (  # noqa: E402
    StructuralDetector, MIN_STYLE_SCORE,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def to_bgr(img):
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3].astype(np.float32) / 255.0
        white = np.ones((img.shape[0], img.shape[1], 3), np.uint8) * 255
        f = img[:, :, :3].astype(np.float32)
        return (f * a[:, :, None] + white * (1.0 - a)[:, :, None]).astype(np.uint8)
    return img


def face_mask(det, path):
    img = to_bgr(cv2.imread(path, cv2.IMREAD_UNCHANGED))
    fh, fw = img.shape[:2]
    ix, iy = int(fw * 0.06), int(fh * 0.06)
    return det._ink_mask(img[iy:fh - iy, ix:fw - ix])


def main():
    det = StructuralDetector()
    bank = det._styles
    # 模板按风格分组
    by_style = {}
    for style in sorted(os.listdir(os.path.join(REPO, "android", "app", "src",
                                                "main", "python", "recognition",
                                                "images", "styles"))):
        sdir = os.path.join(REPO, "android", "app", "src", "main", "python",
                            "recognition", "images", "styles", style)
        if not os.path.isdir(sdir):
            continue
        items = []
        for name in sorted(os.listdir(sdir)):
            if not name.endswith(".png"):
                continue
            binm = (cv2.imread(os.path.join(sdir, name),
                               cv2.IMREAD_GRAYSCALE) > 100).astype(np.uint8)
            if binm.any():
                items.append((name[:-4], bank._soft(binm)))
        by_style[style] = items
    print("风格:", {k: len(v) for k, v in by_style.items()})
    print(f"MIN_STYLE_SCORE = {MIN_STYLE_SCORE}\n")

    sets = [
        ("app_shot", os.path.join(HERE, "tiles", "screenshot"),
         lambda p: os.path.basename(p)[:-4].split("_", 1)[1]),
        ("duma520", os.path.join(HERE, "tiles", "duma520"),
         lambda p: {"5z": "7z", "7z": "5z"}.get(os.path.basename(p)[:-4],
                                                os.path.basename(p)[:-4])),
        ("wikipedia", os.path.join(HERE, "real_tiles"),
         lambda p: os.path.basename(p)[:-4]),
    ]
    worst_cross = 0.0
    n_cross_leak = 0
    for style, d, tf in sets:
        print(f"=== {style} ===")
        print(f"{'file':10s} {'truth':5s} {'same':>6s} {'cross':>6s} {'cross_label':>11s} leak")
        for p in sorted(glob.glob(os.path.join(d, "*.png"))):
            truth = tf(p)
            m = face_mask(det, p)
            q = bank._norm_face(m)
            if q is None:
                continue
            qf = q.ravel()
            qn = float(np.sqrt((qf * qf).sum()))
            same_best, same_l = 0.0, ""
            cross_best, cross_l = 0.0, ""
            for sname, items in by_style.items():
                for label, t in items:
                    den = float(np.sqrt((t.ravel() ** 2).sum())) * qn
                    if den <= 0:
                        continue
                    sc = float((qf * t.ravel()).sum()) / den
                    if sname == style:
                        if sc > same_best:
                            same_best, same_l = sc, label
                    else:
                        if sc > cross_best:
                            cross_best, cross_l = sc, label
            leak = cross_best >= MIN_STYLE_SCORE and cross_l != truth
            if leak:
                n_cross_leak += 1
            worst_cross = max(worst_cross, cross_best)
            print(f"{os.path.basename(p):10s} {truth:5s} {same_best:6.3f} "
                  f"{cross_best:6.3f} {cross_l:>11s} {'LEAK!' if leak else ''}")
        print()
    print(f"跨风格最高分 = {worst_cross:.3f}；越界(>=阈值且标签错误)次数 = {n_cross_leak}")


if __name__ == "__main__":
    main()
