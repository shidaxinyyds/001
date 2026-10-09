# -*- coding: utf-8 -*-
"""手牌条带通道 + 防空窗三件套的接线守卫（本轮真机报障的四项修复）。

被钉住的都是「改回旧写法不会有任何精度测试变红」的行为 —— 它们的产物是
「少读几张牌」「多等两秒」「把对的东西清掉」，只有直接对判据做断言才看得见：

① 手牌通道的主路径是**整屏**，底部条带只能**同帧反超**：格数严格多于整屏、
   峰值置信不低于整屏、且不被牌形异常判负，三者同时成立才顶掉整屏读数。
   「优先喂条带」是本轮试过并**被实测推翻**的写法：格网通道按图像几何铺等距格网，
   报障三帧（2000x899）确实是条带更好（整屏 4 格@0.778 → 条带 8 格@0.805），但同一
   分辨率的 37 帧**已标注** GT 语料上方向完全相反 —— 整屏逐张 444/444（100%），
   条带 389/445（82.5%）且普遍少 1~5 格（`build/_ab_channel_gt.py`）。把它当默认
   就是拿全平台精度基线换一个机型上的个例（踩过：eval_base 手牌 36/37 掉到 26/37）。
   报障素材已固化进 localtest/shots/report_2026_10_*.jpg。

② 严禁零检测帧：整屏每帧必跑；条带试了没赢仍交整屏读数；`allow_probe=False`
   （方向快检的 180° 那一路）也只跑一次整屏。踩过：`allow_full=False` 与「条带已被
   停用」组合出 `if not allow_full: return []`，于是停用后每帧一个格都不读，空 rows
   直接进下游 —— 表现是「识别突然中断」。

③ 探测条带是要付钱的（一次完整识别）：地板线 `HAND_CHANNEL_PROBE_FLOOR` 把它限在
   「整屏已经被切坏」的帧上（GT 语料整屏最低 10 格 > 8 → 正常帧一帧都不多付），
   排障开关 `hand_channel_ab` 才每帧付，且它**只加钱和日志、不改采信规则**。
   这个开关必须真能被 `set_config` 拨到（它得列进 `_cfg` 默认值表）—— `set_config`
   对未知 key 静默忽略，漏登记的开关不报错、不红，只是永远拨不动。

④ 4 方向全探测的熔断必须接在慢路径上（延迟长尾的元凶）。旧写法只把
   `MAX_ORIENT_REPROBES` 接在「连续 0 牌自愈」，弹窗盖住手牌行的每一帧都无条件
   重探一次，单帧 ~1.2s 变 ~5-8s。

⑤ `_face_brightness` 越界返回 None 而不是 0.0（幻影牌的入口）。旧写法让
   「图里放不下这个框」与「牌面全黑」同值，行级自适应据此判 `use_brightness=False`
   —— 防伪闸门被整体关掉，头像/副露区的假牌就是这么放进来的。

⑥ 空手硬重置 fuse：阈值 3→`EMPTY_HAND_RESET_FRAMES`(5) + warmup 期不重置 +
   瞬态阻尼必须覆盖「读到 0 张」。旧写法是自己把自己撞死：3 帧空 ≈ 3.6~7.5s，
   硬重置再叠 warmup 重建 = 面板空 3~6 秒，就是用户报的「等待牌局开始」误报。

   实测分层（本文件 `DampingCase` 的读数，别把两层当成一层）：连续读空时
   前 3 帧由 `_HandStabilizer` 的空帧宽限撑（`_empty_frames >= 4` 才重置），第 4 帧
   只能由瞬态阻尼撑，第 5 帧 fuse 硬重置 → 面板改口「等待牌局开始」。所以阻尼那一格
   坏掉不会少 4 帧的缓冲、只会少 1 帧：看上去很轻，但它恰恰是「换牌弹窗盖住手牌行」
   长度内最后一次改口机会，而且旧写法里它是唯一被 `curr_raw_n > 0` 漏掉的那一帧。

⑦ 调试页「严格识别门槛」开关得真的能降门槛。旧写法把这条判据写在补漏分支**之后**，
   条件（conf >= RELAX 且已建稳定手牌）又被补漏分支完全包含 —— 开关拨到关与拨到开
   一字不差，配置页顶上那句「所有开关都真实下发给识别引擎」当场成假话；而它承诺的
   正是暗光台面上现场能拨的唯一召回逃生口（用户报「只识别一部分」时能用的东西）。
   修它的同时必须保证默认路径（开关开着）逐档与旧顺序同解 —— 见本类第一条穷举对照。

运行：
  py -3.10 -X utf8 localtest/test_hand_strip_channel_guard.py
  py -3.10 -X utf8 localtest/test_hand_strip_channel_guard.py --mutate   #  变异对照
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import engine.engine as engine_mod  # noqa: E402
from engine.engine import (EMPTY_HAND_RESET_FRAMES, MAX_ORIENT_REPROBES,  # noqa: E402
                           Engine, _face_brightness)

MUTATE = "--mutate" in sys.argv

FULL_H, FULL_W = 720, 1280
PLATFORM = "tencent"
MODE = "std_tdh"
GOOD_LABELS = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
               "1p", "2p", "3p", "5p"]


def canvas(h=FULL_H, w=FULL_W, v=220):
    """一张「亮」的假图：亮度闸门不该参与本文件的断言，别让牌面太暗误伤。"""
    return np.full((h, w, 3), v, dtype=np.uint8)


def make_row(labels, image_h=FULL_H, y_frac=0.62):
    """按 `test_tile_ledger_e2e` 已验证可用的几何造一行牌（坐标落在给定图高的下半部）。"""
    th = max(12, int(image_h * 0.16))
    tw = max(7, int(th * 0.56))
    step = tw + 4
    return [((int(image_h * 0.01) + i * step, int(image_h * y_frac), tw, th),
             lab, 0.92) for i, lab in enumerate(labels)]


class ScriptedDetector:
    """假手牌通道识别器：按「被喂的是整屏还是条带」回两套不同的读数。

    真实格网通道对同一帧的两种喂法会切出不同格数、不同峰值（① 的实测根因：
    报障帧整屏 4 格@0.778 / 条带 8 格@0.805，GT 帧整屏 10~14 格@0.99+ / 条带普遍
    少格且低分），这里把两侧分开参数化：`cells/conf` 是整屏读数，`strip_cells/
    strip_conf` 是条带读数（不给就与整屏相同 —— 于是「反超」默认不成立）。
    判据是图高：比 `full_h` 矮就是条带（`_hand_strip` 只会交出更矮的图）。
    """

    def __init__(self, cells=None, conf=0.92, strip_cells=None, strip_conf=None,
                 tile_h=None, full_h=FULL_H):
        self.cells = GOOD_LABELS if cells is None else cells
        self.conf = conf
        self.strip_cells = strip_cells
        self.strip_conf = strip_conf
        self.tile_h = tile_h
        self.full_h = full_h
        self.heights = []

    def detect_all_rows(self, image, **_kw):
        h = int(image.shape[0])
        self.heights.append(h)
        is_strip = h < self.full_h
        cells = (self.strip_cells if (is_strip and self.strip_cells is not None)
                 else self.cells)
        conf = (self.strip_conf if (is_strip and self.strip_conf is not None)
                else self.conf)
        if not cells:
            return []
        row = make_row(cells, image_h=h)
        if self.tile_h:
            row = [((x, y, w, self.tile_h), l, c) for ((x, y, w, _h), l, c) in row]
        if conf != 0.92:
            row = [(d, l, conf) for (d, l, _c) in row]
        return [row]


def bare_engine(**cfg) -> Engine:
    """不起真检测器的 Engine：只用来单测通道与判据本身。"""
    eng = Engine()
    eng.platform = PLATFORM
    eng._strip_disabled = False
    for k, v in cfg.items():
        eng._cfg[k] = v
    return eng


def channel_case(eng, det, image=None, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        rows = eng._hand_channel_rows(det,
                                      canvas() if image is None else image, **kw)
    return rows


class TestFullIsPrimary(unittest.TestCase):
    """① 主路径是整屏；条带只有「同帧反超」才许顶掉它。"""

    def test_healthy_full_read_is_used_verbatim_and_costs_no_probe(self):
        """整屏读数健康（>= 地板线）：一次检测、原样采用、不去试条带。

        这条就是「GT 语料基线不许被条带拖掉」的执行面：逐张 444/444 的读数与
        耗时都取决于它 —— 「优先喂条带」一回来这里立刻红。
        """
        eng = bare_engine()
        det = ScriptedDetector()
        rows = channel_case(eng, det, canvas())
        self.assertEqual(det.heights, [FULL_H],
                         f"整屏健康却还去探条带（付了 {det.heights}）：每帧多一个完整识别")
        self.assertEqual(eng._hand_channel_diag["chosen"], "full")
        self.assertEqual([d[1] for d in rows[0]], GOOD_LABELS,
                         "整屏读数被改道了：GT 语料上条带是净损失（389/445 vs 444/444）")

    def test_unprobed_strip_is_minus_one_not_zero(self):
        """「没试条带」与「试了 0 格」在诊断里必须分开，否则排障会指错方向。"""
        eng = bare_engine()
        channel_case(eng, ScriptedDetector(), canvas())
        self.assertEqual(eng._hand_channel_diag["strip_cells"], -1)

    def test_strip_wins_only_by_strict_comeback(self):
        """整屏被切坏（4 格）+ 条带反超（8 格、峰值不低）→ 采信条带，坐标回整屏。"""
        eng = bare_engine()
        det = ScriptedDetector(cells=GOOD_LABELS[:4], strip_cells=GOOD_LABELS[:8])
        img = canvas()
        strip, y0 = eng._hand_strip(img)
        self.assertGreater(y0, 0, f"{PLATFORM} 没配 hand_roi：本用例失去意义")
        rows = channel_case(eng, det, img)
        self.assertEqual(det.heights, [FULL_H, int(strip.shape[0])],
                         "整屏被切成 4 格却没去试条带：报障帧的缺张就在这个分支里")
        self.assertEqual(eng._hand_channel_diag["chosen"], "strip")
        got = rows[0]
        want = make_row(GOOD_LABELS[:8], image_h=int(strip.shape[0]))
        self.assertEqual([g[1] for g in got], GOOD_LABELS[:8], "采信后标签丢了")
        self.assertEqual([g[0][1] for g in got], [w[0][1] + y0 for w in want],
                         "rows 没映射回整屏坐标系：亮度校验与「牌行在下半部」会静默失效")
        self.assertEqual([g[0][0] for g in got], [w[0][0] for w in want],
                         "只许在 y 上加回条带偏移，x 不该被改动")
        self.assertGreater(max(g[0][1] for g in got), int(strip.shape[0]),
                           "框还留在条带空间：同一个 payload 字段会有两种坐标系")

    def test_tie_in_cells_keeps_the_whole_screen(self):
        """格数打平时不许改口（GT 语料上条带普遍与整屏同格却少命中）。"""
        eng = bare_engine()
        det = ScriptedDetector(cells=GOOD_LABELS[:5],
                               strip_cells=GOOD_LABELS[:5], strip_conf=0.99)
        rows = channel_case(eng, det, canvas())
        self.assertEqual(eng._hand_channel_diag["chosen"], "probe_reject",
                         "条带一格没多却顶掉了整屏：反超判据丢了「严格多于」")
        self.assertEqual([d[1] for d in rows[0]], GOOD_LABELS[:5])

    def test_more_cells_with_lower_conf_is_not_adopted(self):
        """条带多切一格却把峰值压低 = 幻影牌（实测 GT t5 与报障帧 C 同形）。"""
        eng = bare_engine()
        det = ScriptedDetector(cells=GOOD_LABELS[:5], conf=0.99,
                               strip_cells=GOOD_LABELS[:6], strip_conf=0.80)
        rows = channel_case(eng, det, canvas())
        self.assertEqual(eng._hand_channel_diag["chosen"], "probe_reject",
                         "只比格数不比置信：多出来那一格是臆测出来的（严禁错判）")
        self.assertEqual([d[1] for d in rows[0]], GOOD_LABELS[:5])

    def test_lying_down_strip_row_is_not_adopted(self):
        """条带读数牌形躺倒（w/h >= 1.05）时，格数再多也不采信。"""
        eng = bare_engine()
        det = ScriptedDetector(cells=GOOD_LABELS[:4], strip_cells=GOOD_LABELS,
                               tile_h=18)          # 宽 19 / 高 18 → 整行算「躺下」
        channel_case(eng, det, canvas())
        self.assertEqual(eng._hand_channel_diag["chosen"], "probe_reject",
                         "牌形闸门对手牌通道隐形：转了 90° 的条带会被当真手牌上报")

    def test_zero_cell_full_is_rescued_by_strip(self):
        """整屏一个格都没读到（0 格）时条带必须接住：这是缺张最重的形态。"""
        eng = bare_engine()
        det = ScriptedDetector(cells=[], strip_cells=GOOD_LABELS)
        rows = channel_case(eng, det, canvas())
        self.assertEqual(eng._hand_channel_diag["chosen"], "strip")
        self.assertTrue(rows, "整屏读空就交空 rows：面板只能停在旧读数上")
        self.assertEqual(eng._hand_channel_diag["full_cells"], 0,
                         "0 格与「未测」混淆（-1）就再看不出来是哪一路坏了")


class TestNeverZeroDetection(unittest.TestCase):
    """② 严禁零检测帧：任何分支组合都至少读一次整屏。"""

    def test_probe_rejection_still_returns_the_full_rows(self):
        eng = bare_engine()
        det = ScriptedDetector(cells=GOOD_LABELS[:4], strip_cells=GOOD_LABELS[:2])
        rows = channel_case(eng, det, canvas())
        self.assertEqual(eng._hand_channel_diag["chosen"], "probe_reject")
        self.assertEqual([d[1] for d in rows[0]], GOOD_LABELS[:4],
                         "条带没反超就把整屏的 4 格也丢了：宁可少几格也不能读空")

    def test_fast_check_without_probe_still_reads_and_pays_once(self):
        """`allow_probe=False`（180° 快检）：只跑整屏一次，仍交出读数。"""
        eng = bare_engine()
        det = ScriptedDetector(cells=GOOD_LABELS[:4])
        rows = channel_case(eng, det, canvas(), allow_probe=False)
        self.assertEqual(det.heights, [FULL_H],
                         "快检阶段就地探了条带：翻转帧要从 2 次检测变 4 次")
        self.assertEqual(eng._hand_channel_diag["chosen"], "probe_skipped",
                         "「本帧故意没试」与「试了不合格」混淆会把节流报成 ROI 配歪")
        self.assertTrue(rows, "不允探测就等于不读牌：这就是「识别突然中断」")

    def test_disabled_strip_probe_still_reads_the_whole_screen(self):
        """探测已停用 + 整屏退化 → 仍交整屏读数，且不再重复跑同一张图。"""
        eng = bare_engine()
        eng._strip_disabled = True
        det = ScriptedDetector(cells=GOOD_LABELS[:4])
        rows = channel_case(eng, det, canvas(), allow_probe=False)
        self.assertEqual(det.heights, [FULL_H],
                         f"停用后付了 {det.heights}：整屏是主路径，同一张图不该跑两遍")
        self.assertTrue(rows, "停用条带后一个格都没读：曾经造出零检测帧的那一格")

    def test_reading_keeps_working_over_consecutive_frames(self):
        """连续多帧都不许出现「零检测帧」——停用不是终点，整屏必须接得住。"""
        eng = bare_engine()
        eng._strip_disabled = True
        for _ in range(3):
            det = ScriptedDetector(cells=GOOD_LABELS[:4])
            rows = channel_case(eng, det, canvas(), allow_probe=False)
            self.assertEqual(det.heights, [FULL_H])
            self.assertTrue(rows)


class TestProbeCostAndABSwitch(unittest.TestCase):
    """③ 探测的钱：默认只花在整屏被切坏的帧上；开关只加钱不改采信。"""

    def test_healthy_frame_pays_exactly_one_detection(self):
        eng = bare_engine()
        det = ScriptedDetector()
        channel_case(eng, det, canvas())
        self.assertEqual(len(det.heights), 1,
                         f"默认配置付了 {len(det.heights)} 次检测：延迟直接翻倍")

    def test_degenerate_frame_pays_two_and_no_more(self):
        """反超要付钱，但也就付这一次（整屏 + 条带）。

        旧写法在采信条带后又为了对照跑一遍整屏 = 三次；新语义下整屏本来就排在
        第一位，两套格数天然是手上已有的数，日志不该再要钱。
        """
        eng = bare_engine()
        det = ScriptedDetector(cells=GOOD_LABELS[:4], strip_cells=GOOD_LABELS[:8])
        strip, _y0 = eng._hand_strip(canvas())
        channel_case(eng, det, canvas())
        self.assertEqual(det.heights, [FULL_H, int(strip.shape[0])],
                         f"一次反超付了 {det.heights}：对照不得再额外要一次检测")
        dg = eng._hand_channel_diag
        self.assertGreaterEqual(dg["full_cells"], 0)
        self.assertGreaterEqual(dg["strip_cells"], 0)

    def test_ab_switch_probes_every_frame_but_does_not_change_the_read(self):
        """开关的语义是「对照」不是「改道」：健康帧多付一次，读数仍是整屏。"""
        eng = bare_engine(hand_channel_ab=True)
        det = ScriptedDetector()
        rows = channel_case(eng, det, canvas())
        self.assertEqual(len(det.heights), 2, "开了对照却没试条带：诊断字段是假的")
        self.assertGreaterEqual(eng._hand_channel_diag["strip_cells"], 0)
        self.assertEqual([d[1] for d in rows[0]], GOOD_LABELS,
                         "对照把格数一样的条带顶掉了：那它就不是日志开关而是改道开关")

    def test_ab_switch_is_reachable_via_set_config(self):
        """开关必须真能被 `set_config` 拨到：否则「每帧对照」是永久不可达。

        `set_config` 对未知 key 静默忽略（它只认已列进 `self._cfg` 的），所以「写了
        一个 `self._cfg.get(...)` 却忘了登记默认值」的结局是：排障时拨了开关、引擎
        依旧不付钱、日志里永远是 `strip_cells=-1`，而**没有任何测试会红**。
        """
        eng = bare_engine()
        self.assertIn("hand_channel_ab", eng._cfg,
                      "开关没登记进 `_cfg`：`set_config` 会静默丢弃它")
        eng.set_config("hand_channel_ab", True)
        self.assertTrue(eng._cfg["hand_channel_ab"],
                        "`set_config` 拨不动这个开关：它只能停在默认值")
        det = ScriptedDetector()
        channel_case(eng, det, canvas())
        self.assertEqual(len(det.heights), 2,
                         "开关拨开了却没多跑一次条带：诊断字段给的是假数")


class ProbeFuseCase:
    """端到端最小引擎（**不** stub 通道）：停用这笔账只能拿真 `process` 测。

    判定在 process 里一帧一次（方向快检一帧会问两次通道，在通道里累加会把「用户
    转了一次手机」数成「连续两帧不合格」），单元级直调 `_hand_channel_rows` 看不见
    那本账。不 stub 通道就只能 stub 方向与牌河/副露（它们不是本文件的主题）。
    """

    IMG_H = 600                     # 喂 process 的图高；条带比它矮，据此数探测次数

    def boot(self, **cfg):
        engine_mod.load_platform = lambda *a, **k: PLATFORM
        engine_mod.load_mode = lambda *a, **k: MODE
        eng = Engine()
        for k, v in cfg.items():
            eng._cfg[k] = v
        det = ScriptedDetector(cells=GOOD_LABELS[:4], full_h=self.IMG_H)  # 整屏永远被切成 4 格
        eng.get_detector = lambda: det                                    # type: ignore
        eng.get_hand_detector = lambda: det                               # type: ignore
        eng._is_mahjong_table = lambda image: True                         # type: ignore
        eng._settle_orientation = lambda image: (image, False)             # type: ignore
        eng._run_bg_river_and_melds = lambda *a, **k: ([], [])             # type: ignore
        self.det = det
        self.eng = eng
        return eng

    def feed(self, n=1):
        """逐帧喂同一张图，返回每帧「付了几次多大图的检测」。"""
        costs = []
        for _ in range(n):
            self.eng._frame_skipper._last_sig = None   # 本帧画面「确有变化」（同 DampingCase）
            self.det.heights.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                self.eng.process(canvas(h=self.IMG_H, w=1100))
            costs.append(list(self.det.heights))
        return costs

    def probes(self, cost):
        """本帧为兜底付了几次条带检测（图高比整屏矮的那些）。"""
        return sum(1 for h in cost if h < self.IMG_H)


class TestProbeDisableFuseInProcess(ProbeFuseCase, unittest.TestCase):
    """③ 停用的账：连续「试了没反超」才停，停用后整屏必须接得住。"""

    def test_rejects_are_counted_once_per_frame(self):
        self.boot()
        for i, cost in enumerate(self.feed(Engine.HAND_STRIP_MAX_REJECTS)):
            self.assertEqual(self.probes(cost), 1,
                             f"第 {i + 1} 帧的探测记录是 {cost}：整屏被切成 4 格却没启动兜底")
        self.assertTrue(self.eng._strip_disabled,
                        "连续 3 帧试了没反超却不停用：每帧多付一个注定无用的检测")

    def test_after_disable_the_full_screen_still_reads(self):
        self.boot()
        costs = self.feed(Engine.HAND_STRIP_MAX_REJECTS + 2)
        for cost in costs[-2:]:
            self.assertEqual(self.probes(cost), 0, f"停用后还在付钱：{cost}")
            self.assertIn(self.IMG_H, cost, "停用后连整屏也不读了：零检测帧")
        self.assertEqual(self.eng._strip_reject_streak, Engine.HAND_STRIP_MAX_REJECTS,
                         "停用后账还在累加：同一个结论每帧重记一次不是诊断")

    def test_healthy_frames_never_accumulate_rejects(self):
        """整屏健康（GT 语料那一侧）时：不探条带、不累账、不停用。"""
        self.boot()
        self.det.cells = GOOD_LABELS                 # 13 格 >= 地板线
        for cost in self.feed(Engine.HAND_STRIP_MAX_REJECTS + 2):
            self.assertEqual(self.probes(cost), 0, f"正常帧多付了探测：{cost}")
        self.assertEqual(self.eng._strip_reject_streak, 0)
        self.assertFalse(self.eng._strip_disabled)

    def test_ab_switch_is_not_handicapped_by_the_fuse(self):
        """开着每帧对照时绝不触发停用：拨开了却只给前 3 帧 = 日志悄悄断供。"""
        self.boot(hand_channel_ab=True)
        for _ in range(Engine.HAND_STRIP_MAX_REJECTS + 2):
            cost = self.feed(1)[0]
            self.assertGreaterEqual(self.probes(cost), 1,
                                    f"对照被熔断掐掉了（{cost}）：排障拿到的是假样本")
        self.assertFalse(self.eng._strip_disabled,
                         "对照开关把探测永久关掉：下一句「怎么没日志」只能重启进程才会好")


class TestOrientReprobeFuse(unittest.TestCase):
    """④ 4 方向全探测的熔断必须接在慢路径上，且熔断后仍有 rows 产出。"""

    def engine_for_no_hand(self):
        eng = bare_engine()
        eng._orient = 0
        det = ScriptedDetector(cells=[])          # 两个朝向都读不出牌行 → 走慢路径
        eng.get_hand_detector = lambda: det        # type: ignore
        probes = []

        def fake_probe(image):
            probes.append(1)
            return 0, image

        eng._probe_orientation = fake_probe        # type: ignore
        return eng, det, probes

    def test_full_probe_is_limited_after_the_lock(self):
        eng, _det, probes = self.engine_for_no_hand()
        for _ in range(MAX_ORIENT_REPROBES + 4):
            with contextlib.redirect_stdout(io.StringIO()):
                eng._verify_orientation(canvas())
        self.assertEqual(len(probes), MAX_ORIENT_REPROBES,
                         f"全探测跑了 {len(probes)} 次（上限 {MAX_ORIENT_REPROBES}）："
                         "熔断没接在慢路径上，弹窗帧每帧都要 5-8 秒")
        self.assertTrue(eng._orient_probe_skipped, "熔断了却在 diag 里不可见")

    def test_fused_frame_still_produces_rows(self):
        eng, det, _probes = self.engine_for_no_hand()
        for _ in range(MAX_ORIENT_REPROBES + 1):
            with contextlib.redirect_stdout(io.StringIO()):
                eng._verify_orientation(canvas())
        # 熔断后本帧不许再全探测，但仍要给下游一份 rows。停用条带是为了让
        # 快检也跑整屏（否则本帧 0° 快检就走条带了，测不到兜底那一步）。
        eng._strip_disabled = True
        det.cells = GOOD_LABELS
        det.heights.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            eng._verify_orientation(canvas())
        self.assertEqual(det.heights, [FULL_H],
                         "熔断帧一个格都没读：面板停在旧读数而理由全是隐形的")
        self.assertTrue(eng._cached_rows, "熔断了却不缓存 rows：下游拿不到任何东西")

    def test_a_confirmed_frame_resets_the_counter(self):
        """成功必须把重探计数归零，否则熔断会变成「总共只允许多两次」。"""
        eng, det, probes = self.engine_for_no_hand()
        for _ in range(MAX_ORIENT_REPROBES):
            with contextlib.redirect_stdout(io.StringIO()):
                eng._verify_orientation(canvas())
        self.assertEqual(eng._orient_reprobe_count, MAX_ORIENT_REPROBES)
        det.cells = GOOD_LABELS                 # 本帧 0° 快检过关 → 方向被证实
        with contextlib.redirect_stdout(io.StringIO()):
            eng._verify_orientation(canvas())
        self.assertEqual(eng._orient_reprobe_count, 0,
                         "证实了方向还不归零：用户真把手机转过来时救不回来")
        before = len(probes)
        det.cells = []
        for _ in range(3):
            with contextlib.redirect_stdout(io.StringIO()):
                eng._verify_orientation(canvas())
        self.assertLessEqual(len(probes) - before, MAX_ORIENT_REPROBES,
                             "归零后重探额度没恢复，或熔断变成每帧都付")


class TestFaceBrightnessNoneIsNotBlack(unittest.TestCase):
    """⑤ 越界 = 「没有意见」，绝不是「牌面全黑」。"""

    def test_out_of_bounds_rect_returns_none(self):
        big = make_row(GOOD_LABELS, image_h=FULL_H)[0][0]     # 整屏坐标
        small = canvas(h=200, w=FULL_W)                       # 更小的工作图
        self.assertIsNone(_face_brightness(small, big),
                          "越界读出 0.0 就与真黑牌面同值：防伪闸门会被整体关掉")

    def test_genuinely_black_face_still_reads_zero(self):
        img = np.zeros((FULL_H, FULL_W, 3), dtype=np.uint8)   # 真·全黑
        rect = make_row(GOOD_LABELS, image_h=FULL_H)[0][0]
        got = _face_brightness(img, rect)
        self.assertIsNotNone(got, "在范围内也必须给出读数，否则黑牌骗得过闸门")
        self.assertLess(got, engine_mod.MIN_FACE_BRIGHTNESS)

    def test_in_bounds_face_reads_the_real_value(self):
        rect = make_row(GOOD_LABELS, image_h=FULL_H)[0][0]
        got = _face_brightness(canvas(), rect)
        self.assertIsNotNone(got)
        self.assertGreaterEqual(got, engine_mod.MIN_FACE_BRIGHTNESS,
                                "亮图读成暗牌：整行牌会被防伪闸门误杀")


class TestRowShapeAnomaly(unittest.TestCase):
    """倒置/躺倒的独立信号，且对等距格网零误触。"""

    def test_grid_like_row_never_trips(self):
        row = make_row(GOOD_LABELS, image_h=FULL_H)
        self.assertFalse(Engine._row_shape_anomaly(row),
                         "网格同行框等高等宽却被判异常：主路径方向快检会被整体推翻")

    def test_lying_down_row_trips(self):
        row = [((x, 400, 90, 40), lb, 0.9)
               for x, lb in zip(range(0, 900, 95), GOOD_LABELS)]
        self.assertTrue(Engine._row_shape_anomaly(row),
                        "整行牌躺下（w>h）却没被判异常：90°/270° 仍是无人看的盲区")

    def test_mixed_height_row_trips(self):
        row = [((x, 400, 40, 30), lb, 0.9)
               for x, lb in zip(range(0, 700, 95), GOOD_LABELS[:8])]
        row += [((x, 400, 40, 95), lb, 0.9)
                for x, lb in zip(range(760, 950, 95), GOOD_LABELS[8:])]
        self.assertTrue(Engine._row_shape_anomaly(row))

    def test_short_row_is_not_judged(self):
        row = [((x, 400, 90, 40), lb, 0.9)
               for x, lb in zip(range(0, 300, 95), GOOD_LABELS[:3])]
        self.assertFalse(Engine._row_shape_anomaly(row),
                         "副露/短行牌数不足以判形，误伤会把正常碰牌帧当倒置")


CONF_RECT = (10, 20, 60, 100)     # aspect=0.6 落在合法区间里：只测门槛，不测牌形


class TestStrictGateSwitchIsLive(unittest.TestCase):
    """⑦ 严格门槛开关真能降门槛，而默认路径逐字没动。"""

    @staticmethod
    def _legacy(conf, stable, is_grid=False):
        """改动前的门槛顺序（逐字照搬旧实现）。"""
        if is_grid and conf >= 0.38:
            return True
        if conf >= engine_mod.ENGINE_MIN_CONF:
            return True
        if conf >= engine_mod.ENGINE_MIN_CONF_RELAX and stable:
            return True
        return False

    def test_default_path_is_tile_for_tile_the_legacy_chain(self):
        """开关开着（=每个用户的默认）：逐档 conf × 有无稳定手牌 × 网格都得同解。"""
        eng = bare_engine()
        for conf in (0.30, 0.379, 0.38, 0.399, 0.419, 0.42, 0.45, 0.499, 0.50, 0.55, 0.95):
            for stable in ("", "123m"):
                for is_grid in (False, True):
                    eng._stable_hand_mpsz = stable
                    got = eng._apply_conf(CONF_RECT, "5m", conf, is_grid=is_grid) is not None
                    self.assertEqual(self._legacy(conf, stable, is_grid), got,
                                     "默认门槛顺序被改了（conf=%s stable=%s grid=%s）——"
                                     "这一档影响全平台读数，不许顺手改"
                                     % (conf, bool(stable), is_grid))

    def test_switch_off_lowers_the_gate_without_a_stable_hand(self):
        eng = bare_engine(strict=False)
        eng._stable_hand_mpsz = ""
        self.assertEqual("5m", eng._apply_conf(CONF_RECT, "5m", 0.45),
                         "开关拨到关却毫无反应：那条被补漏分支包住的死分支回来了")

    def test_switch_off_still_keeps_the_relax_floor(self):
        eng = bare_engine(strict=False)
        eng._stable_hand_mpsz = ""
        self.assertIsNone(
            eng._apply_conf(CONF_RECT, "5m", engine_mod.ENGINE_MIN_CONF_RELAX - 0.01),
            "关掉严格门槛不等于没有门槛：RELAX 以下照样得拒（否则白板刷屏）")

    def test_switch_off_does_not_disable_the_shape_gate(self):
        eng = bare_engine(strict=False)
        eng._stable_hand_mpsz = "123m"
        self.assertIsNone(eng._apply_conf((10, 20, 200, 100), "5m", 0.99),
                          "降门槛不该顺带关掉牌形校验：躺着的框不是手牌")

    def test_rescue_channel_still_works_with_the_switch_on(self):
        eng = bare_engine()
        eng._stable_hand_mpsz = "123m"
        self.assertEqual("5m",
                         eng._apply_conf(CONF_RECT, "5m", engine_mod.ENGINE_MIN_CONF_RELAX),
                         "补漏通道被删了：把 RELAX 当「数值越大越松」抬到 ≥0.50，"
                         "产物就是这一格红")

    def test_threshold_order_is_the_contract(self):
        self.assertGreater(engine_mod.ENGINE_MIN_CONF, engine_mod.ENGINE_MIN_CONF_RELAX,
                           "严格门槛 <= 补漏下限 ⇒ 补漏通道恒不成立（等于删掉它，"
                           "而不会有任何精度测试变红）")
        self.assertGreaterEqual(engine_mod.ENGINE_MIN_CONF_RELAX, engine_mod.BOOTSTRAP_CONF,
                                "补漏下限低于 bootstrap 门槛 ⇒ 冷启动比稳定期还严，死锁回来")


class TestEmptyHandFuse(unittest.TestCase):
    """⑥ 空手硬重置：阈值与 warmup 保护直接对判据断言。"""

    def setUp(self):
        self.eng = bare_engine()
        self.eng._match_started = True
        self.eng._warmup_left = 0

    def test_below_the_threshold_does_not_kill_the_match(self):
        self.eng._empty_hand_streak = EMPTY_HAND_RESET_FRAMES - 1
        self.assertFalse(self.eng._empty_hand_fuse(),
                         "3~4 帧空就硬重置：一帧 1.2~2.5s，等于自己造 3~6 秒空窗")

    def test_reaches_the_threshold_and_fires(self):
        self.eng._empty_hand_streak = EMPTY_HAND_RESET_FRAMES
        self.assertTrue(self.eng._empty_hand_fuse(),
                        "fuse 永远不响也一样糟：结算弹窗仍被判成牌桌时旧手牌会挂屏")

    def test_warmup_never_resets(self):
        self.eng._empty_hand_streak = EMPTY_HAND_RESET_FRAMES + 5
        self.eng._warmup_left = 2
        self.assertFalse(self.eng._empty_hand_fuse(),
                         "warmup 期拿自己的启动噪声当「局结束了」的证据")

    def test_before_the_match_starts_there_is_nothing_to_flush(self):
        self.eng._match_started = False
        self.eng._empty_hand_streak = EMPTY_HAND_RESET_FRAMES + 5
        self.assertFalse(self.eng._empty_hand_fuse())


class DampingCase:
    """端到端最小引擎：手牌行由脚本喂入，其余（投票/稳定器/状态机）全走生产代码。

    每帧把帧差基线清空（与 `test_tile_ledger_e2e.run_one` 同法）：这不是顺手方便，
    而是**必须有**的一步 —— 读空的那几帧画面本就“没变”（只有手牌行被弹窗盖住），
    会被跳帧闸门判成 `frame_skipped` 直接回放上一帧 payload；那条路也能让面板不改口，
    于是要测的阻尼分支一次都不会被执行，主断言与变异对照一起变成空转。把跳帧这一重
    保护先拆掉，剩下的「面板不改口」才只能由阻尼解释。
    """

    TRANSIENT_WINDOW = 3          # 与 process 里 `_transient_drop_streak <= 3` 同源
    #: 连续读空时面板不改口的实测帧数：前 3 帧由多重集稳定器撑（`_empty_frames` 宽限），
    #: 第 4 帧稳定器已不再撑、只能由瞬态阻尼撑。旧阻尼入口多加的 `curr_raw_n > 0`
    #: 把第 4 帧直接漏成 no_tiles（实测），所以这个数就是阻尼的唯一可观测面。
    HOLD_FRAMES = TRANSIENT_WINDOW + 1

    def setUp(self):
        engine_mod.load_platform = lambda *a, **k: PLATFORM
        engine_mod.load_mode = lambda *a, **k: MODE
        eng = Engine()
        det = ScriptedDetector()
        eng.get_detector = lambda: det                       # type: ignore
        eng.get_hand_detector = lambda: det                  # type: ignore
        eng._is_mahjong_table = lambda image: True            # type: ignore
        eng._settle_orientation = lambda image: (image, False)   # type: ignore
        eng._run_bg_river_and_melds = lambda *a, **k: ([], [])   # type: ignore
        self.labels = list(GOOD_LABELS)
        # 夹具产的是**整屏坐标**（与真实 `_hand_channel_rows` 同一语义），坐标跟随
        # 本帧实际图高：下游 `rows` 全按整屏坐标系吃（亮度校验、「牌行在下半部」）。
        eng._hand_channel_rows = self._stub_channel           # type: ignore
        self.n_feed = 0
        self.eng = eng

    def _stub_channel(self, det, image, **kw):
        if not self.labels:
            return []
        return [make_row(self.labels, image_h=int(image.shape[0]))]

    def feed(self, n=1):
        out = []
        for _ in range(n):
            self.eng._frame_skipper._last_sig = None   # 本帧画面「确有变化」
            with contextlib.redirect_stdout(io.StringIO()):
                res = self.eng.process(canvas(h=600, w=1100))
            out.append(json.loads(res.result) if res is not None else {})
        return out

    def warm_until_stable(self, limit=14):
        """喂到 warmup 结束且手牌已建立：否则后面测的是启动期而不是阻尼期。"""
        d = {}
        for _ in range(limit):
            d = self.feed(1)[-1]
            if (self.eng._warmup_left <= 0
                    and getattr(self.eng, "_match_started", False)
                    and int(d.get("count") or 0) == len(GOOD_LABELS)):
                return d
        self.fail(f"夹具建立不起 13 张稳定手牌（最后 {d.get('status')}/"
                  f"{d.get('count')}，warmup={self.eng._warmup_left}）："
                  "后面的断言都在测空气")


class TestTransientDampingCoversEmptyRead(DampingCase, unittest.TestCase):
    """⑥ 稳定手牌建立后连续读空：面板不许立刻改口，也不许永远挂着旧牌。"""

    def test_blank_frames_do_not_announce_waiting(self):
        """读空的前 HOLD_FRAMES 帧面板不改口，且最后那一帧只能由阻尼撑住。"""
        self.warm_until_stable()
        self.labels = []                        # 换牌弹窗整个盖住手牌行 → 读到 0 张
        for i in range(self.HOLD_FRAMES):
            d = self.feed(1)[0]
            st = str(d.get("status"))
            self.assertIn(
                st, ("ok", "partial"),
                f"对局中第 {i + 1} 帧读空就报「{st}」：瞬态阻尼把主触发情形"
                "(raw==0) 漏在门外，面板上就是「等待牌局开始/未检测到手牌」的闪烁")
            self.assertEqual(int(d.get("count") or 0), len(GOOD_LABELS),
                             "阻尼期该维持上一帧可信手牌数，既不是伪造也不是清空")
        # 最后一帧必须是在阻尼分支里产出的：否则上面四条绿灯全靠稳定器宽限撑着，
        # 阻尼坏了也测不出（踩过：稳定器的 `_empty_frames` 宽限比阻尼窗口还长）。
        self.assertGreater(self.eng._transient_drop_streak, 0,
                           f"连读 {self.HOLD_FRAMES} 帧空都没进过阻尼计数："
                           "阻尼分支在真链路上是死代码，上面的绿灯全是稳定器给的")

    def test_damping_is_bounded_not_forever(self):
        self.warm_until_stable()
        self.labels = []
        self.feed(self.HOLD_FRAMES)
        tail = [str(d.get("status")) for d in self.feed(5)]
        self.assertTrue(any(s in ("waiting", "no_tiles") for s in tail),
                        f"读空 {self.HOLD_FRAMES + 5} 帧还挂着旧手牌（{tail}）："
                        "阻尼变成了跨局残留")


class TestMutationControls(unittest.TestCase):
    """变异体必须让上面某条主断言成立不了；坏不起来的对照就是空转。"""

    def setUp(self):
        self.orig_brightness = engine_mod._face_brightness

    def tearDown(self):
        engine_mod._face_brightness = self.orig_brightness

    def test_mutant_strip_first_adopts_a_tie(self):
        """坏写法 A：优先喂条带且不比反超（本轮试过、被 GT 实测推翻的那一版）→ ① 红。

        它正是「报障帧治好、GT 语料挨枪」那一刀：两侧格数打平时它也会改口条带，
        于是 `test_healthy_full_read_is_used_verbatim_and_costs_no_probe` 与
        `test_tie_in_cells_keeps_the_whole_screen` 一起红（逐张 444/444 → 389/445）。
        """
        eng = bare_engine()

        def bad(det, image, allow_probe=True):
            strip, y0 = eng._hand_strip(image)
            sraw = det.detect_all_rows(strip, classify=True, allow_rotation=False)
            sc = max((len(r) for r in sraw), default=0)
            top = max([c for r in sraw for (_d, _l, c) in r], default=0.0)
            eng._hand_channel_diag = {"chosen": "strip" if sc else "full",
                                      "strip_cells": sc, "full_cells": -1,
                                      "strip_y0": y0}
            if sc >= Engine.HAND_STRIP_MIN_CELLS and top >= Engine.HAND_STRIP_MIN_CONF:
                return [[((d[0], d[1] + y0, d[2], d[3]), l, c)
                         for (d, l, c) in r] for r in sraw]
            return det.detect_all_rows(image, classify=True, allow_rotation=False)
        eng._hand_channel_rows = bad                         # type: ignore
        det = ScriptedDetector()                             # 两侧格数/峰值相同
        channel_case(eng, det, canvas())
        self.assertEqual(eng._hand_channel_diag["chosen"], "strip",
                         "坏写法在格数打平时也没改口：① 的反超判据在测空气")
        self.assertEqual(det.heights, [int(eng._hand_strip(canvas())[0].shape[0])],
                         "坏写法没把条带放在第一路：① 的断言在测空气")

    def test_mutant_no_probe_floor_pays_for_strip_every_frame(self):
        """坏写法 B：没有地板线（每帧都探条带）→ ③ 的成本不变式红。"""
        eng = bare_engine()
        eng.HAND_CHANNEL_PROBE_FLOOR = 10 ** 9               # 任意读数都算「被切坏」
        det = ScriptedDetector()                             # 整屏 13 格，本来就健康
        channel_case(eng, det, canvas())
        self.assertEqual(len(det.heights), 2,
                         "关掉地板线也只需一次检测：③ 的成本断言在测空气")

    def test_mutant_conf_gate_off_opens_the_door_to_phantom_tiles(self):
        """坏写法 C：只看格数不看峰值（条带多一格但分更低也信）→ ① 的幻影牌判据红。"""
        eng = bare_engine()

        def bad(det, image, allow_probe=True):
            raw = det.detect_all_rows(image, classify=True, allow_rotation=False)
            fc = max((len(r) for r in raw), default=0)
            strip, y0 = eng._hand_strip(image)
            sraw = det.detect_all_rows(strip, classify=True, allow_rotation=False)
            sc = max((len(r) for r in sraw), default=0)
            adopt = sc >= Engine.HAND_STRIP_MIN_CELLS and sc > fc      # 少了置信那一条
            eng._hand_channel_diag = {"chosen": "strip" if adopt else "probe_reject",
                                      "strip_cells": sc, "full_cells": fc, "strip_y0": y0}
            return raw
        eng._hand_channel_rows = bad                         # type: ignore
        det = ScriptedDetector(cells=GOOD_LABELS[:5], conf=0.99,
                               strip_cells=GOOD_LABELS[:6], strip_conf=0.80)
        channel_case(eng, det, canvas())
        self.assertEqual(eng._hand_channel_diag["chosen"], "strip",
                         "拆掉置信门后这一帧仍不采信条带：① 的置信判据在测空气")

    def test_mutant_zero_detection_breaks_the_no_blank_frame_invariant(self):
        """坏写法 D：探测没赢就交空 rows（造出零检测帧的那一版）→ ② 红。"""
        eng = bare_engine()
        orig = eng._hand_channel_rows

        def bad(det, image, allow_probe=True):
            if not allow_probe:
                eng._hand_channel_diag = {"chosen": "probe_skipped",
                                          "strip_cells": -1, "full_cells": -1,
                                          "strip_y0": 0}
                return []                                    # 一个格都不读
            return orig(det, image, allow_probe=allow_probe)
        eng._hand_channel_rows = bad                         # type: ignore
        det = ScriptedDetector(cells=GOOD_LABELS[:4])
        rows = channel_case(eng, det, canvas(), allow_probe=False)
        self.assertEqual((det.heights, rows), ([], []),
                         "变异体没造出零检测帧：② 的断言在测空气")

    def test_mutant_no_fuse_pays_every_frame(self):
        """坏写法 E：慢路径没有熔断（每帧无条件 4 方向全探测）→ ④ 红。"""
        eng = bare_engine()
        eng._orient = 0
        det = ScriptedDetector(cells=[])
        eng.get_hand_detector = lambda: det                  # type: ignore
        probes = []
        eng._probe_orientation = lambda image: (probes.append(1), (0, image))[1]
        orig_verify = eng._verify_orientation

        def bad(image):
            eng._orient_reprobe_count = 0     # 抹掉熔断的读数 = 没有熔断
            return orig_verify(image)
        eng._verify_orientation = bad                        # type: ignore
        for _ in range(MAX_ORIENT_REPROBES + 4):
            with contextlib.redirect_stdout(io.StringIO()):
                eng._verify_orientation(canvas())
        self.assertGreater(len(probes), MAX_ORIENT_REPROBES,
                           f"熔断关掉后只跑了 {len(probes)} 次全探测：④ 在测空气")

    def test_mutant_out_of_bounds_black_kills_the_gate(self):
        """坏写法 F：越界读回 0.0（与真黑牌面同值）→ ⑤ 红。"""
        def bad(image, rect):
            v = self.orig_brightness(image, rect)
            return 0.0 if v is None else v
        engine_mod._face_brightness = bad                    # type: ignore
        big = make_row(GOOD_LABELS, image_h=FULL_H)[0][0]
        v = engine_mod._face_brightness(canvas(h=200, w=FULL_W), big)
        self.assertEqual(v, 0.0, "越界仍返回 None：⑤ 的断言在测空气")
        self.assertLess(v, engine_mod.MIN_FACE_BRIGHTNESS,
                        "越界读出的 0.0 过不了亮度线，那就不是「关掉闸门」的坏写法")

    def test_mutant_three_frame_reset_breaks_the_window(self):
        """坏写法 G：阈值回到 3、warmup 不保护 → ⑥ 红。"""
        eng = bare_engine()
        eng._empty_hand_fuse = lambda: bool(                 # type: ignore
            getattr(eng, "_match_started", False)
            and getattr(eng, "_empty_hand_streak", 0) >= 3)
        eng._match_started = True
        eng._warmup_left = 0
        eng._empty_hand_streak = EMPTY_HAND_RESET_FRAMES - 1
        self.assertTrue(eng._empty_hand_fuse(),
                        "阈值回到 3 却没在 4 帧上响：⑥ 的阈值断言在测空气")
        eng._warmup_left = 2
        eng._empty_hand_streak = EMPTY_HAND_RESET_FRAMES + 5
        self.assertTrue(eng._empty_hand_fuse(),
                        "去掉 warmup 保护后 fuse 在 warmup 期仍然沉默：对照失效")

    def test_mutant_damping_gate_requires_nonzero_read(self):
        """坏写法 H：阻尼前置条件回到 `in_active_match and curr_raw_n > 0`。

        那一格写在 `process` 里、跳不过运行时 patch，所以这里用源码契约：
        主守卫拿真实 engine.py 验旧写法不在，本对照拿旧写法的原文验「检查器真的会报」。
        只断言“今天没坏”而不验“检查器能报坏”，与断言本身一起空转。
        """
        old = ('                in_active_match = getattr(self, "_match_started", False)'
               ' and bool(self._stable_hand_mpsz)\n'
               '                if in_active_match and curr_raw_n > 0:\n'
               '                    self._transient_drop_streak += 1\n')
        self.assertTrue(damping_gate_regressed(old),
                        "检查器拦不住旧写法："
                        "`test_damping_gate_does_not_require_nonzero_read` 在测空气")
        self.assertFalse(damping_gate_regressed(ENGINE_SRC),
                         "真实源码里阻尼又回到了「raw>0 才阻尼」：读空帧会直接掉进 no_tiles")

    def test_mutant_strict_branch_placed_after_rescue_is_a_dead_switch(self):
        """坏写法 J：把「严格开关」写回补漏分支之后 → 开关拨了没反应（⑦ 的红）。"""
        def legacy(self, rect, label, conf, bootstrap=False, is_grid=False):
            if label is None:
                return None
            w, h = rect[2], rect[3]
            if h <= 0:
                return None
            aspect = w / float(h)
            if aspect < engine_mod.MIN_TILE_ASPECT or aspect > engine_mod.MAX_TILE_ASPECT:
                return None
            if is_grid and conf >= 0.38:
                return label
            if conf >= engine_mod.ENGINE_MIN_CONF:
                return label
            if conf >= engine_mod.ENGINE_MIN_CONF_RELAX and self._stable_hand_mpsz:
                return label
            if bootstrap and self._cfg.get("bootstrap", True) and conf >= engine_mod.BOOTSTRAP_CONF:
                return label
            if ((not self._cfg.get("strict", True)) and self._stable_hand_mpsz
                    and conf >= engine_mod.ENGINE_MIN_CONF_RELAX):
                return label
            return None
        orig = Engine._apply_conf
        Engine._apply_conf = legacy                       # type: ignore
        try:
            on = bare_engine()._apply_conf(CONF_RECT, "5m", 0.45)
            off = bare_engine(strict=False)._apply_conf(CONF_RECT, "5m", 0.45)
        finally:
            Engine._apply_conf = orig
        self.assertIsNone(on, "变异无效：0.45 在严格门槛下居然被采信了")
        self.assertEqual(on, off,
                         "坏写法里开关居然还有差别：那 ⑦ 钉的就不是死分支")
        # 现行写法必须真能拨动（否则上面两条断言只证明了两侧都坏）
        self.assertEqual("5m", bare_engine(strict=False)._apply_conf(CONF_RECT, "5m", 0.45),
                         "现行写法与坏写法同解：⑦ 在测空气")

    def test_mutant_unregistered_cfg_key_is_silently_dead(self):
        """坏写法 I：开关只被 `_cfg.get` 读、没登进 `_cfg` → 拨不动它，③ 的可达性断言红。

        这条专门照「静默死开关」：`set_config` 不认的 key 不报错、不日志、不红，
        只让一个排障能力从来就不存在。拿真 Engine 减掉那个 key 复现它。
        """
        eng = bare_engine()
        eng._cfg.pop("hand_channel_ab")          # 回到「忘了登记」的写法
        eng.set_config("hand_channel_ab", True)   # 必须不报错（静默丢弃才是坑）
        self.assertFalse(eng._cfg.get("hand_channel_ab", False),
                         "减掉登记后开关反而拨得动：③ 的可达性断言在测空气")
        det = ScriptedDetector()
        channel_case(eng, det, canvas())
        self.assertEqual(len(det.heights), 1,
                         "没登记也照样付了对照的钱：那这条对照就不成立")


class TestDampingSourceContract(unittest.TestCase):
    """源码契约：瞬态阻尼不得再把「读到 0 张」漏在门外，也不得再绑 `_match_started`。"""

    def test_damping_gate_does_not_require_nonzero_read(self):
        line = damping_gate_line(ENGINE_SRC)
        self.assertIsNotNone(line,
                             "找不到瞬态阻尼入口（`in_active_match = ...` 之后那条 "
                             "`if in_active_match`）：检查器已经失去对照物")
        self.assertNotIn(
            "curr_raw_n", line,
            "阻尼入口又变成了「%s」：换牌弹窗盖住手牌行（raw==0，最常见的瞬态形状）"
            "会直接掉进 no_tiles → 面板「等待牌局开始」" % line)

    def test_damping_gate_does_not_require_match_started(self):
        """阻尼入口不得再要求 `_match_started`（真机反馈「13 张只显示 5 张」的根因）。

        换三张/定缺阶段 `_match_started` 可以是 False，旧口径把这段时间的瞬态欠读
        裸露出来：弹窗盖住半排手牌的一帧直接把 13 张降成 5 张，下一帧又跳回去。
        阻尼唯一该看的是「有没有已提交的稳定手牌」，而它在 `_reset_game_state`
        里会被清空，不会把上一局的牌带进新局。
        """
        assign = damping_assign_line(ENGINE_SRC)
        self.assertIsNotNone(assign, "找不到阻尼入口的赋值行：结构变了")
        self.assertNotIn("_match_started", assign,
                         "阻尼又绑回 `_match_started`：%s" % assign)


ENGINE_PY = os.path.join(REPO, "android", "app", "src", "main", "python",
                         "engine", "engine.py")
with open(ENGINE_PY, encoding="utf-8") as _fp:
    ENGINE_SRC = _fp.read()


def damping_gate_line(src: str):
    """取瞬态阻尼入口那行 `if in_active_match...` 的源码文本（找不到返回 None）。

    只认**那一行**而不是扫一段字符串：入口下方的注释里原文写着被拆掉的条件
    （`curr_raw_n > 0`，那是「为什么拆」的留档）。按窗口扫会把「讲历史的注释」
    读成「坏掉的代码」—— 于是主守卫恒红、变异对照恒真，两边都空转。
    """
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if not ln.strip().startswith('in_active_match = '):
            continue
        for nxt in lines[i + 1:i + 12]:
            s = nxt.strip()
            if s.startswith('if in_active_match'):
                return s
            if s and not s.startswith('#'):
                return None       # 入口后面跟的不是阻尼判断：结构变了，另说
        return None
    return None


def damping_gate_regressed(src: str) -> bool:
    """阻尼入口那行是否又带上了「raw 非空才阻尼」的前置条件。"""
    line = damping_gate_line(src)
    return line is not None and 'curr_raw_n' in line


def damping_assign_line(src: str):
    """取阻尼入口的赋值行本体（`in_active_match = ...`），找不到返回 None。

    与 `damping_gate_line` 分开：一个管「入口后面那条判断」，一个管「入口自己
    由什么条件算出来」——后者才是 `_match_started` 回潮的落点。
    """
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith('in_active_match = '):
            return s
    return None


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：每个变异体必须「如期坏掉」")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
