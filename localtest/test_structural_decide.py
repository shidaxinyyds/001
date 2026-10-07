# -*- coding: utf-8 -*-
"""结构裁决（`_decide` 里的物理判据分支）的常驻守卫，当前锁「4条 vs 5条中心索」。

为什么这类规则必须有守卫而不是只跑一次评测：
1. 它是**阈值规则**（中心窗暗像素占比 0.30）。阈值一旦落进两堆数据中间的空隙里，
   换一批素材、换一块屏幕就可能把某堆压过来，表现为零星错读而没人怀疑到这条规则。
   所以断言写成"所有真值 4s 的中心占比都 < 阈值、所有真值 5s 都 > 阈值"，
   而不是"总分没掉"。
2. 它的触发面很宽（凡是最高分为 4s/5s 都会走判据），一次改动很容易顺带翻别家格。
   验收方式是 A/B 消融：`py -3.10 -X utf8 build/_rule_ablate.py "# 7. 4条 vs 5条"
   build/ablate_s45.json` —— 实测 1157 格里只有 1 格（目标格）翻转。本文件的
   `--mutate` 再从反方向验一次：把判据反向后，测试必须变红。
3. 判据只能在**生产口径**下量：打分窗口 `face[10:110,6:74]`、`extract_face` 的去绿底
   外接框、以及声明平台后的候选集都会改变结果（拿 `tiles/` 裁片量普遍虚高 0.1~0.4），
   所以这里逐帧跑 `detect_hand_strip` 再按位序取框，与推理完全同一条码路。

运行：py -3.10 -X utf8 localtest/test_structural_decide.py
      py -3.10 -X utf8 localtest/test_structural_decide.py --mutate
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PKG = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PKG)
sys.path.insert(0, HERE)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_new_material import STYLE_OF  # noqa: E402

GT = os.path.join(HERE, "gt", "shots_b1.json")
SRC = os.path.join(PKG, "recognition", "tencent_grid_detector.py")

# 与生产 `_decide` 第 7 条一致：中心窗 face[cy-10:cy+10, cx-8:cx+8]，灰度 < 150
WIN = (slice(50, 70), slice(32, 48))
DARK_THR = 150
RULE_THR = 0.30                   # 裁决阈值（规则里的 `> 0.30`）
MARGIN = 0.20                     # 两堆与阈值之间要求的最小留白
MIN_SAMPLES = 15                  # 覆盖守卫：样本太少等于没测


def center_dark(face: np.ndarray) -> float:
    return float(np.mean(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)[WIN] < DARK_THR))


def scan(det, shots, styles_wanted=None):
    """在生产口径下收集所有真值为 4s/5s 的格子：(风格, 帧, 位序, 真值, 判出, 中心占比)。"""
    rows = []
    for e in shots:
        if not ({"4s", "5s"} & set(e["hand"])):
            continue
        if styles_wanted and e["style"] not in styles_wanted:
            continue
        src = os.path.join(REPO, e.get("src") or os.path.join("public", "0"))
        img = cv2.imread(os.path.join(src, e["file"]))
        if img is None:
            continue
        det.set_platform_styles(STYLE_OF.get(e["style"]))
        dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
        for i, (rect, lbl, _sc) in enumerate(dets):
            g = e["hand"][i] if i < len(e["hand"]) else "?"
            if g not in ("4s", "5s"):
                continue
            x, y, w, h = [int(v) for v in rect]
            crop = img[y:min(img.shape[0], y + h), x:min(img.shape[1], x + w)]
            rows.append((e["style"], int(e["frame"]), i + 1, g, lbl,
                         center_dark(det.extract_face(crop))))
    return rows


def mutated_source() -> str:
    """把第 7 条的判据方向反过来（`> RULE_THR` -> `< RULE_THR`），其余一字不动。"""
    with open(SRC, encoding="utf-8") as fp:
        code = fp.read()
    i = code.index("# 7. 4条 vs 5条")
    j = code.index("        return best_lbl", i)
    block = code[i:j]
    old = f"float(np.mean(c_gray < {DARK_THR})) > {RULE_THR}"
    if old not in block:
        raise SystemExit(f"变异失败：规则里找不到 `{old}`，说明阈值写法已被改动，"
                         f"本守卫与被测代码脱钩")
    return (code[:i]
            + block.replace(old, f"float(np.mean(c_gray < {DARK_THR})) < {RULE_THR}")
            + code[j:])


def class_from(code: str, name: str):
    mod = types.ModuleType(name)
    mod.__file__ = SRC
    mod.__package__ = "recognition"
    exec(compile(code, SRC + f"[{name}]", "exec"), mod.__dict__)
    return mod.TencentGridDetector


@unittest.skipIf("--mutate" in sys.argv, "变异检验单独跑")
class TestCenterBambooRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GT, encoding="utf-8") as fp:
            cls.shots = json.load(fp)["shots"]
        cls.rows = scan(TencentGridDetector(), cls.shots)

    def test_sample_size_is_big_enough_to_mean_anything(self):
        """覆盖守卫：真值堆太薄时，"两侧都过阈值"是假绿。"""
        n4 = sum(1 for r in self.rows if r[3] == "4s")
        n5 = sum(1 for r in self.rows if r[3] == "5s")
        self.assertGreaterEqual(n4, MIN_SAMPLES, f"真值 4s 只有 {n4} 格")
        self.assertGreaterEqual(n5, MIN_SAMPLES, f"真值 5s 只有 {n5} 格")
        self.assertGreaterEqual(len({r[0] for r in self.rows}), 5,
                                "判据只在少数平台被验过，换平台不一定还成立")

    def test_threshold_sits_in_the_gap_not_inside_a_cluster(self):
        bad = [(st, fr, sl, g, d) for st, fr, sl, g, _l, d in self.rows
               if (g == "4s" and d >= RULE_THR - MARGIN)
               or (g == "5s" and d <= RULE_THR + MARGIN)]
        self.assertFalse(bad, f"中心占比与阈值 {RULE_THR} 的间距不足 {MARGIN}：{bad[:6]}")

    def test_every_4s_5s_cell_is_read_correctly_in_production(self):
        wrong = [(st, fr, sl, g, l, round(d, 3)) for st, fr, sl, g, l, d in self.rows
                 if g != l]
        self.assertFalse(wrong, f"真值 4s/5s 的格子被判错：{wrong}")


class TestKnownDefectCell(unittest.TestCase):
    """钉住这条规则存在的那个理由：一张四索（纯 NCC 会给 5s）。

    夹具帧历史：它原先记在**途游 13** 名下，2026-10 逐帧看水印带后改判为 **JJ 23**
    （同一张截图、同一枚四索、同一个标签，只是换了平台名）。跟随改判而改这里是因为：
    按 (style, frame) 取夹具的写法一旦对不上就会 StopIteration 报错 —— 那是**响了**，
    比静默跳过好；但也绝不能把夹具改成“随便找一格 4s”，那等于把“这条规则存
    在的理由”从断言里抹掉。候选库要同步换成真实平台（jj），否则它跑的不是生产
    在那帧上实际使用的那套核。
    """

    FIXTURE_STYLE = "jj"
    FIXTURE_FRAME = 23

    def setUp(self):
        with open(GT, encoding="utf-8") as fp:
            e = next(x for x in json.load(fp)["shots"]
                     if x["style"] == self.FIXTURE_STYLE
                     and int(x["frame"]) == self.FIXTURE_FRAME)
        img = cv2.imread(os.path.join(REPO, e.get("src") or os.path.join("public", "0"),
                                      e["file"]))
        self.assertIsNotNone(img, "夹具帧读不出来")
        self.det = TencentGridDetector()
        self.det.set_platform_styles(STYLE_OF.get(self.FIXTURE_STYLE))
        d = sorted(self.det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
        x, y, w, h = [int(v) for v in d[1][0]]
        self.crop = img[y:y + h, x:x + w]
        self.assertEqual(e["hand"][1], "4s", "GT 变了，本用例的期望需同步重钉")

    def test_prod_reads_it_as_4s(self):
        self.assertEqual(self.det.classify_tile(self.crop)[0], "4s",
                         "那枚四索又被 5s 抢走：中心索裁决失效了")

    @unittest.skipUnless("--mutate" in sys.argv, "只在 --mutate 下跑")
    def test_mutation_is_detected(self):
        """反向判据必须让目标格变回去：否则这条用例根本不在测该规则，绿灯是假的。"""
        mcls = class_from(mutated_source(), "mutant")
        det = mcls()
        det.set_platform_styles(STYLE_OF.get(self.FIXTURE_STYLE))
        self.assertEqual(det.classify_tile(self.crop)[0], "5s",
                         "把判据反过来仍判 4s -> 这条规则没有被任何断言覆盖")
        rows = scan(mcls(), self.shots_all())
        n_wrong = sum(1 for st, fr, sl, g, l, _d in rows if g != l)
        self.assertGreater(n_wrong, 0,
                           "反向判据后全库 4s/5s 仍然全对 -> 阈值裁决没有参与这些帧")

    def shots_all(self):
        with open(GT, encoding="utf-8") as fp:
            return json.load(fp)["shots"]


if __name__ == "__main__":
    if "--mutate" in sys.argv:
        sys.argv.remove("--mutate")
        print("[mutate] 反向判据：test_mutation_is_detected 必须通过"
              "（即被污染的代码确实被报出来）")
    unittest.main(verbosity=2)
