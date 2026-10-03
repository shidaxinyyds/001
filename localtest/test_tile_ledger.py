# -*- coding: utf-8 -*-
"""牌局账本 tile_ledger 的对拍与守卫测试。

方法：不写「第二个同样公式的实现」当参照（那只会自我复制错误），而是用
**穷举可行域**——对小规模局面枚举未现牌在「牌墙 / 各家手上」的全部合法分配，
拿到每型牌墙量的真实取值集合，断言账本给出的区间必须覆盖它。区间一旦算窄
（撒谎说「这张一定在牌墙」）就会被抓到。

跑法：py -3.10 localtest/test_tile_ledger.py
"""
import itertools
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "android", "app", "src", "main", "python"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import tile_ledger as TL  # noqa: E402


def brute_wall_values(idxs, unseen, standing, dingques):
    """穷举：把每型未现牌分配到 牌墙 + 各对手，返回每型可能的牌墙张数集合。

    约束：每型分配总量恰为 unseen[i]；对手 j 拿到的总张数恰为 standing[j]；
          对手 j 不得拿自己定缺门里的型（定缺是硬约束，声明后该门绝不上手）。
          字牌不属于任何定缺门（tile_suit 返回 None），谁都收——这条故意与实现里
          同一个边界对拍，防止 `dq != suit` 这类写法把 (None, None) 误判成不能拿。
    """
    suits = [TL.tile_suit(i) for i in idxs]
    n = len(idxs)
    out = [set() for _ in range(n)]
    allocs = 0
    for walls in itertools.product(*[range(u + 1) for u in unseen]):
        rest = [unseen[i] - walls[i] for i in range(n)]
        for g1 in itertools.product(*[range(r + 1) for r in rest]):
            if sum(g1) != standing[0]:
                continue
            g2 = [rest[i] - g1[i] for i in range(n)]
            if sum(g2) != standing[1]:
                continue
            bad = False
            for j, grp in enumerate((g1, g2)):
                dq = dingques[j]
                for i in range(n):
                    if grp[i] < 0:
                        bad = True
                        break
                    # 拿 0 张不算违规：只有真拿了定缺门的牌才不可行
                    if grp[i] > 0 and dq is not None and suits[i] == dq:
                        bad = True
                        break
                if bad:
                    break
            if bad:
                continue
            allocs += 1
            for i in range(n):
                out[i].add(walls[i])
    return out, allocs


class TestLedgerIdentities(unittest.TestCase):
    def _rand_case(self, rnd, n_types=27):
        hand = [0] * 34
        disc = [0] * 34
        meld = [0] * 34
        avail = list(range(n_types))
        for i in avail:
            hand[i] = rnd.randint(0, 4)
            rem = 4 - hand[i]
            disc[i] = rnd.randint(0, rem)
            meld[i] = rnd.randint(0, rem - disc[i])
        n_opp = rnd.choice([1, 2, 3])
        opps = [(rnd.randint(0, 4), rnd.choice([None, 0, 1, 2])) for _ in range(n_opp)]
        return hand, disc, meld, avail, opps

    def test_partition_identities_random(self):
        """恒等式：wall_lo+opp_hi==unseen、opp_lo+wall_hi==unseen、W==U−S。"""
        rnd = random.Random(20261003)
        for _ in range(400):
            hand, disc, meld, avail, opps = self._rand_case(rnd)
            led = TL.build_ledger(hand, disc, meld, available=avail, opponents=opps)
            U = sum(4 - (hand[i] + disc[i] + meld[i]) for i in avail)
            S = sum(TL.standing_hand_count(13, m) for m, _ in opps)
            self.assertEqual(led["unseen_total"], U)
            self.assertEqual(led["standing_total"], S)
            if U >= S:
                self.assertEqual(led["wall_remaining"], U - S)
            for i in avail:
                r = led["by_type"][i]
                self.assertEqual(r["wall_lo"] + r["opp_hi"], r["unseen"],
                                 f"idx{i} 拆分不守恒")
                self.assertEqual(r["opp_lo"] + r["wall_hi"], r["unseen"],
                                 f"idx{i} 上界拆分不守恒")
                self.assertLessEqual(r["wall_lo"], r["wall_hi"] + r["opp_lo"])

    def test_brute_force_soundness(self):
        """穷举全部合法分配，真实牌墙取值必须逐个落在账本区间内。

        牌集故意只用 3 个型（人造最小域，非真实玩法），目的是让穷举可在毫秒级跑完；
        账本不关心型数，所以这组断言对真实牌集同样成立。
        对手站立张数只能取 13−3m（副露数 m 为整数），否则构造本身不合法。
        """
        cases = [
            # (牌型真实索引, 每型未现张数, 两家站立张数, 两家的定缺门)
            ([0, 1, 9], [3, 3, 2], [1, 1], (0, None)),
            ([0, 9, 18], [4, 4, 3], [4, 1], (0, 1)),
            ([0, 9, 18], [0, 4, 4], [1, 1], (None, 0)),
            ([0, 9, 18], [2, 2, 2], [1, 1], (0, 1)),
            ([0, 1, 9], [1, 4, 3], [4, 1], (1, None)),
            ([0, 9, 18], [4, 4, 4], [4, 1], (0, 1)),
            ([0, 9, 18], [4, 4, 4], [4, 4], (0, 1)),
            ([0, 9, 18], [4, 4, 4], [7, 1], (0, 2)),
            ([0, 1, 9, 27], [2, 2, 2, 2], [4, 4], (2, None)),
            ([0, 9, 18], [3, 3, 3], [4, 4], (None, None)),
            ([0, 9, 18, 27], [3, 2, 4, 1], [4, 4], (2, 0)),
            # 字牌不属于任何定缺门：定缺万的两家仍然可能握着东风/红中
            ([0, 27, 28], [2, 3, 2], [1, 1], (0, 0)),
            ([9, 27, 33], [4, 2, 4], [4, 1], (None, None)),
        ]
        checked_allocs = 0
        for idxs, unseen, standing, dqs in cases:
            meld = [0] * 34
            for k, u in enumerate(unseen):
                meld[idxs[k]] = 4 - u          # 已现量直接由未现量反推
            opps = [((13 - s) // 3, dq) for s, dq in zip(standing, dqs)]
            for s, (m, _) in zip(standing, opps):
                self.assertEqual(13 - 3 * m, s, f"standing {s} 无法由副露数得到")
            led = TL.build_ledger([0] * 34, [0] * 34, meld,
                                  available=list(idxs), opponents=opps)
            self.assertTrue(led["ok"], f"构造局面被误判脏：{led['violations']}")
            vals, allocs = brute_wall_values(idxs, unseen, standing, dqs)
            self.assertGreater(allocs, 0, f"构造本身无可行分配，白测：{idxs} {unseen}")
            checked_allocs += allocs
            for k in range(len(idxs)):
                r = led["by_type"][idxs[k]]
                self.assertEqual(r["unseen"], unseen[k])
                for v in vals[k]:
                    self.assertGreaterEqual(
                        v, r["wall_lo"],
                        f"idx{idxs[k]} 真实牌墙 {v} 低于账本下界 {r['wall_lo']}（下界撕了谎）"
                        f" idxs={idxs} unseen={unseen} standing={standing} dq={dqs}")
                    self.assertLessEqual(
                        v, r["wall_hi"],
                        f"idx{idxs[k]} 真实牌墙 {v} 高于账本上界 {r['wall_hi']}（上界不够用）")
            # 全定缺时区间必须塌缩成确定值（这是「只能自摸」的依据）
            if dqs[0] is not None and dqs[0] == dqs[1]:
                for k in range(len(idxs)):
                    if TL.tile_suit(idxs[k]) == dqs[0]:
                        r = led["by_type"][idxs[k]]
                        self.assertEqual((r["wall_lo"], r["wall_hi"]), (r["unseen"], r["unseen"]),
                                         f"idx{idxs[k]} 两家都定缺该门，牌墙量应是确定值")
                        self.assertTrue(r["wall_only"])
                    else:
                        # 字牌不能被当成「握不住」：旧写法 dq != suit 在这里会错
                        self.assertGreater(
                            led["by_type"][idxs[k]]["opp_hi"], 0,
                            f"idx{idxs[k]} 不属于定缺门，对手容量不该为 0")
        # 低于 100 说明用例构造退化了（无解或只有一两个可行分配），等于空转
        self.assertGreater(checked_allocs, 100, "穷举没跑出足够合法分配，等于没验")
        print(f"[soundness] 穷举合法分配 {checked_allocs} 次全部通过")

    def test_dingque_suit_collapses_to_wall_only(self):
        """所有对手都定缺该门 → 该门每型 opp_hi=0，区间塌缩，且判定「只能自摸」。"""
        meld = [0] * 34
        meld[0] = 2          # 1m 已见 2 → 未现 2
        led = TL.build_ledger([0] * 34, [0] * 34, meld,
                              available=list(range(27)),
                              opponents=[(0, 0), (0, 0)])   # 两家都定缺万
        r = led["by_type"][0]
        self.assertEqual(r["opp_hi"], 0)
        self.assertEqual((r["wall_lo"], r["wall_hi"]), (r["unseen"], r["unseen"]),
                         f"两家都定缺万，1m 的牌墙量应是确定值：{r}")
        self.assertTrue(r["wall_only"])
        # 筒子门没人定缺 → 不塌缩
        self.assertEqual(led["by_type"][9]["unseen"], 4)
        self.assertGreater(led["by_type"][9]["opp_hi"], 0)

    def test_hands_capacity_respects_melds(self):
        """副露越多，对手可握牌越少 → 牌墙下界越高。"""
        meld = [0] * 34
        meld[5] = 1
        a = TL.build_ledger([0] * 34, [0] * 34, meld, available=list(range(27)),
                            opponents=[(0, None), (0, None)])
        b = TL.build_ledger([0] * 34, [0] * 34, meld, available=list(range(27)),
                            opponents=[(3, None), (3, None)])
        self.assertGreater(a["standing_total"], b["standing_total"])
        self.assertLess(a["wall_remaining"], b["wall_remaining"])
        # 牌墙变多 → 某型未现牌「至少在牌墙」的下界不可能变小
        self.assertLessEqual(a["by_type"][5]["wall_lo"], b["by_type"][5]["wall_lo"])

    def test_unavailable_tile_is_not_alive(self):
        """不在本玩法牌集里的型必须 total=0，绝不能报「还剩 4 张」。"""
        led = TL.build_ledger([0] * 34, [0] * 34, [0] * 34,
                              available=list(range(27)), opponents=[(0, None)])
        for i in range(27, 34):
            self.assertEqual(led["by_type"][i]["total"], 0)
            self.assertEqual(led["by_type"][i]["unseen"], 0)
            self.assertFalse(led["by_type"][i]["wall_only"])


class TestLedgerViolations(unittest.TestCase):
    def test_over_four_is_dirty(self):
        hand = [0] * 34
        disc = [0] * 34
        hand[33] = 2
        disc[33] = 3          # 红中共 5 张：物理不可能
        led = TL.build_ledger(hand, disc, [0] * 34,
                              available=list(range(27)) + [33], laizi=[33],
                              opponents=[(0, None)])
        self.assertFalse(led["ok"])
        kinds = {v["kind"] for v in led["violations"]}
        self.assertIn("over_four", kinds)
        self.assertTrue(any(v["tile"] == "7z" for v in led["violations"]))

    def test_negative_wall_is_dirty(self):
        # 未现只有 3 张，但两家满手共 26 张 → 牌墙为负
        meld = [4] * 34
        led = TL.build_ledger([0] * 34, [0] * 34, meld, available=list(range(34)),
                              opponents=[(0, None), (0, None)])
        self.assertFalse(led["ok"])
        self.assertIn("negative_wall", {v["kind"] for v in led["violations"]})

    def test_wall_overflow_is_dirty(self):
        """各型「只能进牌墙」的需求之和超过牌墙剩余 → 可见牌账本自相矛盾。"""
        # 牌集只有 3 型且全属万门（人造域），两家都定缺万 → 12 张未现全部只能待在牌墙，
        # 但牌墙只剩 12−2=10 张（有 2 张在两家手上）→ 物理上不可能，必须标脏。
        led = TL.build_ledger([0] * 34, [0] * 34, [0] * 34, available=[0, 1, 2],
                              opponents=[(4, 0), (4, 0)])
        self.assertEqual(led["unseen_total"], 12)
        self.assertEqual(led["standing_total"], 2)
        self.assertEqual(led["wall_remaining"], 10)
        self.assertFalse(led["ok"])
        self.assertIn("wall_overflow", {v["kind"] for v in led["violations"]})
        # 对照：牌集拉大到 27 型（108 张），同样两家定缺万是完全合法的，不得误报脏
        ok = TL.build_ledger([0] * 34, [0] * 34, [0] * 34, available=list(range(27)),
                             opponents=[(4, 0), (4, 0)])
        self.assertTrue(ok["ok"], f"合法局面被误判脏：{ok['violations']}")

    def test_hand_size_mismatch(self):
        hand = [0] * 34
        hand[0] = 4
        hand[1] = 4
        hand[2] = 4
        hand[3] = 4
        hand[4] = 3           # 19 张，不在任何玩法的合法站立张数里
        led = TL.build_ledger(hand, [0] * 34, [0] * 34, available=list(range(27)),
                              opponents=[(0, None)],
                              expected_hand_sizes=(14, 13, 12, 11, 10))
        self.assertFalse(led["ok"])
        self.assertIn("hand_size", {v["kind"] for v in led["violations"]})

    def test_hand_size_check_is_opt_in(self):
        """不声明合法张数就不卡：碰过两家的 7 张局面不能被误判脏。"""
        hand = [0] * 34
        for i in (0, 1, 2, 9, 10, 18, 19):
            hand[i] = 1
        no_claim = TL.build_ledger(hand, [0] * 34, [0] * 34, available=list(range(27)),
                                   opponents=[(0, None)])
        self.assertTrue(no_claim["ok"], no_claim["violations"])
        claimed = TL.build_ledger(hand, [0] * 34, [0] * 34, available=list(range(27)),
                                  opponents=[(0, None)],
                                  expected_hand_sizes=(14, 13, 12, 11, 10))
        self.assertIn("hand_size", {v["kind"] for v in claimed["violations"]})


class TestLedgerNoOpponentInfo(unittest.TestCase):
    def test_unknown_opponents_never_claim_wall_only(self):
        """无对手信息 = 未知，绝不能退化成「全定缺」而全局刷「只能自摸」。"""
        led = TL.build_ledger([0] * 34, [0] * 34, [0] * 34,
                              available=list(range(27)))
        self.assertFalse(led["opp_known"])
        for i, r in led["by_type"].items():
            self.assertFalse(r["wall_only"], f"idx{i} 在对手未知时被判成只能自摸")
            self.assertEqual(r["opp_hi"], r["unseen"], "未知时应不排斥对手握牌")
        txt = TL.describe_opportunity(led, [0, 1])
        self.assertIn("未知", txt)
        self.assertNotIn("只能自摸", txt)
        chance = TL.ting_chance(led, [0])
        self.assertEqual(chance["wall_only"], 0)
        self.assertFalse(chance["opp_known"])


class TestLedgerAfterDraw(unittest.TestCase):
    def _led(self):
        meld = [0] * 34
        meld[20] = 1                       # 3条已现 1 → 未现 3
        return TL.build_ledger([0] * 34, [0] * 34, meld, available=list(range(27)),
                               opponents=[(0, 0), (0, 0)], standings=[13, 13])

    def test_draw_moves_tile_from_wall_to_seen(self):
        led = self._led()
        before = led["by_type"][20]
        after = TL.ledger_after_draw(led, 20)["by_type"][20]
        self.assertEqual(after["seen"], before["seen"] + 1)
        self.assertEqual(after["unseen"], before["unseen"] - 1)
        self.assertEqual(after["wall_hi"], min(after["unseen"],
                                               led["wall_remaining"] - 1))
        # 恒等式在摸牌后仍成立（与 build_ledger 共用一份公式，不是重写一遍）
        self.assertEqual(after["wall_lo"] + after["opp_hi"], after["unseen"])
        self.assertEqual(after["opp_lo"] + after["wall_hi"], after["unseen"])
        self.assertEqual(TL.ledger_after_draw(led, 20)["unseen_total"],
                         led["unseen_total"] - 1)
        self.assertEqual(TL.ledger_after_draw(led, 20)["wall_remaining"],
                         led["wall_remaining"] - 1)

    def test_draw_of_dead_tile_is_noop(self):
        meld = [0] * 34
        for i in range(27):
            meld[i] = 4                    # 全见光：牌墙一张不剩
        dead = TL.build_ledger([0] * 34, [0] * 34, meld, available=list(range(27)),
                               opponents=[(0, None)], standings=[0])
        self.assertEqual(dead["unseen_total"], 0)
        self.assertEqual(dead["wall_remaining"], 0)
        same = TL.ledger_after_draw(dead, 5)
        self.assertIs(same, dead, "无牌可摸时必须原样返回，不能负转")

    def test_draw_of_wild_updates_wild_bucket(self):
        c28 = [0] * 28
        for i, n in ((0, 2), (1, 2), (2, 2), (3, 2), (9, 2), (10, 1), (18, 1), (27, 2)):
            c28[i] = n
        led = TL.ledger_for_sichuan(c28, available34=list(range(27)) + [33],
                                    laizi34=[33], opponents=[(0, None)])
        self.assertEqual(led["wild"]["seen"], 2)
        drew = TL.ledger_after_draw(led, 33)
        self.assertEqual(drew["wild"]["seen"], 3)
        self.assertEqual(drew["wild"]["unseen"], led["wild"]["unseen"] - 1)
        # 原账本不得被改动（副本语义，否则同一个 ledger 会被多个场景累加污染）
        self.assertEqual(led["wild"]["seen"], 2)


class TestLedgerBridges(unittest.TestCase):
    def test_index_mapping_matches_std_analyzer(self):
        """三处 mpsz 映射不许漂移：tile_ledger 与 std_analyzer 必须逐型一致。"""
        from std.std_analyzer import index_to_chinese, index_to_mpsz
        for i in range(34):
            self.assertEqual(TL.idx_to_mpsz(i), index_to_mpsz(i))
            self.assertEqual(TL.idx_to_chinese(i), index_to_chinese(i))

    def test_sichuan_bridge_puts_red_dragon_at_33(self):
        c28 = [0] * 28
        c28[27] = 3            # 手里的红中
        c28[10] = 2            # 2筒
        led = TL.ledger_for_sichuan(
            c28, [0] * 34, [0] * 34,
            available34=list(range(27)) + [33], laizi34=[33],
            opponents=[(0, None)])
        self.assertEqual(led["by_type"][33]["seen"], 3)
        self.assertEqual(led["by_type"][33]["unseen"], 1)
        self.assertTrue(led["by_type"][33]["is_wild"])
        self.assertEqual(led["by_type"][10]["seen"], 2)
        # 牌集应含红中，不含风牌
        self.assertIn(33, led["available"])
        self.assertNotIn(28, led["available"])
        self.assertEqual(led["wild"]["seen"], 3)
        self.assertEqual(led["wild"]["unseen"], 1)

    def test_sichuan_bridge_declared_tileset_is_not_guessed(self):
        """牌集必须声明：available34 不给就报错，红中不在牌集时必须标脏而非静默消失。"""
        c28 = [0] * 28
        # 13 张合法手牌（含 1 张红中），避开 hand_size 校验的干扰，只考牌集这一条
        for i, n in ((0, 2), (1, 2), (2, 2), (3, 2), (9, 2), (10, 1), (18, 1), (27, 1)):
            c28[i] = n
        with self.assertRaises(TypeError):
            TL.ledger_for_sichuan(c28)          # 漏声明牌集 → 立即失败，不猜默认
        # 纯川麻 sc_xz（27 型，无字牌）却看到红中 → 越界脏帧，不能算成「不存在」
        led = TL.ledger_for_sichuan(c28, available34=list(range(27)),
                                    opponents=[(0, None)])
        self.assertFalse(led["ok"])
        self.assertIn("over_four", {v["kind"] for v in led["violations"]})
        # 反过来：声明了含红中的牌集，同一手牌必须干净，112 张实物减去已见的 13 张
        clean = TL.ledger_for_sichuan(c28, available34=list(range(27)) + [33],
                                      laizi34=[33], opponents=[(0, None)])
        self.assertTrue(clean["ok"], clean["violations"])
        self.assertEqual(clean["unseen_total"], 28 * 4 - 13)

    def test_describe_opportunity_numbers_traceable(self):
        """文案里出现的每个数字都必须能在账本里指回来源。"""
        meld = [0] * 34
        meld[20] = 1           # 3条已现 1 → 未现 3
        meld[11] = 2           # 3筒已现 2 → 未现 2
        led = TL.build_ledger([0] * 34, [0] * 34, meld, available=list(range(27)),
                              opponents=[(0, 1), (0, 1)])   # 两家都定缺筒
        txt = TL.describe_opportunity(led, [20, 11])
        self.assertIn("5", txt)          # 3 + 2 = 共余 5 张
        self.assertIn("3条", txt)
        self.assertIn("3筒", txt)
        U = led["by_type"][20]["unseen"] + led["by_type"][11]["unseen"]
        self.assertEqual(U, 5)
        # 筒子门两家都定缺 → 那 2 张只能自摸
        self.assertEqual(led["by_type"][11]["opp_hi"], 0)
        self.assertIn("只能", txt)
        # 牌墙轮数必须与账本一致，不许文案自造数字
        self.assertIn(str(led["rounds_left"]), txt)

    def test_describe_opportunity_empty_when_no_waits(self):
        led = TL.build_ledger([0] * 34, [0] * 34, [0] * 34, available=list(range(27)))
        self.assertEqual(TL.describe_opportunity(led, []), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
