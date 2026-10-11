# -*- coding: utf-8 -*-
"""A7 的直接测量：同一局连帧里「本家缺门」会不会变。

用户报的 A7 是「定缺徽章跳变」。定缺是一局只发生一次的事实 —— 一旦选定，整局
不可能改。所以只要在同一局的连帧里看到 `dingque_suit` 变了，就是 A7 复现。

素材：`localtest/shots_tuyou/rNN_*.jpg` 是同一局的连续 10 帧（按帧号有序），
带 manifest。这是本会话第一次能真正用「同一局多帧」测这件事 —— 之前只有单帧。

按本会话纪律：不跨局预热、每段用同一个 Engine（这才是"一局"），且先确认这些帧
真被判成途游牌局（否则测的是垃圾进垃圾出）。

⚠ 实测结果：A7 复现，而且不是“徽章跳变”那么轻 —— 是**整局账本被摧毁**：

  r01  ok       缺门=条   断门=[]
  r07  ok       缺门=条   断门=[1]
  r26  dingque  缺门=—    断门=[]      ← 阶段倒退，同时断门被清空
  r28/r40/r43  swap   缺门=—          ← 连续三帧“开局前”
  r51  ok       缺门=—    断门=[1,2]
  r57  dingque  缺门=—    断门=[]      ← 再倒一次、再清一次

因果链：局中帧误判回开局前阶段 → “命中阶段即清池”把牌河/断门一起清掉
→ 本家缺门从此永久丢失 → 清完牌河为空，反而让后面几帧更像“开局前”（自我强化）。

试过并已回退的修法：在阶段判定处拿累积单调账本 `_monotonic_discards` 做门。
不触发 —— 因为**清池就发生在同一帧**，判定时读到的账本已被清空。

下一步的准确入口（本轮已定位）：清池在 `engine.py` 约 2448 行那个函数里
（`self._pending_discards.clear()` 附近，同时清三本账）；而“命中阶段即清池”需
连续帧确认的逻辑在约 5157-5160 行。要修的是**清池的条件**，不是阶段标志本身：
一次阶段命中不得拿整局账本去赌（账本比阶段贵得多）。

⚠ 本轮已试过两种改法，都**不能单独上**（所以均已回退）。对照数据：

  基线（v1.7.22）       阶段倒退 5 帧（r26/28/40/43/57）   缺门跳变 1 次
  改法一 拿 in_play_by_ledger 否决清池 + 取消定缺豁免
                        倒退 0 帧、账本全程保住            缺门跳变 1 次
                        → 但被 `test_engine_phase_guard` 驳回：真新局的换三张面板
                          连续多帧命中必须清池，否则**新一局会带着上一局的牌河**。
                          清池正是旧账得以下台的唯一手段，不能否决。
  改法二 只删单帧旁路 `or (dq_p and curr_raw_n >= 13)`，一律要求连续确认
                        倒退 2 帧（r40/43）                  缺门跳变 **3 次**
                        → 47/47 守卫全绿（含新局清池契约），但用户看得见的徽章
                          跳变从 1 次变 3 次（条→—→万→—）。

结论：两半得合起来做 —— **改法二的“不许单帧清池” + 缺门一旦读到就不得被后续重读
抹掉（只能被同值确认，新值需连续多帧一致）**。单上任何一半都会把一个问题换成另一个。
注意：永久锁存已被 `test_dingque_stability_guard` 驳回过一次（它的“新局”场景不走
_reset_game_state），所以“不被抹掉”必须做成**只允许同值确认、不允许静默置空**，
并且新值要连续帧一致才能替换旧值。

⚠ 改法三（上面那个“合起来”的完整版）也已实现并实测，**仍然不能上**：跳变从 1 次
变 5 次（条→—→条→万→—→万）。原因很关键，它推翻了前两半的前提：

  删掉单帧旁路后 `_reset_game_state` 仍会在 r26/r57 触发（连续两帧命中就够），
  而每次 reset 都**合法地**清空锁存 —— 于是每次误判都制造一对新的跳变。

所以 A7 的主因不在状态机，而在识别层：**`is_dingque_phase` 在途游的局中帧上误报**
（r26、r57 都是牌河已有弃牌的局中帧，却 `dq_p=True`）。状态机上的闸门只能事后
补救，补不了“整局账本被一次误判摧毁”这个后果。

下一步的准确靶子（正反例已齐，不需要新素材）：
  正例（6 张）`shots_phase_fix/dq_*.jpg` —— 应判为定缺页
  反例（新增）`shots_tuyou/r26_4cda06de.jpg`、`r57_ce0dc83c.jpg` —— 局中帧，不得判为定缺页
  反例（已有）`shots_tuyou_select/s4_after_swap_cd04.jpg` —— 换牌后的局中帧
先量这些帧上定缺盘检测到底看到了什么（而不是继续调参数），再决定改哪条判据。

用法: py -3.10 -X utf8 localtest/probe_dingque_stability.py
"""
from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import re
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402

SUIT = {0: "万", 1: "筒", 2: "条", None: "—"}

files = sorted(glob.glob(os.path.join(HERE, "shots_tuyou", "r*_*.jpg")),
               key=lambda p: int(re.search(r"r(\d+)_", os.path.basename(p)).group(1)))
if not files:
    sys.exit("缺 shots_tuyou 连帧素材")

orig_lp, orig_lm = E.load_platform, E.load_mode
E.load_platform = lambda *a, **k: "tuyou"
E.load_mode = lambda *a, **k: "sc_hz"
rows = []
try:
    eng = E.Engine()                 # 同一个 Engine = 同一局
    eng.get_hand_detector()
    for p in files:
        img = cv2.imread(p)
        if img is None:
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
        rows.append((os.path.basename(p), d))
finally:
    E.load_platform, E.load_mode = orig_lp, orig_lm

print(f"{'帧':22s} {'status':>8s} {'count':>5s} {'本家缺门':>7s} {'对手断门':>12s}  变化")
# 注意：`x is not object()` 永远为真 —— object() 每次新建一个对象，与旧值永不全等。
# 上一版就因此在第一帧去 `list(object())` 直接报 TypeError。哨兵必须只造一个。
_MISS = object()
prev_suit = _MISS
prev_opq = _MISS
jumps = 0
for name, d in rows:
    s = d.get("dingque_suit")
    opq = tuple(sorted(d.get("opponents_dingque") or []))
    notes = []
    if prev_suit is not _MISS and s != prev_suit:
        notes.append(f"本家缺门 {SUIT.get(prev_suit)}→{SUIT.get(s)}")
        jumps += 1
    if prev_opq is not _MISS and opq != prev_opq:
        notes.append(f"断门 {list(prev_opq)}→{list(opq)}")
    print(f"{name:22s} {str(d.get('status')):>8s} {str(d.get('count')):>5s} "
          f"{SUIT.get(s):>7s} {str(list(opq)):>12s}  {' / '.join(notes)}")
    prev_suit, prev_opq = s, opq

print(f"\n同一局 {len(rows)} 帧中，本家缺门变化次数 = {jumps}")
print("定缺一局只发生一次：>0 即为 A7 复现（徽章跳变的直接证据）。")
print("注意：断门随牌河推进而增加是合法的（那是新读到的对手信息），不计入跳变。")
