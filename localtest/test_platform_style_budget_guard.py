# -*- coding: utf-8 -*-
"""风格核预算守卫：声明了平台，就不许再扫别家牌风；跨家救援也不许被顺手剥掉。

背景（用户反馈第 1 条「识别与响应、显示较慢」）：分类占单帧成本 88%，而每片牌面
要跑多少次 `matchTemplate` 完全由 `active_styles` 决定。实测（`localtest/core_census.py`，
川麻 sc_hz 候选集 28 型，核总数 594）：

    平台 tencent     active_styles=['tencent']                  每片 110 核
    平台 shushan     active_styles=['shushan','tencent']        每片 186 核
    平台 tuyou       active_styles=['shushan','tencent','tuyou'] 每片 253 核
    平台 weile / jj / zj_sichuan / gd_queshen                   每片 262~269 核

腾讯是 `platforms.DEFAULT_PLATFORM`、也是真机在用的那一家，它此前白挂着蜀山 76 枚
核。A/B（`localtest/ab_style_cores.py`，37 帧 verified GT，逐帧 fresh Engine）裁掉
蜀山后 **hand/status/dingque 逐帧差异 0**，单帧均值 654.6ms → 447.6ms（-31.6%）。
原始输出：build/ab_none.txt、build/ab_drop_shushan.txt。

为什么不干脆把蜀山收进它自己的平台（整条限平台）：那是真的会掉精度 ——
`test_eval_alignment.py` 的 zj#08 在 LOFO 剔掉本帧模板后，那一格七筒是被蜀山核抢
回来的；整条限平台让它变成 6p，`test_skip_probe_guard.py` 同时变红。所以 v1.7.2
的口径是**定点减法**（`STYLE_PLATFORM_DENYLIST`）：只踢「用户已声明腾讯却还扫蜀山」，
其它平台的跨家救援一律保持原样。这条分工就是本守卫要钉住的东西。

运行：
  py -3.10 -X utf8 localtest/test_platform_style_budget_guard.py
  py -3.10 -X utf8 localtest/test_platform_style_budget_guard.py --mutate
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PY_DIR = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

MUTATE = "--mutate" in sys.argv

import modes  # noqa: E402
from recognition import tencent_grid_detector as TGD  # noqa: E402
from recognition.tencent_grid_detector import resolve_candidate_tiles  # noqa: E402

BASE_STYLE = "tencent"      # 主 bank（templates_data）在 _build_cores 里固定的风格名
CANDIDATE_MODE = "sc_hz"    # 真机在用的玩法：候选集 28 型


def cores_for(platform_key: str, mode: str = CANDIDATE_MODE):
    """声明该平台后，一片牌面实际会去匹配多少枚核（与 _score_face 同一道过滤）。"""
    det = TGD.TencentGridDetector()
    det.set_platform_styles(platform_key)
    allowed = det.active_styles
    vt = resolve_candidate_tiles(modes.available_set(mode),
                                 getattr(det, "_mode_tiles", None), det.full_honors)
    hits = [(lbl, style) for lbl, style, *_r in det._cores
            if lbl in vt and (allowed is None or style in allowed)]
    return det, allowed, hits


class TestDeclaredPlatformBudget(unittest.TestCase):
    """A 组：声明 = 事实。说了是腾讯，就不该再拿别家牌风去认腾讯的牌。"""

    def test_tencent_does_not_scan_shushan_cores(self):
        _det, allowed, hits = cores_for("tencent")
        self.assertEqual({BASE_STYLE}, allowed,
                         f"腾讯白名单里又混进了别家牌风：{allowed}")
        self.assertEqual({s for _l, s in hits}, {BASE_STYLE},
                         "腾讯帧仍有用别家核匹配的分支（每片白付一次 matchTemplate）")

    def test_tencent_core_budget_is_the_measured_one(self):
        # 实测 110。上界给到 130 是为了让「同类多收一枚变体」不至于立刻红，
        # 但蜀山那种整块 76 枚的回归一定越过这条线。
        _det, _allowed, hits = cores_for("tencent")
        self.assertLessEqual(len(hits), 130,
                             f"腾讯每片核预算被撑到 {len(hits)}（基线 110，蜀山整块是 76）")

    def test_other_platforms_keep_the_cross_style_rescue(self):
        """定点减法不许溢出：其它家依然能看到蜀山（LOFO 救援池）。"""
        for pk in ("shushan", "zj_sichuan", "tuyou", "weile", "jj", "gd_queshen"):
            _det, allowed, _hits = cores_for(pk)
            self.assertIsNotNone(allowed, pk)
            self.assertIn("shushan", allowed,
                          f"{pk} 的跨家救援池被剥掉了（zj#08 的七筒就是靠它抢回来的）")

    def test_own_bank_always_present(self):
        """每家必须还是拿自己的牌风打底，减法不能把本家也减掉。"""
        for pk, style in (("shushan", "shushan"), ("zj_sichuan", "zj"),
                          ("tuyou", "tuyou"), ("weile", "weile"),
                          ("jj", "jj"), ("gd_queshen", "queshen")):
            _det, allowed, _hits = cores_for(pk)
            self.assertIn(style, allowed, f"{pk} 丢了本家 bank")

    def test_denylist_never_names_the_base_style(self):
        """结构底线：被踢的永远不可能是主 bank，否则 scores 会空。"""
        self.assertNotIn(BASE_STYLE, TGD.STYLE_PLATFORM_DENYLIST,
                         "主 bank 被写进了减法表：候选集会被掏空")


class TestNoEmptyCandidateSet(unittest.TestCase):
    """B 组：任何平台/玩法组合都不许把可用风格收窄成空集。"""

    def test_every_platform_keeps_a_non_empty_style_set(self):
        for pk in sorted(TGD.__dict__.get("STYLE_PLATFORM_WHITELIST", {})) + \
                ["tencent", "shushan", "zj_sichuan", "tuyou", "weile", "jj",
                 "gd_queshen", "unknown_platform"]:
            det = TGD.TencentGridDetector()
            det.set_platform_styles(pk)
            self.assertTrue(det.active_styles, f"{pk} 白名单为空")
            self.assertIn(BASE_STYLE, det.active_styles, f"{pk} 丢了主 bank")

    def test_base_style_survives_even_if_denylisted(self):
        """`style != "tencent"` 那道保底是承重的：把它拿掉就必须出事。"""
        det = TGD.TencentGridDetector()
        saved = dict(TGD.STYLE_PLATFORM_DENYLIST)
        try:
            TGD.STYLE_PLATFORM_DENYLIST[BASE_STYLE] = (BASE_STYLE,)
            det.set_platform_styles(BASE_STYLE)
            self.assertIn(BASE_STYLE, det.active_styles,
                          "保底失效：主 bank 被减法表掏空，classify_tile 会退化成"
                          "「任意标签 0 分」")
        finally:
            TGD.STYLE_PLATFORM_DENYLIST.clear()
            TGD.STYLE_PLATFORM_DENYLIST.update(saved)


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestDeclaredPlatformBudget, TestNoEmptyCandidateSet):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


class TestMutationControls(unittest.TestCase):
    """--mutate：把两种坏写法塞回减法表，断言本守卫确实会红。"""

    def setUp(self) -> None:
        if not MUTATE:
            self.skipTest("加 --mutate 才跑变异对照")

    def _fresh(self):
        det = TGD.TencentGridDetector()
        return det

    def test_whole_style_restriction_is_caught(self):
        """坏写法一：把蜀山整条限平台（本守卫实测会掉 zj 的救援）。"""
        saved = dict(TGD.STYLE_PLATFORM_WHITELIST)
        saved_d = dict(TGD.STYLE_PLATFORM_DENYLIST)
        try:
            TGD.STYLE_PLATFORM_WHITELIST["shushan"] = ("shushan",)
            TGD.STYLE_PLATFORM_DENYLIST.pop("shushan", None)
            det = self._fresh()
            det.set_platform_styles("zj_sichuan")
            broke = "shushan" not in det.active_styles
            self.assertTrue(broke, "变异无效：整条限平台没有改变 zj 的救援池，"
                                   "说明这条断言测不到真问题")
            print("[mutate] 蜀山整条限平台：zj 的跨家救援被剥掉——已被拦下")
        finally:
            TGD.STYLE_PLATFORM_WHITELIST.clear()
            TGD.STYLE_PLATFORM_WHITELIST.update(saved)
            TGD.STYLE_PLATFORM_DENYLIST.clear()
            TGD.STYLE_PLATFORM_DENYLIST.update(saved_d)

    def test_no_denylist_at_all_is_caught(self):
        """坏写法二：减法表整条删掉（腾讯重新白挂 76 枚蜀山核）。"""
        saved = dict(TGD.STYLE_PLATFORM_DENYLIST)
        try:
            TGD.STYLE_PLATFORM_DENYLIST.clear()
            det = self._fresh()
            det.set_platform_styles("tencent")
            broke = "shushan" in det.active_styles
            self.assertTrue(broke, "变异无效：删掉减法表没有让腾讯重新扫蜀山，"
                                   "说明减法根本没接线")
            print("[mutate] 减法表清空：腾讯每片重新白挂 76 枚蜀山核——已被拦下")
        finally:
            TGD.STYLE_PLATFORM_DENYLIST.clear()
            TGD.STYLE_PLATFORM_DENYLIST.update(saved)


if __name__ == "__main__":
    sys.exit(main())
