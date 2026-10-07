# -*- coding: utf-8 -*-
"""风格探针路由（_probe_style）回归：路由选错 bank，后面全错。

为什么值得单独锁：手牌行的分类范围由探针决定（`classify_tile(crop, styles={胜出风格})`），
所以探针是准确率的**上游变量**——实测单枚代表牌 + 单点阈值的路由命中率只有 39%
（`localtest/probe_style_rules.py` R0），错路由会把腾讯底座从 100% 拖到 86%
（`localtest/ab_bank_impact.py`）。改成多枚取平均后，离线牌集 84%、线上整帧实切 49%。

本文件锁六件事：
1. 自配分必须路由到本家 bank（正交合成模板：本家 1.0、别家 ~0.0）。
2. **额外代表牌必须真的参与裁决**：一枚被角标/阴影污染的牌不该决定整帧路由，
   多数枚要能否决单枚。这条同时锁住“忽略 extra_crops”和“取单枚最优而非平均”两种
   退化——合成核的顺序特意把主牌风格排在 bank 首位，任何靠平局取先出现的实现都会翻车。
3. 平均不是取最差：一枚强 + 一枚弱但同风格，仍应路由（min 实现会误判成拒识）。
4. 门槛两侧各有一个夹具：0.60 被调松（例如 0.30）或调紧（例如 0.80）都必须有断言变红。
5. 纯色/零方差裁片只能拒识，不许抛异常——实测真实 bank 上 `_probe_style(纯灰)`
   曾直接 `ValueError: max() arg is an empty sequence`（TM_CCOEFF_NORMED 对方差为零
   的图像恒返 0，于是所有风格都进不了候选字典），而 `extract_face` 对退化输入正是
   返回纯黑图，所以这条路径线上可达。
6. 生产调用点真的传了额外枚、且枚数有上限（每多一枚就多扫一整轮模板分，是耗时项）。
7. 排名表 `last_probe_ranking` 的账目：首项就是本次胜者、平局顺序与原 `max()` 逐字
   一致、每条提前拒识的路径留空表（否则上一帧的表会决定本帧用哪些字模）。
8. 第二名真的进了分类候选集（`_probe_runner_up` 接线）：陌生平台实测 92.1% -> 94.8%
   靠的就是这一步，helper 写了但调用点没并 = 白改。

诚实说明：本文件**不**声称锁住“取平均 vs 取单枚最优”的全部区分能力——正交夹具上
两者只在平局顺序上有差别（第 2 条正是利用这一点）。真正的规则对照（单枚/投票/均值/
领先差）由 `localtest/probe_style_rules.py` 用真牌实测负责。

运行：py -3.10 localtest/test_style_probe.py
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

import cv2  # noqa: E402

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

# 合成 bank：三种互相正交的图案，本家自配分 1.0、别家 ~0.0，路由结论完全可预期。
# 顺序有意把 "sB" 放最前：第 2 条用例的主裁片属于 sB，若实现退化成“取单枚最优”，
# 平局时会先取到 sB 而不是多枚平均应得的 sA，用例就会红。
STYLES = ("sB", "sA", "sC")


def _pattern(kind: str) -> np.ndarray:
    """120x80 的灰度图案（B==G==R => 色相 0，不会被误判成黄色角标而走 btn 核）。"""
    img = np.full((120, 80, 3), 180, dtype=np.uint8)
    if kind == "v":                                   # 竖条
        for x in range(8, 72, 12):
            img[:, x:x + 6] = 40
    elif kind == "h":                                 # 横条
        for y in range(10, 110, 14):
            img[y:y + 7, :] = 40
    elif kind == "c":                                 # 棋盘
        for y in range(0, 120, 16):
            for x in range(0, 80, 16):
                if (x // 16 + y // 16) % 2:
                    img[y:y + 16, x:x + 16] = 40
    else:
        raise ValueError(kind)
    return img


def _core(t: np.ndarray):
    """按 _build_cores 的切片与顺序造核：(lbl, style, core_btn, core_plain, btn_g, plain_g)。"""
    plain = t[16:104, 12:68]
    btn = t[44:104, 12:68]
    plain_g = cv2.normalize(cv2.cvtColor(plain, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
    btn_g = cv2.normalize(cv2.cvtColor(btn, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
    return btn, plain, btn_g, plain_g


T_A, T_B, T_C = _pattern("v"), _pattern("h"), _pattern("c")


def _blend(t: np.ndarray, u: np.ndarray, w: float) -> np.ndarray:
    """按权重线性混合。两图案正交 => 自配分 = 自身权重 / sqrt(权重平方和)。"""
    return np.clip(t * (1.0 - w) + u * w, 0, 255).astype(np.uint8)


# 夹具：TM_CCOEFF_NORMED 对亮度/对比度线性变换不变，所以分数只由“混合权重”决定。
# 理论值（已在实测中复核）：
#   单枚完全匹配 -> 1.00 对 ~0.00
#   52/48 混合   -> sA 0.73 / sB 0.68      （门槛之上、且低于 0.80）
#   三等分混合   -> 0.577 每风格            （门槛之下、且高于 0.30）
#   35/65 混合   -> sA 0.47 / sB 0.88      （用来验证“平均不是取最差”）
MIX_HIGH = _blend(T_A, T_B, 0.48)          # sA 略胜，且两者都在 0.60~0.80 之间
MIX_LOW = _blend(_blend(T_A, T_B, 0.5), T_C, 1.0 / 3.0)   # 三风格均分，最高约 0.58
MIX_WEAK = _blend(T_A, T_B, 0.65)          # sA 只剩 ~0.47，sB ~0.88
FLAT = np.full((120, 80, 3), 180, dtype=np.uint8)

# 生产里探针拿的是手牌带里的真实切片，任何一块都可能被切成纯色背景，所以这些
# “退化输入”必须按线上路径列全，而不是只测一个灰图。
DEGENERATE = {
    "纯灰": np.full((120, 80, 3), 180, dtype=np.uint8),
    "纯黑": np.zeros((120, 80, 3), dtype=np.uint8),
    "纯白": np.full((120, 80, 3), 255, dtype=np.uint8),
    "纯绿桌布": np.full((120, 80, 3), (0, 120, 0), dtype=np.uint8),
    "极小碎片": np.zeros((2, 3, 3), dtype=np.uint8),
    "空数组": np.zeros((0, 0, 3), dtype=np.uint8),
    "None": None,
}

# 接线用例的真帧夹具（存在几个算几个，不足 2 帧直接红，避免静默失去覆盖）
# 特意混进非腾讯风格：路到 tuyou/weile 这些“受限风格”的帧才能测探针与白名单的边界。
REAL_SHOTS = [
    os.path.join(HERE, "shots", "n1.jpg"),
    os.path.join(HERE, "shots", "n2.jpg"),
    os.path.join(HERE, "shots_tuyou", "r01_00081be2.jpg"),
    os.path.join(HERE, "shots_tuyou", "r51_b81325bc.jpg"),
    os.path.join(HERE, "shots_shushan", "s1.jpg"),
]

_real_det = None
_tile_cache = {}


def real_detector() -> TencentGridDetector:
    """真实 bank 的 detector（全库 ~280 条模板，构建要读盘，进程内共享一次）。"""
    global _real_det
    if _real_det is None:
        _real_det = TencentGridDetector()
    return _real_det


def hand_tile_crops(path):
    """从真帧里取**已检出的手牌**裁片列表：探针的线上入参就是这种东西。

    随手画一个固定坐标区域的话，很可能切到牌河/桌面，那样 `_probe_style` 返 None
    是"因为本来就无牌可路由"，断言就成了空转。按得分降序返回，第一枚是行里最可信的牌。"""
    if path in _tile_cache:
        return _tile_cache[path]
    det = real_detector()
    img = cv2.imread(path)
    out = []
    if img is not None:
        for rect, _lbl, _s in sorted(det.detect_hand_strip(img) or [], key=lambda e: -e[2]):
            x, y, w, h = (int(v) for v in rect)
            piece = img[y:y + h, x:x + w]
            if piece.size:
                out.append(piece)
    _tile_cache[path] = out
    return out


def best_tile_crop(path):
    """行里分数最高的一枚裁片（没有则 None）。"""
    crops = hand_tile_crops(path)
    return crops[0] if crops else None


def synth_detector() -> TencentGridDetector:
    """只换掉 _cores 的 detector：走真实 _probe_style 代码，但分数完全可控。

    active_styles 一并置 None（真实 __init__ 会给它默认值）：不然“探针去读白名单”
    这种变异只会以 AttributeError 变红，测不到行为而是测到属性缺失。"""
    cls = real_detector().__class__
    det = cls.__new__(cls)
    det.active_styles = None
    det.full_honors = False
    det._cores = [(f"t{i}", st) + _core(t)
                  for i, (st, t) in enumerate(zip(STYLES, (T_B, T_A, T_C)))]
    return det


def tie_detector(order=("sB", "sA")):
    """两家装**同一枚图案**的 bank：分数严格相等，胜者只能由遍历顺序决定。

    不用“两张图各混一半”造平局——竖条与横条并非严格正交，量化后总有一家常年
    微胜零点几分，那种夹具测到的是数值噪声而不是平局规则（会随机红/随机绿）。
    """
    cls = real_detector().__class__
    det = cls.__new__(cls)
    det.active_styles = None
    det.full_honors = False
    det._cores = [(f"t{i}", st) + _core(T_A) for i, st in enumerate(order)]
    return det


def runner_up_variant(ranking, style):
    """变异体（**勿接回生产**）：只看排名表，不校验这张表是不是本帧的。

    它存在的唯一理由是自证第 7 条不是空转：生产实现若去掉“表首必须等于本次胜者”
    这道校验就退化成它。`test_runner_up_is_conservative` 喂给它一份陈旧表会照样
    给出第二名，于是那条断言真的有对照物 —— 否则“宁可不加第二名”写不写都一样绿。
    """
    if style is None or len(ranking) < 2:
        return set()
    return {ranking[1][0]}


class TestStyleProbeRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.det = synth_detector()
        cls.styles = {c[1] for c in cls.det._cores}

    def test_fixture_scores_are_orthogonal(self):
        """夹具腐坏守卫：合成 bank 必须真的“本家满分、别家零分”，否则后面的断言全是空话。"""
        for st, crop in (("sA", T_A), ("sB", T_B), ("sC", T_C)):
            self.assertEqual(self.det._probe_style(crop), st,
                             f"{st} 的自配夹具没能路由到自身，合成 bank 已失效")
        # 混合夹具必须落在门槛的“预期那一侧”，这是第 4 条用例的前提
        self.assertEqual(self.det._probe_style(MIX_HIGH), "sA",
                         "MIX_HIGH 夹具不再位于门槛之上（0.60 两侧需要它）")
        self.assertIsNone(self.det._probe_style(MIX_LOW),
                          "MIX_LOW 夹具不再位于门槛之下（松门槛的变异测试会失效）")

    def test_extra_crops_participate_in_the_decision(self):
        """多数枚能否决单枚：一枚脏牌不该决定整帧走哪个 bank。

        主裁片是 sB，额外三枚是 sA => 平均 sA 0.75 / sB 0.25，必须路由 sA。
        忽略 extra_crops 的实现给 sB（红）；取单枚最优的实现里两者都是 1.0，
        靠“风格顺序取先”会落在 sB（红）——sB 特意排在 STYLES 首位。"""
        self.assertEqual(self.det._probe_style(T_B, [T_A, T_A, T_A]), "sA",
                         "额外代表牌没参与裁决：单枚污染就能带偏整帧")
        self.assertEqual(self.det._probe_style(T_A, [T_B, T_B, T_B]), "sB",
                         "反方向也必须成立，否则断言只是碰巧")

    def test_average_is_not_worst_case(self):
        """一枚强 + 一枚弱但同风格：平均 0.74 仍应路由，取 min 会误判成拒识。"""
        self.assertEqual(self.det._probe_style(T_A, [MIX_WEAK]), "sA",
                         "均值被改成最差值：一个偏暗的代表牌就会让整行退到全库兜底")

    def test_gate_has_samples_on_both_sides(self):
        """门槛必须真的在起作用：两侧各有一个夹具贴着它。

        MIX_HIGH 最高分约 0.73（>0.60 且 <0.80）、MIX_LOW 约 0.58（<0.60 且 >0.30），
        所以把 0.60 调松到 0.30 或调紧到 0.80，本用例必有一红。"""
        self.assertEqual(self.det._probe_style(MIX_HIGH), "sA")
        self.assertIsNone(self.det._probe_style(MIX_LOW),
                          "门槛之下的分数被放行了：错 bank 会比错牌更难查")
        self.assertEqual(self.det._probe_style(MIX_LOW, [T_A, T_A]), "sA",
                         "两份不足过门的样本 + 两份本家满分，平均应能过门并路由到本家")

    def test_degenerate_crops_refuse_instead_of_raising(self):
        """真实缺陷回归：零方差裁片曾抛 ValueError: max() arg is an empty sequence。

        用**真实 bank** 测：TM_CCOEFF_NORMED 对方差为零的图像恒返 0，于是没有任何
        风格进得了候选字典，`max()` 拿到空序列。而 detect_hand_strip 完全可能把探针
        切到纯色背景（`extract_face` 对退化输入返回的就是纯黑图）。"""
        det = real_detector()
        self.assertTrue(det.is_available, "真实 bank 没加载成功，本用例是假绿")
        for name, crop in DEGENERATE.items():
            try:
                got = det._probe_style(crop)
            except Exception as e:                       # noqa: BLE001
                self.fail(f"{name} 让探针抛异常而不是拒识：{type(e).__name__}: {e}")
            self.assertIsNone(got, f"{name} 被探针当成了可信路由，会把整行锁进错 bank")
        # 一枚纯色 + 一枚真牌：纯色不得把有效样本稀释到拒识
        tile = best_tile_crop(REAL_SHOTS[0])
        self.assertIsNotNone(tile, "拿不到真实手牌裁片，本用例只剩一半意义")
        solo = det._probe_style(tile)
        self.assertIsNotNone(solo, "真牌本身就没路由成功，下面的‘不被纯色拖下水’断言是空转")
        self.assertEqual(det._probe_style(tile, [FLAT]), solo,
                         "纯色额外枚把有效样本拖成拒识（应“不投票”，而非投 0 票）")


class TestProbeWiringIsAlive(unittest.TestCase):
    """探针改了算法但调用点没传额外枚 = 白改；这条只看接线，不看分数。"""

    def test_hand_strip_passes_a_capped_number_of_extra_crops(self):
        det = real_detector()
        seen = []
        calls = []

        def spy(crop, extra_crops=None):
            calls.append(0 if not extra_crops else len(extra_crops))
            return det.__class__._probe_style(det, crop, extra_crops)

        orig = det._probe_style
        det._probe_style = spy
        try:
            for path in REAL_SHOTS:
                img = cv2.imread(path)
                if img is None:
                    continue
                dets = det.detect_hand_strip(img) or []
                if len(dets) >= 9:          # 只有真的检出手牌行时才会走到探针
                    seen.append(os.path.basename(path))
        finally:
            det._probe_style = orig
        self.assertGreaterEqual(len(seen), 2,
                                f"只有 {len(seen)} 帧可用，接线守卫失去意义：{REAL_SHOTS}")
        self.assertTrue(calls, "detect_hand_strip 根本没调用探针")
        # 两种节距模型各切一枚，所以枚数只能落在 1~2；0 枚说明切牌宽度不够或接线丢了
        self.assertGreaterEqual(min(calls), 1, f"有帧一枚额外代表牌都没传：{calls}")
        # 枚数上限是耗时项：每多一枚就多扫一整轮 bank 模板分
        self.assertLessEqual(max(calls), 2, f"探针枚数涨到 {max(calls)}，需先量耗时再放行")

    def test_probe_is_independent_of_platform_whitelist(self):
        """探针必须始终扫全 bank（它的工作就是猜平台），白名单只在分类阶段收窄。

        这条锁的是 `set_platform_styles` 与探针的职责边界：若哪天探针也去读
        active_styles，未声明平台的帧会永久路由不到本家，而现象只是“变慢/变差”。

        夹具必须是“胜出风格被某些平台禁掉”的那类帧：拿腾讯帧测的话，腾讯在所有
        白名单里都可用，变异体也不会改变结果（实测如此，所以这里按行为筛选夹具，
        不写死文件名）。"""
        det = real_detector()
        plans = []
        for path in REAL_SHOTS:
            det.set_platform_styles(None)
            for tile in hand_tile_crops(path):
                base = det._probe_style(tile)
                if base is None:
                    continue
                # 用生产自己算的白名单来选平台，不在测试里复制一遍过滤规则
                excluded = []
                for plat in ("tencent", "weile", "tuyou", "jj", "gd_queshen", "shushan"):
                    det.set_platform_styles(plat)
                    if det.active_styles is not None and base not in det.active_styles:
                        excluded.append(plat)
                det.set_platform_styles(None)
                if excluded:
                    plans.append((path, tile, base, excluded))
                    break
        det.set_platform_styles(None)
        self.assertTrue(plans,
                        "没有一帧的胜出风格被任何平台禁掉，职责边界没被真正测到")
        for path, tile, base, excluded in plans[:3]:
            det.set_platform_styles(None)
            self.assertEqual(det._probe_style(tile), base, f"{os.path.basename(path)} 路由不稳定")
            for plat in excluded:
                det.set_platform_styles(plat)
                self.assertEqual(det._probe_style(tile), base,
                                 f"声明 {plat}（其白名单不含 {base}）后探针结果变了："
                                 f"探针被白名单污染，未声明平台的帧会永久路由失败")
            det.set_platform_styles(None)

    def test_bank_cores_fit_the_probe_scan_windows(self):
        """每条模板核都必须放得进探针的两种扫描窗，否则 matchTemplate 直接抛异常。

        has_btn 分支的窗是 72x68，普通分支是 100x68（`extract_face` 统一尺寸后切片）。
        收割新平台模板时最容易塞进一张尺寸异常的图。"""
        det = real_detector()
        self.assertGreaterEqual(len(det._cores), 200,
                                f"模板条目只有 {len(det._cores)}，bank 可能没挂全")
        bad = []
        for lbl, style, core_btn, core_plain, _bg, _pg in det._cores:
            if core_btn.shape[0] > 72 or core_btn.shape[1] > 68:
                bad.append((lbl, style, "btn", core_btn.shape[:2]))
            if core_plain.shape[0] > 100 or core_plain.shape[1] > 68:
                bad.append((lbl, style, "plain", core_plain.shape[:2]))
        self.assertFalse(bad, f"这些核超出探针扫描窗，会在带角标的牌上抛异常：{bad[:6]}")

    def test_routing_is_reproducible_for_the_same_frame(self):
        """同一帧连跑两次必须同路由：路由不稳定，投票与一致性判定就全不可复现。"""
        det = real_detector()
        tile = best_tile_crop(REAL_SHOTS[0])
        self.assertIsNotNone(tile, "夹具帧缺失")
        first = det._probe_style(tile)
        for _ in range(5):
            self.assertEqual(det._probe_style(tile), first, "同一裁片路由结果漂移")
        mixed = det._probe_style(tile, [MIX_LOW, MIX_WEAK])
        for _ in range(5):
            self.assertEqual(det._probe_style(tile, [MIX_LOW, MIX_WEAK]), mixed,
                             "带额外枚时路由结果漂移（风格遍历顺序不稳定）")


class TestProbeRankingLedger(unittest.TestCase):
    """排名表与第二名本身：账目错了，放宽候选集就会变成“拿上一帧的牌风认这一帧”。"""

    def setUp(self):
        self.det = synth_detector()

    def test_head_of_ranking_is_the_returned_style(self):
        for crop, want in ((T_A, "sA"), (T_B, "sB"), (T_C, "sC"), (MIX_HIGH, "sA")):
            self.assertEqual(self.det._probe_style(crop), want)
            self.assertTrue(self.det.last_probe_ranking,
                            f"路由到 {want} 却没留下排名表：调用点拿不到第二名")
            self.assertEqual(self.det.last_probe_ranking[0][0], want,
                             "排名表首项不等于本次胜者，`_probe_runner_up` 会把它当陈旧表丢弃")
            scores = [s for _st, s in self.det.last_probe_ranking]
            self.assertEqual(scores, sorted(scores, reverse=True), "排名表必须降序")

    def test_tie_order_matches_the_old_max(self):
        """平局必须仍落在 bank 遍历顺序的先出现者上 —— 与改动前的 `max()` 逐字一致。

        排名表若加了第二级键（例如按风格名排序），现象只是“某些帧换了 bank”，
        从准确率上几乎查不回来，所以只能钉死在夹具顺序上，两个方向都要试。"""
        for order, want, want_second in ((("sB", "sA"), "sB", "sA"),
                                         (("sA", "sB"), "sA", "sB")):
            det = tie_detector(order)
            self.assertEqual(det._probe_style(T_A), want,
                             f"bank 顺序 {order} 下平局裁决漂移：排名表已经在改变路由结果")
            self.assertEqual([st for st, _s in det.last_probe_ranking], list(order),
                             "严格平局时排名顺序必须是 bank 顺序（稳定排序未生效？）")
            self.assertEqual(det._probe_runner_up(want), {want_second},
                             "平局时第二名必须是另一家，否则“放宽到两家”是空话")

    def test_ranking_lists_each_style_once(self):
        """真 bank 上每家有几十枚核（btn/plain + 多变体），排名表必须已按风格聚合。

        若哪天直接把逐核分数排序塞进表里，“第二名”就会变成同一家的另一枚核，
        放宽候选集等于没放宽 —— 而这在所有精度指标上都是不可见的。"""
        det = real_detector()
        tile = best_tile_crop(REAL_SHOTS[0])
        self.assertIsNotNone(tile, "夹具帧缺失，本用例是空转")
        det._probe_style(tile)
        styles = [st for st, _s in det.last_probe_ranking]
        self.assertGreaterEqual(len(styles), 3, f"真实 bank 上只打出 {len(styles)} 家，夹具可疑")
        self.assertEqual(len(styles), len(set(styles)), f"排名表有重复风格：{styles}")

    def test_refusal_below_the_gate_still_records_this_frame(self):
        """未过门时表照记本帧事实：调用点在 style is None 的分支走全库兜底，不读它。"""
        self.assertIsNone(self.det._probe_style(MIX_LOW))
        self.assertGreaterEqual(len(self.det.last_probe_ranking), 2,
                                "未过门的帧连排名表都没留下，诊断时无法复现“差一点”")

    def test_every_early_refusal_leaves_an_empty_table(self):
        """提前返回 None 的路径必须清空表；只要有一条留了旧表，本帧就会继承上一帧的第二名。"""
        det = real_detector()
        for name, crop in DEGENERATE.items():
            det.last_probe_ranking = [("stale", 1.0), ("staler", 0.9)]   # 先污染成“上一帧的表”
            self.assertIsNone(det._probe_style(crop), f"{name} 竟然路由成功了")
            self.assertEqual(det.last_probe_ranking, [],
                             f"{name} 拒识后仍留着上一帧的排名表：跨帧陈旧")
        bare = synth_detector()
        bare._cores = []                                                  # 无核路径
        bare.last_probe_ranking = [("stale", 1.0)]
        self.assertIsNone(bare._probe_style(T_A))
        self.assertEqual(bare.last_probe_ranking, [], "无核路径没清空表")

    def test_runner_up_is_conservative(self):
        """宁缺毋滥：无表 / 胜者为 None / 表首不是胜者 / 只有一家，四种情况都不给第二名。"""
        det = self.det
        det.last_probe_ranking = []
        self.assertEqual(det._probe_runner_up("sA"), set(), "空表却给出了第二名")
        det.last_probe_ranking = [("sA", 0.91), ("sC", 0.80)]
        self.assertEqual(det._probe_runner_up(None), set(), "探针没赢出风格，第二名无来源")
        self.assertEqual(det._probe_runner_up("sB"), set(),
                         "表首不是本次胜者（跨帧陈旧）仍然给出了第二名")
        # 变异体在这份陈旧表上会端出 sC —— 正是“拿上一帧的第二名认本帧”的病灶；
        # 生产实现必须给空集，两者不同才说明上面那条断言真的有对照物（第 7 条非空转）。
        self.assertEqual(runner_up_variant(det.last_probe_ranking, "sB"), {"sC"},
                         "变异体探针失效：去掉陈旧校验后本用例无法区分，第 7 条是空转")
        det.last_probe_ranking = [("sA", 0.91)]
        self.assertEqual(det._probe_runner_up("sA"), set(), "只有一家时没有第二名，不得编造")
        det.last_probe_ranking = [("sA", 0.91), ("sB", 0.80)]
        self.assertEqual(det._probe_runner_up("sA"), {"sB"})


class TestRunnerUpWiring(unittest.TestCase):
    """第二名得真的并进分类候选集 —— helper 写了但调用点没接上，表现只是“略差一点”。"""

    def test_candidate_set_widens_and_only_by_the_runner_up(self):
        det = real_detector()
        orig_probe, orig_face, orig_ru = det._probe_style, det._score_face, det._probe_runner_up
        routed, seen = [], []

        def spy_probe(crop, extra_crops=None):
            st = orig_probe(crop, extra_crops)
            routed.append(st)
            return st

        def spy_face(face, avail=None, styles=None):
            if styles is not None:
                seen.append(frozenset(styles))
            return orig_face(face, avail, styles)

        def run_once(path):
            del routed[:]
            del seen[:]
            det.set_platform_styles(None)
            det.detect_hand_strip(cv2.imread(path))
            return (routed[0] if routed else None), set(seen)

        det._probe_style, det._score_face = spy_probe, spy_face
        widened = 0
        try:
            for path in REAL_SHOTS:
                if cv2.imread(path) is None:
                    continue
                win_a, sets_a = run_once(path)
                if win_a is None or not sets_a:
                    continue                       # 这帧没路由成功（全库兜底），下一帧
                # 变异体：调用点拿不到第二名，等价于改动前的 `{style}` 单家候选集
                det._probe_runner_up = lambda _style: frozenset()
                win_b, sets_b = run_once(path)
                det._probe_runner_up = orig_ru
                self.assertEqual(win_a, win_b,
                                 f"{os.path.basename(path)} 同帧两次路由不一致：排名表引入了不确定性")
                self.assertEqual(sets_b, {frozenset({win_a})},
                                 f"{os.path.basename(path)} 断开 runner 后候选集居然不是单家，"
                                 f"说明这条用例比的不是 runner（{sets_b}）")
                want = frozenset({win_a} | det._probe_runner_up(win_a))
                self.assertIn(want, sets_a,
                              f"{os.path.basename(path)} 分类时没拿到胜者+第二名：{sets_a}")
                if len(want) == 2:
                    widened += 1
        finally:
            det._probe_style, det._score_face, det._probe_runner_up = orig_probe, orig_face, orig_ru
            det.set_platform_styles(None)
        self.assertGreaterEqual(widened, 1,
                                "没有一帧真的因第二名放宽到两家：夹具已失去接线检验能力")

    def test_declared_platform_still_keeps_its_own_bank(self):
        """b6b 的结论在并上第二名之后仍要成立：探针无权把用户声明平台的专属 bank 挤出候选集。"""
        det = real_detector()
        orig_probe = det._probe_style

        def make_fake(win, second):
            def fake(crop, extra_crops=None):
                det.last_probe_ranking = [(win, 0.91), (second, 0.88)]
                return win
            return fake

        try:
            for plat, win, second in (("weile", "tencent", "weile"),
                                      ("tuyou", "tencent", "tuyou"),
                                      ("jj", "jj", "tencent")):
                det.set_platform_styles(plat)
                det._probe_style = make_fake(win, second)
                got = det._resolve_styles({win} | det._probe_runner_up(win))
                own = det.active_styles - {"tencent"}
                self.assertIsNotNone(got, f"声明 {plat} 时候选集被收窄成空集")
                self.assertTrue(own <= set(got),
                                f"声明 {plat}、探针给 {win}/{second} 时专属 bank 被挤出：{got}")
        finally:
            det._probe_style = orig_probe
            det.set_platform_styles(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
