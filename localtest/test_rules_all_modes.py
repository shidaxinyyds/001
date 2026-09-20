# -*- coding: utf-8 -*-
"""10 种玩法规则完整性 + 互不干扰单测（锁死"张冠李戴"回归）。

覆盖两套生产引擎：
- 川麻家族（sc_hz / sc_xz / sc_xl / gy_zj）→ SichuanAnalyzer（28 型 + 定缺）
- 通用地方玩法（std_tdh / wh_kk / db_qh / hz_bd / gd_hz / cs_zz）→ StdAnalyzer（34 型）

每个玩法都对照其**真实规则**用「已知答案的标准牌例」对拍，并交叉验证：
同一手牌在不同玩法下判定应正确分化（选血战不得套血流红中，反之亦然）。

运行:
  py -3.10 localtest/test_rules_all_modes.py
  或 py -3.10 -m unittest localtest.test_rules_all_modes -v
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from modes import MODES, get_mode, get_analyzer, is_sichuan_family  # noqa: E402
from std import StdAnalyzer  # noqa: E402
from sichuan.sichuan_analyzer import SichuanAnalyzer as SA  # noqa: E402

ALL34 = list(range(34))


def c34(s: str):
    """'123m456p78z' -> 34 型计数（数字累积到花色字母）。"""
    c = [0] * 34
    nums = []
    for ch in s:
        if ch.isdigit():
            nums.append(int(ch))
        else:
            base = {"m": 0, "p": 9, "s": 18, "z": 27}[ch]
            for x in nums:
                c[base + x - 1] += 1
            nums = []
    return c


def n(s: str) -> int:
    return sum(c34(s))


def R(key: str):
    return get_mode(key)


# 张数自检：任何字面量写错，这里立即炸
class TestTileLiterals(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(n("123m456p789s111p22s"), 14)
        self.assertEqual(n("123m456p789s111p2s"), 13)
        self.assertEqual(n("1199m1199p1199s22z"), 14)


# ============ 配置层：锁死 10 玩法关键规则字段（张冠李戴根因）============
class TestModeConfig(unittest.TestCase):
    def test_ten_modes_present(self):
        for k in ("sc_hz", "sc_xz", "sc_xl", "gy_zj",
                  "std_tdh", "wh_kk", "db_qh", "hz_bd", "gd_hz", "cs_zz"):
            self.assertIn(k, MODES)
            self.assertIn(get_analyzer(k), ("sichuan", "std"), k)

    def test_sichuan_family_laizi_split(self):
        # 血战到底：纯 108 张，无红中赖子
        self.assertIsNone(MODES["sc_xz"]["laizi"])
        self.assertNotIn(33, MODES["sc_xz"]["available"])
        # 血流红中 / 血流成河 / 贵阳捉鸡：红中(33) 既可用又做赖子
        for k in ("sc_hz", "sc_xl", "gy_zj"):
            self.assertEqual(MODES[k]["laizi"], 33, k)
            self.assertIn(33, MODES[k]["available"], k)
            self.assertTrue(MODES[k]["dingque"], k)

    def test_std_family_flags(self):
        self.assertIsNone(MODES["std_tdh"]["laizi"])
        self.assertTrue(MODES["std_tdh"]["kokushi"])
        self.assertTrue(MODES["wh_kk"].get("need_open"))
        self.assertEqual(MODES["wh_kk"]["laizi"], 33)
        self.assertTrue(MODES["db_qh"].get("need_terminals"))
        self.assertEqual(MODES["hz_bd"]["laizi"], 31)  # 白板百搭
        self.assertTrue(MODES["hz_bd"].get("fan_wild_per_use"))
        # 转转胡：不能吃（无顺子）、全刻、红中赖子
        self.assertFalse(MODES["cs_zz"]["sequences"])
        self.assertTrue(MODES["cs_zz"].get("need_all_pungs"))
        # 广东/长沙仅留红中一种字牌可用
        for k in ("gd_hz", "cs_zz"):
            self.assertIn(33, MODES[k]["available"])
            for honor in range(27, 33):
                self.assertNotIn(honor, MODES[k]["available"])


# ============ 大众推倒胡 std_tdh ============
class TestStdTdh(unittest.TestCase):
    K = "std_tdh"

    def test_basic_win(self):
        self.assertTrue(StdAnalyzer.can_win(c34("123m456p789s111p22s"), 0, R(self.K)))
        self.assertTrue(StdAnalyzer.can_win(c34("111m999m111p999p22s"), 0, R(self.K)))

    def test_no_pair_no_win(self):
        self.assertFalse(StdAnalyzer.can_win(c34("123m456p789s111p24s"), 0, R(self.K)))

    def test_count_guard(self):
        self.assertFalse(StdAnalyzer.can_win(c34("123m456p789s1122p"), 0, R(self.K)))

    def test_seven_pairs(self):
        self.assertTrue(StdAnalyzer.can_win(c34("1199m1199p1199s22z"), 0, R(self.K)))
        self.assertFalse(StdAnalyzer.can_win(c34("1199m1199p1199s23z"), 0, R(self.K)))

    def test_kokushi(self):
        self.assertTrue(StdAnalyzer.can_win(c34("119m19p19s1234567z"), 0, R(self.K)))


# ============ 东北穷胡 db_qh：必须带幺九 ============
class TestDbQh(unittest.TestCase):
    K = "db_qh"

    def test_with_terminal_wins(self):
        self.assertTrue(StdAnalyzer.can_win(c34("123m456p789s111p11z"), 0, R(self.K)))

    def test_all_middle_no_win(self):
        hand = "234m567m234p567p22s"
        self.assertFalse(StdAnalyzer.can_win(c34(hand), 0, R(self.K)),
                         "穷胡无幺九/字不得胡")

    def test_noninterference_same_hand(self):
        # 同一手牌在推倒胡可胡 → 证明 need_terminals 只在穷胡生效
        hand = "234m567m234p567p22s"
        self.assertTrue(StdAnalyzer.can_win(c34(hand), 0, R("std_tdh")))
        self.assertFalse(StdAnalyzer.can_win(c34(hand), 0, R(self.K)))


# ============ 长沙转转 cs_zz：只能碰不能吃 + 红中赖子 ============
class TestCsZz(unittest.TestCase):
    K = "cs_zz"

    def test_all_pungs_win(self):
        self.assertTrue(StdAnalyzer.can_win(c34("111m999p111s999s22z"), 0, R(self.K)))

    def test_sequence_no_win(self):
        self.assertFalse(StdAnalyzer.can_win(c34("123m456p111s999s22z"), 0, R(self.K)),
                         "转转胡禁顺子结构")

    def test_wild_pung_extend(self):
        # 红中(7z) 补 2s 成刻 + 白板... 用三红中：2s 对 + 赖子成刻/作将
        self.assertTrue(StdAnalyzer.can_win(c34("111m999p111s22s7z7z7z"), 0, R(self.K)))


# ============ 杭州百搭 hz_bd：白板(5z) 万能 + 逐张加番 ============
class TestHzBd(unittest.TestCase):
    K = "hz_bd"

    def test_wild_as_pair(self):
        self.assertTrue(StdAnalyzer.can_win(c34("123m456p789s111p2s5z"), 0, R(self.K)))

    def test_no_wild_when_stripped(self):
        self.assertFalse(
            StdAnalyzer.can_win(c34("123m456p789s111p2s5z"), 0,
                                {**R(self.K), "laizi": None}))

    def test_pure_wild_meld(self):
        self.assertTrue(StdAnalyzer.can_win(c34("123m456p789s11p5z5z5z"), 0, R(self.K)))

    def test_wild_fan_bonus(self):
        fan, names = StdAnalyzer.calc_fan(c34("123m456p789s111p2s5z"), 0, R(self.K))
        self.assertTrue(any("百搭" in x for x in names), names)


# ============ 武汉开口翻 wh_kk / 广东红中 gd_hz：红中鬼牌 ============
class TestWhGd(unittest.TestCase):
    def test_whk_wild_win(self):
        # 红中(7z) 替任意成胡
        self.assertTrue(StdAnalyzer.can_win(c34("123s456s789m11p23s7z"), 0, R("wh_kk")))

    def test_gd_wild_win(self):
        self.assertTrue(StdAnalyzer.can_win(c34("123s456s789m11p23s7z"), 0, R("gd_hz")))

    def test_laizi_never_discard_candidate(self):
        # 含红中的 14 张出牌态：分析结果里不应出现把红中(7z) 当弃牌
        res = StdAnalyzer.analyze_discards(
            c34("123m456p789s11p23s7z"), R("gd_hz"), ALL34)
        self.assertTrue(res)
        self.assertNotIn("7z", [r["tile"] for r in res])

    def test_gd_wild_tenpai_regression(self):
        # 回归锁：手中含赖子的 13 张必须判听牌。历史 bug：calculate_shanten
        # 把 split_wild 剥除赖子后的 counts 传给 is_tenpai（其内部会再次剥除）
        # → 赖子槽恒空，含赖子牌型全部漏报听牌。
        r = R("gd_hz")
        hand = c34("123m456p789s11p2s7z")
        self.assertEqual(n("123m456p789s11p2s7z"), 13)
        self.assertEqual(StdAnalyzer.calculate_shanten(hand, 0, r), 0)
        waits = StdAnalyzer.find_waits(hand, 0, r, ALL34)
        self.assertIn(19, waits)      # 2s：2s+赖子成刻 222s
        self.assertNotIn(33, waits)   # 赖子本体不作叫口上报

    def test_hz_wild_tenpai_regression(self):
        # 杭州百搭同理（赖子=5z 即 idx31）：含百搭 13 张判听、不上报百搭本体
        r = R("hz_bd")
        self.assertEqual(r.get("laizi"), 31)
        hand = c34("123m456p789s11p2s5z")
        self.assertEqual(StdAnalyzer.calculate_shanten(hand, 0, r), 0)
        waits = StdAnalyzer.find_waits(hand, 0, r, ALL34)
        self.assertIn(19, waits)
        self.assertNotIn(31, waits)


# ============ 川麻家族：数据层已由 SichuanAnalyzer 消费，锁死互不干扰 ============
class TestSichuanFamily(unittest.TestCase):
    def H(self, mpsz):
        return SA.counts_from_tiles(SA.parse_hand_mpsz(mpsz))

    def test_xz_two_suit_win(self):
        # 血战到底：万/筒两门（缺条）+ 将，无字牌即可胡
        self.assertTrue(is_sichuan_family("sc_xz"))
        self.assertTrue(SA.can_win(self.H("123m456m789m123p44p")))

    def test_three_suit_no_win(self):
        # 三门未缺 → 川麻不得胡（区别于推倒胡，互不干扰）
        self.assertFalse(SA.can_win(self.H("123m456p789s11m22p")))
        self.assertTrue(StdAnalyzer.can_win(c34("123m456p789s111m22p"), 0, R("std_tdh")))

    def test_hz_red_dragon_wild(self):
        # 血流红中：红中(7z) 帮补成胡（缺条，万筒两门 + 赖子）
        self.assertTrue(SA.can_win(self.H("123m456m789m1p2p3p5p7z")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
