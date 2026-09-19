"""P0 评测底座 · 人工校验 montage。

对每张截图，裁出底部手牌带，把引擎检测到的每张牌用方框+标签叠加画出，
输出到 localtest/gt/montage/<name>.png，供人工快速核对"检测==肉眼所见"，
再据此修正 gt/shots.json 并翻转 verified。

用法: py -3.10 localtest/montage.py
"""
import glob
import contextlib
import io
import json
import os
import sys

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from engine.engine import Engine  # noqa: E402

SHOT_DIR = os.path.join(REPO, "localtest", "shots")
OUT_DIR = os.path.join(REPO, "localtest", "gt", "montage")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    # 每帧独立 Engine（避免上帧手牌泄漏）
    for path in sorted(glob.glob(os.path.join(SHOT_DIR, "*.jpg"))):
        fname = os.path.basename(path)
        img = cv2.imread(path)
        ih, iw = img.shape[:2]
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(Engine().process(img).result)
        tiles = d.get("tiles", [])
        canvas = img.copy()
        for t in tiles:
            x, y, w, h, lbl = t[0], t[1], t[2], t[3], t[4]
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(canvas, lbl or "?", (x + 4, y + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        # 只保留底部手牌带 + 顶部状态条，缩小体积便于翻阅
        y0 = int(ih * 0.62)
        crop = canvas[y0:ih, :]
        banner = f"{fname[:14]} status={d.get('status')} dq={d.get('dingque')} n={d.get('count')} conf={d.get('top_score')}"
        cv2.putText(crop, banner, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        out = os.path.join(OUT_DIR, fname.replace(".jpg", ".png"))
        cv2.imwrite(out, crop)
    print("montage written to", OUT_DIR, "files:", len(glob.glob(os.path.join(OUT_DIR, '*.png'))))


if __name__ == "__main__":
    main()
