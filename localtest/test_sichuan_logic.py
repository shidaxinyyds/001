# -*- coding: utf-8 -*-
"""P3 · SichuanAnalyzer 标准牌例单测（锁死定缺/换牌/选牌/出牌决策逻辑）。

与 verify_logic.py 互补：verify_logic 测的是 trainer/ 的通用向听引擎；
本文件专测生产实际使用的 sichuan/sichuan_analyzer.py 的**决策函数**，
全部用「已知答案的标准牌例」对拍，不依赖截图、不依赖牌河识别。

运行:
  py -3.10 -m unittest localtest.test_sichuan_logic -v
  或 py -3.10 localtest/test_sichuan_logic.py
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from sichuan.sichuan_analyzer import (  # noqa: E402
    SichuanAnalyzer as SA,
    SUIT_M, SUIT_P, SUIT_S, SUIT_NAMES,
    mpsz_to_index27, index27_to_mpsz,
)


def H(mpsz: str):
    """mpsz 字符串 -> 28 长度计数数组。"""
    return SA.counts_from_tiles(SA.parse_hand_mpsz(mpsz))


def idx(mpsz_tile: str) -> int:
    return mpsz_to_index27(mpsz_tile)


class TestDingque(unittest.TestCase):
    """定缺：应缺张数最少的一门。"""

    def test_shortest_suit_is_dingque(self):
        c = H("1m9m5m" + "1p2p3p4p5p" + "1s2s3s4s5s")  # 万3 筒5 条5
        r = SA.recommend_dingque(c)
        self.assertEqual(r["suit_id"], SUIT_M)
        self.assertEqual(r["suit"], "万")
        self.assertEqual(r["count"], 3)

    def test_clear_single_shortest(self):
        c = H("1m2m3m4m5m6m7m8m9m" + "1p2p3p4p5p6p7p" + "1s")  # 万9 筒7 条1
        r = SA.recommend_dingque(c)
        self.assertEqual(r["suit_id"], SUIT_S)
        self.assertEqual(r["count"], 1)

    def test_dingque_reason_mentions_fastest_tenpai(self):
        c = H("5m" + "1p2p3p4p5p6p7p8p9p" + "1s2s3s4s")
        r = SA.recommend_dingque(c)
        self.assertEqual(r["suit_id"], SUIT_M)
        self.assertIn("听", r["reason"])


class TestHuanSanZhang(unittest.TestCase):
    """换三张：换出最短/最孤的一门，绝不虚报、绝不跨门。"""

    def test_swap_out_isolated_shortest_suit(self):
        # 万3(全孤 1/5/9) 筒5 条5
        c = H("1m5m9m" + "1p2p3p4p5p" + "1s2s3s4s5s")
        r = SA.recommend_huan_san_zhang(c)
        self.assertTrue(r["viable"])
        self.assertEqual(r["suit"], "万")
        self.assertEqual(sorted(r["tiles"]), ["1m", "5m", "9m"])

    def test_returned_tiles_are_three_same_suit_and_in_hand(self):
        c = H("1m2m3m4m" + "5p6p7p8p" + "1s2s3s4s5s")  # 万4 筒4 条5
        r = SA.recommend_huan_san_zhang(c)
        if r["viable"]:
            self.assertEqual(len(r["tiles"]), 3)
            suits = {mpsz_to_index27(t) // 9 for t in r["tiles"]}
            self.assertEqual(len(suits), 1, "换出的三张必须同门")
            for t in r["tiles"]:
                self.assertGreater(c[idx(t)], 0, "换出的牌必须在手牌中")

    def test_no_suit_with_three_is_not_viable(self):
        c = H("1m2m" + "1p2p" + "1s2s")  # 每门仅 2 张
        r = SA.recommend_huan_san_zhang(c)
        self.assertFalse(r["viable"])


class TestDiscard(unittest.TestCase):
    """出牌/选叫：EV 排序、强制定缺、面子保护、听牌判定。"""

    def test_results_sorted_by_ev_desc(self):
        c = H("123m456m789m1p2p3p5p9p")  # 两门，含孤张 5p/9p
        res = SA.analyze_discards(c, dingque_suit=SUIT_S)
        evs = [x["ev"] for x in res]
        self.assertEqual(evs, sorted(evs, reverse=True))

    def test_must_discard_dingque_suit(self):
        # 手牌含一条 9s（定缺门），必须优先打缺门
        c = H("123m456m789m1p2p3p5p" + "9s")
        res = SA.analyze_discards(c, dingque_suit=SUIT_S)
        self.assertTrue(res, "应给出候选")
        self.assertEqual(res[0]["tile"], "9s")
        self.assertTrue(res[0]["is_dingque"])
        # 含定缺牌时，候选只应来自定缺门（条 s）
        for x in res:
            self.assertEqual(x["tile"][1], "s")

    def test_isolated_tile_is_best_discard_not_meld(self):
        # 三副万 + 1p2p3p 副 + 孤张 5p、8p：最优打孤张，不动面子
        c = H("123m456m789m1p2p3p5p8p")
        res = SA.analyze_discards(c, dingque_suit=SUIT_S)
        self.assertIn(res[0]["tile"], {"5p", "8p"})
        # 面子中的牌（如 1m）不应排第一
        self.assertNotEqual(res[0]["tile"], "1m")

    def test_tenpai_detected_with_correct_wait(self):
        # 13 张单钓 5p 听牌
        c = H("123m456m789m1p2p3p5p")
        wait = SA.find_waiting_tiles(c)
        self.assertIn(idx("5p"), wait)
        self.assertEqual(set(wait.keys()), {idx("5p")})


class TestWinAndShanten(unittest.TestCase):
    """胡牌/向听 oracle。"""

    def test_can_win_standard(self):
        c = H("123456789m123p44p")  # 万9(3副)+筒1p2p3p+4p4p将 = 4副1对
        self.assertTrue(SA.can_win(c))

    def test_cannot_win_three_suits(self):
        c = H("123m456p789s11m22p")  # 三门未缺
        self.assertFalse(SA.can_win(c))

    def test_seven_pairs_win(self):
        c = H("1122334455667m")  # 万 6 对 + ... 需 7 对
        c2 = H("11223344556677m")  # 7 对
        self.assertTrue(SA.can_win(c2))
        self.assertFalse(SA.is_seven_pairs(c))  # 13 张非七对（sum!=14）

    def test_shanten_tenpai_zero(self):
        c = H("123m456m789m1p2p3p5p")  # 单钓听牌
        self.assertEqual(SA.calculate_shanten(c), 0)

    def test_wild_red_dragon_helps_win(self):
        # 缺一张 + 一张红中赖子 应能胡
        c = H("123m456m789m1p2p3p5p" + "7z")  # 13 张 + 赖子
        self.assertTrue(SA.can_win(c))


class TestDefenseRadarEvidenceGating(unittest.TestCase):
    """锁死 P1 证据门控：牌河证据不足时不得凭空捏造危险。"""

    def test_no_fabricated_danger_without_evidence(self):
        c = H("5p")  # 中心张 5p，seen=0
        ratings = SA.evaluate_defense_radar(c[:27], pool_evidence=0)
        for r in ratings:
            self.assertNotIn(r["level"], {"DANGER", "SUSPICIOUS"})

    def test_danger_reappears_with_evidence(self):
        c = H("5p")
        ratings = SA.evaluate_defense_radar(c[:27], pool_evidence=5)
        self.assertTrue(any(r["level"] == "DANGER" for r in ratings),
                        "证据充足后中心生张应恢复 DANGER 评级")

    def test_opponent_dingque_is_always_safe(self):
        c = H("5p")
        ratings = SA.evaluate_defense_radar(
            c[:27], opponents_dingque=[SUIT_P], pool_evidence=5)
        self.assertTrue(all(r["level"] == "SAFE" for r in ratings))


class TestTenpaiAlert(unittest.TestCase):
    """查大叫 / 查花猪 生死预警。"""

    def test_huazhu_critical(self):
        # 牌墙将尽且手仍含定缺门 -> 花猪警报
        c = H("123m456m789m1p2p3p5p" + "9s")
        a = SA.check_tenpai_alert(c, tiles_remaining_in_wall=15, dingque_suit=SUIT_S)
        self.assertTrue(a["alert"])
        self.assertEqual(a["type"], "huazhu")
        self.assertEqual(a["level"], "CRITICAL")
        self.assertIn("9s", a["must_discard"])

    def test_tenpai_no_alert(self):
        c = H("123m456m789m1p2p3p5p")  # 已听牌，无缺门牌
        a = SA.check_tenpai_alert(c, tiles_remaining_in_wall=15, dingque_suit=SUIT_S)
        self.assertFalse(a["alert"])
        self.assertEqual(a["type"], "tenpai")

    def test_wall_not_low_returns_none(self):
        c = H("123m456m789m1p2p3p5p9p")
        self.assertIsNone(SA.check_tenpai_alert(c, tiles_remaining_in_wall=60,
                                                 dingque_suit=SUIT_S))


if __name__ == "__main__":
    unittest.main(verbosity=2)
