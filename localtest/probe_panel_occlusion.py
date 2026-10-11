# -*- coding: utf-8 -*-
"""B10 测量：自家悬浮窗压住手牌行时，引擎读到几张、有没有如实报「没读全」。

用户补的蜀山定缺页上出现过「屏上 14 张牌、面板显示 手牌(5张)」。可能原因不是识别
坏了，而是**我们自己的悬浮窗盖住了手牌行的一部分** —— 那部分牌在截图中根本不存在
于像素里。

⚠ 真值不能拍脑袋：上一版把六张夹具都写成「屏上 14 张」，于是把三张**正确的 13 张**
帧报成了「静默少报」—— 假判据比没判据更坑。**定缺页非庄家就是 13 张**（庄家 14）。
所以现在不拿猜的真值下"对/错"结论，只报引擎自己说了什么：读到几张、有没有交代 missing。

实测结论（本轮）：六张定缺页夹具上引擎都读到 13/14 张、`hand_missing=0`，用户截图里
那个「只显 5 张」的状态**在这批夹具上不复现**。B10 不能算"已修"，只能算"现有夹具上
无法复现"；要定性需要一帧面板确实盖住手牌行、且引擎仍只读出几张的截图。

用法: py -3.10 -X utf8 localtest/probe_panel_occlusion.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402

CASES = [
    ("shots_phase_fix/dq_zj_01.jpg", "zj_sichuan", "面板在左上，不挡牌行"),
    ("shots_phase_fix/dq_tencent_01.jpg", "tencent", "面板未出现"),
    ("shots_phase_fix/dq_shushan_01.jpg", "shushan", "面板未出现"),
    ("shots_phase_fix/dq_shushan_02.jpg", "shushan", "面板压住手牌行左侧"),
    ("shots_phase_fix/dq_tuyou_01.jpg", "tuyou", "面板压住手牌行左侧"),
    ("shots_phase_fix/dq_jj_01.jpg", "jj", "面板压住手牌行上方"),
]

print(f"{'夹具':40s} {'读到':>4s} {'missing':>7s} {'dim':>5s}  说明")
for rel, platform, note in CASES:
    p = os.path.join(HERE, rel)
    img = cv2.imread(p) if os.path.exists(p) else None
    if img is None:
        print(f"{rel:40s} 缺素材")
        continue
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, _p=platform, **k: _p
    E.load_mode = lambda *a, **k: "sc_hz"
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
    n = int(d.get("count") or 0)
    miss = int(d.get("hand_missing") or 0)
    dim = bool(d.get("hand_dim"))
    print(f"{rel:40s} {n:4d} {miss:7d} {str(dim):>5s}  {note}")
