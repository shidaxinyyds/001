# -*- coding: utf-8 -*-
"""手牌并行打分守卫：接线 / 保序 / 阈值 / 不泄漏线程。

为什么有这条改动（实测 `build/thread_memo.txt`）：每帧 691ms 的手牌通道里，打分是
「25 枚互不相关的 NCC 扫描」，串行 5563ms/74 枚；同一批牌面 2 线程 1.91x、
4 线程 5.28x、8 线程 6.90x，且**每枚结果与串行逐枚全等**。同一次实测把另一条看着
更安全的路判死了：按牌面像素做精确记忆化，1px 位移 / 亮度±2 / JPEG 重压全部失配，
只在逐像素静止时命中，对循环回放的压测夹具更是刷分工具。

于是风险全在实现细节上，不在算法上。守卫逐条对应：
  ① 等价：并行与串行必须**逐格全等**（rect + label + conf），差一格就不许开；
  ② 接线：并行路径必须真的被走到（线程池被创建），否则整份改动是死代码；
  ③ 阈值：张数不足 `PARALLEL_MIN_TILES` 时必须不建池（调度开销大于收益）；
  ④ 保序：完成顺序不许泄漏进格位——用「越靠后的牌睡得越久」的假打分专门逆序完成；
  ⑤ 不泄漏：线程数不随帧数增长（实时链路 1000 帧无泄漏那条硬要求同样管线程）。

`--mutate` 只跑变异对照：把生产换成坏写法，必须**如期坏到主守卫报红**，
否则主守卫就是空断言。M1 对应②（把阈值抬到天上 -> 并行从没发生），
M2 对应①④（按分数排序返回 -> 格位错位）。

用法：py -3.10 -X utf8 localtest/test_parallel_classify_guard.py [--mutate]
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import recognition.tencent_grid_detector as T  # noqa: E402
from modes import available_set  # noqa: E402
from eval_new_material import STYLE_OF  # noqa: E402
from layer_cost import mode_for  # noqa: E402

GT = os.path.join(HERE, "gt", "shots_b1.json")
PER_STYLE = 1               # 每平台 1 帧：等价性看的是逐格全等，一帧就够暴露错位
MAX_FRAMES = 4              # 守卫预算：每次整帧跑要几秒，超了就没人在回归里跑它
MUTATE = "--mutate" in sys.argv


def _flat(dets):
    """[(rect, label, conf)] -> 可比较元组（rect 取整，conf 保留 6 位）。"""
    return [(int(r[0]), int(r[1]), int(r[2]), int(r[3]), lbl, round(float(sc), 6))
            for r, lbl, sc in dets]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = []
        cls.setup_error = ""
        if not os.path.exists(GT):
            cls.setup_error = f"夹具缺失：{GT}"
            return
        with open(GT, encoding="utf-8") as fp:
            shots = [e for e in json.load(fp)["shots"] if e.get("verified")]
        seen = {}
        for e in shots:
            if seen.get(e["style"], 0) >= PER_STYLE:
                continue
            path = os.path.join(REPO, e.get("src") or os.path.join("public", "1"), e["file"])
            img = cv2.imread(path)
            if img is None:
                continue
            seen[e["style"]] = seen.get(e["style"], 0) + 1
            platform = STYLE_OF.get(e["style"], "tencent")
            hand = sorted({x for x in e["hand"] if x != "?"})
            cls.frames.append({"style": e["style"], "frame": e["frame"], "platform": platform,
                               "mode": mode_for(platform, hand)[0], "img": img})
        # 等价性是「同一条打分码路的两种调度」的结构性质，不是平台特性；平台几何由
        # 别的守卫管。所以这里砍到 4 帧换运行时间，是明码标价的预算不是偷懒。
        cls.frames = cls.frames[:MAX_FRAMES]
        if not cls.frames:
            cls.setup_error = "夹具里一帧都读不出来"

    def setUp(self):
        self.det = T.TencentGridDetector()

    def tearDown(self):
        self.det.parallel_classify = True
        pool = self.det._pool
        if pool is not None:
            pool.shutdown(wait=True)

    def _run(self, f, parallel=True):
        """跑一帧手牌条，返回逐格元组序列。"""
        self.det.parallel_classify = parallel
        self.det.set_platform_styles(f["platform"])
        self.det.set_mode_tiles(available_set(f["mode"]))
        with contextlib.redirect_stdout(io.StringIO()):
            return _flat(self.det.detect_hand_strip(f["img"]))


class TestParallelEquivalence(Base):
    def setUp(self):
        super().setUp()
        if self.setup_error:
            self.skipTest(self.setup_error)

    def test_1_parallel_is_identical_to_serial_tile_by_tile(self):
        """① 并行只改「什么时候算」，不改「算什么」。

        比较单位是**格位**：rect 与 conf 一起比，所以整排错位、漏算一格、
        重复算一格都会在这里报红，而不是被某个均值掩盖掉。
        """
        bad = []
        for f in self.frames:
            par = self._run(f, parallel=True)
            ser = self._run(f, parallel=False)
            if len(par) != len(ser):
                bad.append((f["style"], f["frame"], f"格数 {len(par)} vs {len(ser)}"))
                continue
            for i, (a, b) in enumerate(zip(par, ser)):
                if a != b:
                    bad.append((f["style"], f["frame"], f"第{i}格 {a} vs {b}"))
        self.assertEqual(bad, [], f"以下帧并行与串行逐格不一致：{bad}")

    def test_2_pool_is_actually_created(self):
        """② 接线：一帧下来线程池必须真的存在，否则这份改动是死代码。"""
        self.assertIsNone(self.det._pool, "新建的探测器不该已经带线程池")
        self._run(self.frames[0], parallel=True)
        self.assertIsNotNone(self.det._pool,
                             "并行开着、牌也够，却没建线程池 —— 并行从未发生")
        self.assertLessEqual(self.det._pool._max_workers, T.PARALLEL_WORKERS,
                             f"线程数 {self.det._pool._max_workers} 超出约定上限 "
                             f"{T.PARALLEL_WORKERS}：会和 cv2 的内部线程抢核")

    def test_3_knob_off_never_touches_the_pool(self):
        """② 的对照：关掉开关必须一路串行。"""
        self._run(self.frames[0], parallel=False)
        self.assertIsNone(self.det._pool, "开关关了还建线程池：调试口子不成立，"
                                        "以后的 A/B 对照也就都不成立")

    def test_4_small_batch_stays_serial(self):
        """③ 阈值：牌少时不许开线程。"""
        crops = [np.zeros((40, 30, 3), dtype=np.uint8) for _ in range(T.PARALLEL_MIN_TILES - 1)]
        with contextlib.redirect_stdout(io.StringIO()):
            out = self.det._classify_batch(crops, None)
        self.assertEqual(len(out), len(crops))
        self.assertIsNone(self.det._pool,
                          f"{len(crops)} 枚（< {T.PARALLEL_MIN_TILES}）就建池："
                          "阈值没接线，小批量反而更慢")

    def test_5_order_survives_inverted_completion(self):
        """④ 保序：故意让**后提交的先完成**，返回值仍必须按提交顺序。

        这是 `pool.map` 与 `as_completed` 的唯一区别所在。真实链路里它坏了不会
        报错、分数照样高，只是整排牌挪了格位——帧级均值几乎不动，所以只能由这条
        专门盯着，不能靠①的统计巧合。
        """
        n = T.PARALLEL_MIN_TILES + 3
        crops = []
        for i in range(n):
            c = np.zeros((40, 30, 3), dtype=np.uint8)
            c[0, 0, 0] = i                      # 把格位编码进像素，假打分按它回答
            crops.append(c)

        class Ordered(T.TencentGridDetector):
            def classify_tile(self, crop, avail=None, styles=None):
                i = int(crop[0, 0, 0])
                time.sleep((n - i) * 0.02)      # 越靠后的格睡得越短 -> 完成序倒过来
                return f"L{i:02d}", i / 100.0

        det = Ordered()
        try:
            out = det._classify_batch(crops, None)
        finally:
            if det._pool is not None:
                det._pool.shutdown(wait=True)     # 不起孤儿线程，免得污染⑤的计数
        self.assertEqual([lbl for lbl, _s in out], [f"L{i:02d}" for i in range(n)],
                         f"并行返回的顺序不是提交顺序：{out}")

    def test_6_threads_do_not_grow_with_frames(self):
        """⑤ 不泄漏：线程数不随帧数增长（实时链路 1000 帧那条硬要求同样管线程）。"""
        two = self.frames[:2]
        self._run(two[0], parallel=True)
        base = threading.active_count()
        for _ in range(3):
            for f in two:
                self._run(f, parallel=True)
        grew = threading.active_count() - base
        self.assertLessEqual(grew, 0,
                             f"又跑了 6 次整帧后线程数净增 {grew}：线程池没被复用，"
                             "长时间运行会一直攒线程")


@unittest.skipUnless(MUTATE, "只在 --mutate 下跑")
class TestMutationControls(Base):
    """变异对照：坏写法必须坏成主守卫禁止的样子。"""

    def setUp(self):
        super().setUp()
        if self.setup_error:
            self.skipTest(self.setup_error)

    def test_M1_unreachable_threshold_unwires_parallelism(self):
        """把 `PARALLEL_MIN_TILES` 抬到天上 -> ②的「池必须存在」必须报红。

        没有这条对照，②可能只是在断言「我恰好碰上了一个会建池的阈值」，
        而阈值一旦被人调到高于真实张数，并行就静默消失了。
        """
        orig = T.PARALLEL_MIN_TILES
        T.PARALLEL_MIN_TILES = 10 ** 9
        try:
            self._run(self.frames[0], parallel=True)
            bad = self.det._pool is None
        finally:
            T.PARALLEL_MIN_TILES = orig
        self.assertTrue(bad, "阈值调到 1e9 后线程池照样被创建：②没在测阈值")

    def test_M2_sorting_results_breaks_slot_identity(self):
        """把返回按分数排序（看着像「优化」）-> ①④必须报红。

        这条是①的灵敏度对照：如果逐格全等连整体错位都测不出来，那①就是空断言。
        """
        f = self.frames[0]
        good = self._run(f, parallel=True)
        orig = self.det._classify_batch

        def sorted_batch(crops, styles):
            return sorted(orig(crops, styles), key=lambda t: -t[1])

        self.det._classify_batch = sorted_batch
        try:
            shifted = _flat(self.det.detect_hand_strip(f["img"]))
        finally:
            del self.det._classify_batch
        self.assertNotEqual(good, shifted,
                           "按分数重排后逐格仍然一致：说明这些帧的分数完全相同，"
                           "①的比较粒度不够，得换夹具")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 手牌并行：两个坏写法必须「如期坏掉」")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
