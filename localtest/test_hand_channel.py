# -*- coding: utf-8 -*-
"""手牌通道守卫：一行的牌由哪个识别器读，以及换通道后接线有没有断。

背景（本轮实测，同一帧、两侧都按平台声明的生产口径 —— `localtest/layer_cost.py`）：
  新素材 4 帧 52 张（模板未收割过，可外推）：模板 NCC 50/52=96.2%，
  而 Engine 主检测器 YOLODetector 只有 44/52=84.6%；腾讯底座 253 张是 100% vs 87.0%。
  也就是说瓶颈从来不在 Engine 后处理（门槛丢的牌被补槽抵消回去，净损失 0 张），
  而在“手牌行走哪条通道”。引擎因此新增 `get_hand_detector()`：已挂本家 bank 的
  平台，手牌行走模板 NCC；牌河/副露/阶段探测仍走主检测器。

本文件锁四件事，每件都是“改回旧写法就必红”的断言，不是结果快照：
1. `banked_platforms()` 由 EXTRA_BANKS + 平台白名单推导（新平台收割完 bank 就自动
   进清单，不需要改引擎），且清单里每个平台在**已加载**的模板里真有本家字模 ——
   清单说有、bank 没挂上，等于拿别家牌风认这家的牌（雀神补 bank 前实测 2/14）。
2. 路由：有 bank 平台→网格；无 bank 平台→主检测器；`hand_grid` 关掉→主检测器
   （回退口子必须一直有效，真机出问题时调试页能一键退回旧行为）。
3. `process` 真的用手牌通道取 rows（首个 detect_all_rows 落在网格实例上），
   且 4 帧合计命中跟着涨到网格水平（回退成主检测器就会掉回 44/52）。
4. rows 缓存的来源门：出处与当前通道不一致不得复用（否则方向探测用主检测器算的
   rows 会被当成网格结果直接上屏，换了通道也等于没换）；一致时必须复用（否则同帧
   白付两遍检测的钱）。这一条用配对断言：同一份 rows 只改来源标记，结果必须相反。

夹具：`public/0` 的新素材人工 GT（`localtest/gt/new_shots.json`）。仓库不跟踪任何
测试图片（见 docs/new_platform_onboarding.md），本文件只在本地跑；夹具缺失时**判红**
而不是静默跳过 —— 静默跳过的门禁看起来是绿的，实际什么都没测。

运行：py -3.10 localtest/test_hand_channel.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import engine.engine as E  # noqa: E402
from engine.engine import Engine  # noqa: E402
from modes import available_set  # noqa: E402
from platforms import PLATFORMS  # noqa: E402
from recognition.tencent_grid_detector import (EXTRA_BANKS,  # noqa: E402
                                              STYLE_PLATFORM_WHITELIST,
                                              TencentGridDetector,
                                              banked_platforms)
from eval_base import canon_mpsz, tile_accuracy  # noqa: E402
from layer_cost import load_entries, _hand_row, _labels  # noqa: E402
from trainer.utils.convert import tiles34_index_to_mpsz  # noqa: E402

MAIN_BANK_STYLE = "tencent"   # 主 bank 的风格名（_build_cores 里固定）


class _FakeDet:
    """只记录“被怎么用”的假识别器：路由与来源标记不该依赖真实图片才能验证。"""

    def __init__(self):
        self.pushed = []

    def set_platform_styles(self, platform_key):
        self.pushed.append(("styles", platform_key))

    def set_mode_tiles(self, avail):
        self.pushed.append(("tiles", len(avail)))


class _HandFake(_FakeDet):
    pass


class YOLODetector(_FakeDet):
    """类名是接线判据的一部分（手牌通道只接管 YOLO 主检测器），不要改名。"""


class _CustomDetector(_FakeDet):
    """测试/调试页通过覆盖 get_detector 注入的第三方检测器。"""


def derive_banked():
    """独立重算一遍清单：引擎侧只认这个推导，不接受硬编码平台名。"""
    out = {MAIN_BANK_STYLE}
    for _mod, style in EXTRA_BANKS:
        owners = STYLE_PLATFORM_WHITELIST.get(style)
        out |= set(owners) if owners else {style}
    return out


class TestBankedListIsDerived(unittest.TestCase):
    def test_list_equals_the_registry_derivation(self):
        self.assertEqual(set(banked_platforms()), derive_banked(),
                         "清单与 bank 注册表推导结果不一致：多半是有人图省事写死了平台名")

    def test_platforms_without_bank_stay_off_the_list(self):
        unbanked = [k for k in PLATFORMS if k not in set(banked_platforms())]
        self.assertGreaterEqual(
            len(unbanked), 1,
            "所有平台都在清单里 = 这条门禁失去区分力（新平台还没收割就被放行走 NCC）")
        for k in unbanked:
            self.assertNotIn(k, banked_platforms())

    def test_listed_platforms_really_have_own_glyphs(self):
        """清单说“这个平台有本家字模”，就必须真在已加载的模板里能数出来。

        bank 模块 import 失败时 TencentGridDetector 只 print 一行 warning 就继续跑，
        于是清单照样列着它 —— 引擎会拿腾讯字模去认别家牌，正是加清单前踩过的坑。
        """
        det = TencentGridDetector()
        self.assertTrue(det.is_available, "主 bank 都没加载，本用例没有意义")
        for p in sorted(set(banked_platforms()) - {MAIN_BANK_STYLE}):
            det.set_platform_styles(p)
            allowed = det.active_styles or set()
            own = {c[1] for c in det._cores if c[1] in allowed} - {MAIN_BANK_STYLE}
            self.assertTrue(
                own,
                f"{p} 在 banked_platforms() 里，但当前模板里没有它的本家字模 —— "
                f"bank 模块没挂上/被裁掉了，手牌走 NCC 只会认错")


class TestChannelRouting(unittest.TestCase):
    def setUp(self):
        self.eng = Engine()
        self.primary = YOLODetector()
        self.hand = _HandFake()
        self.eng._detector = self.primary        # 不再懒加载真检测器
        self.eng._hand_detector = self.hand      # 预置后 get_hand_detector 直接复用
        self.eng._hand_bank_key = None

    def test_banked_platform_routes_hand_row_to_grid(self):
        self.eng.platform = MAIN_BANK_STYLE
        self.assertIs(self.eng.get_hand_detector(), self.hand)

    def test_unbanked_platform_keeps_primary_detector(self):
        for k in [k for k in PLATFORMS if k not in set(banked_platforms())]:
            self.eng.platform = k
            self.assertIs(self.eng.get_hand_detector(), self.primary,
                          f"{k} 没有本家字模却走了 NCC 通道")

    def test_switch_off_falls_back_to_primary(self):
        self.eng.platform = MAIN_BANK_STYLE
        self.eng.set_config("hand_grid", False)
        try:
            self.assertIs(self.eng.get_hand_detector(), self.primary,
                          "hand_grid 开关失效：真机需要一键退回旧行为时退不了")
        finally:
            self.eng.set_config("hand_grid", True)

    def test_injected_custom_detector_is_not_bypassed(self):
        """注入面必须被尊重。

        “覆盖 get_detector 假货再驱 process”是这批守卫的统一写法（
        test_process_stability / test_tile_ledger_e2e 等）。手牌通道要是绕过
        注入自己去建真模板，这些套件测的就不是它们写入的那份检测了。
        """
        custom = _CustomDetector()
        self.eng._detector = custom
        self.eng.platform = MAIN_BANK_STYLE
        self.assertIs(self.eng.get_hand_detector(), custom,
                      "主检测器不是 YOLO 却还是另建了一条手牌通道")

    def test_bank_is_pushed_once_per_platform_mode_key(self):
        self.eng.platform = MAIN_BANK_STYLE
        self.eng.get_hand_detector()
        self.eng.get_hand_detector()
        self.assertEqual(len([x for x in self.hand.pushed if x[0] == "styles"]), 1,
                         "每帧都重推白名单：等于把 set_platform_styles 的开销乘上帧率")

        self.eng.platform = "tuyou" if "tuyou" in PLATFORMS else "generic"
        self.eng.get_hand_detector()
        last = [x for x in self.hand.pushed if x[0] == "styles"][-1]
        self.assertEqual(last, ("styles", self.eng.platform),
                         "平台变了没重推白名单：会用上一家的牌风认这一家的牌")

    def test_platform_switch_invalidates_the_key(self):
        # set_platform_explicit 会写手机绝对路径并改模块级全局，测试里替成空操作：
        # 本用例要验的是“平台切换作废缓存键”，不是磁盘写入。
        self.eng.platform = MAIN_BANK_STYLE
        self.eng.get_hand_detector()
        self.assertIsNotNone(self.eng._hand_bank_key)
        orig = E.set_platform_explicit
        E.set_platform_explicit = lambda *a, **k: None
        # 选一个同样已挂 bank 的平台：切到无 bank 的平台会直接走主检测器，
        # 根本不会推白名单，测不到“重推”这一步。
        target = next(p for p in sorted(banked_platforms()) if p != MAIN_BANK_STYLE)
        try:
            self.eng.set_platform(target)
        finally:
            E.set_platform_explicit = orig
        self.assertIsNone(self.eng._hand_bank_key)
        self.eng.get_hand_detector()
        self.assertEqual([x for x in self.hand.pushed if x[0] == "styles"][-1],
                         ("styles", target))


class TestCacheSource(unittest.TestCase):
    def test_cache_rows_records_the_channel(self):
        eng = Engine()
        rows = [[((0, 0, 10, 20), "1m", 0.9)]]
        eng._cache_rows(_HandFake(), rows)
        self.assertEqual(eng._cached_rows_src, "_HandFake")
        self.assertEqual(eng._cached_rows, rows)
        eng._cache_rows(None, rows)
        self.assertIsNone(eng._cached_rows_src, "来源记空 = 复用门形同虚设")

    def test_orientation_fast_path_caches_from_the_hand_channel(self):
        """方向快检顺手算的 rows 必须出自手牌通道、并标着手牌通道。

        这一段每帧都跑。它要是还按主检测器算，process 要么因来源不匹配重跑
        一遍（同帧付两遍检测的钱），要么网格结果根本没被缓存。
        """
        ih, iw = 720, 1280
        labels = [f"{i}m" for i in range(1, 10)] + [f"{i}p" for i in range(1, 6)]
        row = [((100 + 45 * k, int(ih * 0.82), 40, 90), lb, 0.9)
               for k, lb in enumerate(labels)]
        hand = _HandFake()
        hand.detect_all_rows = lambda image, *a, **k: [row]
        eng = Engine()
        eng._detector = YOLODetector()            # 不建真 YOLO，也不走注入接管
        eng.platform = MAIN_BANK_STYLE
        eng._hand_detector = hand
        eng._hand_bank_key = (MAIN_BANK_STYLE, eng.mode)   # 跳过白名单推送
        eng._orient = 0
        with contextlib.redirect_stdout(io.StringIO()):
            eng._apply_orientation(np.zeros((ih, iw, 3), dtype=np.uint8))
        self.assertEqual(eng._cached_rows_src, "_HandFake",
                         "方向快检不是用手牌通道算的：缓存来源与实际通道不符")
        self.assertEqual(eng._cached_rows, [row])


class TestPanelFollowsHandChannel(unittest.TestCase):
    """端到端：面板显示的手牌必须出自手牌通道，且命中率跟着通道一起涨。"""

    @classmethod
    def setUpClass(cls):
        entries = []
        try:
            entries = load_entries("new")
        except Exception as e:      # 夹具缺失必须响，不能假绿
            cls.entries = []
            cls.setup_error = repr(e)
        else:
            cls.entries = [e for e in entries if os.path.exists(e["path"])]
            cls.setup_error = "" if cls.entries else "GT 里没有 verified 帧"

    def run_engine(self, img, platform, mode=None):
        """按生产口径喂一帧：平台与**玩法**都显式声明（否则读到 cwd 残留就不可复现）。

        玩法为什么和平台同级：分类器打分前有一道牌集闸门（`resolve_candidate_tiles`），
        玩法里没有的牌根本不进候选。实测（`localtest/_probe_panel_styles_blame.py`）
        广东雀神帧的 東/發 在川麻牌集（字牌只放开 7z）下被判成 1p(0.43)/1s(0.41)，
        换成装得下字牌的玩法就逐位回到 GT(1.00) —— 不声明玩法的面板数字不是识别水平。
        """
        orig_lp, orig_lm = E.load_platform, E.load_mode
        E.load_platform = lambda *a, **k: platform
        if mode:
            E.load_mode = lambda *a, **k: mode
        try:
            eng = Engine()
            hand = eng.get_hand_detector()
            primary = eng.get_detector()
            order = []

            def wrap(det, tag, fn):
                def inner(image, *a, **k):
                    order.append(tag)
                    return fn(image, *a, **k)
                return inner

            hand.detect_all_rows = wrap(hand, "hand", hand.detect_all_rows)
            primary.detect_all_rows = wrap(primary, "primary", primary.detect_all_rows)
            with contextlib.redirect_stdout(io.StringIO()):
                res = eng.process(img)
            data = json.loads(res.result) if res is not None else {}
            return eng, order, canon_mpsz(data.get("hand", "")), str(data.get("status"))
        finally:
            E.load_platform, E.load_mode = orig_lp, orig_lm

    def test_fixtures_are_usable(self):
        self.assertTrue(self.entries,
                        f"新素材夹具不可用（{self.setup_error}）：本文件的端到端断言"
                        f"将全部空转，请按 docs/new_platform_onboarding.md 恢复夹具")

    def test_every_frame_declares_a_mode_that_can_hold_its_hand(self):
        """夹具口径守卫：每帧必须声明玩法，且该玩法的牌集要装得下 GT 里的牌。

        这条是被真实事故逼出来的：面板 84 / 网格 91 的 7 张差额，全部来自
        「评测没声明玩法 → 引擎按川麻牌集（字牌只放开 7z）认含字牌的雀神帧」。
        装不下时不是放宽闸门，而是**报红**：那说明平台玩法表与素材冲突，得改表。
        """
        for e in self.entries:
            with self.subTest(frame=os.path.basename(e["path"])[:14]):
                mode = e.get("mode")
                self.assertTrue(mode, f"{e['platform']} 帧没声明玩法，面板数字不可复现")
                self.assertEqual(
                    e.get("mode_missing"), [],
                    f"{e['platform']} 的 supported_modes 里没有一个玩法装得下这批牌："
                    f"缺 {e.get('mode_missing')}（平台玩法表与素材冲突，不是识别问题）")
                legal = {tiles34_index_to_mpsz(i) for i in available_set(mode)}
                self.assertTrue(set(e["hand"]) <= legal,
                                f"mode={mode} 的牌集装不下 GT 手牌")

    def test_hand_channel_is_the_grid_detector(self):
        for e in self.entries:
            with self.subTest(platform=e["platform"]):
                img = cv2.imread(e["path"])
                self.assertIsNotNone(img, e["path"])
                eng = Engine()
                eng.platform = e["platform"]
                self.assertEqual(eng.get_hand_detector().__class__.__name__,
                                 "TencentGridDetector",
                                 f"{e['platform']} 已挂 bank 却没走 NCC 通道")

    def test_rows_come_from_the_hand_channel(self):
        for e in self.entries:
            img = cv2.imread(e["path"])
            _eng, order, _panel, _st = self.run_engine(img, e["platform"], e.get("mode"))
            self.assertTrue(order, "process 没有调用任何 detect_all_rows")
            self.assertEqual(
                order[0], "hand",
                f"{os.path.basename(e['path'])[:20]}: 首个 rows 取自 {order[0]} 通道，"
                f"手牌又走回主检测器了")

    def test_panel_accuracy_follows_the_grid_channel(self):
        """合计命中必须站在网格一侧；掉回主检测器水平就是接线断了。

        两侧同玩法口径才可比：裸通道原先不推牌集（= 无玩法约束的全 34 类上限），
        而面板那侧的引擎必然带玩法闸门 —— 拿无闸门的上限当基准去比有闸门的输出，
        量出来的是**闸门**而不是通道差距（实测：雀神含字牌帧因此在闸门下少 7 张）。
        """
        tot = {"gt": 0, "grid": 0, "primary": 0, "panel": 0}
        diff_frames = 0
        for e in self.entries:
            img = cv2.imread(e["path"])
            platform = e["platform"]
            mode = e.get("mode")
            grid = TencentGridDetector()
            grid.set_platform_styles(platform)
            if mode:
                grid.set_mode_tiles(available_set(mode))
            grid_labels = sorted(_labels(grid.detect_hand_strip(img)))
            primary = Engine().get_detector()
            if hasattr(primary, "set_platform_styles"):
                primary.set_platform_styles(platform)
            prim_rows = primary.detect_all_rows(img, allow_rotation=False)
            primary_labels = sorted(_labels(_hand_row(prim_rows)))
            _eng, _order, panel, _st = self.run_engine(img, platform, mode)
            want = e["hand"]
            tot["gt"] += len(want)
            for key, labs in (("grid", grid_labels), ("primary", primary_labels),
                              ("panel", panel)):
                tot[key] += tile_accuracy(want, labs)[0]
            if sorted(grid_labels) != sorted(primary_labels):
                diff_frames += 1
            self.assertLessEqual(tot["panel"], tot["gt"])
        self.assertGreaterEqual(diff_frames, 1,
                                "两条通道给出一模一样的结果：这批夹具测不出通道差异")
        self.assertGreater(
            tot["panel"], tot["primary"],
            f"面板合计 {tot['panel']} 没超过主检测器 {tot['primary']}："
            f"手牌通道切换没有在面板上生效")
        self.assertGreaterEqual(
            tot["panel"], tot["grid"] - 2,
            f"面板 {tot['panel']} 明显低于网格通道 {tot['grid']}："
            f"切换后又被后处理吃掉了，需要按 layer_cost 重新归因")


class TestCachedRowsSourceGate(unittest.TestCase):
    """同一份 rows 只改来源标记，复用结果必须相反（配对断言）。"""

    def frame_and_engine(self):
        entries = [e for e in load_entries("new") if os.path.exists(e["path"])]
        self.assertTrue(entries, "缺新素材夹具，来源门没有被测到")
        e = entries[0]
        img = cv2.imread(e["path"])
        orig_lp = E.load_platform
        E.load_platform = lambda *a, **k: e["platform"]
        eng = Engine()
        hand = eng.get_hand_detector()
        rows = hand.detect_all_rows(img, allow_rotation=False)
        calls = []
        hand.detect_all_rows = lambda image, *a, **k: (calls.append(1),
                                                       rows)[1]
        # 关掉自动方向探测：否则快路径自己会重算 rows，盖掉要测的缓存
        eng.set_config("auto_orient", False)
        return img, eng, hand, rows, calls, orig_lp

    def test_matching_source_is_reused(self):
        img, eng, hand, rows, calls, orig_lp = self.frame_and_engine()
        try:
            eng._cache_rows(hand, rows)
            with contextlib.redirect_stdout(io.StringIO()):
                eng.process(img)
            self.assertEqual(calls, [], "来源匹配却仍重跑检测：同帧白付两遍检测的钱")
        finally:
            E.load_platform = orig_lp

    def test_foreign_source_is_not_reused(self):
        img, eng, hand, rows, calls, orig_lp = self.frame_and_engine()
        try:
            # 模拟 _probe_orientation 的产物：那是主检测器算的 rows，不能当网格结果上屏
            eng._cached_rows = rows
            eng._cached_rows_src = "_ProbeFromPrimaryDetector"
            with contextlib.redirect_stdout(io.StringIO()):
                eng.process(img)
            self.assertTrue(calls, "来源不是手牌通道却被复用了：换通道等于没换")
        finally:
            E.load_platform = orig_lp


if __name__ == "__main__":
    unittest.main(verbosity=2)
