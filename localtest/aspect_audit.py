# -*- coding: utf-8 -*-
"""全量截图手牌宽高比审计：定位 _apply_conf 的全局 aspect 门槛误杀。

引擎在 _apply_conf 里用全局 MIN_TILE_ASPECT/MAX_TILE_ASPECT 做牌形校验，
但各平台立牌的宽高比差异很大（雀神/JJ/途游明显更"瘦高"）。本脚本按引擎同样的
裁切方式（hand_roi）跑 detect_all_rows，统计每平台手牌行的 aspect 分布，
直接给出"该平台需要多大 aspect 下限"的校准依据。

用法: py -3.10 localtest/aspect_audit.py
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402
from engine.engine import MIN_TILE_ASPECT, MAX_TILE_ASPECT  # noqa: E402
from platforms import get_hand_roi  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")

PLATFORM_OF = {i: "weile" for i in range(1, 13)}
PLATFORM_OF[13] = "shushan"
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"


def main():
    eng = Engine()  # 复用一个实例拿 detector，避免每张重建模型
    det = eng.get_detector()
    files = sorted(f for f in os.listdir(SHOTS) if f.lower().endswith(".jpg"))
    per_platform = {}
    print(f"全局 aspect 门槛 = [{MIN_TILE_ASPECT}, {MAX_TILE_ASPECT}]")
    print(f"{'idx':>3} {'platform':11} {'裁剪后行':>8} {'n':>3} {'aspect min~max':>16} "
          f"{'越界':>5}")

    for f in files:
        idx = int(f.split("_")[1][:2])
        platform = PLATFORM_OF.get(idx, "generic")
        img = cv2.imread(os.path.join(SHOTS, f))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        h, _ = img.shape[:2]
        roi = get_hand_roi(platform)
        y0, y1 = int(h * roi[0]), int(h * roi[1])
        crop = img[y0:y1, :] if y1 - y0 >= 80 else img

        rows = det.detect_all_rows(crop, allow_rotation=False)
        if not rows:
            print(f"{idx:3d} {platform:11} {'-':>8} {0:3d} {'无检测行':>16}")
            continue
        # 引擎对 YOLO/Grid 直接取 rows[0] 当手牌行
        row = rows[0]
        asp = [t[0][2] / float(t[0][3]) for t in row if t[0][3] > 0]
        if not asp:
            continue
        n_low = sum(1 for a in asp if a < MIN_TILE_ASPECT)
        n_high = sum(1 for a in asp if a > MAX_TILE_ASPECT)
        print(f"{idx:3d} {platform:11} {f'{y0}~{y1}':>8} {len(row):3d} "
              f"{min(asp):.3f}~{max(asp):.3f}  {n_low + n_high:5d}")
        per_platform.setdefault(platform, []).append(min(asp))

    print("\n=== 各平台手牌行 aspect 最小值（决定 MIN_TILE_ASPECT 需要降到多少）===")
    for p, vals in sorted(per_platform.items()):
        print(f"  {p:12} n={len(vals):2d}  min(aspect) 全局最低={min(vals):.3f}  "
              f"典型最低={sorted(vals)[len(vals)//2]:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
