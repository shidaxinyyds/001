"""全语料跑三个开局阶段视觉判据，按 GT status 列混淆 —— 改门槛前后的对照表。

只做几何/颜色计算，不加载模板库（这些方法体不触碰 self）。
"""
import json
import os
import sys

import cv2

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
from recognition.tencent_grid_detector import TencentGridDetector as D  # noqa: E402

SHOTS = os.path.join(REPO, "localtest", "shots")
REAL = os.path.join(REPO, "localtest", "shots_tuyou_select")
GT = os.path.join(REPO, "localtest", "gt", "shots.json")

FN = ("is_swap_phase", "is_dingque_phase", "is_pick_phase")


def rows():
    with open(GT, encoding="utf-8") as f:
        for e in json.load(f)["shots"]:
            if not e.get("verified"):
                continue
            img = cv2.imread(os.path.join(SHOTS, e["file"]))
            if img is not None:
                yield e["file"][:14], e.get("status", ""), img
    for nm in sorted(os.listdir(REAL)):
        if nm.endswith(".jpg"):
            img = cv2.imread(os.path.join(REAL, nm))
            if img is not None:
                yield nm[:14], "real", img


def main():
    print(f"{'帧':16s} {'GT':8s} " + "  ".join(f"{n[3:]:>10s}" for n in FN))
    tally = {}
    for name, status, img in rows():
        vals = []
        for n in FN:
            try:
                vals.append(bool(getattr(D, n)(None, img)))
            except Exception as e:  # noqa: BLE001
                vals.append(f"ERR:{type(e).__name__}")
        hit = ",".join(n[3:6] for n, v in zip(FN, vals) if v is True) or "-"
        tally.setdefault(status, {}).setdefault(hit, 0)
        tally[status][hit] += 1
        print(f"{name:16s} {status:8s} " + "  ".join(f"{str(v):>10s}" for v in vals)
              + f"   -> {hit}")
    print("\n按 GT status 汇总（命中了哪个判据）：")
    for st, d in sorted(tally.items()):
        print(f"  {st:8s} {d}")


if __name__ == "__main__":
    main()
