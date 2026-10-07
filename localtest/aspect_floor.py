# -*- coding: utf-8 -*-
"""测量各平台真实立牌的 aspect 分布，为 MIN_TILE_ASPECT 下限提供定标依据。

关键：引擎的 _apply_orientation 会在「按 hand_roi 裁切之前」先对整屏跑一次
detect_all_rows 并把结果缓存进 _cached_rows，随后 _apply_conf 校验的正是这批
整屏坐标的框。所以 aspect 必须以「整屏检测」为准，而不是裁片内检测。

用法: py -3.10 localtest\aspect_floor.py
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402
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
    eng = Engine()
    det = eng.get_detector()
    files = sorted(f for f in os.listdir(SHOTS) if f.lower().endswith(".jpg"))
    agg = {}
    print(f"{'idx':>3} {'platform':11} {'y0':>5} {'n':>3} {'aspect':>18} {'<0.52':>5}")
    for f in files:
        idx = int(f.split("_")[1][:2])
        platform = PLATFORM_OF.get(idx, "generic")
        img = cv2.imread(os.path.join(SHOTS, f))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        h = img.shape[0]
        rows = det.detect_all_rows(img, classify=True, allow_rotation=False)
        # 只看落在该平台 hand_roi 行内的检测（与引擎实际采信的手牌带一致）
        roi = get_hand_roi(platform)
        best, best_n = None, 0
        for r in rows:
            inb = [t for t in r if roi[0] <= (t[0][1] + t[0][3] / 2.0) / h <= roi[1] + 0.02]
            if len(inb) > best_n:
                best, best_n = inb, len(inb)
        if not best:
            print(f"{idx:3d} {platform:11} {'-':>5} {0:3d} 带内无检测")
            continue
        asp = [t[0][2] / float(t[0][3]) for t in best if t[0][3] > 0]
        y0 = min(t[0][1] for t in best)
        n_low = sum(1 for a in asp if a < 0.52)
        print(f"{idx:3d} {platform:11} {y0:5d} {len(best):3d} "
              f"{min(asp):.3f}~{max(asp):.3f} {n_low:5d}")
        agg.setdefault(platform, []).extend(asp)

    print("\n=== 各平台真实立牌 aspect 分位（决定安全下限）===")
    for p, vals in sorted(agg.items()):
        v = sorted(vals)
        q = lambda fr: v[min(len(v) - 1, int(fr * (len(v) - 1)))]  # noqa: E731
        print(f"  {p:12} n={len(v):4d}  min={v[0]:.3f} p05={q(0.05):.3f} "
              f"p25={q(0.25):.3f} 中位={q(0.5):.3f} p95={q(0.95):.3f} max={v[-1]:.3f}")
    print(f"\n当前全局下限 MIN_TILE_ASPECT=0.52")
    for p, vals in sorted(agg.items()):
        frac = sum(1 for a in vals if a < 0.52) / len(vals)
        print(f"  {p:12} 被 0.52 误杀比例 {frac*100:5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
