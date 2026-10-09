# -*- coding: utf-8 -*-
"""玩法闸门守卫：识别时生效的牌集必须等于**本帧声明的玩法**，且首帧也不例外。

逼出本文件的事故（全部为实测，`localtest/_probe_panel_styles_blame.py`）：
`test_hand_channel` 的面板合计 84 / 网格 91，7 张差额全在广东雀神两帧的字牌上。
逐位对账发现两侧**图、框、模板风格集全同**，唯一差别是打分时的允许牌集：

    裸通道   mode_tiles=-  -> n_valid=34 -> 3z(1.00) 6z(1.00)   ← 与 GT 一致
    引擎链路 mode_tiles=28 -> n_valid=28 -> 1p(0.43) 1s(0.41)   ← 字牌被闸门挤掉

`28` 来自 `resolve_candidate_tiles`：川麻系玩法的字牌只放开 `7z`
（tencent_grid_detector.py:107-130）。而引擎建实例时把玩法写成了常量
`DEFAULT_MODE`（'sc_hz'），真正的 `self.mode = load_mode()` 在 process 里**跑完
方向验证（含手牌识别）之后**才执行 —— 于是「新建引擎后的第一帧」按一个用户根本没
选的玩法识别。测试每帧新建 Engine，等于每帧都是第一帧，缺口才这么稳定。
自然实验同样吻合：这批帧里只含 7z 的（jj/tuyou）全对，需要 1z/3z/6z 的（queshen）全错。

本文件锁六件事：
① 首帧识别拿到的牌集 == 声明玩法的牌集（平台白名单与玩法无交集时按分类器自身的
   兜底口径算，不在此处重复实现）。
② 闸门的用户可见后果：同一帧，声明全牌玩法读出字牌、声明川麻玩法读不出。
   这条防止有人把「按玩法收窄」改成写死，也防止把「面板少字牌」错报成识别退化。
③ 夹具侧：GT 帧的玩法由 `layer_cost.mode_for` 选（能装下这批牌的最小牌集），
   装不下必须报红 —— 那是平台玩法表与素材冲突，不是识别问题。
④ 平台 default_mode 本身必须装得下该平台素材里实拍到的牌（D2）。默认玩法就是
   用户进这个平台拿到的闸门，它漏字牌时面板少字牌是**必然**，与识别质量无关。
   `gd_queshen` 已从 gd_hz（28 类、字牌只 7z）改为 std_tdh（34 全牌）；两侧一致性
   由 `localtest/test_new_modes.py` 的 Dart↔Python 对账钉，本文件不重复实现。
⑤ 广东红中王（gd_hz）的牌集必须是 34 型全牌 + wall 136（P2-A）。它曾是
   `range(27)+[33]`：那是把川麻「先删光字牌、再单加回红中」的形状照抄到广东玩法
   上，而广东麻将的底子本就是 136 全牌。真机实证（`localtest/shots_multi/`）：
   `queshen_play_01.jpg` 面板标题就是「广东红中王」，屏上明摆着一枚 北(4z)。
   闸门里没有的字牌连被模板比较的机会都没有 → 被强贴成 7z/9s/8s 还凭空少读一张。
   **用户报障原话「一些特殊的牌识别不出来，比如东西南北风」的根因就在牌集，不在模板。**
⑥ 引擎**不替用户换玩法**，但必须把「这个玩法不在这家平台的房卡清单里」说出去
   （v1.7.5 改口径）。为什么改：P2-B 曾拿 `supported_modes` 当硬闸门，把列表外的玩法
   换成平台默认，理由是「那个组合在本平台读不出牌」；而
   `localtest/measure_mode_coverage.py` 量下来——主 bank（手绘 34 面）在**每个**平台都
   常驻（`STYLE_PLATFORM_DENYLIST` 只踢附加风格、永不踢主 bank），34/34 面家家都有。
   那份清单是手写的房卡猜测而不是能力事实；拿它当闸门把用户的玩法列表从 19 种砍到
   3~6 种、且主动选了也会被改回去，比猜错房卡更坏。本条钉两面：生效玩法**逐字等于**
   声明值；选了房卡外的玩法时 payload 必须交 `mode_off_catalog`、标题必须带「未收录」。

变异检验（`py -3.10 -X utf8 localtest/test_mode_gate_guard.py --mutate`）：
  ① 的变异体：把 `Engine.__init__` 的玩法退回常量 DEFAULT_MODE（即修法前）。
  ④ 的变异体：把 `gd_queshen` 默认玩法改回装不下字牌的那条（现在是 sc_hz：
     gd_hz 已经按 ⑤ 修正成全牌，拿它当变异体不再能成立，见该用例注释）。
  ⑤ 的变异体：把 gd_hz 牌集改回 `range(27)+[33]` —— 真帧上的 北 必须掉出来。
  ⑥ 的变异体（两面各一条）：把「未收录」提示算成永远为空 → 那帧必须不再报
     `mode_off_catalog`、标题必须不再带「未收录」；把房卡核对改回「替用户换玩法」
     → 那帧的生效玩法必须不再是声明的那个（即本条改动被回退时必红）。
  若哪天有人把某条主断言削弱成空断言，对应变异体会与它互相矛盾而报红。

运行：py -3.10 -X utf8 localtest/test_mode_gate_guard.py
"""
from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import engine.engine as E  # noqa: E402
import layer_cost as lc  # noqa: E402
import modes as M  # noqa: E402
from platforms import PLATFORMS  # noqa: E402
from eval_new_material import STYLE_OF  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

MUTATE = "--mutate" in sys.argv

# 含 1z/3z/6z 的广东雀神帧：闸门收窄时它们必然掉出来（见模块 docstring）
FRAME_KEY = "45329b3e"
FULL_MODE = "hz_bd"       # 34 类全字牌（gd_queshen 的 supported_modes 之一）
NO_HONOR_MODE = "sc_hz"   # 川麻：字牌只放开 7z
GT_B1 = os.path.join(HERE, "gt", "shots_b1.json")   # 多平台 GT（91 帧）
MULTI = os.path.join(HERE, "shots_multi")           # 用户真机 20 帧（已进仓）

# ⑤⑥ 钉住的两帧广东雀神。手牌真值 = **人眼逐帧读屏**（拼屏工具
# `localtest/montage_hands.py`，一批 20 帧里只有这两帧与读数不符），不是引擎输出：
#   queshen_play_01.jpg 面板「广东红中王」：4p 9p9p 2s 4s 5s5s5s 6s 北 4s = 11 张
#   queshen_play_02.jpg 面板挂着「血流红中」（不在这家房卡里，v1.7.5 起照用户说的跑）：
#                              9p9p 2s 4s 5s5s5s 9s 北 白白白白 4p = 14 张
# 两帧的差别就是 ⑥ 的两面：房卡里的玩法逐张等于真值，房卡外的玩法**被如实读出窄闸门
# 的代价**（丢北/白板）并同时自报不可信 —— 见 TestModePlatformNote。
QUESHEN_HZ = ("queshen_play_01.jpg", "gd_queshen", "gd_hz",
              ["4p", "9p", "9p", "2s", "4s", "4s", "5s", "5s", "5s", "6s", "4z"])
QUESHEN_OFF_CATALOG = ("queshen_play_02.jpg", "gd_queshen", "sc_hz",
                  ["9p", "9p", "2s", "4s", "5s", "5s", "5s", "9s",
                   "4z", "5z", "5z", "5z", "5z", "4p"])

# 平台 default_mode 装不下自家素材已标注牌的**待裁决冲突台账**（不是已解决）。
# 实测（2026-10，py 读 gt/shots_b1.json 逐平台对账）：
#   tencent  default=sc_hz  素材里另有 1z/3z/4z/5z/6z
#   weile    default=sc_xz  素材里另有 2z/4z
# 这两条与被修好的 gd_queshen 是**同一形状**（素材有非 7z 字牌，默认川麻玩法装
# 不下），但用户本轮只拍板了 gd_queshen，所以如实登记而**不**顺手改玩法语义。
# 台账是双向棘轮：台账外的平台出现差额 → 红（新冲突必须显式裁决）；台账内的
# 差额消失或变形 → 也红（那条要么该删、要么该重写，不许留着当历史）。
DEFAULT_MODE_CONFLICTS = {
    "tencent": ["1z", "3z", "4z", "5z", "6z"],
    "weile": ["2z", "4z"],
}


def _i2l(i):
    from trainer.utils.convert import tiles34_index_to_mpsz
    return tiles34_index_to_mpsz(i)


def mode_labels(key):
    """玩法 key -> 允许出现的牌的 mpsz 集合（与分类器同一把尺：available_set）。"""
    return {_i2l(i) for i in M.available_set(key)}


def entry():
    for e in lc.load_entries("new"):
        if FRAME_KEY in os.path.basename(e["path"]) and os.path.exists(e["path"]):
            return e
    raise unittest.SkipTest(f"缺夹具帧 {FRAME_KEY}（public/0 未就位）")


def first_frame_valid_tiles(img, platform, mode):
    """按生产口径喂**一帧**，返回首张牌打分时的允许牌集与面板手牌串。

    只取第一次 `_score_face`：那才是「第一帧」的真实状态。抓多次会把 reload 之后
    的正确状态混进来，等于替被守卫的 bug 打掩护。

    只认「没传 avail」的那批调用：牌河/副露现在也用同一个网格检测器分类，而
    `detect_river_discards` 会显式传 `avail=available_set(mode)`；手牌通道不传 avail
    （用 `_mode_tiles`）。不按这个区分，`seen[0]` 会随机变成牌河那一次，① 与它的
    变异体就都在测一个不确定的东西。

    为什么不用「主线程」过滤：试过，错得更隐蔽——检测器内部本身就是多线程打分
    （`PARALLEL_WORKERS`），按线程筛会把**所有**手牌调用也剔掉，`seen` 直接空掉。
    这是修测试的口径，不是放宽断言。
    """
    seen = []
    orig = TencentGridDetector._score_face

    def spy(self, face, avail=None, styles=None):
        out = orig(self, face, avail, styles)
        if avail is None:
            seen.append(set(out[2]))
        return out

    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    TencentGridDetector._score_face = spy
    try:
        eng = E.Engine()
        det = eng.get_hand_detector()          # 生产里 process 也先经这里拿通道
        with contextlib.redirect_stdout(io.StringIO()):
            data = json.loads(eng.process(img).result)
    finally:
        TencentGridDetector._score_face = orig
        E.load_platform, E.load_mode = orig_lp, orig_lm
    return seen, lc.canon_mpsz(data.get("hand", ""))


def run_payload(img, platform, mode):
    """按声明的 (平台, 玩法) 跑一帧，返回引擎完整 payload。

    与 `first_frame_valid_tiles` 同一套钉口径方式（只改 engine 命名空间里的
    `load_platform`/`load_mode`，绝不写磁盘 —— 写磁盘会在仓库外留下全局配置，
    把下一轮不钉口径的评测静默改成别的平台，见 `eval_base.pin_config`）。
    ⑥ 要看的是「引擎自己说纠正了什么」，那只在 payload 里，不在首张打分的牌集里。
    """
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    try:
        eng = E.Engine()
        eng.get_hand_detector()              # 与生产同序：先推牌风/牌集再跑帧
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm


def multi_frame(name):
    """读 shots_multi 帧；**缺夹具直接报红而不 skip**（拿不存在的夹具自证是假绿灯）。"""
    p = os.path.join(MULTI, name)
    img = cv2.imread(p) if os.path.exists(p) else None
    if img is None:
        raise AssertionError(f"真机夹具帧缺失/读不出来，守卫在测空气：{p}")
    return img


class TestModeGateWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = entry()
        cls.img = cv2.imread(cls.e["path"])
        if cls.img is None:
            raise unittest.SkipTest(f"夹具帧读不出来：{cls.e['path']}")

    def test_first_frame_uses_the_declared_mode(self):
        """① 首帧打分的允许牌集必须等于声明玩法的牌集。"""
        seen, _panel = first_frame_valid_tiles(self.img, self.e["platform"], FULL_MODE)
        self.assertTrue(seen, "首帧没有任何一次牌面打分：本断言是空的")
        want = mode_labels(FULL_MODE)
        self.assertEqual(seen[0], want,
                         f"首帧牌集 {len(seen[0])} 类 != 声明玩法 {FULL_MODE} 的 "
                         f"{len(want)} 类（差 {sorted(want ^ seen[0])[:6]}）："
                         "识别跑在玩法 reload 之前，用户声明的玩法被无视")

    def test_honors_exist_only_when_the_mode_allows_them(self):
        """② 全牌玩法必须读出屏上每一枚字牌（窄玩法那一半由 ②b 钉）。"""
        _s_full, panel_full = first_frame_valid_tiles(self.img, self.e["platform"], FULL_MODE)
        # 前提（不是空断言）：这帧的平台房卡里没这条窄牌集玩法——v1.7.5 之后引擎
        # 不拦它、只提示，所以 ②b 能直接走生产链路测窄闸门；若这对组合哪天进了
        # 房卡，本对照仍成立，但 ⑥ 的「未收录」那一面就少了个真夹具，得换帧。
        self.assertNotIn(NO_HONOR_MODE, PLATFORMS[self.e["platform"]]["supported_modes"],
                         "② 用窄牌集做对照的前提变了：这对组合已进了房卡清单")
        gt_honors = [t for t in self.e["hand"] if t.endswith("z") and t != "7z"]
        self.assertTrue(gt_honors, "夹具帧没有非 7z 字牌，本断言无从成立")
        for t in gt_honors:
            self.assertIn(t, panel_full,
                          f"玩法允许 {t} 却面板读不到（面板={panel_full}）：闸门之外的新问题")
        self.assertEqual(sorted(panel_full), sorted(self.e["hand"]),
                         f"全牌玩法下面板应逐位等于 GT，实为 {panel_full} vs {self.e['hand']}")

    def test_out_of_gate_tiles_are_never_read_without_a_trail(self):
        """②b 闸门外的牌要么不读，要么读了必须留痕（`hand_gate_conflict`）。

        为什么不再断言「窄玩法必读不出字牌」：用户报的是「字牌认不出」，而「把屏上
        明摆着的牌读对」不该以玩法为条件（v1.7.6 的 `_rescue_out_of_gate`）。但救回来的
        每张都得能查到：没有留痕的门外读数就是面板凭空多出的牌，比少报更难发现。
        """
        d = run_payload(self.img, self.e["platform"], NO_HONOR_MODE)
        panel = lc.canon_mpsz(d.get("hand", ""))
        trail = {c[1] for c in (d.get("hand_gate_conflict") or [])}
        gate = mode_labels(NO_HONOR_MODE)
        outside_read = [t for t in panel if t not in gate]
        self.assertTrue(outside_read,
                        "这帧在窄玩法下没读到任何门外牌：本条退化成空断言，得换帧")
        self.assertEqual(set(outside_read), set(outside_read) & trail,
                         f"读到了闸门外的牌却没全部留痕：读到={outside_read} 留痕={trail}")
        for t in trail:
            self.assertNotIn(t, gate, f"留痕里的 {t} 本来就在牌集里：这条字段在测空气")
            self.assertIn(t, set(self.e["hand"]),
                          f"留痕说屏上有 {t}，人眼真值里却没有：救错了牌")
        # 数牌一张不许因为这道补打分而变动：门外读数只允许是字牌
        self.assertTrue(all(t.endswith("z") for t in trail),
                        f"补打分把数牌也换掉了（这不是该救的形状）：{trail}")

    def test_sc_mode_gate_is_the_reason_for_the_84_gap(self):
        """② 的量化版：窄玩法相对全牌玩法的差额**只能落在字牌上**。

        这个形状是「玩法闸门」独有的指纹。哪天丢的是数牌，就不是这条链路，
        得回头查识别/后处理，别把新问题塞进旧结论里。（v1.7.6 后差额可能为空——
        那正是补打分把字牌救回来了，所以这里不再强求「必须丢牌」。）
        """
        _s, panel = first_frame_valid_tiles(
            self.img, self.e["platform"], NO_HONOR_MODE)
        want = self.e["hand"]
        lost = sorted(collections.Counter(want) - collections.Counter(panel))
        gained = sorted(collections.Counter(panel) - collections.Counter(want))
        self.assertTrue(all(t.endswith("z") for t in lost),
                        f"窄玩法丢掉了数牌（闸门不该影响数牌）：{lost}")
        self.assertTrue(all(t.endswith("z") for t in gained),
                        f"窄玩法凭空多出数牌：{gained}")

    def test_fixture_mode_selection_covers_the_gt(self):
        """③ 夹具侧：每帧声明的玩法必须装得下该帧 GT；装不下要留在台账里。"""
        bad = []
        for e in lc.load_entries("new"):
            legal = mode_labels(e["mode"]) if e.get("mode") else set()
            if not set(e["hand"]) <= legal:
                bad.append((os.path.basename(e["path"])[:12], e.get("mode"),
                            sorted(set(e["hand"]) - legal)))
        self.assertEqual(bad, [], f"这些帧的声明玩法装不下 GT：{bad}")


class TestDefaultModeCoversMaterial(unittest.TestCase):
    """④ 平台默认玩法就是用户拿到的闸门：它漏字牌，面板就必然漏字牌。"""

    @staticmethod
    def measured():
        """平台 -> (default_mode, 该平台素材已标注牌里 default_mode 装不下的部分)。

        `?` 是人眼读不准的占位格（不是一张牌），拿它去要求玩法「装得下」是假冲突。
        """
        if not os.path.exists(GT_B1):
            raise unittest.SkipTest(f"缺多平台 GT：{GT_B1}")
        with open(GT_B1, encoding="utf-8") as fp:
            shots = json.load(fp)["shots"]
        labeled = collections.defaultdict(set)
        for e in shots:
            if not e.get("verified"):
                continue
            pf = STYLE_OF.get(e["style"])
            if pf in PLATFORMS:
                labeled[pf] |= {t for t in e["hand"] if t != "?"}
        return {pf: (PLATFORMS[pf]["default_mode"],
                     sorted(tiles - mode_labels(PLATFORMS[pf]["default_mode"])))
                for pf, tiles in labeled.items()}

    def test_queshen_default_mode_can_hold_its_own_material(self):
        """被 D2 修好的那家必须自洽：这条是本次改动的直接验收，不许藏进台账。"""
        got = self.measured()
        mode, miss = got["gd_queshen"]
        self.assertEqual(miss, [],
                         f"gd_queshen 默认玩法又退回装不下字牌的那条（{mode} 缺 {miss}）："
                         "素材里实拍到 東/西/發，默认玩法=识别闸门，"
                         "这条一旦回退，面板少字牌会伪装成识别退化（见模块 docstring）")

    def test_conflicts_are_exactly_the_registered_ledger(self):
        """其余平台的差额必须**逐字**等于台账：新冲突要显式裁决，修好的该从台账删。"""
        got = self.measured()
        conflicts = {pf: miss for pf, (_m, miss) in sorted(got.items()) if miss}
        self.assertEqual(conflicts, DEFAULT_MODE_CONFLICTS,
                         f"默认玩法/素材冲突台账与实测不一致。实测={conflicts}，"
                         f"台账={DEFAULT_MODE_CONFLICTS}：新出现的条目必须像 gd_queshen "
                         "那样裁决（改默认玩法或扩牌集，都是玩法语义决定，要人拍板）；"
                         "消失/变形的条目说明有人已改动，本守卫不知道该改动是否覆盖全部素材")

    def test_default_mode_actually_reads_the_honors(self):
        """④ 的用户可见面：只按平台默认玩法喂（等价于用户刚切到这个平台），
        那帧的東/西/發 必须出现在面板上 —— 不再依赖评测端替它挑玩法。"""
        e = entry()
        img = cv2.imread(e["path"])
        if img is None:
            raise unittest.SkipTest(f"夹具帧读不出来：{e['path']}")
        default = PLATFORMS[e["platform"]]["default_mode"]
        _seen, panel = first_frame_valid_tiles(img, e["platform"], default)
        self.assertEqual(sorted(panel), sorted(e["hand"]),
                         f"按平台默认玩法（{default}）跑，面板应逐位等于 GT，"
                         f"实为 {panel} vs {e['hand']}")


class TestGdHzTileset(unittest.TestCase):
    """⑤ 广东红中王必须是 34 型全牌（用户报障「东西南北风认不出来」的真根因）。"""

    def test_gate_holds_all_seven_honors(self):
        avail = M.available_set("gd_hz")
        for i in range(27, 34):
            self.assertIn(i, avail,
                          f"字牌 {_i2l(i)} 不在 gd_hz 牌集里：它在分类之前就被闸门挤掉，"
                          "模板连被比较的机会都没有（表现是「特殊牌认不出/漏识别」）")
        self.assertEqual(len(avail), 34, f"gd_hz 牌集应 34 类，实为 {len(avail)}")
        self.assertEqual(M.MODES["gd_hz"]["wall"], 136)
        self.assertEqual(M.MODES["gd_hz"]["wall"], len(avail) * 4,
                         "契约 wall == len(available)*4（与 test_new_modes 同一把尺）")
        self.assertEqual(M.MODES["gd_hz"]["laizi"], 33,
                         "红中升为鬼牌是这个玩法唯一与全牌不同的地方")

    def test_real_frame_reads_the_beit(self):
        """⑤ 的用户可见面：面板标题「广东红中王」那帧，北(4z) 必须上屏。"""
        name, pf, mode, gt = QUESHEN_HZ
        d = run_payload(multi_frame(name), pf, mode)
        panel = lc.canon_mpsz(d.get("hand", ""))
        self.assertIsNone(d.get("mode_off_catalog"),
                          f"{name} 声明的 {mode} 就在本平台房卡里，不该报「未收录」")
        self.assertIn("4z", panel, f"{name} 屏上明摆着一枚北，面板却没读到：{panel}")
        self.assertEqual(panel, sorted(gt),
                         f"{name} 面板手牌应逐张等于人眼真值 {sorted(gt)}，实为 {panel}")


class TestModePlatformNote(unittest.TestCase):
    """⑥ 引擎不替用户换玩法；但「房卡未收录」必须说出去，错声明的代价也得看得见。"""

    def test_declared_mode_is_always_honored(self):
        R = E.reconcile_mode_platform
        self.assertEqual(R("sc_hz", "gd_queshen"), ("sc_hz", "sc_hz"),
                         "血流红中不在广东雀神房卡里：现在必须照用户说的跑，并报未收录")
        self.assertEqual(R("not_a_mode", "tencent"), ("not_a_mode", "not_a_mode"),
                         "旧版本残留的野 key 也不许被悄悄换成别的（它走 modes 自己的兜底牌集）")

    def test_legal_combinations_are_never_touched(self):
        """在房卡里的玩法连提示都不许有：否则「未收录」会喊成狼来了。"""
        R = E.reconcile_mode_platform
        for key, p in PLATFORMS.items():
            for m in p["supported_modes"]:
                self.assertEqual(R(m, key), (m, None), f"{key}×{m} 在房卡里却被报未收录")
            self.assertEqual(R(p["default_mode"], key), (p["default_mode"], None),
                             f"{key} 的 default_mode 自己都被报未收录：玩法表不自洽")

    def test_real_frame_keeps_the_mode_and_says_it_out_loud(self):
        """⑥ 的用户可见面：挂着「血流红中」的那帧——玩法照旧生效、标题多一句未收录，
        而屏上明摆着的 北/白板 必须被读回来并逐张留在 `hand_gate_conflict` 里。"""
        name, pf, mode, gt = QUESHEN_OFF_CATALOG
        d = run_payload(multi_frame(name), pf, mode)
        panel = lc.canon_mpsz(d.get("hand", ""))
        self.assertEqual(d.get("mode"), mode,
                         f"{name} 生效玩法被引擎换成了 {d.get('mode')}：v1.7.5 不再替用户换玩法")
        self.assertEqual(d.get("mode_off_catalog"), mode,
                         "房卡外的玩法必须报出来，否则面板与实桌不一致时没人知道从哪查")
        self.assertIn("未收录", d.get("mode_name", ""),
                      f"标题必须把这件事说出口，实为「{d.get('mode_name')}」")
        # v1.7.6：错玩法不再拿用户的牌当代价——屏上的 北 与 4 张白板必须读回来，
        # 并且每一张都得在留痕里（否则就是“面板凭空多出字牌”那种更难发现的坑）。
        self.assertIn("4z", panel, f"{name} 屏上有北却没读回来：{panel}")
        self.assertEqual(panel.count("5z"), 4, f"{name} 屏上有 4 张白板：{panel}")
        self.assertEqual(panel, sorted(gt),
                         f"{name} 面板应逐张等于人眼真值 {sorted(gt)}，实为 {panel}")
        trail = {c[1] for c in (d.get("hand_gate_conflict") or [])}
        # 川麻牌集只放开 7z，所以这帧上被救回来的只可能是 北(4z) 与 白板(5z)。
        self.assertEqual(trail, {"4z", "5z"},
                         f"留痕与读数对不上：读到={panel} 留痕={trail}")
        self.assertEqual(int(d.get("hand_missing") or 0), 0,
                         "读完了却还报「有框没读出」：两条口径在打架")


class TestMutationControls(unittest.TestCase):
    """变异体 = 修法本身被回退：每条主断言（①④⑤⑥）都必须有一条能把它打红的对照。"""

    @classmethod
    def setUpClass(cls):
        cls.e = entry()
        cls.img = cv2.imread(cls.e["path"])
        if cls.img is None:
            raise unittest.SkipTest(f"夹具帧读不出来：{cls.e['path']}")

    def test_mutant_constant_mode_breaks_the_first_frame(self):
        orig_init = E.Engine.__init__
        default = M.DEFAULT_MODE

        def mutant(self, *a, **k):
            orig_init(self, *a, **k)
            self.mode = default          # 即改动前：建表时的常量，与用户声明无关

        E.Engine.__init__ = mutant
        try:
            seen, _panel = first_frame_valid_tiles(self.img, self.e["platform"], FULL_MODE)
        finally:
            E.Engine.__init__ = orig_init
        self.assertTrue(seen, "变异体下首帧没有任何打分，对照不成立")
        want = mode_labels(FULL_MODE)
        self.assertNotEqual(seen[0], want,
                            "变异体（玩法写死成常量）居然还满足①：说明①在测空气")
        self.assertLess(len(seen[0]), len(want),
                        f"变异体的首帧牌集 {len(seen[0])} 应比声明玩法 {len(want)} 窄"
                        "（DEFAULT_MODE 是川麻）——形状不对就换人了")

    def test_mutant_old_default_mode_breaks_4(self):
        """变异体 = 把 gd_queshen 默认玩法改回装不下字牌的那条，④必须报红。

        这里用的是 sc_hz 而不是改动前的 gd_hz：P2-A 已把 gd_hz 修正成 34 型全牌
        （见 ⑤），它现在**装得下**字牌，拿它当变异体会报不出红——那不是 ④ 失效，
        而是那条缺陷在这个 key 上已不存在。sc_hz（川麻：字牌只放开 7z）仍是真实
        存在的同类形状，且正是用户把「血流红中」挂到广东雀神时引擎要拦住的那一条。

        ④ 的两条实测断言都靠 `PLATFORMS` 现算，所以这条回退必须立刻显形：
        要么「自洽」断言拿到非空差额，要么冲突台账多出一条未登记的。
        """
        key = "gd_queshen"
        orig = PLATFORMS[key]["default_mode"]
        PLATFORMS[key]["default_mode"] = "sc_hz"      # 28 类、字牌只 7z
        try:
            got = TestDefaultModeCoversMaterial.measured()
        finally:
            PLATFORMS[key]["default_mode"] = orig
        _mode, miss = got[key]
        self.assertTrue(miss,
                        "变异体（默认玩法回退成装不下字牌的那条）下实测差额仍为空：④ 在测空气")
        self.assertTrue(all(t.endswith("z") and t != "7z" for t in miss),
                        f"变异体丢的不全是非 7z 字牌：{miss}（形状不对，换人了）")
        self.assertNotIn(key, DEFAULT_MODE_CONFLICTS,
                         "gd_queshen 已按 D2 修好，不该再躺在待裁决台账里")

    def test_mutant_shrunk_gd_hz_gate_breaks_5(self):
        """变异体 = 把 gd_hz 牌集改回 `range(27)+[33]`（P2-A 前），⑤ 必须报红。

        v1.7.6 之后“北掉出去”不再是可观察后果（补打分把它救回来），可观察的是：
        牌集一旦变窄，北 就从「合法读数」变成「门外读数」——它必须出现在留痕里。
        没留痕只有两种可能：牌集根本没进闸门，或者闸门失效。两者都得红。
        """
        name, pf, mode, gt = QUESHEN_HZ
        entry_m = M.MODES["gd_hz"]
        orig_avail, orig_wall = entry_m["available"], entry_m["wall"]
        entry_m["available"] = list(range(27)) + [33]   # 只留红中一枚字牌
        entry_m["wall"] = 112
        try:
            d = run_payload(multi_frame(name), pf, mode)
        finally:
            entry_m["available"], entry_m["wall"] = orig_avail, orig_wall
        panel = lc.canon_mpsz(d.get("hand", ""))
        trail = {c[1] for c in (d.get("hand_gate_conflict") or [])}
        self.assertIn("4z", trail,
                      f"闸门改回窄牌集后，北 既没被挡在外面也没留痕（面板={panel} "
                      f"留痕={trail}）：⑤ 在测空气，available_set 根本没进闸门")

    def test_mutant_silenced_note_breaks_6(self):
        """变异体 = 把「未收录」提示算成永远为空，⑥ 必须报红。"""
        name, pf, mode, gt = QUESHEN_OFF_CATALOG
        orig = E.reconcile_mode_platform
        E.reconcile_mode_platform = lambda m, p: (m, None)
        try:
            d = run_payload(multi_frame(name), pf, mode)
        finally:
            E.reconcile_mode_platform = orig
        self.assertIsNone(d.get("mode_off_catalog"),
                          "恒等变异体仍报 mode_off_catalog：这个字段不是从房卡核对来的")
        self.assertNotIn("未收录", d.get("mode_name", ""),
                         "删掉房卡核对后标题仍写着未收录：那句话另有来源，本字段在测空气")

    def test_mutant_silent_substitution_breaks_6(self):
        """变异体 = 把房卡核对改回 P2-B 的「替用户换玩法」，⑥ 必须报红。

        这一条钉的是本次改口径的方向：不是「提示漏了」而是「行为被改回去了」。
        生效玩法一旦不再是声明值，用户选什么都等于没选。"""
        name, pf, mode, gt = QUESHEN_OFF_CATALOG
        orig = E.reconcile_mode_platform

        def old(m, p):
            sup = PLATFORMS[p]["supported_modes"]
            return (m, None) if m in sup else (PLATFORMS[p]["default_mode"], m)

        E.reconcile_mode_platform = old
        try:
            d = run_payload(multi_frame(name), pf, mode)
        finally:
            E.reconcile_mode_platform = orig
        # 变异体必须给出与生产**不同**的可观察结果，否则 ⑥ 那条「生效玩法==声明玩法」
        # 根本钉不住这件事（只写「断言会红」而不验红，就是空断言）。
        self.assertNotEqual(d.get("mode"), mode,
                            f"改回偷换玩法后生效玩法仍等于声明值（{mode}）：⑥ 在测空气")
        self.assertEqual(d.get("mode"), PLATFORMS[pf]["default_mode"],
                         f"变异体没按旧写法回落平台默认，而是给了 {d.get('mode')}：对照不成立")
        panel = lc.canon_mpsz(d.get("hand", ""))
        self.assertIn("4z", panel,
                      f"换成全牌玩法后仍读不出北（{panel}）：那 ⑥ 的丢牌后果在测另一件事")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：① 玩法写死成常量、④ 默认玩法退回窄牌集、"
              "⑤ gd_hz 牌集改回 112、⑥ 静默提示 / ⑥ 偷换玩法 —— 五条各自必须把对应主断言打红")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
