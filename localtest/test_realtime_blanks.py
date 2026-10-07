# -*- coding: utf-8 -*-
"""B-P4「实时决策空白」补全回归：面板有指标，也必须有一条能照着做的行动指引。

四项空白各挡一种"看着实现了其实没接上"的坏法：

A 相对优势排序 — 每条候选必须说清"为什么排在次选前面"。
  专门禁掉「优于中位打法 1.2 分」这类伪量纲：`ev` 是合成评分（实测同帧候选差
  1000/20/9 这种量级），把它印成小数就是 B-P3 刚拆掉的同类病。
B 纯解析式评估的行动映射 — `pureAnalytical` 时 gauge 不得再画进度条、不得报分值。
  这道门只能靠 Dart 源码文本守（本机无 Flutter SDK），所以按语句/位置断言，
  而不是"文件里含有某字符串"（旧进度条留在 else 分支里也必须能被位置检查发现）。
C 危险分级执行建议 — 中间档（medium/low）也得有颜色 + 一句话 + 替代方案。
  反向也要守：主推本来就安全时不得给出"建议优先选低危出张"（误报会把用户
  推去改一个不需要改的决定）。
D 摸牌预演 — 只允许引用引擎真的跑过的摸牌情景；14 张帧与 std 家族一个字符
  都不许渲染。变异检验里把 `predraw` 摘掉再补一行"若摸到"就算造假。

运行：py -3.10 localtest/test_realtime_blanks.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

import probability_bands as pb  # noqa: E402
from discards_tiebreak import advantage_note, first_diff_layer  # noqa: E402
from engine.engine import DECISION_KEYS, annotate_advice_decisions  # noqa: E402
from test_discards_tiebreak import item as tb_item  # noqa: E402
from test_equity_honesty import DART, _dart_body, _dart_strip  # noqa: E402
from test_tile_ledger_e2e import make_engine, make_image, run_frames  # noqa: E402
from trainer.utils.convert import tile_to_chinese  # noqa: E402

PCT = re.compile(r"\d\s*%")
# 「优 1.2 分」：ev 没有量纲，出现带小数的"分"就是把合成评分当测量值卖
FAKE_SCORE = re.compile(r"\d+\.\d+\s*分")

# ---- 真帧（由 build/_p4_probe2.py 扫出来，覆盖四项空白各自的适用区）----
# 13 张川麻：走预摸路径，且确实有摸牌情景会改掉主推（空白 D 的唯一真数据源）
FLIP_HAND = ["1m", "1m", "2m", "3m", "5m", "6m", "7m", "9m", "1p", "4p", "4p", "7s", "8s"]
FLIP_RIVER = ["1s", "2s", "9p"]
# 13 张川麻（听牌形）：预摸全都不改主推 → 只有"仍打"行，没有 flip 行
CALM_HAND = ["3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "5p", "5p", "5p"]
CALM_RIVER = ["1s", "2s", "9p"]
# 14 张川麻 + 三家副露：主推落到 medium，且候选里存在安全牌（空白 C 的适用区）
PRESS_HAND = ["1m", "9m", "1p", "9p", "1s", "9s", "3m", "4m", "5m", "6m", "7p", "8p", "9p", "5s"]
PRESS_RIVER = ["2m", "6p", "8s", "1s", "9m", "4p"]
PRESS_MELDS = ["5m", "6m", "7m", "1p", "2p", "3p", "7s", "8s", "9s"]
# std：既没有 danger_flow 也没有预摸路径，还有 ev_gauge——三项空白在这里都必须沉默
STD_HAND = ["3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "5p", "5p", "5p", "7s"]
STD_RIVER = ["1s", "2s"]
# A-P2 同分裁决专用帧（build/_p2_probe_ties.py 实测：24 帧里 12 帧含 EV 全等候选对，
# 本帧一次同时命中两类场景：进张数分出优劣的，以及只剩索引兜底的真正等价）
TIE_HAND = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
            "1p", "1p", "1p", "9s", "9s"]
TIE_RIVER = ["5z"]


def _read(path):
    """读源码文本（面板/引擎两边都只看当前磁盘上的内容，不起子进程）。"""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _payload(mode, hand, river, melds=None, seed=5):
    eng = make_engine(mode, hand, river, meld_labels=melds)
    return run_frames(eng, make_image(seed=seed), 8)[-1]


def _by_rank(advice):
    return sorted(advice, key=lambda a: a.get("rank") or 999)


class TestBlankAAdvantageReason(unittest.TestCase):
    """空白 A：非听牌/听牌态都要给出"比次选好在哪"，且只说可核对的事实。"""

    @classmethod
    def setUpClass(cls):
        cls.frames = {
            "sc13_flip": _payload("sc_xz", FLIP_HAND, FLIP_RIVER),
            "sc14_press": _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11),
            "std14": _payload("std_tdh", STD_HAND, STD_RIVER, seed=67),
        }

    def test_rank_is_contiguous_and_starts_at_best(self):
        for name, pl in self.frames.items():
            adv = _by_rank(pl.get("advice") or [])
            self.assertTrue(adv, f"{name} 必须有候选")
            self.assertEqual([a.get("rank") for a in adv], list(range(1, len(adv) + 1)),
                             f"{name} 名次必须 1..n 连续（面板按名次配文案）")
            self.assertEqual(adv[0].get("rank_total"), len(adv), f"{name} 总数不对")
            if pl.get("best"):
                self.assertEqual(adv[0].get("tile"), pl["best"],
                                 f"{name}：rank1 必须与面板主推同源，否则文案与推荐牌分叉")

    def test_every_non_last_advice_explains_the_next_one(self):
        for name, pl in self.frames.items():
            adv = _by_rank(pl.get("advice") or [])
            for a, nxt in zip(adv, adv[1:]):
                reason = a.get("advantage_reason")
                self.assertIsInstance(reason, str, f"{name} rank{a.get('rank')} 缺 advantage_reason")
                m = re.match(r"^(?:优于次选 (\S+)|与次选 (\S+) 等价)：(.+)$", reason)
                self.assertIsNotNone(m, f"{name} 文案不合口径：{reason!r}")
                tb = (nxt.get("tile") or "")
                self.assertIn(tb, reason,
                              f"{name}：说了优势却没提真正被比掉的那张 {tb}｜{reason}")
                self.assertTrue(m.group(3).strip(), f"{name} 理由为空：{reason}")

    def test_last_advice_never_claims_an_advantage(self):
        """末位没有次选可比：留白是对的，写一句就是凭空造优势（也是脏缓存的信号）。"""
        for name, pl in self.frames.items():
            adv = _by_rank(pl.get("advice") or [])
            self.assertNotIn("advantage_reason", adv[-1],
                             f"{name} 末位仍带上一帧的优势文案：{adv[-1].get('advantage_reason')}")

    def test_reasons_carry_no_pseudo_dimension(self):
        for name, pl in self.frames.items():
            for a in pl.get("advice") or []:
                for text in (a.get("advantage_reason") or "",
                             (a.get("advantage") or {}).get("note") or ""):
                    self.assertIsNone(PCT.search(text), f"{name} 优势文案带百分比：{text}")
                    self.assertIsNone(FAKE_SCORE.search(text),
                                      f"{name} 把合成评分写成带小数的「分」：{text}")
                    self.assertNotIn("胜率", text, f"{name}：{text}")

    def test_fact_numbers_are_recomputable(self):
        """说「少 1 向听」「上界多 6 张」就得能在候选字段上对上号——数字对不上就是假解释。"""
        checked = 0
        for name, pl in self.frames.items():
            adv = _by_rank(pl.get("advice") or [])
            for a, b in zip(adv, adv[1:]):
                note = (a.get("advantage") or {})
                if note.get("kind") != "fact" or note.get("equivalent"):
                    continue
                text = note["note"]
                m = re.search(r"少 (\d+) 向听（(\d+) vs (\d+)）", text)
                if m:
                    self.assertEqual(int(m.group(2)), int(a["shanten"]), f"{name}：{text}")
                    self.assertEqual(int(m.group(3)), int(b["shanten"]), f"{name}：{text}")
                    self.assertEqual(int(m.group(1)),
                                     int(b["shanten"]) - int(a["shanten"]), f"{name}：{text}")
                    checked += 1
                    continue
                m = re.search(r"未现进张上界多 (\d+) 张（(\d+) vs (\d+)）", text)
                if m:
                    self.assertEqual(int(m.group(2)), int(a["ukeire"]), f"{name}：{text}")
                    self.assertEqual(int(m.group(3)), int(b["ukeire"]), f"{name}：{text}")
                    self.assertEqual(int(m.group(1)),
                                     int(a["ukeire"]) - int(b["ukeire"]), f"{name}：{text}")
                    checked += 1
        self.assertGreaterEqual(checked, 2,
                                "没有任何一条事实差额被真正核对：本用例已空转")

    def test_engine_does_not_invent_a_reason_when_it_has_none(self):
        """说不出事实就承认是评分/等价：全落"事实"反而说明有人在编理由。"""
        kinds, layers = set(), set()
        for pl in self.frames.values():
            for a in pl.get("advice") or []:
                note = a.get("advantage") or {}
                if note:
                    kinds.add(note.get("kind"))
                    layers.add(note.get("layer"))
        self.assertIn("model", kinds, f"优势文案没有一条承认是未标定评分：{kinds}")
        self.assertIn("fact", kinds, f"优势文案没有一条给出可核对事实：{kinds}")
        self.assertGreaterEqual(len(layers), 2, f"决胜层只出现一层，其余层永不生效：{layers}")


class TestAdvantageNoteLayerMutation(unittest.TestCase):
    """决胜链每一层都得产出各自的文案（层写了但轮不到 = 装饰）。"""

    def _note(self, a, b, layer):
        self.assertEqual(first_diff_layer(a, b), layer,
                         msg=f"本应由「{layer}」分开，实际差异在更早的层")
        n = advantage_note(a, b)
        self.assertEqual(n["layer"], layer)
        self.assertNotIn("模型综合评分更高", n["note"],
                         f"{layer} 有明确事实，却落回了兜底文案：{n['note']}")
        return n

    def test_each_layer_has_its_own_words(self):
        expect = {
            "进张数": "未现进张上界多",
            "听口面数": "叫口多",
            "定缺门": "先断缺门",
            "牌墙可摸": "牌墙确实还能摸到",
            "防守安全": "点炮",
            "牌面弹性": "改良空间小",
        }
        base = tb_item("5m")
        cases = {
            "进张数": (tb_item("5m", ukeire=8), tb_item("6m", ukeire=4)),
            "听口面数": (tb_item("5m", waits=["1m", "4m"]), tb_item("6m", waits=["1m"])),
            "定缺门": (tb_item("5m", dingque=True), tb_item("6m")),
            "牌墙可摸": (tb_item("5m", wall_lo=6), tb_item("6m", wall_lo=1)),
            "防守安全": (tb_item("5m", danger=0.01), tb_item("6m", danger=0.2)),
            # 只有牌面弹性层：同 ev/进张/叫口/危险，字牌 vs 中张
            "牌面弹性": (tb_item("5m"), tb_item("6m", ukeire=base["ukeire"])),
        }
        seen = set()
        for layer, (a, b) in cases.items():
            if layer == "牌面弹性":
                # 1m（幺九）与 5m（中张）在所有上层都持平时，才轮到弹性层
                a, b = tb_item("1m"), tb_item("5m")
            n = self._note(a, b, layer)
            self.assertIn(expect[layer], n["note"], f"{layer} 文案不对：{n['note']}")
            seen.add(layer)
        self.assertEqual(seen, set(expect), "有层没被测到")

    def test_full_equivalence_is_admitted_not_spun(self):
        a, b = tb_item("5m"), tb_item("5m")
        self.assertEqual(first_diff_layer(a, b), "完全同序")
        n = advantage_note(a, b)
        self.assertTrue(n["equivalent"])
        # note 本身要把“全同”说清；“等价”这个词由 equivalent 位与调用方前缀负责，
        # 不在 note 里重复一次（面板只有一行宽）。
        self.assertIn("打哪张都不亏", n["note"])

    def test_index_fallback_says_so(self):
        a, b = tb_item("1m"), tb_item("9m")   # 同为幺九，前 7 层全平，只剩索引兜底
        self.assertEqual(first_diff_layer(a, b), "索引兜底")
        n = advantage_note(a, b)
        self.assertTrue(n["equivalent"], "仅靠枚举顺序列前，不能包装成牌理优势")
        self.assertIn("无牌理依据", n["note"])

    def test_lower_ev_but_first_is_not_called_higher_score(self):
        """rank 不是评分给的（阶段固定次序/取首规则），文案就不许说"评分更高"。"""
        a = tb_item("9m", ev=-1995.0)
        b = tb_item("2m", ev=-1975.0)
        n = advantage_note(a, b)
        self.assertEqual(n["layer"], "EV")
        self.assertIn("不是因为有评分优势", n["note"])


class TestBlankBHonestGauge(unittest.TestCase):
    """空白 B：未标定 + 纯解析式 → 面板降级为文本，不画条、不报分。"""

    @classmethod
    def setUpClass(cls):
        cls.gauge = _payload("sc_xz", CALM_HAND, CALM_RIVER, seed=3)["ev_gauge"]
        cls.src = _read(DART)
        cls.body = _dart_body(cls.src, "Widget _buildWinEquityGaugeWidget(")

    def test_gauge_carries_tier_and_no_percentage(self):
        tables = pb.band_tables()
        self.assertFalse(self.gauge["calibrated"], "未标定态是本组断言的前提")
        self.assertEqual(self.gauge["equity_basis"], "analytical")
        self.assertIn(self.gauge["tier"], set(tables["tier"].values()))
        self.assertEqual(self.gauge["tier"], pb.coarse_tier(self.gauge["level"]))
        # badge 就是面板渲染门上的那颗章：改成三档档位词，且不得带数字
        self.assertEqual(self.gauge["badge"], self.gauge["tier"])
        for k in ("tier", "badge", "band"):
            self.assertIsNone(PCT.search(str(self.gauge[k])), f"{k} 里出现了百分比")
        self.assertEqual({pb.coarse_tier(l) for l in tables["equity"]},
                         {"偏优", "中性", "偏劣"}, "三档投影必须恰好是三档")

    def test_std_frame_still_downlinks_no_gauge(self):
        self.assertIsNone(_payload("std_tdh", STD_HAND, STD_RIVER, seed=67)["ev_gauge"],
                          "std 没有胜率模型，不得凭空补一个仪表盘（P3 结论不得回退）")

    def test_dart_progress_bar_only_in_the_non_degraded_branch(self):
        body = self.body
        self.assertIn("if (degrade)", body, "gauge 没有降级分支：进度条又在冒充测量结果")
        gate = body.index("if (degrade)")
        else_at = body.index("else", gate)
        # 进度条只准出现在 else 之后（整块代码只留一处 LinearProgressIndicator）
        self.assertEqual(body.count("LinearProgressIndicator"), 1,
                         "面板里又多了第二个进度条")
        self.assertGreater(body.index("LinearProgressIndicator"), else_at,
                           "未标定态仍在渲染进度条")
        self.assertIn("degradeLabel", body[gate:else_at], "降级分支没显示文本标签")

    def test_dart_hides_numeric_ev_when_degraded(self):
        body = self.body
        self.assertIn("if (!degrade)", body, "净收益分值没有降级守卫：未标定态又在报分")
        self.assertGreater(body.index("netEv.toStringAsFixed"), body.index("if (!degrade)"),
                           "分值渲染跑到了降级守卫之前")

    def test_dart_declares_pure_analytical_label(self):
        # 「纯解析式评估」这句话必须走判据，写死在某个无条件分支里就是假标签
        segs = [s for s in self.body.split(";") if "纯解析式评估" in s]
        self.assertTrue(segs, "面板已不再声明纯解析式评估")
        for s in segs:
            self.assertIn("pureAnalytical", s, msg="标签必须由判据驱动：" + " ".join(s.split())[:120])

    def test_dart_degrade_gate_is_driven_by_the_purity_judgement(self):
        """降级开关必须接在「无模型」判据上，而不是写死的常量。

        这是规约里“把 pureAnalytical 守卫去掉→测试应变红”的那道门：
        只看源码里还有没有 `if (degrade)` 是挡不住 `degrade = false` 的。
        """
        m = re.search(r"final bool degrade = ([^;]+);", self.body)
        self.assertIsNotNone(m, "降级判据行已不存在")
        expr = " ".join(m.group(1).split())
        self.assertIn("pureAnalytical", expr, f"降级不再由纯解析式判据驱动：{expr}")
        self.assertIn("calibrated", expr, f"降级丢掉标定态判据：{expr}")
        self.assertNotIn("false", expr.lower(), f"降级被写死成常量：{expr}")
        # 标签里的牌势词也必须来自 tier（写死一个档位名就是假信息）
        lab = re.search(r"final String degradeLabel = ([^;]+);", self.body)
        self.assertIsNotNone(lab, "降级标签行已不存在")
        self.assertIn("$tier", lab.group(1), "降级标签没拼上档位词")

    def test_dart_tier_fallback_matches_python_table(self):
        tables = pb.band_tables()
        body = _dart_body(self.src, "String _equityTierWord(String level)")
        got = dict(re.findall(r"case '([a-z_]+)':\s+return '([^']+)';", body))
        self.assertTrue(got, "_equityTierWord 没抽到档位：正则失效，本测试空转")
        self.assertEqual(got, tables["tier"], "Dart 三档兜底词与 probability_bands 已分叉")
        self.assertIn(tables["unknown"], body, "认不出的 level 必须兜到「未定档」，不能猜一档")
        self.assertIn("evGauge['tier']", self.src, "面板没读 payload 的 tier，兜底将永久生效")


class TestBlankCDangerGuidance(unittest.TestCase):
    """空白 C：每一档危险度都给一句行动指令；主推已安全时不得误报。"""

    @classmethod
    def setUpClass(cls):
        cls.press = _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11)
        cls.calm = _payload("sc_xz", CALM_HAND, CALM_RIVER, seed=3)
        cls.std = _payload("std_tdh", STD_HAND, STD_RIVER, seed=67)
        cls.src = _read(DART)

    def test_medium_top_gets_actionable_hint_and_alternatives(self):
        top = _by_rank(self.press["advice"])[0]
        level = (top.get("danger_flow") or {})["danger_level"]
        self.assertGreaterEqual(pb.danger_rank(level), pb.danger_rank("medium"),
                                f"本帧前提：主推应为中危及以上，实际 {level}")
        self.assertEqual(top["danger_hint"], pb.danger_advice(level),
                         "行动指令必须与单一来源逐字一致")
        alts = top.get("safer_alternatives")
        self.assertIsInstance(alts, list, "中危主推必须给替代方案，否则用户只能干看着")
        self.assertTrue(alts, f"候选里有安全牌却没列出：{[a.get('tile') for a in self.press['advice']]}")
        safe = set(pb.DANGER_SAFE_LEVELS)
        ranks = [pb.danger_rank(a["danger_level"]) for a in alts]
        for a in alts:
            self.assertIn(a["danger_level"], safe, f"替代方案给了不放心的牌：{a}")
            self.assertEqual(a["danger_band"], pb.danger_band(a["danger_level"]))
            self.assertIn(a["tile"], [x["tile"] for x in self.press["advice"][1:]],
                          f"替代牌 {a['tile']} 不是本帧真实候选")
        self.assertEqual(ranks, sorted(ranks), "替代方案必须按危险序排（走 DANGER_LEVEL_ORDER）")
        self.assertLessEqual(len(alts), 3, "面板只放三张，多了是噪声")

    def test_safe_top_is_not_bumped_into_defense(self):
        """主推本来就安全时不给替代方案：那会让用户去改一个不需要改的决定。"""
        for name, pl in (("sc13_calm", self.calm), ("std14", self.std)):
            adv = _by_rank(pl["advice"])
            top = adv[0]
            level = (top.get("danger_flow") or {}).get("danger_level")
            if level is None or pb.danger_rank(level) < pb.danger_rank("medium"):
                self.assertNotIn("safer_alternatives", top,
                                 f"{name}：主推 {top['tile']}（{level}）不该被提示换牌")

    def test_hint_is_per_item_and_worded_from_the_table(self):
        for a in self.press["advice"]:
            flow = a.get("danger_flow") or {}
            self.assertEqual(a.get("danger_hint"),
                             pb.danger_advice(flow.get("danger_level") or ""))
        # std 没有危险模型：一句"轻微风险"都不能编（kind 缺失比假档位更诚实）
        for a in self.std["advice"]:
            self.assertNotIn("danger_hint", a, f"std 帧伪造了危险指令：{a['tile']}")

    def test_dart_color_covers_every_level(self):
        body = _dart_body(self.src, "Color _dangerHintColor(String level)")
        got = set(re.findall(r"case '([a-z_]+)':", body))
        self.assertTrue(got, "配色函数没抽到任何档位：正则失效")
        self.assertEqual(got, set(pb.DANGER_LEVEL_ORDER),
                         f"有档位没配色，medium 又会掉回默认色：{got}")
        card = _dart_body(self.src, "Widget _adviceSection(")
        self.assertIn("top['danger_hint']", card, "面板没读 danger_hint，Python 侧文案全废")
        self.assertIn("_dangerHintColor(dangerLevel)", card, "提示语必须按档位配色")
        self.assertIn("top['safer_alternatives']", card, "面板没接替代方案")
        self.assertIn("if (saferAlts.isNotEmpty)", card, "替代方案必须有无数据守卫")

    def test_dart_hint_fallback_matches_python_table(self):
        tables = pb.band_tables()
        body = _dart_body(self.src, "String _dangerHintWord(String level)")
        got = dict(re.findall(r"case '([a-z_]+)':\s+return '([^']+)';", body))
        self.assertTrue(got, "_dangerHintWord 没抽到档位：正则失效")
        self.assertEqual(got, tables["danger_advice"], "Dart 兜底行动指令与 Python 已分叉")

    def test_dart_colors_actually_differ_by_level(self):
        """分级不能只在 case 名单里分级：五档得拿到五种颜色，越危越红。

        只断言「case 存在」的话，把 medium 改成与 low 同一灰色也永远绿——
        而那正好就是空白 C 要修的病（中间档看上去与 safe 无异）。
        """
        body = _dart_body(self.src, "Color _dangerHintColor(String level)")
        pairs = re.findall(r"case '([a-z_]+)':\s+return const Color\(0x([0-9A-Fa-f]{8})\);",
                           body)
        got = {k: v for k, v in pairs}
        self.assertEqual(set(got), set(pb.DANGER_LEVEL_ORDER), f"配色档位不全覆盖：{got}")
        self.assertEqual(len(set(got.values())), len(got), "有两档共用一个颜色：分级在屏幕上不存在")

        def rgb(hexv):
            return tuple(int(hexv[2 + i:4 + i], 16) for i in (0, 2, 4))

        # 低危侧不得是红系（R>G），中危及以上必须进红系（R>G）且绿分量逐档下降
        for lvl in ("safe", "low"):
            r, g, _b = rgb(got[lvl])
            self.assertLessEqual(r, g, f"{lvl} 用了红系颜色：{got[lvl]}")
        greens = [rgb(got[l])[1] for l in ("medium", "high", "critical")]
        self.assertEqual(greens, sorted(greens, reverse=True),
                         f"越危应当越红：{dict(zip(('medium', 'high', 'critical'), greens))}")
        for l in ("medium", "high", "critical"):
            r, g, _b = rgb(got[l])
            self.assertGreater(r, g, f"{l} 没进红系：{got[l]}")

    def test_every_new_panel_row_is_behind_a_data_guard(self):
        """B-P4 新增的五行面板文案都得有非空守卫：没数据时不得渲染空壳或假句。"""
        card = _dart_body(self.src, "Widget _adviceSection(")
        for var in ("advantageReason", "dangerHint", "predrawLine", "predrawFlipLine"):
            self.assertIn(f"if ({var} != null && {var}.isNotEmpty) ...[", card,
                          f"{var} 行没有非空守卫")
        self.assertIn("if (saferAlts.isNotEmpty) ...[", card, "替代方案行没有非空守卫")
        # 字段只能从 payload 来：面板自己拼「优于次选」就是第二套口径
        for key in ("advantage_reason", "danger_hint", "safer_alternatives",
                    "predraw_line", "predraw_flip_line", "rank", "rank_total"):
            self.assertIn(f"top['{key}']", card, f"面板没接 payload 的 {key}")


class TestBlankDPredraw(unittest.TestCase):
    """空白 D：摸牌预演只许引用真跑过的模拟，没跑过就整行沉默。"""

    @classmethod
    def setUpClass(cls):
        cls.flip = _payload("sc_xz", FLIP_HAND, FLIP_RIVER)
        cls.calm = _payload("sc_xz", CALM_HAND, CALM_RIVER)
        cls.press14 = _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11)
        cls.std = _payload("std_tdh", STD_HAND, STD_RIVER, seed=67)
        cls.src = _read(DART)

    def test_flip_frame_reports_real_draw_scenarios(self):
        top = _by_rank(self.flip["advice"])[0]
        raw = top["predraw"]
        scen = raw["scenarios"]
        self.assertLessEqual(len(scen), 6, "预摸只做前 6 种高概率摸牌，多了是另算的账")
        self.assertEqual(raw["simulated_kinds"], len(scen))
        self.assertGreaterEqual(raw["considered_kinds"], len(scen),
                                "参与模拟的种类数不得大于总可联络种类数")
        cur = top["tile"]
        kept = sum(1 for row in scen if row[2] == cur)
        self.assertEqual(top["predraw_kept"], kept, f"仍打数与实际情景不符：{scen}")
        self.assertIn(f"预演 {len(scen)} 种摸牌：{kept} 种仍打 "
                      f"{tile_to_chinese(cur)}", top["predraw_line"])
        omit = raw["considered_kinds"] - len(scen)
        if omit > 0:
            self.assertIn(f"另有 {omit} 种可联络摸牌未参与模拟", top["predraw_line"],
                          "不写这句，用户会把 6 种当成全部可能")

    def test_flip_line_matches_the_simulated_group(self):
        top = _by_rank(self.flip["advice"])[0]
        line = top["predraw_flip_line"]
        flips = top["predraw_flips"]
        scen = top["predraw"]["scenarios"]
        self.assertTrue(flips, "本帧前提：必须有会改主推的摸牌情景")
        self.assertTrue(line.startswith("若摸到 "), line)
        head, _, tail = line.partition(" 将改打 ")
        target = flips[0]["to"]
        self.assertEqual(tail.split("（")[0].strip(), tile_to_chinese(target), line)
        tokens = head.replace("若摸到 ", "", 1).split("、")
        self.assertTrue(tokens, f"文案里没有摸牌：{line}")
        self.assertLessEqual(len(tokens), 3, f"一条改法最多列 3 张摸牌：{line}")
        for tok in tokens:
            m = re.match(r"^(.+?)·余(\d+)张$", tok)
            self.assertIsNotNone(m, f"摸牌写法不合口径：{tok}")
            name, rem = m.group(1), int(m.group(2))
            idx = [i for i, row in enumerate(scen) if tile_to_chinese(row[0]) == name]
            self.assertTrue(idx, f"文案里出现了没模拟过的摸牌：{name}")
            self.assertEqual(scen[idx[0]][2], target, f"{name} 的改打结果与文案不一致")
            self.assertEqual(scen[idx[0]][1], rem, f"{name} 的余数与账本不符")
        # 改法分组不能与情景表自相矛盾：每行要么“仍打”要么属于某个改法
        self.assertEqual(sum(len(g["draws"]) for g in flips),
                         top["predraw_simulated"] - top["predraw_kept"],
                         f"改法分组与情景表对不上：{flips}")

    def test_no_flip_frame_omits_the_flip_line(self):
        top = _by_rank(self.calm["advice"])[0]
        self.assertIn("predraw_line", top, "13 张帧必须有预演行（本帧前提）")
        self.assertNotIn("predraw_flip_line", top,
                         "所有情景都不改主推，却写了一句「若摸到…将改打…」")

    def test_frames_without_simulation_data_stay_silent(self):
        for name, pl in (("sc14", self.press14), ("std14", self.std)):
            for a in pl["advice"]:
                for k in ("predraw", "predraw_line", "predraw_flip_line", "predraw_flips"):
                    self.assertNotIn(k, a, f"{name} 的 {a.get('tile')} 凭空带了预演字段 {k}")
            self.assertEqual(json.dumps(pl).count("predraw_line"), 0,
                             f"{name} 整帧不得出现预演文案")

    def test_dart_renders_predraw_only_behind_a_null_guard(self):
        card = _dart_body(self.src, "Widget _adviceSection(")
        self.assertIn("top['predraw_line']", card, "面板没接 predraw_line")
        self.assertIn("if (predrawLine != null && predrawLine.isNotEmpty)", card,
                      "预演行没有非空守卫：无数据时会渲染成空壳或假文案")
        self.assertNotIn("若摸到", card,
                         "面板里写死了「若摸到」：没有模拟数据时它也会显示出来")

    def test_stale_predraw_is_scrubbed_before_rewriting(self):
        """缓存里的 dict 会被下一帧复用：不清掉派生字段就是把上一帧的话当本帧结论。"""
        items = _json_round(_by_rank(self.flip["advice"]))
        stats = annotate_advice_decisions(items, items[0]["tile"])
        self.assertTrue(items[0].get("predraw_line"), f"前提：{stats}")
        for it in items:
            it.pop("predraw", None)                      # 模拟数据没了（14 张帧/降级）
        stats = annotate_advice_decisions(items, items[0]["tile"])
        self.assertEqual(stats["predraw"], 0)
        for it in items:
            for k in DECISION_KEYS:
                if not k.startswith("predraw"):
                    continue
                self.assertNotIn(k, it, f"上一帧的预演字段 {k} 没被清掉：那是伪造")

    def test_candidates_changed_so_old_ranking_is_gone(self):
        """候选变少时，末位不得留着上一帧「优于次选 X」的句子。"""
        items = _json_round(_by_rank(self.calm["advice"]))
        annotate_advice_decisions(items, items[0]["tile"])
        self.assertTrue(items[0].get("advantage_reason"))
        short = items[:1]
        annotate_advice_decisions(short, short[0]["tile"])
        self.assertNotIn("advantage_reason", short[0],
                         f"现在只有一条候选，仍写着：{short[0].get('advantage_reason')}")


class TestDualStrategyCompare(unittest.TestCase):
    """空白 A 的另一半：双策略卡（稳胡极速流/大番收益流）也必须说得出与主推的差别。

    它们走的是另一套主键（向听/进张 vs EV），不补这一句就是「两张卡各自报一个牌」。
    措辞只声明事实（排名更前/等价）：大番流的 EV 常常高于主推，写「主推比它优」
    就是假话；而「主推排名更前」是引擎顺序的事实，不会被注文案推翻。
    """

    KEYS = ("fast_advice", "big_advice")

    @classmethod
    def setUpClass(cls):
        cls.frames = {
            "sc13": _payload("sc_xz", FLIP_HAND, FLIP_RIVER),
            "sc14": _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11),
            "std14": _payload("std_tdh", STD_HAND, STD_RIVER, seed=67),
        }

    def _alts(self, name):
        pl = self.frames[name]
        out = []
        for k in self.KEYS:
            v = pl.get(k)
            if isinstance(v, dict) and v.get("tile"):
                out.append((k, v))
        return out

    def test_alternative_says_the_same_kind_of_sentence(self):
        seen = 0
        for name in self.frames:
            for k, alt in self._alts(name):
                best = self.frames[name].get("best")
                if not best or alt.get("tile") == best:
                    self.assertNotIn("advantage_reason", alt,
                                     f"{name}.{k} 与主推同一张牌，没得比却写了比较")
                    self.assertNotIn("main_compare", alt, f"{name}.{k} 同上")
                    continue
                reason = alt.get("advantage_reason")
                self.assertIsInstance(reason, str,
                                      f"{name}.{k} 与本卡不同牌却没有与主推的差别说明")
                m = re.match(r"^(?:主推「(.+?)」排名更前：(.+)|与主推「(.+?)」等价：(.+))$",
                             reason)
                self.assertIsNotNone(m, f"{name}.{k} 句式不合口径：{reason!r}")
                bn, note = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                self.assertEqual(bn, tile_to_chinese(best),
                                 f"{name}.{k} 前缀里的主推不是本帧主推 {best}：{reason}")
                self.assertTrue(note.strip(), f"{name}.{k} 理由为空：{reason}")
                cmp_blk = alt.get("main_compare")
                self.assertIsInstance(cmp_blk, dict, f"{name}.{k} 缺机器可查的 main_compare")
                self.assertIn(cmp_blk.get("kind"), ("fact", "model"), f"{name}.{k} kind 越界")
                self.assertIsInstance(cmp_blk.get("layer"), str)
                self.assertTrue(cmp_blk.get("layer"), f"{name}.{k} 没说在哪一层分出先后")
                self.assertEqual(bool(cmp_blk.get("equivalent")), bool(m.group(3)),
                                 f"{name}.{k} 文字与 equivalent 标记分叉：{reason}")
                self.assertIsNone(FAKE_SCORE.search(reason), f"{name}.{k} 伪量纲：{reason}")
                self.assertIsNone(PCT.search(reason), f"{name}.{k} 带百分比：{reason}")
                seen += 1
        self.assertGreaterEqual(seen, 1, "三帧里没有一个张双策略卡与主推不同：本用例已空转")

    def test_alternative_fact_numbers_are_recomputable(self):
        """卡片上的数字必须能在 advice 条目上对号，否则就是假解释。"""
        fact = 0
        checked = 0
        for name, pl in self.frames.items():
            by_tile = {a.get("tile"): a for a in pl.get("advice") or []}
            best = pl.get("best")
            for k, alt in self._alts(name):
                if not best or alt.get("tile") == best:
                    continue
                a, b = by_tile.get(best), by_tile.get(alt.get("tile"))
                if not isinstance(a, dict) or not isinstance(b, dict):
                    self.fail(f"{name}.{k} 的牌不在候选列表里，无法核对：{alt.get('tile')}")
                cmp_blk = alt.get("main_compare") or {}
                note = cmp_blk.get("note") or ""
                if cmp_blk.get("kind") != "fact" or cmp_blk.get("equivalent"):
                    continue          # 承认是评分/等价：没有数字可核，也就骗不了人
                fact += 1
                m = re.search(r"少 (\d+) 向听（(\d+) vs (\d+)）", note)
                if m:
                    self.assertEqual((int(m.group(2)), int(m.group(3))),
                                     (int(a["shanten"]), int(b["shanten"])), f"{name}.{k}：{note}")
                    checked += 1
                    continue
                m = re.search(r"未现进张上界多 (\d+) 张（(\d+) vs (\d+)）", note)
                if m:
                    self.assertEqual((int(m.group(2)), int(m.group(3))),
                                     (int(a["ukeire"]), int(b["ukeire"])), f"{name}.{k}：{note}")
                    checked += 1
                    continue
                m = re.search(r"牌墙确实还能摸到 (\d+) 张", note)
                if m:
                    self.assertGreater(int(m.group(1)), 0, f"{name}.{k}：{note}")
                    checked += 1
        self.assertEqual(fact, checked,
                         f"有 {fact - checked} 条事实型差额没被核对（句式改了而守卫没跟上）")

    def test_compare_comes_from_the_same_tiebreak_source(self):
        """口径必须回到底层决胜链：面板/卡片自己拿 ev 相减就是第二套口径。"""
        src = _read(os.path.join(PKG, "engine", "engine.py"))
        at = src.rindex("双策略卡片（稳胡极速流/大番收益流）")
        block = src[at:at + 2400]
        self.assertIn("_advantage_note(_main, _it)", block,
                      "双策略卡的差别没走决胜链，而是另一套手写文案")
        self.assertNotIn('- float(', block,
                         "卡片区里出现了 ev 减法：那是把合成评分当测量值卖")


class TestAP2TieFrames(unittest.TestCase):
    """A-P2 同分裁决的帧级门：两张牌 EV 全等时，必须给出可核对的决胜层或等价声明。

    与 `TestAdvantageNoteLayerMutation` 的分工：那组用合成条目逐层验证句式；
    这一组跑**真实引擎帧**（analyzer 排序 → 防守回填 → 战术加权 → annotate 全链），
    确认“同分”不是测试里造出来的边角——24 个真实帧里 12 个含 EV 全等候选对。
    本批帧的层分布：索引兜底 8、进张数 4、防守安全 4、听口面数 4。
    """

    @classmethod
    def setUpClass(cls):
        cls.tie_std = _payload("std_tdh", TIE_HAND, TIE_RIVER, seed=5)
        cls.tie_sc = _payload("sc_xz", FLIP_HAND, FLIP_RIVER, seed=5)
        cls.src = _read(DART)

    def _ties(self, pl):
        """按面板顺序抽相邻且 EV 全等的候选对。"""
        adv = _by_rank(pl["advice"])
        return [(a, b) for a, b in zip(adv, adv[1:]) if a.get("ev") == b.get("ev")]

    @staticmethod
    def _next_label(a, b):
        """被比较那张在面板上的名字（字牌会过一道标签转换，不能直接拿 mpsz 比）。"""
        return ((a.get("advantage") or {}).get("compares") or {}).get("next") or b["tile"]

    def test_ev_tie_event_really_happens_in_these_frames(self):
        """同分事件必须真实存在，否则整组守卫都是空转。"""
        for name, pl in (("std", self.tie_std), ("sc", self.tie_sc)):
            ties = self._ties(pl)
            self.assertGreaterEqual(len(ties), 1, f"{name} 帧里没有 EV 全等候选对")

    def test_ev_tie_is_explained_by_a_tiebreak_layer_not_by_score(self):
        """同分的解释不得回到分数：那正是“EV 相同却说一张更好”的歧义本身。"""
        checked = 0
        for pl in (self.tie_std, self.tie_sc):
            for a, b in self._ties(pl):
                r = a.get("advantage_reason")
                self.assertTrue(r, f"{a.get('tile')}~{b.get('tile')} 同分却无排序说明")
                self.assertIn(self._next_label(a, b), r)   # 点名被比较的那张
                n = a["advantage"]
                self.assertNotEqual(n["layer"], "EV",
                                    f"同分对被判成 EV 差异：{n}")
                self.assertNotIn("1.2 分", r)
                self.assertNotIn("分）优于", r)
                # 口径只能落在两个互斥的头部之一：真分出优劣、或认等价。
                # 出现第三种句式（“评分更高”“综合分优”）就是把合成评分当量纲卖。
                heads = (f"优于次选 {self._next_label(a, b)}：",
                         f"与次选 {self._next_label(a, b)} 等价：")
                self.assertTrue(r.startswith(heads), f"排序说明口径漂移：{r}")
                checked += 1
        self.assertGreaterEqual(checked, 2)

    def test_genuine_equivalence_is_declared_as_such(self):
        """没有任何牌理差异时只能认等价，文案用「与次选 X 等价」而不是「优于」。"""
        found = 0
        for pl in (self.tie_std, self.tie_sc):
            for a, b in self._ties(pl):
                n = a["advantage"]
                if not n["equivalent"]:
                    continue
                found += 1
                r = a["advantage_reason"]
                self.assertTrue(r.startswith(f"与次选 {self._next_label(a, b)} 等价："), r)
                self.assertNotIn("优于", r, f"等价却写了排序：{r}")
                # 牌名只应该出现在前缀里：note 再说一次“与打X”就是浪费行宽
                self.assertEqual(r.count(self._next_label(a, b)), 1,
                                 f"牌名重复，面板那一行会被挤爆：{r}")
                # 等价必须建立在“展示的事实真的全等”上，不能把有差别的两张说成平手
                self.assertEqual(a["ev"], b["ev"])
                self.assertEqual(a.get("ukeire"), b.get("ukeire"))
                self.assertEqual(a.get("shanten"), b.get("shanten"))
                self.assertEqual((a.get("danger_flow") or {}).get("level"),
                                 (b.get("danger_flow") or {}).get("level"))
                self.assertIn(n["layer"], ("完全同序", "索引兜底"),
                              f"声称等价但决胜层停在 {n['layer']}：{n['note']}")
        self.assertGreaterEqual(found, 1,
                                "本批帧未命中真正等价的同分对（守卫空转）")

    def test_real_difference_is_never_called_equivalent(self):
        """反向：同分但事实层有差时，不许拿“等价”糊弄过去。"""
        for pl in (self.tie_std, self.tie_sc):
            for a, b in self._ties(pl):
                diff = (a.get("ukeire") != b.get("ukeire")
                        or a.get("shanten") != b.get("shanten"))
                if diff:
                    self.assertFalse(a["advantage"]["equivalent"],
                                     f"有牌理差却说等价：{a['advantage']}")

    def test_tie_order_is_reproducible(self):
        """同分先后必须确定性：重跑一次得不到同一顺序就是隐式随机源。"""
        again = _payload("std_tdh", TIE_HAND, TIE_RIVER, seed=5)
        self.assertEqual([a["tile"] for a in _by_rank(self.tie_std["advice"])],
                         [a["tile"] for a in _by_rank(again["advice"])])
        self.assertEqual([a.get("advantage_reason") for a in _by_rank(self.tie_std["advice"])],
                         [a.get("advantage_reason") for a in _by_rank(again["advice"])])

    def test_dart_turns_the_equivalence_flag_into_the_parallel_label(self):
        """面板得把这个布尔真读到：等价写「并列」，有差写「次选」。"""
        card = re.sub(r"\s+", " ", _dart_body(self.src, "Widget _adviceSection("))
        want = ("(sorted[i - 1]['advantage'] is Map && (sorted[i - 1]['advantage'] as Map)"
                "['equivalent'] == true) ? '并列' : '次选'")
        self.assertIn(want, card)
        # 判据必须读上一位而不是本位向上：本位的对手可能是没被渲染的第四张，
        # 那时候“并列”就是个不知与谁并列的标签。
        self.assertNotIn("sorted[i]['advantage']", card)
        self.assertEqual(card.count("'并列'"), 1, "「并列」只应有一个源头")
        self.assertEqual(card.count("'次选'"), 1,
                         "「次选」也只允许一处：两处就变成两套口径")

    def test_dart_badge_stops_endorsing_a_ranking_that_is_tied(self):
        """主推与次选等价时，卡片顶部徽标也不许再写「较优选择」。

        旧行为：上面一句刚说“与次选 X 等价”，下面仍贴着“推荐 · 较优选择”——
        同一张卡片自己否自己，正是本规约要消除的“歧义”。
        """
        card = re.sub(r"\s+", " ", _dart_body(self.src, "Widget _adviceSection("))
        self.assertIn(
            "final bool topTied = top['advantage'] is Map && "
            "(top['advantage'] as Map)['equivalent'] == true;", card)
        self.assertIn("topTied ? '并列 · 任选其一' : '推荐 · 较优选择'", card)
        # 徽标仍只在主推位出现，不能因为改文案就到处贴
        self.assertIn("if (advRank <= 1 && advTotal > 1)", card)
        self.assertEqual(card.count("'推荐 · 较优选择'"), 1)
        self.assertEqual(card.count("'并列 · 任选其一'"), 1)

    def test_dart_badge_fits_the_narrowest_panel(self):
        """徽标宽度预算：文本从 4 字长成一句就会把理由列挤到行数翻倍。

        几何全部从源码现读，不写死“174dp”这种凭印象的数：徽标是非 flex 子项，
        自己不会被截断，但会从 `Expanded` 的理由列那里抢走横向空间；面板可缩到
        `minPanelW`，那就是最坏情况。
        """
        m = re.search(r"static const double minPanelW = (\d+(?:\.\d+)?);", self.src)
        self.assertTrue(m, "minPanelW 常量被改名，宽度预算失去依据")
        m2 = re.search(r"topTied \? '([^']+)' : '([^']+)',\s*style: const TextStyle\(\s*"
                       r"color: [^,]+,\s*fontSize: ([\d.]+),", self.src, re.S)
        self.assertTrue(m2, "拿不到徽标文本与字号，无法算宽度")
        fs = float(m2.group(3))
        # 卡片内边距 horizontal:8（两侧共 16）+ 徽标自身 padding 3×2 + 与理由列间距 3
        budget = float(m.group(1)) - 16.0 - 6.0 - 3.0
        for label in m2.groups()[:2]:
            # 中日韩字符按 1 em 估，空格 0.25 em，间隔号 0.5 em（偏保守）
            w = sum(fs if ord(ch) > 0x2E80 else (fs * 0.5 if ch == "·" else fs * 0.25)
                    for ch in label)
            self.assertLessEqual(
                w, budget * 0.42,
                f"徽标「{label}」宽 {w:.1f}dp，超过最窄面板可用宽的 42%"
                f"（预算 {budget * 0.42:.1f}dp）——理由列会被挤到换行翻倍")
            self.assertLess(w, budget, f"徽标「{label}」连自身预算都超了")


class TestWiringIsNotSilent(unittest.TestCase):
    """接线与降级可见性：字段没接上必须能在 diag 里看见，而不是"面板少一行"。"""

    def test_no_frame_swallows_an_error_inside_annotation(self):
        """annotate 的异常会被 except 吃掉并记进 stats['error']：每帧都必须查。

        只查其中几帧的话，“某一帧在 annotate 里抢了异常”就会变成静默降级：
        面板少一行、diag 里一句 error，而两边都没人看（变异检验 P4 就是专抓这个）。
        """
        frames = {
            "sc13_flip": _payload("sc_xz", FLIP_HAND, FLIP_RIVER),
            "sc13_calm": _payload("sc_xz", CALM_HAND, CALM_RIVER),
            "sc14_press": _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11),
            "std14": _payload("std_tdh", STD_HAND, STD_RIVER, seed=67),
        }
        for name, pl in frames.items():
            st = (pl.get("diag") or {}).get("decisions")
            self.assertIsInstance(st, dict, f"{name}：diag.decisions 没下发")
            self.assertIsNone(st.get("error"), f"{name}：补全决策时降级了 {st.get('error')}")
            adv = pl["advice"]
            has_pd = any(isinstance(a.get("predraw"), dict) for a in adv)
            if has_pd:
                self.assertGreaterEqual(st["predraw"], 1,
                                        f"{name}：有模拟数据却没给出预演行（接线断了）")
                self.assertEqual(st["predraw"],
                                 sum(1 for a in adv if "predraw_line" in a), f"{name} 计数分叉")

    def test_diag_counts_match_what_reached_the_payload(self):
        for name, pl in (
                ("sc13_flip", _payload("sc_xz", FLIP_HAND, FLIP_RIVER)),
                ("sc14_press", _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11)),
                ("std14", _payload("std_tdh", STD_HAND, STD_RIVER, seed=67))):
            st = (pl.get("diag") or {}).get("decisions")
            self.assertIsInstance(st, dict, f"{name}：diag.decisions 未下发，接线断了也没人知道")
            self.assertIsNone(st["error"], f"{name} 降级了：{st['error']}")
            adv = pl["advice"]
            self.assertEqual(st["n"], len(adv), f"{name} 候选数对不上")
            self.assertEqual(st["advantage"],
                             sum(1 for a in adv if "advantage_reason" in a), f"{name} 优势计数空转")
            self.assertEqual(st["danger_hint"], sum(1 for a in adv if "danger_hint" in a),
                             f"{name} 危险指令计数与实际下发不一致")
            self.assertEqual(st["predraw"], sum(1 for a in adv if "predraw_line" in a),
                             f"{name} 预演计数不一致")
            self.assertEqual(st["alternatives"],
                             sum(1 for a in adv if "safer_alternatives" in a), f"{name} 替代方案计数不一致")

    def test_annotating_does_not_reorder_the_engine_cache(self):
        """build_advice 命中缓存时返回的就是那份列表；就地重排会改掉下一帧的主推。"""
        eng = make_engine("sc_xz", FLIP_HAND, FLIP_RIVER)
        first = run_frames(eng, make_image(seed=5), 8)[-1]
        order_a = [a["tile"] for a in first["advice"]]
        again = run_frames(eng, make_image(seed=5), 4)[-1]
        self.assertEqual([a["tile"] for a in again["advice"]], order_a,
                         "候选顺序在连续帧之间漂移：缓存被展示层改动污染了")
        self.assertEqual(again["best"], first["best"])

    def test_output_is_deterministic(self):
        a = _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11)
        b = _payload("sc_xz", PRESS_HAND, PRESS_RIVER, PRESS_MELDS, seed=11)
        self.assertEqual(_json_round(a["advice"]), _json_round(b["advice"]),
                         "同一帧两次求解结果不同：名次文案会跟着抖")


def _json_round(obj):
    """走一次 JSON：面板拿到的是序列化后的副本，测试必须看同一份形态。"""
    return json.loads(json.dumps(obj, ensure_ascii=False))


class TestSourceDiscipline(unittest.TestCase):
    """源码纪律：口径只能有一个源头，面板不得自己算 ev 减法。"""

    def test_panel_never_subtracts_ev_for_a_reason(self):
        """文案口径在 Python：面板不得自己拿 ev 相减、也不得写死一套句式。"""
        src = _read(DART)
        card = _dart_body(src, "Widget _adviceSection(")
        self.assertNotIn("优于次选", card,
                         "「优于次选 X」必须由引擎给出，面板自己拼就会与引擎名次两套口径")
        self.assertNotIn("推荐 · 较优选择", _dart_strip(src, keep_strings=True).replace(
            "推荐 · 较优选择", "", 1), "「推荐」标签只能出现在主推行守卫里（写死第二处就失控了）")
        self.assertNotIn("1.2 分", src, "伪量纲样本串不得出现在面板")

    def test_engine_annotates_after_the_final_resort_order(self):
        src = _read(os.path.join(PKG, "engine", "engine.py"))
        call = src.rindex("annotate_advice_decisions(advice, best)")
        kb = src.rindex("KnowledgeBase.evaluate_tactics(")
        self.assertGreater(call, kb,
                           "文案必须在知识库重排之后补，否则名次与实际顺序分叉")
        self.assertIn('"predraw"', src, "predraw 没进透传白名单，analyzer 的模拟数据会被丢掉")


if __name__ == "__main__":
    unittest.main(verbosity=2)
