"""P0 评测底座 · GT 引导生成器。

对 localtest/shots/ 下每张截图跑引擎，把「高置信」帧的当前输出写成【初版/未校验】
ground-truth 到 localtest/gt/shots.json，供人工用 montage 校验后翻转 verified。

设计原则：
- verified=false 的条目只作占位，eval_base 默认不计分（避免"自己测自己"的假绿灯）。
- 只有人工核对过、翻转 verified=true 的条目，才作为回归门禁的判定依据。

用法: py -3.10 localtest/gen_gt.py
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
GT_DIR = os.path.join(REPO, "localtest", "gt")
GT_PATH = os.path.join(GT_DIR, "shots.json")
CONF = 0.95


def main():
    os.makedirs(GT_DIR, exist_ok=True)
    # 每张截图都是独立对局帧：必须用全新 Engine，避免 _TileVoter/_FrameSkipper
    # 把上一帧的手牌临时平滑/缓存泄漏到下一帧（否则遮挡帧会echo上一帧结果）。
    entries = []
    for path in sorted(glob.glob(os.path.join(SHOT_DIR, "*.jpg"))):
        fname = os.path.basename(path)
        img = cv2.imread(path)
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(Engine().process(img).result)
        conf = d.get("top_score", 0.0)
        hand = d.get("hand", "")
        entries.append({
            "file": fname,
            "verified": False,
            "confidence": round(float(conf), 3),
            "hand": hand,
            "count": d.get("count", 0),
            "status": d.get("status", ""),
            "dingque": d.get("dingque"),
            "best": d.get("best", ""),
            "note": ("高置信，待人工核对牌面" if conf >= CONF and hand
                     else "低置信/遮挡，仅记录当前输出"),
        })
    with open(GT_PATH, "w", encoding="utf-8") as f:
        json.dump({"shots": entries}, f, ensure_ascii=False, indent=2)
    hi = sum(1 for e in entries if e["confidence"] >= CONF and e["hand"])
    print(f"wrote {GT_PATH}")
    print(f"total={len(entries)} high_conf={hi} verified=0 (需人工校验)")


if __name__ == "__main__":
    main()
