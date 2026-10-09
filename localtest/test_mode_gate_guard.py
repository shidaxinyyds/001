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
⑥ 引擎必须拒绝「本平台根本没开的玩法」并回落到平台默认，且**必须把纠正说出去**
   （P2-B）。`queshen_play_02.jpg` 是同一台机器上的另一帧：面板挂着「血流红中」
   （川麻牌集，闸门只放开 7z），屏上却有 北 + 4 张 白板 → 修法前少读一张并错贴。
   只在 UI 筛选项堵不住全部：配置到达引擎有三条路（Java 推送、磁盘轮询、旧版本
   残留的 json），所以纠正必须在引擎每帧做，而纠正本身不是终点 —— 让用户知道
   「面板上那个玩法在这个平台根本不存在」才是终点。

变异检验（`py -3.10 -X utf8 localtest/test_mode_gate_guard.py --mutate`）：
  ① 的变异体：把 `Engine.__init__` 的玩法退回常量 DEFAULT_MODE（即修法前）。
  ④ 的变异体：把 `gd_queshen` 默认玩法改回装不下字牌的那条（现在是 sc_hz：
     gd_hz 已经按 ⑤ 修正成全牌，拿它当变异体不再能成立，见该用例注释）。
  ⑤ 的变异体：把 gd_hz 牌集改回 `range(27)+[33]` —— 真帧上的 北 必须掉出来。
  ⑥ 的变异体：把 `reconcile_mode_platform` 换成恒等（删掉纠正调用）—— 那帧必须
     既不报 `mode_forced`，也读不出 北/白板。
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
#   queshen_play_02.jpg 面板挂着「血流红中」（本平台未开放）：
#                              9p9p 2s 4s 5s5s5s 9s 北 白白白白 4p = 14 张
QUESHEN_HZ = ("queshen_play_01.jpg", "gd_queshen", "gd_hz",
              ["4p", "9p", "9p", "2s", "4s", "4s", "5s", "5s", "5s", "6s", "4z"])
QUESHEN_FORCED = ("queshen_play_02.jpg", "gd_queshen", "sc_hz",
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


def first_frame_valid_tiles(img, platform, mode, bypass_reconcile=False):
    """按生产口径喂**一帧**，返回首张牌打分时的允许牌集与面板手牌串。

    只取第一次 `_score_face`：那才是「第一帧」的真实状态。抓多次会把 reload 之后
    的正确状态混进来，等于替被守卫的 bug 打掩护。

    `bypass_reconcile` 只给 ② 用：② 要的对照是「川麻窄闸门 vs 全牌闸门」在同一帧
    上的形状，而 P2-B 之后 (gd_queshen, sc_hz) 已是引擎会当场拦掉的非法组合
    （见 ⑥）——生产链路里那对组合根本不存在，窄闸门的形状只能做成显式声明的
    **实验条件**，并且必须同时断言「生产链路会纠正它」，否则这条对照就是在
    测量一个已经修掉的世界。
    """
    seen = []
    orig = TencentGridDetector._score_face

    def spy(self, face, avail=None, styles=None):
        out = orig(self, face, avail, styles)
        seen.append(set(out[2]))
        return out

    orig_lp, orig_lm = E.load_platform, E.load_mode
    orig_rc = E.reconcile_mode_platform
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    if bypass_reconcile:
        E.reconcile_mode_platform = lambda m, p: (m, None)
    TencentGridDetector._score_face = spy
    try:
        eng = E.Engine()
        det = eng.get_hand_detector()          # 生产里 process 也先经这里拿通道
        with contextlib.redirect_stdout(io.StringIO()):
            data = json.loads(eng.process(img).result)
    finally:
        TencentGridDetector._score_face = orig
        E.load_platform, E.load_mode = orig_lp, orig_lm
        E.reconcile_mode_platform = orig_rc
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
        """② 同一帧：全牌玩法读出字牌，川麻玩法读不出 —— 闸门的用户可见后果。"""
        _s_full, panel_full = first_frame_valid_tiles(self.img, self.e["platform"], FULL_MODE)
        _s_sc, panel_sc = first_frame_valid_tiles(
            self.img, self.e["platform"], NO_HONOR_MODE, bypass_reconcile=True)
        # 窄闸门那半边是实验条件，不是生产状态：生产里这对组合同帧就会被换成全牌
        # 玩法（⑥）。有人日后把纠正挪走，这里必须立刻显形。
        self.assertEqual(E.reconcile_mode_platform(NO_HONOR_MODE, self.e["platform"]),
                         (PLATFORMS[self.e["platform"]]["default_mode"], NO_HONOR_MODE),
                         "② 靠旁路纠正才能测窄闸门；若这对组合已变成合法组合，"
                         "本对照的前提就该重写（不再需要旁路）")
        gt_honors = [t for t in self.e["hand"] if t.endswith("z") and t != "7z"]
        self.assertTrue(gt_honors, "夹具帧没有非 7z 字牌，本断言无从成立")
        for t in gt_honors:
            self.assertIn(t, panel_full,
                          f"玩法允许 {t} 却面板读不到（面板={panel_full}）：闸门之外的新问题")
            self.assertNotIn(t, panel_sc,
                             f"川麻牌集本应排除 {t}，面板却读出了它：闸门失效（{panel_sc}）")
        self.assertEqual(sorted(panel_full), sorted(self.e["hand"]),
                         f"全牌玩法下面板应逐位等于 GT，实为 {panel_full} vs {self.e['hand']}")

    def test_sc_mode_gate_is_the_reason_for_the_84_gap(self):
        """② 的量化版：川麻玩法下丢的必须**全是字牌**，补的**全是数牌**。

        这个形状是「玩法闸门」独有的指纹。哪天丢的是数牌，就不是这条链路，
        得回头查识别/后处理，别把新问题塞进旧结论里。
        """
        _s, panel = first_frame_valid_tiles(
            self.img, self.e["platform"], NO_HONOR_MODE, bypass_reconcile=True)
        want = self.e["hand"]
        lost = sorted(collections.Counter(want) - collections.Counter(panel))
        gained = sorted(collections.Counter(panel) - collections.Counter(want))
        self.assertTrue(lost, "川麻玩法下没丢牌：闸门假设被否证，本守卫该重写")
        self.assertTrue(all(t.endswith("z") for t in lost), f"丢的不全是字牌：{lost}")
        self.assertTrue(all(not t.endswith("z") for t in gained), f"补的不全是数牌：{gained}")

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
        self.assertIsNone(d.get("mode_forced"),
                          f"{name} 声明的 {mode} 就是本平台合法玩法，不许被悄悄换掉")
        self.assertIn("4z", panel, f"{name} 屏上明摆着一枚北，面板却没读到：{panel}")
        self.assertEqual(panel, sorted(gt),
                         f"{name} 面板手牌应逐张等于人眼真值 {sorted(gt)}，实为 {panel}")


class TestModePlatformReconcile(unittest.TestCase):
    """⑥ 引擎拒绝「本平台根本没开的玩法」，回落平台默认并把纠正说出去。"""

    def test_unsupported_mode_falls_back_to_platform_default(self):
        R = E.reconcile_mode_platform
        self.assertEqual(R("sc_hz", "gd_queshen"), ("std_tdh", "sc_hz"),
                         "血流红中不在广东雀神的玩法表里：必须回落 default 并报告原值")
        self.assertEqual(R("not_a_mode", "tencent"), ("sc_hz", "not_a_mode"),
                         "旧版本残留的野 key 也要被接住，并如实报出原值")

    def test_legal_combinations_are_never_touched(self):
        """零行为变化：所有「平台×自家玩法」必须逐字原样返回。"""
        R = E.reconcile_mode_platform
        for key, p in PLATFORMS.items():
            for m in p["supported_modes"]:
                self.assertEqual(R(m, key), (m, None), f"{key}×{m} 合法却被纠正")
            self.assertEqual(R(p["default_mode"], key), (p["default_mode"], None),
                             f"{key} 的 default_mode 自己都被纠正：玩法表不自洽")

    def test_real_frame_reports_the_correction_visibly(self):
        """⑥ 的用户可见面：挂着「血流红中」的那帧必须被换掉，且面板上得看得见。"""
        name, pf, mode, gt = QUESHEN_FORCED
        d = run_payload(multi_frame(name), pf, mode)
        panel = lc.canon_mpsz(d.get("hand", ""))
        self.assertEqual(d.get("mode"), "std_tdh", f"{name} 生效玩法应被换成 std_tdh")
        self.assertEqual(d.get("mode_forced"), "sc_hz",
                         "被纠正了却不报原值：用户看到的是面板凭空换了玩法")
        self.assertIn("已纠正", d.get("mode_name", ""),
                      f"面板标题必须带上纠正说明，实为「{d.get('mode_name')}」")
        self.assertIn("4z", panel, f"{name} 屏上有北：{panel}")
        self.assertEqual(panel.count("5z"), 4, f"{name} 屏上有 4 张白板：{panel}")
        self.assertEqual(panel, sorted(gt),
                         f"{name} 面板手牌应逐张等于人眼真值 {sorted(gt)}，实为 {panel}")


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
        """变异体 = 把 gd_hz 牌集改回 `range(27)+[33]`（P2-A 前），⑤必须报红。

        不能只看「牌集长回去了」——必须在真帧上把北弄丢，才能证明 ⑤ 真的在测
        闸门到面板的那条链路，而不是在抄一遍表。
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
        self.assertNotIn("4z", panel,
                         f"闸门改回窄牌集后面板仍读到了北（{panel}）：⑤ 在测空气")
        self.assertNotEqual(panel, sorted(gt),
                            "窄牌集与全牌牌集给出同一行手牌：识别根本没走 available_set")

    def test_mutant_identity_reconcile_breaks_6(self):
        """变异体 = 把 `reconcile_mode_platform` 换成恒等（即 P2-B 前），⑥必须报红。"""
        name, pf, mode, gt = QUESHEN_FORCED
        orig = E.reconcile_mode_platform
        E.reconcile_mode_platform = lambda m, p: (m, None)
        try:
            d = run_payload(multi_frame(name), pf, mode)
        finally:
            E.reconcile_mode_platform = orig
        self.assertEqual(d.get("mode"), "sc_hz", "恒等变异体下生效玩法仍被换掉了：接线不对")
        self.assertIsNone(d.get("mode_forced"),
                          "没纠正却报了 mode_forced：这条字段不是从纠正里来的")
        panel = lc.canon_mpsz(d.get("hand", ""))
        self.assertNotEqual(panel, sorted(gt),
                            f"恒等变异体（不纠正）竟仍能读出完整手牌：⑥ 在测空气（{panel}）")
        self.assertNotIn("4z", panel,
                         f"删掉纠正后川麻闸门仍在读北（{panel}）：那说明牌集不是从玩法来的")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：① 玩法写死成常量、④ 默认玩法退回窄牌集、"
              "⑤ gd_hz 牌集改回 112、⑥ 删掉错配纠正 —— 四条各自必须把对应主断言打红")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
