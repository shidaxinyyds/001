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

② 非跳帧帧恰好一次调用；默认喂**整屏**，产出的 rows 必须是**整屏坐标**。
   这条原来钉的是「输入必须是 hand_roi **条带**」，那是把「识别缺张」的诊断结论
   直接当成默认喂法留下的：格网通道按图像高度铺**等距格网**，整屏与条带两套切法
   会给出不同格数 —— 报障三帧确实是条带好（整屏 4 格@0.778 → 条带 8 格@0.805），
   但 37 帧**已标注** GT 语料上方向完全相反：整屏逐张 444/444（100%）vs 条带
   389/445（82.5%）且普遍少 1~5 格（build/_ab_channel_gt.py）。于是最终契约是
   「整屏为主 + 同帧反超」：条带只在整屏读数低于地板线时才去试，且必须**格数更多
   且峰值不低**才顶掉整屏读数（见 `_hand_channel_rows`）。
   所以本守卫在这一侧钉的是**钱**：读数健康的帧只付一次、且付在整屏上，不许每帧
   多买一条注定没用的条带（那等于把全平台精度基线换成个例机的赌注 + 每帧一个
   多余完整识别）。另一侧钉的是**坐标**：产出必须是整屏坐标，条带被采信时由
   `_hand_channel_rows` 加回 y0 —— `_apply_conf`、aspect 下限、亮度校验与「牌行
   在画面下半部」判据全吃后者。（见 localtest/aspect_floor.py：同一张牌在两种
   坐标系下的检出分数不同。）

③ 反向横屏（方向锁被推翻）那一帧绝不被跳帧，且必须自愈
   这条是①的边界：省钱的代价不能是把「画面真的变了」也跳掉。实测翻转帧的帧差
   为 7048，而 FRAME_SKIP_DIFF_THRESH=3.0 —— 差 3 个数量级，不是擦线运气。
   同时验证翻转后的第二帧能跳（帧差基线被 `_frame_skipper.set_baseline` 收到了
   新朝向上；没收敛的话每次旋转都要多付一整帧识别）。

④ 静默期逐帧 payload 与改动前**逐字一致**（剥掉天生每帧不同的计时字段）
   ①说的「零精度代价」必须是可证的：跳帧帧返回的本来就是上一帧状态。

⑤ 缓慢漂移不得无限期复用旧 payload（累计漂移门）
   ①~④ 全部建立在「相邻帧差小 = 画面没变」这个假设上，而它有个实在的漏洞：
   `diff()` 每帧（**包括被跳过的帧**）都把 `_last_sig` 往前推，于是「每帧只比上一帧
   动一点点」可以永远过门 —— 逐帧挪动的动画、亮度缓慢漂移，连着几十帧后画面已经
   面目全非，面板却还在播第一帧的结论。这条把漏洞钉成不变量：
   **凡是跳帧的帧，它相对「上一次真识别过的那帧」的累计变化必须 < 阈值。**
   夹具不自标定不出真幅度就直接判红（ROI 会裁掉上半屏、降采样会抹平小块，写死
   幅度最容易调出一个「签名根本不变」的假夹具，那 ⑤ 就在测空气）。
   配套还钉「锚点真的被识别帧重置」：重识别后的下一帧必须能重新跳帧（哪怕它
   相对最初那张图已漂过阈值），且串尾同一帧连喂两遍第二遍必静默 ——
   `remember`/`set_baseline` 里推进锚点那两行只要断一条，这里就红。

变异检验（`TestMutationControls` + `TestDriftGateSourceMutants`，随主守卫一起跑，也可单跑）：
    py -3.10 -X utf8 localtest/test_skip_frame_guard.py --mutate
  变异体是**正向对照**——把生产顺序换成坏顺序、把累计漂移门摘掉，断言它确实坏成
  ① / ③ / ⑤ 禁止的那个样子（跳帧帧照付识别 / 翻转不再自愈 / 漂移画面被当没变）。
  若哪天有人把 ① ③ ⑤ 削弱成空断言，这些会与主守卫互相矛盾而报红。

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
import numpy as np

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


class RectSpaceProbe:
    """记录手牌通道**产出**的框坐标系：包住 `Engine._hand_channel_rows`。

    为什么包通道而不是包 `detect_all_rows`：通道干了两件事 —— 把底部条带喂给
    格网、再把条带坐标加回 y0 还原成整屏坐标。包在 `detect_all_rows` 上只看得见
    第一件事的产物（那本来就是条带坐标），拿它去断言「坐标回整屏」是量错了对象。

    ⚠ 记账层次：本探针包 Engine 实例的方法，`Tracer` 包 detector 实例的方法，两
    个不同的对象 → 与构造/进入顺序无关。曾经把两个探针都包在 `detect_all_rows`
    上、且在 `with` 之前就各自构造完，于是后装的 `self.orig` 抓到的是**原函数**而
    不是前一个 wrapper，嵌套被绕过 —— 表现是 `Tracer.calls` 恒 0、探针却记录到 26
    个框，把一个健康的引擎伪装成「识别凭空消失」。同帧两个探针必须分层次或后构造。
    """

    def __init__(self, eng) -> None:
        self.eng = eng
        self.orig = eng._hand_channel_rows
        self.max_y = -1
        self.n_rects = 0
        self.in_heights = []

    def __enter__(self) -> "RectSpaceProbe":
        orig = self.orig

        def probed(det, image, *a, **kw):
            self.in_heights.append(int(image.shape[0]))
            rows = orig(det, image, *a, **kw)
            for r in rows:
                for d in r:
                    self.max_y = max(self.max_y, int(d[0][1]))
                    self.n_rects += 1
            return rows

        self.eng._hand_channel_rows = probed
        return self

    def __exit__(self, *exc) -> None:
        self.eng._hand_channel_rows = self.orig


class FrameLog:
    """一帧的账：是否跳帧、通道调用次数、通道输入高度、方向锁、帧差、手牌串。"""

    def __init__(self, name, eng, img, full_h) -> None:
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            det = eng.get_hand_detector()
            if det is None:
                raise unittest.SkipTest("手牌通道没有独立 detector（平台未挂 bank？）")
            with Tracer(det) as tr, RectSpaceProbe(eng) as rp:
                res = eng.process(img)
        self.name = name
        self.ms = (time.perf_counter() - t0) * 1000.0
        self.calls = tr.calls
        self.heights = tr.heights
        self.max_rect_y = rp.max_y
        self.n_rects = rp.n_rects
        self.full_h = full_h
        self.payload = json.loads(res.result) if res is not None else {}
        self.payload.pop("elapsed", None)
        # 本帧手牌通道实际走了哪条路（full / strip / probe_reject / probe_skipped
        # + 两套格网各切了几格）。钱的账只能从引擎的 diag 上看，光看输入高度看不
        # 出「这次整屏是不是被条带顶掉了」。
        self.channel = dict((self.payload.get("diag") or {}).get("hand_channel")
                            or {})
        self.skipped = bool(self.payload.get("frame_skipped"))
        # 「非牌桌」也会返回 frame_skipped 结果（另一条早退路径），与本守卫的
        # 主体无关，但必须能区分开，否则会把牌桌校验的失败当成跳帧判定的失败。
        self.non_table = int(getattr(eng, "_non_table_frames", 0))
        self.orient = eng._orient
        self.diff = float(getattr(eng, "_cur_frame_diff", float("inf")))
        self.image = getattr(res, "image", None) if res is not None else None
        # 跳帧门里除帧差之外的其余门项（⑤ 要靠它们做因果归因：一帧没跳，到底是
        # 哪一项拦的）。
        # ⚠ 这些是 `process()` **返回之后**读的：跳帧帧在门处就早退了、状态没被动过，
        # 读到的等于门检时的值；被识别的那帧 `pending` 可能已被消费，故 ⑤ 只看
        # 前一帧（那帧必然被跳过）的值。
        self.pending = bool(eng._hand_stab.pending)
        self.match_started = bool(getattr(eng, "_match_started", False))
        self.stable_count = int(getattr(eng, "_stable_hand_count", 0))
        self.warmup_left = int(getattr(eng, "_warmup_left", 0))
        self.cached = eng._frame_skipper.cached is not None

    def norm_payload(self):
        p = dict(self.payload)
        d = p.get("diag")
        if isinstance(d, dict):
            d = dict(d)
            d.pop("perf", None)      # 分阶段耗时：只要跑过一帧就不同，非语义
            # 本帧方向验证耗时也是墙钟，两侧比不出语义；但它不能默默被剥掉：
            # 守卫只允许剥「时间」，手牌通道走了哪条路（hand_channel）必须照比。
            d.pop("orient_ms", None)
            p["diag"] = d
        # 局况视图里只有「当场几点」是天生对不上的：两个 Engine 实例各拿各的墙钟。
        # 只剥时间戳，phase/label/feed 文本/副露一览照旧比对 —— 剥到语义上去，
        # 这个守卫就只剩形式了。
        mp = p.get("match_phase")
        if isinstance(mp, dict):
            mp = dict(mp)
            mp.pop("updated_at_ms", None)
            mp["feed"] = [{k: v for k, v in f.items() if k != "at_ms"}
                          for f in (mp.get("feed") or []) if isinstance(f, dict)]
            p["match_phase"] = mp
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


def neutralize_drift_gate(eng):
    """变异体（**勿接回生产**）：摘掉累计漂移门 —— 退回改动前的语义。

    只把 `anchor_drift` 这个读数抹成 0.0，等价于「门里那一项被删掉了」，但状态一个
    不动（缓存/基线/锚点都还在）。必须用 `__class__` 就地换子类：`anchor_drift` 是一
    个 property（data descriptor），直接写实例属性 `eng._frame_skipper.anchor_drift = 0`
    会被它挡住，看起来装了变异体、实际什么都没改 —— 那又是一条在测空气的对照。
    """
    sk = eng._frame_skipper
    sk.__class__ = type("MutantSkipperNoDrift", (type(sk),),
                        {"anchor_drift": property(lambda self: 0.0)})


def warm_until_skip(eng, img, name, full_h, limit=24):
    """连续喂同一帧直到出现跳帧帧；返回跳帧开始前的日志列表。"""
    log = []
    for _ in range(limit):
        rec = FrameLog(name, eng, img, full_h)
        log.append(rec)
        if rec.skipped:
            return log
    return log


# ------------------------------------------------- ⑤ 慢漂移夹具（幅度自标定）
DRIFT_STEPS = 12                # 漂移帧数：够让累计量跨过阈值几次
DRIFT_TONE = 255.0              # 单调往纯白漂：不会来回抵消累计漂移
# 单步幅度占阈值的比例：上限必须 **远低于 1.0**（否则相邻门本帧就拦下了，测不到
# 慢漂移），下限又得让 12 帧能把累计量推到阈值的 3 倍以上。
DRIFT_STEP_FRAC = (0.10, 0.40)
DRIFT_STEP_AIM_FRAC = 0.35      # 单步目标：既留足「连跳几帧」的空隙，又靠实跨阈值

# 跳帧门里读累计漂移那一项（⑤ 的接线契约与变异体都拿它当对照物）
DRIFT_GATE_TERM = "and self._frame_skipper.anchor_drift < FRAME_SKIP_DIFF_THRESH"
ANCHOR_ADVANCE = "self._anchor_sig = self._cur_sig"


def _sig_of(eng, img):
    """与生产同一口径算一帧的块均值签名：`diff()` 吃的就是这张图。"""
    import engine.engine as E
    return E._block_diff_signature(eng._frame_diff_source(eng._apply_hand_roi(img)))


def _l1(a, b) -> float:
    if a is None or b is None or a.shape != b.shape:
        return float("inf")
    return float(np.abs(a - b).sum())


def _drift_threshold() -> float:
    import engine.engine as E
    return float(E.FRAME_SKIP_DIFF_THRESH)


def _patch_region(base, rect, alpha):
    y0, y1, x0, x1 = rect
    out = base.copy()
    patch = out[y0:y1, x0:x1].astype(np.float32)
    out[y0:y1, x0:x1] = np.clip(patch * (1.0 - alpha) + DRIFT_TONE * alpha,
                                 0.0, 255.0).astype(np.uint8)
    return out


def _drift_plan(eng, base):
    """标定「每帧只比上一帧动一点、连着看却明显变了」的补丁位置与幅度。

    必须实测标定：`_apply_hand_roi` 会把上半屏裁掉，`_frame_diff_source` 又会降
    采样，写死一个 rect/alpha 很容易调出「签名根本不变化」的假夹具。返回
    `(plan, tried)`：`plan` 是 `(rect, alpha, 单步差值)`，标定不出就返 None。

    取「最靠近目标单步」的那一组而不是「第一个落在窗口里」的：单步 0.10×阈值与
    0.40×阈值都算「窗口内」，前者 12 帧只漂到 1.2×阈值（一旦中间有一帧因其它原因
    重识别、锚点被重置，就再也跳不过去了），后者能漂到 4.8× —— 同一个断言，
    一个靠擦线一个不靠。
    """
    h, w = base.shape[:2]
    thr = _drift_threshold()
    sig0 = _sig_of(eng, base)
    tried = []
    for fy in (0.72, 0.86, 0.55, 0.30, 0.06, 0.95):
        y0 = max(0, min(h - 15, int(h * fy)))
        for (hh, ww) in ((10, 40), (6, 24), (14, 64)):
            rect = (y0, min(h, y0 + hh), 4, min(w, 4 + ww))
            for alpha in (0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.20, 0.30):
                step = _l1(sig0, _sig_of(eng, _patch_region(base, rect, alpha)))
                tried.append((rect, alpha, round(step, 3)))
    ok = [t for t in tried
          if DRIFT_STEP_FRAC[0] * thr <= t[2] <= DRIFT_STEP_FRAC[1] * thr]
    if not ok:
        return None, tried
    return min(ok, key=lambda t: abs(t[2] - DRIFT_STEP_AIM_FRAC * thr)), tried


def _drift_series(base, rect, alpha, n=DRIFT_STEPS):
    """第 k 帧 = 原图在 rect 上往纯白漂了 (k+1)*alpha。漂移量对 alpha 线性，
    所以每帧的**相邻**变化差不多大、**累计**变化却一路长。"""
    return [_patch_region(base, rect, alpha * (k + 1)) for k in range(n)]


def _run_drift(eng, base, series, name, full_h):
    """喂慢漂移帧，逐帧返回 `(本帧相对锚点的累计漂移, FrameLog)`。

    锚点自己跟着推（与生产同语义：只有一帧真被识别了，它才成为新锚点），否则
    「这一帧的门检到底是哪一项拦的」无法归因。签名在 `process()` 之前算，拿到的
    就是门检当时看到的量。起点锚 = `base`（调用方已拿 base 热身到静默）。
    """
    out = []
    anchor = _sig_of(eng, base)
    for i, f in enumerate(series):
        sig = _sig_of(eng, f)
        drift = _l1(sig, anchor)
        rec = FrameLog(f"{name}/drift#{i}", eng, f, full_h)
        if rec.calls >= 1:             # 真识别了 → 锚点换成本帧
            anchor = sig
        out.append((drift, rec))
    return out


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

    # ---- ② 非跳帧帧恰好一次、默认喂整屏、产出整屏坐标 -----------------------
    def test_recognizing_frames_pay_one_full_screen_detection(self):
        eng = quiet_engine()
        strip, y0 = eng._hand_strip(self.img0)
        strip_h = int(strip.shape[0])
        log = [FrameLog(self.name0, eng, self.img0, self.full_h) for _ in range(6)]
        kept = [r for r in log if not r.skipped]
        self.assertTrue(kept, "一帧都没真识别，断言无从谈起")
        for r in kept:
            self.assertEqual(r.calls, 1,
                             f"{r.name} 非跳帧却调用通道 {r.calls} 次（同帧付两遍钱）")
            # 输入必须是整屏。报障帧的「整屏被切坏」已由 `_hand_channel_rows` 的
            # 同帧反超接住（那边有守卫）；本守卫钉的是另一头：读数健康的帧不能
            # 回退成「默认喂条带」—— 那在 37 帧已标注 GT 上逐张从 444/444 掉到
            # 389/445，还给每帧多付一个完整识别。
            self.assertEqual(r.heights, [self.full_h],
                             f"{r.name} 的识别输入高度 {r.heights} != 整屏 "
                             f"{self.full_h}（条带 {strip_h}）：整屏读数健康时默认喂"
                             "条带就是拿全平台精度基线换一个个例机")
            self.assertEqual(r.channel.get("chosen"), "full",
                             f"{r.name} 本帧通道诊断 {r.channel}：读数健康却不该是「full」"
                             "以外的值")
            self.assertEqual(r.channel.get("strip_cells"), -1,
                             f"{r.name} 没低于地板线却去试了条带（多付一个完整识别）："
                             f"{r.channel}")
            if r.n_rects:
                # 产出必须是整屏坐标：忘了把 y0 加回去时，框会落在 0..strip_h 里，
                # 既过不了「牌行在画面下半部」，也让亮度校验读不到牌面。
                self.assertGreater(r.max_rect_y, strip_h,
                                   f"{r.name} 产出的框最大 y={r.max_rect_y} 没超过条带高 "
                                   f"{strip_h}：坐标没映射回整屏（y0={y0}）")

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
        strip_h = int(eng._hand_strip(self.img0)[0].shape[0])
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
        # 当成 bug。两次都必须走**同一个整屏入口**：反超探测得等方向定下来才付
        # （快检里就地探条带会把「另一个朝向可能才对」的信号提前花掉，实测从 2 次
        # 变 3 次；方向判据过了再用 `raw` 拿本帧已有的整屏读数补一次反超）。
        self.assertEqual(r.calls, 2, f"翻转帧的自愈调用次数应为 2（0°试探+180°命中），实为 {r.calls}")
        self.assertEqual(set(r.heights), {self.full_h},
                         f"翻转帧的识别输入不是整屏：{r.heights}"
                         f"（整屏 {self.full_h} / 条带 {strip_h}）：反超探测被提到了"
                         "方向判定之前，白付一个完整识别")
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

    # ---- ⑤ 缓慢漂移不得无限期复用旧 payload ---------------------------
    def test_slow_drift_cannot_keep_a_stale_payload(self):
        """每帧只比上一帧动一点、连着看已经明显变了：必须在若干帧内重识别。

        钉的是一条无条件不变量：**跳帧 ⇒ 相对锚点的累计漂移 < 阈值**。
        旧语义（只看相邻差）下这条必然被破：`diff()` 每帧都推基线，慢漂移可以永远
        过门，面板就永远停在第一帧的结论上（用户口中的「不更新了」）。
        """
        thr = _drift_threshold()
        eng = quiet_engine()
        warm_until_skip(eng, self.img0, self.name0, self.full_h)
        plan, tried = _drift_plan(eng, self.img0)
        self.assertIsNotNone(
            plan,
            f"标定不出「单步小幅但累计能跨阈值」的补丁幅度（试了 {len(tried)} 组，"
            f"最大累计才 {max([t[2] for t in tried]) * DRIFT_STEPS:.2f}）：⑤ 在测空气")
        rect, alpha, step = plan
        series = _drift_series(self.img0, rect, alpha)

        # 夹具诚实性：相邻差一直过门，累计量却把阈值跨了几次 —— 否则本测试拦的
        # 不是漂移门，只是又一条普通帧差门。
        sig_base = _sig_of(eng, self.img0)
        sigs = [_sig_of(eng, f) for f in series]
        adj = [_l1(sigs[0], sig_base)] + [_l1(sigs[i], sigs[i - 1])
                                          for i in range(1, len(sigs))]
        cum = [_l1(s, sig_base) for s in sigs]
        self.assertLess(max(adj), thr,
                        f"夹具的相邻帧差 {max(adj):.2f} 已经超阈值 {thr}：这不是慢漂移")
        self.assertGreaterEqual(cum[-1], 3.0 * thr,
                                f"累计只漂了 {cum[-1]:.2f}，没把阈值跨过去（擦线运气）")
        self.assertGreaterEqual(len([c for c in cum if c > cum[0]]), DRIFT_STEPS // 2,
                                "漂移不是单调的（往回飘会互相抵消，累计门量不到东西）")

        rows = _run_drift(eng, self.img0, series, self.name0, self.full_h)
        skipped = [(d, r) for d, r in rows if r.skipped]
        forced = [(i, d, r) for i, (d, r) in enumerate(rows) if r.calls >= 1]
        self.assertTrue(skipped, "整串慢漂移帧一个都没跳：那①的省钱前提在本夹具里不成立")
        self.assertGreaterEqual(len(skipped), 2,
                                "只跳过一帧就重识别：那和「按帧数封顶」没区别，量不出慢漂移")
        self.assertTrue(forced,
                        f"画面已经累计漂了 {cum[-1]:.2f}（阈值 {thr}）却没有一帧重识别："
                        "旧 payload 被当成事实无限期上屏")
        # 主体不变量：跳帧帧相对锚点的累计漂移必须小于阈值
        for d, r in skipped:
            self.assertLess(d, thr,
                            f"{r.name} 相对「上一次真识别过的那帧」已累计漂移 {d:.2f} "
                            f"≥ 阈值 {thr}，却仍复用旧 payload（慢漂移拿相邻差当「没变」）")

        # 因果归因：第一个被重识别的帧，其余门项全都是「允许跳」的形状，
        # 相邻差也照样过门 —— 唯一没过的那项只能是累计漂移。
        i, d0, r0 = forced[0]
        self.assertGreaterEqual(i, 1,
                                "第一帧漂移就被重识别：夹具没造出「相邻像、累计不像」")
        prev = rows[i - 1][1]
        self.assertTrue(prev.skipped,
                        f"#{i} 前一帧就付了完整识别（{prev.calls} 次）：锚点早就换过，"
                        "再拿它当漂移门的对照就是量错了对象")
        self.assertLess(r0.diff, thr,
                        f"#{i} 相邻差 {r0.diff:.2f} 本身就超阈值：那它是被帧差门拦的，"
                        "不是漂移门（本断言在测另一东西）")
        self.assertGreaterEqual(d0, thr,
                                f"#{i} 没跳，可累计漂移只有 {d0:.2f} < {thr}：那它也不是"
                                "被漂移门拦的，⑤ 的因果链断了")
        self.assertFalse(r0.non_table, f"#{i} 走的是「非牌桌」早退，不是漂移门")
        self.assertGreater(prev.stable_count, 0, "其余门项 `stable_count>0` 在前一帧就不过："
                                                 "那本帧不跳是这一项拦的，与漂移无关")
        self.assertTrue(prev.match_started, "同上：`_match_started` 在跳帧帧上是 False，"
                                           "本测试的因果归因无从谈起")
        self.assertFalse(prev.pending, "同上：稳定器本来就挂着待确认变化")
        self.assertLessEqual(prev.warmup_left, 0, "热身未完，本帧不跳与漂移门无关")
        self.assertTrue(prev.cached, "没有缓存 payload 就根本进不了跳帧分支")

        # 锚点确实被识别帧重置了：重识别之后的下一帧能重新跳帧，哪怕它相对最初
        # 那张 base 已经漂过阈值（锚点不跟着识别帧走的话，这一帧会永远漂在阈值外，
        # 方向自愈/重识别之后的静默期逐帧都要付一次完整识别）。
        conv = [(k, d, r) for k, (d, r) in enumerate(rows)
                if k > i and r.skipped and cum[k] >= thr]
        self.assertTrue(
            conv,
            f"重识别（#{i}）之后的漂移帧再没跳过，或都没漂过最初那张 base：锚点没被"
            f"识别帧推进（累计列 {['%.2f' % c for c in cum]}）")
        k, dk, rk = conv[0]
        self.assertLess(dk, thr,
                        f"#{k} 相对当时锚点只有 {dk:.2f} 却本不该跳：归因不成立")
        self.assertEqual(rk.calls, 0, f"#{k} 是跳帧帧却付了 {rk.calls} 次识别")

        # 再把串尾那一帧连喂两遍：第二遍必须静默（`remember` 的锚点推进在跑）。
        # 第一遍喂下去可能仍会重识别（相对当时锚点确实又漂了一段），那不是缺陷。
        tail = series[-1]
        first = FrameLog(f"{self.name0}/tail#0", eng, tail, self.full_h)
        again = FrameLog(f"{self.name0}/tail#1", eng, tail, self.full_h)
        self.assertTrue(again.skipped,
                        f"同一帧连喂两遍还在重识别（first calls={first.calls} "
                        f"adj={first.diff:.2f} / again calls={again.calls} "
                        f"adj={again.diff:.2f}）：识别帧没把锚点推进到自己")
        self.assertEqual(again.calls, 0, f"静默后的同一帧仍付了 {again.calls} 次识别")


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

    def test_mutant_never_verify_cannot_self_heal_a_flip(self):
        """变异体「延后了却从不补做验证」必须丢不了方向自愈。

        原来这条断言的是「识别退化成 hand_roi 裁片」：在 rows 只由整屏验证产出
        的旧语义下成立。手牌通道改成「统一入口（整屏为主，只在整屏被切坏时才
        同帧试条带反超）」后该差异消失了，继续抱着它不放就是把一条已经空转的
        对照当成安全带。现在钉两条真能区分的：
          (a) 不补验证 → 永远没人看一眼「本帧画面是不是转了」→ 方向锁不会翻；
          (b) 它的 rows 只能由 process 默认入口在**错朝向**上拿：实测整屏读数
              0 格（`channel.full_cells == 0`），低过地板线于是还得去试一条注定
              没用的条带（`strip_h` 出现在输入高度里）；生产同一帧整屏读出 13 格
              且一次条带都不试（见 ③）。两边付的次数相同（各 2 次），差的不是钱
              是「牌到底读到没有」—— 别把这条读成省钱对照。
        ③ 里「翻转后手牌数逐字相等」**故意不给变异对照**：实测
        （`build/_probe_mutflip.py`）错朝向的底部条带照样切出 13 格，而投票器 +
        多重集稳定器把输出钉在旧手牌上（count/mpsz 与生产逐字相同，只有 `_orient`
        差得很远）。拿不出对照的断言就该说自己没对照：那条只是「自愈后精度不跳」
        的兜底检查，不是「自愈真发生了」的证据；而稳定器把一次错读盖住 4+ 帧这件
        事本身，就是用户报的「弹窗滞后」的另一面。
        """
        eng = quiet_engine()
        strip_h = int(eng._hand_strip(self.img0)[0].shape[0])
        warm_until_skip(eng, self.img0, self.name0, self.full_h)
        self.assertEqual(eng._orient, 0, "热身结束应当锁在 0°，否则对照无意义")
        self.assertGreaterEqual(int(eng._stable_hand_count or 0), 13,
                                 f"热身手牌不足：{eng._stable_hand_count}")
        settle_never_verify(eng)
        flipped = cv2.rotate(self.img0, cv2.ROTATE_180)
        first = FrameLog("mut/180#0", eng, flipped, self.full_h)
        last = first
        for i in range(1, 4):
            last = FrameLog(f"mut/180#{i}", eng, flipped, self.full_h)
        self.assertEqual(
            eng._orient, 0,
            f"变异体居然自己修了方向（{eng._orient}）：那 ③ 的自愈就不是"
            "「被推后的验证真跑了」的功劳，而是在测另一东西")
        # (b) 的对照：没补验证 → rows 只能由默认入口在错朝向上拿，整屏 0 格。
        self.assertEqual(int(first.channel.get("full_cells", -1)), 0,
                         f"变异体在错朝向上整屏居然读出了 {first.channel}：那它就不"
                         "是靠「没人纠正方向」坏的，③ 的对照量错了对象")
        self.assertIn(strip_h, first.heights,
                      f"变异体整屏读 0 格却没去试条带：{first.heights}。那 ③ 的"
                      "「生产翻转帧不付条带探测」就没有对照：同一付两次钱的帧，"
                      "一侧读到 13 格、一侧读到 0 格")
        self.assertEqual(last.calls, 0,
                         "连喂 4 帧同一张错朝向图都没收敛回静默：那 ③ 的"
                         "「翻转后第二帧该跳」就没有对照了")

    def test_mutant_without_drift_gate_keeps_skipping_a_drifting_screen(self):
        """变异体「摘掉累计漂移门」必须让 ⑤ 的不变量成立不了（即复现老 bug）。

        装上它，同一串慢漂移帧应重新出现「画面已经漂多了、却还在播旧 payload」；
        若摘掉门它反而不发生了，说明 ⑤ 测的不是漂移（可能连基线都推错了）。
        """
        thr = _drift_threshold()
        eng = quiet_engine()
        warm_until_skip(eng, self.img0, self.name0, self.full_h)
        plan, tried = _drift_plan(eng, self.img0)
        self.assertIsNotNone(plan, f"标定不出漂移幅度（试了 {len(tried)} 组）：⑤ 的对照在测空气")
        rect, alpha, _step = plan
        series = _drift_series(self.img0, rect, alpha)
        neutralize_drift_gate(eng)
        self.assertEqual(float(eng._frame_skipper.anchor_drift), 0.0,
                         "变异体没装上（`anchor_drift` 还是真读数）：那 ⑤ 就没得对照")
        rows = _run_drift(eng, self.img0, series, self.name0, self.full_h)
        stale = [(d, r) for d, r in rows if r.skipped and d >= thr]
        self.assertTrue(
            stale,
            "摘掉漂移门后它仍然会因为累计漂移而重识别：那 ⑤ 拦的是一个不存在的缺陷"
            f"（门项写在了别处，或根本没读漂移）")
        for d, r in stale:
            self.assertLess(r.diff, thr,
                            f"{r.name} 相邻差 {r.diff:.2f} 本来就超阈值：那是帧差门的"
                            "事，不能拿来当漂移门的对照")
        print(f"[mutate] 无漂移门：{len(stale)} 帧累计漂移 ≥{thr} 仍复用旧 payload"
              f"（首帧 {stale[0][0]:.2f}，其中相邻差最大 {max(r.diff for _d, r in stale):.2f}）")


# ---------------------------------------------------- ⑤ 的接线契约（只查源码）
ENGINE_PY = os.path.join(REPO, "android", "app", "src", "main", "python",
                         "engine", "engine.py")
with open(ENGINE_PY, encoding="utf-8") as _fp:
    ENGINE_SRC = _fp.read()

# `set_baseline` 里必须同步刷新本帧签名（否则帧末尾 `remember` 会把锚点写回
# 旧朝向的图，③ 已实测过：方向自愈后的每一帧都退化成完整识别）
SET_BASELINE_SYNC = "self._cur_sig = sig"


def drift_gate_wired(src: str) -> bool:
    """跳帧门里是否真在读累计漂移（必须恰好一项：多了是重复门，少了是没接）。"""
    return src.count(DRIFT_GATE_TERM) == 1


def frame_cap_live(src: str) -> bool:
    """`MAX_SKIP_FRAMES` 是否还活在**代码**里（历史说明注释不算）。

    只排除 `#` 开头的整行注释：那个名字现在只作为「为什么不用按帧封顶」的历史
    说明存在；把它从注释里也一起禁掉，只会逼下一轮改动删掉这段有价值的缘由。
    """
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "MAX_SKIP_FRAMES" in line.split("#", 1)[0]:
            return True
    return False


class TestDriftGateContracts(unittest.TestCase):
    """累计漂移门的接线契约：这三处只要断一条，⑤ 就只是一段能跑但不保事的代码。"""

    def test_gate_reads_the_cumulative_drift_exactly_once(self):
        self.assertTrue(drift_gate_wired(ENGINE_SRC),
                        f"跳帧门里没在读累计漂移（或不止一处）：{DRIFT_GATE_TERM!r}")

    def test_recognized_frame_advances_the_anchor(self):
        """锚点必须被「真的识别过的那帧」推进：否则它只会越漂越远，静默期每帧都付钱。"""
        self.assertEqual(ENGINE_SRC.count(ANCHOR_ADVANCE), 1,
                         f"`remember` 里推进锚点的那行({ANCHOR_ADVANCE!r})不见了或不止一处")
        self.assertIn("if self._cur_sig is not None:", ENGINE_SRC,
                      "推进锚点不再要求签名新鲜：签名算不出那一帧会拿旧签名充新")
        self.assertIn(SET_BASELINE_SYNC, ENGINE_SRC,
                      "`set_baseline` 没同步 `_cur_sig`：帧末 `remember` 会把锚点写回"
                      "未归一那张图（实测 ③ 的翻转帧之后全部退化成完整识别）")

    def test_diff_leaves_signature_none_when_it_cannot_compute(self):
        self.assertIn("self._cur_sig = None", ENGINE_SRC,
                      "`diff()` 不再先清空本帧签名：算不出时会拿上一帧的签名当「没变」")

    def test_dead_frame_count_cap_stays_dead(self):
        """负契约：`MAX_SKIP_FRAMES` 这个「注释承诺却从没被读过」的死常量不许回来。

        它的坑不是「少了一个参数」而是「注释写着一件事、代码做的是另一件事」：
        按帧数封顶要么逼静止画面周期性重付一次完整识别，要么挡不住慢漂移。
        口径是「代码里不得出现」，历史说明注释不在射程内（见 `frame_cap_live`）。
        """
        self.assertFalse(frame_cap_live(ENGINE_SRC),
                         "MAX_SKIP_FRAMES 又回到了代码里：它曾只活在注释里，"
                         "从未参与任何判定")
        self.assertIn("MAX_SKIP_FRAMES", ENGINE_SRC,
                       "连历史说明注释也没了：下一个人会把同一个坑重新挖回来")


class TestDriftGateSourceMutants(unittest.TestCase):
    """契约层的变异对照：把门项/锚点推进拆掉，上面的契约必须报错。"""

    def test_mutant_gate_line_removed_breaks_the_contract(self):
        mutated = ENGINE_SRC.replace(DRIFT_GATE_TERM + "\n", "\n", 1)
        self.assertNotEqual(mutated, ENGINE_SRC, "变异无效：那行拆不掉（引擎形态已不读漂移）")
        self.assertFalse(drift_gate_wired(mutated),
                         "拆掉一行后门里仍在读漂移：契约在测别的东西")
        self.assertTrue(drift_gate_wired(ENGINE_SRC))

    def test_mutant_anchor_advance_removed_breaks_the_contract(self):
        mutated = ENGINE_SRC.replace(ANCHOR_ADVANCE + "\n", "\n", 1)
        self.assertNotEqual(mutated, ENGINE_SRC, "变异无效：锚点推进那行拆不掉")
        self.assertNotIn(ANCHOR_ADVANCE, mutated,
                         "拆掉一行后仍能在原文里找到它：契约定位不唯一")
        self.assertIn(ANCHOR_ADVANCE, ENGINE_SRC)

    def test_mutant_stale_cur_sig_in_set_baseline_breaks_the_contract(self):
        mutated = ENGINE_SRC.replace(SET_BASELINE_SYNC + "\n", "\n", 1)
        self.assertNotEqual(mutated, ENGINE_SRC, "变异无效：`set_baseline` 里那行拆不掉")
        self.assertNotIn(SET_BASELINE_SYNC, mutated)
        self.assertIn(SET_BASELINE_SYNC, ENGINE_SRC)

    def test_mutant_reintroducing_a_frame_cap_breaks_the_negative_contract(self):
        """负契约也要有对照：把死常量接回代码，检查器必须括住。"""
        anchor = DRIFT_GATE_TERM
        self.assertEqual(ENGINE_SRC.count(anchor), 1, "契约定位不唯一")
        mutated = ENGINE_SRC.replace(anchor, "MAX_SKIP_FRAMES < 2 " + anchor, 1)
        self.assertTrue(frame_cap_live(mutated),
                        "检查器拦不住旧常量回到代码里，负契约在测空气")
        self.assertFalse(frame_cap_live(ENGINE_SRC), "现行源码已经不干净了")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：三个行为变异体 + 四个源码变异体必须「如期坏掉」")
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for tc in (TestMutationControls, TestDriftGateSourceMutants):
            suite.addTests(loader.loadTestsFromTestCase(tc))
        unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        unittest.main()
