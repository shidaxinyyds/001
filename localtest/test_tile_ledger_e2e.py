# -*- coding: utf-8 -*-
"""牌局账本（tile_ledger）端到端接线测试：证明它真的走到了决策层，而不是空转。

单元测试只能证明账本本身算得对；本文件用 process() 全链路（假 detector + 假牌河）
锁住四件在真实链路上最容易悄悄失效的事：

1. 账本确实到达 analyzer：建议 reason 必须是账本口径的措辞（旧口径没有「共余/牌墙还能撑」）。
   这类断言专门防「except Exception: pass 把升级静默降级回旧行为」——功能没接上但测试全绿。
2. payload 必须下发 tile_ledger / ting_chance，且 JSON 里恒等式仍成立（跨进程不丢精度）。
3. 守恒违例（牌河同一型看到 5 张）必须让整帧**不给建议**并标脏；而同局 4 张（合法）
   必须照给建议 —— 一正一反成对，否则「硬门」可能是永远不响的空守卫（变异检验）。
4. 川麻末期警报必须用**牌墙真值**而不是「未现总数」（后者含对手手上 39 张，会让
   查大叫警报整整晚一截才响）。

运行：py -3.10 localtest/test_tile_ledger_e2e.py
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

import numpy as np  # noqa: E402

from engine import engine as engine_mod  # noqa: E402
from engine.engine import Engine, DIRTY_HEAL_FRAMES  # noqa: E402
from modes import available_set, get_laizi_set, hand_sizes, set_mode_explicit  # noqa: E402
from tile_ledger import ledger_for_sichuan, ting_chance  # noqa: E402
from discards_tiebreak import order_key, tie_tail  # noqa: E402


def make_image(h=600, w=1100, seed=7):
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), (40, 115, 50), dtype=np.uint8)
    noise = rng.integers(-5, 6, (h, w, 3), dtype=np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return img


def make_engine(mode: str, hand_labels, river_labels, meld_labels=None):
    """最小可用 Engine：手牌行由固定标签喂入，牌河/副露走生产同源的全图扫描入口。

    牌河必须用 `_run_bg_river_and_melds` 的返回形式注入（而非自己猜牌河行的像素带）：
    真机上 `process()` 会先把图裁剪/缩放到工作分辨率，手拼的 rect 坐标会整体会错位，
    测出来的“牌河没计入”其实是坐标错位的假现象。走全图扫描入口才能碰到与线上
    一致的 _update_visual_ledger 双账本合并 + 两帧确认路径。
    """
    set_mode_explicit(mode)
    engine_mod.load_mode = lambda: mode
    eng = Engine()

    class FakeDetector:
        def __init__(self):
            self.last_top_score = 0.85
            self.last_screen = (1100, 600)
            self._glyphs = type("G", (), {"nums": {"a": 1}})()
            self._styles = type("S", (), {"tpls": [1]})()

        def detect_all_rows(self, image, **_kw):
            # rect 按当帧实际图尺寸生成（引擎内部可能已经缩放/裁切过）。
            ih, iw = image.shape[:2]
            th = max(12, int(ih * 0.16))
            tw = max(7, int(th * 0.56))
            step = tw + 4
            row = [((int(min(8, iw * 0.01)) + i * step, int(ih * 0.62), tw, th),
                    lbl, 0.92) for i, lbl in enumerate(hand_labels)]
            self.last_detections = [row]
            return [row]

    det = FakeDetector()
    eng.get_detector = lambda: det  # type: ignore
    eng._is_mahjong_table = lambda image: True  # type: ignore
    # 牌河：(mpsz, 区域)；副露：(区域, mpsz, 张数)。区域轮流分给三家，贴近真实分布。
    zones = ("right", "top", "left")
    river_entries = [(lab, zones[i % 3]) for i, lab in enumerate(river_labels)]
    meld_entries = [(zones[i % 3], lab, 3) for i, lab in enumerate(meld_labels or [])]
    eng._run_bg_river_and_melds = (  # type: ignore
        lambda *a, **k: (list(river_entries), list(meld_entries)))
    eng._tile_voter = engine_mod._TileVoter(window=engine_mod.VOTE_WINDOW)
    eng._frame_skipper = engine_mod._FrameSkipper()
    eng._motion_guard = engine_mod._MotionGuard()
    eng.mode = mode
    eng._prev_mode = mode
    return eng


def run_one(eng, image):
    """跑一帧完整识别并返回 payload。

    两处门控都必须按帧解除，否则测的不是决策链：
    * 帧差去重：画面静止时直接回吐上一份 payload，硬门与自愈计数根本不推进；
    * 牌河区差分门控：牌河未变就跳过全图扫描，两帧确认永远凑不满。
    """
    eng._frame_skipper._last_sig = None   # 等同于“画面确有变化”
    eng._river_scan_sig = None            # 等同于“牌河重扫一次”
    return json.loads(eng.process(image).result)


def run_frames(eng, image, n):
    """连跑 n 帧，返回每帧 payload。"""
    return [run_one(eng, image) for _ in range(n)]


# 14 张：123m 456m 789m 123p 55p + 5p（打 5p 即单吊听 5p 的形）
TENPAI_HAND = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
               "1p", "2p", "3p", "5p", "5p"]


class TestLedgerReachesDecision(unittest.TestCase):
    """账本必须真的改变下发内容，而不只是存在于内存里。"""

    def setUp(self):
        self.img = make_image(seed=11)

    def test_ledger_downlinked_and_identity_holds_through_json(self):
        eng = make_engine("std_tdh", TENPAI_HAND, ["2p", "3p"])
        payload = run_frames(eng, self.img, 8)[-1]
        led = payload.get("tile_ledger")
        self.assertIsNotNone(led, "payload 必须下发 tile_ledger（未下发=账本没接进引擎）")
        self.assertTrue(led["ok"], f"合法局面不该被判脏：{led['violations']}")
        # 恒等式跨 JSON 仍成立：未现 = 牌墙 + 对手手上；逐型 unseen = wall_lo + opp_hi
        self.assertEqual(led["unseen_total"], led["wall_remaining"] + led["standing_total"])
        # 全量守恒：已现 + 未现 = 本玩法牌集总量（std_tdh = 34 型 × 4 = 136）
        self.assertEqual(led["seen_total"] + led["unseen_total"],
                         len(available_set("std_tdh")) * 4)
        for idx, rec in led["by_type"].items():
            self.assertEqual(rec["unseen"], rec["wall"] + rec["in_hands"],
                             msg=f"{idx} 拆账不闭合：{rec}")
            self.assertLessEqual(rec["wall"], rec["unseen"])
            self.assertLessEqual(rec["in_hands"], rec["unseen"])

    def test_ledger_degradation_leaves_a_trace(self):
        """记账构造失败必须在 diag.ledger_error 里留痕，不得静默退回旧口径。

        川麻链路就吃过这个坑：ledger_for_sichuan 的形参名拼错抛 TypeError，被
        `except Exception: return None` 吞掉 → 建议照旧、测试照绿、账本永远为空。
        变异检验（拨入一个必然失败）同时证明这条留痕不是空转。
        """
        import trainer.trainer as trainer_mod

        eng = make_engine("std_tdh", TENPAI_HAND, ["2p", "3p"])
        ok_payload = run_frames(eng, self.img, 8)[-1]
        self.assertIsNone(ok_payload["diag"]["ledger_error"], "正常链路不该带降级痕迹")
        self.assertIsNotNone(ok_payload["tile_ledger"])

        real = trainer_mod.build_ledger

        def boom(*_a, **_k):
            raise RuntimeError("注入：记账构造失败")

        trainer_mod.build_ledger = boom
        try:
            eng._advice_key = None      # 绕开建议缓存，逼本帧真重算一次
            payload = run_one(eng, self.img)
        finally:
            trainer_mod.build_ledger = real
        self.assertIn("注入", payload["diag"]["ledger_error"] or "",
                      msg="记账失败必须能在端上诊断出来，而不是默默变回旧文案")
        self.assertIsNone(payload["tile_ledger"], "记账失败时不得凭空下发账本")
        self.assertTrue(payload["advice"], "记账失败只降级文案，不该把建议一起吃掉")

    def test_advice_reason_uses_ledger_vocabulary(self):
        """听牌建议的 reason 必须是账本口径（旧文案不会说「共余/牌墙还能撑」）。"""
        eng = make_engine("std_tdh", TENPAI_HAND, ["2p", "3p"])
        payload = run_frames(eng, self.img, 8)[-1]
        advice = payload.get("advice") or []
        self.assertTrue(advice, f"应有出牌建议，status={payload.get('status')}")
        top = advice[0]
        reason = top.get("reason", "")
        self.assertIn("共余", reason, f"reason 未走账本口径：{reason!r}")
        self.assertIn("牌墙还能撑", reason, f"reason 缺少牌墙轮数：{reason!r}")
        # reason 里的每个数字都必须能在随条目下发的 ting_chance 里指回字段
        ch = top.get("ting_chance")
        self.assertIsNotNone(ch, "听牌条目必须带 ting_chance")
        self.assertIn(str(ch["total_unseen"]), reason)
        self.assertIn(str(ch["rounds_left"]), reason)
        # 面板结论字段（payload 顶层）与条目同源于同一份账本，不许两处数字打架
        panel = payload.get("ting_chance")
        self.assertIsNotNone(panel, "payload 必须下发顶层 ting_chance")
        self.assertEqual(panel["total_unseen"], ch["total_unseen"])
        self.assertEqual(panel["wall_only"], ch["wall_only"])
        # 双策略路线卡上那句「进张 N 张」同样不许脱离账本：听牌时必须与 reason 同源
        fast = payload.get("fast_advice") or {}
        self.assertIn("共余", str(fast.get("desc") or ""),
                      f"极速流卡仍在报裸进张数，与 reason 两处口径打架：{fast}")

    def test_dingque_suit_upper_bound_zero_reaches_analyzer(self):
        """川麻定缺门：所有对手都定缺的那一门，在别人手上界必须塌成 0。"""
        counts = [0] * 28
        for t in range(9):
            counts[t] = 1                    # 1~9 万（14 张：9+3+2）
        for t in range(9, 12):
            counts[t] = 1                    # 123 筒
        counts[13] = 2                       # 5p 对
        avail = sorted(available_set("sc_xz"))
        led = ledger_for_sichuan(
            counts, [0] * 34, [0] * 34,
            available34=avail, laizi34=sorted(get_laizi_set("sc_xz")),
            opponents=[(0, 0)] * 3, standings=[10, 10, 10],
            expected_hand_sizes=hand_sizes("sc_xz"))
        self.assertTrue(led["ok"], f"{led['violations']}")
        for i in range(9):                   # 万子（门 0）：全定缺 → 上界 0
            self.assertEqual(led["by_type"][i]["opp_hi"], 0,
                             msg="全定缺该门时「在别人手上」上界必须是 0")
            if led["by_type"][i]["unseen"] > 0:
                self.assertTrue(led["by_type"][i]["wall_only"])
        for i in range(9, 18):               # 筒子没定缺 → 仍可能在对手手上
            self.assertGreater(led["by_type"][i]["opp_hi"], 0)
        ch = ting_chance(led, [4])           # 5 万（索引 4）
        self.assertEqual(ch["wall_only"], ch["total_unseen"])
        self.assertEqual(ch["discardable"], 0)
        self.assertIn("只能自摸", ch["text"])


class TestConservationHardGate(unittest.TestCase):
    """守恒违例 = 整帧拒答；合法上限内 = 必须照答（正反例成对，防空守卫）。"""

    def setUp(self):
        self.img = make_image(seed=23)

    def _hand_no_1m(self):
        return ["3m", "4m", "5m", "6m", "7m", "8m", "9m",
                "1p", "2p", "3p", "5p", "5p", "5p", "7s"]

    def test_four_copies_is_legal_and_still_advises(self):
        """变异检验的「正例」：一种牌 4 张是实物上限，绝不能被判脏。

        如果这条也拒答，说明下一条的拒答根本不是守恒门抓出来的，守卫是空转。
        """
        eng = make_engine("std_tdh", self._hand_no_1m(), ["1m"] * 4)
        payload = run_frames(eng, self.img, 8)[-1]
        led = payload["tile_ledger"]
        self.assertTrue(led["ok"], f"4 张同型是合法的，不该判脏：{led['violations']}")
        self.assertTrue(payload["advice"], "合法帧必须给建议")

    def test_five_copies_rejects_whole_frame(self):
        """牌河同一型看到 5 张 = 物理不可能 → 本帧一条建议都不给。"""
        eng = make_engine("std_tdh", self._hand_no_1m(), ["1m"] * 5)
        frames = run_frames(eng, self.img, 8)
        dirty = [p for p in frames if not p["tile_ledger"]["ok"]]
        self.assertTrue(dirty, "5 张同型必须被账本判脏（否则硬门永远不响）")
        p = dirty[-1]
        self.assertEqual(p["advice"], [], "脏帧必须拒答")
        self.assertEqual(p["best"], "")
        self.assertIn("自相矛盾", p["message"])
        kinds = {v["kind"] for v in p["tile_ledger"]["violations"]}
        self.assertIn("over_four", kinds)

    def test_dirty_frame_self_heals_and_resumes(self):
        """连续脏满 DIRTY_HEAL_FRAMES 帧后必须剪除误识张数，并且**一次治好**。

        自愈那一帧本身仍不给建议：它的建议是在错账上算出来的，改完账就该重算；
        但下一帧必须恢复，且同一张误识牌不得再把局面拖回脏帧（否则整局变成
        “拒答 3 秒 → 正常 3 秒”的周期抽风）。
        """
        eng = make_engine("std_tdh", self._hand_no_1m(), ["1m"] * 5)
        frames = run_frames(eng, self.img, DIRTY_HEAL_FRAMES + 12)
        heal_at = [i for i, p in enumerate(frames) if "剪除" in (p.get("message") or "")]
        self.assertTrue(heal_at, "误识别的超出张数必须被剪除并告知用户")
        i = heal_at[0]
        self.assertGreaterEqual(i + 1, DIRTY_HEAL_FRAMES, "没脏够帧数不得自愈")
        self.assertEqual(frames[i]["advice"], [], "改账的那一帧不得沿用错账上算出的建议")
        self.assertTrue(frames[i + 1]["advice"], "自愈后下一帧必须恢复给建议")
        self.assertEqual(len(heal_at), 1, f"自愈不是一次性的，将周期性拒答：{heal_at}")
        self.assertTrue(all(p["tile_ledger"]["ok"] for p in frames[i + 1:]),
                        "剪除后同一张误识牌不得重新回到账本")

    def test_stale_ledger_cannot_gate_current_frame(self):
        """账本签名与本帧不符时不得参与硬门（防上一帧的违例把本帧误判脏）。"""
        eng = make_engine("std_tdh", self._hand_no_1m(), ["1m"] * 4)
        frames = run_frames(eng, self.img, 8)
        self.assertTrue(frames[-1]["advice"], "前提：本身是能给出建议的合法帧")
        self.assertIsNotNone(eng._ledger, "认领成功时账本必须挂上")
        # 伪造一份“上一帧的账本”：内容带着 over_four 违例，但签名与本帧可见牌不一致。
        # 真机上这就是“牌河已变而 trainer 还拿旧账本”的那一帧。
        stale = copy.deepcopy(eng._ledger)
        stale["sig"] = ((5,) + (0,) * 33, tuple(stale["sig"][1]), stale["sig"][2])
        stale["ok"] = False
        stale["violations"] = [{"kind": "over_four", "tile": "1m",
                                "detail": "1万 已见 5 张，超过实物上限"}]
        real_cd = eng.trainer.calculate_discards

        def cd_then_forget():
            out = real_cd()
            eng.trainer.ledger = stale      # 重算完成后把“旧账本”顶上去
            return out

        eng.trainer.calculate_discards = cd_then_forget
        eng._advice_key = None              # 强制走完整重算，否则命中缓存不调上面那个
        payload = run_one(eng, self.img)
        self.assertIsNone(payload["tile_ledger"],
                          "签名不符的陈旧账本不得下发，也不得用来判脏")
        self.assertTrue(payload["advice"],
                        "账本只是丢了，本帧牌面依旧合法，不能因此拒答")


class TestWallRemainingDrivesLateGameAlert(unittest.TestCase):
    """末期警报必须用牌墙真值：未现总数里含着对手手上的 39 张，会让警报晚响。"""

    def test_alert_uses_true_wall_not_unseen(self):
        # 川麻 108 张：20 种牌各见 2 张（牌河 40 张）+ 手牌 13 张
        # → 未现 108−53 = 55 张，牌墙 55−3×13 = 16 张（阈值 20）
        river = [t for t in (["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
                              "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
                              "1s", "2s"])] * 2
        hand = ["3s", "3s", "4s", "4s", "5s", "5s", "6s", "6s", "7s", "8s", "9s",
                "1s", "2s"]
        eng = make_engine("sc_xz", hand, river)
        frames = run_frames(eng, make_image(seed=31), 10)
        payload = frames[-1]
        led = payload["tile_ledger"]
        self.assertIsNotNone(led, "川麻链路必须下发账本")
        self.assertGreater(payload["remaining"], 20,
                           "本例的『未现总数』必须高于阈值，才能证明旧口径根本不响")
        self.assertLessEqual(led["wall_remaining"], 20)
        alert = payload.get("tenpai_alert")
        self.assertIsNotNone(alert, "牌墙只剩 16 张必须报警（用未现总数会漏报）")
        nums = [int(x) for x in re.findall(r"(\d+)\s*张", alert.get("message", ""))]
        self.assertIn(led["wall_remaining"], nums,
                      msg=f"警报里的牌墙数必须等于账本真值：{alert}")


class TestDartPanelFieldContract(unittest.TestCase):
    """本机没有 dart SDK 可编译，就把「面板读的字段名」反向钉在 Python 侧。

    面板对缺失字段一律 `?? 0`（不报错，只是把数字说成 0），所以 Python 端改了字段名
    不会有任何人立刻发现——它表现为“牌墙 0 张·约 0 轮”。本测试直接去 Dart 源码里
    把 `ledger['x']` / `chance?['x']` 的键名抓出来，逐个要求 payload 里真实存在，
    等于用 Python 给跨语言契约上了一道会自动更新的门（两侧任一改名都会红）。
    """

    DART = os.path.abspath(os.path.join(HERE, "..", "lib", "overlays",
                                        "mahjong_overlay.dart"))
    # 期望下限：正则一旦因面板改写而失配，测试必须立刻变成“没抓到东西”而红，
    # 绝不能因为集合为空而静默通过（空守卫就是这个病）。
    EXPECT_LEDGER = {"opp_known", "ok", "violations", "seen_total", "unseen_total",
                     "wall_remaining", "rounds_left", "standing_total",
                     "wall_only_unseen", "wall_only_types"}
    EXPECT_CHANCE = {"wall_only", "total_unseen", "opp_known", "text"}

    def setUp(self):
        self.img = make_image(seed=41)

    def _keys(self, pattern, expect, what):
        with open(self.DART, encoding="utf-8") as fh:
            src = fh.read()
        got = set(re.findall(pattern, src))
        self.assertTrue(got, f"Dart 源码里没抓到任何 {what} 字段：正则失效，本测试空转")
        self.assertTrue(expect <= got,
                        f"{what} 应至少包含 {sorted(expect)}，实抓 {sorted(got)}")
        return got

    def test_panel_field_names_exist_in_payload(self):
        led_keys = self._keys(r"ledger\['([a-z_]+)'\]", self.EXPECT_LEDGER, "账本")
        ch_keys = self._keys(r"chance\?\['([a-z_]+)'\]", self.EXPECT_CHANCE, "听口")

        eng = make_engine("std_tdh", TENPAI_HAND, ["2p", "3p"])
        payload = run_frames(eng, self.img, 8)[-1]
        led = payload["tile_ledger"]
        ch = payload["ting_chance"]
        self.assertIsNotNone(led)
        self.assertIsNotNone(ch, "听牌帧必须带顶层 ting_chance，否则面板字段无从校验")

        for k in sorted(led_keys):
            self.assertIn(k, led, f"面板读了账本字段 {k}，但 payload 里没有这个键")
            if k != "violations":
                # Dart 侧 `(x as num? ?? 0)`：缺键与 null 都会“合理地”显示成 0，
                # 所以下发的数字绝不可以是 null，否则错在用户眼里 invisible。
                self.assertIsNotNone(led[k], f"{k} 不得为 null")
        for k in sorted(ch_keys):
            self.assertIn(k, ch, f"面板读了听口字段 {k}，但 ting_chance 里没有这个键")
        # 类型口径：Dart 侧 `as num?`/`as List?`/`== true`，这里必须是原生同型，
        # 不能是“字符串化的数字”（JSON 过 JNI 后这类错位最难查）。
        for k in ("seen_total", "unseen_total", "wall_remaining", "rounds_left",
                  "standing_total", "wall_only_unseen", "wall_only_types"):
            self.assertIsInstance(led[k], int, msg=f"{k} 必须是 int")
        self.assertIsInstance(led["violations"], list)
        self.assertIsInstance(led["ok"], bool)
        self.assertIsInstance(led["opp_known"], bool)
        self.assertIsInstance(ch["text"], str)

    def test_summary_numbers_agree_with_per_type_ledger(self):
        """面板头部那一行的数字必须与逐型账本同源（两处口径不许打架）。

        注意：逐型 `wall` 是**下界**（“至少这么多张只可能在牌墙”），头部 `wall_remaining`
        是牌墙总张数，两者本来就不得相等（把下界之和当牌墙会天天少报）。这里卡的
        是真正必须成立的那几条：拆账闭合 + 下界不超总量 + 「只能自摸」统计可重算。
        """
        eng = make_engine("std_tdh", TENPAI_HAND, ["2p", "3p"])
        led = run_frames(eng, self.img, 8)[-1]["tile_ledger"]
        cells = led["by_type"].values()
        self.assertEqual(sum(c["unseen"] for c in cells), led["unseen_total"])
        self.assertEqual(led["unseen_total"],
                         led["wall_remaining"] + led["standing_total"])
        # 下界之和不得超过牌墙总量（超了就是账本自相矛盾，硬门应判脏）
        self.assertLessEqual(sum(c["wall"] for c in cells), led["wall_remaining"])
        for c in cells:
            self.assertEqual(c["unseen"], c["wall"] + c["in_hands"], msg=str(c))
        self.assertEqual(led["wall_only_unseen"],
                         sum(c["unseen"] for c in cells if c["wall_only"]))
        self.assertEqual(led["wall_only_types"],
                         sum(1 for c in cells if c["wall_only"] and c["unseen"] > 0))


class TestAdviceOrderFollowsTieBreakChain(unittest.TestCase):
    """下发给面板的 payload 里，建议顺序必须已经是决胜链的结果。

    这一条存在的意义：analyzer 排对了不算，engine 与知识库都可能在后面再排一次
    （历史上 knowledge_base 就自己写过一套 (定缺/EV/进张) 键）。只要有人在链之
    外重排，面板首条就会与 reason 里的描述不同源。用真 payload 字段反查，而不是
    只测函数。本帧实测就有两组同分（ev=10017.0 ×2、10010.0 ×2），故非空守卫。
    """

    HAND = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
            "1p", "2p", "3p", "5p", "5p"]

    def _advice(self):
        eng = make_engine("std_tdh", self.HAND, ["2p", "3p"])
        payload = run_frames(eng, make_image(seed=11), 8)[-1]
        advice = payload.get("advice") or []
        self.assertTrue(advice, "前提：本帧必须有建议，否则下面的断言全部空转")
        return payload, advice

    def test_payload_advice_is_already_chain_sorted(self):
        _, advice = self._advice()
        self.assertEqual([a["tile"] for a in advice],
                         [a["tile"] for a in sorted(advice, key=order_key)],
                         msg="payload 里的顺序不是决胜链结果：链之后还有人在重排")

    def test_best_tile_is_chain_head(self):
        payload, advice = self._advice()
        self.assertEqual(payload.get("best"), advice[0]["tile"],
                         msg="主推选位与建议首条不同源（两套排序）")

    def test_same_position_twice_gives_identical_sequence(self):
        """确定性：同一局面两次独立构建，建议序列逐位相同。"""
        first = [a["tile"] for a in self._advice()[1]]
        for _ in range(3):
            self.assertEqual([a["tile"] for a in self._advice()[1]], first,
                             msg="同一帧多次构建给出不同顺序 = 随机性未消除")

    def test_tie_group_winner_is_chain_best_among_real_entries(self):
        """同分簇内链首必须逐层不劣于任何一张（用 payload 真字段验，不构数据）。"""
        advice = self._advice()[1]
        groups = {}
        for a in advice:
            groups.setdefault(round(float(a.get("ev") or 0.0), 1), []).append(a)
        tied = [g for g in groups.values() if len(g) > 1]
        self.assertTrue(tied, f"本帧无同分簇，用例将空转（实际 advice={advice}）")
        for g in tied:
            winner = sorted(g, key=tie_tail)[0]
            for x in g:
                self.assertLessEqual(tie_tail(winner), tie_tail(x),
                                     msg=f"同分簇裁决不可复现：{winner['tile']} vs {x['tile']}")
            # 链首必须就是 payload 里这组同分牌中出现最早的那一条（面板只看首条）
            win_tiles = {x["tile"] for x in g}
            first_of_group = next(a for a in advice if a["tile"] in win_tiles)
            self.assertEqual(first_of_group["tile"], winner["tile"],
                             msg=f"payload 首条不是裁决结果：实={first_of_group['tile']} "
                                 f"应={winner['tile']}（同分簇 {sorted(win_tiles)}）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
