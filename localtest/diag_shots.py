"""对真实截图跑引擎，dump 完整识别/建议结果，用于定位识别与建议链路缺陷。

用法: py -3.10 localtest/diag_shots.py <img1> [<img2> ...]
不带参数时扫描 localtest/shots/ 下所有图片。
"""
import glob
import json
import os
import sys

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT_DIR = os.path.join(HERE, "shots")

INTEREST = [
    "status", "phase", "screen", "hand", "count", "top_score",
    "dingque", "swap", "swap_advice", "pick", "pick_candidates",
    "advice", "best", "message", "river", "my_river", "melds",
    "remaining", "remaining_tiles", "hot_tiles", "defense", "error_type",
]


def dump(path):
    image = cv2.imread(path)
    if image is None:
        print(f"!! cannot read {path}")
        return
    eng = Engine()
    res = eng.process(image)
    if res is None:
        print("!! process returned None")
        return
    try:
        data = json.loads(res.result)
    except Exception as e:  # noqa: BLE001
        print(f"!! result not json: {e}\nraw={getattr(res,'result',res)!r}")
        return
    print("=" * 70)
    print("FILE:", os.path.basename(path), "shape:", image.shape)
    print("ALL KEYS:", sorted(data.keys()))
    for k in INTEREST:
        if k in data:
            v = data[k]
            s = json.dumps(v, ensure_ascii=False)
            if len(s) > 400:
                s = s[:400] + " ...(truncated)"
            print(f"  {k:16s}= {s}")


def main():
    args = sys.argv[1:]
    if not args:
        args = sorted(glob.glob(os.path.join(SHOT_DIR, "*.jpg"))) + \
            sorted(glob.glob(os.path.join(SHOT_DIR, "*.png")))
    if not args:
        print(f"no images given and none in {SHOT_DIR}")
        return
    for p in args:
        dump(p)


if __name__ == "__main__":
    main()
