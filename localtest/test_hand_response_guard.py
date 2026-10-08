# -*- coding: utf-8 -*-
"""手牌显示响应守卫：正常对局事件必须**首帧上屏**，陌生大改必须**等满共识帧**。

这条链路的两端各有一种坏法，只钉一端就会把产品做成另一种东西：

  - 只钉「快」：让陌生突变也首帧采纳（旧实现 `self._streak >= 1` 短路就是这种坏法），
    单帧误识别直接上屏 —— 用户报的「乱识别不存在的东西」；
  - 只钉「稳」：所有改动都要连续 2 帧，于是摸/打/碰/换牌各慢一整帧（实测单帧
    415~1300ms，一巡就是 0.8~2.6s）—— 用户报的「变化好久了才显示」。

为什么「缩短确认窗口」这件事在本次修复里**不再往下调**（实测，不是猜测）：
`build/_probe_diff_scale.py` 在同一链路上量到 —— 静止画面重编码 JPEG 的帧差
4.6~35.3，最小真实改动（只把一张牌大小的区域变亮）182~229，整行平移（摸牌重排）
1556~2090，夹具里真实两帧之间 863~6323。而 `FRAME_SKIP_DIFF_THRESH = 3.0` 落在
静止噪声**下面**三个数量级 —— 也就是说「真变化被跳帧判定挡住」这件事根本不成立，
降阈值只会让静默帧照付完整识别（更慢、更烫），升阈值才可能挡掉真改动。
于是延迟剩下的唯一来源就是「确认几帧」，而 `_HandStabilizer` 今天对对局里的**全部
常规事件**都已经是首帧即采纳（本文件 ① 钉住它，谁改回等帧就红），唯一还等帧的
只有「同张数、一次改 >HAND_BIG_JUMP 张、窗口内没见过」的陌生大改（② 钉住它不许退化）。

四条采纳通道（与 `_HandStabilizer.observe` 逐条对号，改代码必须两边一起改）：
  ① 张数切换 1~HAND_BIG_JUMP 张（摸 13→14、打 14→13、碰 −3、两碰 −6…）→ 首帧
  ② 同张数且只换 ≤HAND_BIG_JUMP 张（换三张、单张被认错后自愈）→ 首帧
  ③ 这个陌生牌型在最近 HAND_MODE_WINDOW 帧里已出现过 HAND_MODE_VOTES 次（含本帧）
     → 采纳：识别器稳定漏同一张牌时永远凑不满**连续**帧，靠这条兜住
  ④ 其余陌生突变 → 连续 HAND_CONFIRM_FRAMES 帧完全一致才采纳

今天 HAND_MODE_VOTES=HAND_CONFIRM_FRAMES=2，④ 被 ③ 包含（连续两帧必然在窗口里数到
两次）。留着它不是冗余摆设：它锁的是「连续」这一维语义，一旦有人只抬 ③ 的票数，
④ 就是唯一的下限 —— 所以 M2 必须同时改两个常量才能复现「首帧放行 phantom」。

本文件同时是「三个常量真在说话」的证明：它们过去只是躺在注释里（`observe` 用的是
写死的字面量，`need = HAND_CONFIRM_FRAMES` 算了没用），现在判据直接引用常量，
于是 `--mutate` 里**只改常量**就能把三种坏法逐一复现出来（见 TestMutationControls）。
还有第四条契约：`pending` 必须被跳帧闸门读走（`TestPendingIsWiredIntoTheSkipGate`）——
写了没人读的 flag 等于没有，那是「陌生大改永远凑不满第二帧」的隐藏坑。

已知缺口（**故意不钉成绿灯**，只留证据，别把它当成已修）：杠后的 9/6/3 张不在
`modes.hand_sizes` 表里，① 会把那一帧判成「张数不合法」而慢一巡。它不在本次范围
有两个理由：(i) 生产手牌通道（已挂模板 bank 的平台，`banked_platforms()` 里的
tuyou/tencent/shushan/…）在 `Engine.process` 里直接采信整排切片，根本不经过
`_HandStabilizer.observe`，所以真机上杠后不滞后；(ii) `hand_sizes` 同时是规则
求解器的合法张数口径，动它等于改「什么叫相公」，不属于「不改推理与规则」这一轮的
授权范围。要修就先补求解器侧的用例，别只改这张表。

运行：
  py -3.10 -X utf8 localtest/test_hand_response_guard.py
  py -3.10 -X utf8 localtest/test_hand_response_guard.py --mutate
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import engine.engine as E  # noqa: E402
from engine.engine import _HandStabilizer  # noqa: E402
from modes import hand_sizes  # noqa: E402

MUTATE = "--mutate" in sys.argv
SIZES = set(hand_sizes("std_tdh"))     # 与引擎同一张合法张数表（不要在此写死）

ENGINE_PY = os.path.join(REPO, "android", "app", "src", "main", "python",
                         "engine", "engine.py")
with open(ENGINE_PY, encoding="utf-8") as _fp:
    ENGINE_SRC = _fp.read()
# 跳帧闸门里读 pending 的那一行（它才是「第二帧一定会被识别」的凭据）
PENDING_CONSUMER = "and not self._hand_stab.pending"

BASE13 = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
          "1p", "2p", "3p", "4p"]


def _ev(stab, tiles):
    """喂一帧，返回 (对外输出的 mpsz, stab.pending)。"""
    return stab.observe(list(tiles), SIZES), stab.pending


def _establish(tiles=BASE13):
    """冷启动：合法张数首帧即立稳（`observe` 的冷启动分支），返回稳定器。"""
    stab = _HandStabilizer()
    out, pending = _ev(stab, tiles)
    assert out == "".join(tiles), "冷启动首帧就该立住稳定手牌，否则下面的断言全在测空气"
    assert pending is False
    return stab


class TestNormalEventsAreOneFrame(unittest.TestCase):
    """① ②：对局里真会发生的事件，一帧都不许多等。"""

    def test_draw_13_to_14_shows_immediately(self):
        stab = _establish()
        drawn = BASE13 + ["5p"]
        out, pending = _ev(stab, drawn)
        self.assertEqual("".join(drawn), out, "摸牌被压在共识帧里：弹窗慢一整帧")
        self.assertFalse(pending, "摸牌后还挂着待确认，会连下一帧都不许跳，白付一次识别")

    def test_discard_14_to_13_shows_immediately(self):
        stab = _establish(BASE13 + ["5p"])
        out, pending = _ev(stab, BASE13)
        self.assertEqual("".join(BASE13), out, "打牌（14→13）必须首帧生效")
        self.assertFalse(pending)
        self.assertEqual(13, len(out) // 2)

    def test_pong_13_to_10_shows_immediately(self):
        """碰：一次少 3 张，正是 HAND_BIG_JUMP 之内的张数切换，不该等。"""
        stab = _establish()
        ponged = [t for t in BASE13 if t not in ("7m", "8m", "9m")]
        self.assertEqual(10, len(ponged))
        out, pending = _ev(stab, ponged)
        self.assertEqual("".join(ponged), out, "碰牌被压在共识帧里：用户已经碰了面板还挂着旧牌")
        self.assertFalse(pending)

    def test_two_pongs_10_to_7_shows_immediately(self):
        ponged = [t for t in BASE13 if t not in ("7m", "8m", "9m")]
        stab = _establish(ponged)
        after = [t for t in ponged if t not in ("1p", "2p", "3p")]
        out, _ = _ev(stab, after)
        self.assertEqual("".join(after), out, "第二次碰（10→7）同样必须首帧生效")

    def test_tile_swap_within_four_tiles_shows_immediately(self):
        """同张数只换 3 张（换三张）：走 is_tile_swap 通道，首帧生效。"""
        stab = _establish()
        swapped = [t for t in BASE13 if t not in ("1p", "2p", "3p")] + ["5p", "6p", "7p"]
        self.assertEqual(13, len(swapped))
        out, pending = _ev(stab, swapped)
        self.assertEqual("".join(swapped), out, "换三张被当成陌生大改：换牌阶段整巡延迟")
        self.assertFalse(pending)

    def test_single_misread_heals_on_first_frame(self):
        """单张被认错后下一帧改回：同张数 diff=1，也必须首帧生效（否则错牌会挂两帧）。"""
        stab = _establish()
        wrong = [t for t in BASE13[:-1]] + ["5s"]
        out_wrong, _ = _ev(stab, wrong)
        self.assertEqual("".join(wrong), out_wrong, "单张改动应视为换牌，首帧即采信")
        out_back, _ = _ev(stab, BASE13)
        self.assertEqual("".join(BASE13), out_back, "自愈也必须首帧生效")


class TestForeignBigChangeWaits(unittest.TestCase):
    """④：唯一该等帧的那一档 —— 等帧本身就是它的作用，不许为了响应拆掉。"""

    def _big_change(self):
        """同张数、一次改 5 张：正常一巡内不可能发生，典型画像是误识别爆发。"""
        keep = BASE13[:8]                       # 保留 8 张
        new = ["1s", "2s", "3s", "7p", "8p"]    # 换掉 5 张
        return keep + new

    def test_first_frame_is_not_displayed(self):
        stab = _establish()
        big = self._big_change()
        out, pending = _ev(stab, big)
        self.assertEqual("".join(BASE13), out,
                         "陌生大改首帧就上屏：单帧误识别会变成面板上的假手牌")
        self.assertTrue(pending,
                        "没置 pending=True，引擎下一帧可能被跳帧判定挡掉，坏牌型永远凑不满 2 帧")

    def test_second_consecutive_frame_adopts_it(self):
        """等帧只能等一帧：真变化第二帧必须生效，否则就成了「一直不显示」。"""
        stab = _establish()
        big = self._big_change()
        _ev(stab, big)
        out, pending = _ev(stab, big)
        self.assertEqual("".join(big), out, "连续 2 帧一致的合法新牌型必须采纳")
        self.assertFalse(pending)
        # 诚实说明：上面已写明 ③/④ 今天重合，这条断言只保证「第二帧一定采纳」，
        # 不区分是哪条通道放行的（要区分就得改生产常量，那不是守卫该做的事）。

    def test_invalid_tile_count_never_becomes_stable(self):
        """张数不合法的帧不参与共识（漏识别 1 张的帧不该把稳定手牌改掉）。"""
        self.assertNotIn(6, SIZES, "夹具假设被破坏：6 张在合法张数表里，本用例测不到东西")
        stab = _establish()
        out, _ = _ev(stab, BASE13[:6])
        self.assertEqual("".join(BASE13), out,
                         "非法张数（6 张）不该更新稳定手牌")


class TestEvidenceWindow(unittest.TestCase):
    """③：非连续也能采纳 —— 识别器稳定漏同一张牌时永远凑不满连续帧。"""

    def _big_change(self, seed):
        keep = BASE13[:8]
        new = seed
        return keep + new

    def test_alternating_recognition_adopts_on_second_sighting(self):
        """A(稳定) → B(拒) → A(稳定不变) → B(采纳)：第二眼就够，不必连续。"""
        stab = _establish()
        bigB = self._big_change(["1s", "2s", "3s", "7p", "8p"])
        out, _ = _ev(stab, bigB)
        self.assertEqual("".join(BASE13), out, "第一眼就该被挡住")
        _ev(stab, BASE13)                      # 中间夹一帧回到旧牌型（连续流被打断）
        out2, pending = _ev(stab, bigB)
        self.assertEqual("".join(bigB), out2,
                         "窗口里见过第二次的牌型必须采纳：这就是 HAND_MODE_WINDOW 存在的全部理由")
        self.assertFalse(pending)

    def test_window_is_the_module_constant(self):
        """证据窗口必须真的跟着 HAND_MODE_WINDOW（写死字面量时这条会失去意义）。"""
        self.assertEqual(E.HAND_MODE_WINDOW, _HandStabilizer()._recent.maxlen,
                         "稳定器的证据窗口不是 HAND_MODE_WINDOW：调常量不生效")


class TestPendingIsWiredIntoTheSkipGate(unittest.TestCase):
    """`pending=True` 必须被跳帧闸门读走，否则「第二帧必采纳」可能永远等不到。"""

    def test_skip_gate_consumes_pending(self):
        """引擎在「画面冻结就复用上一 payload」那条闸门里必须考到 pending。

        不考这一条会漏掉最阴的坏法：稳定器侧写得好好（本文件④ 拦第 1 帧），
        但没人读它 —— 陌生大改凑不满第二帧，面板卡在旧牌型上看似「乱识别」修好了，
        实则变成「一直不更新」。
        """
        self.assertIn(PENDING_CONSUMER,
                      ENGINE_SRC,
                      "跳帧闸门不再看 pending：待确认的新牌型可能被「画面没变」挡掉，"
                      "第二帧根本不会被识别")


class _Patched:
    """临时改引擎模块级常量（判据现在直接引用它们）。"""

    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(E, k)
            setattr(E, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(E, k, v)


class TestMutationControls(unittest.TestCase):
    """--mutate：只改常量就能复现三种坏法 —— 这同时证明判据真的接着常量。"""

    def setUp(self) -> None:
        if not MUTATE:
            self.skipTest("加 --mutate 才跑变异对照")

    def test_m1_no_quick_channel_makes_every_event_wait(self):
        """坏法一：把「正常一巡内的改动」上限压成 0 → 摸/打/碰全部要等共识帧。"""
        with _Patched(HAND_BIG_JUMP=0):
            stab = _establish()
            out, _ = _ev(stab, BASE13 + ["5p"])
            self.assertNotEqual("".join(BASE13 + ["5p"]), out,
                                "变异无效：HAND_BIG_JUMP=0 居然没让摸牌慢下来 —— "
                                "说明张数切换通道没接常量")

    def test_m2_one_frame_consensus_shows_phantom_immediately(self):
        """坏法二：共识 1 帧 + 窗口 1 次 → 陌生大改首帧直接上屏（phantom 牌）。"""
        with _Patched(HAND_CONFIRM_FRAMES=1, HAND_MODE_VOTES=1):
            stab = _establish()
            big = BASE13[:8] + ["1s", "2s", "3s", "7p", "8p"]
            out, _ = _ev(stab, big)
            self.assertEqual("".join(big), out,
                             "变异无效：一档共识居然没放行陌生大改 —— "
                             "说明采纳条件里的常量是摆设，② 拦不住 phantom")

    def test_m3_window_of_one_breaks_non_consecutive_evidence(self):
        """坏法三：证据窗口缩到 1 帧 → 交替出现的同一牌型永远采纳不了。"""
        with _Patched(HAND_MODE_WINDOW=1):
            stab = _HandStabilizer()
            _ev(stab, BASE13)                          # 冷启动立稳
            big = BASE13[:8] + ["1s", "2s", "3s", "7p", "8p"]
            _ev(stab, big)
            _ev(stab, BASE13)
            out, _ = _ev(stab, big)
            self.assertNotEqual("".join(big), out,
                                "变异无效：窗口=1 帧居然还能采纳 —— ③ 在测别的东西")
            # 窗口只剩 1 帧时「第二眼」永远数不到 2 次；但连续两帧仍该由 ④ 兜住：
            # （这一条同时证明 ③ 被拆掉时 ④ 还在工作，不是整块判据失效）
            out2, _ = _ev(stab, big)
            self.assertEqual("".join(big), out2,
                             "④ 也碎了：连续两帧一致仍不采纳，说明 CONFIRM 通道没接常量")

    def test_m4_unwired_pending_breaks_the_contract(self):
        """坏法四：把跳帧闸门里读 pending 的那行拆掉 → 契约必须报（防止只写不读）。"""
        mutated = ENGINE_SRC.replace(PENDING_CONSUMER + "\n", "\n", 1)
        self.assertNotEqual(mutated, ENGINE_SRC,
                            "变异无效：那行拆不掉（引擎形态已不读本守卫的对照物）")
        self.assertNotIn(PENDING_CONSUMER, mutated,
                         "拆掉一行后仍能在原文里找到它：契约在测别的东西")
        # 现行源码必须真的接着它（否则上面那条对照只是屠龙技）
        self.assertIn(PENDING_CONSUMER, ENGINE_SRC)


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for tc in (TestNormalEventsAreOneFrame, TestForeignBigChangeWaits,
               TestEvidenceWindow, TestPendingIsWiredIntoTheSkipGate):
        suite.addTests(loader.loadTestsFromTestCase(tc))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
