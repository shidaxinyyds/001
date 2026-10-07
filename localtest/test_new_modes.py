# -*- coding: utf-8 -*-
"""新增玩法（第 11~20 条）与运行时鬼牌机制的规则测试。

重点不是「字段齐不齐」，而是三件更容易出错的事：
1. 新玩法的判胡结果必须与独立暴力实现一致（复用 audit_std_win 的 brute ground truth）
2. 多鬼牌（中发白三鬼）时，鬼牌绝不能出现在弃牌候选里
3. 自选鬼牌玩法的哨兵解析不得污染 MODES 原表——一旦污染，其它玩法会跟着读到脏鬼牌
"""
import os
import random
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python")))

from modes import (MODES, ALIASES, DEFAULT_MODE, LAIZI_CONFIG, available_set, get_analyzer,
                   get_laizi_explicit, get_laizi_set, get_mode, is_known_mode, is_sichuan_family,
                   native_solver_ready, set_laizi_explicit)
from platforms import PLATFORMS, get_supported_modes
from std.std_analyzer import StdAnalyzer
from audit_std_win import brute_win, gen_structured

ROOT = os.path.abspath(os.path.join(HERE, ".."))
MODE_DART = os.path.join(ROOT, "lib", "mode_store.dart")
PLATFORM_DART = os.path.join(ROOT, "lib", "platform_store.dart")
JAVA_IP = os.path.join(ROOT, "android", "app", "src", "main", "java",
                       "com", "example", "auto_vision", "ImageProcessor.java")
JAVA_MA = os.path.join(ROOT, "android", "app", "src", "main", "java",
                       "com", "example", "auto_vision", "MainActivity.java")
OVERLAY_DART = os.path.join(ROOT, "lib", "overlays", "mahjong_overlay.dart")
PY_ENGINE = os.path.join(ROOT, "android", "app", "src", "main", "python", "engine", "engine.py")


def _str_list(body):
    return re.findall(r"'([^']+)'", body)


def _dart_block(text, decl):
    """抽取 `decl = [ ... ];` 形式的字面量列表（ Dart 侧只有 const 列表）。"""
    m = re.search(re.escape(decl) + r"\s*=\s*\[(.*?)\];", text, re.S)
    return _str_list(m.group(1)) if m else None

NEW_KEYS = ["wz_tdh", "hz_all", "fc_all", "zfb_bd", "pp_zz",
            "sc_xz_3p", "mj_2p", "mj_3p", "hz_ne", "cf_wild"]


class TestNewModeStructure(unittest.TestCase):
    def test_all_new_modes_registered(self):
        for k in NEW_KEYS:
            self.assertIn(k, MODES, f"未登记玩法 {k}")
            self.assertTrue(is_known_mode(k), f"{k} 未通过 is_known_mode")

    def test_field_contract(self):
        """每个新玩法的几何/牌集契约：available 落在 34 型内且不与鬼牌冲突。"""
        for k in NEW_KEYS:
            p = MODES[k]
            for required in ("name", "players", "available", "hand_sizes", "wall",
                             "dingque", "laizi", "analyzer", "sequences",
                             "seven_pairs", "kokushi"):
                self.assertIn(required, p, f"{k} 缺字段 {required}")
            avail = set(p["available"])
            self.assertTrue(avail, f"{k} 牌集为空")
            self.assertTrue(all(0 <= i < 34 for i in avail), f"{k} 牌集越界")
            self.assertEqual(len(avail), len(p["available"]), f"{k} 牌集有重复")
            # 鬼牌必须是牌集内的牌，否则「红中为鬼」却不含红中=规则自相矛盾
            lz = p["laizi"]
            if lz is not None and lz != LAIZI_CONFIG:
                items = lz if isinstance(lz, (list, tuple)) else [lz]
                for i in items:
                    self.assertIn(i, avail, f"{k}: 鬼牌 {i} 不在牌集内")
            self.assertIn(p["analyzer"], ("std", "sichuan"), f"{k} analyzer 非法")
            self.assertLessEqual(p["wall"], 144)

    def test_wall_matches_tileset_for_every_mode(self):
        """wall == 牌集种类数 × 4 对**全部**玩法成立（含 legacy 条目）。

        wall 是悬浮窗「还剩多少牌」的基准，也与牌河物理守恒同源。legacy `sc` 曾写
        成 28 类牌集 + wall 108（自相矛盾），只因 ALIASES 把它指向 sc_xz 才没在运行
        时暴露——只校验 NEW_KEYS 会漏掉这类旧脏数据。
        """
        for k, p in MODES.items():
            self.assertEqual(p["wall"], len(p["available"]) * 4,
                             f"{k} wall({p['wall']}) 与牌集张数({len(p['available'])}*4) 不一致")

    def test_unknown_mode_falls_back_and_is_detectable(self):
        # get_mode 对未知 key 静默回退（保引擎不崩），但 is_known_mode 必须能识别
        self.assertFalse(is_known_mode("non_existent_mode_999"))
        self.assertEqual(get_mode("non_existent_mode_999"), get_mode(DEFAULT_MODE))

    def test_sichuan_family_matches_analyzer(self):
        """is_sichuan_family 改为读 analyzer 字段后，必须与旧硬编码集合语义一致。"""
        legacy = {"sc_hz", "sc_xz", "sc_xl", "gy_zj"}
        for k in MODES:
            norm = ALIASES.get(k, k)
            expect = get_analyzer(k) == "sichuan" or norm in legacy
            self.assertEqual(is_sichuan_family(k), expect, f"{k} 分流判断不一致")
        self.assertTrue(is_sichuan_family("sc_xz_3p"), "三人血战必须走 SichuanAnalyzer")
        self.assertFalse(is_sichuan_family("wz_tdh"))


class TestNewModeWinParity(unittest.TestCase):
    """新玩法判胡与独立 brute 对拍：这是「逻辑正确」的直接证据。

    注意样本必须用构造式生成。早期版本用随机 14 张，实测 9 个玩法 × 120 手只跑出
    1 手胡牌——两个实现只在「都不能胡」上一致，对拍完全空转。同时必须卡住正例
    数量，否则造牌器坏了也会显示「0 差异」。
    """

    def setUp(self):
        self.rng = random.Random(20261003)

    def test_win_parity_against_brute(self):
        for k in NEW_KEYS:
            rules = get_mode(k)
            if rules.get("analyzer") != "std":
                continue
            lz = rules.get("laizi")
            lz_list = [] if lz is None or lz == LAIZI_CONFIG else (
                list(lz) if isinstance(lz, (list, tuple)) else [lz])
            diff, n_pos = 0, 0
            for shape in ("mix", "pung", "seq", "pairs", "kokushi"):
                for w in ([0, 1, 2, 3] if lz_list else [0]):
                    for break_n in (0, 1, 2):
                        for _ in range(4):
                            counts = gen_structured(rules["available"], lz_list,
                                                    rules, self.rng, shape, w, break_n)
                            if counts is None:
                                continue
                            mine = brute_win(counts, 0, rules, rng=self.rng)
                            if mine == "unsat":
                                continue
                            theirs = StdAnalyzer.can_win(list(counts), 0, rules)
                            if mine:
                                n_pos += 1
                            if mine != theirs:
                                diff += 1
            self.assertEqual(diff, 0, f"{k} 判胡与 brute 有 {diff} 处不一致")
            # 正例不够就是“两边都说不能胡”的伪通过，必须报失败
            self.assertGreaterEqual(n_pos, 8,
                                    f"{k} 对拍只有 {n_pos} 个正例，不足以证明判胡正确")


class TestMultiLaizi(unittest.TestCase):
    def test_three_ghosts_never_discard_candidates(self):
        """三鬼玩法：鬼牌不得出现在任何弃牌建议里（analyze_discards 的 tile 集合）。"""
        rules = get_mode("zfb_bd")
        counts = [0] * 34
        # 必须是 14 张（3k+2）出牌态：analyze_discards 对其它张数直接返回空列表，
        # 那样下面的 assertNotIn 会全部“通过”却什也没验。
        for t in [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 28, 33]:
            counts[t] += 1
        counts[31] += 1   # 白板鬼
        counts[32] += 1   # 发财鬼
        self.assertEqual(sum(counts), 14, f"造牌失败 sum={sum(counts)}")
        res = StdAnalyzer.analyze_discards(list(counts), rules, rules["available"])
        self.assertTrue(res, "14 张出牌态应给出建议，否则本测试是假通过")
        tiles = {r["tile"] for r in res}
        self.assertNotIn("5z", tiles, "白板(鬼)被当成弃牌候选")
        self.assertNotIn("6z", tiles, "发财(鬼)被当成弃牌候选")
        self.assertNotIn("7z", tiles, "红中(鬼)被当成弃牌候选")

    def test_get_laizi_set_normalizes(self):
        self.assertEqual(get_laizi_set("std_tdh"), frozenset())
        self.assertEqual(get_laizi_set("hz_all"), frozenset([33]))
        self.assertEqual(get_laizi_set("zfb_bd"), frozenset([31, 32, 33]))

    def test_waits_exclude_ghost(self):
        """听口不得把鬼牌报成「等的牌」：鬼牌不能被别家打来吃。"""
        rules = get_mode("hz_all")
        counts = [0] * 34
        for t in [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 33, 33]:
            counts[t] += 1
        waits = StdAnalyzer.find_waits(counts, 0, rules, rules["available"])
        self.assertNotIn(33, waits, "鬼牌被报成等张")


class TestRuntimeLaizi(unittest.TestCase):
    def tearDown(self):
        set_laizi_explicit([])   # 复位，避免污染其它用例

    def test_sentinel_resolves_and_table_not_mutated(self):
        self.assertEqual(MODES["cf_wild"]["laizi"], LAIZI_CONFIG, "原表本应是哨兵")
        ok = set_laizi_explicit([33])
        self.assertTrue(ok)
        self.assertEqual(get_mode("cf_wild")["laizi"], [33])
        self.assertEqual(get_laizi_set("cf_wild"), frozenset([33]))
        # 关键：解析发生在副本上，原表仍是哨兵
        self.assertEqual(MODES["cf_wild"]["laizi"], LAIZI_CONFIG)
        # 且其它玩法不受影响
        self.assertEqual(get_mode("std_tdh").get("laizi"), None)

    def test_two_ghosts_via_runtime(self):
        self.assertTrue(set_laizi_explicit([5, 12]))
        rules = get_mode("cf_wild")
        self.assertEqual(sorted(rules["laizi"]), [5, 12])
        self.assertEqual(get_laizi_set("cf_wild"), frozenset([5, 12]))

    def test_empty_means_no_ghost(self):
        self.assertTrue(set_laizi_explicit([]))
        self.assertEqual(get_mode("cf_wild")["laizi"], [])
        self.assertEqual(get_laizi_set("cf_wild"), frozenset())

    def test_dirty_input_rejected_atomically(self):
        # 越界/非法必须整体拒绝，不能"注入一半"
        set_laizi_explicit([3])
        self.assertFalse(set_laizi_explicit([3, 99]))
        self.assertEqual(get_laizi_explicit(), [3], "脏输入污染了已生效的鬼牌")
        self.assertFalse(set_laizi_explicit(["abc"]))
        self.assertEqual(get_laizi_explicit(), [3])

    def test_cf_wild_plays_without_injection(self):
        """未注入鬼牌时，自选鬼牌玩法应等价于全牌推倒胡（不崩、能出建议）。"""
        set_laizi_explicit([])
        rules = get_mode("cf_wild")
        counts = [0] * 34
        for t in [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 28, 33, 33, 5]:
            counts[t] += 1
        res = StdAnalyzer.analyze_discards(list(counts), rules, rules["available"])
        self.assertTrue(res, "未注入鬼牌时应仍能给出出牌建议")
        for r in res:
            self.assertIn(r["shanten"], list(range(0, 9)))


class TestEveryModeAnalyzable(unittest.TestCase):
    """全量玩法（含 legacy 之外的 20 条）都能跑通一次出牌分析，不抛异常。"""

    def test_analyze_discards_no_crash(self):
        rng = random.Random(7)
        for k, p in MODES.items():
            if not p.get("analyzer"):
                continue      # legacy 4p/3p/2p/sc 无 analyzer，走旧回退路径
            if p.get("analyzer") == "sichuan":
                continue      # 川麻走 SichuanAnalyzer（28 型口径），由 audit_sichuan_win 覆盖
            rules = get_mode(k)
            avail = rules["available"]
            lz = rules.get("laizi")
            lz_set = set() if lz in (None, LAIZI_CONFIG) else set(
                lz if isinstance(lz, (list, tuple)) else [lz])
            pool = [i for i in avail if i not in lz_set]
            if not pool:
                self.fail(f"{k}: 扣除鬼牌后无可用牌")
            counts = [0] * 34
            for i in lz_set:
                counts[i] = 1
            need = 14 - len(lz_set)   # 凑足 14 张（3k+2）出牌态，否则分析直接返回空
            while need > 0:
                t = rng.choice(pool)
                if counts[t] >= 4:
                    continue
                counts[t] += 1
                need -= 1
            res = StdAnalyzer.analyze_discards(counts, rules, avail)
            self.assertTrue(res, f"{k} 出牌分析返回空")
            self.assertLessEqual(len(res), 34)


class TestDartPythonParity(unittest.TestCase):
    """Dart UI 目录与 Python 规则表必须逐项一致。

    这是最容易出“静默坑”的地方：get_mode() 对未知 key 不报错，而是回退
    DEFAULT_MODE。所以只要 Dart 多写/写错一个 key，用户看到的花样名字与实际
    生效的规则就完全不同，而且不会有任何异常。下面把两侧当成同一份数据校验。
    """

    @classmethod
    def setUpClass(cls):
        with open(MODE_DART, encoding="utf-8") as f:
            cls.mode_src = f.read()
        with open(PLATFORM_DART, encoding="utf-8") as f:
            cls.plat_src = f.read()
        cls.dart_mode_keys = re.findall(r"key:\s*'([^']+)'", cls.mode_src)
        cls.dart_allowed = _dart_block(cls.mode_src, "static const List<String> allowed")

    def test_dart_mode_keys_all_known(self):
        self.assertTrue(self.dart_mode_keys, "没从 mode_store.dart 解析到任何 key，本测试已失效")
        unknown = [k for k in self.dart_mode_keys if not is_known_mode(k)]
        self.assertEqual(unknown, [], f"Dart 有 {unknown} 在 Python 不存在 → 会被静默回退成 {DEFAULT_MODE}")

    def test_dart_allowed_covers_all_displayed_modes(self):
        # allModes 里能被点到的 key，必须同时在 allowed 里（否则 GameMode.set 直接返回 false）
        missing = [k for k in self.dart_mode_keys if k not in self.dart_allowed]
        self.assertEqual(missing, [], f"{missing} 在目录里但不在 allowed 里，选了也切不过去")
        ghost = [k for k in self.dart_allowed if not is_known_mode(k)]
        self.assertEqual(ghost, [], f"allowed 含未知 key：{ghost}")

    def test_python_modes_not_in_dart_are_deliberate(self):
        """反向：Python 有而 Dart 没上架的玩法，必须是被显式标注为未开放的。"""
        hidden = set(MODES) - set(self.dart_mode_keys) - set(self.dart_allowed)
        # legacy 别名与 cf_wild（无注入通道）允许不在 UI；其余必须上架
        allowed_hidden = {"sc", "4p", "3p", "2p", "cf_wild"}
        self.assertTrue(hidden <= allowed_hidden,
                        f"{sorted(hidden - allowed_hidden)} 既不在 UI 也无线上回退通道，属于漏上架")

    def test_platform_supported_modes_identical(self):
        """每个平台的 supportedModes / defaultMode / handRoi 两侧逐项相等。"""
        # 不能用 split("GamePlatformInfo(")：构造函数声明 `const GamePlatformInfo({`
        # 也会被切出一块没有 key 的碎片。这里用锚定 `key:` 的正则只取真实条目。
        entries = list(re.finditer(
            r"GamePlatformInfo\(\s*key:\s*'([^']+)'(.*?)\n    \),", self.plat_src, re.S))
        self.assertEqual(len(entries), len(PLATFORMS),
                         f"Dart 解析到 {len(entries)} 个平台，Python 有 {len(PLATFORMS)} 个")
        for m in entries:
            key, body = m.group(1), m.group(2)
            self.assertIn(key, PLATFORMS, f"Dart 平台 {key} 在 Python 不存在")
            sm = re.findall(r"'([^']+)'", re.search(
                r"supportedModes:\s*\[(.*?)\],", body, re.S).group(1))
            self.assertEqual(sm, get_supported_modes(key),
                             f"{key} 的 supportedModes 两侧不一致")
            dm = re.search(r"defaultMode:\s*'([^']+)'", body).group(1)
            self.assertEqual(dm, PLATFORMS[key]["default_mode"], f"{key} defaultMode 不一致")
            roi = [float(x) for x in re.findall(r"([0-9]+\.[0-9]+)", re.search(
                r"handRoi:\s*\[(.*?)\],", body, re.S).group(1))]
            self.assertEqual(len(roi), 4, f"{key} handRoi 没解析到 4 个值：{roi}")
            self.assertEqual(roi, list(PLATFORMS[key]["hand_roi"]), f"{key} handRoi 不一致")

    def test_platform_supported_modes_are_known_and_analyzable(self):
        for key in PLATFORMS:
            for m in get_supported_modes(key):
                self.assertTrue(is_known_mode(m), f"{key} 列了未知玩法 {m}")
                self.assertTrue(get_analyzer(m), f"{key} 的 {m} 无 analyzer，只能走旧回退")
            self.assertTrue(is_known_mode(PLATFORMS[key]["default_mode"]))

    def test_default_mode_single_source(self):
        """默认玩法只能有一条：Python modes.DEFAULT_MODE 与 Dart GameMode.defaultMode。

        悬浮窗曾经自己又写了一个 `selectedMode = 'sc'`，而 'sc' 经别名解析是 sc_xz
        （108 张无字），与真默认 sc_hz（112 张带红中）不是同一个玩法：首帧前
        面板按错的 wall 显示剩余牌数，标题也显示另一个玩法。
        """
        m = re.search(r"static\s+const\s+String\s+defaultMode\s*=\s*'([^']+)'", self.mode_src)
        self.assertIsNotNone(m, "没从 mode_store.dart 解析到 defaultMode，本测试已失效")
        self.assertEqual(m.group(1), DEFAULT_MODE,
                         "Dart defaultMode 与 Python DEFAULT_MODE 不同一条")
        self.assertTrue(is_known_mode(m.group(1)))
        with open(OVERLAY_DART, encoding="utf-8") as f:
            overlay = f.read()
        self.assertIn("String selectedMode = GameMode.defaultMode;", overlay,
                      "悬浮窗又自己写死默认玩法 key 了，应从 GameMode.defaultMode 取")

    def test_java_reads_mode_file_without_key_whitelist(self):
        """Java 读共享玩法文件不得回成枚举白名单（只允许字符集校验）。

        MainActivity 旧注释声称“必须是 2p|3p|4p”，代码实为 [a-z0-9_]+。注释必须跟
        一致，否则下一个维护者会“照注释修好”它，把 20 个新玩法全部静默回退。
        """
        with open(JAVA_MA, encoding="utf-8") as f:
            ma = f.read()
        block = ma[ma.index("private String readModeFile()"):ma.index("private int writeModeFile")]
        code = re.sub(r"//[^\n]*", "", block)
        self.assertNotIn("2p|3p|4p", code, "readModeFile 又变成枚举白名单")
        self.assertIn("sc_hz", code, "readModeFile 回退值不再与 Python DEFAULT_MODE 一致")


class TestRecognitionCandidateSet(unittest.TestCase):
    """识别候选集必须跟玩法牌集走，而不是按平台猜字牌。

    classify_tile 里“不传 avail 就按 PLATFORM_FULL_HONORS 定字牌”的写法，让
    YOLO 覆盖层/牌桌探针/任选牌弹窗这些入口在全牌玩法下永远读不到
    东南西北白发；而上架 20 个玩法后这种“选得到、读不到”的玩法一大片。
    """

    @classmethod
    def setUpClass(cls):
        from recognition.tencent_grid_detector import resolve_candidate_tiles
        cls.resolve = staticmethod(resolve_candidate_tiles)

    def test_sichuan_modes_keep_old_candidate_set(self):
        """川麻类候选集不引入新字牌风险：绝不放开 1z..6z，红中玩法只多一个 7z。

        旧平台兜底是“base + 7z”，对手 27 张的血战来说其实宽了一个 7z；现在按
        玩法牌集走会更窄（sc_xz 不再把 7z 放进候选），这是收紧而不是放宽。
        """
        base = {f"{i}m" for i in range(1, 10)} | {f"{i}p" for i in range(1, 10)} \
            | {f"{i}s" for i in range(1, 10)}
        for k in ("sc_hz", "sc_xl", "gy_zj", "sc_xz"):
            got = self.resolve(avail=available_set(k))
            self.assertTrue(base <= got, f"{k} 丢了数牌候选")
            self.assertFalse(got & {"1z", "2z", "3z", "4z", "5z", "6z"},
                             f"{k} 候选集里出现了风牌/白板发财，8p/9p 误配风险回来了")
            self.assertEqual(bool(got & {"7z"}), k != "sc_xz",
                             f"{k} 的 7z 候选与牌集不符")

    def test_full_honor_modes_get_all_honors(self):
        for k in ("std_tdh", "hz_bd", "fc_all", "zfb_bd", "db_qh"):
            got = self.resolve(avail=available_set(k))
            for z in ("1z", "2z", "3z", "4z", "5z", "6z", "7z"):
                self.assertIn(z, got, f"{k} 需要全字牌候选集，{z} 缺失")

    def test_mode_tiles_beat_platform_guess(self):
        """不传 avail 的调用点（覆盖层/探针）现在拿当前玩法牌集，不再被平台卡住。"""
        mode_tiles = self.resolve(avail=available_set("std_tdh"))
        got = self.resolve(avail=None, mode_tiles=mode_tiles, full_honors=False)
        self.assertIn("1z", got, "platform 兜底仍优先于玩法牌集，推入没生效")
        # 完全无信息时才回到按平台兜底（历史行为）
        self.assertNotIn("1z", self.resolve(avail=None, mode_tiles=None, full_honors=False))
        self.assertIn("1z", self.resolve(avail=None, mode_tiles=None, full_honors=True))

    def test_empty_candidate_set_never_returned(self):
        """空候选集会让 classify_tile 走到 next(iter(set())) 抛 StopIteration（整帧挂）。"""
        for bad in ([], set(), frozenset()):
            got = self.resolve(avail=bad, mode_tiles=None, full_honors=False)
            self.assertTrue(got, f"候选集为空（avail={bad!r}）")
        # 不传 avail 且无玩法信息时也不能返回空
        self.assertTrue(self.resolve(avail=None, mode_tiles=None, full_honors=True))

    def test_detector_and_engine_wiring(self):
        """YOLO 检测器必须把牌集转给覆盖层的 helper（它拿不到 avail 参数）。"""
        from recognition.yolo_detector import YOLODetector

        calls = {}

        class _Helper:
            def set_mode_tiles(self, avail):
                calls["avail"] = avail

        class _Holder:
            _phase_helper = _Helper()

        YOLODetector.set_mode_tiles(_Holder(), [0, 1, 33])
        self.assertEqual(calls.get("avail"), [0, 1, 33],
                         "YOLODetector 没把玩法牌集转给模板覆盖层 helper")

        with open(PY_ENGINE, encoding="utf-8") as f:
            eng = f.read()
        self.assertIn("set_mode_tiles", eng,
                      "engine 未把当前玩法牌集推给识别器（跨层不同步）")


class TestNativeHandoff(unittest.TestCase):
    """C++ NativeEngine 的接管边界。

    native 只实现了「27 型万筒条 + 定缺」：没有鬼牌概念，且 Java 侧
    parseMpszToTiles 只映射 m/p/s。一旦它接管带鬼牌的玩法，手牌会被静默截短后
    照常算向听，并把 Python 已经算对的 shanten/advice/hand 整体覆写。这类错误
    不报错、不崩、看起来“很快”，所以必须用机器守卫钉住路由判据。
    """

    def setUp(self):
        with open(JAVA_IP, encoding="utf-8") as f:
            self.java_src = f.read()
        with open(PY_ENGINE, encoding="utf-8") as f:
            self.engine_src = f.read()

    def test_capability_flag_tracks_native_actual_coverage(self):
        """被标为可接管的玩法，牌集必须落在 native 认识的 0..26 内（直接对能力校
        验，而不是复述实现里的条件）。"""
        ready = [k for k in MODES if native_solver_ready(k)]
        for k in ready:
            self.assertTrue(set(MODES[k]["available"]) <= set(range(27)),
                            f"{k} 被标为 native 可用，但牌集含 native 表达不了的牌")
            self.assertFalse(get_laizi_set(k), f"{k} 带鬼牌却允许 native 接管")
        # 主流带鬼玩法必须落回 Python
        for k in ("sc_hz", "sc_xl", "gy_zj", "hz_bd", "zfb_bd", "cf_wild"):
            self.assertFalse(native_solver_ready(k), f"{k} 不得交给 native")
        self.assertTrue(native_solver_ready("sc_xz"), "纯川麻血战应允许 native 加速")

    def test_java_no_longer_routes_by_mode_prefix(self):
        # 先剥行注释：注释里需要引用旧写法作为说明，不应以文本存在与否误报。
        code = re.sub(r"//[^\n]*", "", self.java_src)
        self.assertNotIn('startsWith("sc")', code,
                         "ImageProcessor 又用 key 前缀决定 native 接管了：前缀判据会把"
                         "鬼牌玩法一起交给川麻口径")
        self.assertIn("native_ready", self.java_src,
                      "ImageProcessor 未读 Python 下发的 native_ready 判据")
        self.assertIn("handMappable", self.java_src,
                      "缺少「手牌含 native 无法映射的牌则不接管」兜底守卫")

    def test_engine_payload_carries_flag_on_every_returned_result(self):
        """三个结果出口（正常帧/待机帧/错误帧）都必须带判据，否则缺字段的帧会
        被 Java 当成“不走 native”，造成同一局里两种口径来回跳。"""
        self.assertGreaterEqual(self.engine_src.count('"native_ready"'), 3,
                                "engine.py 的结果负载未在所有出口写入 native_ready")
        self.assertIn("native_solver_ready", self.engine_src,
                      "engine.py 未接入 modes.native_solver_ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
