# -*- coding: utf-8 -*-
"""一次只问一个问题：广告页连喂时，空帧计数到底是什么值。

上一轮我写「空帧≥2 就清建议」的闸门完全没生效，怀疑是读错键或该路径根本不累加。
先把 `diag.empty_frames`、稳定器内部 `_empty_frames`、以及 count/advice 并排打出来。

用法: py -3.10 -X utf8 localtest/probe_empty_frames.py
"""
import contextlib
import io
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import engine.engine as E  # noqa: E402

E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"

eng = E.Engine()
eng.get_hand_detector()
g = cv2.imread(os.path.join(HERE, "shots_batch3", "zj_play_03.jpg"))
a = cv2.imread(os.path.join(HERE, "shots_batch3", "ad_screen_01.jpg"))
if g is None or a is None:
    sys.exit("缺夹具")

with contextlib.redirect_stdout(io.StringIO()):
    eng.process(g)

for i in range(4):
    with contextlib.redirect_stdout(io.StringIO()):
        d = json.loads(eng.process(a).result)
    dg = d["diag"]
    print(f"ad#{i + 1}  diag.empty_frames={dg.get('empty_frames')}  "
          f"stab内部={getattr(eng._hand_stab, '_empty_frames', None)}  "
          f"raw_hand={dg.get('raw_hand')}  count={d.get('count')}  "
          f"advice={len(d.get('advice') or [])}  carried={d.get('hand_carried_over')}  "
          f"status={d.get('status')}")
print("diag 全部键:", sorted(dg.keys()))
