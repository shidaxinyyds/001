# -*- coding: utf-8 -*-
"""一帧判定：途游局中帧上，当前引擎到底会不会输出换三张内容。

起因：用户截图（`shots_multi/tuyou_swap_01.jpg`，屏上是「大家在等您出牌哦」的局中
画面）里，我们的面板写着「换三张 准备换出【7条、8条、8条】」。代码里
`swap_advice` 是受 `is_swap_phase` 门控的（engine.py:6041），所以两种可能：
  · 截图来自旧版 APK，这条已经不存在了；
  · 或者门控之外还有别的路径把换牌内容送上了屏（缓存沿用、tactical 文案等）。
不分辨就"修"会白改一版，也可能漏掉真通道。所以直接跑当前引擎，把面板会拿到的
每个可能提到换牌的字段都打出来。

用法: py -3.10 -X utf8 localtest/probe_swap_leak.py
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

# 可指帧：两张用户截图都出现过“局中却显示换三张博弈”，都要能单独复验。
_NAME = sys.argv[1] if len(sys.argv) > 1 else "tuyou_swap_01.jpg"
_PLAT = sys.argv[2] if len(sys.argv) > 2 else "tuyou"
FRAME = os.path.join(HERE, "shots_multi", _NAME)
if not os.path.exists(FRAME):
    FRAME = os.path.join(HERE, "shots_batch3", _NAME)
if not os.path.exists(FRAME):
    FRAME = os.path.join(HERE, "shots_report", _NAME)

img = cv2.imread(FRAME)
if img is None:
    sys.exit(f"读不到夹具 {FRAME}——本判定不成立，别把它当引擎行为")

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: _PLAT
E.load_mode = lambda *a, **k: "sc_hz"
try:
    eng = E.Engine()
    eng.get_hand_detector()
    # 预热帧**默认关掉**。上一版这里先喂 2 帧 zj_play_01 再喂目标帧，于是同一个引擎
    # 里混进了另一张牌桌的弃牌账本，单帧本来没问题的图也被顶成「同型已见 5 张」——
    # 我因此一度判定“当前引擎仍在误判脏帧”，那是探针造的，不是引擎做的。
    # 要测串帧泄漏，预热帧必须与目标帧同平台同局；需要时传 --warm 再开。
    if "--warm" in sys.argv:
        warm = cv2.imread(os.path.join(HERE, "shots_multi", "zj_play_01.jpg"))
        for _ in range(2):
            if warm is not None:
                with contextlib.redirect_stdout(io.StringIO()):
                    eng.process(warm)
    with contextlib.redirect_stdout(io.StringIO()):
        d = json.loads(eng.process(img).result)
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm

swap = d.get("swap_phase")
print(f"swap_phase={swap}  dq_phase={d.get('dingque_phase')}  "
      f"pick_phase={d.get('pick_phase')}  status={d.get('status')}")
print(f"swap_tiles={d.get('swap_tiles')}")
print(f"phase_label={d.get('phase_label')!r}  badge={d.get('tactical_badge')!r}")
print(f"intent={d.get('tactical_intent')!r}")
print(f"message={d.get('message')!r}")
adv = d.get("advice") or []
print(f"advice 条数={len(adv)}  best={d.get('best')!r}")
for i, a in enumerate(adv[:3]):
    if isinstance(a, dict):
        print(f"  advice[{i}] tile={a.get('tile')!r} reason={str(a.get('reason'))[:60]!r}")

# 把「面板上任何一处可能出现换牌字样的字段」都扫一遍，不只看 swap_tiles
blob = json.dumps(d, ensure_ascii=False)
hits = [k for k in ("换三张", "换牌", "换【", "准备换出", "博弈") if k in blob]
print(f"\npayload 中出现换牌字样的关键词：{hits or '无'}")
if swap is False and hits:
    where = [k for k, v in d.items() if isinstance(v, str) and any(
        w in v for w in ("换三张", "换牌", "博弈"))]
    print(f"  疑似泄漏字段：{where}")
    print("  ⇒ 门控之外确有路径把换牌内容送上屏，需要修")
elif swap is False and not hits:
    print("  ⇒ 当前引擎在该帧不输出任何换牌内容；截图现象属旧版，无需修改")
