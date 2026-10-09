# -*- coding: utf-8 -*-
"""喂同一帧 6 次，问一个二选一的问题：牌河到底是「根本没扫」还是「扫了没等到」。

牌河/副露检测是异步的（`_river_executor` + `_river_future`），结果在后续帧里认领；
而 `allow_river_scan` 又要求 `_match_started`。所以「面板上牌河为空」有两种成因，
修法完全相反：
  · 根本没扫（门被卡住 / 取区全错）→ 要改门或改取区；
  · 扫了但结果没被认领（异步链断）→ 要改认领那一环。
只看一帧分不出来，所以按同一帧连喂 6 次：若是"没等到"，第 2~3 帧起就该出现计数。

用法: py -3.10 -X utf8 localtest/probe_river_repeat.py [帧名]
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "zj_play_03.jpg"
# 后台牌河扫描实测要 1~3s，而重复喂同一帧只要几百毫秒：帧数太短时「牌河为空」
# 只能说明**没等到**，不能说明**没扫到**。所以帧数可调，默认给到足够跨过一次扫描。
n_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 20
img = cv2.imread(os.path.join(HERE, "localtest", "shots_batch3", name))
if img is None:
    # 读不到图时不能继续往下喂：`process(None)` 会回一条 decode_error，看起来像
    # 「引擎报错了」，而真相是探针自己的路径写错——上一版就踩了这个坑，
    # 六帧全报 decode_error 差点被当成引擎缺陷。夹具读不到就当场说清楚。
    sys.exit(f"!! 读不到夹具：{name}（应在 localtest/shots_batch3/ 下）")
orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    for i in range(n_frames):
        # 每帧给一个 1px 位移：同一张图直接重喂会被帧差去重直接回吐上一份 payload，
        # 几乎不花墙钟时间——那会量到「后台任务始络没完成」的假现象（上一轮就是这么
        # 把“测不到”误读成“设备上就是空”）。位移不动牌的内容，只让去重不拦。
        frame = np.roll(img, i, axis=1)
        with __import__("contextlib").redirect_stdout(__import__("io").StringIO()):
            d = json.loads(eng.process(frame).result)
        dg = d.get("diag") or {}
        print(f"第{i + 1}次  status={d.get('status'):8s} "
              f"手牌={len((d.get('hand') or '')) // 2:2d}  "
              f"river_zones={json.dumps(dg.get('river_zones'), ensure_ascii=False)} "
              f"discards={d.get('discard_count')}  "
              f"river_error={json.dumps(dg.get('river_error'), ensure_ascii=False)}  "
              f"submits={dg.get('river_submits')} consumes={dg.get('river_consumes')}  "
              f"perf.river={json.dumps((dg.get('perf') or {}).get('river'))}")
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm
