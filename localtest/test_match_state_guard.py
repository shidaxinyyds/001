# -*- coding: utf-8 -*-
"""局况阶段机守卫：牌局阶段、回合归属与碰/杠/摸/打事件播报的逻辑契约。

背景（用户反馈「还是不太清楚当前牌局在干嘛，换牌、摸牌、杠、碰都不太明白」）：
排查后确认不是识别没数据，而是**数据没有被折算成局况**，且缺一个恒有输出的事实层：

1. `phase_label/tactical_badge/tactical_intent` 是战术建议层，`shanten >= 2` 时
   直接 `return "", "", ""`，而悬浮窗见空串就 `SizedBox.shrink()` —— 开局到中盘
   那一大段时间面板上关于"现在在干什么"一个字都没有。
2. 碰/杠早就被 `detect_player_melds` 识别出来了，但只进了牌池计数账本，从未以
   "对家 碰 5万"的形式露出；本家副露（bottom 分区）更在 `zone_to_seat` 里被丢弃。
3. **真实缺陷**：判"轮到你"用 `count % 3 == 2`（14/11/8/5/2）。杠从手里抽走 4 张，
   4 ≢ 0 (mod 3) ⇒ 杠过一次后立牌基数是 9、摸进是 10，`10 % 3 == 1`，于是
   **那一局余下所有回合都误报"候牌中"**，且永不纠正。本守卫第 4 组用例锁死这条。

运行：
  py -3.10 -X utf8 localtest/test_match_state_guard.py          # 主守卫
  py -3.10 -X utf8 localtest/test_match_state_guard.py --mutate  # 变异对照
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from engine.match_state import (  # noqa: E402
    KINDS, MatchPhaseMachine, PHASE_LABEL, SEAT_NAMES, empty_phase_view,
)

MUTATE = "--mutate" in sys.argv
PY_DIR = os.path.join(REPO, "android", "app", "src", "main", "python")
ENGINE_PY = os.path.join(PY_DIR, "engine", "engine.py")
STATE_PY = os.path.join(PY_DIR, "engine", "match_state.py")
OVERLAY_DART = os.path.join(REPO, "lib", "overlays", "mahjong_overlay.dart")
DEBUG_DART = os.path.join(REPO, "lib", "debug_page.dart")

T0 = 1_700_000_000_000
STEP = 100  # ms/帧，> CONFIRM_MS(60) ⇒ 一个新值喂两帧即提交

# 全局单调时钟：真机上帧只会越来越晚，而多个 play() 拼起来模拟的是同一段连续录像。
# 若每个 play() 各自从 T0 起算，就会造出“时间倒流”，把时间门测成“永不提交”。
# （倒流本身也是真隐患，所以单独有一条 test_wall_clock_rollback_... 钉住它。）
_NOW = [T0]


def _tick(step: int = STEP) -> int:
    _NOW[0] += int(step)
    return _NOW[0]


def idx(mpsz: str) -> int:
    """mpsz → 34 型索引（m 0-8, p 9-17, s 18-26, z 27-33）。"""
    suit = {"m": 0, "p": 9, "s": 18, "z": 27}[mpsz[-1]]
    return suit + int(mpsz[0]) - 1


def base_obs(**kw) -> dict:
    """一帧"正常行牌"观测：牌桌内、无特殊阶段、各家牌河与副露都为空。"""
    obs = {"status": "ok", "count": 13, "shanten": 2, "drawing_tile": None,
           "self_discard": None, "swap_phase": False, "dq_phase": False,
           "pick_phase": False, "seat_river": {}, "seat_river_tiles": {},
           "seat_melds": {}, "dingque_suit": None, "dingque_name": "",
           "dingque_recommend": "", "ting_tiles": []}
    obs.update(kw)
    return obs


def play(m: MatchPhaseMachine, frames, step: int = STEP):
    """按序喂帧（时刻沿全局单调时钟推进），返回每帧的视图。"""
    out = []
    for f in frames:
        obs = dict(f)
        obs.setdefault("now_ms", _tick(step))
        out.append(m.update(obs))
    return out


def kinds(view: dict):
    return [x["kind"] for x in view["feed"]]


def texts(view: dict):
    return [x["text"] for x in view["feed"]]


class TestPhaseAndTurn(unittest.TestCase):
    """阶段与回合归属：恒有输出，且杠之后不再误报候牌。"""

    def setUp(self) -> None:
        self.m = MatchPhaseMachine()

    def test_waiting_frame_still_yields_a_phase(self):
        v = self.m.update({"now_ms": T0, "status": "waiting", "count": 0})
        self.assertEqual("idle", v["phase"])
        self.assertEqual("等待开局", v["label"])
        self.assertTrue(v["hint"], "待机也必须给一句人话，不能空着让面板整块收起")

    def test_hand_14_means_my_turn_and_says_which_tile(self):
        views = play(self.m, [
            base_obs(count=13),
            base_obs(count=14),
            base_obs(count=14, drawing_tile="5m"),
            base_obs(count=14, drawing_tile="5m"),
        ])
        self.assertEqual("wait", views[1]["phase"], "14 只被看到一帧，不该抢跑")
        self.assertEqual("turn", views[3]["phase"])
        self.assertEqual(0, views[3]["turn_seat"])
        self.assertIn("已摸进 5万", views[3]["hint"])
        self.assertIn("draw", kinds(views[3]))

    def test_discard_returns_to_waiting_for_next_turn(self):
        play(self.m, [base_obs(count=13), base_obs(count=14), base_obs(count=14)])
        views = play(self.m, [
            base_obs(count=13, self_discard="3p"),
            base_obs(count=13, self_discard="3p"),
        ], step=STEP)
        self.assertEqual("wait", views[1]["phase"])
        self.assertIn("本家 打出 3筒", texts(views[1]))

    def test_kong_does_not_blind_the_turn_judgement(self):
        """本轮修掉的真缺陷：杠过一次后 `count % 3 == 2` 恒不成立。

        立牌基数 = 13 - 4 = 9；摸进那一张是 10。旧口径 `10 % 3 == 1 != 2` ⇒ 永远
        显示"候牌中"，玩家对着"候牌"两个字干等。新口径按副露扣牌数校正基数后判同余。
        """
        self.assertEqual(1, 10 % 3)      # 旧口径为什么必然漏
        play(self.m, [base_obs(count=13)])
        views = play(self.m, [
            base_obs(count=13, seat_melds={0: [("1s", 4)]}),
            base_obs(count=13, seat_melds={0: [("1s", 4)]}),
            base_obs(count=10),          # 杠后基数 9，本帧起算
            base_obs(count=10),
        ])
        self.assertEqual(4, self.m.own_removed(), "杠必须扣 4 张")
        self.assertEqual("turn", views[3]["phase"],
                         "杠后摸进的第 10 张必须判成「轮到你出牌」")
        self.assertIn("本家 杠 1条", texts(views[1]))

    def test_irregular_count_is_reported_not_guessed(self):
        """张数与副露对不上时，如实报"待对齐"，绝不硬猜一个阶段糊上去。"""
        views = play(self.m, [
            base_obs(count=13),
            base_obs(count=13, seat_melds={0: [("1s", 4)]}),
            base_obs(count=13, seat_melds={0: [("1s", 4)]}),
            base_obs(count=12),   # 基数 9，12 与 9/10 都对不上
            base_obs(count=12),
        ])
        self.assertEqual("irregular", views[4]["phase"])
        self.assertIn("对不上", views[4]["hint"])
        self.assertIsNone(views[4]["turn_seat"])

    def test_wall_clock_rollback_does_not_freeze_the_machine(self):
        """墙钟回拨（用户改系统时间/NTP 校时）不能把局况永久冻住。

        确认门看的是 `now - since >= min_ms`。时间倒流后这个差恒为负 ⇒ 所有门再也不
        提交，表现是“牌局在走、面板一动不动”而且不报错——商业上这是“突然变傻”，
        比报错难查得多。计时点必须跟着重定基。
        """
        play(self.m, [base_obs(count=13)])
        t_hi = _tick(5_000)
        v_a = self.m.update({**base_obs(count=14), "now_ms": t_hi})
        v_b = self.m.update({**base_obs(count=14), "now_ms": t_hi - 5_000})
        v_c = self.m.update({**base_obs(count=14), "now_ms": t_hi - 4_900})
        self.assertEqual("wait", v_a["phase"], "14 只被看到一帧，不该抢跑")
        self.assertEqual("wait", v_b["phase"])
        self.assertEqual("turn", v_c["phase"],
                         "时钟回拨后确认门必须继续工作，而不是永久冻结")
        self.assertGreaterEqual(v_c["evidence"]["rejected"]["clock_back"], 1,
                                "回拨重定基要留痕，否则事后无法解释时间轴")

    def test_stale_count_after_kong_is_called_out_once_the_settle_window_closes(self):
        """杠后立牌迟迟不减：结算窗口内按旧基数解释，窗口外只能说“待对齐”。

        副露区与手牌行是同一帧采的，但两边的确认门各自提交，中间必然有一两帧
        “碰已计、立牌还没减”。那个间隙说“等你把多出的牌打出去”是对的；间隙过了还
        多着 4 张，就只能说读数与副露对不上——同余式会把这种情况当成“轮到你”。
        """
        play(self.m, [base_obs(count=13)])
        play(self.m, [base_obs(count=13, seat_melds={0: [("1s", 4)]})] * 2)
        views = play(self.m, [base_obs(count=13)] * 2, step=2000)
        self.assertEqual("irregular", views[-1]["phase"])
        self.assertIn("对不上", views[-1]["hint"])

        m2 = MatchPhaseMachine()
        play(m2, [base_obs(count=13)])
        play(m2, [base_obs(count=13, seat_melds={0: [("1s", 4)]})] * 2)
        views = play(m2, [base_obs(count=13), base_obs(count=13)], step=100)
        self.assertEqual("turn", views[-1]["phase"],
                         "副露刚落定的那一小段不能报成异常，否则每次碰杠都闪一下“待对齐”")
        self.assertIn("副露刚落定", views[-1]["turn_basis"])


class TestEventTruth(unittest.TestCase):
    """事件播报的诚实性：只播被跨帧确认的事实，脏读不播，说不清就明说。"""

    def setUp(self) -> None:
        self.m = MatchPhaseMachine()

    def test_single_frame_jitter_never_becomes_a_broadcast(self):
        play(self.m, [base_obs(count=13)])
        views = play(self.m, [
            base_obs(count=14),                       # 只出现一帧的抖动
            base_obs(count=13),
            base_obs(count=13, seat_river={1: 1},
                     seat_river_tiles={1: [idx("3s")]}),
            base_obs(count=13, seat_river={1: 1},
                     seat_river_tiles={1: [idx("3s")]}),
        ])
        self.assertIn("discard_other", kinds(views[3]))
        self.assertNotIn("draw", kinds(views[3]),
                         "单帧 14 就播「摸到牌」，牌河动画一闪就是满屏假事件")

    def test_opponent_discard_names_the_tile_only_when_unambiguous(self):
        """报不报得出牌面，只看“张数增量与牌面新增是否吻合”。

        牌面读数列表**不是行牌顺序**（`detect_river_discards` 的 NMS 按置信度降序
        返列），所以不能拿“最后一张”当最新弃牌——那会凭空造出一句看似精确的假播报。
        """
        play(self.m, [base_obs(count=13)])
        # 张数 +1 但牌面同时多出两型 ⇒ 只能确认"打了牌"，不能确认打了哪张
        views = play(self.m, [
            base_obs(count=13, seat_river={1: 1},
                     seat_river_tiles={1: [idx("3s"), idx("7p")]}),
            base_obs(count=13, seat_river={1: 1},
                     seat_river_tiles={1: [idx("3s"), idx("7p")]}),
        ])
        self.assertIn("下家 打出一张牌（牌面待确认）", texts(views[1]))
        # 张数 +1 而牌面没多出新牌型（牌河区漏读）：仍然只播“打了牌”
        views = play(self.m, [
            base_obs(count=13, seat_river={1: 2},
                     seat_river_tiles={1: [idx("3s"), idx("7p")]}),
            base_obs(count=13, seat_river={1: 2},
                     seat_river_tiles={1: [idx("3s"), idx("7p")]}),
        ])
        self.assertIn("下家 打出一张牌（牌面待确认）", texts(views[1]))
        # 牌面确实多出一个新牌型、且与张数增量吻合 ⇒ 才敢报具体牌
        views = play(self.m, [
            base_obs(count=13, seat_river={1: 3},
                     seat_river_tiles={1: [idx("3s"), idx("7p"), idx("9m")]}),
            base_obs(count=13, seat_river={1: 3},
                     seat_river_tiles={1: [idx("3s"), idx("7p"), idx("9m")]}),
        ])
        self.assertIn("下家 打出 9万", texts(views[1]))

    def test_river_shrink_is_rejected_and_counted(self):
        """牌河不会变短：变小必是误读，只忽略并留下计数，绝不反向播事件。"""
        play(self.m, [base_obs(count=13)])
        play(self.m, [
            base_obs(count=13, seat_river={2: 3}, seat_river_tiles={2: [idx("1m")] * 3}),
            base_obs(count=13, seat_river={2: 3}, seat_river_tiles={2: [idx("1m")] * 3}),
        ])
        before = list(self.m._feed)
        views = play(self.m, [
            base_obs(count=13, seat_river={2: 1}),
            base_obs(count=13, seat_river={2: 1}),
        ])
        self.assertEqual([x["kind"] for x in before], kinds(views[1]))
        self.assertGreaterEqual(views[1]["evidence"]["rejected"]["non_monotonic"], 1,
                                "脏读被挡掉必须留下证据，否则端上只会表现为"
                                "「神秘地没播报」而无从排查")

    def test_river_over_cap_is_refused(self):
        play(self.m, [base_obs(count=13)])
        views = play(self.m, [
            base_obs(count=13, seat_river={1: self.m.RIVER_CAP + 5}),
            base_obs(count=13, seat_river={1: self.m.RIVER_CAP + 5}),
        ])
        self.assertNotIn("discard_other", kinds(views[1]))
        self.assertGreaterEqual(views[1]["evidence"]["rejected"]["river_over_cap"], 1)

    def test_pong_then_kong_is_one_added_kong_not_two_kongs(self):
        play(self.m, [base_obs(count=13)])
        play(self.m, [
            base_obs(count=13, seat_melds={2: [("5m", 3)]}),
            base_obs(count=13, seat_melds={2: [("5m", 3)]}),
        ])
        views = play(self.m, [
            base_obs(count=13, seat_melds={2: [("5m", 4)]}),
            base_obs(count=13, seat_melds={2: [("5m", 4)]}),
        ])
        self.assertIn("对家 加杠 5万", texts(views[1]))
        self.assertEqual(["pong", "added_kong"],
                         [x["kind"] for x in views[1]["feed"] if
                          x["kind"] in ("pong", "kong", "added_kong")])

    def test_meld_that_vanishes_then_returns_does_not_rebroadcast(self):
        """副露只增不减：漏读一帧再读回来，不能当成"又碰了一次"。"""
        play(self.m, [base_obs(count=13)])
        play(self.m, [
            base_obs(count=13, seat_melds={1: [("3p", 3)]}),
            base_obs(count=13, seat_melds={1: [("3p", 3)]}),
        ])
        views = play(self.m, [
            base_obs(count=13, seat_melds={}),
            base_obs(count=13, seat_melds={1: [("3p", 3)]}),
            base_obs(count=13, seat_melds={1: [("3p", 3)]}),
        ])
        self.assertEqual(1, sum(1 for x in views[2]["feed"] if x["kind"] == "pong"))
        self.assertEqual(1, len(views[2]["melds"]))

    def test_meld_group_cap_blocks_fifth_group(self):
        play(self.m, [base_obs(count=13)])
        tiles = [("1m", 3), ("2m", 3), ("3m", 3), ("4m", 3)]
        play(self.m, [base_obs(count=13, seat_melds={1: tiles})] * 2)
        views = play(self.m, [
            base_obs(count=13, seat_melds={1: tiles + [("9s", 3)]}),
            base_obs(count=13, seat_melds={1: tiles + [("9s", 3)]}),
        ])
        self.assertEqual(4, len(views[1]["melds"]))
        self.assertGreaterEqual(views[1]["evidence"]["rejected"]["meld_group_cap"], 1)

    def test_two_tiles_per_group_only(self):
        """接线给的不是 3 就是 4 张；出现 2 张说明上层拼错了，宁可不播。"""
        play(self.m, [base_obs(count=13)])
        views = play(self.m, [
            base_obs(count=13, seat_melds={1: [("1m", 2)]}),
            base_obs(count=13, seat_melds={1: [("1m", 2)]}),
        ])
        self.assertEqual([], views[1]["melds"])


class TestSpecialPhasesAndBoundaries(unittest.TestCase):
    """换牌/定缺/选牌、局末与跨局边界。"""

    def setUp(self) -> None:
        self.m = MatchPhaseMachine()

    def test_swap_phase_wins_and_its_river_noise_stays_silent(self):
        views = play(self.m, [
            base_obs(swap_phase=True, count=13),
            base_obs(swap_phase=True, count=13),
            base_obs(swap_phase=True, count=13, seat_river={1: 4},
                     seat_river_tiles={1: [idx("1m")] * 4}),
            base_obs(swap_phase=True, count=13, seat_river={1: 4},
                     seat_river_tiles={1: [idx("1m")] * 4}),
        ])
        self.assertEqual("swap", views[1]["phase"])
        self.assertIn("swap_in", kinds(views[1]))
        self.assertNotIn("discard_other", kinds(views[3]),
                        "换牌阶段的牌河残留读数必须只当基准，不能当事件播出去")
        views = play(self.m, [base_obs(count=13), base_obs(count=13)])
        self.assertIn("swap_done", kinds(views[1]))
        self.assertEqual(0, self.m._river[1].committed,
                         "被吸收成基准时不能留下「下家已有 4 张牌河」的假账")

    def test_dingque_and_tenpai_edges_are_broadcast(self):
        play(self.m, [base_obs(count=13)])
        views = play(self.m, [
            base_obs(dingque_suit=2, dingque_name="条"),
            base_obs(dingque_suit=2, dingque_name="条"),
            base_obs(count=13, shanten=0, ting_tiles=["3s", "7p"]),
            base_obs(count=13, shanten=0, ting_tiles=["3s", "7p"]),
        ])
        self.assertIn("定缺敲定：条", texts(views[1]))
        self.assertIn("本家听牌：3条/7筒", texts(views[3]))

    def test_entering_dingque_does_not_claim_it_is_decided(self):
        """进定缺那一屏只报“进入”，缺门读到了才报“敲定”。

        这两个时刻共用一个 kind 时，引擎会把“还没做的决定”播成“已经做完”——
        玩家手上还开着选门弹窗，屏幕上写着“定缺已敲定”，这一句就够让人不再
        相信面板上任何其他读数了。与 swap_in/swap_done 对称，分成两个 kind。
        """
        play(self.m, [base_obs(count=13)])
        views = play(self.m, [
            base_obs(dq_phase=True, count=13),
            base_obs(dq_phase=True, count=13),
        ])
        self.assertIn("dingque_in", kinds(views[1]))
        self.assertIn("进入定缺选门", texts(views[1]))
        self.assertEqual("dingque", views[1]["phase"])
        self.assertFalse([t for t in texts(views[1]) if "敲定" in t],
                         "刚进定缺屏就说“敲定”，是在替玩家编一个没发生的结果")
        views = play(self.m, [
            base_obs(dq_phase=True, count=13, dingque_suit=2, dingque_name="条"),
            base_obs(dq_phase=True, count=13, dingque_suit=2, dingque_name="条"),
        ])
        self.assertIn("定缺敲定：条", texts(views[1]),
                      "缺门真读到了才该报敲定，这一句不能被我拆没了")

    def test_match_end_needs_persistence_and_fires_once(self):
        play(self.m, [base_obs(count=13), base_obs(count=14), base_obs(count=14)])
        views = play(self.m, [
            {"status": "waiting", "count": 0},
        ], step=0)
        self.assertNotIn("over", kinds(views[0]), "一闪而过的空帧不该播结束")
        m2 = MatchPhaseMachine()
        play(m2, [base_obs(count=13)])
        views = play(m2, [
            {"status": "waiting", "count": 0},
            {"status": "waiting", "count": 0},
            {"status": "waiting", "count": 0},
            {"status": "waiting", "count": 0},
        ], step=300)
        self.assertIn("over", kinds(views[2]), "离开牌桌持续够久才播“本局结束”")
        self.assertEqual(kinds(views[2]), kinds(views[3]),
                         "「本局结束」只能播一次，重复播会刷满实录")

    def test_baselines_do_not_leak_across_matches(self):
        """上一局的牌河/副露/张数不得跨局生效：否则新局第一帧就报凭空事件。"""
        m = MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        play(m, [base_obs(count=13, seat_melds={1: [("5p", 3)]})] * 2)
        play(m, [base_obs(count=13, seat_river={2: 4},
                          seat_river_tiles={2: [idx("1m")] * 4})] * 2)
        play(m, [{"status": "waiting", "count": 0}] * 4, step=300)
        self.assertIn("over", kinds(m.update({**base_obs(count=0), "now_ms": _tick(300)})))
        v = m.update({**base_obs(count=13), "now_ms": _tick(300)})
        self.assertEqual([], v["melds"], "副露一览带着上一局的碰")
        self.assertEqual(0, sum(v["evidence"]["river"].values()),
                         "牌河基准跨局残留会让新局第一帧就报假事件")
        self.assertIn("start", kinds(v))

    def test_feed_and_meld_caps(self):
        m = MatchPhaseMachine()
        frames = [base_obs(count=13)]
        for i in range(1, 30):
            frames.append(base_obs(count=13, seat_river={1: i},
                                   seat_river_tiles={1: [idx("1m")] * i}))
            frames.append(base_obs(count=13, seat_river={1: i},
                                   seat_river_tiles={1: [idx("1m")] * i}))
        views = play(m, frames)
        self.assertLessEqual(len(views[-1]["feed"]), m._feed_max)
        self.assertLessEqual(len(views[-1]["melds"]), m.meld_cap)


class TestViewSchema(unittest.TestCase):
    """视图 schema：所有出口逐键一致，端上才不会「某条路径整块不显示」。"""

    def test_every_path_exposes_the_same_keys(self):
        need = {"phase", "label", "hint", "turn_seat", "turn_basis", "melds", "feed",
                "hand", "evidence", "updated_at_ms", "seq"}
        m = MatchPhaseMachine()
        views = [m.update({"now_ms": T0, "status": "waiting", "count": 0}),
                 m.update({**base_obs(count=13), "now_ms": T0 + 100})]
        for v in views:
            self.assertEqual(need, set(v))
        self.assertEqual(need, set(empty_phase_view("x")))
        # 空视图与真视图必须同 phase 语义（idle）
        self.assertEqual("idle", empty_phase_view("x")["phase"])

    def test_all_phase_keys_have_labels(self):
        self.assertEqual(set(PHASE_LABEL), {"idle", "swap", "dingque", "pick",
                                            "turn", "wait", "irregular"})
        self.assertTrue(all(PHASE_LABEL[k] for k in PHASE_LABEL))

    def test_feed_items_carry_time_and_seat_for_the_panel(self):
        m = MatchPhaseMachine()
        views = play(m, [base_obs(count=13), base_obs(count=14),
                         base_obs(count=14, drawing_tile="9m")])
        item = [x for x in views[2]["feed"] if x["kind"] == "draw"][0]
        self.assertIsInstance(item["at_ms"], int)
        self.assertIn("seat", item)
        self.assertTrue(item["text"])
        self.assertEqual(0, item["seat"])
        for x in views[2]["feed"]:
            self.assertIn(x["kind"], KINDS, "事件类型必须两端都认识，否则面板只会画默认色")

    def test_unknown_seat_strings_are_dropped_not_misattributed(self):
        """接线给了不认识的位置（比如拼错的 'front'）不能随便安到某家头上。"""
        m = MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        views = play(m, [base_obs(count=13, seat_melds={"7": [("1m", 3)]}),
                         base_obs(count=13, seat_melds={"7": [("1m", 3)]})])
        self.assertEqual([], views[1]["melds"])


ENGINE_SRC = open(ENGINE_PY, encoding="utf-8").read()
STATE_SRC = open(STATE_PY, encoding="utf-8").read()
OVERLAY_SRC = open(OVERLAY_DART, encoding="utf-8").read()
DEBUG_SRC = open(DEBUG_DART, encoding="utf-8").read()


def dart_code(src: str) -> str:
    """剔除整行注释后的 Dart 源码。

    契约查的是真代码，不是解释性注释 —— 悬浮窗里专门写了一段“为什么不再用
    `count % 3`”，拿原文子串去比会把这段说明当成案发现场。行尾注释不剔：
    那些写法本就该带在下面几行的代码里。"""
    kept = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        kept.append(line)
    return "\n".join(kept)


DART_CODE = dart_code(OVERLAY_SRC)


def hits_in(code: str, needle: str):
    """返回包含 needle 的行。失败时只打这几行，不 dump 整个 4500 行文件。"""
    return [ln.strip() for ln in code.splitlines() if needle in ln]


def dart_method(name: str, code: str = None) -> str:
    """取出某个 Dart 方法的源码块（从签名行到两空格缩进的收尾 `}`）。

    契约得能只对某个方法提问：“局况层不得灌进战术条”如果拿整文件去比，
    `_pulseKeyEvent` 里那些正当的 `_phaseOf` 调用会让断言永远调不成；
    反过来一旦有人把兜底加回去，整文件子串检查又根本看不出加在了哪一行。
    变异对照需要拿“改过的源码”取块，所以 code 可以传。
    """
    lines = (DART_CODE if code is None else code).splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.search(r"\b" + re.escape(name) + r"\s*\(", ln) and ln.rstrip().endswith("{"):
            start = i
            break
    if start is None:
        raise AssertionError(f"找不到方法 {name}（签名行变了，契约得跟着改）")
    for j in range(start, len(lines)):
        if lines[j] == "  }":
            return "\n".join(lines[start:j + 1])
    raise AssertionError(f"方法 {name} 没按两空格缩进收尾，取块失败")


class TestWiring(unittest.TestCase):
    """接线契约：状态机必须在真链路上被喂、被 reset、被发端，且不能污染牌河账本。"""

    def test_engine_builds_feeds_and_resets_the_machine(self):
        self.assertIn("from .match_state import MatchPhaseMachine", ENGINE_SRC)
        self.assertIn("self._phase_machine = MatchPhaseMachine(", ENGINE_SRC)
        self.assertIn("self._phase_machine.reset()", ENGINE_SRC)
        self.assertIn("self._phase_machine.update({", ENGINE_SRC)
        self.assertIn('"match_phase": match_phase', ENGINE_SRC)

    def test_every_exit_carries_match_phase(self):
        """正常帧、待机帧、错误帧三条出口都必须带 match_phase。

        少一条的后果不是「少一个字段」而是「那条路径上面板什么都不显示」——
        而错误/待机恰好是用户最容易困惑的时候。
        """
        self.assertEqual(2, ENGINE_SRC.count('"match_phase": match_phase'),
                         "正常帧与待机帧出口都要带 match_phase")
        self.assertIn('"match_phase": empty_phase_view(', ENGINE_SRC)

    def test_own_melds_reach_the_machine(self):
        """本家副露（bottom 分区）必须进座位表：否则「你碰了/你杠了」永远播不出，
        而且杠之后的立牌基数算不出来（就是上面那条 mod 3 盲区的根因之一）。

        断言取整行映射而不是 `"bottom": 0`：后者在牌河区计数里也出现几次，挨了变异
        也能靠残留子串混过断言（上一版就在这里漏拦一条）。"""
        self.assertIn('"right": 1, "top": 2, "left": 3, "bottom": 0', ENGINE_SRC)
        self.assertIn("seat_entries[seat].append((md, int(n_tiles)))", ENGINE_SRC)
        self.assertIn('"seat_melds": getattr(self, "_seat_meld_entries", {})',
                      ENGINE_SRC)

    def test_is_drawing_uses_meld_corrected_parity(self):
        self.assertIn("own_removed = self._phase_machine.own_removed()", ENGINE_SRC)
        self.assertIn(
            "if curr_n > 0 and (curr_n - (self.base_hand_tiles - own_removed)) % 3 == 1:",
            ENGINE_SRC)
        self.assertLess(ENGINE_SRC.index("own_removed = self._phase_machine"),
                        ENGINE_SRC.index('"is_drawing": is_drawing'),
                        "is_drawing 必须在发端之前就被校正过")

    def test_own_river_is_not_fed_twice(self):
        """本家出牌已经由手牌张数转移给出，不能再喂 bottom 牌河增量，否则同一件事播两遍。"""
        i = ENGINE_SRC.index('"seat_river": _seat_river')
        j = ENGINE_SRC.index("_seat_river = {")
        block = ENGINE_SRC[j:i]
        self.assertNotIn("bottom", block.split("}")[0],
                         "seat_river 里出现 bottom 就会把本家弃牌双计")

    def test_phase_failure_degrades_visibly(self):
        """局况折算异常不能拖垮识别，但必须留下可见证据（diag.phase_error）。"""
        self.assertIn('"phase_error": getattr(self, "_phase_error", None)', ENGINE_SRC)
        self.assertIn('match_phase = empty_phase_view("局况折算异常，不影响识别与建议")',
                      ENGINE_SRC)


class TestOverlayDartContract(unittest.TestCase):
    """端上契约：悬浮窗是叠在真实牌局上的覆盖层 —— 局况只能「换文案」，不能长出新控件。

    这几条看着像风格检查，拦的却是三类会真疼的事故：
    ① 拿恒有输出的局况层去填战术层的空白（那一行会从“没建议时收起”变成对局
      全程常驻 —— 悬浮窗叠在真实牌局上，等于给它多加了一块面板）；
    ② 端上自己再算一次 `count % 3`（杠后把「轮到你」读成「候牌中」，而且永不纠正）；
    ③ 瞬时胶囊被塞进 Column（那就成了常驻，还会把建议区顶下去）；
    ④ 胶囊不做时间水位去重（payload 是缓存的，同一句「对家 杠 5万」会闪三遍）。
    """

    def test_panel_does_not_mirror_the_mod3_rule(self):
        self.assertEqual([], hits_in(DART_CODE, "count % 3 == 2"),
                         "端上不得再镜像同余判据：杠抽走 4 张，基数只能由引擎给"
                         "（match_phase.hand.base）")

    def test_match_phase_never_fills_the_tactical_row(self):
        """战术三段只读引擎战术层：它为空就为空，那一行整块收起。

        局况的出口只有两个，都不占布局：瞬时胶囊（~2.5s）与主页调试页。「拿
        match_phase 兜底」听着能回答“现在在干嘛”，代价是对局中每一帧都有话说，
        那一行于是再也不会收起 —— 看着就是新增常驻。
        """
        for fn in ("_resolvePhaseLabel", "_resolveTacticalBadge",
                   "_resolveTacticalIntent"):
            self.assertEqual([], hits_in(dart_method(fn), "_phaseOf("),
                             f"{fn} 把恒有输出的局况层灌进了战术条")
        self.assertEqual([], hits_in(DART_CODE, "_kPhaseBadge"),
                         "兜底徽章表还在，说明那条补位路径没拆干净")

    def test_engine_tactical_text_is_taken_verbatim(self):
        """引擎战术层有词时一律照抄，不得让本地粗推抢在前面。

        下面那段 `is_drawing + shanten` 的拼句只是“payload 压根没带这个键”时的
        垫背（错误帧、隔帧重发的旧缓存）。一旦它能盖过引擎文案，两端各自一套
        说法就会开始漂移 —— 同余式误报就是这么长回来的。
        """
        for fn, ret in (("_resolvePhaseLabel", "if (p != null) return p;"),
                        ("_resolveTacticalBadge", "if (b != null) return b;"),
                        ("_resolveTacticalIntent", "if (t != null) return t;")):
            self.assertIn(ret, dart_method(fn),
                          f"{fn} 不再照抄引擎战术层，本地粗推爬到前面了")

    def test_flash_kinds_are_a_subset_of_engine_kinds(self):
        m = re.search(r"_kFlashKinds = \{(.*?)\};", OVERLAY_SRC, re.S)
        self.assertIsNotNone(m, "悬浮窗的关键事件清单必须还能解析")
        listed = re.findall(r"'([a-z_]+)'", m.group(1))
        self.assertTrue(listed, "清单为空等于什么都不播")
        self.assertEqual([], [k for k in listed if k not in KINDS],
                         "端上闪了引擎根本不会播的事件类型")
        # 每一巡都在发生的事不能闪：那一屏就是滚动字幕，瞬时元素也就成了常驻。
        self.assertEqual([], [k for k in ("draw", "discard", "discard_other")
                              if k in listed],
                         "摸牌/弃牌逐巡播闪，屏幕上会一直有东西在顶新")
        # 阶段切换得在清单里：玩家问的“现在到哪一步了”恰好就是这几个时刻。
        for k in ("start", "swap_in", "swap_done", "dingque_in", "over"):
            self.assertIn(k, listed, f"阶段切换事件 {k} 不得从播报清单里掉出去")

    def test_capsule_is_transient_and_does_not_occupy_layout(self):
        i = OVERLAY_SRC.index("Widget _eventFlashCapsule()")
        body = OVERLAY_SRC[i:OVERLAY_SRC.index("// 授权到期", i)]
        self.assertIn("return Positioned(", body, "不占布局：只能挂 Positioned")
        self.assertIn("IgnorePointer(", body, "不吃手势：否则挡住唯一的拖动区")
        self.assertIn("overflow: TextOverflow.ellipsis", body, "超宽必须省略，不许撑破面板")
        self.assertIn("if (_eventFlash != null) _eventFlashCapsule(),", OVERLAY_SRC,
                      "挂载点要在根 Stack 的子元素表里，而不是 Column 内")
        self.assertIn("if (at <= _lastFlashAtMs) return;", OVERLAY_SRC,
                      "at_ms 水位去重：缓存 payload 重发不得重复闪")
        self.assertIn("_eventFlashTimer?.cancel();", OVERLAY_SRC, "dispose 必须收掉定时器")

    def test_detail_lives_on_the_debug_page(self):
        """完整明细（牌河张数、副露一览、被挡下的脏读计数）只能在主页调试页常驻。"""
        self.assertIn("title: '当前牌局'", DEBUG_SRC)
        self.assertIn("_matchPhaseReadout()", DEBUG_SRC)
        self.assertIn("(ev['match_phase'] as Map?)", DEBUG_SRC)
        # 上报必须搭 2s 那班车：逐帧发会让主页每帧重建（识别高峰期点什么都没反应的病根）
        self.assertIn("share['match_phase'] = _phaseDigestForShare();", OVERLAY_SRC)


# -------------------------------------------------------------------- 变异对照

class TestMutationControls(unittest.TestCase):
    """--mutate：把坏写法塞回源码，断言本守卫确实会红（防止守卫只是空转）。

    判据是**每一条**变异都要被拦住，不是“至少一条”：只要有一条悄悄活下去，就说明
    对应那条纪律其实并没有人在守。每条变异都是「真实世界里会自然发生」的改法（为了
    少等 60ms 把确认门去掉、为了“看起来精确”把同余式当真理），不是为反而反。
    """

    def setUp(self) -> None:
        if not MUTATE:
            self.skipTest("加 --mutate 才跑变异对照")

    # ------------------------------------------------ 行为探针（跑在变异后的模块上）
    # 返回 True = 这个变异版本开始说谎/失控，即本守卫能拦住它。

    @staticmethod
    def _lie_irregular_masked(mod) -> bool:
        """纯同余判据会把“比基数多 4 张”的脏读糊成“轮到你”。"""
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        play(m, [base_obs(count=13, seat_melds={0: [("1s", 4)]})] * 2)
        # 杠后基数只有 9；结算窗口（1.2s）过去之后手里还摆着 13 张：多出来的 4 张
        # 不可能是摸进的牌。诚实实现只能说“待对齐”，同余式会说“轮到你出牌”。
        views = play(m, [base_obs(count=13)] * 2, step=2000)
        return views[-1]["phase"] != "irregular"

    @staticmethod
    def _lie_meld_leak(mod) -> bool:
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        play(m, [base_obs(count=13, seat_melds={1: [("5p", 3)]})] * 2)
        play(m, [{"status": "waiting", "count": 0}] * 4, step=300)
        views = play(m, [base_obs(count=13), base_obs(count=13)])
        return bool(views[-1]["melds"])          # 新一局不该带着上一局的碰

    @staticmethod
    def _lie_river_shrink(mod) -> bool:
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        play(m, [base_obs(count=13, seat_river={1: 1},
                          seat_river_tiles={1: [idx("3s")]})] * 2)
        play(m, [base_obs(count=13, seat_river={1: 0})] * 2)      # 脏读：牌河“没了”
        views = play(m, [base_obs(count=13, seat_river={1: 1},
                          seat_river_tiles={1: [idx("3s")]})] * 2)
        n = sum(1 for x in views[-1]["feed"] if x["kind"] == "discard_other")
        return n > 1     # 诚实实现只认第一次；缩水后回升不该再播一张弃牌

    @staticmethod
    def _lie_special_noise(mod) -> bool:
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        play(m, [base_obs(swap_phase=True, count=13, seat_river={1: 4},
                          seat_river_tiles={1: [idx("1m")] * 4})] * 2)
        views = play(m, [base_obs(count=13, seat_river={1: 4},
                          seat_river_tiles={1: [idx("1m")] * 4})] * 2)
        return any(x["kind"] == "discard_other" for x in views[-1]["feed"])

    @staticmethod
    def _lie_single_frame_jitter(mod) -> bool:
        """确认门只看“是不是又看到一次”而不看时长时，活跃节奏(20ms)下的抖动就能上屏。"""
        m = mod.MatchPhaseMachine()
        views = play(m, [base_obs(count=13), base_obs(count=14),
                         base_obs(count=14), base_obs(count=13)], step=20)
        return any(x["kind"] == "draw" for x in views[-1]["feed"])

    @staticmethod
    def _lie_feed_unbounded(mod) -> bool:
        m = mod.MatchPhaseMachine()
        frames = [base_obs(count=13)]
        for i in range(1, 30):
            frames += [base_obs(count=13, seat_river={1: i},
                               seat_river_tiles={1: [idx("1m")] * i})] * 2
        views = play(m, frames)
        return len(views[-1]["feed"]) > m._feed_max

    @staticmethod
    def _lie_meld_group_cap(mod) -> bool:
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        tiles = [(f"{i}m", 3) for i in range(1, 7)]      # 6 组，物理上不可能
        views = play(m, [base_obs(count=13, seat_melds={1: tiles})] * 2)
        return len(views[-1]["melds"]) > m.MELD_GROUP_CAP

    @staticmethod
    def _lie_instant_over(mod) -> bool:
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        v = m.update({"status": "waiting", "count": 0, "now_ms": _tick(20)})
        return "over" in kinds(v)      # 一闪而过的空帧就报“本局结束”

    @staticmethod
    def _lie_dingque_entry_claimed(mod) -> bool:
        """进入定缺与缺门敲定共用一个 kind 时，前者会说成“定缺已敲定”。"""
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        views = play(m, [base_obs(dq_phase=True, count=13)] * 2)
        return any("敲定" in t for t in texts(views[1]))

    @staticmethod
    def _lie_dingque_result_never_broadcast(mod) -> bool:
        """特殊阶段分支提前 return，不补调缺门折算时，结果永远播不出来。"""
        m = mod.MatchPhaseMachine()
        play(m, [base_obs(count=13)])
        views = play(m, [base_obs(dq_phase=True, count=13,
                                  dingque_suit=2, dingque_name="条")] * 2)
        return not any("定缺敲定" in t for t in texts(views[1]))

    # ------------------------------------------------ 接线探针（源码级契约）

    STATE_MUTANTS = [
        ("立牌判定退回纯同余式（把多 3 张的脏读糊成候牌）",
         '        if count == base + 1:\n            return True,',
         '        if (count - base) % 3 == 1:\n            return True,',
         _lie_irregular_masked.__func__),
        ("局末只清牌河不清副露（上一局的碰带进新一局）",
         "self._melds[s] = {}", "pass", _lie_meld_leak.__func__),
        ("牌河读数变小也认（误读后回升会多播一张弃牌）",
         '            if n < prev_n:\n                # 牌河不会变短：脏读，丢候选保基准\n'
         '                self._rejected["non_monotonic"] += 1\n'
         '                gate.drop()\n                continue',
         "            if n < prev_n:\n                gate.force(n)",
         _lie_river_shrink.__func__),
        ("特殊阶段不吸收读数（换牌期满屏假弃牌）",
         "self._absorb(obs, absorb_dingque=False)", "pass",
         _lie_special_noise.__func__),
        ("确认门改成「看到一次就提交」（抖动直接上屏）",
         "if now_ms - self.since_ms >= min_ms:", "if True:",
         _lie_single_frame_jitter.__func__),
        ("实录不再限长（payload 与面板被历史淹没）",
         "if len(self._feed) > self._feed_max:", "if False:",
         _lie_feed_unbounded.__func__),
        ("副露组数不设上界（脏读把 6 组塞进一览）",
         "if prev == 0 and len(self._melds[seat]) >= self.MELD_GROUP_CAP:",
         "if False:", _lie_meld_group_cap.__func__),
        ("离开牌桌立刻播结束（空帧一闪就报本局结束）",
         "and now - self._idle_since >= self.OVER_MS):", "and True):",
         _lie_instant_over.__func__),
        ("进入定缺被播成“定缺已敲定”（替玩家编一个还没做的决定）",
         'self._push("dingque_in", 0, None, now)',
         'self._push("dingque", 0, None, now)',
         _lie_dingque_entry_claimed.__func__),
        ("定缺屏上不再折算缺门（“定缺敲定：条”永远轮不到）",
         "            self._update_dingque(obs, now, confirm)\n            self._phase = special",
         "            self._phase = special",
         _lie_dingque_result_never_broadcast.__func__),
        ("定缺屏把缺门当残留噪声吸收（事件被基准吞掉，就是最初那个缺陷）",
         "self._absorb(obs, absorb_dingque=False)", "self._absorb(obs)",
         _lie_dingque_result_never_broadcast.__func__),
    ]

    ENGINE_MUTANTS = [
        ("退回旧的 count%3 判回合（杠后永久误报候牌）",
         "if curr_n > 0 and (curr_n - (self.base_hand_tiles - own_removed)) % 3 == 1:",
         "if curr_n in (14, 11, 8, 5, 2):",
         lambda s: "(curr_n - (self.base_hand_tiles - own_removed)) % 3 == 1" in s),
        ("把本家副露从座位表里去掉（播不出「你碰了」）",
         '"right": 1, "top": 2, "left": 3, "bottom": 0',
         '"right": 1, "top": 2, "left": 3',
         lambda s: '"right": 1, "top": 2, "left": 3, "bottom": 0' in s),
        ("待机出口不再带 match_phase（面板整块消失）",
         '"match_phase": match_phase,\n                "native_ready"',
         '"native_ready"',
         lambda s: s.count('"match_phase": match_phase') >= 2),
        ("错误帧出口不带局况视图",
         '"match_phase": empty_phase_view("画面识别异常，暂无局况")',
         '"phase_label": "画面识别异常"',
         lambda s: '"match_phase": empty_phase_view(' in s),
        ("新局不清算阶段机（上一局的碰杠牌河带进新一局）",
         "self._phase_machine.reset()", "pass",
         lambda s: "self._phase_machine.reset()" in s),
        ("把本家牌河也喂进去（自家弃牌播两遍）",
         '_seat_river = {1: int(_rz.get("right", 0) or 0),',
         '_seat_river = {0: int(_rz.get("bottom", 0) or 0), 1: int(_rz.get("right", 0) or 0),',
         lambda s: "bottom" not in s[s.index("_seat_river = {"):s.index('"seat_river": _seat_river')]),
    ]

    def test_state_mutants_are_caught(self):
        import types
        missed = []
        for name, old, new, probe in self.STATE_MUTANTS:
            self.assertIn(old, STATE_SRC, f"变异模板过期（state）：{name}")
            mod = types.ModuleType("mutated_match_state")
            mod.__dict__["__name__"] = "mutated_match_state"
            try:
                exec(compile(STATE_SRC.replace(old, new, 1), "<mutant>", "exec"),
                     mod.__dict__)
            except Exception as exc:      # 语法被改坏也算“红”
                print(f"[mutate] {name}：改到无法编译（{exc}）——已拦下")
                continue
            if probe(mod):
                print(f"[mutate] {name}：行为出现说谎——已被本守卫拦下")
            else:
                missed.append(name)
        self.assertEqual([], missed, "这些变异活了下来，说明对应纪律其实没有守卫在守")

    def test_engine_mutants_are_caught(self):
        missed = []
        for name, old, new, still_ok in self.ENGINE_MUTANTS:
            self.assertIn(old, ENGINE_SRC, f"变异模板过期（engine）：{name}")
            src = ENGINE_SRC.replace(old, new, 1)
            self.assertNotEqual(src, ENGINE_SRC, name)
            if still_ok(src):
                missed.append(name)
            else:
                print(f"[mutate] {name}：接线契约被打破——已被拦下")
        self.assertEqual([], missed, "这些接线变异活了下来，源码契约有洞")

    DART_MUTANTS = [
        ("把恒有输出的局况层灌进战术条（那一行从「没建议时收起」变成全程常驻）",
         "    final p = res['phase_label'] as String?;\n    if (p != null) return p;",
         "    final p = res['phase_label'] as String?;\n"
         "    if (p != null && p.trim().isNotEmpty) return p;\n"
         "    final mp = _phaseOf(res);\n"
         "    if (mp != null) {\n"
         "      final l = (mp['label'] as String? ?? '').trim();\n"
         "      if (l.isNotEmpty) return l;\n"
         "    }",
         lambda s: "_phaseOf(" not in
                   dart_method("_resolvePhaseLabel", dart_code(s))),
        ("战术条改拿本地同余式当主判据（开局到中盘都在误报候牌）",
         "    final p = res['phase_label'] as String?;\n    if (p != null) return p;",
         "    final p = res['phase_label'] as String?;\n    if (false) return p;",
         lambda s: "if (p != null) return p;" in
                   dart_method("_resolvePhaseLabel", dart_code(s))),
        ("端上恢复 count%3 同余判据（杠后永久误报候牌）",
         "    if (isDrawing) {\n      if (shanten == 0) return '听牌决胜';",
         "    if (isDrawing || count % 3 == 2) {\n      if (shanten == 0) return '听牌决胜';",
         lambda s: hits_in(dart_code(s), "count % 3 == 2") == []),
        ("事件胶囊塞进 Column（成了常驻，还会顶掉建议区）",
         "          if (_eventFlash != null) _eventFlashCapsule(),",
         "                        _eventFlashCapsule(),",
         lambda s: "if (_eventFlash != null) _eventFlashCapsule()," in s),
        ("胶囊去掉 IgnorePointer（挡住悬浮窗唯一的拖动区）",
         "      child: IgnorePointer(",
         "      child: GestureDetector(\n        onTap: () {},",
         lambda s: "child: IgnorePointer(" in s),
        ("不做 at_ms 水位去重（同一句碰/杠反复顶新）",
         "    if (at <= _lastFlashAtMs) return;",
         "    // 去重拆了：每帧都按 feed 尾重闪一次",
         lambda s: "if (at <= _lastFlashAtMs) return;" in s),
        ("局况明细断供（摘要不再搭 2s 那班车上报，调试页永远空白）",
         "      share['match_phase'] = _phaseDigestForShare();",
         "      // 忘了搭车：调试页那一块从此没有读数",
         lambda s: "share['match_phase'] = _phaseDigestForShare();" in s),
    ]

    def test_dart_mutants_are_caught(self):
        missed = []
        for name, old, new, still_ok in self.DART_MUTANTS:
            self.assertIn(old, OVERLAY_SRC, f"变异模板过期（dart）：{name}")
            src = OVERLAY_SRC.replace(old, new, 1)
            self.assertNotEqual(src, OVERLAY_SRC, name)
            if still_ok(src):
                missed.append(name)
            else:
                print(f"[mutate] {name}：端上契约被打破——已被拦下")
        self.assertEqual([], missed, "这些端上变异活了下来，「不新增常驻」没人守")


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestPhaseAndTurn, TestEventTruth, TestSpecialPhasesAndBoundaries,
                TestViewSchema, TestWiring, TestOverlayDartContract):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
