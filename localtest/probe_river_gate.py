# -*- coding: utf-8 -*-
"""把 `run_river_scan` 的两个门分别量一遍，看牌河到底被谁关住的。

`run_river_scan = allow_river_scan and self._should_scan_river(image)`，两个门任缺
一个都会得到「牌河四区永远为 0」这个同一个现象，但成因和修法完全不同：
  · allow_river_scan 被阶段门卡住（is_dq/swap/pick 任一为真，或川麻玩法下
    `_match_started` 没点亮）→ 是门的设计问题；
  · `_should_scan_river` 差分门一直判「没变」→ 是签名/基准问题。
所以这里把两个门连同它们的全部输入项一起打出来，不猜。

用法: py -3.10 -X utf8 localtest/probe_river_gate.py [帧名]
"""
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
from modes import is_dingque_mode  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "zj_play_03.jpg"
img = cv2.imread(os.path.join(HERE, "localtest", "shots_batch3", name))
if img is None:
    sys.exit(f"!! 读不到夹具：{name}")

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "zj_sichuan"
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    import contextlib
    import io
    for i in range(3):
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
        allow = (not d.get("dingque_phase") and not d.get("swap_phase")
                 and not d.get("pick_phase"))
        print(f"第{i + 1}次  payload阶段门: dq={d.get('dingque_phase')} "
              f"swap={d.get('swap_phase')} pick={d.get('pick_phase')} "
              f"→ 非阶段门={allow}")
        print(f"        is_dingque_mode(sc_hz)={is_dingque_mode('sc_hz')} "
              f"_match_started={getattr(eng, '_match_started', None)} "
              f"dingque_suit={d.get('dingque_suit')!r}")
        print(f"        _should_scan_river={eng._should_scan_river(img)} "
              f"sig已建={eng._river_scan_sig is not None} "
              f"wait={eng._river_scan_wait} future={eng._river_future}")
        print(f"        river_zones={json.dumps((d.get('diag') or {}).get('river_zones'))}")
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm
