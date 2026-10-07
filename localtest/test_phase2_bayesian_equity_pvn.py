# -*- coding: utf-8 -*-
"""Phase 2 & Phase 3 核心业务逻辑与模型集成单测：
1. 贝叶斯对手手牌透视 (Bayesian Hand Range Reading)
2. 全息危险流向反推 (Danger Flow Reverse Inference)
3. 全场实时胡牌胜率与期望收益雷达 (Win Equity & EV Gauge)
4. 轻量级强化学习策略价值网络 (Policy-Value Network, PVN)
"""
import os
import sys
import unittest
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

from sichuan.hand_range import BayesianHandRangeReader, OpponentState, SUIT_M, SUIT_P, SUIT_S
from sichuan.equity_radar import WinEquityGauge
from recognition.policy_value_net import PolicyValueNetwork
from sichuan.sichuan_analyzer import SichuanAnalyzer


class TestBayesianHandRange(unittest.TestCase):
    """对手手牌贝叶斯概率透视单测。"""

    def setUp(self):
        # 初始 27 种牌各余 2 张未见
        self.pool_remaining = [2] * 27

    def test_dingque_suit_is_strictly_zero_probability(self):
        # 对手定缺筒 (SUIT_P)
        opp = OpponentState(seat=1, name="下家", dingque_suit=SUIT_P, standing_count=13)
        dist = BayesianHandRangeReader.calculate_hand_distribution(opp, self.pool_remaining)
        # 筒子 (9..17) 的持有概率必须严格为 0
        for t in range(9, 18):
            self.assertEqual(dist[t], 0.0, f"定缺筒门牌 {t} 持有概率必须为 0")
        # 非定缺花色持有概率应大于 0
        self.assertGreater(dist[0], 0.0)

    def test_genbutsu_probability_discount(self):
        # 对手刚打出 1m (现物)
        opp = OpponentState(seat=2, name="对家", discards=[0], standing_count=13)
        dist = BayesianHandRangeReader.calculate_hand_distribution(opp, self.pool_remaining)
        # 现物 1m 的后验持有概率应显著低于未打的同剩余量牌 2m
        self.assertLess(dist[0], dist[1] * 0.3, "现物持有概率应显著折减")

    def test_absent_suit_bias_amplification(self):
        # 对手狂打万和条，从未打过筒 -> 筒子持有概率激增
        opp = OpponentState(seat=3, name="上家", discards=[0, 1, 2, 18, 19, 20], standing_count=13)
        dist = BayesianHandRangeReader.calculate_hand_distribution(opp, self.pool_remaining)
        # 筒子 (如 5p，索引 13) 概率应明显高于万/条 (如 5m，索引 4)
        self.assertGreater(dist[13], dist[4], "未打过的蓄势花色概率应大幅上升")

    def test_tenpai_probability_increases_with_melds_and_turns(self):
        opp_early = OpponentState(seat=1, discards=[0, 1], melds=[])
        opp_late = OpponentState(seat=1, discards=list(range(14)), melds=[9, 9, 9, 10, 10, 10])
        p_early = opp_early.estimate_tenpai_probability(turn=2)
        p_late = opp_late.estimate_tenpai_probability(turn=14)
        self.assertLess(p_early, 0.25)
        self.assertGreater(p_late, 0.70)


class TestDangerFlowInference(unittest.TestCase):
    """危险流向反推单测。"""

    def setUp(self):
        self.pool_remaining = [2] * 27

    def test_dingque_tile_is_always_safe(self):
        opponents = [
            OpponentState(seat=1, name="下家", dingque_suit=SUIT_M),
            OpponentState(seat=2, name="对家", dingque_suit=SUIT_M),
            OpponentState(seat=3, name="上家", dingque_suit=SUIT_M),
        ]
        # 1m (万门) 对全场定缺万的对手
        df = BayesianHandRangeReader.evaluate_danger_flow(0, self.pool_remaining, opponents)
        self.assertEqual(df["deal_in_prob"], 0.0)
        self.assertEqual(df["danger_level"], "safe")
        self.assertIn("全场安目", df["danger_reason"])

    def test_center_tile_is_dangerous_when_tenpai_high(self):
        # 对手均为高听牌率
        opponents = [
            OpponentState(seat=1, name="下家", discards=list(range(12)), melds=[0, 0, 0]),
            OpponentState(seat=2, name="对家", discards=list(range(12)), melds=[9, 9, 9]),
        ]
        # 5p (中张纯生张)
        df = BayesianHandRangeReader.evaluate_danger_flow(13, self.pool_remaining, opponents, total_turn=14)
        self.assertGreater(df["deal_in_prob"], 0.15)
        self.assertIn(df["danger_level"], {"medium", "high", "critical"})

    def test_suji_defense_discount(self):
        # 对家打过 4m (索引 3)
        opp = OpponentState(seat=2, name="对家", discards=[3])
        # 测试 1m (筋牌) vs 2m (非筋生张)
        df_1m = BayesianHandRangeReader.evaluate_danger_flow(0, self.pool_remaining, [opp])
        df_2m = BayesianHandRangeReader.evaluate_danger_flow(1, self.pool_remaining, [opp])
        self.assertLess(df_1m["deal_in_prob"], df_2m["deal_in_prob"])


class TestWinEquityAndEVGauge(unittest.TestCase):
    """实时胡牌胜率与期望收益雷达单测。"""

    def setUp(self):
        self.pool_remaining = [3] * 27

    def test_tenpai_with_ample_outs_has_high_equity(self):
        # 听 5p，场上存活 4 张
        waiting = {13: 4}
        eq = WinEquityGauge.calculate_win_equity(0, waiting, 4, self.pool_remaining)
        self.assertGreater(eq, 0.60)
        self.assertLessEqual(eq, 1.0)

    def test_dead_wait_has_zero_equity(self):
        # 绝张死叫 (存活 0 张)
        waiting = {13: 0}
        eq = WinEquityGauge.calculate_win_equity(0, waiting, 0, self.pool_remaining)
        self.assertEqual(eq, 0.0)

    def test_gauge_evaluation_levels(self):
        # 高胜率 -> extreme / high
        g_high = WinEquityGauge.evaluate_gauge(0.85, expected_fan=3, max_deal_in_prob=0.02)
        self.assertIn(g_high["level"], {"extreme", "high"})
        self.assertGreater(g_high["net_ev"], 10.0)

        # 低胜率高风险 -> risk
        g_low = WinEquityGauge.evaluate_gauge(0.05, expected_fan=1, max_deal_in_prob=0.45)
        self.assertEqual(g_low["level"], "risk")


class TestPolicyValueNetwork(unittest.TestCase):
    """强化学习策略价值网络单测。"""

    def setUp(self):
        self.pvn = PolicyValueNetwork.get_instance()

    def test_output_shapes_and_ranges(self):
        hand = [1] * 14 + [0] * 20
        policy, val = self.pvn.forward(hand)
        self.assertEqual(len(policy), 34)
        self.assertAlmostEqual(float(np.sum(policy)), 1.0, places=4)
        self.assertGreaterEqual(val, -1.0)
        self.assertLessEqual(val, 1.0)

    def test_unheld_tiles_are_masked_in_policy(self):
        # 手牌只有 1m (索引 0) 和 2m (索引 1)
        hand = [2, 2] + [0] * 32
        policy, _ = self.pvn.forward(hand)
        # 未持有的牌索引概率应为 0.0
        for i in range(2, 34):
            self.assertEqual(policy[i], 0.0)
        # 持有的牌概率和为 1.0
        self.assertAlmostEqual(policy[0] + policy[1], 1.0, places=4)

    def test_integration_in_sichuan_analyzer(self):
        """默认态（权重未训练）：PVN 必须完全不参与决策与胜率。

        旧断言 `policy_prob > 0` 锁的是「未训练网络也在抬高指标」这个 bug 本身，
        故按真实契约重写：开关关着时 policy 项恒 0、胜率逐字等于解析式、排序由
        解析式 EV 决定。
        """
        counts = [0] * 28
        # 手牌 123m 456m 789m 123p 55p
        for t in range(9):
            counts[t] = 1
        for t in range(9, 12):
            counts[t] = 1
        counts[13] = 2  # 5p 对子

        self.assertFalse(self.pvn.trained, "固定初始化权重不得标记为已训练")

        results = SichuanAnalyzer.analyze_discards(counts)
        self.assertTrue(results, "应生成有效建议")
        top = results[0]
        # 核心字段均存在且口径正确
        for k in ("danger_flow", "policy_prob", "win_equity", "ev_gauge",
                  "pvn_used", "analytical_equity"):
            self.assertIn(k, top)
        self.assertIsNotNone(top["ev_gauge"])
        for r in results:
            self.assertFalse(r["pvn_used"], "未训练时不得标记为已融合")
            self.assertEqual(r["policy_prob"], 0.0, "policy 项必须归零，不得向 EV 注入伪偏好")
            self.assertAlmostEqual(r["win_equity"], r["analytical_equity"], places=3,
                                   msg="胜率必须回到纯解析式区间")
            self.assertGreaterEqual(r["win_equity"], 0.0)
            self.assertLessEqual(r["win_equity"], 1.0)
        evs = [r["ev"] for r in results]
        self.assertEqual(evs, sorted(evs, reverse=True), "排序必须由解析式 EV 给出")

    def test_trained_switch_really_gates(self):
        """变异检验：把 trained 拨成 True，PVN 必须立刻回到融合路径。

        这条测试专门防止开关被写成「两边都不生效」的死代码：若摘掉权重后
        再也没人能把它打开，那等于 PVN 永远只是个装饰；若打开后胜率纹丝不动，
        则融合已接错线。两种错法都会被下面的断言抓住。
        """
        counts = [0] * 28
        for t in range(9):
            counts[t] = 1
        for t in range(9, 12):
            counts[t] = 1
        counts[13] = 2

        baseline = SichuanAnalyzer.analyze_discards(counts)
        self.assertTrue(baseline)
        try:
            self.pvn.trained = True
            fused = SichuanAnalyzer.analyze_discards(counts)
        finally:
            self.pvn.trained = False
        self.assertTrue(fused)
        top = fused[0]
        self.assertTrue(top["pvn_used"], "trained=True 后必须重新参与融合")
        self.assertGreater(top["policy_prob"], 0.0)
        # 常数偏置必须体现为胜率抬高（这正是 P0 摘掉的那 ~+0.4）。按牌对齐，
        # 不能按位置 zip：加了 policy 项后两侧排序本身就会不同。
        base_map = {r["tile"]: r["win_equity"] for r in baseline}
        bias = [r["win_equity"] - base_map[r["tile"]]
                for r in fused if r["tile"] in base_map]
        self.assertGreaterEqual(len(bias), 3, "候选牌应能按牌对齐")
        self.assertTrue(all(d > 0.05 for d in bias),
                        msg=f"旧融合应显著抬高胜率，实测 {bias[:4]}")
        # 关掉后必须逐字回到基线（幂等，不因多次调用累计污染）
        again = SichuanAnalyzer.analyze_discards(counts)
        self.assertEqual([r["win_equity"] for r in again],
                         [r["win_equity"] for r in baseline],
                         msg="恢复开关后胜率必须逐字回到基线")


if __name__ == "__main__":
    unittest.main(verbosity=2)
