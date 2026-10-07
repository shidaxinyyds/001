# -*- coding: utf-8 -*-
"""逐位对齐口径守卫：det 行必须按**物理槽位**贴到 GT 行上。

缺陷原型（2026-10 看图坐实）：`detect_hand_strip` 把牌带等宽切成 k 格，而置信救援
会把低分张整框丢掉，于是「检出的第 j 框」不等于「第 j 格」。zj#08 漏 4 框后，第 10 格
的七筒被按序号当成第 6 格的一筒，评测报出一条根本不存在的「真失误」（裁片图见
build/audit_zj_08_lofo.png：那一框里明摆着是七筒）。这类假案的危险方向不是保守 ——
它会把人引去攻一个不存在的分类问题，而真正的漏框债被记在别的桶里。

本文件锁三件事：
1. `align_by_slots` 在漏框时把每框放回它自己的槽位，漏掉的槽标 `<无>`；
2. 局格拿不到或不自洽（重复槽、越界、节距非正）时必须**退回序号对齐**而不是猜一个
   对齐 —— 猜出来的对齐会直接改分数；
3. 真实帧 zj#08 上这条不变量成立（槽位数 = GT 长度、每框各归其位、七筒落在第 10 槽）。

变异检验：py -3.10 -X utf8 localtest/test_eval_alignment.py --mutate
它在同一个真帧上并列算两种口径（序号对齐 vs 槽位对齐），断言序号对齐会多判出
失误 —— 即这条缺陷真的存在、而槽位对齐真的把它拆掉了。

运行：py -3.10 -X utf8 localtest/test_eval_alignment.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_new_material import (STYLE_OF, align_by_slots, load_provenance,  # noqa: E402
                               lofo_keep_indices)

GT = os.path.join(HERE, "gt", "shots_b1.json")
CASE = ("zj", 8)          # 坐实这条缺陷的帧：GT10 槽、只检出 6 框


def _dets(pairs):
    """[(label, x)] -> detect_hand_strip 形状的 [(rect, label, score)]（按 x 升序）。"""
    return [((x, 547, 82, 110), lbl, 0.6) for lbl, x in pairs]


class SlotAlign(unittest.TestCase):
    def test_holes_go_back_to_their_own_slots(self):
        # 局格 10 槽、节距 82、左边界 339；只检出第 1~5 与第 10 格
        dets = _dets([("7z", 339), ("9s", 421), ("9s", 503), ("9s", 585),
                      ("1p", 667), ("7p", 1078)])
        labs, scs, note = align_by_slots(dets, (339.0, 82.0, 10), 10)
        self.assertEqual(labs, ["7z", "9s", "9s", "9s", "1p", "<无>", "<无>",
                                "<无>", "<无>", "7p"], note)
        self.assertEqual(scs[9], 0.6)
        self.assertEqual(scs[5], 0.0)
        self.assertIn("漏槽 [6, 7, 8, 9]", note)

    def test_full_lattice_matches_index_order(self):
        dets = _dets([("1m", 100), ("2m", 182), ("3m", 264)])
        labs, _sc, _n = align_by_slots(dets, (100.0, 82.0, 3), 3)
        self.assertEqual(labs, ["1m", "2m", "3m"])

    def test_float_tile_lands_after_the_lattice(self):
        # 摸牌框在主格右侧一格之外：按 x 顺序接在尾部，而不是越界导致整帧退回
        dets = _dets([("1m", 100), ("2m", 182), ("3p", 345)])
        labs, _sc, _n = align_by_slots(dets, (100.0, 82.0, 2), 3)
        self.assertEqual(labs, ["1m", "2m", "3p"])

    def test_refuses_to_guess_an_alignment(self):
        dets = _dets([("1m", 100), ("2m", 182)])
        self.assertIsNone(align_by_slots(dets, None, 2), "没有局格时不许猜对齐")
        self.assertIsNone(align_by_slots(dets, (100.0, 0.0, 2), 2), "节距非正")
        self.assertIsNone(align_by_slots(dets, (100.0, 82.0, 2), 1), "槽位越界")
        # 两框落进同一槽 = 切分/重叠，位置信息已不可信
        dup = _dets([("1m", 100), ("2m", 140)])
        self.assertIsNone(align_by_slots(dup, (100.0, 82.0, 2), 2), "重复槽位")


class RealFrame(unittest.TestCase):
    """第 3 件事：真帧上这条不变量必须成立（不然单元测试只是在演）。"""

    @classmethod
    def setUpClass(cls):
        cls.det = TencentGridDetector()
        cls.img, cls.gt, cls.n_drop = load_case(cls.det, *CASE)
        cls.dets = sorted(cls.det.detect_hand_strip(cls.img) or [], key=lambda d: d[0][0])

    def test_slot_alignment_recovers_the_shifted_tile(self):
        gt = self.gt
        self.assertGreater(self.n_drop, 0, f"{CASE} 没剔到本帧模板：LOFO 现场没复现")
        self.assertLess(len(self.dets), len(gt),
                        f"{CASE} 这一帧不再是漏框样本了：{len(self.dets)}/{len(gt)}，"
                        f"本测试的存在理由失效，要么换帧要么删掉它（别留着演）")
        aligned = align_by_slots(self.dets, self.det.last_hand_grid, len(gt))
        self.assertIsNotNone(aligned, f"{CASE} 拿不到自洽局格：槽位对齐在生产上没生效")
        labs, _sc, note = aligned
        self.assertEqual(labs.count("<无>"), len(gt) - len(self.dets), note)
        # 坐实的那一格：七筒必须在它自己的槽（GT 末位），而不是被当成第 6 格
        self.assertEqual(gt[-1], "7p", "GT 末位不再是七筒，这条断言要重新看图")
        self.assertEqual(labs[-1], "7p", note)
        index_row = [d[1] for d in self.dets] + ["<无>"] * (len(gt) - len(self.dets))
        self.assertNotEqual(labs, index_row,
                            "槽位对齐与序号对齐给出同一行：这条缺陷已不存在，测试该改")


def det_tracks(det):
    return det._cores, det._core_keys, det._core_harvested


def load_case(det, style, frame, lofo=True):
    """载帧图并（可选）把本帧收割模板剔掉。返回 (img, gt, 剔掉数)。

    必须跑在 LOFO 状态下：不剔本帧时这帧 10/10 一个漏框都没有（实测），
    而评测报出假案正是在剔完模板、置信救援把低分张整框丢掉之后。
    不在这里复现那个前提，探针就只能在一个人工造出来的现场上自证。
    """
    shots = json.load(open(GT, encoding="utf-8"))["shots"]
    e = next(x for x in shots if x["style"] == style and int(x["frame"]) == int(frame))
    img = cv2.imread(os.path.join(REPO, e["src"], e["file"]))
    if img is None:
        raise SystemExit(f"读不到帧图：{e['src']}/{e['file']}")
    det.set_platform_styles(STYLE_OF.get(style))
    n_drop = 0
    if lofo:
        drop = load_provenance().get(style, {}).get(int(frame), set())
        cores, keys, harv = det_tracks(det)
        keep = lofo_keep_indices(cores, keys, harv, style, drop)
        det._cores = [cores[i] for i in keep]
        det._core_keys = [keys[i] for i in keep]
        det._core_harvested = [harv[i] for i in keep]
        n_drop = len(drop)
    return img, e["hand"], n_drop


def mutate_alignment():
    """变异检验：把「按序号读」这一错误口径造出来，确认两种口径确实不同判。

    探针做的事就是漏框时评测的旧行为：把 det 行直接按序号前移补位。两个口径必须
    给出不同的失误数（否则说明槽位对齐根本没生效，或者这个帧已不再漏框）。
    """
    style, frame = CASE
    det = TencentGridDetector()
    img, gt, n_drop = load_case(det, style, frame)
    if not n_drop:
        print(f"[mutate] {style}#{frame} 无溯源可剔，LOFO 现场复现不了")
        return 1
    dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
    if len(dets) >= len(gt):
        print(f"[mutate] {style}#{frame} 当前不漏框（{len(dets)}/{len(gt)}），"
              f"换一帧再测：探针不能凭空造一个不存在的效果")
        return 1
    aligned = align_by_slots(dets, det.last_hand_grid, len(gt))
    if aligned is None:
        print("[mutate] 局格不可用，槽位对齐根本没生效")
        return 1
    labs, _sc, _note = aligned
    index_row = [d[1] for d in dets] + ["<无>"] * (len(gt) - len(dets))
    wrong_idx = sum(1 for a, b in zip(gt, index_row) if a != b)
    wrong_slot = sum(1 for a, b in zip(gt, labs) if a != b)
    ok = wrong_slot < wrong_idx
    print(f"[mutate] 同一帧：序号对齐判错 {wrong_idx} 格，槽位对齐判错 {wrong_slot} 格"
          f"（少报的正是漏框造成的假案，拦下：{ok}）")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--mutate" in sys.argv:
        sys.exit(mutate_alignment())
    unittest.main(verbosity=2)
