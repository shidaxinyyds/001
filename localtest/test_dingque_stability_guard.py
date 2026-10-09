# -*- coding: utf-8 -*-
"""r6dq 守卫：定缺徽章读数必须**时间稳定**才可信（面板自噬的次级防线）。

故障原型（实测，非推理）：自家悬浮窗展开时压住徽章取区，`detect_dingque` 读到的是
面板 chip 的颜色而不是牌桌事实 —— 真机 s2 报「筒」、s4 报「条」，同一局两个说法，而
玩家一次都没改过缺门。像素层面区分不了二者：那条启发式已被实测证伪并拆除（六项特征
在「真徽章」与「面板 chip」两组上全部重叠，把 dingque 从 11/11 打到 2/11，见
`test_real_phase_frames_guard.py` 的 B' 组）。还能区分的只剩**时间轴**：牌桌徽章一局
内恒定，面板内容随牌局逐秒变。

于是这一层把「单枪原始观测」与「可信读数」分开：最近 DQ_STABLE_WINDOW 次扫描攒成窗口，
窗口内非 None 读数彼此不一致就整体判「没读准」，宁可不报也不让最后那一枪直接上屏。

分四层，每层都注明它**证到了什么**、以及**证不到什么**：
  A. 判据表（纯函数窗口）：全等→可信；分歧→不可信；单枪漏读不算分歧；空窗不报。
     这一层直接跑生产方法 `_stable_dingque_read`（未绑定 + 假 self），不复制逻辑。
  B. 接线契约（源码断言）：扫描点必须把 raw 观测送进窗口、且只报稳定值；新局必须
     作废窗口；朝向变更必须作废窗口。这三处活在 engine.py 里，没法像 A 那样单跑。
  C. 真机帧流水线行为：把 s4 真机帧反复喂进**同一个 Engine**，用脚本注入徽章读数
     （patch engine 模块级 `detect_dingque`），断言稳定→可信、抽搐→扣下、自愈、
     新局不被旧局顶替。这一层证的是「这道门真的接在主链路上，不是个孤立工具函数」。
  D. 已知边界（如实固化）：**持续**遮挡（面板一直压着徽章）时窗口内读数全等，
     假值照样会被报出——本门只挡「抽搐」，不挡「恒定的污染」。彻底解法是面板矩形
     事实源（Java 已知窗口绝对偏移与宽高 → 归一矩形推给引擎 → 相交即报「没读到」），
     它涉及 Dart→主引擎→Java→Python 四跳与旋转映射，必须真机验证，不在此假装解决。
     把这条边界写成断言，是为了防止后来人把这道门当成自噬的终极修复而删掉事实源需求。

运行：
  py -3.10 -X utf8 localtest/test_dingque_stability_guard.py
  py -3.10 -X utf8 localtest/test_dingque_stability_guard.py --mutate
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import textwrap
import unittest
from collections import deque

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PY_DIR = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

MUTATE = "--mutate" in sys.argv

ENGINE_PY = os.path.join(PY_DIR, "engine", "engine.py")
SHOTS = os.path.join(HERE, "shots_tuyou_select")
FRAME_OK = "s4_after_swap_cd04.jpg"   # GT：status=ok、14 张立牌，会走到徽章读数出口

with open(ENGINE_PY, encoding="utf-8") as fp:
    ENGINE_SRC = fp.read()

import engine.engine as EE  # noqa: E402

WINDOW = EE.DQ_STABLE_WINDOW
NAMES = EE.DINGQUE_SUIT_NAMES


# --------------------------------------------------------------------- A 组
class _Win:
    """只带稳定门所需那两个字段的最小 self。

    生产方法只读 `_dq_scan_hist`、只写 `_dq_unstable_hits`，所以不必为此造一个
    完整 Engine（那要加载模板库、跑一次整屏识别）。这既是省事，也是为了让 A 组能
    在变异检验里把「换掉的实现」和「原实现」喂同一批窗口状态做对照。"""

    def __init__(self, hist):
        self._dq_scan_hist = deque(hist, maxlen=WINDOW)
        self._dq_unstable_hits = 0


def _read_prod(win):
    """直接调生产方法（未绑定函数，把 _Win 当 self 递进去）。"""
    return EE.Engine._stable_dingque_read(win)


# (窗口内容, 期望 (门, 名), 期望是否记一次「分歧扣下」)
WINDOW_TABLE = [
    ([], (None, None), False),
    ([2], (2, NAMES[2]), False),
    ([2, 2], (2, NAMES[2]), False),
    # 单枪漏读不算分歧：徽章一旦敲定就整局常驻，压缩噪声/瞬时遮挡不该抹掉事实
    ([2, None], (2, NAMES[2]), False),
    ([None, 2, None], (2, NAMES[2]), False),
    ([2, None, None], (2, NAMES[2]), False),
    ([None, None], (None, None), False),
    # 分歧：后一枪推翻前一枪 → 不可信，也不许「谁最新听谁的」
    ([1, 2], (None, None), True),
    ([2, 1], (None, None), True),
    ([2, 2, 1], (None, None), True),   # 多数也不采信：不确定就别装确定
    ([1, 2, 2], (None, None), True),
    ([0, 1, 2], (None, None), True),
]


def _assert_window_table(read):
    for hist, want, want_unstable in WINDOW_TABLE:
        win = _Win(hist)
        got = tuple(read(win))
        assert got == want, f"窗口 {hist} 的稳定读数 = {got}，应 {want}"
        assert (win._dq_unstable_hits > 0) == want_unstable, (
            f"窗口 {hist} 的分歧计数 = {win._dq_unstable_hits}，"
            f"应 {'记一次' if want_unstable else '不记'}（扣下必须可追溯，"
            "否则事后查不出是「读不准」还是「根本没读到」）")


def _assert_stable_is_trusted(read):
    """A 组的反向半句：稳定的单一读数必须照报，否则这道门会退化成「永远不报」。"""
    for suit in (0, 1, 2):
        win = _Win([suit] * WINDOW)
        assert tuple(read(win)) == (suit, NAMES[suit]), f"恒定读数 {suit} 被扣下了"


class TestStabilityWindow(unittest.TestCase):
    """A 组：判据表。跑的是生产 `_stable_dingque_read` 本体。"""

    def test_window_table(self):
        _assert_window_table(_read_prod)

    def test_stable_reading_is_still_reported(self):
        _assert_stable_is_trusted(_read_prod)

    def test_names_table_matches_badge_reader(self):
        # 稳定门用 DINGQUE_SUIT_NAMES 复原花色名；这张表必须与 detect_dingque 自己
        # 返回的名字一致，否则「门」与「原始观测」会给出两个不同说法。
        for suit, name in ((0, "万"), (1, "筒"), (2, "条")):
            self.assertEqual(name, NAMES[suit])


# --------------------------------------------------------------------- C 组
def _feed(eng, img):
    """喂一帧。

    本夹具会反复喂同一帧，而那正好满足跳帧门的「画面冻结」前提（真机帧帧有压缩
    噪声与牌局变化，不会长期冻结），后续帧会被整段吞掉、徽章根本不扫（实测：
    同一帧喂到第 4 次起 tick 不再上涨）。所以喂之前清一次跳帧基线，等价于「每次
    都是新采集」：只动采集时序，不碰任何判据与代码路径。
    """
    eng._frame_skipper = EE._FrameSkipper()
    with contextlib.redirect_stdout(io.StringIO()):
        res = eng.process(img)
    return json.loads(res.result) if res is not None else None


def _drive_one_scan(eng, img):
    """喂帧直到真的发生一次徽章**扫一枪**，返回那一帧的 payload。

    必须等扫描计数而不是节流 tick：tick 每帧都涨，而扫描只在 `tick % 4 == 1` 发生。
    拿 tick 当「扫了一枪」会让后续每一枪都落在没扫描的帧上（实测：第一枪消耗了脚本，
    第二枪直接拿着未消费的 [2] 去断言，假红）。跳帧也会吞掉整段处理，所以按帧数驱动
    同样错。64 帧仍没触成一枪 = 夹具没接到线，当场判红而不是静默跳过。"""
    n0 = eng._dq_scan_count
    for _ in range(64):
        d = _feed(eng, img)
        if eng._dq_scan_count != n0:
            assert d is not None, "扫了一枪却没有返回帧"
            return d
    raise AssertionError("64 帧都没触发一次徽章扫描：夹具没接到线，本测试在测空气")


class _ScriptedBadge:
    """把 engine 模块级 `detect_dingque` 换成脚本化的读数源。

    必须换 engine 命名空间里的那个名字：扫描点调用的是模块全局（`detect_dingque(...)`），
    换别处只是自说自话。装完立刻自检「模块属性真的是这个 fake」，没生效就当场失败——
    假测量比没测量更糟（踩过：补丁签名错位时两边payload 不同而计数为 0）。
    消费完毕由 `_ScriptedBadge.suits` 变空来证明（见 TestBadgePipeline._drive）。
    """

    def __init__(self, suits):
        self.suits = list(suits)
        self.orig = EE.detect_dingque

    def __enter__(self):
        def fake(_img):
            s = self.suits.pop(0)
            return s, (NAMES[s] if s is not None else None)
        EE.detect_dingque = fake
        if EE.detect_dingque is not fake:
            raise AssertionError("detect_dingque 补丁没生效（假测量，当场失败）")
        return self

    def __exit__(self, *exc):
        EE.detect_dingque = self.orig
        return False


class TestBadgePipeline(unittest.TestCase):
    """C 组：真机帧 + 脚本读数，证明这道门接在主链路上。"""

    @classmethod
    def setUpClass(cls):
        p = os.path.join(SHOTS, FRAME_OK)
        if not os.path.exists(p):
            raise AssertionError(f"真机夹具缺失，守卫在测空气：{p}")
        img = cv2.imread(p)
        if img is None:
            raise AssertionError(f"夹具读不出来：{p}")
        cls.img = img

    def setUp(self):
        # 每个用例一个新鲜 Engine：`_dq_unstable_hits`/窗口都是跨帧累计量，共用一台
        # 就会让用例之间互相依赖方法名字典序（那种绿是假的）。
        self.eng = EE.Engine()
        self.eng.set_platform("tencent")
        self.eng.set_mode("sc_hz")

    def _drive(self, suit):
        with _ScriptedBadge([suit]) as sc:
            d = _drive_one_scan(self.eng, self.img)
            self.assertEqual(sc.suits, [], "这一枪没被消耗：扫描点没有调用被 patch 的读数源")
        return d

    def test_stable_badge_is_reported(self):
        for _ in range(3):
            d = self._drive(2)
            self.assertEqual("条", d.get("dingque"), "恒定读到「条」却没有报出去（门被装反）")
        self.assertEqual(0, self.eng._dq_unstable_hits, "稳定读数被记成分歧")

    def test_flapping_badge_is_withheld(self):
        # 先把读数钉稳，再让它抽搐一枪：这正是 s2 报筒、s4 报条的真机形态。
        for _ in range(3):
            self._drive(2)
        d = self._drive(1)
        self.assertIsNone(d.get("dingque"),
                          "后一枪推翻前一枪时还把最新值当缺门报出去——缺门会在面板上抽搐")
        self.assertIsNone(d.get("dingque_suit"),
                          "面板没报，却把同一个假值递给了决策层（advice 拿的是 suit）")
        self.assertGreaterEqual(self.eng._dq_unstable_hits, 1,
                                "扣下的分歧没被计数：事后查不出这里是读不准还是没读到")

    def test_withholding_does_not_break_the_hand_outlet(self):
        # 扣下徽章读数不许伤到主链路：用户报的「有手牌却显示等待牌局开始」就是
        # 这类「一个不确定证据拖垮其它出口」的形态，本门必须是可失败的旁路。
        for _ in range(2):
            self._drive(2)
        d = self._drive(1)
        self.assertIsNone(d.get("dingque"), "前提不成立：这一帧没被扣下，本测试在测空气")
        self.assertEqual(14, d.get("count"), "扣下徽章读数把代价转嫁到了识别出口")
        self.assertTrue(d.get("hand"), "被扣下的那一帧不再报出手牌")
        self.assertEqual("ok", d.get("status"), "不稳的徽章读数把阶段拖进了等待开局")

    def test_recovers_after_the_badge_settles(self):
        # 一次抽搐不该把门永久关死：旧值滑出窗口（WINDOW 次扫描）后必须重新可信。
        for _ in range(2):
            self._drive(2)
        self._drive(1)
        for _ in range(WINDOW):
            self._drive(2)
        self.assertEqual("条", self._drive(2).get("dingque"),
                         "读数已经稳定了三个周期，这道门还在扣着不上屏")

    def test_new_game_does_not_inherit_previous_dingque(self):
        # 上一局确立的缺门不得顶替本局「还没定缺」。
        self._drive(2)
        self.assertEqual("条", self._drive(2).get("dingque"))
        self.eng._reset_game_state()
        self.assertEqual([], list(self.eng._dq_scan_hist), "新局没有作废读数窗口")
        d = self._drive(None)
        self.assertIsNone(d.get("dingque"),
                          "新局第一枪什么都没读到，却还挂着上一局的缺门（窗口没随新局作废）")

    def test_orient_change_invalidates_the_window(self):
        # 换朝向后同一个归一化取区对应屏幕上另一块像素，旧读数不可比。
        # 这里只改动「窗口是在哪个朝向下攒的」那个标签，而不是去改 `_orient`：
        # 后者会被下一帧的方向验证改回去，测出来的绿/红取决于重探时序，是假判据。
        self._drive(2)
        self._drive(2)
        base = self.eng._dq_hist_orient
        self.assertIsNotNone(base, "窗口没有朝向标签：本测试的前提不成立")
        self.eng._dq_hist_orient = (base + 180) % 360
        d = self._drive(1)
        self.assertEqual("筒", d.get("dingque"),
                         "朝向变了却还拿旧朝向的读数当分歧，把新取区的可信读数扣死了")

    def test_read_is_only_used_when_mode_has_dingque(self):
        # 非川麻（无定缺概念）不得扫徽章、不得攒窗口、不得报缺门：旧实现每帧无条件
        # 全图检测，含 std 六款，纯烧 CPU 拖慢主链路响应。
        self.eng.set_mode("std_tdh")
        self.assertFalse(EE.is_dingque_mode("std_tdh"), "这个玩法其实是有定缺的")
        sc = _ScriptedBadge([2] * 6)
        try:
            with sc:
                for _ in range(5):
                    _feed(self.eng, self.img)
            self.assertEqual(6, len(sc.suits),
                             "没有定缺的玩法仍在调用徽章读数（脚本被消耗了）")
            self.assertEqual(0, self.eng._dq_scan_count, "非川麻也在扫徽章（每帧白付全图检测）")
            self.assertEqual([], list(self.eng._dq_scan_hist), "没有定缺的玩法也在攒徽章读数窗口")
            self.assertIsNone(_feed(self.eng, self.img).get("dingque"))
        finally:
            # set_mode 会写配置档（引擎每帧 reload），不回滚就会漏给后续用例与别的守卫。
            self.eng.set_mode("sc_hz")


# --------------------------------------------------------------------- D 组
class TestKnownBoundary(unittest.TestCase):
    """如实记录这道门**挡不住**什么（防止它被当成自噬的终极修复）。"""

    def test_persistently_covered_badge_still_passes(self):
        # 面板一直压着徽章时，窗口内读数全等 → 假值照报。时间稳定门只判「抽搐」。
        win = _Win([1, 1, 1])
        self.assertEqual((1, "筒"), _read_prod(win))
        # 彻底解法需要「面板矩形」这个事实源，它至今没接上——别把这条边界写成已解决。
        self.assertNotIn("panel_rect", ENGINE_SRC,
                         "面板矩形事实源若真落地了，就该同步改掉这道门的定位说明")


# ------------------------------------------------------------------- B 组
# 这三处活在 engine.py 的处理主链路里：没法像 A 组那样单独跑，也没法像 C 组那样
# 只改一个方法体，所以用「源码契约 + 变异检验」双层口径（照 `test_real_phase_frames_guard`
# 的 E 组先例）。行为层兜底在 C 组与 eval_base 的定缺基准。
WIRING_CONTRACTS = [
    ("扫描点只报稳定门产物，不把原始观测直接写进缓存", "engine",
     "                    self._dq_scan_cache = self._stable_dingque_read()",
     "                    self._dq_scan_cache = detect_dingque(full_for_preview)"),
    ("原始观测必须先入窗（时间轴的证据来源）", "engine",
     "                    self._dq_scan_hist.append(suit)",
     "                    pass  # 不往窗口里记账（只剩「最后一枪」的证据链）"),
    ("新局必须作废读数窗口（旧局缺门不得顶替本局）", "engine",
     "        self._dq_scan_hist.clear()\n        self._dq_scan_cache = (None, None)",
     "        self._dq_scan_hist = deque(maxlen=DQ_STABLE_WINDOW)  # 只换对象不清窗"),
    ("朝向变更必须作废读数窗口（换朝向后读数不可比）", "engine",
     "                    if self._dq_hist_orient != self._orient:",
     "                    if False:  # 朝向不作废窗口"),
]

WIRING_SRC = {"engine": ENGINE_SRC}


def _assert_wiring_holds(srcs):
    for name, key, good, bad in WIRING_CONTRACTS:
        assert good in srcs[key], f"{key} 里的接线断了/改了写法：{name}"
        assert srcs[key].count(good) == 1, f"{name}：契约定位不唯一，改错地方也没人知道"
        assert bad not in srcs[key], f"坏写法已经在场上了：{name}"


class TestWiring(unittest.TestCase):
    def test_contracts_are_wired(self):
        _assert_wiring_holds(WIRING_SRC)

    def test_window_size_is_scan_units(self):
        # 窗口单位是「扫描次数」，扫描本身 4 帧一次：改成按帧数记账就等于把门调松。
        self.assertEqual(3, WINDOW, "稳定窗被改动，请同步核对 C 组的自愈/抽搐用例")
        self.assertIn("if self._dq_scan_tick % 4 == 1:", ENGINE_SRC)
        # C 组靠「扫了几枪」驱动（帧数/tick 都与枪数不等），这个计数必须在场。
        self.assertIn("self._dq_scan_count += 1", ENGINE_SRC)


# ------------------------------------------------------------- 变异检验夹具
def _guard_breaks(assertion) -> bool:
    """跑一段守卫断言，返回它是否**变红**。只用 AssertionError：其它异常意味着
    模板本身不合法（语法/路径错），那要当作测试错误抛出去，不能算「拦住」。"""
    try:
        assertion()
        return False
    except AssertionError:
        return True


def _engine_method_src(name: str) -> str:
    """从 engine.py 切出一个方法的完整定义（含原有 4 格缩进）。

    下一个成员的起始标记可能是 `def` 也可能是装饰器（`@staticmethod`）：只找 `def`
    会把上一个 `@staticmethod` 扫进切片，exec 时就多了一条落单的装饰器 → SyntaxError。"""
    head = f"    def {name}("
    start = ENGINE_SRC.index(head)
    ends = [ENGINE_SRC.index(mark, start + len(head))
            for mark in ("\n    def ", "\n    @")
            if mark in ENGINE_SRC[start + len(head):]]
    if not ends:
        raise AssertionError(f"切不出 {name} 的方法边界（后面没有下一个成员）")
    return ENGINE_SRC[start:min(ends)]


def _mutated_read(old: str, new: str):
    """只改坏 `_stable_dingque_read` 的一处，返回可当未绑定方法调用的副本。

    方法体只依赖 self 与 DINGQUE_SUIT_NAMES，所以拿 EE 的命名空间当 globals 就够用。"""
    body = _engine_method_src("_stable_dingque_read")
    if old not in body:
        raise AssertionError(f"变异模板过期：{old[:70]}")
    mutated = body.replace(old, new, 1)
    if mutated == body:
        raise AssertionError(f"变异无效：{old[:70]}")
    ns = dict(vars(EE))
    exec(compile(textwrap.dedent(mutated), "<mutant:_stable_dingque_read>", "exec"), ns)
    fn = ns["_stable_dingque_read"]
    return lambda win: fn(win)


BEHAVIOR_MUTANTS = [
    ("分歧也照报最新那一枪（缺门在面板上抽搐的老毛病）",
     "        if len(set(seen)) != 1:",
     "        if False:",
     _assert_window_table),
    ("多数派即采信（[2,2,1] 会被报成条，而它其实就是抽搐中）",
     "        if len(set(seen)) != 1:",
     "        if len(set(seen)) > 2:",
     _assert_window_table),
    ("把单枪漏读也当分歧（徽章没变，却因压缩噪声抹掉已确立的缺门）",
     "        seen = [s for s in self._dq_scan_hist if s is not None]",
     "        seen = list(self._dq_scan_hist)",
     _assert_window_table),
    ("空窗报成万（「没读到」这个合法出口被关闭，未知冒充已定缺）",
     "        if not seen:\n            return None, None",
     "        if not seen:\n            return 0, DINGQUE_SUIT_NAMES[0]",
     _assert_window_table),
    ("把「稳定就照报」也一起砍掉（这道门退化成永远不报缺门）",
     "        return seen[0], DINGQUE_SUIT_NAMES[seen[0]]",
     "        return None, None",
     _assert_stable_is_trusted),
]

PIPELINE_MUTANTS = [
    # 行为对照的第二档：换掉主链路上的方法本体，再用 C 组同一条断言跑真机帧。
    ("抽搐时被扣下这一步整个失效（真机 s2/s4 那种一改口就上屏）",
     "        if len(set(seen)) != 1:",
     "        if False:",
     "flapping_badge_is_withheld"),
]


class TestMutationControls(unittest.TestCase):
    """--mutate：把坏写法塞回生产代码，本守卫必须立刻变红。

    三档各自证到什么，如实标注：
      BEHAVIOR：exec 一份只改坏一处的方法副本，用守卫同一条断言跑同一批窗口状态——
        证「那段判据真的在生效」，而不是「那段字还在」。
      PIPELINE：把方法本体换成改坏的副本（签名与调用点必须一致，并当场核对它真被
        调过），再拿 C 组同一条真机帧断言跑完整 process()——证这道门真的接在主链路上。
      WIRING：只能证「这条接线是承重的」。行为层兜底在 C 组真机帧与 eval_base。
    """

    def test_behavior_mutants_are_caught(self):
        missed = []
        for name, old, new, assertion in BEHAVIOR_MUTANTS:
            probe = _mutated_read(old, new)
            if _guard_breaks(lambda: assertion(probe)):
                print(f"[mutate] {name}：同一条断言立刻变红——已接住")
            else:
                missed.append(name)
        self.assertEqual([], missed, "这些变异活了下来：判据其实没被这段代码决定")

    def test_pipeline_mutant_is_caught(self):
        """主链路档：把方法本体换成改坏的副本，再用 C 组同一条断言跑真机帧。

        这里不用源码字符串对照，而是真的拿坏实现去跑完整 process()：只有真机帧能回答
        「这道门接在链路上了吗」（扫描点、缓存、payload 出口三处都在 engine 里）。

        签名必须与生产一致（`self` 一个参数）并**数得清它真被调过几次**：踩过一次用
        `lambda self_, win` 包装的坏实现，调用点是 `self._stable_dingque_read()`，于是每一枪
        都在 `process()` 里抛 TypeError 被引擎吞掉，C 组断言照样变红 —— 那是「崩掉了」
        不是「判据改了」，等于一次假测量。调用计数为 0 就当场失败，不再当成拦住。"""
        orig = EE.Engine._stable_dingque_read
        missed = []
        try:
            for name, old, new, case in PIPELINE_MUTANTS:
                fn = _mutated_read(old, new)
                calls: list = []

                def mutant(self_, _f=fn, _c=calls):
                    _c.append(1)
                    return _f(self_)

                EE.Engine._stable_dingque_read = mutant
                tc = TestBadgePipeline("test_" + case)
                tc.setUpClass()
                tc.setUp()
                try:
                    getattr(tc, "test_" + case)()
                    missed.append(name)      # 坏实现还能让断言绿 = 没人接住
                except AssertionError as exc:
                    if not calls:
                        raise AssertionError(
                            f"{name}：坏实现一次都没被调用 —— 断言变红是崩出来的、"
                            "不是判据改出来的（假测量，当场失败）") from exc
                    print(f"[mutate] {name}：真机帧流水线上的同一断言变红——已接住"
                          f"（坏实现被调用 {len(calls)} 次）")
                finally:
                    EE.Engine._stable_dingque_read = orig
        finally:
            EE.Engine._stable_dingque_read = orig
        self.assertEqual([], missed,
                         "这个主链路变异活了下来：C 组其实没跑到被改的那段（假接线）")

    def test_wiring_mutants_are_caught(self):
        missed = []
        for name, key, good, bad in WIRING_CONTRACTS:
            srcs = dict(WIRING_SRC)
            srcs[key] = srcs[key].replace(good, bad, 1)
            self.assertNotEqual(srcs[key], WIRING_SRC[key], f"变异模板过期：{name}")
            if _guard_breaks(lambda: _assert_wiring_holds(srcs)):
                print(f"[mutate] {name}：接线契约变红——已接住（行为层由 C 组兜底）")
            else:
                missed.append(name)
        self.assertEqual([], missed, "这些接线变异活了下来：契约字符串其实不承重")


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestStabilityWindow, TestBadgePipeline, TestKnownBoundary, TestWiring):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
