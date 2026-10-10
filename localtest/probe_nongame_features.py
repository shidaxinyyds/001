# -*- coding: utf-8 -*-
"""量「非牌局屏 vs 真牌桌」的可分特征，为 A4/A6 的剩余根因定判据。

前几轮已把靶子收窄到这一条：开局等待页与两张结算页被当成局中（`_is_mahjong_table`
对三张非牌局帧全返回 True），于是面板在结算页上继续报「本家缺万」这类结论。
`localtest/probe_detect_dingque.py` 已证伪「只在定缺页采纳缺门」这条路（局中的
「缺」角标本来就是合法读数）。

这里不再提假设，只打数字。候选特征三条，全部是牌局的物理必要条件：

  1) 底部手牌带能不能切出牌 —— 真牌桌必有 13/14 张自家牌；结算/等待页没有
  2) 牌河带有没有弃牌 —— 结算页牌已摊开在中间，不在河带
  3) 手牌张数是否落在合法张数集合 —— 局中只可能是 1/2/4/5/7/8/10/11/13/14

正例用仓库里已核实的真牌局帧，负例用用户补的 3 张非牌局屏。

用法: py -3.10 -X utf8 localtest/probe_nongame_features.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402

CASES = [
    # 负例：用户补的非牌局屏
    ("shots_phase_fix/nongame_lobby_01.jpg", "zj_sichuan", "等待页"),
    ("shots_phase_fix/nongame_settle_01.jpg", "shushan", "结算页"),
    ("shots_phase_fix/nongame_settle_02.jpg", "zj_sichuan", "结算页"),
    ("shots_batch3/shushan_settle_01.jpg", "shushan", "结算页"),
    ("shots_batch3/zj_idle_01.jpg", "zj_sichuan", "空闲页"),
    ("shots_batch3/ad_screen_01.jpg", "zj_sichuan", "广告页"),
    # 正例：已核实的真牌局帧
    ("shots_batch3/zj_play_03.jpg", "zj_sichuan", "局中"),
    ("shots_batch3/zj_play_04.jpg", "zj_sichuan", "局中"),
    ("shots_batch3/shushan_dingque_03.jpg", "shushan", "局中"),
    ("shots_batch3/tuyou_play_03.jpg", "tuyou", "局中"),
    ("shots_batch3/jj_play_03.jpg", "jj", "局中"),
    ("shots_phase_fix/dq_zj_01.jpg", "zj_sichuan", "定缺页"),
    ("shots_phase_fix/dq_shushan_01.jpg", "shushan", "定缺页"),
]

VALID = {1, 2, 4, 5, 7, 8, 10, 11, 13, 14}

print(f"{'帧':44s} {'类别':8s} {'count':>5s} {'张数合法':>8s} "
      f"{'牌河':>4s} {'status':>9s} {'缺门':>4s}")
sep_pos, sep_neg = [], []
for rel, platform, kind in CASES:
    p = os.path.join(HERE, rel)
    img = cv2.imread(p) if os.path.exists(p) else None
    if img is None:
        print(f"{rel:44s} {kind:8s}  缺素材")
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
    river = sum(int(v or 0) for v in ((d.get("diag") or {}).get("river_zones") or [])) \
        if isinstance((d.get("diag") or {}).get("river_zones"), list) else 0
    legal = n in VALID
    print(f"{rel:44s} {kind:8s} {n:5d} {str(legal):>8s} {river:4d} "
          f"{str(d.get('status')):>9s} {str(d.get('dingque_suit')):>4s}")
    (sep_pos if kind in ("局中", "定缺页") else sep_neg).append((n, legal))

# 可分性：真牌局帧是否都满足「张数合法」，非牌局屏是否都不满足
all_legal_pos = all(l for _n, l in sep_pos)
any_legal_neg = any(l for _n, l in sep_neg)
print(f"\n正例全部张数合法: {all_legal_pos}   负例中存在合法张数: {any_legal_neg}")
print("若前者 True 且后者 False，则「手牌张数合法性」单独就能当非牌局判据。")
