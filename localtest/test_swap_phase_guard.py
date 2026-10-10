# -*- coding: utf-8 -*-
"""换三张 / 选牌 两条阶段判据的全素材真值表（C16 的现状台账）。

先说这份文件为什么存在：我此前对 C16 下过两次错结论——
  × 「缺换三张正样本」：JJ / 途游 / 腾讯 / 蜀山 的换三张帧其实都在仓库里；
  × 「判据两个检测器都不会」：`jj_swap_01` 上两个都返回 True。
两次都因为**只看了三帧**。所以这里把全部正/负样本一次摊平，并钉成双向棘轮：
修好一帧就从台账里删一行；判据变形（错到别处去）也立刻红。

全素材实测矩阵（`localtest/diag_phase_samples.py` 跑出来的，逐帧对过真值）：

    命中 swap:   jj_swap_01(batch3 + multi)  —— 2 帧
    漏判 swap:   tencent_swap_01, tuyou_swap_01, shushan_swap_01,
                 shushan_swap_02, zj_swap_03  —— 5 帧
    误报成 swap: tencent_pick_01, tuyou_pick_01,
                 shots_tuyou_select/s1_select_cd17  —— 3 帧（其实是「选牌/确定」弹窗）

结论：现有 `is_swap_phase` 抓的是**金色"确定/选牌"按钮**那一类控件，而不是换三张
画面本身。所以它既漏掉真正的换三张（多数平台的提示不是那种按钮），又把选牌弹窗
误报成换三张。这不是阈值问题，是**指向错了对象**。

本文件因此同时钉两件事：漏判清单（修一条删一条）与**误报清单**（防止有人为了
「让换三张命中」把选牌弹窗越推越宽——那是按下葫芦浮起瓢）。

运行：py -3.10 -X utf8 localtest/test_swap_phase_guard.py
"""
from __future__ import annotations

import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402
from platforms import set_platform_explicit  # noqa: E402

# 真值=换三张、但现有判据**仍然漏**的帧。修好一个就删一行；删完本台账就该翻向，
# 断言变成「所有 swap 帧必须命中」。
KNOWN_SWAP_MISSES = {
    "shots_multi/tencent_swap_01.jpg",
    "shots_multi/tuyou_swap_01.jpg",
    "shots_report/shushan_swap_01.jpg",
    "shots_report/shushan_swap_02.jpg",
    "shots_batch3/zj_swap_03.jpg",
}

# 真值=选牌弹窗/局中动作、现有判据却误报成换三张的帧。**任何时候都必须保持不误报**：
# 为了补齐上面的漏判而把这里放宽，是典型的按下葫芦浮起瓢。
#
# 2026-10 真值纠正（重要）：这个集合原来填的是
#   tencent_pick_01/02/03、tuyou_pick_01、tuyou_select/s1_select_cd17
# 理由是它们被 `is_swap_phase` 判成换三张。直接看图发现**它们就是换三张画面**：
# 中央写「排位 · 红中血战 · 换三张」，提示「选择3张同花色手牌 (10s)」，右下金色
# 圆钮上写「换牌」；游戏自己显示的「选牌中」是换三张的选牌子状态。因此那些
# True 是**判对了**，把它们当误报会让守卫把正确行为当 bug 去“修”。
# 误报清单因此换成真・牌局中帧（它们必须不被判成换三张）。
KNOWN_SWAP_FALSE_POSITIVES = {
    "shots_multi/queshen_play_01.jpg": "gd_queshen",
    "shots_batch3/jj_play_03.jpg": "jj",
    "shots_batch3/zj_play_03.jpg": "zj_sichuan",
    "shots_report/zj_play_02.jpg": "zj_sichuan",
}

# 已经命中的换三张帧：判据若退化到连它们都不认，必须立刻红。
MUST_HIT_SWAP = {
    "shots_batch3/jj_swap_01.jpg": "jj",
    "shots_multi/jj_swap_01.jpg": "jj",
}

# 全量真值表里所有「换三张」帧（含已命中的），用于逐帧对答案。
ALL_SWAP = MUST_HIT_SWAP | {p: "" for p in KNOWN_SWAP_MISSES}


def detect(rel: str, platform: str = "generic"):
    """两个检测器各跑一次 `is_swap_phase`，返回 (yolo, grid)。"""
    p = os.path.join(HERE, rel)
    img = cv2.imread(p)
    if img is None:
        raise AssertionError(f"夹具帧读不出来，守卫在测空气：{rel}")
    set_platform_explicit(platform) if platform else None
    yolo, grid = YOLODetector(), TencentGridDetector()
    try:
        ys = bool(yolo.is_swap_phase(img))
    except Exception:
        ys = False
    try:
        gs = bool(grid.is_swap_phase(img))
    except Exception:
        gs = False
    return ys, gs


class TestSwapPhaseLedger(unittest.TestCase):
    def test_already_hit_frames_stay_hit(self):
        """判据不许退化：现已命中的两帧必须继续命中。"""
        for rel, platform in MUST_HIT_SWAP.items():
            ys, gs = detect(rel, platform or "generic")
            self.assertTrue(ys or gs, f"{rel} 现在判不出换三张了（判据退化）")

    def test_misses_are_exactly_the_ledger(self):
        """漏判清单双向钉：多一个漏判=退步；少一个漏判=有人修好了却没删台账。"""
        misses = set()
        for rel, platform in ALL_SWAP.items():
            ys, gs = detect(rel, platform or "generic")
            if not (ys or gs):
                misses.add(rel)
        extra = misses - KNOWN_SWAP_MISSES
        fixed = KNOWN_SWAP_MISSES - misses
        self.assertFalse(extra, f"出现新的换三张漏判帧（C16 变差了）：{sorted(extra)}")
        self.assertFalse(fixed, f"这些帧已经能判出换三张，但台账没删（下次没人知道修好了）："
                                f"{sorted(fixed)}")

    def test_pick_popups_are_not_reported_as_swap(self):
        """牌局中帧不得被说成换三张——这条与漏判清单同等重要。

        真值已在 2026-10 重新核对（看图确认）：清单里现在是真正的牌局中帧。
        断言很严格：任何一帧被误报成换三张就直接红——因为误报会让引擎在局中
        清掉牌池、面板弹出“换三张”假阶段（用户报的那类“不在换牌却显示换牌”）。
        """
        fp = []
        for rel, platform in KNOWN_SWAP_FALSE_POSITIVES.items():
            ys, gs = detect(rel, platform)
            if ys or gs:
                fp.append(rel)
        self.assertFalse(fp, f"牌局中帧被误报成换三张（A4/D21 类现象的源头）：{fp}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
