# -*- coding: utf-8 -*-
"""手牌带定位门禁（贴边主块曾被整行丢弃）。

缺陷原型（本轮在 public/0 途游四川麻将 2712x1220 帧上实测定位）：13 张手牌在二值 mask 里
连成一个 x=63、宽 2355、高 247 的整块，高度门（>132px）通过，却被 `x >= 带宽*5%` 这条
“滤左下角头像”的限制整块判死 —— 表现为屏幕上明摆着 13 张牌，引擎报 0 张。

本文件锁四件事，缺一件都可能有朝一日悄悄退回原状：
1. 每张回归夹具都能检出手牌行（张数/均分/峰值过生产验收线）。
2. **同一内容只在屏幕上平移，结果不得改变**——贴边与否不该决定识别成败。这条是差分断言，
   不依赖任何快照，改几何常量时最容易暴露问题。
3. 窄而贴边的干扰物仍被滤掉（用同一枚真实牌做配对断言：贴边不放行、不贴边要放行）：
   证明修的是“宽块误杀”，不是把过滤器删了。
4. 空桌/非牌局画面仍返回 0 张：防伪门没被顺手拆掉。

正面用例故意只用真截图、不用合成图：空白牌面过不了分类分（top<0.5 会被防伪门整行
放弃），拿它断言“被接受”会测到另一道题。贴边止于何处由上面第 1 条的覆盖守卫保证。

运行：py -3.10 localtest/test_hand_strip_gate.py
"""
from __future__ import annotations

import collections
import json
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

import cv2  # noqa: E402

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

SHOT_DIR = os.path.join(HERE, "shots_tuyou")
MANIFEST = os.path.join(SHOT_DIR, "manifest.json")
TOP_FRAC = 0.68            # 与生产默认一致（set_hand_strip_top(None)）
MIN_TILES = 9              # 生产验收线：少于此数说明整行没被定位到
MIN_MEAN = 0.55
MIN_TOP = 0.68


def shots():
    return sorted(f for f in os.listdir(SHOT_DIR) if f.lower().endswith(".jpg"))


def band_of(img):
    ih = img.shape[0]
    return int(ih * TOP_FRAC), int(ih * 0.99)


def pad_view(img, dx):
    """整幅画面左边补黑边 dx 像素：内容零损失地平移。

    不用“只移手牌带”的做法：那会把带右端的真实牌挤出画面（实测 r26 从 14 张变 2 张），
    测到的是“内容被剪掉”而不是“贴边与否”。整幅补边后唯一变的就剩屏幕横坐标。"""
    h, w = img.shape[:2]
    out = np.zeros((h, w + dx) + img.shape[2:], dtype=img.dtype)
    out[:, dx:] = img
    return out


def stats(det, img):
    dets = det.detect_hand_strip(img) or []
    scores = [float(s) for _r, _l, s in dets]
    return (len(dets),
            sum(scores) / len(scores) if scores else 0.0,
            max(scores) if scores else 0.0,
            sorted(l for _r, l, _s in dets),
            [int(r[0]) for r, _l, _s in dets])


def blank_canvas(h=1220, w=2712):
    return np.zeros((h, w, 3), dtype=np.uint8)


class TestHandStripEdgeGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.det = TencentGridDetector()
        cls.files = shots()

    def test_fixtures_are_present_and_traceable(self):
        """夹具腐坏守卫：图与 manifest 必须一一对应，否则本文件的覆盖率是假的。"""
        self.assertGreaterEqual(len(self.files), 9,
                                f"回归夹具缩水到 {len(self.files)} 帧，门禁形同虚设")
        with open(MANIFEST, encoding="utf-8") as fp:
            man = json.load(fp)
        names = {m["file"] for m in man["shots"]}
        self.assertEqual(names, set(self.files),
                         f"manifest 与目录不一致：{names ^ set(self.files)}")
        for m in man["shots"]:
            self.assertTrue(m.get("source"), "夹具必须能追溯到原始截图文件名")

    def test_every_fixture_localizes_the_hand_row(self):
        edge_cases = 0
        for f in self.files:
            img = cv2.imread(os.path.join(SHOT_DIR, f))
            self.assertIsNotNone(img, f"{f} 读不出来")
            n, mean, top, _labels, xs = stats(self.det, img)
            self.assertGreaterEqual(
                n, MIN_TILES,
                f"{f}: 手牌带只检出 {n} 张（应>= {MIN_TILES}），均分{mean:.2f} 峰值{top:.2f}"
                f" —— 整行拒识的老毛病回来了")
            self.assertGreaterEqual(mean, MIN_MEAN, f"{f}: 行均分 {mean:.2f} 不达标")
            self.assertGreaterEqual(top, MIN_TOP, f"{f}: 行峰值 {top:.2f} 不达标")
            if xs and min(xs) < int(img.shape[1] * 0.05):
                edge_cases += 1
        # 覆盖自检守卫：要是没有一帧真的贴到 5% 线以内，本文件就没在测这条门，
        # 而看起来仍是绿的（夹具换批次后最容易静默失去覆盖）。
        self.assertGreaterEqual(
            edge_cases, 3,
            f"只有 {edge_cases} 帧的主块落在贴边区，贴边门没被真正测到")

    def test_hand_row_answer_is_invariant_to_screen_offset(self):
        """同一帧整体右移（无损补边），张数与牌面多重集都不许变。"""
        for f in self.files:
            img = cv2.imread(os.path.join(SHOT_DIR, f))
            n0, _m0, _t0, lb0, xs0 = stats(self.det, img)
            for dx in (140, 400):
                n1, _m1, _t1, lb1, xs1 = stats(self.det, pad_view(img, dx))
                self.assertEqual(
                    (n0, lb0), (n1, lb1),
                    f"{f}: 贴边(x起点{min(xs0) if xs0 else '-'}) 与右移{dx}px"
                    f"(x起点{min(xs1) if xs1 else '-'}) 结果不同 "
                    f"-> {n0}张{lb0[:3]} vs {n1}张{lb1[:3]}；识别不该由画面横坐标决定")

    def test_narrow_left_edge_object_is_still_filtered(self):
        """贴边过滤本身不能因为修宽块误杀而被删掉。

        用同一枚**真实牌**做配对断言：空白块会先被分类分拦下，分不清是哪道门拦的，
        那样的“防伪用例”恒绿、没有守卫力。实测同一枚 7z（0.97 分）：
        贴边放 x=10 必须被滤掉，放 x=400/1300 必须能识出——差异只能来自横坐标。"""
        img = cv2.imread(os.path.join(SHOT_DIR, self.files[0]))
        dets = self.det.detect_hand_strip(img) or []
        self.assertTrue(dets, "拿不到真实牌，本用例失去意义")
        x, y, w, h = min((int(r[0]), int(r[1]), int(r[2]), int(r[3]))
                         for r, _l, _s in dets)
        tile = img[y:y + h, x:x + w]
        self.assertLess(w, img.shape[1] * 0.30, "夹具牌宽已算宽块，测不到窄块分支")
        y0 = band_of(img)[0]

        edge = blank_canvas(img.shape[0], img.shape[1])
        edge[y0:y0 + h, 10:10 + w] = tile
        n_edge, _m, _t, lb, _xs = stats(self.det, edge)
        self.assertEqual(n_edge, 0, f"窄块贴边却被放行（{lb}），防伪门被拆了")

        for px in (400, 1300):
            ref = blank_canvas(img.shape[0], img.shape[1])
            ref[y0:y0 + h, px:px + w] = tile
            n_ref, _m2, _t2, lb2, _xs2 = stats(self.det, ref)
            self.assertEqual(n_ref, 1, f"同一枚牌放在 x={px} 却没识出（{lb2}）："
                                       f"用例本身失效，贴边断言会跟着变假绿")

    def test_empty_table_still_yields_no_hand(self):
        img = blank_canvas()
        n, _mean, _top, _lb, _xs = stats(self.det, img)
        self.assertEqual(n, 0, "空桌不该检出手牌")


class TestProbeNumbersAreReproducible(unittest.TestCase):
    """夹具与生产入口的耦合：任何一帧检不到牌，本目录的门禁就该红，而不是静默少跑。"""

    def test_detector_is_available(self):
        det = TencentGridDetector()
        self.assertTrue(det.is_available, "模板 bank 没加载成功，所有断言都会假绿")
        self.assertGreaterEqual(len(det._cores), 200,
                                f"模板条目只有 {len(det._cores)}，bank 可能没挂全")

    def test_labels_come_from_the_shared_bank_not_from_hardcoding(self):
        """夹具帧的标签必须落在合法牌集里（防止把某帧的期望值硬编进测试）。"""
        det = TencentGridDetector()
        legal = {c[0] for c in det._cores} | {"pass"}
        seen = collections.Counter()
        for f in shots()[:4]:
            img = cv2.imread(os.path.join(SHOT_DIR, f))
            _n, _m, _t, lb, _xs = stats(det, img)
            seen.update(lb)
        bad = {l for l in seen if l not in legal}
        self.assertFalse(bad, f"检出标签不在模板牌集内：{bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
