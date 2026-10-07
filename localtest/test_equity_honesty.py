# -*- coding: utf-8 -*-
"""B-P3 概率诚实化：未标定的模型值不得以任何绝对百分比出现在用户可见字段里。

为什么需要这道门（设计意图）
----------------------------
项目里的数字分三类，长得一样、含义完全不同：

1. **事实 fact**：牌局账本里的计数（已现 X 张、叫口共余 Y 张、牌墙还能撑 N 轮）。
   可逐张核对，可以直接显示；但下界/上界必须写「至少/至多」。
2. **模型 model**：`win_equity`、`deal_in_prob`、`tenpai_prob`、`top_held.prob`。
   由解析式或手工先验算出（logistic 斜率 0.28、鸣牌 +0.20、`p_wait=0.04~0.26`、
   `likelihood *= 2.2`、equity 的 1.6 倍点炮乘数与 0.05/0.65/0.98 地板天花板……），
   **从未与真实对局结果做过标定**。`0.81` 只代表「在模型里排 0.81 的位置」，
   不代表「100 局胡 81 局」。把它印成「胜率 81%」就是本项目最典型的伪精确。
3. **已标定 learned**：目前没有。

本套测试锁住四件事（任一条红都是产品事故，不是测试太严）：
A. `evaluate_gauge` 的 insight/badge 在任何输入下都不出现 `N%`，且档位词与
   `probability_bands` 逐字同源；`calibrated` 必须读常量，不许写死。
B. PVN 未训练时 `win_equity == analytical_equity` 且 `equity_basis == "analytical"`
   （P0 摘权无残留）；面板据此显示「纯解析式评估」。
C. std 非听牌的「进张 N 张」必须拆账或标「至多」（N 是未现牌上界，含对手按住的牌）。
D. Dart 面板反向契约（两个面板文件一起扫）：禁复活的写法（`胜率 $winRate%`、`)}番'`、
   `$dealInPercent%危`、`叫听 $tenpaiRate%`、`'score'` 装饰分值）不得再出现在代码或文案
   里（注释里讲历史除外）；面板实际读取的 band 类字段必须在 payload 里真实存在；
   没有 Flutter SDK 时，代码骨架的括号配平由本文件代劳。

运行：py -3.10 localtest/test_equity_honesty.py
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)
sys.path.insert(0, HERE)

import probability_bands as pb                      # noqa: E402
from modes import available_set, get_mode, hand_sizes  # noqa: E402
from sichuan.equity_radar import WinEquityGauge     # noqa: E402
from sichuan.hand_range import BayesianHandRangeReader, OpponentState  # noqa: E402
from sichuan.sichuan_analyzer import SichuanAnalyzer, pool_remaining_from_visible  # noqa: E402
from std.std_analyzer import StdAnalyzer            # noqa: E402
from tile_ledger import build_ledger                 # noqa: E402
from recognition.policy_value_net import PolicyValueNetwork  # noqa: E402
from test_tile_ledger_e2e import make_engine, make_image, run_frames  # noqa: E402

PCT = re.compile(r"\d+\s*%")
DART = os.path.abspath(os.path.join(HERE, "..", "lib", "overlays", "mahjong_overlay.dart"))
DART_DEBUG = os.path.abspath(os.path.join(HERE, "..", "lib", "debug_page.dart"))

# 14 张：123m 456m 789m 123p + 55p（打 5p 即听 5p 的形）
TENPAI28 = [0] * 28
for _i in list(range(9)) + [9, 10, 11]:
    TENPAI28[_i] = 1
TENPAI28[13] = 2


def _strings(node, keys=("reason", "desc", "insight", "badge", "message", "tip")):
    """递归掏出 payload 里所有「会说给用户听」的字符串（跨 JSON 后仍是 str）。"""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and k in keys:
                out.append((k, v))
            else:
                out.extend(_strings(v, keys))
    elif isinstance(node, list):
        for v in node:
            out.extend(_strings(v, keys))
    return out


def _dart_body(src: str, signature: str) -> str:
    """按大括号配平切出一个 Dart 方法体（先剔掉 `//` 注释行）。

    为何不直接逐行正则：“胜率 X%”在 `cond ? '胜率 …%' : band` 这种跨行三元里
    很常见，逐行判定会把「有守卫的合法写法」误判成违规，然后被人改成无守卫的
    单行写法来“让测试过关”——那正好把守卫废了。按语句（`;`）切段才能与排版无关。
    """
    i = src.index(signature)
    # 不能直接取第一个 `{`：命名参数 `{double? analyticalEquity}` 比方法体先出现，
    # 从它开始配平会只拿到参数表那一小段，然后「方法体里找不到胜率」会变成假绿。
    m = re.search(r"\{\s*\n", src[i:])
    assert m, f"没定位到方法体左括号：{signature}"
    j = i + m.start()
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                body = src[j:k + 1]
                return "\n".join(ln for ln in body.splitlines()
                                 if not ln.strip().startswith(("//", "///")))
    raise AssertionError(f"大括号未配平：{signature}")


def _dart_interp_end(text: str, j: int) -> int:
    """text[j-2:j] 是 `${`；返回与之匹配的 `}` 之后的位置。

    插值里允许再套同种引号的字符串（ Dart 合法，面板里就有
    `'候选: ${pickCandidates.join(' ')}'`），所以嵌套字符串必须一起跳过去——
    不然外层字面量会在内层引号处提前结束，后面的括号全部错位。
    """
    depth, n = 1, len(text)
    while j < n and depth:
        ch = text[j]
        if ch == "{":
            depth += 1
            j += 1
        elif ch == "}":
            depth -= 1
            j += 1
        elif ch in "\"'":
            j = _dart_string_end(text, j, text.startswith(ch * 3, j))
        else:
            j += 1
    return j


def _dart_string_end(text: str, i: int, multi: bool) -> int:
    """字符串字面量的结束位置（开区间，含结尾引号）。multi 对应 `'''`/`\"\"\"`。

    单行串遇到裸换行就地断开：那说明源码本身少了引号，宁可在这里把范围缩小，
    也不要把后面整片真代码当字面量吞掉（吞掉会让括号配平假绿）。
    """
    q = text[i]
    n, step = len(text), (3 if multi else 1)
    j = i + step
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "\n" and not multi:
            break
        if ch == "$" and text.startswith("${", j):
            j = _dart_interp_end(text, j + 2)
            continue
        if text.startswith(q * step, j):
            return j + step
        j += 1
    return n


def _dart_interp_code(seg: str) -> str:
    """只留下字符串里 `${…}` 插值表达式中的代码，其余字面量丢弃。"""
    out: list = []
    k = 0
    while True:
        p = seg.find("${", k)
        if p < 0:
            return "".join(out)
        out.append(" ")
        end = _dart_interp_end(seg, p + 2)
        out.append(_dart_strip(seg[p + 2:max(p + 2, end - 1)], False))
        k = end


def _dart_strip(text: str, keep_strings: bool = False) -> str:
    """剔掉 Dart 注释；`keep_strings=True` 时保留字符串字面量原文。

    两种模式各挡一类误判，缺一不可：
    - 保留文案（扫 `胜率 $winRate%` 这类禁复活的写法）：它们活在字符串里，被剔掉
      测试就变假绿；而注释里「原本印心理胜势指数 98%」是设计说明，不该被判红。
    - 剔成代码骨架（做括号配平）：文案里的括号与注释里的括号都不是语法结构。

    为什么需要这道门：本机没有 Flutter SDK，`flutter analyze` 跑不了。本轮改 Dart 时
    两次差点被编辑工具合并行/漏括号——那类错在编译期是整片红，但会拖到真机构建才
    暴露。本扫描不验类型不验语义，只保证代码骨架没塌。`${…}` 里的表达式当代码递归
    处理（Dart 要求它自身配平）。
    """
    out: list = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "//":                       # 行注释（/// 也算）
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if two == "/*":                       # 块注释
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if c in "\"'":
            multi = text.startswith(c * 3, i)
            end = _dart_string_end(text, i, multi)
            seg = text[i:end]
            out.append(seg if keep_strings else _dart_interp_code(seg))
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _dart_unbalanced(text: str):
    """返回第一个括号不配对的位置（None = 配平）。"""
    close_of = {")": "(", "]": "[", "}": "{"}
    stack = []
    line = 1
    for ch in text:
        if ch == "\n":
            line += 1
            continue
        if ch in "([{":
            stack.append((ch, line))
        elif ch in close_of:
            if not stack or stack[-1][0] != close_of[ch]:
                return f"第 {line} 行多出/错位的 '{ch}'"
            stack.pop()
    if stack:
        ch, ln = stack[-1]
        return f"第 {ln} 行的 '{ch}' 始终没闭合"
    return None


class TestGaugeInsightIsBandOnly(unittest.TestCase):
    """雷达结论文案：全区间扫描都不得出现绝对百分比，且档位与常量同源。"""

    def _grid(self):
        for eq in (0.0, 0.005, 0.02, 0.05, 0.1, 0.2, 0.3, 0.45, 0.5, 0.69,
                   0.7, 0.81, 0.9, 0.98, 1.0):
            for fan in (1, 2, 3, 5, 6):
                for dp in (0.0, 0.01, 0.05, 0.2, 0.45, 0.9):
                    yield eq, fan, dp

    def test_no_absolute_percent_across_equity_grid(self):
        n = 0
        for eq, fan, dp in self._grid():
            g = WinEquityGauge.evaluate_gauge(
                eq, expected_fan=fan, max_deal_in_prob=dp,
                deal_in_level="critical", facts=["听 5筒 共余 3 张；牌墙还能撑 6 轮"])
            n += 1
            for field in ("insight", "badge"):
                self.assertIsNone(PCT.search(g[field]),
                                  msg=f"{field}={g[field]!r} 含绝对百分比（未标定值）")
            self.assertNotIn("期望收益", g["insight"], msg="net_ev 是无单位评分，不该进文案")
            self.assertIn(g["band"], g["insight"], msg="insight 必须显档位")
            self.assertNotIn("胜率", g["insight"],
                             msg="「胜率」二字只能在标定后复活")
        self.assertEqual(n, 15 * 5 * 6, "网格必须跑满，否则本测试是空转")

    def test_band_and_level_share_one_source(self):
        """level → band 只能由 probability_bands 翻译，雷达不得自带一套词典。"""
        for eq, fan, dp in self._grid():
            g = WinEquityGauge.evaluate_gauge(eq, expected_fan=fan, max_deal_in_prob=dp)
            self.assertEqual(g["band"], pb.equity_band(g["level"]),
                             msg=f"{g['level']} 的档位词与单一来源不一致")

    def test_gauge_exposes_provenance_and_drops_win_rate(self):
        g = WinEquityGauge.evaluate_gauge(0.81, expected_fan=3, max_deal_in_prob=0.05)
        for k in ("band", "calibrated", "note", "equity_basis", "net_ev_unit"):
            self.assertIn(k, g, f"payload 缺 {k}：面板无法判断该显示档位还是百分比")
        self.assertNotIn("win_rate", g,
                         "win_rate（把模型值当百分比）必须彻底移除，否则旧面板会读它复活伪精确")
        self.assertEqual(g["net_ev_unit"], "分",
                         msg="net_ev 单位曾是「番」：那是会让用户按番数取舍的错标")
        self.assertEqual(g["equity_basis"], "analytical", "默认口径是纯解析式")
        self.assertIs(g["calibrated"], pb.CALIBRATED)

    def test_calibrated_flag_is_read_from_single_source(self):
        """把常量翻True，档位元信息必须跟着翻——证明没有人写死 False。

        写死 `calibrated: False` 今天看着更安全，实际是让「标定完成」这件
        事发生时面板仍然说不出真话（并且没人会记得来这里改）。
        """
        try:
            pb.CALIBRATED = True
            g = WinEquityGauge.evaluate_gauge(0.6, expected_fan=2)
            self.assertTrue(g["calibrated"], "calibrated 必须是读常量，不是写死")
            self.assertIn("标定", g["note"])
        finally:
            pb.CALIBRATED = False
        self.assertFalse(WinEquityGauge.evaluate_gauge(0.6)["calibrated"],
                         "复位失败会污染后续用例（全局常量必须还原）")

    def test_facts_are_quoted_and_deal_in_becomes_band(self):
        with_f = WinEquityGauge.evaluate_gauge(
            0.75, expected_fan=2, facts=["牌墙还能撑 6 轮"], deal_in_level="high")
        self.assertIn("依据：牌墙还能撑 6 轮", with_f["insight"])
        self.assertIn(pb.danger_band("high"), with_f["insight"])
        without = WinEquityGauge.evaluate_gauge(0.75, expected_fan=2)
        self.assertNotIn("依据：", without["insight"],
                         "没有账本事实时只能讲档位，不许编一个数字充数")


class TestPvnOffIdentityStillHolds(unittest.TestCase):
    """P0 摘权回归：未训练时胜率必须逐字等于解析式，且口径字段如实说明。"""

    def setUp(self):
        self.pvn = PolicyValueNetwork.get_instance()
        self.assertFalse(self.pvn.trained, "类默认必须是未训练")

    def _run(self):
        pool = pool_remaining_from_visible(list(TENPAI28), [0] * 34, [0] * 34)
        return SichuanAnalyzer.analyze_discards(
            list(TENPAI28), 0, pool, None)

    def test_untrained_equity_equals_analytical_and_basis_marked(self):
        rows = self._run()
        self.assertTrue(rows, "前提：必须给出建议，否则本用例空转")
        for r in rows:
            self.assertFalse(r["pvn_used"])
            self.assertEqual(r["win_equity"], r["analytical_equity"],
                             msg=f"{r['tile']}：PVN 关闭态胜率必须等于解析式")
            self.assertEqual(r["ev_gauge"]["equity_basis"], "analytical")
            self.assertIsNone(PCT.search(r["ev_gauge"]["insight"]))

    def test_trained_blend_marks_its_basis(self):
        """开关打开时口径必须如实改成 analytical+pvn（否则面板会误标解析式）。"""
        try:
            self.pvn.trained = True
            rows = self._run()
        finally:
            self.pvn.trained = False
        self.assertTrue(rows)
        r = rows[0]
        self.assertTrue(r["pvn_used"])
        self.assertEqual(r["ev_gauge"]["equity_basis"], "analytical+pvn")
        self.assertIsNone(PCT.search(r["ev_gauge"]["insight"]),
                          "即便融合 PVN，输出仍是未标定值：档位化不因开关而豁免")


class TestStdUkeireIsHonest(unittest.TestCase):
    """std 非听牌的「进张 N 张」：有账本必须拆，没账本必须标「至多」。"""

    # 2 向听的散牌形：124579m 124689p 中中。任何一个弃牌候选都还在非听牌态，
    # 否则本用例会退化成“只验了听牌文案”（上一版就踩了这个坑：用听牌形往非听牌分支）。
    HAND = [0, 1, 3, 4, 6, 8, 9, 10, 12, 14, 16, 17, 31, 31]   # 14 张
    MODE = "std_tdh"

    def _counts(self):
        c = [0] * 34
        for t in self.HAND:
            c[t] += 1
        return c

    def _rules_avail(self):
        rules = get_mode(self.MODE)
        return rules, sorted(available_set(self.MODE))

    def test_non_tenpai_reason_is_ledger_split_or_upper_bound(self):
        c = self._counts()
        disc = [0] * 34
        disc[18] = 2                       # 牌河两张 1s，保证有非听牌候选可考
        led = build_ledger(c, disc, [0] * 34, available=self._rules_avail()[1],
                           laizi=[], opponents=[(0, None)] * 3,
                           standings=[13, 13, 13],
                           expected_hand_sizes=hand_sizes(self.MODE))
        self.assertTrue(led["ok"], led["violations"])
        rules, avail = self._rules_avail()
        pool = [max(0, 4 - c[i] - disc[i]) for i in range(34)]
        rows = StdAnalyzer.analyze_discards(c, rules, avail, pool_remaining=pool,
                                            ledger=led)
        self.assertTrue(rows)
        deep = [r for r in rows if r["shanten"] > 0]
        self.assertTrue(deep, f"本例必须含非听牌候选，实际 {[(r['tile'], r['shanten']) for r in rows]}")
        for r in deep:
            self.assertIsNotNone(r.get("ukeire_chance"),
                                 msg=f"{r['tile']} 有账本却没拆进张：{r['reason']}")
            self.assertIn("共余", r["reason"], msg=f"{r['tile']} reason 未走账本口径")
            self.assertIsNone(re.search(r"进张\d+张", r["reason"]),
                              msg="裸「进张N张」把上界说成确定机会")
            # 文案里的数字必须能指回字段（可追溯），而不是另算一份
            self.assertIn(str(r["ukeire_chance"]["total_unseen"]), r["reason"])

    def test_without_ledger_marks_upper_bound(self):
        rules, avail = self._rules_avail()
        rows = StdAnalyzer.analyze_discards(self._counts(), rules, avail,
                                            pool_remaining=None, ledger=None)
        deep = [r for r in rows if r["shanten"] > 0]
        self.assertTrue(deep, "前提：必须有非听牌候选")
        for r in deep:
            self.assertIn("至多", r["reason"],
                          msg=f"无账本时 {r['tile']} 仍把未现上界当确定值：{r['reason']}")
            self.assertIsNone(PCT.search(r["reason"]))


class TestDangerFlowAndHandRangeBands(unittest.TestCase):
    """危险度与对手透视：档位随字段下发，含义（相对后验）写进 payload。"""

    def _opponents(self):
        # discards/melds 必须是 27 型索引（生产链路就是这么传的），传 mpsz 字符串
        # 会在 tile_to_suit 里报 TypeError，且旧版曾以“测试错而代码对”的方式静默。
        return [OpponentState(seat=1, dingque_suit=0, discards=[11, 13, 14],
                              standing_count=10),
                OpponentState(seat=2, dingque_suit=None, discards=[0, 8],
                              standing_count=13)]

    def test_danger_flow_exposes_band_and_kind(self):
        pool = [4] * 27
        d = BayesianHandRangeReader.evaluate_danger_flow(13, pool, self._opponents())
        self.assertEqual(d["danger_band"], pb.danger_band(d["danger_level"]))
        self.assertEqual(d["prob_kind"], "model",
                         "未标定期间必须自报『这是模型值』，前端才敢不印百分号")

    def test_hand_ranges_bands_agree_with_single_source(self):
        pool = [4] * 27
        for i in (0, 1, 2):
            pool[i] = 1
        summaries = BayesianHandRangeReader.get_hand_ranges_summary(
            self._opponents(), pool)
        self.assertEqual(len(summaries), 2, "前提：两个对手都要有摘要")
        for s in summaries:
            self.assertEqual(s["tenpai_band"], pb.tenpai_band(s["tenpai_prob"]))
            self.assertEqual(s["prob_kind"], "model")
            self.assertEqual(s["held_prob_kind"], pb.held_kind())
            for h in s["top_held"]:
                self.assertEqual(h["band"], pb.held_band(h["prob"]))
        self.assertTrue(any(s["top_held"] for s in summaries),
                        "top_held 全空的话本用例什么都没验")


class TestPayloadEndToEndHonesty(unittest.TestCase):
    """真 payload 层：面板会读的每一条文案都不许带未标定百分比。"""

    def _payload(self, mode, hand, river, seed):
        eng = make_engine(mode, hand, river)
        return run_frames(eng, make_image(seed=seed), 8)[-1]

    def test_sichuan_frame_strings_are_band_or_fact(self):
        hand = ["3m", "4m", "5m", "6m", "7m", "8m", "9m",
                "1p", "2p", "3p", "5p", "5p", "5p", "7s"]
        payload = self._payload("sc_xz", hand, ["1s", "2s", "9p", "9p"], 51)
        self.assertTrue(payload.get("advice"), "前提：本帧必须有建议")
        pairs = _strings(payload)
        self.assertTrue(pairs, "payload 里必须有待展示文案，否则本用例空转")
        for k, v in pairs:
            self.assertIsNone(PCT.search(v), msg=f"{k}={v!r} 含绝对百分比")
        gauge = payload.get("ev_gauge")
        self.assertIsNotNone(gauge, "payload 必须带 ev_gauge")
        for k in ("band", "calibrated", "note", "equity_basis", "net_ev_unit"):
            self.assertIn(k, gauge)

    def test_std_frame_does_not_fabricate_a_gauge(self):
        """std 链路没有胜率模型，就不许凭空补一个仪表盘。

        仪表盘只在川麻 analyzer 算出 win_equity 时下发（engine 里的兜底也在川麻分支内）。
        宁可不显示，也不给一个无依据的档位当“胜率”——这是 B-P3 的另一半：诚实不只是
        把百分比换成档位，还包括算不出来的时候说“算不出”。
        """
        hand = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
                "1p", "2p", "3p", "5p", "5p"]
        payload = self._payload("std_tdh", hand, ["2p", "3p"], 53)
        self.assertIsNone(payload.get("ev_gauge"),
                          "std 帧不得下发仪表盘：没有胜率模型就没有可展示的口径")
        rows = payload.get("advice") or []
        self.assertTrue(rows, "前提：本帧必须有建议")
        for a in rows:
            self.assertIsNone(PCT.search(str(a.get("reason") or "")), msg=str(a))

    def test_sichuan_gauge_declares_basis_and_provenance(self):
        """川麻仪表盘（主路或兜底）必须自报口径，且无百分比。"""
        hand = ["3m", "4m", "5m", "6m", "7m", "8m", "9m",
                "1p", "2p", "3p", "5p", "5p", "5p", "7s"]
        payload = self._payload("sc_xz", hand, ["1s", "2s", "9p", "9p"], 57)
        gauge = payload.get("ev_gauge")
        self.assertIsNotNone(gauge, "川麻链路必须下发仪表盘")
        self.assertEqual(gauge["equity_basis"], "analytical",
                         "PVN 未训练时兜底/主路都是纯解析式，必须如实标注")
        self.assertFalse(gauge["calibrated"])
        self.assertIsNone(PCT.search(gauge["insight"]))

    def test_fast_and_big_routes_follow_ledger_wording(self):
        """双路线 desc：有账本用账本口径，没账本必须带「至多」。"""
        hand = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
                "1p", "2p", "3p", "5p", "5p"]
        payload = self._payload("std_tdh", hand, ["9s", "9s"], 55)
        routes = [payload.get("fast_advice"), payload.get("big_advice")]
        seen = [r for r in routes if isinstance(r, dict)]
        self.assertTrue(seen, f"双路线都没产出，用例将空转：{routes}")
        for r in seen:
            desc = str(r.get("desc") or "")
            self.assertIsNone(PCT.search(desc), msg=desc)
            if "进张" in desc:
                self.assertTrue("共余" in desc or "至多" in desc,
                                msg=f"desc 把上界当确定机会：{desc}")

    def test_tied_candidates_are_honest_in_e2e(self):
        """同分候选（B-P2 决胜链的适用区）在 E2E 文案里同样不得有绝对百分比。

        为什么单独验这一层：同分时用户只能靠文案做决定，排序稳了但文案里藏一个
        「胜率 81%」，比排序不稳更伤人。两个反空转断言：必须真的存在同分组，且
        同分组里必须至少一条带张数（否则全是空文案，什么都没验到）。
        """
        frames = [("std_tdh",
                   ["1m", "2m", "4m", "5m", "7m", "9m", "1p", "2p", "4p", "6p",
                    "8p", "9p", "7z", "7z"], ["3s", "6s", "1z"], 53),
                  ("sc_xz",
                   ["1m", "3m", "5m", "7m", "9m", "2p", "4p", "6p", "8p",
                    "1s", "3s", "5s", "7s", "9s"],
                   ["1p", "1p", "1p", "2s", "2s", "3p", "4p", "5p", "6p", "7p",
                    "8p", "8p", "9p", "9p", "1m", "1m", "4s", "6s"], 52)]
        tied_groups = 0
        numbered_groups = 0
        for mode, hand, river, seed in frames:
            advice = self._payload(mode, hand, river, seed).get("advice") or []
            self.assertTrue(advice, f"{mode} 帧必须有建议")
            by_ev = {}
            for a in advice:
                by_ev.setdefault(round(float(a.get("ev") or 0.0), 6), []).append(a)
            for ev, items in by_ev.items():
                if len(items) < 2:
                    continue
                tied_groups += 1
                # 带不带张数分组里不一样（定缺阶段的同分根本没说数字），所以
                # 不要求每一组都带数；但全场至少得有一组带数，否则本用例空转。
                if any(re.search(r"\d+\s*张", str(a.get("reason") or "")) for a in items):
                    numbered_groups += 1
                for a in items:
                    reason = str(a.get("reason") or "")
                    self.assertIsNone(PCT.search(reason),
                                      msg=f"同分组 ev={ev} 的文案带绝对百分比：{reason}")
                    self.assertNotIn("胜率", reason, msg=reason)
                    # 裸「进张N张」把未现上界说成确定机会；带「至多」或走账本拆张才行
                    self.assertIsNone(re.search(r"进张\s*\d+\s*张", reason),
                                      msg=f"同分组里出现裸上界：{reason}")
        self.assertGreaterEqual(tied_groups, 1,
                                "没有任何同分候选：本用例验不到 B-P2 的适用区")
        self.assertGreaterEqual(numbered_groups, 1,
                                "同分组里没有一组带张数：上界口径断言将空转")


class TestDartHonestyContract(unittest.TestCase):
    """反向锁 Dart 源码：禁复活的写法不得回来，面板读的字段必须真实存在。"""

    # 扫描范围：两个面板文件都算。文案伪造不关心它活在哪个文件，把人从
    # 主面板赶去设置页同样是个事故。
    FILES = {"mahjong_overlay.dart": DART, "debug_page.dart": DART_DEBUG}

    FORBIDDEN = {
        r"\['win_rate'\]": "Python 已停发 win_rate，面板不得再读它拼百分比",
        r"胜率 \$winRate%": "未标定胜率不得以百分比展示",
        r"\}番'": "net_ev 单位是『分』，标成『番』会误导用户按番取舍",
        r"\$dealInPercent%危": "点炮概率是手工先验乘积，不得印成百分比",
        r"点炮 \$dealInPercent%": "同上",
        r"叫听 \$tenpaiRate%": "logistic 先验不得印成百分比",
        r"\$tileStr \$p%": "top_held 是未归一化相对后验，只能比大小",
        r"进张 \$topUkeire 张": "上界必须走 _ukeireLabel（带 ≤ 或账本拆账）",
        # debug_page：与牌局算式无关的装饰值不得长成“测量指标”（B-P3）
        r"'score'": "签文分值字段已删：没有任何算式能导出它",
        r"\$score": "装饰值不得插值到文案里（曾渲染成「指数 98%」）",
        r"心理胜势指数": "写死的 98 不是测量结果，不得再当指数展示",
    }

    # 反向哨兵：剔注释不应该把屏幕上的文案一并剔掉。如果扫描器吃多了，
    # FORBIDDEN 会因为“什么都没剩”而假绿，这些串必须还在。
    SENTINELS = {
        "mahjong_overlay.dart": ("胜率", "进张", "候选:"),
        "debug_page.dart": ("心态签文 · 仅供调节情绪", "参悟心法"),
    }

    def setUp(self):
        with open(DART, encoding="utf-8") as fh:
            self.src = fh.read()                     # 含注释：给方法体定位用
        self.texts = {}                               # 只剔注释：给文案扫描用
        self.skeletons = {}                           # 连字面量一起剔：给配平用
        for name, path in self.FILES.items():
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
            self.texts[name] = _dart_strip(raw, keep_strings=True)
            self.skeletons[name] = _dart_strip(raw)

    @classmethod
    def setUpClass(cls):
        # 危险度/透视档位只在川麻链路下发，这里造一份真 payload 供多处断言复用
        # （起一次引擎比每个用例各起一次便宜，且保证三处断言看的是同一帧）。
        eng = make_engine("sc_xz", ["3m", "4m", "5m", "6m", "7m", "8m", "9m",
                                   "1p", "2p", "3p", "5p", "5p", "5p", "7s"],
                          ["1s", "2s", "9p", "9p"])
        cls.sc_payload = run_frames(eng, make_image(seed=59), 8)[-1]

    def test_comment_stripper_keeps_on_screen_text(self):
        """先验扫描器自身：它不能把文案当注释剔掉（否则下面的文案门全是假的）。

        文案扫描与“删了整块代码”在文本上长得一样，所以必须有一道门区分
        “确实没写”和“根本没看到”。
        """
        for name, sentinels in self.SENTINELS.items():
            for s in sentinels:
                self.assertIn(s, self.texts[name],
                              f"{name}：剔注释后掉了文案 {s!r}——扫描器吃多了，"
                              "文案扫描将变成假绿")

    def test_forbidden_resurrections_absent(self):
        for name, text in self.texts.items():
            for pat, why in self.FORBIDDEN.items():
                self.assertIsNone(re.search(pat, text),
                                  msg=f"{name} 出现禁复活写法 {pat!r}：{why}")

    def test_dart_skeletons_have_balanced_brackets(self):
        """无 Flutter SDK 时的结构门：代码骨架必须括号配平。

        本轮改 Dart 时编辑工具吞过 `def` 行、漏过括号；那类错编译期整片红，
        但会拖到真机构建（CI 打 APK）才暴露。这里只验结构，不验类型不验语义。
        """
        for name, sk in self.skeletons.items():
            self.assertIsNone(_dart_unbalanced(sk),
                              msg=f"{name} 结构损伤：{_dart_unbalanced(sk)}")

    def test_bracket_gate_is_not_vacuous(self):
        """配平门自身不得是空的：人为抽掉一个括号必须被它拦住。

        扫描器一旦退化成“把所有内容都剔掉”，上面的配平断言会永远绿。用真源码
        的一个片段做反向样本，把“能报错”这件事钉下来。
        """
        good = "Widget a() { return Row(children: [Text('${x.join(' ')}'),]); }"
        self.assertIsNone(_dart_unbalanced(_dart_strip(good)))
        # 同种引号嵌套必须正确识别：外层不能在内层 ' ' 处提前结束
        self.assertIn("join", _dart_strip(good), "插值里的代码被误剔了")
        for broken in ("Widget a() { return Row(children: [Text('x'),]; }",
                       "Widget a() { return Row(children: [Text('(')}"):
            self.assertIsNotNone(_dart_unbalanced(_dart_strip(broken)),
                                 f"配平门失效，样本没报错：{broken}")

    def test_win_rate_word_only_survives_behind_calibrated_guard(self):
        """面板上每一个「胜率」展示都必须受 `calibrated` 保护。

        只查字面量会漏：把 `equityChip` 改成无条件 `'胜率 ${…}%'` 后，旧的正则
        （盯的是 `胜率 $winRate%`）依旧全绿——那才是真会发生的回归。本断言按语句
        划段，要求同句里必然出现 `calibrated`（变异检验 M5 专门验这道门不是空的）。
        """
        body = _dart_body(self.src, "Widget _buildWinEquityGaugeWidget(")
        segs = [s for s in body.split(";") if "胜率" in s]
        self.assertTrue(segs, "面板方法体里已完全没「胜率」：实现被整块删了，本守卫失去对象")
        for s in segs:
            self.assertIn("calibrated", s,
                          msg="未标定的胜率展示必须受 calibrated 判定保护："
                               "\n  " + " ".join(s.split())[:160])

    def test_panel_declares_analytical_basis_when_pvn_off(self):
        """PVN 关闭态的面板标题必须自报口径：「纯解析式评估」。

        这是用户明确要求的守卫（win_equity == analytical_equity 时不得含糊地报成胜率）。
        只查字面量不够：那句必须同时带上两个判据（口径字段 + 恒等式），否则
        “双重判据”只剩下一条，另一条将来被改也不知道（变异检验 M9 专验这道门）。
        """
        body = _dart_body(self.src, "Widget _buildWinEquityGaugeWidget(")
        segs = [s for s in body.split(";") if "纯解析式评估" in s]
        self.assertTrue(segs, "面板已不再声明「纯解析式评估」：PVN 关闭态会被当成网络胜率")
        for s in segs:
            self.assertIn("pureAnalytical", s, msg="标题必须走判据，不能写死文案")
        judge = [s for s in body.split(";") if "pureAnalytical =" in s]
        self.assertTrue(judge, "没找到 pureAnalytical 的判据定义")
        self.assertIn("analytical+pvn", judge[0], "判据必须参考口径字段")
        self.assertIn("analyticalEquity", judge[0],
                      "判据必须含「win_equity == analytical_equity」这条数值恒等式")
        # 且面板得真的把解析式原值从 advice[0] 取出来，否则第二个判据永远是 null。
        # 这里不走 _dart_body：那不是个方法，按大括号配平定位会抹到一个任意块；
        # 按语句切就能把「取值语句」与「判据语句」分开钉住。
        wiring = [s for s in self.texts["mahjong_overlay.dart"].split(";")
                  if "analyticalEquity=" in s.replace(" ", "").replace("\n", "")]
        self.assertTrue(wiring, "面板里找不到 analyticalEquity 的取数语句："
                                "第二个判据形同虚设")
        self.assertTrue(any("analytical_equity" in s for s in wiring),
                        "payload 里的 analytical_equity 没接到面板，双判据退化成单判据")

    def test_panel_uses_band_fields_that_actually_exist_in_payload(self):
        got = set(re.findall(r"evGauge\['([a-z_]+)'\]", self.src))
        self.assertTrue(got, "Dart 源码没抓到任何 evGauge 字段：正则失效，本测试空转")
        expect = {"band", "calibrated", "note", "equity_basis", "net_ev_unit",
                  "level", "net_ev", "win_equity"}
        self.assertTrue(expect <= got, f"面板应读 {sorted(expect)}，实抓 {sorted(got)}")
        # 字段契约只能在川麻真 payload 上对账（std 帧不下发仪表盘，
        # 见 test_std_frame_does_not_fabricate_a_gauge）。
        gauge = self.sc_payload["ev_gauge"]
        for k in sorted(got):
            self.assertIn(k, gauge, f"面板读了 ev_gauge['{k}']，但 payload 没有这个键")
        df_keys = set(re.findall(r"df\['([a-z_]+)'\]", self.src))
        self.assertIn("danger_band", df_keys, "危险勋章必须读档位字段")
        hr_keys = set(re.findall(r"opp\['([a-z_]+)'\]", self.src))
        self.assertIn("tenpai_band", hr_keys, "对手透视必须读档位字段")
        # 档位字段必须在真 payload 里存在：面板若只靠 Dart 兜底词渲染，
        # Python 侧改了阈值也不会有人发现（两处口径悄悄分叉）。
        df = self.sc_payload.get("danger_flow")
        self.assertIsInstance(df, dict, f"川麻帧必须下发 danger_flow：{type(df)}")
        self.assertEqual(df["danger_band"], pb.danger_band(df["danger_level"]),
                         "payload 里的 danger_band 必须与单一来源一致")
        self.assertEqual(df["prob_kind"], "model",
                         "点炮概率必须自报类别，前端才敢不印百分号")
        hrs = self.sc_payload.get("hand_ranges")
        self.assertTrue(hrs, "川麻帧必须下发 hand_ranges，否则透视档位只能走兜底词")
        for h in hrs:
            self.assertEqual(h["tenpai_band"], pb.tenpai_band(h["tenpai_prob"]))
        # advice 条目级的 danger_flow 与顶层同源（面板两处都在读）
        top = (self.sc_payload.get("advice") or [{}])[0]
        self.assertIn("danger_band", top.get("danger_flow") or {},
                      "条目级 danger_flow 缺档位字段")

    def test_dart_fallback_words_match_python_band_tables(self):
        """Dart 兜底档位词必须与 probability_bands 逐字同表（跨语言契约）。

        本机没有 Flutter SDK，编译/分析都跑不了，面板的错字只能靠这道文本门；
        两边分叉不会报错，只会“同一个 level 在旧链路和新链路上译成两种说法”。
        """
        tables = pb.band_tables()
        for fn, key in (("_equityBandWord", "equity"), ("_dangerBandWord", "danger")):
            body = _dart_body(self.src, f"String {fn}(String level)")
            got = dict(re.findall(r"case '([a-z_]+)':\s+return '([^']+)';", body))
            self.assertTrue(got, f"{fn} 没抽到任何档位：正则失效，本测试将空转")
            self.assertEqual(got, tables[key], f"{fn} 与 Python 档位表已分叉")
            self.assertIn(tables["unknown"], body,
                          f"{fn} 缺少未知 level 的「{tables['unknown']}」兜底，不能猜一个档")
        for fn, key in (("_tenpaiBandWord", "tenpai"), ("_heldBandWord", "held")):
            body = _dart_body(self.src, f"String {fn}(double p)")
            steps = [(float(t), lbl) for t, lbl in
                     re.findall(r"if \(p < ([0-9.]+)\)\s*return '([^']+)';", body)]
            want = [tuple(x) for x in tables[key][0]]
            self.assertTrue(steps, f"{fn} 没抽到阶梯阈值：正则失效")
            self.assertEqual(steps, want, f"{fn} 的阶梯阈值/措辞与 Python 分叉")
            self.assertEqual(re.findall(r"return '([^']+)';", body)[-1], tables[key][1],
                             f"{fn} 顶档词与 Python 分叉")

    def test_panel_reads_advice_chance_keys_present(self):
        """_ukeireLabel 的 ledgerBacked 判据必须与 Python 下发键同名。"""
        self.assertIn("ukeire_chance", self.src,
                      "面板必须认识 ukeire_chance，否则 std 拆账信息在 UI 侧丢失")
        eng = make_engine("std_tdh", ["3m", "4m", "5m", "6m", "7m", "8m", "9m",
                                      "1p", "2p", "3p", "5p", "5p", "5p", "7s"],
                          ["1s", "2s"])
        payload = run_frames(eng, make_image(seed=67), 8)[-1]
        advice = payload.get("advice") or []
        self.assertTrue(advice, "前提：必须给出建议")
        # 听牌帧带 ting_chance；未听牌帧带 ukeire_chance。两者至少存在其一，
        # 否则面板永远走「≤」分支，等于账本没接上。
        self.assertTrue(any(("ting_chance" in a) or ("ukeire_chance" in a)
                            for a in advice),
                        msg="advice 里没有任何账本拆账字段，≤ 号将永久生效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
