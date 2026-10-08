# -*- coding: utf-8 -*-
"""`classify=False` 契约守卫：YOLO 通道必须真的跳过牌面分类。

背景（实测 `localtest/ab_classify_fix.py` -> `build/ab_classify_all.txt`，
91 帧 × old/new/old 三趟同进程对比）：
`YOLODetector.detect_all_rows` 的签名里有 `classify`，函数体**从不读它**。于是两个
明确声明「只要几何、不要标签」的调用方每帧照付全套模板匹配：

  * `Engine._verify_hand_evidence`（拿底部张数判「在不在牌桌上」）
    —— jj 平台实测 997.8ms/帧，占该帧总耗时 56.6%；
  * `Engine._probe_orientation` 阶段 A（4 方向几何筛选，注释写着 ~17ms/方向）
    —— 微乐帧实测单次 4899ms。

对照结果：修好后引擎产出逐字段（hand/count/status/drawing_tile/phase_label）
**0/91 不一致**，对 GT 不符三趟同为 3；jj p50 1358.5ms -> 738.3ms（-45.7%）、
p95 1940.4ms -> 827.1ms；微乐 p95 5047.1ms -> 2553.0ms。同进程内 old 自身漂移
96.3ms，jj/weile 的降幅远超该噪声，其余平台在噪声内（不声称有收益）。

本守卫锁三件事，每条都是「改回坏写法就必红」：
1. `classify=False` 时**一次都不许**调用模板分类器（替身计数 == 0）。
2. `classify=True` 时仍必须调用（> 0）——把精修一起删掉不会让第 1 条变红，
   只会悄悄摘掉面板的读牌能力，所以这条同样必须是硬断言。
3. `classify=False` 的返回不得出现 YOLO 框之外的槽位中心（补槽只在精修路径里
   发生；它若在读不到标签的情况下被触发，等于凭空多出一张没有证据的牌）。
4. 引擎侧的调用点必须继续声明 `classify=False`（把 `_verify_hand_evidence` 改回
   `classify=True` 就是本次事故的原样复发，且不会有任何精度测试变红）。

夹具：`localtest/gt/shots_b1.json` 里 jj 平台已钉帧（读不到夹具/模型判红，不静默跳过
——本守卫量的就是 YOLO 通道，模型缺席时它等于没跑）。

运行：
  py -3.10 -X utf8 localtest/test_classify_contract_guard.py          # 主守卫
  py -3.10 -X utf8 localtest/test_classify_contract_guard.py --mutate  # 变异对照
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

from recognition.yolo_detector import YOLODetector  # noqa: E402

GT = os.path.join(HERE, "gt", "shots_b1.json")
MUTATE = "--mutate" in sys.argv


def load_frame(style: str = "jj"):
    """取该风格第一张「手牌已核对且已知格 >= 9」的夹具帧。"""
    with open(GT, encoding="utf-8") as fp:
        shots = json.load(fp)["shots"]
    for e in shots:
        if e.get("style") != style or not e.get("verified"):
            continue
        hand = e.get("hand") or []
        if len([c for c in hand if c and c != "?"]) < 9:
            continue
        path = os.path.join(REPO, e["src"], e["file"])
        img = cv2.imread(path)
        if img is None:
            raise AssertionError(f"夹具帧读不出来：{path}")
        return e, img
    raise AssertionError(f"夹具里没有 {style} 平台的可用帧（{GT}）")


def counting_helper(det):
    """给模板分类器装计数器；装不上就是链路断了，判红而不是跳过。"""
    helper = getattr(det, "_phase_helper", None)
    if helper is None:
        raise AssertionError("YOLODetector._phase_helper 为空：模板精修链路不存在")
    calls = []
    orig = helper.classify_tile

    def spy(patch, *a, **k):
        calls.append(1)
        return orig(patch, *a, **k)

    helper.classify_tile = spy
    return calls, lambda: setattr(helper, "classify_tile", orig)


def recording_strip(det):
    """记录 YOLO 自己切出来的框中心，用来断言「未分类时不会凭空多槽」。"""
    seen = []
    orig = det.detect_strip

    def spy(strip_bgr, offset_x=0, offset_y=0, *a, **k):
        out = orig(strip_bgr, offset_x=offset_x, offset_y=offset_y, *a, **k)
        for rect, _lbl, _conf in out or []:
            seen.append(rect[0] + rect[2] / 2.0)
        return out

    det.detect_strip = spy
    return seen, lambda: setattr(det, "detect_strip", orig)


def ignore_classify(det):
    """坏写法：像事故当时那样无视 classify 参数。"""
    orig = type(det).detect_all_rows

    def patched(self, image, classify=True, *a, **k):
        return orig(self, image, True, *a, **k)

    type(det).detect_all_rows = patched
    return lambda: setattr(type(det), "detect_all_rows", orig)


class TestClassifyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.det = YOLODetector()
        if not cls.det.is_available:
            raise AssertionError("YOLO 模型不可用：本守卫无对象可量（判红，不跳过）")
        cls.e, cls.img = load_frame("jj")

    def test_classify_false_never_touches_template_bank(self):
        calls, restore = counting_helper(self.det)
        try:
            rows = self.det.detect_all_rows(self.img, classify=False,
                                           allow_rotation=False)
        finally:
            restore()
        n = sum(len(r) for r in rows)
        self.assertGreater(n, 0, "几何路径本该还能切出牌框，0 张说明改动把检测也带走了")
        self.assertEqual(
            0, len(calls),
            f"classify=False 仍打了 {len(calls)} 次模板分类：参数又被无视了"
            f"（牌桌实证每帧白付约 1s）")

    def test_classify_true_still_refines(self):
        calls, restore = counting_helper(self.det)
        try:
            self.det.detect_all_rows(self.img, classify=True, allow_rotation=False)
        finally:
            restore()
        self.assertGreater(
            len(calls), 0,
            "classify=True 却一次模板分类都没跑：精修被整体删除，面板读牌能力已断")

    def test_unclassified_rows_invent_no_slots(self):
        centers, restore = recording_strip(self.det)
        try:
            rows = self.det.detect_all_rows(self.img, classify=False,
                                           allow_rotation=False)
        finally:
            restore()
        got = [r[0][0] + r[0][2] / 2.0 for row in rows for r in row]
        for cx in got:
            self.assertTrue(
                any(abs(cx - s) < 8.0 for s in centers),
                f"classify=False 输出了 YOLO 框之外的槽中心 x={cx:.0f}："
                "补槽只该发生在精修路径里")

    def test_engine_declares_classify_false_for_table_evidence(self):
        """引擎侧调用点必须继续声明「只要张数」，否则事故原样复发。"""
        import engine.engine as EE

        captured = {}

        class Stub:
            def detect_all_rows(self, image, classify=True, *a, **k):
                captured["classify"] = classify
                return [[((0, int(image.shape[0] * 0.9) + i * 10, 40, 60), "5m", 0.9)
                         for i in range(9)]]

        eng = EE.Engine.__new__(EE.Engine)
        eng._detector = Stub()
        eng._hand_detector = None
        n = EE.Engine._verify_hand_evidence(eng, self.img)
        self.assertIn("classify", captured, "牌桌实证根本没走主检测器，守卫对象错位")
        self.assertFalse(captured["classify"],
                         "牌桌实证又按 classify=True 要标签：只要张数就不该付模板钱")
        self.assertGreaterEqual(n, 5, "实证张数判据本身不许被改动")


class TestMutationControls(unittest.TestCase):
    """变异对照：把坏写法拉回现行链路，主断言必须「如期坏掉」。"""

    def test_ignoring_classify_must_go_red(self):
        det = YOLODetector()
        if not det.is_available:
            raise AssertionError("YOLO 模型不可用，变异对照无法进行（判红）")
        _e, img = load_frame("jj")
        restore = ignore_classify(det)
        calls, restore_helper = counting_helper(det)
        try:
            det.detect_all_rows(img, classify=False, allow_rotation=False)
        finally:
            restore_helper()
            restore()
        self.assertGreater(
            len(calls), 0,
            "变异无效：无视 classify 的坏写法没有触发任何分类调用 —— "
            "说明主守卫第 1 条在测空气")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：坏写法必须「如期坏掉」")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
