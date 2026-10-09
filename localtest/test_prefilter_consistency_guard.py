# -*- coding: utf-8 -*-
"""延迟第四刀守卫：打分改成「粗筛 + 精算」两阶段后，**最终裁决必须与全量扫逐字全等**。

这一刀改的是什么（口径见 `recognition/tencent_grid_detector.py` 里 `PREFILTER_SCALE`
上方那一整段）：每枚牌面原来要把 110 枚全尺寸核全部 `matchTemplate` 一遍；现在先把
牌面图与核一起缩到 1/2（像素 1/4）跑一遍便宜的 NCC 只做排序，只对少数标签跑全尺寸
NCC。实测（`build/prefilter_gt.txt`，37 帧 verified GT 的真实调用序列 738 次打分）
11.26ms/枚 → 5.71ms/枚。

它为什么危险，以及危险在哪一处：这一刀动的是**候选集**，不是判据。判据写错会稳定
错，候选集裁错会**偶发**错——真答案被粗筛挤出精算集，NCC 分再高也没人替它说话。
唯一一条会「造成错判而不是造成慢」的机制是 `_decide` 的结构裁决要在分数表上读**相邻
类**的分（2m↔3m 等 7 条），对家被粗筛淘汰后 `scores.get("3m", 0.0)` 读成 0，那条判决
整条失效。所以精算集必须对相邻类做闭包，而这条闭包是本守卫的主要靶子。

分四层，每层都注明它**证到了什么**、**证不到什么**：
  A. 精算集的集合语义（纯函数 `_refine_labels`）：top-K、分差带、相邻类闭包、
     放不下窗口者强进、边界 `>`。这一层跑生产本体，不复制逻辑。
  B. 真帧对拍（本守卫主体）：把 37 帧 verified GT 里**每一次** `_score_face` 调用
     （手牌行/牌河/副露/救援重扫都算）录下来，同一枚 face 分别在 `prefilter` 开/关
     下打分，比过完 `_decide` 的最终 (label, score)。另外钉两条「对拍不是空转」的
     事实：精算真的少扫了核，且全量 argmax 每一枚都落进了生产自己算出的精算集。
     为什么逐枚对拍比"整帧 A/B"更强：帧级任何差异都必须由某一枚 (label, score) 的
     差异引起，逐枚全等 ⇒ 帧级不可能变；反过来整帧 A/B 会把"这帧恰好没走到那条判决"
     的漏检当成通过。
  C. 粗筛核轨的身份校验（合成核）：离线评测（LOFO 剔模板）会**在运行时就地替换**
     `det._cores`，粗筛轨必须以对象身份而不是长度为准，否则错位。这一层用可控合成
     夹具，因为真实 bank 里造不出「同长度、不同内容」的那种替换。
  D. 接线契约 + 已知边界：闭包表必须与 `_decide` 源码里实际成对出现的标签一致
     （新增判决忘了加表，离线就红，不等真机认错牌）；以及如实写下「对拍只覆盖这
     37 帧的腾讯/血流红人口径，别的牌风靠分差带自我退化兜，不靠测试兜」。

运行：
  py -3.10 -X utf8 localtest/test_prefilter_consistency_guard.py
  py -3.10 -X utf8 localtest/test_prefilter_consistency_guard.py --mutate
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import textwrap
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PY_DIR = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

MUTATE = "--mutate" in sys.argv

DET_PY = os.path.join(PY_DIR, "recognition", "tencent_grid_detector.py")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")
# 对拍至少要覆盖这么多枚 face：少了就说明夹具没接上，"全等"是在比空气。
MIN_FACES = 600

with open(DET_PY, encoding="utf-8") as fp:
    DET_SRC = fp.read()

from recognition import tencent_grid_detector as TGD  # noqa: E402

TOP_K = TGD.PREFILTER_TOP_K
MARGIN = TGD.PREFILTER_MARGIN
SCALE = TGD.PREFILTER_SCALE
PAIRS = TGD.ADJACENT_PAIRS
ADJ = TGD.ADJACENCY


# --------------------------------------------------------------------- A 组
def _refine_prod(coarse, forced, top_k=TOP_K, margin=MARGIN):
    """跑生产 `_refine_labels` 本体（静态方法，不复制逻辑）。"""
    return TGD.TencentGridDetector._refine_labels(coarse, forced, top_k, margin)


# 分差带边界用**同一个表达式**算出来（而不是手写一个“看上去相等”的 0.84）：
# 0.90-0.06 在二进制浮点里不等于字面量 0.84，手写等值会让「把 > 松成 >=」那一条
# 变异根本跑不到边界，测了个空。
_LEAD = 0.90
_EDGE = _LEAD - MARGIN            # 恰好等于带宽：严格大于 ⇒ 不进
_INSIDE = _EDGE + 0.005           # 比带宽多一吹毛 ⇒ 进

# (粗筛分数表, forced, 期望精算集, 这一行在测什么)
# 特意把 4p 放在「分差带之外 + 未被 top-K 选中」的位置：它是 5p 的对家，但 5p 没入围，
# 所以闭包**不该**把它拉进来——闭包只服务入围者，不是「凡是形近类都精算」。
REFINE_TABLE = [
    ({"5m": 0.90, "3p": 0.80, "2m": 0.79, "7s": 0.78, "4p": 0.50, "9p": 0.30},
     set(), {"5m", "3p", "2m", "3m", "2p"},
     "top-3 + 2m⇒3m/3p⇒2p 闭包；带外且未入围的 4p/7s/9p 淘汰"),
    ({"3m": 0.91, "6m": 0.90, "8m": 0.895, "1s": 0.20}, set(),
     {"3m", "6m", "8m", "2m"}, "闭包方向对称（3m 入围也要把 2m 拉进来）"),
    ({"4s": 0.95, "5m": 0.94, "6m": 0.93, "9p": 0.10}, set(),
     {"4s", "5m", "6m", "5s"}, "4s↔5s 这条判决**没有分差门槛**，对家必须在场"),
    ({"4p": 0.95, "5m": 0.94, "6m": 0.93, "9p": 0.10}, set(),
     {"4p", "5m", "6m", "5p"}, "4p↔5p 同上（中心红判据读的是两边的分）"),
    ({"1m": _LEAD, "5m": 0.89, "7s": 0.885, "9p": _INSIDE, "8m": _EDGE, "6m": 0.50},
     set(), {"1m", "5m", "7s", "9p"},
     "分差带边界：比带宽多一吹毛进，恰好等于带宽**不**进"),
    ({"1m": 0.90, "5m": 0.85, "6m": 0.80, "7s": 0.20, "9p": 0.15}, {"8m"},
     {"1m", "5m", "6m", "8m"},
     "粗筛排不上的核（forced）必须直接精算；带外且未入围的 7s/9p 仍淘汰"),
    ({"5s": 0.90, "9p": 0.89, "6m": 0.88, "1m": 0.875, "4s": 0.50}, set(),
     {"5s", "9p", "6m", "1m", "4s"},
     "4s 粗筛垫底也要进：闭包服务的是判决输入，不是粗筛名次"),
]


def _assert_refine_table(refine):
    for coarse, forced, want, why in REFINE_TABLE:
        got = refine(dict(coarse), set(forced))
        assert got == want, f"{why}：精算集 = {sorted(got)}，应 {sorted(want)}"
    # 非空是硬要求：精算集空 ⇒ scores 空 ⇒ classify_tile 退化成「报候选集里任意一张 0 分」
    for coarse, forced, _w, _y in REFINE_TABLE:
        assert refine(dict(coarse), set(forced)), "精算集为空（会把整枚牌判废）"


def _assert_closure_holds(refine):
    """闭包的不变量版断言（不依赖具体数值）：入围者的对家必须全在场。"""
    coarse = {"2m": 0.99, "5m": 0.98, "6m": 0.97, "7s": 0.10, "9p": 0.05}
    keep = refine(dict(coarse), set())
    for lbl in ("2m", "5m", "6m"):
        for p in ADJ.get(lbl, ()):
            assert p in keep, f"{lbl} 入围却没把对家 {p} 拉进精算集（结构判决会失效）"


def _assert_topk_kept(refine):
    """另一种不变量：粗筛前 K 名无条件入围（带与闭包只能加人，不能减人）。"""
    coarse = {"1m": 0.9, "5m": 0.8, "7s": 0.7, "8m": 0.6, "9p": 0.5}
    keep = refine(dict(coarse), set())
    for lbl, _s in sorted(coarse.items(), key=lambda kv: -kv[1])[:TOP_K]:
        assert lbl in keep, f"{lbl} 是粗筛前 {TOP_K} 名却没进精算集"


class TestRefineSet(unittest.TestCase):
    """A 组：精算集的集合语义。跑的是生产 `_refine_labels` 本体。"""

    def test_refine_table(self):
        _assert_refine_table(_refine_prod)

    def test_adjacency_is_symmetric_and_complete(self):
        for a, b in PAIRS:
            self.assertIn(b, ADJ[a], f"闭包表不对称：{a} 拉不到 {b}")
            self.assertIn(a, ADJ[b], f"闭包表不对称：{b} 拉不到 {a}")
        self.assertGreaterEqual(len(PAIRS), 7,
                                "闭包表被砍小了：_decide 有 7 条成对判决，一条都不能漏")

    def test_refined_set_is_smaller_than_candidates(self):
        # 这一刀的收益前提是「精算集确实比候选集小」。若表/K 被改到精算=全量，
        # 对拍照样全绿但时间一分没省——那条红只有这里能给出。
        coarse = {f"{i}m": 0.9 - 0.01 * i for i in range(1, 10)}
        keep = _refine_prod(coarse, set())
        self.assertLess(len(keep), len(coarse),
                        "分差带把所有候选都吸进精算集了（这一刀不会再生效）")


# ------------------------------------------------------------- 真帧夹具（B 组）
_FACES = None          # [(det, face, avail, styles)]：生产真实调用序列


def _load_verified():
    with open(GT_PATH, encoding="utf-8") as fp:
        return [e for e in json.load(fp)["shots"] if e.get("verified")]


def _record_faces():
    """跑一遍 `Engine.process()`，把每一次 `_score_face` 的入参录下来。

    录的是**生产实际调用**（含 avail/styles 两份过滤参数），不是探针自己另挑一批
    face：候选集与风格过滤会改变精算集的大小，换一批 face 就等于换一个结论。"""
    import engine.engine as EE
    recorded = []
    orig = TGD.TencentGridDetector._score_face

    def spy(self, face, avail=None, styles=None):
        out = orig(self, face, avail, styles)
        recorded.append((self, face, avail, styles))
        return out

    TGD.TencentGridDetector._score_face = spy
    try:
        for e in _load_verified():
            img = cv2.imread(os.path.join(SHOT_DIR, e["file"]))
            if img is None:
                continue
            with contextlib.redirect_stdout(io.StringIO()):
                EE.Engine().process(img)
    finally:
        TGD.TencentGridDetector._score_face = orig
    return recorded


def faces():
    global _FACES
    if _FACES is None:
        _FACES = _record_faces()
    return _FACES


def _score_with(det, face, avail, styles, prefilter):
    old = det.prefilter
    det.prefilter = prefilter
    try:
        _f, scores, _v = det._score_face(face, avail, styles)
    finally:
        det.prefilter = old
    return scores


def _final(det, face, scores):
    """过完 `_decide` 的最终裁决——消费者能看见的只有这两项。"""
    if not scores:
        return ("<无分>", 0.0)
    return det._decide(face, scores)


class TestRealFrameConsistency(unittest.TestCase):
    """B 组：真帧逐枚对拍。"""

    def test_every_face_final_output_is_identical(self):
        diffs = []
        fs = faces()
        self.assertGreaterEqual(len(fs), MIN_FACES,
                                f"只录到 {len(fs)} 枚 face（<{MIN_FACES}）：夹具没接上，全等是假的")
        for det, face, avail, styles in fs:
            full = _score_with(det, face, avail, styles, False)
            two = _score_with(det, face, avail, styles, True)
            a, b = _final(det, face, full), _final(det, face, two)
            if a != b:
                diffs.append((sorted(full, key=full.get, reverse=True)[:3], a, b))
        self.assertEqual([], diffs[:8],
                         f"{len(diffs)}/{len(fs)} 枚 face 的两阶段最终裁决与全量不一致："
                         f"前三行 =（全量前 3 名, 全量裁决, 两阶段裁决）")

    def test_two_phase_actually_scans_fewer_cores(self):
        """对拍不是「两次相同的全量扫」：精算必须真的少扫核。"""
        fs = faces()
        full_cores, two_cores, two_labels = _count_scans(fs)
        self.assertGreater(full_cores, 0, "一次都没扫过核")
        self.assertLess(two_cores, full_cores * 0.75,
                        f"两阶段扫了 {two_cores} 枚核 vs 全量 {full_cores} 枚："
                        "粗筛没在淘汰任何东西，这一刀等于没做（时间一分没省）")
        self.assertGreater(two_labels, 0)

    def test_full_argmax_is_inside_the_produced_refined_set(self):
        """构造性判据：全量 argmax 必须落进**生产自己算出的**精算集。

        这条比「最终一致」更硬：只要它成立，最终一致就是由构造保证的（见
        `PREFILTER_SCALE` 上方）。反过来最终一致 100% 而这条不成立，就是靠运气。"""
        escaped = []
        fs = faces()
        for det, face, avail, styles in fs:
            full = _score_with(det, face, avail, styles, False)
            if not full:
                continue
            amx = max(full, key=full.get)
            keep = _capture_refined(det, face, avail, styles)
            if keep is None:          # 这一枚走了退回全量（候选太小/粗筛空分），必然在内
                continue
            if amx not in keep:
                escaped.append((amx, round(full[amx], 3), sorted(keep)))
        self.assertEqual([], escaped[:8],
                         f"{len(escaped)} 枚 face 的全量 argmax 没进精算集（最终一致是撞上的）")

    def test_production_detectors_run_with_prefilter_on(self):
        # 开关默认关着的话，上面三条会全绿而生产一分没省。
        self.assertTrue(all(det.prefilter for det, _f, _a, _s in faces()),
                        "生产实例上的 prefilter 不是 True：这一刀没上线")


def _count_scans(fs):
    """数两阶段与全量各扫了多少枚核（包 `_scan_full` 取入参长度，不改行为）。"""
    box = {"n": 0, "labels": 0}
    orig = TGD.TencentGridDetector._scan_full
    full_cores = two_cores = two_labels = 0

    def counted(self, img, entries, is_grey, has_btn):
        box["n"] += len(entries)
        box["labels"] += len({e[0] for e in entries})
        return orig(self, img, entries, is_grey, has_btn)

    TGD.TencentGridDetector._scan_full = counted
    try:
        for det, face, avail, styles in fs:
            box["n"] = 0
            _score_with(det, face, avail, styles, False)
            full_cores += box["n"]
            box["n"] = box["labels"] = 0
            _score_with(det, face, avail, styles, True)
            two_cores += box["n"]
            two_labels += box["labels"]
    finally:
        TGD.TencentGridDetector._scan_full = orig
    return full_cores, two_cores, two_labels


def _capture_refined(det, face, avail, styles):
    """跑一次 ON 打分，返回生产 `_refine_labels` 本次给出的精算集（未走两阶段则 None）。

    取的是生产函数的**返回值**而不是探针自己重算：重算等于用一份副本去验收原件。
    还原时必须重新包成 `staticmethod`——直接赋裸函数会让下一次 `self._refine_labels(...)`
    把 self 绑成第一个参数（那会把后面的用例全弄坏）。"""
    seen = []
    orig = TGD.TencentGridDetector._refine_labels

    def spy(coarse, forced, top_k=TOP_K, margin=MARGIN):
        out = orig(coarse, forced, top_k, margin)
        seen.append(set(out))
        return out

    TGD.TencentGridDetector._refine_labels = staticmethod(spy)
    try:
        _score_with(det, face, avail, styles, True)
    finally:
        TGD.TencentGridDetector._refine_labels = staticmethod(orig)
    return seen[-1] if seen else None


# --------------------------------------------------------------- C 组（合成核）
CORE_ROWS, CORE_COLS = TGD.CORE_H, TGD.CORE_W          # 88 x 56
BTN_ROWS = TGD.CORE_Y1 - TGD.CORE_BTN_Y0               # 60
SYNTH_LABELS = ("1m", "5m", "6m", "7s", "8m", "9p")     # 都不在闭包表里，便于单点归因


def _synth_entry(lbl, seed):
    """造一枚形状合规、内容可控的核：plain 88x56、btn 60x56，灰/彩各一份。

    图案按 seed 唯一（`arange % (37*seed+11)`），所以不同标签的分数天然拉开，
    「谁进精算集」在这套夹具里是可控量而不是噪声。"""
    n = CORE_ROWS * CORE_COLS
    pat = (np.arange(n).reshape(CORE_ROWS, CORE_COLS) % (37 * seed + 11)).astype(np.uint8)
    btn = pat[CORE_ROWS - BTN_ROWS:CORE_ROWS, :]
    bgr = cv2.merge([pat, pat, pat])
    bgr_btn = cv2.merge([btn, btn, btn])
    pat_g = cv2.normalize(pat, None, 0, 255, cv2.NORM_MINMAX)
    btn_g = cv2.normalize(btn, None, 0, 255, cv2.NORM_MINMAX)
    return (lbl, "tencent", bgr_btn, bgr, btn_g, pat_g)


def _synth_det():
    """只带打分所需字段的 detector（绕开 bank 装载，`__init__` 的默认值手动补）。"""
    det = TGD.TencentGridDetector.__new__(TGD.TencentGridDetector)
    det.active_styles = None
    det.full_honors = False
    det._mode_tiles = None
    det.prefilter = True
    det._coarse_pair = None
    det._cores = [_synth_entry(l, i) for i, l in enumerate(SYNTH_LABELS)]
    return det


def _synth_face(seed):
    """把某一枚核的图案原样嵌进打分窗口 ⇒ 该标签全尺寸分 = 1.0。"""
    face = np.zeros((TGD.FACE_H, TGD.FACE_W, 3), np.uint8)
    pat = (np.arange(CORE_ROWS * CORE_COLS).reshape(CORE_ROWS, CORE_COLS)
           % (37 * seed + 11)).astype(np.uint8)
    win = face[10:110, 6:74]          # 与 `_score_face` 的窗口一致：100 x 68
    win[0:CORE_ROWS, 0:CORE_COLS] = cv2.merge([pat, pat, pat])
    return face


class TestCoarseTrackIdentity(unittest.TestCase):
    """C 组：粗筛核轨必须以**对象身份**为准（离线评测会就地替换 `det._cores`）。"""

    def test_track_is_aligned_with_full_cores(self):
        det = _synth_det()
        src, coarse = det._coarse_tracks()
        self.assertEqual(len(det._cores), len(coarse), "粗筛轨与全尺寸轨长度不等")
        self.assertEqual([c[0] for c in src], [c[0] for c in coarse],
                         "粗筛轨错位：标签顺序与全尺寸轨不一致")
        for f, c in zip(det._cores, coarse):
            for i in range(2, 6):
                self.assertEqual(
                    (max(1, int(f[i].shape[0] * SCALE)), max(1, int(f[i].shape[1] * SCALE))),
                    c[i].shape[:2], f"{f[0]} 的第 {i} 个核没按 PREFILTER_SCALE 缩")

    def test_track_is_reused_when_cores_unchanged(self):
        # 每枚 face 都重算 594 次 resize 的话，这一刀的时间就白给了。
        det = _synth_det()
        p1 = det._coarse_tracks()
        p2 = det._coarse_tracks()
        self.assertIs(p1[1][0], p2[1][0], "核没变却重建了粗筛轨（粗筛成本回到每枚一次）")

    def test_same_length_swap_rebuilds_track_and_stays_consistent(self):
        det = _synth_det()
        face = _synth_face(0)
        self.assertEqual(_final(det, face, _score_with(det, face, None, None, False)),
                         _final(det, face, _score_with(det, face, None, None, True)),
                         "前提不成立：合成夹具在两阶段下就已经与全量不一致")
        # 长度不变、内容变（这正是「只比长度」那种校验会漏掉的一类替换）：
        # 把 index 0 换成同标签的另一枚核，此后 1m 的分应该塌下去。
        det._cores = list(det._cores)
        det._cores[0] = _synth_entry("1m", 21)
        src, coarse = det._coarse_tracks()
        self.assertTrue(all(a is b for a, b in zip(src, det._cores)),
                        "核轨没按对象身份重建（拿旧粗筛核配新全尺寸核 = 错位）")
        self.assertEqual(_final(det, face, _score_with(det, face, None, None, False)),
                         _final(det, face, _score_with(det, face, None, None, True)),
                        "同长度替换核轨后，两阶段与全量不一致（粗筛轨错位）")
        two = _score_with(det, face, None, None, True)
        full = _score_with(det, face, None, None, False)
        self.assertLess(two.get("1m", 0.0), 0.90,
                        "1m 的核已经被换掉，两阶段却还给它高分：精算用的是旧轨")
        self.assertAlmostEqual(full["1m"], two["1m"], places=6)

    def test_grey_and_btn_branches_use_the_same_pairing(self):
        """粗筛与精算必须取同一个组合（灰/彩 × 顶标），否则排序与分数无关。"""
        det = _synth_det()
        face = _synth_face(2)
        full = _score_with(det, face, None, None, False)
        two = _score_with(det, face, None, None, True)
        self.assertIn(SYNTH_LABELS[2], full)
        self.assertEqual(full[SYNTH_LABELS[2]], two[SYNTH_LABELS[2]],
                         "同一枚牌在两条路上取的核组合不同")


# ------------------------------------------------------------- 接线契约（D 组）
def _decide_mentioned_labels():
    """从 `_decide` 源码里抽出它实际成对读写的标签。

    两个来源都要覆盖：`best_lbl in [...]` 决定哪条判决生效，`scores.get("xy")`
    决定它读谁的分。两处出现的标签集合必须**正好**等于闭包表覆盖的标签集合 ——
    新增了判决忘了加表、或加了表却没有对应判决，都会被这条问住。"""
    body = _detector_method_src("_decide")
    read = set(re.findall(r'scores\.get\("([1-9][mpsz])"', body))
    rule = set()
    for grp in re.findall(r'best_lbl in \[([^\]]*)\]', body):
        rule |= set(re.findall(r'"([1-9][mpsz])"', grp))
    for one in re.findall(r'best_lbl == "([1-9][mpsz])"', body):
        rule.add(one)
    return read, rule


WIRING_CONTRACTS = [
    ("精算必须用**全尺寸**打分图，不许拿粗筛图出分",
     "                scores = self._scan_full(\n"
     "                    img, [e for e in entries if e[0] in keep], is_grey, has_btn)",
     "                scores = self._scan_full(\n"
     "                    c_img, [e for e in entries if e[0] in keep], is_grey, has_btn)"),
    ("精算集必须来自 `_refine_labels`（闭包与分差带只有那一个出口）",
     "                keep = self._refine_labels(coarse, forced)",
     "                keep = {lbl for lbl, _ in sorted(coarse.items(), key=lambda kv: -kv[1])[:3]}"),
    ("粗筛与精算必须共用同一道候选/风格过滤（同序成对筛出两份轨）",
     "            entries.append(e)\n"
     "            if coarse_src is not None:\n"
     "                coarse_entries.append(coarse_src[i])",
     "            entries.append(e)\n"
     "            if coarse_src is not None:\n"
     "                coarse_entries.append(coarse_src[0])  # 偷懒：永远配第一枚粗筛核"),
    ("prefilter 关掉时必须逐字退回全量扫（对照开关要真换路）",
     "            src, coarse_src = self._cores, None",
     "            src, coarse_src = self._coarse_tracks()  # 关不掉两阶段"),
    ("粗筛轨按对象身份校验，长度相同而内容不同也要重建",
     "                    a is b for a, b in zip(src, self._cores)):",
     "                    True):  # 只比长度（LOFO 替换后就会错位）"),
]

WIRING_SRC = {"detector": DET_SRC}


def _assert_wiring_holds(srcs):
    for name, good, bad in WIRING_CONTRACTS:
        src = srcs["detector"]
        assert good in src, f"接线断了/改了写法：{name}"
        assert src.count(good) == 1, f"{name}：契约定位不唯一，改错地方也没人知道"
        assert bad not in src, f"坏写法已经在场上了：{name}"


class TestWiring(unittest.TestCase):
    def test_contracts_are_wired(self):
        _assert_wiring_holds(WIRING_SRC)

    def test_closure_table_matches_decide(self):
        read, rule = _decide_mentioned_labels()
        covered = {l for p in PAIRS for l in p}
        self.assertEqual(covered, read,
                         "`_decide` 读的分与闭包表覆盖的标签不一致：新增/删除判决必须同步 "
                         "ADJACENT_PAIRS，否则结构裁决会在两阶段下静默失效")
        self.assertTrue(rule <= covered,
                        f"`_decide` 里出现了闭包表没有的判决标签：{sorted(rule - covered)}")

    def test_prefilter_constants_are_the_ones_measured(self):
        # 这三个值是被 `build/prefilter_gt.txt` 的量级钉住的，动它们 = 重做决定。
        self.assertEqual((0.5, 3, 0.06), (SCALE, TOP_K, MARGIN),
                         "粗筛参数被改动：请重跑 probe_prefilter_gt.py 并同步本守卫的 A 组表")

    def test_decide_is_still_a_pure_function_over_scores(self):
        # 这一刀只该动候选集。`_decide` 若开始自己扫核/看 prefilter，B 组的对拍口径
        # （同一枚 face 两次打分）就不再覆盖它。
        body = _detector_method_src("_decide")
        self.assertNotIn("matchTemplate", body, "_decide 开始自己打分了：对拍口径失效")
        self.assertNotIn("prefilter", body, "_decide 读到了开关：A/B 不再是同一条码路")


class TestKnownBoundary(unittest.TestCase):
    """如实记录这一刀**兜不住**什么。"""

    def test_coverage_is_this_batch_only(self):
        # 对拍的样本就是 gt/shots.json 里 verified 的这 37 帧（腾讯 + 血流红中）。
        # 别的平台/牌风没有逐枚对拍过：那里的失稳由「分差带」把精算集撑大来兜，
        # 极端时退化回全量——不是由本守卫兜。
        n = len(_load_verified())
        self.assertGreaterEqual(n, 30, f"GT verified 帧只剩 {n} 条，对拍样本已不成立")
        self.assertGreaterEqual(len(faces()), MIN_FACES)

    def test_escape_hatch_exists(self):
        # 唯一逃生口：整刀关掉回到全量扫。删掉它就没有 A/B，也没有线上回退。
        self.assertIn("self.prefilter = True", DET_SRC)
        self.assertTrue(_synth_det().prefilter, "开关默认值不是 True")

    def test_prefilter_does_not_touch_decision_rules(self):
        # 边界：这一刀不改变任何判据本身，所以它**不该**让任何一条结构判决变强或变弱。
        body = _detector_method_src("_score_face")
        self.assertNotIn("_decide", body,
                         "_score_face 里开始做裁决：对拍不再只比候选集，边界描述失效")


# ------------------------------------------------------------- 变异检验夹具
def _guard_breaks(assertion) -> bool:
    """跑一段守卫断言，返回它是否**变红**。只认 AssertionError：其它异常说明变异
    模板本身不合法（语法/切片错），那要当测试错误抛出去，不能算「拦住了」。"""
    try:
        assertion()
        return False
    except AssertionError:
        return True


def _detector_method_src(name: str) -> str:
    """从识别器源码切出一个方法的完整定义（含 4 格缩进）。

    下一个成员的起始标记可能是 `def` 也可能是装饰器：只找 `def` 会把上一个
    `@staticmethod` 扫进切片，exec 时多一条落单装饰器 → SyntaxError（同
    `test_dingque_stability_guard.py` 踩过的坑）。"""
    head = f"    def {name}("
    start = DET_SRC.index(head)
    rest = DET_SRC[start + len(head):]
    ends = [rest.index(mark) for mark in ("\n    def ", "\n    @") if mark in rest]
    if not ends:
        raise AssertionError(f"切不出 {name} 的方法边界（后面没有下一个成员）")
    return DET_SRC[start:start + len(head) + min(ends)]


def _mutated_refine(old: str, new: str):
    body = _detector_method_src("_refine_labels")
    if old not in body:
        raise AssertionError(f"变异模板过期：{old[:70]}")
    mutated = body.replace(old, new, 1)
    if mutated == body:
        raise AssertionError(f"变异无效：{old[:70]}")
    ns = dict(vars(TGD))
    exec(compile(textwrap.dedent(mutated), "<mutant:_refine_labels>", "exec"), ns)
    fn = ns["_refine_labels"]
    return lambda coarse, forced, top_k=TOP_K, margin=MARGIN: fn(coarse, forced, top_k, margin)


BEHAVIOR_MUTANTS = [
    ("砍掉相邻类闭包（本刀唯一会造成**错判**而不是**慢**的地方）",
     "        for lbl in seeds:\n            out.update(ADJACENCY.get(lbl, ()))",
     "        pass  # 不做闭包：粗筛说了算",
     _assert_closure_holds),
    ("砍掉分差带（粗筛自己都分不清高下时仍然敢淘汰）",
     "            seeds |= {lbl for lbl, s in coarse.items() if s > lead - margin}",
     "            seeds |= set()",
     _assert_refine_table),
    ("top_k 写成 0（第一名之外全靠运气）",
     "        seeds = {lbl for lbl, _ in ranked[:top_k]}",
     "        seeds = {ranked[0][0]} if ranked else set()",
     _assert_topk_kept),
    ("忽略 forced（缩过之后放不下窗口的核被当落后淘汰）",
     "        out = set(seeds) | set(forced)",
     "        out = set(seeds)",
     _assert_refine_table),
    ("分差带边界从 > 松成 >=（把恰好等于带宽的候选吸进来）",
     "if s > lead - margin}",
     "if s >= lead - margin}",
     _assert_refine_table),
]

PIPELINE_MUTANTS = [
    # 真链路档：换掉方法本体（只改坏一处），再用 B/C 组同一条断言跑同一批 face。
    ("精算拿粗筛图出分（分数会整片漂移，最终裁决不再等价）",
     "                scores = self._scan_full(\n"
     "                    img, [e for e in entries if e[0] in keep], is_grey, has_btn)",
     "                scores = self._scan_full(\n"
     "                    c_img, [e for e in entries if e[0] in keep], is_grey, has_btn)",
     TestRealFrameConsistency, "test_every_face_final_output_is_identical", "_score_face"),
    ("粗筛轨只比长度不比对象身份（LOFO 同长度替换后错位）",
     "            if len(src) == len(self._cores) and all(\n"
     "                    a is b for a, b in zip(src, self._cores)):",
     "            if len(src) == len(self._cores) and all(\n"
     "                    True for _a, _b in zip(src, self._cores)):",
     TestCoarseTrackIdentity,
     "test_same_length_swap_rebuilds_track_and_stays_consistent", "_coarse_tracks"),
    ("粗筛轨缓存永不失效（核被替换后继续用旧轨）",
     "            if len(src) == len(self._cores) and all(\n"
     "                    a is b for a, b in zip(src, self._cores)):",
     "            if True:  # 不校验，拿了旧轨就用",
     TestCoarseTrackIdentity,
     "test_same_length_swap_rebuilds_track_and_stays_consistent", "_coarse_tracks"),
]


def _install_mutated_method(name: str, old: str, new: str):
    """把识别器某个方法换成「只改坏一处」的副本，返回 (原名, 原实现) 供回滚。

    用 `textwrap.dedent` 而不是 `lstrip()`：切片带着类内 4 格缩进，lstrip 只去掉
    第一行的前导空格，剩下的体依旧缩着 → exec 直接 IndentationError（那会被当成
    「没接住」而不是「模板错」，误报一次就浪费一轮实测）。"""
    body = _detector_method_src(name)
    if old not in body:
        raise AssertionError(f"变异模板过期：{name} :: {old[:70]}")
    mutated = body.replace(old, new, 1)
    if mutated == body:
        raise AssertionError(f"变异无效：{name} :: {old[:70]}")
    ns = dict(vars(TGD))
    exec(compile(textwrap.dedent(mutated), "<mutant:%s>" % name, "exec"), ns)
    fn = ns[name]
    cls = TGD.TencentGridDetector
    orig = getattr(cls, name)
    setattr(cls, name, fn)
    return name, orig


class TestMutationControls(unittest.TestCase):
    """--mutate：把坏写法塞回生产代码，本守卫必须立刻变红。

    三档各自证到什么，如实标注：
      BEHAVIOR：只改坏 `_refine_labels` 的一处，用 A 组同一条断言跑同一批输入——
        证「那段判据真的在决定精算集」，而不是「那段字还在」。
      PIPELINE：把方法本体换成改坏的副本，再跑 B/C 组同一条真帧/合成断言——
        证这条链路是承重的（换掉就红），并顺带证明 B/C 组真的走到了被改的那段。
      WIRING：只能证「契约字符串是承重的」。行为层兜底在上面两档与 eval_base。
    """

    def test_behavior_mutants_are_caught(self):
        missed = []
        for name, old, new, assertion in BEHAVIOR_MUTANTS:
            refine = _mutated_refine(old, new)
            if _guard_breaks(lambda: assertion(refine)):
                print(f"[mutate] {name}：同一条断言立刻变红——已接住")
            else:
                missed.append(name)
        self.assertEqual([], missed, "这些变异活了下来：精算集其实不是由这段代码决定的")

    def test_pipeline_mutants_are_caught(self):
        missed = []
        for name, old, new, cls, case, method in PIPELINE_MUTANTS:
            holder = [None]
            try:
                holder[0] = _install_mutated_method(method, old, new)
                tc = cls(case)
                try:
                    getattr(tc, case)()
                    missed.append(name)          # 坏实现还能让断言绿 = 没人接住
                except AssertionError:
                    print(f"[mutate] {name}：{cls.__name__} 的同一条断言变红——已接住")
            finally:
                if holder[0]:
                    setattr(TGD.TencentGridDetector, holder[0][0], holder[0][1])
        self.assertEqual([], missed,
                         "这些主链路变异活了下来：B/C 组其实没跑到被改的那段（假接线）")

    def test_wiring_mutants_are_caught(self):
        missed = []
        for name, good, bad in WIRING_CONTRACTS:
            srcs = dict(WIRING_SRC)
            srcs["detector"] = srcs["detector"].replace(good, bad, 1)
            self.assertNotEqual(srcs["detector"], WIRING_SRC["detector"],
                                f"变异模板过期：{name}")
            if _guard_breaks(lambda: _assert_wiring_holds(srcs)):
                print(f"[mutate] {name}：接线契约变红——已接住（行为层由 B/C 组兜底）")
            else:
                missed.append(name)
        self.assertEqual([], missed, "这些接线变异活了下来：契约字符串其实不承重")


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestRefineSet, TestRealFrameConsistency, TestCoarseTrackIdentity,
                TestWiring, TestKnownBoundary):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
