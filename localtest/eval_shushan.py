# -*- coding: utf-8 -*-
"""新平台回归门禁 · 蜀山四川麻将（红中血流）手牌识别 100% 断言。

协议与 eval_base 一致：每张截图新建 Engine、单帧评测（多帧确认类逻辑会破坏
本协议，改动前先看 eval_base 的架构约束）。GT 为人工逐图核对的手牌多重集。

后续新增平台帧：把截图放进 localtest/shots_shushan/（或新建平台目录）、
在 GT 里补一行，先跑 harvest/build_*_bank 收模板再进门禁。

用法: py -3.10 localtest/eval_shushan.py
退出码：任一 verified 帧手牌不匹配 → 1；否则 0。
"""
import contextlib
import io
import json
import os
import sys
from collections import Counter

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import engine.engine as ee  # noqa: E402
ee.load_mode = lambda: "sc_hz"  # 强制川麻血流红中模式（本地无 Java 模式文件）
from engine.engine import Engine  # noqa: E402

SHOT_DIR = os.path.join(REPO, "localtest", "shots_shushan")

# 人工逐图核对 GT（mpsz：1-9m=1-9万, 1-9p=筒, 1-9s=条, 7z=红中）
GT = {
    "s1.jpg": "7z8m8m8m9m7p7p7p9p9p",
    "s2.jpg": "7z8m8m8m9m7p7p9p9p7s7p",
    "s3.jpg": "7z8m8m8m9m7p7p9p9p7s7p",
    "s4.jpg": "7z2m2m3m3m4m4m4m5m5m7s7s7s",
    "s5.jpg": "7z2m2m3m3m4m4m4m5m5m7s7s7s",
    "s6.jpg": "7z2m2m3m3m4m4m4m5m5m7s7s7s",
    "s7.jpg": "7z2m2m3m3m4m4m4m5m5m7s7s7s",
}


def codes(s):
    return sorted(s[i:i + 2] for i in range(0, len(s), 2))


def run_engine(img):
    with contextlib.redirect_stdout(io.StringIO()):
        return json.loads(Engine().process(img).result)


def main():
    n_hand = n_tile = n_ok = 0
    fails = []
    for fname, gt in sorted(GT.items()):
        img = cv2.imread(os.path.join(SHOT_DIR, fname))
        assert img is not None, f"读不到 {fname}"
        data = run_engine(img)
        det = codes(data.get("hand") or "")
        g = codes(gt)
        inter = sum((Counter(g) & Counter(det)).values())
        n_hand += 1
        n_tile += max(len(g), len(det), 1)
        n_ok += inter
        exact = inter == len(g) == len(det)
        mark = "OK " if exact else "FAIL"
        print(f"[{mark}] {fname}: gt={len(g)}张 det={len(det)}张 交={inter} "
              f"status={data.get('status')}")
        if not exact:
            print(f"      gt : {''.join(g)}")
            print(f"      det: {''.join(det)}")
            fails.append(fname)
    print(f"\n手牌整帧: {n_hand - len(fails)}/{n_hand}  逐张: {n_ok}/{n_tile}")
    print("RESULT:", "PASS" if not fails else f"FAIL {fails}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
