# -*- coding: utf-8 -*-
"""评测归因层的单测：blame() 分桶 与 shift_runs() 摘掉“整段平移”。

为什么要给评测脚本本身写测试：归因桶直接决定下一步攻哪里。shift_runs 判据
从 `>=2` 改成 `>=1` 就会把“单格巧合”当成检测层平移，真失误从 25 张掉到十几张，
而**报告上的总精度一个字都不会变**——只有归因在悄悄撒谎。这类“数字不变、
结论变了”的缺陷必须用断言钉住，不能靠人看图。

运行:
  py -3.10 -X utf8 localtest/test_eval_blame.py
  或 py -3.10 -m unittest localtest.test_eval_blame -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from eval_new_material import blame, shift_runs  # noqa: E402


class TestBlame(unittest.TestCase):
    def test_漏框独立成桶(self):
        # 漏框是检测层的事，不能混进“数错数”（两者修法完全不同）
        self.assertEqual(blame("9s", "<无>"), "漏框/未检出")

    def test_跨花色与同花色分开(self):
        self.assertEqual(blame("6z", "1s"), "跨花色形近抢判")
        self.assertEqual(blame("5s", "7s"), "同花色错号")

    def test_相邻号按花色给具体名字(self):
        # 这三条就是历史上“索子根数/筒子圈数”两个说法的来源，名字不能漂
        self.assertEqual(blame("8p", "7p"), "筒子圈数±1")
        self.assertEqual(blame("4s", "5s"), "索子根数±1")
        self.assertEqual(blame("7m", "8m"), "萬字笔画±1")

    def test_环绕序号不算相邻(self):
        # 9->1 号差 8，不是数错一格；写成 abs(1-n) 就会把它塞进±1 桶
        self.assertEqual(blame("9p", "1p"), "同花色错号")


class TestShiftRuns(unittest.TestCase):
    def test_weile06_实测多框平移(self):
        # GT 13 张里 7p/8p/9p 各一枚，det 多出一个 6p 导致后段整体左移：
        # 旧版把这三格报成“筒子圈数±1x3”，其实分类器一张没错
        gt = ["1p", "2p", "3p", "4p", "5p", "5p", "6p", "6p", "6p",
              "7p", "8p", "9p", "9p"]
        got = ["1p", "2p", "3p", "4p", "5p", "5p", "6p", "6p", "6p",
               "6p", "7p", "8p", "9p"]
        self.assertEqual(shift_runs(gt, got), {9, 10, 11})

    def test_tencent08_实测两格平移(self):
        gt = ["3m", "3m", "3m", "4m", "5m", "8m", "8m", "6s", "1p", "2p", "3p", "7p", "7p"]
        got = ["3m", "3m", "3m", "4m", "5m", "8m", "8m", "6s", "1p", "2p", "2p", "3p", "7p"]
        self.assertEqual(shift_runs(gt, got), {10, 11})

    def test_单格巧合不算平移(self):
        # 只有 1 格满足 gt[k]==got[k+1]：这是真的认错牌，不能塞进检测层桶
        gt = ["4s", "5s", "9p"]
        got = ["5s", "9p", "9p"]
        self.assertEqual(shift_runs(gt, got), set())

    def test_纯分类错误不误报(self):
        # 5s->7s 这种同位错号，前后不满足平移关系
        gt = ["3m", "5s", "9p"]
        got = ["3m", "7s", "9p"]
        self.assertEqual(shift_runs(gt, got), set())

    def test_全对与全错边界(self):
        self.assertEqual(shift_runs(["1s", "2s"], ["1s", "2s"]), set())
        # 完全错开但无平移关系：不该吞掉任何格（吞了就是拿归因做小失误数）
        self.assertEqual(shift_runs(["1s", "2s"], ["8p", "9p"]), set())

    def test_单格平移与巧合不可分故意不摘(self):
        # gt=[4s,1p,2p,3p] got=[8s,1p,1p,2p]：只有 1 格满足平移关系，而“单格
        # 巧合”长一模一样。宁可把这格算回分类失误（高估自己的锅），也不能
        # 把真错判成检测层（低估锅）——后者会让归因报告把问题说没。
        gt = ["4s", "1p", "2p", "3p"]
        got = ["8s", "1p", "1p", "2p"]
        self.assertEqual(shift_runs(gt, got), set())

    def test_平移段不吞掉段外错格(self):
        # 前段真错 1 格 + 后段平移 3 格：只应摘掉平移那三格，段外那格留着
        gt = ["4s", "1p", "2p", "3p", "4p", "5p"]
        got = ["8s", "1p", "1p", "2p", "3p", "4p"]
        self.assertEqual(shift_runs(gt, got), {2, 3, 4})


if __name__ == "__main__":
    unittest.main(verbosity=2)
