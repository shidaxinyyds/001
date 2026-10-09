# -*- coding: utf-8 -*-
"""把一条已定位、**尚未修**的接线缺陷钉成棘轮：牌河/副露拿到的是没有分类接口的主检测器。

已证实的因果链（2026-10 真机帧 `localtest/shots_batch3/` 指尖四川）：
  设备上的主检测器是 YOLODetector，它没有 `classify_tile`；而
  `detect_river_discards` / `detect_player_melds` 自己做分区轮廓扫描，唯一需要
  检测器提供的就是分类。`_classify_tile_fast` 外层那句 `hasattr(detector,
  "classify_tile")` 于是把每一张都静默兑成 (None, 0.0)：不报错、不降级，只是
  「牌河永远是空的」。下游连锁全部在面板可见：记牌器空白、`tile_ledger` 没有弃牌
  证据、三家 `tenpai_prob` 全落在同一个先验 0.5（= 用户报的「三家都高危听牌、
  危险牌一模一样」）。实测数字见 `localtest/probe_river_gates.py`。

为什么本文件钉的是**错的现状**而不是期望行为：把提交参数换成可分类检测器之后，
账本输入从无到有，5 条既有断言（守恒硬门、牌墙余量警报、双策略替代方案）随之改变，
而那几处改变**尚未逐条弄清为什么**。在弄清之前把修复留下，只有两种收尾方式：留红灯，
或者去放宽断言——后者就是本仓库禁止的假绿灯。所以先回退修复、把缺陷钉在这里。

修好那天本文件会红，必须连同 `test_tile_ledger_e2e` / `test_realtime_blanks` 一起
重新验证后才允许改成期望断言。

运行：py -3.10 -X utf8 localtest/test_river_classifier_guard.py
"""
from __future__ import annotations

import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
from modes import available_set  # noqa: E402
from platforms import get_river_zones  # noqa: E402

FRAME = os.path.join(HERE, "shots_batch3", "zj_play_03.jpg")
ENGINE_PY = os.path.join(REPO, "android", "app", "src", "main", "python",
                         "engine", "engine.py")


def river_scores_with(det, img):
    """用给定检测器对牌河四区里的候选逐张打分，返回 [(分数, 标签)]。"""
    ih, iw = img.shape[:2]
    from engine.engine import _classify_tile_fast, _RIVER_ROT_PREF
    avail = available_set("sc_hz")
    out = []
    for z in get_river_zones("zj_sichuan"):
        zn, x1, y1, x2, y2 = (z[0], int(iw * z[1]), int(ih * z[2]),
                              int(iw * z[3]), int(ih * z[4]))
        crop = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        white = ((hsv[:, :, 1] < 65) & (hsv[:, :, 2] > 115)
                 & (crop[:, :, 0] > 65) & (crop[:, :, 1] > 65) & (crop[:, :, 2] > 65))
        for c in cv2.findContours(white.astype('uint8'), cv2.RETR_EXTERNAL,
                                  cv2.CHAIN_APPROX_SIMPLE)[0]:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw < 14 or bh < 14 or bw * bh < 220 or bw * bh > 6000:
                continue
            pref = _RIVER_ROT_PREF.get(zn, (None, cv2.ROTATE_90_CLOCKWISE,
                                            cv2.ROTATE_90_COUNTERCLOCKWISE))
            lbl, sc = _classify_tile_fast(det, crop[by:by + bh, bx:bx + bw], avail,
                                          pref, {})
            out.append((float(sc), lbl))
    return out


class TestRiverClassifierDefect(unittest.TestCase):
    """钉住现状：牌河拿到的是 YOLO，因此每张都是 0 分。"""

    @classmethod
    def setUpClass(cls):
        cls.img = cv2.imread(FRAME)
        if cls.img is None:
            raise AssertionError(f"夹具帧读不出来，守卫在测空气：{FRAME}")
        orig_lp, orig_lm = E.load_platform, E.load_mode
        E.load_platform = lambda *a, **k: "zj_sichuan"
        E.load_mode = lambda *a, **k: "sc_hz"
        try:
            eng = E.Engine()
            eng.get_detector()
            cls.main_det = eng._detector
            cls.scores = river_scores_with(cls.main_det, cls.img)
        finally:
            E.load_platform, E.load_mode = orig_lp, orig_lm

    def test_main_detector_cannot_classify_tiles(self):
        """前提：主检测器确实没有 classify_tile（有就说明缺陷已不存在，本文件该重写）。"""
        self.assertFalse(hasattr(self.main_det, "classify_tile"),
                         f"主检测器 {type(self.main_det).__name__} 现在有 classify_tile 了："
                         "本文件钉的缺陷可能已修，必须改成期望断言并同步验证账本类守卫")

    def test_river_candidates_currently_all_score_zero(self):
        """症状：候选框到了，但每张 0 分 → 牌河静默为空。"""
        self.assertTrue(self.scores, "四区里一个候选都没框到：本用例已空转，得换帧")
        nonzero = [s for s, _l in self.scores if s > 0.0]
        self.assertEqual(nonzero, [],
                         f"用主检测器竟打出了非零分（{sorted(nonzero, reverse=True)[:5]}）："
                         "说明分类不再依赖 classify_tile，本文件钉的因果链要重写")

    def test_submit_site_still_hands_the_main_detector(self):
        """接线：提交给后台牌河检测的仍是 `self._detector`。

        这条与上面两条同向：修好之后它必须变红，逼着改动者去复核
        `test_tile_ledger_e2e`（守恒硬门、牌墙警报）与 `test_realtime_blanks`
        （双策略替代方案）——那 5 条断言会因为牌河从无到有而改变。
        """
        with open(ENGINE_PY, encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("_run_bg_river_and_melds,")
        seg = src[i:i + 240]
        self.assertIn("self._detector", seg,
                      f"提交参数已不再传主检测器（{seg}）：本棘轮该翻向，"
                      "并重新验证账本/双策略那 5 条断言")


if __name__ == "__main__":
    unittest.main()
