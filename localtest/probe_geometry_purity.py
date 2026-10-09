# -*- coding: utf-8 -*-
"""坐实一件反常事：`detect_all_rows(classify=False)` 的**框数**为什么会随玩法变。

这条判据对外承诺的是「只给几何框，不做分类」（方向验证、条带对照都靠它，见
`engine._verify_orientation` 的注释：「分类（最贵的步骤）留到锁定方向后再做一遍」）。
如果它实际上受 `_mode_tiles` 影响，那"几何层"就不是一个稳定的事实来源：
上一轮量到同一帧在 sc_hz 下 13 框、std_tdh 下 14 框 —— 少的那一框就是用户看到的
「明明有 14 张只报 13 张」。

所以这里在**同一进程、同一 detector 实例**上只改 `_mode_tiles`，连跑三遍
（A→B→A），把「框数」与「每框的 label」都打出来。同进程交替是仓库的硬规矩：
跨进程比数在这里毫无意义。
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
import modes as M  # noqa: E402
from trainer.utils.convert import tiles34_index_to_mpsz  # noqa: E402

img = cv2.imread(os.path.join(HERE, "shots_report", "queshen_play_03.jpg"))
orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "gd_queshen"
E.load_mode = lambda *a, **k: "std_tdh"
try:
    eng = E.Engine()
    det = eng.get_hand_detector()
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm


def tiles_of(key):
    return {tiles34_index_to_mpsz(i) for i in M.available_set(key)}


def show(tag, classify):
    rows = det.detect_all_rows(img, classify=classify, allow_rotation=False)
    r = max(rows, key=len) if rows else []
    labs = [d[1] for d in r]
    print(f"  {tag:22s} 行数={len(rows)} 最长行框数={len(r)} "
          f"label 非空数={sum(1 for l in labs if l)}")
    return len(r)


for key in ("sc_hz", "std_tdh", "sc_hz"):
    det.set_mode_tiles(tiles_of(key))
    print(f"{key}: 牌集 {len(tiles_of(key))} 类")
    show("classify=False", False)
    show("classify=True", True)
