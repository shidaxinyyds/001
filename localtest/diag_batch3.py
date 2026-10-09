# -*- coding: utf-8 -*-
"""把这批 20 张真机截图在 PC 上逐帧跑一遍，打印引擎自己承认的那套事实。

为什么要跑这一遍：截图里面板显示的张数/阶段，是**手机端连续跑帧**的产物（带阻尼、
稳定手牌、阶段机、定缺锁存）；而 PC 上按同一帧单张跑，是没有历史包袱的裸读数。
两边一比就能分清楚：某一帧读不出，到底是"算法本身看不见"，还是"帧间状态把旧读数
粘在屏上"。这两件事的修法完全不同，混在一起就会像前几轮那样修了还在。

用法: py -3.10 -X utf8 localtest/diag_batch3.py [帧名前缀 ...]
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
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
import layer_cost as lc  # noqa: E402

SHOT_DIR = os.path.join(HERE, "shots_batch3")

# 平台按截图里的游戏名；玩法一律按面板标题上写着的那一条（这批全是「血流红中」）
FRAMES = [
    ("jj_play_03.jpg", "jj"),
    ("jj_play_04.jpg", "jj"),
    ("jj_swap_01.jpg", "jj"),
    ("ad_screen_01.jpg", "zj_sichuan"),      # 不是牌局画面：看引擎会不会照报
    ("shushan_dingque_03.jpg", "shushan"),
    ("shushan_settle_01.jpg", "shushan"),
    ("tuyou_play_03.jpg", "tuyou"),
    ("tuyou_play_04.jpg", "tuyou"),
    ("tuyou_play_05.jpg", "tuyou"),
    ("tuyou_swap_02.jpg", "tuyou"),
    ("queshen_play_06.jpg", "gd_queshen"),
    ("queshen_play_07.jpg", "gd_queshen"),
    ("queshen_play_08.jpg", "gd_queshen"),
    ("queshen_play_09.jpg", "gd_queshen"),
    ("zj_idle_01.jpg", "zj_sichuan"),
    ("zj_dingque_02.jpg", "zj_sichuan"),
    ("zj_play_03.jpg", "zj_sichuan"),
    ("zj_popup_02.jpg", "zj_sichuan"),
    ("zj_play_04.jpg", "zj_sichuan"),
    ("zj_swap_03.jpg", "zj_sichuan"),
]
MODE = "sc_hz"


def run(img, platform, mode):
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm


def main() -> int:
    keys = sys.argv[1:]
    for name, pf in FRAMES:
        if keys and not any(k in name for k in keys):
            continue
        img = cv2.imread(os.path.join(SHOT_DIR, name))
        if img is None:
            print(f"!! 读不到 {name}")
            continue
        d = run(img, pf, MODE)
        hand = lc.canon_mpsz(d.get("hand", ""))
        tiles = d.get("tiles") or []
        empty = sum(1 for t in tiles if not t[4])
        print(f"{name:26s} {pf:11s} status={d.get('status'):8s} "
              f"label={d.get('phase_label') or '-':12s} badge={d.get('tactical_badge') or '-':6s} "
              f"框={len(tiles):2d} 空label={empty} 面板张数={len(hand):2d} "
              f"dim={bool(d.get('hand_dim'))} 冲突={len(d.get('hand_gate_conflict') or [])}")
        print(f"    hand={' '.join(hand)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
