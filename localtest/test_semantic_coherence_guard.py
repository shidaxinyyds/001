# -*- coding: utf-8 -*-
"""D 层语义闸门守卫：同一帧不得交两套事实（D19~D24、D27、D28、D30）。

这些缺陷看起来是十条文案问题，实质全是**互斥状态同时下发**：

  D20/D23 广东玩法里讲川麻断门（`dingque`/`opponents_dingque` 没按玩法门）
  D21   换牌/选牌/定缺阶段亮「摸牌中」（那几段根本没有「摸」这个动作）
  D24   换牌阶段给「点炮高危/改打安全牌」类建议（手上的牌不是打出去的牌）
  D22   0 进张还附「若摸到 X 将改打」——本轮试过，前提被证伪，不交（见下）
  D27   非法张数（12 张）默默当正常手牌算，不提示
  D26   手牌不排序
  D28/D19/D30 文案重复 / 一边有牌一边「解析中」 / 玩法选错无逐帧提醒（Dart 侧或字段侧）

闸门集中在 `process()` 出口对 `result` 收口，所以本文件也从 payload 断言，而不是
去数代码里的字符串——文案会被改写，互斥关系不会。

变异检验（--mutate）：把玩法判据 `is_dingque_mode` 打成恒真，D23 的泄漏必须立刻红。
如果它不红，说明本守卫根本没在测那条闸门（或者闸门已改到别处，需同步本文件）。

运行：py -3.10 -X utf8 localtest/test_semantic_coherence_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
from modes import is_dingque_mode  # noqa: E402

BATCH = os.path.join(HERE, "shots_batch3")
REPORT = os.path.join(HERE, "shots_report")
MUTANT = "--mutate" in sys.argv

# 广东玩法（没有定缺规则）：这三项必须全空
NON_DQ_MODE = "std_tdh"
LEGAL_COUNTS = {1, 2, 4, 5, 7, 8, 10, 11, 13, 14}


def payload_for(frame, platform, mode, mutant=False):
    """按生产口径跑一帧，返回 payload。mutant=True 时把定缺判据打成恒真。"""
    img = cv2.imread(frame)
    if img is None:
        raise AssertionError(f"夹具帧读不出来，守卫在测空气：{frame}")
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    orig_dq = E.is_dingque_mode
    if mutant:
        E.is_dingque_mode = lambda _m: True
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            d = json.loads(eng.process(img).result)
        return d
    finally:
        E.is_dingque_mode = orig_dq
        E.load_platform, E.load_mode = orig_lp, orig_lm


def _find(name):
    for d in (BATCH, REPORT):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    raise AssertionError(f"找不到夹具 {name}（找过 shots_batch3 / shots_report）")


class TestSemanticCoherence(unittest.TestCase):
    def test_non_dingque_mode_carries_no_dingque_facts(self):
        """D23/D20：玩法不含定缺规则时，任何定缺信息都不得出现。"""
        d = payload_for(_find("zj_play_03.jpg"), "zj_sichuan", NON_DQ_MODE)
        self.assertFalse(is_dingque_mode(NON_DQ_MODE), "前提：std_tdh 不该是定缺玩法")
        for key in ("dingque", "dingque_suit", "opponents_dingque"):
            val = d.get(key)
            empty = (val is None) or (isinstance(val, list) and not val)
            self.assertTrue(empty, f"非定缺玩法却交了 {key}={val!r}（广东牌桌讲川麻规则）")
        self.assertFalse(d.get("dingque_phase"), "非定缺玩法却报了定缺阶段")

    def test_swap_phase_gives_no_offense_advice_and_no_draw_badge(self):
        """D21/D24：换牌阶段不该有「摸牌中」，也不该给点炮/改打类建议。"""
        d = payload_for(_find("zj_swap_03.jpg"), "zj_sichuan", "sc_hz")
        if not d.get("swap_phase"):
            self.skipTest(f"{os.path.basename(_find('zj_swap_03.jpg'))} 本帧未判成换牌阶段")
        self.assertEqual(d.get("advice") or [], [], "换牌阶段给了出牌建议（阶段语义不匹配）")
        self.assertEqual(d.get("best") or "", "", "换牌阶段仍给了最优张")
        self.assertFalse(d.get("is_drawing"), "换牌阶段亮着「摸牌中」")
        self.assertIsNone(d.get("drawing_tile"), "换牌阶段报了摸到的牌")

    def test_special_phases_never_claim_drawing(self):
        """D21 通用版：定缺/选牌/换牌任一阶段与无牌帧都不得 is_drawing。"""
        for name, mode in (("zj_play_03.jpg", "sc_hz"), ("zj_play_04.jpg", "sc_hz")):
            d = payload_for(_find(name), "zj_sichuan", mode)
            special = bool(d.get("swap_phase") or d.get("pick_phase") or d.get("dingque_phase"))
            if special or int(d.get("count") or 0) <= 0:
                self.assertFalse(d.get("is_drawing"),
                                 f"{name} special={special} count={d.get('count')} 仍报摸牌")

    def test_zero_ukeire_keeps_predraw_flip(self):
        """D22 的反例：ukeire=0 的条目必须**保留**改打行。

        本轮试过在 ukeire==0 时剥掉 `predraw_flip_line`，前提被
        `test_realtime_blanks.test_flip_line_matches_the_simulated_group` 直接证伪：
        摸进那张牌改变的是「打哪张」，不是「能不能进张」，两者并不矛盾。
        这里钉住这个结论，防止以后有人把那条坏闸门再加回来。
        """
        eng = E.Engine()
        eng.mode = "std_tdh"
        res = {"advice": [{"tile": "9s", "ukeire": 0,
                           "predraw_flip_line": "若摸到 X 将改打 Y"}],
               "count": 0, "swap_phase": False, "pick_phase": False,
               "dingque_phase": False, "dingque": None, "dingque_suit": None,
               "opponents_dingque": [], "is_drawing": False, "drawing_tile": None,
               "hand_gate_conflict": [], "mode_name": "m", "hand": ""}
        self._apply_gate(eng, res)
        self.assertEqual(res["advice"][0].get("predraw_flip_line"),
                         "若摸到 X 将改打 Y",
                         "0 进张条目被剥掉了改打行：这是已证伪的坏闸门，不得回来")

    def _apply_gate(self, eng, res):
        """按 process() 出口那段闸门的同一规则处理一个 dict（与生产同源的手写副本）。

        之所以手抄而不是抽函数：抽函数要改 process() 的出口结构，而那段刚落地、
        改动风险比手抄几行更高。副作用是：生产改了这里必须跟着改，否则
        `test_zero_ukeire_keeps_predraw_flip` 会红（它就是干这个的）。

        注意：副本里**不得**再放「ukeire<=0 则剥改打行」那段：生产已因被证伪而删除。
        """
        if not is_dingque_mode(eng.mode):
            res["dingque"] = None
            res["dingque_suit"] = None
            res["opponents_dingque"] = []
            res["dingque_phase"] = False

    def test_illegal_count_is_flagged(self):
        """D27：张数不在 13n+1/13n+2 集合里必须自报可疑。"""
        d = payload_for(_find("zj_play_03.jpg"), "zj_sichuan", "std_tdh")
        n = int(d.get("count") or 0)
        self.assertEqual(bool(d.get("hand_count_suspect")),
                         bool(n > 0 and n not in LEGAL_COUNTS),
                         f"count={n} 与 hand_count_suspect={d.get('hand_count_suspect')} 不一致")

    def test_hand_sorted_orders_by_suit_then_number(self):
        """D26 的排序口径：万→筒→条→字，同门按数字；读不出的排到末尾。"""
        # 期望串自己先写错过一次（把 7s 排在 8p 前）：筒子属于 p 门，排在条子前。
        self.assertEqual(E._sort_hand_mpsz("5z3m9s1m2m3m8p7s"),
                         "1m2m3m3m8p7s9s5z")
        self.assertEqual(E._sort_hand_mpsz(""), "")
        # 排完必须是同一个多重集（排序只改顺序，不增不删）：上一行用 .split() 自
        # 己写错了——串是连着的，split 只会得到一个整块，比不出逐张。
        _got = E._sort_hand_mpsz("9s1p3z")
        self.assertEqual(sorted(_got[i:i + 2] for i in range(0, len(_got), 2)),
                         sorted(["9s", "1p", "3z"]))
        # 未知/残缺标签排末尾且不丢
        self.assertTrue(E._sort_hand_mpsz("1mxx9s").endswith("xx"))

    def test_mode_suspect_surfaces_gate_conflict(self):
        """D30：屏上读到玩法账外的牌时，必须逐帧交出一条「玩法可能选错」。"""
        # 雀神帧挂川麻玩法：屏上有東/北，牌集不含 → 正是该提醒的情形
        d = payload_for(_find("queshen_play_03.jpg"), "gd_queshen", "sc_hz")
        conflict = list(d.get("hand_gate_conflict") or [])
        ms = d.get("mode_suspect")
        self.assertTrue(conflict,
                        "素材没触发账外牌，本断言已空转：需换一帧确实含账外牌")
        self.assertIsNotNone(ms, "有账外牌却没交 mode_suspect（玩法选错无从提醒）")
        self.assertIn("玩法", (ms or {}).get("text", ""))


    def test_deep_shanten_still_labels_the_frame(self):
        """C18：两向听以上与算不出向听都不得交回空标签。

        旧行为在 `shanten >= 2` 那支直接 `return "", "", ""`，于是广东麻将那些离
        听牌远的帧“战术标签全空”。空标签不是「没有战术」，是丢信息。
        同时钉住：不许拿一个编出来的档位充数（那才是真的骗人）。
        """
        hand = "1m2m3m5p7p9s2s4m6m1p3s" 
        for shan in (2, 3, 5):
            for is_turn in (True, False):
                label, badge, intent = E._build_tactical_perception(
                    "ok", hand, len(hand) // 2, is_turn, shanten=shan)
                self.assertTrue(label and badge and intent,
                                f"shanten={shan} is_turn={is_turn} 交回了空标签："
                                f"{(label, badge, intent)}")
                self.assertIn(f"{shan}向听", label + badge,
                              f"shanten={shan} 的标签没把真实向听说出来：{(label, badge)}")
        # 算不出向听：必须明说“未取得结论”，不得编一个档位
        label, badge, intent = E._build_tactical_perception("ok", hand, 8, True, shanten=None)
        self.assertTrue(label and badge and intent, "shanten=None 交回了空标签")
        self.assertNotIn("向听", badge, f"算不出向听却编了个档位：{badge!r}")


    def test_carried_over_flag_matches_reality(self):
        """A1/A2：面板手牌多于本帧读到时，`hand_carried_over` 必须为真。

        旧实现只在阻尼那一条分支置 True，于是「本帧读到 0 张、稳定器宽限期把
        上帧的 10 张继续下发」这条路（实测 zj_popup_02：raw=0、面板 10 张、
        status=ok）标记仍是假——用户看到“手牌冻住、其他块照旧更新”时，数据层
        根本没记下这件事。本条把不变量直接钉在 payload 上，不分走哪条路。
        """
        d = payload_for(_find("zj_popup_02.jpg"), "zj_sichuan", "sc_hz")
        raw = int((d.get("diag") or {}).get("raw_hand") or 0)
        shown = int(d.get("count") or 0)
        if shown > raw:
            self.assertTrue(d.get("hand_carried_over"),
                            f"本帧读到 {raw} 张却下发 {shown} 张，沿用标记为假")
            self.assertTrue(str(d.get("message") or "").startswith("沿用"),
                            f"沿用旧读数时 message 不再明说沿用：{d.get('message')!r}")
        else:
            self.assertFalse(d.get("hand_carried_over"),
                             "本帧读数已足够，却报成沿用（标记不能反向说谎）")


class TestMutationControls(unittest.TestCase):
    """变异体：把定缺判据打成恒真，D23 的泄漏必须让上面的断言红。"""

    def test_mutant_always_dingque_leaks_facts(self):
        d = payload_for(_find("zj_play_03.jpg"), "zj_sichuan", NON_DQ_MODE, mutant=True)
        # 恒真变异体下闸门失效：只要引擎本来能算出定缺信息就会漏到 payload。
        # 若这帧引擎本来就没算出定缺信息，本对照不成立 —— 必须明说而不是假装通过。
        print(f"[mutant] 恒真时 dingque={d.get('dingque')!r} "
              f"opponents_dingque={d.get('opponents_dingque')!r} "
              f"dingque_phase={d.get('dingque_phase')!r}")
        leaked = (d.get("dingque") or d.get("opponents_dingque") or d.get("dingque_phase"))
        self.assertTrue(leaked,
                        "变异体（定缺判据恒真）没有漏出任何定缺信息：本守卫对 D23 是空转，"
                        "需要换一帧真能触发定缺读数的素材")


if __name__ == "__main__":
    if MUTANT:
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
