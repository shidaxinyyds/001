# -*- coding: utf-8 -*-
"""框级台账（GT 的 `missed` 字段 + 检测层 TP/FP/FN 公式）的守卫。

危险原型：逐位指标只比「检出的格」，漏框的牌既不进分子也不进分母；而 GT 常常还是
按检出框数对齐落盘的（harvest 硬门「标签数 == 框数」）。两处叠起来，「检测器少框」
这件事在 99% 的逐位精度里完全隐形 —— 实测 jj#02 物理 13 枚只框到 11，右侧两枚 6p
整枚消失，而逐位数字一分没掉。度量对漏检不敏感，就等于换个方向自拟合。

本文件锁五件事：
1. 表里的 `+label` 台账必须原样编译进 GT json，且**表里删掉时 json 里也得没有**
   （留着旧台账就是拿上一版人工结论冒充本版）；
2. 台账只能记 34 类里真实存在的牌，`?` 不允许（读不出就不配记账）；
3. `box_accounting` 的两条恒等式（TP+FP==检出框数、TP+FN==GT槽+台账数）：公式一旦
   被改成「把 FN 从分母里挪走」，数字会悄悄变好，这条会叫；
4. `grid_slot_outliers` 在真帧 weile#04 上必须把那枚行尾摸牌认成「局格外的框」而不是
   误检 —— 同时证明旧判据（末间隙 >= 1.8x 节距）在这帧上根本判不出（见 --mutate b）。
5. 混淆矩阵：对角总数必须等于逐位判对数（矩阵与总分离账），`<无>` 这类去向不许被
   抹平成「无混淆」，而且评测必须真的调用它 —— 探针见 ConfusionMatrix 与 --mutate c。

变异检验：py -3.10 -X utf8 localtest/test_det_ledger.py --mutate
"""
from __future__ import annotations

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

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_new_material import box_accounting, confusion_report, grid_slot_outliers  # noqa: E402
from gt_from_table import parse_table  # noqa: E402

GT = os.path.join(HERE, "gt", "shots_b1.json")
TABLE = os.path.join(HERE, "gt", "labels_b1.txt")
# 平台 key -> GT 里的风格短名（表用前者、json 用后者，见 gt_from_table 顶部）
from eval_new_material import STYLE_OF  # noqa: E402
STYLE_OF_PLATFORM = {v: k for k, v in STYLE_OF.items()}

TILE_LABELS = {f"{n}{s}" for s in ("m", "s", "p") for n in "123456789"} | \
              {f"{n}z" for n in "1234567"}


def _gt():
    with open(GT, encoding="utf-8") as fp:
        return json.load(fp)["shots"]


def _shot(style, frame):
    return next(e for e in _gt() if e["style"] == style and e["frame"] == frame)


class LedgerRoundTrip(unittest.TestCase):
    def test_table_ledger_survives_compile(self):
        """表里每一行 `+label` 都必须等于 json 里的 missed（不多不少、按序）。"""
        declared = {(STYLE_OF_PLATFORM.get(p), i): m
                    for p, i, _h, m, _ln in parse_table(TABLE)
                    if STYLE_OF_PLATFORM.get(p)}
        bad = []
        for e in _gt():
            key = (e["style"], e["frame"])
            if key not in declared:
                continue                      # 不由本表管理的条目（旧批次）不比
            if list(e.get("missed", [])) != list(declared[key]):
                bad.append((key, e.get("missed"), declared[key]))
        self.assertEqual([], bad,
                         "漏框台账与文本表不一致：改完表必须重编译 gt_from_table.py，"
                         "而 json 里残留的旧台账必须被抹掉（否则拿上一版结论扣本版 recall）")

    def test_ledger_labels_are_readable_tiles(self):
        for e in _gt():
            for lab in e.get("missed", []):
                self.assertIn(lab, TILE_LABELS, f"{e['style']}#{e['frame']} 台账 {lab} 不是 34 类")

    def test_known_black_hole_is_on_the_books(self):
        """jj#02 的两枚 6p 是「人眼看得到、检测器看不见」的那类牌：必须常驻台账。

        这条不是形式检查：如果哪天有人觉得「反正逐位 100%」而把台账删了，检测层的
        洞就会重新隐形，而且下一次重收割会把这帧当成 11 枚的标准帧。
        """
        self.assertEqual(["6p", "6p"], _shot("jj", 2).get("missed", []))


class BoxAccounting(unittest.TestCase):
    def test_identities_hold(self):
        for slots, boxes, missed in [(13, 13, 0), (13, 14, 0), (11, 11, 2), (10, 6, 0),
                                     (0, 0, 0), (14, 0, 0), (0, 3, 1)]:
            tp, fp, fs, fn = box_accounting(slots, boxes, missed)
            self.assertTrue(tp >= 0 and fp >= 0 and fs >= 0 and fn >= 0,
                            (slots, boxes, missed))
            self.assertEqual(boxes, tp + fp, (slots, boxes, missed))
            # TP + FN 必须等于「人工确认应检」的张数：把 FN 从分母里挪走就过不了这条
            self.assertEqual(slots + missed, tp + fn, (slots, boxes, missed))
            self.assertEqual(fs + missed, fn, (slots, boxes, missed))
            self.assertEqual(tp, min(slots, boxes))

    def test_missed_only_moves_fn(self):
        """加台账只该让 FN 变大，绝不该动 TP/FP（否则记一次账就刷了一次分）。"""
        a = box_accounting(11, 11, 0)
        b = box_accounting(11, 11, 2)
        self.assertEqual((a[0], a[1], a[2]), (b[0], b[1], b[2]))
        self.assertEqual(2, b[3] - a[3])


class GridOutliers(unittest.TestCase):
    def test_dup_box_is_not_a_tail_draw(self):
        dets = [((100, 0, 80, 120), "1m", 1.0), ((182, 0, 80, 120), "2m", 1.0),
                ((182 + 30, 0, 80, 120), "3m", 1.0)]          # 第二枚被劈成两框
        self.assertEqual((0, 1), grid_slot_outliers(dets, (100.0, 82.0, 2)))

    def test_no_grid_is_reported_not_guessed(self):
        dets = [((100, 0, 80, 120), "1m", 1.0)]
        self.assertIsNone(grid_slot_outliers(dets, None))
        self.assertIsNone(grid_slot_outliers(dets, (100.0, 0.0, 13)))

    def test_weile04_extra_box_is_the_tail_draw(self):
        """真帧 weile#04：检出 14 框、局格自报 13 槽，多出的那框必须是「局格外的摸牌」。

        同时钉住它的身份：GT 注释写的是行尾摸进一张發（6z），若检测器哪天把它挪进
        局格、或牌型变了，这条会要求重新看图而不是继续把 FP 解释成摸牌位。
        """
        e = _shot("weile", 4)
        img = cv2.imread(os.path.join(REPO, e.get("src") or "public/0", e["file"]))
        self.assertIsNotNone(img, "素材不在位，无法复核这条归因")
        det = TencentGridDetector()
        det.set_platform_styles(STYLE_OF["weile"])
        dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
        grid = getattr(det, "last_hand_grid", None)
        self.assertIsNotNone(grid, "检测器没报局格，摸牌位无从判定")
        self.assertEqual(len(e["hand"]), int(grid[2]),
                         "局格槽数与 GT 标签数不等：`+` 台账/重编译没跟上")
        self.assertEqual(len(dets), len(e["hand"]) + 1, "本帧的多框事实变了，需重新归因")
        out, dup = grid_slot_outliers(dets, grid)
        self.assertEqual((1, 0), (out, dup),
                         "多出的那框没被认成行尾摸牌位：它要么是真误检，要么判据失效")
        x0, pitch, n = grid
        last = dets[-1][0][0]
        self.assertGreater(round((last - x0) / pitch), n - 1)
        self.assertEqual("6z", dets[-1][1], "行尾摸牌不再是發：GT 注释与归因要一起改")


class ConfusionMatrix(unittest.TestCase):
    """混淆矩阵的两个职责分开：报出去的形状，以及它真的被接进了评测。"""

    def test_diagonal_equals_correct_cells(self):
        conf = {("tencent", "3z", "3z"): 6, ("tencent", "3z", "2z"): 1,
                ("tencent", "5z", "5z"): 4,
                ("zj", "1p", "1p"): 10, ("zj", "1p", "<无>"): 2}
        lines, diag = confusion_report(conf, ["tencent", "zj"])
        self.assertEqual(20, diag, "对角线只该数「GT==det」的格")
        txt = "\n".join(lines)
        self.assertIn("3z 6/7 -> 2zx1", txt)
        self.assertIn("1p 10/12 -> <无>x2", txt,
                      "没框到被抹平成「无混淆」就等于把检测层的洞从矩阵里藏掉")
        self.assertNotIn("5z", txt, "全对的类不该出现在混淆行里（否则矩阵没人看得完）")

    def test_empty_style_is_not_a_crash(self):
        lines, diag = confusion_report({}, ["jj"])
        self.assertEqual(0, diag)
        self.assertIn("判对 0 张", lines[0])

    def test_eval_actually_wires_the_ledger_and_matrix(self):
        """接线守卫：口径函数必须被评测调用，而不是躺在模块里当摆设。

        这三条都是本项目被咬过的同族缺陷（编译了但没接线、字段写了但没人读）。
        拿源码断言而不是重跑一遍评测：重跑要几分钟且会随素材波动，接线这件事
        本身只要求「调用点存在且参数对得上」。
        """
        src = io.open(os.path.join(HERE, "eval_new_material.py"), encoding="utf-8").read()
        for probe in WIRING_PROBES:
            self.assertIn(probe, src, f"评测没接上 `{probe}`：新增口径是死代码")


WIRING_PROBES = (
    'conf_st[(e["style"], x, y)] += 1',
    "confusion_report(conf_st, per_style_pos)",
    "conf_diag != hit_pos",
    'box_accounting(len(gt_all), len(got), len(missed))',
)
EVAL_SRC = os.path.join(HERE, "eval_new_material.py")


def _eval_src():
    with io.open(EVAL_SRC, encoding="utf-8") as fp:
        return fp.read()


def run_wiring_test():
    """只跑接线那一条测试，返回它过不过（变异检验要的是布尔，不是整份报告）。"""
    # 直接按方法名实例化 TestCase：loadTestsFromName 的 name/module 组合在这个
    # 脚本自己就是 __main__ 的场合会解析成 _FailedTest，测试根本没跑却像“通过了”。
    suite = unittest.TestSuite([ConfusionMatrix(
        "test_eval_actually_wires_the_ledger_and_matrix")])
    return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite).wasSuccessful()


def mutate_ledger_visibility():
    """把台账从分母里抹掉，数字必须变好——否则说明评测根本没在算它。"""
    e = _shot("jj", 2)
    slots, boxes = len(e["hand"]), len(e["hand"])          # 检出面：框数 == GT 槽
    with_ledger = box_accounting(slots, boxes, len(e.get("missed", [])))
    without = box_accounting(slots, boxes, 0)
    ok_with = 100.0 * with_ledger[0] / (with_ledger[0] + with_ledger[3])
    ok_without = 100.0 * without[0] / (without[0] + without[3])
    print(f"  [a] jj#02 记台账 recall={ok_with:.1f}% / 抹掉台账 recall={ok_without:.1f}%")
    hit = ok_without > ok_with
    # 旧判据（末间隙 >= 1.8x 中位节距）在同一真帧上判不出摸牌位：坐实换判据的理由
    e2 = _shot("weile", 4)
    img = cv2.imread(os.path.join(REPO, e2.get("src") or "public/0", e2["file"]))
    det = TencentGridDetector()
    det.set_platform_styles(STYLE_OF["weile"])
    dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
    xs = [r[0] for r, _l, _s in dets]
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    body = sorted(gaps[:-1])
    med = body[len(body) // 2]
    old = med > 0 and gaps[-1] >= 1.8 * med
    print(f"  [b] weile#04 末间隙 {gaps[-1]} vs 中位节距 {med}（{gaps[-1] / med:.2f}x）"
          f"：旧判据={old} 局格判据=(1, 0)")
    run_wiring_ok_before = run_wiring_test()
    # [c] 接线守卫的变异检验：把评测里的调用点断开，那条测试必须变红
    #（探针型守卫最怕「探针自己也是死的」，所以这里真改磁盘再改回来，而不是重跑一遍字符串匹配）。
    orig = _eval_src()
    hit_c = hit_restore = False
    try:
        broken = orig.replace(WIRING_PROBES[1], "confusion_report_UNUSED(conf_st)", 1)
        with io.open(EVAL_SRC, "w", encoding="utf-8") as fp:
            fp.write(broken)
        hit_c = not run_wiring_test() and run_wiring_ok_before
    finally:
        with io.open(EVAL_SRC, "w", encoding="utf-8") as fp:
            fp.write(orig)
        hit_restore = _eval_src() == orig and run_wiring_test()
    print(f"  [c] 断开 confusion_report 调用点 -> 接线守卫变红={hit_c}；"
          f"恢复后重新变绿={hit_restore}")
    return hit and not old and hit_c and hit_restore


def main():
    if "--mutate" in sys.argv:
        ok = mutate_ledger_visibility()
        print("[mutate] " + ("三条都被拦住：台账真的在扣分、旧判据真的判不出、"
                             "接线守卫真的会响"
                             if ok else "!! 变异没被拦住，上面的数字不可信"))
        return 0 if ok else 1
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
