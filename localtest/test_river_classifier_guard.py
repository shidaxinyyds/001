# -*- coding: utf-8 -*-
"""钉住一条接线：牌河/副露必须拿到「能分类的检测器」，且断链不许伪装成空牌河。

根因（实测，2026-10 真机帧 localtest/shots_batch3/ 指尖四川）：设备上的主检测器是
YOLODetector，它没有 classify_tile；而 detect_river_discards / detect_player_melds
自己做分区轮廓扫描，唯一需要检测器提供的就是分类。`_classify_tile_fast` 外层那句
`hasattr(detector, "classify_tile")` 于是把每一张都静默成 (None, 0.0)：不报错、
不降级，只是「牌河永远是空的」。下游连锁全部在面板可见：记牌器空白、tile_ledger
没有弃牌证据、三家 tenpai_prob 全落在同一个先验 0.5、top_held 全 0.42 —— 就是用户
报的「三家都高危听牌、危险牌一模一样」。

实测数字（localtest/probe_river_gates.py）：同一帧四区框到 8 个候选，用主检测器打分
全部 0.0；换成网格检测器立刻拿到 0.178~0.463，其中 3 个过了 0.42 采信线。

钉四件事：
① 提交给牌河的检测器有 classify_tile；
② 牌河候选真的在出分（有非零分、且有牌过采信线）——只断言类型不够，这条缺陷的
   伪装形态就是"调用成功、返回空表"；
③ 接线形状：提交处用本帧手牌通道检测器；既不许退回 `self._detector`，也不许在
   提交处懒构造新检测器（后者会顶掉评测/守卫自己的假检测器，实测连累账本/牌墙/
   双策略共 5 条断言）；
④ 「空牌河」与「分类链断了」必须在 diag 上分得开（river_error）。

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
    """用给定检测器对牌河四区里的候选逐张打分，返回 [(分数, 标签)]。

    走生产的 `_classify_tile_fast`（含风格探针与早停），不自己重写打分：打分窗、
    灰度分支、候选裁剪任一项不同，分数就不可比（实测普遍虚高 0.1~0.4）。
    """
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
        cnts, _ = cv2.findContours(white.astype('uint8'), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw < 14 or bh < 14 or bw * bh < 220 or bw * bh > 6000:
                continue
            pref = _RIVER_ROT_PREF.get(zn, (None, cv2.ROTATE_90_CLOCKWISE,
                                            cv2.ROTATE_90_COUNTERCLOCKWISE))
            lbl, sc = _classify_tile_fast(det, crop[by:by + bh, bx:bx + bw], avail,
                                          pref, {})
            out.append((float(sc), lbl))
    return out


class TestRiverClassifierWiring(unittest.TestCase):
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
            # 与生产提交处同一个对象：process() 里牌河拿到的就是 hand_detector，
            # 而它来自 get_hand_detector()。守卫不另起一条路，否则测的不是同一条链。
            cls.det = eng.get_hand_detector()
            cls.scores = river_scores_with(cls.det, cls.img)
            cls.main_det = eng.get_detector()
        finally:
            E.load_platform, E.load_mode = orig_lp, orig_lm

    def test_river_detector_can_classify_tiles(self):
        self.assertTrue(hasattr(self.det, "classify_tile"),
                        f"牌河拿到的是 {type(self.det).__name__}，它没有 classify_tile")

    def test_main_detector_alone_could_not_do_this(self):
        """前提仍然成立：主检测器没有分类接口。它没了就说明本文件的因果链要重写。"""
        self.assertFalse(hasattr(self.main_det, "classify_tile"),
                         f"主检测器 {type(self.main_det).__name__} 现在能分类了："
                         "牌河恒空的因果链已不成立，本文件需按新事实重写")

    def test_river_candidates_actually_get_scores(self):
        """② 候选必须真的在出分：全 0.0 就是那个「静默空表」的伪装形态。"""
        self.assertTrue(self.scores, "四区里一个候选都没框到：本用例已空转，得换帧")
        nonzero = sorted((s for s, _l in self.scores if s > 0.0), reverse=True)
        self.assertTrue(nonzero,
                        f"{len(self.scores)} 个候选全部 0.0 分——分类链是断的")
        accepted = [s for s, _l in self.scores if s >= E.RIVER_CONF["min_conf"]]
        self.assertTrue(accepted,
                        f"没有一张候选过牌河采信门槛 {E.RIVER_CONF['min_conf']}，"
                        f"最高分={nonzero[:6]}")

    def test_submit_site_uses_the_frame_hand_detector(self):
        """③ 接线：既不许退回 self._detector，也不许在提交处懒构造新检测器。"""
        with open(ENGINE_PY, encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("_run_bg_river_and_melds,")
        seg = src[max(0, i - 900):i + 200]
        # 先剔掉注释：上一版直接拿原文比，结果被自己写的「为什么不用
        # get_hand_detector()」那段说明触发假红。源码字面断言只能看代码。
        code = "\n".join(line.split("#")[0] for line in seg.splitlines())
        self.assertIn("river_det = hand_detector", code,
                      f"提交处不再用本帧手牌通道检测器：{code[-300:]}")
        self.assertNotIn("self._detector,", code,
                         "提交参数里又出现 self._detector（设备上是 YOLO，无分类接口）")
        self.assertNotIn("get_hand_detector()", code,
                         "提交处又去懒构造检测器：会顶掉评测/守卫自己的假检测器")

    def test_blind_detector_is_reported_not_silent(self):
        """④ 空牌河必须能区分「场上真没牌」与「分类链断了」。"""
        self.assertIn('"river_error"', open(ENGINE_PY, encoding="utf-8").read(),
                      "payload 不再交 river_error：断链会退化成看不出来有问题")
        src = open(ENGINE_PY, encoding="utf-8").read()
        i = src.index("bg_river_entries, bg_meld_entries = rf.result()")
        seg = src[i:i + 900]
        self.assertIn("_river_error", seg,
                      "认领牌河结果时不再判定「无分类接口」：断链又变成静默空表")


if __name__ == "__main__":
    unittest.main()
