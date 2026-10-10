# -*- coding: utf-8 -*-
"""一步定性质：定缺阶段漏判，是判据没认出来，还是引擎把它压掉了？

两张定缺正例帧的 `dingque_phase` 都是 False。可能有两类原因，修法相反：

  A) 检测器的 `is_dingque_phase()` 本身就返回 False  → 识别层，要改判据；
  B) 检测器返回 True，但引擎在调用链上把它压掉了
     （`is_dingque_mode(self.mode)` 为假、或 %2 节流、或迟滞/连续帧确认没攒够）
     → 接线/门控层，改判据是白改。

所以直接并排打：检测器原始返回值 vs 引擎最终下发值。方法上按本会话教训：
**每帧新建检测器**（复用实例会让平台模板 bank 锁死，我已经因此误判过一次 C16）。

用法: py -3.10 -X utf8 localtest/probe_dingque_gate.py
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
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402
from modes import is_dingque_mode  # noqa: E402
from platforms import set_platform_explicit  # noqa: E402

CASES = [
    ("zj_swap_03.jpg", "zj_sichuan"),
    ("shushan_swap_01.jpg", "shushan"),
    ("tuyou_swap_02.jpg", "tuyou"),
    ("shushan_dingque_03.jpg", "shushan"),
    ("zj_dingque_02.jpg", "zj_sichuan"),
    ("shushan_settle_01.jpg", "shushan"),
    ("zj_idle_01.jpg", "zj_sichuan"),
]


def load(name):
    for d in ("shots_batch3", "shots_report", "shots_multi"):
        p = os.path.join(HERE, d, name)
        if os.path.exists(p):
            img = cv2.imread(p)
            if img is not None:
                return img
    return None


for name, platform in CASES:
    img = load(name)
    if img is None:
        print(f"{name:26s} 缺素材")
        continue
    set_platform_explicit(platform)
    # 每帧新建检测器：复用实例会让模板 bank 停在第一次构造时的平台
    ys = gs = None
    try:
        ys = bool(YOLODetector().is_dingque_phase(img))
    except Exception as e:
        ys = f"err {type(e).__name__}"
    try:
        gs = bool(TencentGridDetector().is_dingque_phase(img))
    except Exception as e:
        gs = f"err {type(e).__name__}"
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: "sc_hz"
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
    print(f"{name:26s} 判据: yolo={str(ys):5s} grid={str(gs):5s} | "
          f"引擎 dq_phase={str(bool(d.get('dingque_phase'))):5s} "
          f"dq_suit={d.get('dingque_suit')} 对手断门={d.get('opponents_dingque')} | "
          f"mode是定缺玩法={is_dingque_mode('sc_hz')}")
