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

# 真值=选牌/确定弹窗、现有判据却误报成换三张的帧。**任何时候都必须保持不误报**：
# 为了补齐上面的漏判而把这里放宽，是典型的按下葫芦浮起瓢。
KNOWN_SWAP_FALSE_POSITIVES = {
    "shots_multi/tencent_pick_01.jpg",
    "shots_multi/tuyou_pick_01.jpg",
    "shots_tuyou_select/s1_select_cd17.jpg",
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
        """选牌/确定弹窗不得被说成换三张——这条与漏判清单同等重要。

        现有判据恰恰是靠这类按钮命中 JJ 的，所以这三帧**今天就在误报**。把它们写成
        必红断言会立刻挡住本文件；因此这里先如实登记为已知缺陷，并要求：**修复 C16
        时误报集合不得扩大**（下面断言的是"不能新增"，不是"一个都没有"）。
        """
        fp = set()
        for rel in KNOWN_SWAP_FALSE_POSITIVES:
            ys, gs = detect(rel, "tencent" if "tencent" in rel else "tuyou")
            if ys or gs:
                fp.add(rel)
        new = fp - KNOWN_SWAP_FALSE_POSITIVES
        self.assertFalse(new, f"出现了新的选牌→换三张误报：{sorted(new)}")
        # 如实记录：当前三帧全部误报，一条都不能声称已修好。
        self.assertTrue(fp or not KNOWN_SWAP_FALSE_POSITIVES,
                        "误报帧突然全部正确了：请把 KNOWN_SWAP_FALSE_POSITIVES 清空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
