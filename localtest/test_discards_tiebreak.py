# -*- coding: utf-8 -*-
"""B-P2 同分牌理裁决（discards_tiebreak）回归。

三类断言，缺一不可：
1. 逐层**变异检验**：每一层都必须能在「上层全部持平」时被拨动而改变名次。
   只验「排好序」不验「哪一层决定的」，就是空守卫——决胜层最常见的坏法是永远轮不到它。
2. 确定性：同一组候选的任意输入排列，输出序列必须逐位相同（消除随机性才是本任务目标）。
3. 接线：三个排序点 + 双策略路线必须引用**同一个** key 对象，且源码里不许复活旧写法。
   链写对了但没接上 = 死代码（本项目栽过好几次）。

运行：py -3.10 localtest/test_discards_tiebreak.py
"""
from __future__ import annotations

import copy
import io
import os
import random
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

import discards_tiebreak as TB  # noqa: E402
from discards_tiebreak import (first_diff_layer, order_key, sort_discards,  # noqa: E402
                               tie_tail, tile_flex_rank)


def item(tile, ev=10.0, ukeire=6, waits=None, wall_lo=None, danger=None,
         dingque=False):
    """构造一条最小候选：只有被测试显式改动的字段参与区分。"""
    chance = None
    if wall_lo is not None:
        chance = {"wall_lo_total": wall_lo, "total_unseen": ukeire}
    it = {
        "tile": tile,
        "ev": ev,
        "ukeire": ukeire,
        "shanten": 0,
        "ting_tiles": list(waits or []),
        "ting_chance": chance,
        "is_dingque": dingque,
    }
    if danger is not None:
        it["danger_penalty"] = danger
    return it


class TestFlexAndParsing(unittest.TestCase):
    def test_flex_rank_order(self):
        self.assertEqual(tile_flex_rank("1z"), 0)   # 字牌最先打
        self.assertEqual(tile_flex_rank("9m"), 1)   # 幺九次之
        self.assertEqual(tile_flex_rank("5m"), 2)   # 中张最后
        self.assertEqual(tile_flex_rank("1p"), 1)
        self.assertEqual(tile_flex_rank("8s"), 2)

    def test_bad_tile_input_never_grabs_priority(self):
        # 解析失败一律当中张（最高档），绝不因为脏数据抢到「先打」的位置
        for bad in ("", "x", "0m", "10m", "8z", None, 5):
            self.assertEqual(tile_flex_rank(str(bad if bad is not None else "")), TB._FLEX_SIMPLE,
                             msg=f"脏输入 {bad!r} 不该参与牌理裁决")

    def test_index_parser_is_inverse_of_ledger_formatter(self):
        """与 tile_ledger.idx_to_mpsz 双向对拍：两份独立实现必须一致。"""
        from tile_ledger import idx_to_mpsz
        for i in range(34):
            self.assertEqual(TB._mpsz_to_idx34(idx_to_mpsz(i)), i, msg=f"idx {i} 不自洽")


class TestLayerByLayerMutation(unittest.TestCase):
    """每一层都要在「上层全平」时真的能决定名次（变异检验）。"""

    def _assert_first(self, a, b, layer, expect_a_first=True):
        """a、b 只在 layer 上有差异：先确认差异确实来自该层，再确认名次符合预期。"""
        self.assertEqual(first_diff_layer(a, b), layer,
                         msg=f"本应由「{layer}」分开，实际差异在更早的层")
        out = sort_discards([b, a])       # 故意逆序喂，防「输入顺序恰好对」的假通过
        self.assertEqual(out[0]["tile"] == a["tile"], expect_a_first,
                         msg=f"「{layer}」层裁决反了：{[x['tile'] for x in out]}")

    def test_layer0_ev_is_always_the_primary_key(self):
        """EV 必须在最前：高 EV 但非缺门的牌，仍要压低 EV 的缺门牌排在它前面。

        这条是「零副作用」的构造保证：决胜链只裁决同分，绝不改掉非同分的主推牌。
        曾经把定缺门写成第 0 层，那会让缺门牌无条件抢主推选位（越权改牌理模型）。
        """
        a = item("5s", ev=20.0, ukeire=9, dingque=True)
        b = item("5m", ev=99.0, ukeire=30)
        self.assertEqual(first_diff_layer(b, a), "EV")
        self.assertEqual(sort_discards([a, b])[0]["tile"], "5m",
                         msg="定缺层越过了 EV：非同分的主推牌被改掉，零副作用破坏")

    def test_layer1_dingque_decides_on_tie(self):
        # 同 EV 时缺门牌先处理（川麻不断门不能胡）
        a = item("5s", ev=20.0, ukeire=9, dingque=True)
        b = item("3p", ev=20.0, ukeire=9)
        self._assert_first(a, b, "定缺门")

    def test_ev_beats_ukeire_even_when_fewer(self):
        """EV 优先于进张：高 EV 但进张少的牌必须仍排在前面（否则等于改掉了价值模型）。"""
        a = item("5m", ev=12.0, ukeire=2)
        b = item("3p", ev=11.0, ukeire=20)
        self._assert_first(a, b, "EV")

    def test_layer2_ukeire(self):
        self._assert_first(item("5m", ukeire=8), item("3p", ukeire=6), "进张数")

    def test_layer3_wait_count(self):
        # 同为 3 张进张：三门各 1 张（叫口种类 3）比一门 3 张（种类 1）更难被一次弃牌打掉
        self._assert_first(item("5m", ukeire=3, waits=["1p", "2p", "3p"]),
                           item("3p", ukeire=3, waits=["9m"]), "听口面数")

    def test_layer4_wall_lo(self):
        self._assert_first(item("5m", waits=["1p"], wall_lo=3),
                           item("3p", waits=["1p"], wall_lo=0), "牌墙可摸")

    def test_layer5_safety(self):
        self._assert_first(item("5m", danger=0.0), item("3p", danger=12.0), "防守安全")

    def test_layer6_flex(self):
        self._assert_first(item("1z"), item("5m"), "牌面弹性")
        self._assert_first(item("9m"), item("5m"), "牌面弹性")

    def test_layer7_index_fallback_is_deterministic(self):
        # 前 7 层全等：只剩索引兜底（万<筒<条<字）。没有它就会退化成枚举序。
        a, b = item("2p"), item("5p")
        self.assertEqual(first_diff_layer(a, b), "索引兜底")
        self.assertEqual([x["tile"] for x in sort_discards([b, a])], ["2p", "5p"])

    def test_layer_order_is_strictly_prioritized(self):
        """低层不得越过高层：把弹性差拉到最大，仍要被进张数压住。"""
        a = item("1z", ukeire=2)          # 字牌（弹性最优先打）但进张少
        b = item("5m", ukeire=9)          # 中张但进张多
        self.assertEqual(sort_discards([a, b])[0]["tile"], "5m",
                         msg="牌面弹性越过了进张数，优先级链被写反")


class TestDeterminism(unittest.TestCase):
    def test_output_identical_under_any_input_permutation(self):
        base = [item("1z", ukeire=4), item("9m", ukeire=4), item("5m", ukeire=4),
                item("3p", ukeire=7), item("7s", ukeire=7), item("2s", ev=1.0),
                item("8p", ev=1.0, dingque=True)]
        expect = [x["tile"] for x in sort_discards(base)]
        rng = random.Random(20261004)
        for _ in range(200):
            shuffled = base[:]
            rng.shuffle(shuffled)
            self.assertEqual([x["tile"] for x in sort_discards(shuffled)], expect,
                             msg="同一组候选因输入顺序不同而给出不同排序 = 随机性未消除")

    def test_no_side_effects_on_items(self):
        base = [item("1z"), item("5m", danger=3.0)]
        before = copy.deepcopy(base)
        out = sort_discards(base)
        self.assertEqual(base, before, "排序不许改动候选条目（engine 还按原顺序做诊断）")
        self.assertEqual(len(out), len(base))
        for k in ("ev", "ukeire", "ting_chance"):
            self.assertNotIn("_tie", str(out[0].keys()), "不得往条目里塞私有字段")


class TestRobustness(unittest.TestCase):
    def test_missing_and_none_fields_do_not_crash(self):
        """脏条目绝不能让排序抛异常：这里出错的表现是「建议突然全空」，最难查。"""
        rows = [
            {"tile": "5m"},
            {"tile": "3p", "ev": None, "ukeire": None, "ting_tiles": None,
             "ting_chance": None, "danger_penalty": None, "is_dingque": None},
            {"tile": "bad", "ev": "x", "ukeire": "y", "ting_tiles": {"no": 1},
             "ting_chance": 5},
            {"tile": "1z", "ev": 1.0, "ukeire": 1, "danger_flow": {"deal_in_prob": 0.2}},
        ]
        out = sort_discards(rows)
        self.assertEqual(len(out), 4)
        # 有可信 EV 的排最前（-1.0 > 其余 0/脏值按 0 处理）
        self.assertEqual(out[0]["tile"], "1z")

    def test_empty_and_single(self):
        self.assertEqual(sort_discards([]), [])
        self.assertEqual([x["tile"] for x in sort_discards([item("5m")])], ["5m"])

    def test_tie_tail_reusable_by_different_primary_keys(self):
        """双策略路线主键不同，但 tie 部分必须与主链同一份。"""
        a, b = item("1z"), item("5m")
        self.assertEqual(tie_tail(a)[4:], order_key(a)[6:], "tie_tail 与 order_key 尾部漂移")
        # 主键（向听）相等时，胜负必须交给同一个尾部链
        key_a = (0, -int(a["ukeire"])) + tie_tail(a)
        key_b = (0, -int(b["ukeire"])) + tie_tail(b)
        self.assertLess(key_a, key_b, "极速流在同分时应先打字牌，与主链一致")


class TestWiringGuards(unittest.TestCase):
    """链写对 ≠ 链生效：三个排序点 + engine 都必须引用同一个对象，旧写法不许复活。"""

    SRC = {
        "sichuan": os.path.join(PKG, "sichuan", "sichuan_analyzer.py"),
        "std": os.path.join(PKG, "std", "std_analyzer.py"),
        "kb": os.path.join(PKG, "knowledge_base.py"),
        "engine": os.path.join(PKG, "engine", "engine.py"),
    }

    def _read(self, who):
        with io.open(self.SRC[who], encoding="utf-8") as fh:
            return fh.read()

    def test_analyzers_and_kb_share_one_key_object(self):
        import knowledge_base
        import sichuan.sichuan_analyzer as sc
        import std.std_analyzer as std
        self.assertIs(sc._tie_order_key, order_key, "川麻引用的不是同一份 key")
        self.assertIs(std._tie_order_key, order_key, "std 引用的不是同一份 key")
        self.assertIs(knowledge_base._tie_order_key, order_key, "知识库引用的不是同一份 key")
        from engine import engine as eng
        self.assertIs(eng._tie_tail, tie_tail, "engine 双路线引用的不是同一份尾部")

    def test_call_sites_count_and_no_legacy_keys(self):
        sc = self._read("sichuan")
        # 14 张 + 13 张预摸两条路径各一处
        self.assertEqual(sc.count("results.sort(key=_tie_order_key)"), 2,
                         "川麻两条出牌路径必须都接上（少一条就是同一手牌两条路径给不同主推牌）")
        self.assertEqual(self._read("std").count("results.sort(key=_tie_order_key)"), 1)
        self.assertEqual(self._read("kb").count("advice_list.sort(key=_tie_order_key)"), 1)
        # 旧的「同分交给枚举序」写法一律不许复活
        self.assertNotIn('key=lambda item: item["ev"]', sc)
        self.assertNotIn('(-r["ev"], -r["ukeire"])', self._read("std"))
        self.assertNotIn('-float(x.get("is_dingque", False))', self._read("kb"))
        self.assertEqual(len(re.findall(r"sorted\(advice, key=", self._read("engine"))), 2,
                         "engine 双策略路线的两处排序必须都带决胜尾部")


if __name__ == "__main__":
    unittest.main(verbosity=2)
