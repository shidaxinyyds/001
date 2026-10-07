# -*- coding: utf-8 -*-
"""跳帧必须真的绕开识别：方向几何归一 / 帧差判定 / 补验证 三者先后的接线守卫。

被钉住的是「跳帧空转」这一退化本身。它有实测形状，不是想象中的病：
1000 帧压测里 100 个跳帧帧**每个都付了 997ms**，与非跳帧帧的 950ms 基本相同 ——
因为旧顺序在 `process` 开头就把方向验证（内含一次完整手牌通道识别）跑完了，
等跳到「本帧画面冻结、可以复用上一帧结果」的判定时，钱已经花光。

本文件锁四件事：

① 跳帧帧零识别调用（核心棘轮）
   画面冻结的静默帧一帧识别都不许付。这是本次改动的**全部收益**，也是唯一
   不许回退的不变量。

② 非跳帧帧恰好一次调用，且调用拿到的是**整屏**图（不是 hand_roi 裁片）
   方向验证顺手算出的 rows 是整屏坐标，`_apply_conf` 与 aspect 下限吃的就是
   这批框（见 localtest/aspect_floor.py）。如果验证被跳过而由 process 主体兜底
   去 detect，输入会变成 hand_roi 裁片 —— 同一张牌在两种坐标系下的检出分数
   不同，这不是等价改写。所以「谁跑的、拿什么跑的」都要钉住。

③ 反向横屏（方向锁被推翻）那一帧绝不被跳帧，且必须自愈
   这条是①的边界：省钱的代价不能是把「画面真的变了」也跳掉。实测翻转帧的帧差
   为 7048，而 FRAME_SKIP_DIFF_THRESH=3.0 —— 差 3 个数量级，不是擦线运气。
   同时验证翻转后的第二帧能跳（帧差基线被 `_frame_skipper.set_baseline` 收到了
   新朝向上；没收敛的话每次旋转都要多付一整帧识别）。

④ 静默期逐帧 payload 与改动前**逐字一致**（剥掉天生每帧不同的计时字段）
   ①说的「零精度代价」必须是可证的：跳帧帧返回的本来就是上一帧状态。

变异检验（`TestMutationControls`，随主守卫一起跑，也可单跑）：
    py -3.10 -X utf8 localtest/test_skip_frame_guard.py --mutate
  两个变异体是**正向对照**——把生产顺序换成坏顺序，断言它确实坏成 ① / ② 禁止的
  那个样子（跳帧帧照付识别 / 识别输入退化成 hand_roi 裁片）。若哪天有人把 ① 或 ②
  削弱成空断言，这两条会与主守卫互相矛盾而报红。

运行：py -3.10 -X utf8 localtest/test_skip_frame_guard.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

FIX = os.path.join(HERE, "shots_tuyou")
PLATFORM = "tuyou"
MUTATE = "--mutate" in sys.argv


def _frames(limit=3):
    out = []
    for f in sorted(os.listdir(FIX)):
        if not f.endswith(".jpg"):
            continue
        im = cv2.imread(os.path.join(FIX, f))
        if im is not None:
            out.append((f, im))
        if len(out) >= limit:
            break
    return out


class Tracer:
    """包住手牌通道的 `detect_all_rows`：只记账，不改行为。

    记的是「本帧被叫了几次」+「拿到的图多高」。后者是关键：整屏高 vs hand_roi
    裁片高，一眼就能看出这批 rows 是谁算的。

    必须**作为上下文管理器使用**（`with Tracer(det) as tr:`）：补丁在 `__enter__`
    里装、`__exit__` 里卸。只 new 不用等于什么都没记 —— 踩过，全部计数恒 0 会把
    「跳帧仍在付识别」这个真缺陷伪装成不存在。
    """

    def __init__(self, det) -> None:
        self.det = det
        self.orig = det.detect_all_rows
        self.calls = 0
        self.heights = []

    def __enter__(self) -> "Tracer":
        def timed(image, *a, **kw):
            self.calls += 1
            self.heights.append(int(image.shape[0]))
            return self.orig(image, *a, **kw)

        self.det.detect_all_rows = timed
        return self

    def __exit__(self, *exc) -> None:
        self.det.detect_all_rows = self.orig


class FrameLog:
    """一帧的账：是否跳帧、通道调用次数、通道输入高度、方向锁、帧差、手牌串。"""

    def __init__(self, name, eng, img, full_h) -> None:
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            tr = Tracer(eng.get_hand_detector())
            with tr:
                res = eng.process(img)
        self.name = name
        self.ms = (time.perf_counter() - t0) * 1000.0
        self.calls = tr.calls
        self.heights = tr.heights
        self.full_h = full_h
        self.payload = json.loads(res.result) if res is not None else {}
        self.payload.pop("elapsed", None)
        self.skipped = bool(self.payload.get("frame_skipped"))
        # 「非牌桌」也会返回 frame_skipped 结果（另一条早退路径），与本守卫的
        # 主体无关，但必须能区分开，否则会把牌桌校验的失败当成跳帧判定的失败。
        self.non_table = int(getattr(eng, "_non_table_frames", 0))
        self.orient = eng._orient
        self.diff = float(getattr(eng, "_cur_frame_diff", float("inf")))
        self.image = getattr(res, "image", None) if res is not None else None

    def norm_payload(self):
        p = dict(self.payload)
        d = p.get("diag")
        if isinstance(d, dict):
            d = dict(d)
            d.pop("perf", None)      # 分阶段耗时：只要跑过一帧就不同，非语义
            p["diag"] = d
        return p

    def preview_sig(self):
        pv = self.image
        if pv is None or not getattr(pv, "size", 0):
            return None
        return (int(pv.shape[0]), int(pv.shape[1]), int(pv.sum(dtype="int64") % 1000000007))


def quiet_engine():
    import engine.engine as E
    E.load_platform = lambda *a, **kw: PLATFORM
    with contextlib.redirect_stdout(io.StringIO()):
        eng = E.Engine()
    return eng


def settle_old_order(eng):
    """变异体（**勿接回生产**）：把验证塞回跳帧判定之前 —— 即改动前的顺序。"""
    eng._settle_orientation = lambda img: (eng._apply_orientation(img), False)


def settle_never_verify(eng):
    """变异体（**勿接回生产**）：几何归一后声称「本帧不需要验证」，永远不补做。"""
    orig = eng._settle_orientation

    def settled(img):
        image, _need = orig(img)
        return image, False
    eng._settle_orientation = settled


def warm_until_skip(eng, img, name, full_h, limit=24):
    """连续喂同一帧直到出现跳帧帧；返回跳帧开始前的日志列表。"""
    log = []
    for _ in range(limit):
        rec = FrameLog(name, eng, img, full_h)
        log.append(rec)
        if rec.skipped:
            return log
    return log


class TestSkipFrameGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = _frames(3)
        if not cls.frames:
            raise unittest.SkipTest(f"缺夹具 {FIX}")
        cls.name0, cls.img0 = cls.frames[0]
        cls.full_h = int(cls.img0.shape[0])

    # ---- ① 跳帧帧零识别调用 ------------------------------------------------
    def test_skip_frames_pay_zero_recognition(self):
        eng = quiet_engine()
        warm_until_skip(eng, self.img0, self.name0, self.full_h)
        log = [FrameLog(self.name0, eng, self.img0, self.full_h) for _ in range(6)]
        skipped = [r for r in log if r.skipped]
        self.assertTrue(skipped, "连续喂同一帧都没触发跳帧：本守卫的空转断言是空的")
        for r in skipped:
            self.assertEqual(r.calls, 0,
                             f"跳帧帧 {r.name} 仍调用手牌通道 {r.calls} 次：识别付在跳帧"
                             "判定之前，跳帧成了空转")
            self.assertLess(r.ms, 200.0,
                            f"跳帧帧 {r.name} 耗时 {r.ms:.0f}ms，静默期根本没省下钱")

    # ---- ② 非跳帧帧恰好一次、且输入是整屏 ---------------------------------
    def test_recognizing_frames_use_the_full_frame(self):
        eng = quiet_engine()
        log = [FrameLog(self.name0, eng, self.img0, self.full_h) for _ in range(6)]
        kept = [r for r in log if not r.skipped]
        self.assertTrue(kept, "一帧都没真识别，断言无从谈起")
        for r in kept:
            self.assertEqual(r.calls, 1,
                             f"{r.name} 非跳帧却调用通道 {r.calls} 次（同帧付两遍钱）")
            self.assertEqual(r.heights, [self.full_h],
                             f"{r.name} 的识别输入高度 {r.heights} != 整屏 {self.full_h}："
                             "rows 不再是方向验证算的整屏坐标，_apply_conf/aspect 下限的"
                             "坐标系跟着换掉，这不是等价改写")

    # ---- 画面变化时不许误跳 ------------------------------------------------
    def test_changing_frames_are_never_skipped(self):
        eng = quiet_engine()
        warm_until_skip(eng, self.frames[0][1], self.frames[0][0], self.full_h)
        recognized, off_table = [], []
        for name, img in self.frames[1:]:
            r = FrameLog(name, eng, img, int(img.shape[0]))
            if r.non_table:
                # 另一条早退路径（牌桌校验没过）：它返回的也是缓存 payload，但那
                # 不是「智能跳帧」，不在本守卫的口径里；记下来以便看见。
                off_table.append(name)
                continue
            if r.skipped:
                # 到了这里还跳，就只能是帧差判定被绕过 —— 本次改动最怕的退化。
                self.assertLess(r.diff, 3.0,
                                f"{name} 帧差 {r.diff:.1f} 远超阈值却仍判跳帧")
            else:
                recognized.append(name)
        self.assertGreaterEqual(len(recognized), 1, "换画面后一帧都没真识别")
        self.assertLess(len(off_table), len(self.frames) - 1,
                        f"对照帧全都没过牌桌校验（{off_table}），本测试是空的")

    # ---- ③ 反向横屏：那帧不跳、要自愈、下一帧收敛回跳帧 ------------------
    def test_orientation_flip_is_recognized_not_skipped(self):
        eng = quiet_engine()
        warm = warm_until_skip(eng, self.img0, self.name0, self.full_h)
        self.assertEqual(eng._orient, 0, "夹具帧应当锁在 0°")
        pre_count = int(warm[-1].payload.get("count") or 0)
        self.assertGreaterEqual(pre_count, 13, f"热身结束时手牌不足：{pre_count}")

        flipped = cv2.rotate(self.img0, cv2.ROTATE_180)
        r = FrameLog(f"{self.name0}/180", eng, flipped, self.full_h)
        self.assertFalse(r.skipped,
                         "方向锁被推翻的这一帧被跳掉了：延后验证省钱的代价是漏掉旋转")
        self.assertGreater(r.diff, 100.0 * 3.0,
                           f"翻转帧帧差 {r.diff:.1f} 没远超阈值（FRAME_SKIP_DIFF_THRESH=3.0），"
                           "「翻转必不跳帧」靠的是运气而不是判据")
        self.assertEqual(eng._orient, 180, f"反向横屏没自愈，方向锁={eng._orient}")
        # 自愈帧付 **2** 次：0° 先探一次（牌不在下半部 → 判据不过），旋到 180°
        # 再命中一次。这是改动前就有的代价，不是本次引入的；写成 1 会把真实语义
        # 当成 bug。两次都必须拿整屏图（裁片会让 rows 坐标系换掉）。
        self.assertEqual(r.calls, 2, f"翻转帧的自愈调用次数应为 2（0°试探+180°命中），实为 {r.calls}")
        self.assertEqual(set(r.heights), {self.full_h},
                         f"翻转帧的识别输入不是整屏：{r.heights}")
        self.assertEqual(int(r.payload.get("count") or 0), pre_count,
                         f"翻转后手牌数掉了：{r.payload.get('count')} vs {pre_count}")

        again = FrameLog(f"{self.name0}/180b", eng, flipped, self.full_h)
        self.assertTrue(again.skipped,
                        "翻转后的第二帧仍在重识别：帧差基线没跟着新朝向刷新")
        self.assertEqual(again.calls, 0)

    # ---- ④ 与改动前逐帧等价（静默期）------------------------------------
    def test_outputs_identical_to_old_ordering(self):
        """把两侧引擎喂同一串静默帧，逐帧比 payload 与预览。

        两侧都从**同一段新语义热身**开始，否则进入测量段的状态不同（老语义的
        热身会多跳/少跳帧，比出来的差异是夹具造的，不是生产行为）。
        """
        rounds = 6
        sides = {}
        for side in ("new", "old"):
            eng = quiet_engine()
            for _ in range(10):                       # 热身（立稳手牌 + 进入跳帧）
                with contextlib.redirect_stdout(io.StringIO()):
                    eng.process(self.img0)
            if side == "old":
                settle_old_order(eng)
            sides[side] = [FrameLog(self.name0, eng, self.img0, self.full_h)
                           for _ in range(rounds)]
        for i, (a, b) in enumerate(zip(sides["new"], sides["old"])):
            self.assertEqual(a.skipped, b.skipped, f"#{i} 跳帧判定两侧不同")
            self.assertEqual(a.norm_payload(), b.norm_payload(),
                             f"#{i} payload 两侧不同（已剥 diag.perf 计时）")
            self.assertEqual(a.preview_sig(), b.preview_sig(), f"#{i} 预览两侧不同")


class TestMutationControls(unittest.TestCase):
    """变异检验的正向对照：两个变异体必须让上面的断言成立不了。"""

    @classmethod
    def setUpClass(cls):
        cls.name0, cls.img0 = _frames(1)[0]
        cls.full_h = int(cls.img0.shape[0])

    def test_mutant_old_order_still_pays_on_skip_frames(self):
        eng = quiet_engine()
        settle_old_order(eng)
        log = [FrameLog(self.name0, eng, self.img0, self.full_h) for _ in range(16)]
        skipped = [r for r in log if r.skipped]
        self.assertTrue(skipped, "老顺序也该有跳帧，否则对照无意义")
        self.assertTrue(all(r.calls >= 1 for r in skipped),
                        "老顺序的跳帧帧居然没付识别？那①的守卫在测什么")

    def test_mutant_never_verify_degrades_to_roi_crop(self):
        """变异体「延后了却从不补做验证」必须让识别退化成 hand_roi 裁片。

        喂**不同**帧：同一串静默帧从这个变异体下会全部命中跳帧，`kept` 里只剩
        首帧（首帧方向未锁，settle 内部本来就要验一次、拿整屏图），断言会空转。
        """
        eng = quiet_engine()
        settle_never_verify(eng)
        frames = _frames(4)
        log = [FrameLog(f"{n}#{i}", eng, im, int(im.shape[0]))
               for i, (n, im) in enumerate(frames * 2)]
        kept = [r for r in log[1:] if not r.skipped]
        self.assertTrue(kept, "变异体下一帧都没真识别，对照无从成立")
        for r in kept:
            self.assertTrue(r.heights and max(r.heights) < r.full_h,
                            f"变异体本应让识别退化成 hand_roi 裁片，实际输入 {r.heights}"
                            f"（整屏 {r.full_h}）")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：两个变异体必须「如期坏掉」")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
