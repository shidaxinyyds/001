# -*- coding: utf-8 -*-
"""张数候选序贯早停守卫：省掉「第二档那一整行」，但必须逐格证到它没改变输出。

背景（r6lat 延迟第二刀，build/cost_split.txt + build/probe_hand_candidates.txt）：
单帧 process 均值 443.0ms，其中「手牌行检测+分类」211.3ms（47.7%）。成本结构是
**每档张数候选都切一整行并全量打分**：41 帧里典型一次 strip 提交 27 枚（13 张 +
14 张两档），而真张数只有一档。总打分枚数 1252 枚，其中约四成是给落选档位打的。

剪枝依据不是「几何估计看起来挺准」，是逐帧量到的仲裁结果：
  · 第一档（离 `bw/节距` 最近的那档）均分 >= 0.55 的 44 次里，第二档赢 **0** 次；
  · 第二档赢的 3 次全部落在重扫帧，第一档均分只有 0.413 / 0.283 / 0.238，
    即「错位半格 → 整行 NCC 塌到 0.55 以下」这条物理判据正是下面那条重扫门本身。
所以新逻辑是：先只切第一档，**不达标就直接走原来的全候选全模板重扫**。0.55 这个
门限一个都没改，只是把它往前推了一格复用。
中途写过一级「不达标先补跑第二档」，实测证伪后删掉了：第二档赢的 3 帧均分全
都 < 0.55，一帧都没躲开重扫；而重扫候选集是它的超集、风格集在已声明平台时同一个
（`_resolve_styles(None)` → 平台白名单）。变异检验（把补跑整条关掉）41 帧输出仍与
双档逐格全等——一条「从未生效过的兜底」就是用户说的坑，不留。

守卫分四层，每层都是能单独失效的：
  A 等价对拍：41 帧上「早停」与「双档（`hand_count_dual=True`）」的输出逐格全等
    （rect+label+conf）。这条是"零回归"的唯一诚实证据，不是快照，是当场对拍。
  B 成本牙：早停确实少切了整行（总枚数降 ≥ 25%，且过半帧只提交了一档）。
    没有这条，A 组可以靠「把早停删掉」轻松全绿 —— 那就是白改。
  C 兜底牙：第一档不达标的帧必须回到全量重扫（重扫门没被顺手删）。
  D 探针分支不早停：未声明平台时两档照跑（那条路上没有「用户已声明平台」这个
    硬事实顶着，少跑一档就是拿正确率换速度）。

运行：
  py -3.10 -X utf8 localtest/test_hand_count_seq_guard.py
  py -3.10 -X utf8 localtest/test_hand_count_seq_guard.py --mutate
"""
from __future__ import annotations

import json
import os
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
SHOTS = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")
REAL_DIR = os.path.join(HERE, "shots_tuyou_select")
REAL_FILES = ("s1_select_cd17.jpg", "s2_select_cd07.jpg",
              "s3_select_cd01.jpg", "s4_after_swap_cd04.jpg")

DET_SRC = open(DET_PY, encoding="utf-8").read()

import recognition.tencent_grid_detector as TGD_MOD  # noqa: E402  (变异要它的命名空间)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from modes import available_set  # noqa: E402

PASS_MEAN = 0.55          # 与生产那条重扫门同源，改这里必须同时改生产


# --------------------------------------------------------------------- 夹具
def _jpeg_degraded(img):
    """与 eval_base/cost_split 同一预处理：端上拿到的就是 q50 的 jpeg 帧。"""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def load_frames():
    out = []
    if not os.path.exists(GT_PATH):
        return out
    with open(GT_PATH, encoding="utf-8") as fp:
        gt = json.load(fp)["shots"]
    for s in gt:
        if not s.get("verified"):
            continue
        img = cv2.imread(os.path.join(SHOTS, s["file"]))
        if img is None:
            continue
        out.append((s["file"], _jpeg_degraded(img)))
    for f in REAL_FILES:
        img = cv2.imread(os.path.join(REAL_DIR, f))
        if img is not None:
            out.append(("real_" + f, img))
    return out


FRAMES = load_frames()

_DET = None


def det() -> TencentGridDetector:
    """一个实例跑完全部帧：模板库加载要好几秒，而 detect_hand_strip 每帧自洗状态。
    平台/玩法按生产口径推入（与 Engine._apply_platform_styles 同两条调用）。"""
    global _DET
    if _DET is None:
        d = TencentGridDetector()
        d.set_platform_styles("tencent")
        d.set_mode_tiles(available_set("sc_hz"))
        _DET = d
    return _DET


def _snap(dets, grid, runs):
    """把一次 strip 的结果压成可比较的元组。conf 取 6 位：早停与双档理应是
    **同一些格片打同一个分**，不是「差不多」，所以留 6 位足以暴露真实差异，
    又不会被浮点末位噪声绊倒。"""
    return {
        "out": tuple((tuple(int(v) for v in r), lbl, round(float(sc), 6))
                     for r, lbl, sc in dets),
        "grid": None if grid is None else (round(float(grid[0]), 3),
                                           round(float(grid[1]), 4), int(grid[2])),
        "runs": tuple(tuple((int(k), round(float(m), 6)) for k, m in run) for run in runs),
        "tiles": sum(k for run in runs for k, _m in run),
    }


def strip_once(img, dual: bool, method=None, declared="tencent"):
    """跑一次手牌行检测。dual=True 走旧的双档；method 是变异副本时用调用方给的函数。"""
    d = det()
    d.set_platform_styles(declared)
    d.hand_count_dual = dual
    if method is None:
        dets = d.detect_hand_strip(img)
    else:
        dets = method(d, img)
    return _snap(dets, d.last_hand_grid, [list(r) for r in d.last_count_runs])


# 生产两档的结果各跑一次，全部断言共用这份缓存（82 次 strip 已经够慢了）。
CACHE = {}


def both():
    if not CACHE:
        for name, img in FRAMES:
            CACHE[name] = {"dual": strip_once(img, True), "seq": strip_once(img, False)}
    return CACHE


def fallback_names():
    """双档口径下第一档就不达标的那几帧 —— 兜底路的牙只可能由它们证明。"""
    bad = []
    for name, r in both().items():
        run = r["dual"]["runs"]
        if run and run[0][0][1] < PASS_MEAN:
            bad.append(name)
    return bad


# ----------------------------------------------------------------- 断言（共用）
def assert_paths_identical(call_seq, frames):
    """A 组：早停的输出必须与双档逐格全等。call_seq(img) -> snapshot。"""
    diff = []
    for name, img in frames:
        base = both()[name]["dual"]
        got = call_seq(img)
        if got["out"] != base["out"] or got["grid"] != base["grid"]:
            diff.append((name, len(base["out"]), len(got["out"]),
                         base["grid"], got["grid"]))
    assert not diff, "早停改变了识别结果（应当只在第一档达标时省一行）：%s" % diff[:5]


def assert_early_stop_saves(call_seq, frames):
    """B 组：省下的那一整行必须真的没打。枚数不降就说明早停是纸上的。"""
    tot_dual = tot_seq = single = 0
    for name, _img in frames:
        base = both()[name]["dual"]
        got = call_seq(_img)
        tot_dual += base["tiles"]
        tot_seq += got["tiles"]
        if len(got["runs"]) == 1 and len(got["runs"][0]) == 1:
            single += 1
    assert tot_seq < tot_dual, (
        "早停后总分枚数一点没少（双档 %d vs 早停 %d）：剪枝没接线" % (tot_dual, tot_seq))
    assert tot_seq <= tot_dual * 0.75, (
        "只省了 %d/%d 枚（%.1f%%），不到双档的 75%%：这一刀几乎没砍到东西" % (
            tot_seq, tot_dual, 100.0 * tot_seq / max(tot_dual, 1)))
    assert single * 2 >= len(frames), (
        "只提交一档的帧才 %d/%d：早停几乎没生效（那一刀本应砍掉整行冗余）" % (
            single, len(frames)))


def assert_single_row_when_first_row_passes(call_seq, frames):
    """B 组的逐帧版：第一档达标的帧，只允许切一行。"""
    violate = []
    for name, img in frames:
        base = both()[name]["dual"]
        first_mean = base["runs"][0][0][1] if base["runs"] else 0.0
        if first_mean < PASS_MEAN:
            continue
        got = call_seq(img)
        if len(got["runs"]) != 1 or len(got["runs"][0]) != 1:
            violate.append((name, round(first_mean, 3), got["runs"]))
    assert not violate, "第一档明明达标却还去切第二档：%s" % violate[:5]


def assert_fallback_armed(call_seq, frames):
    """C 组：第一档不达标的帧必须回到全量重扫，且重扫后的结果与双档一致。

    这一层同时证两件事：重扫门没被删（`len(runs) >= 2`），以及重扫真的把
    本帧接住了（输出全等）。少了前者，早停就是「失手帧只能拿第13枚错位格交差」。"""
    hit = 0
    for name, img in frames:
        base = both()[name]["dual"]
        first_mean = base["runs"][0][0][1] if base["runs"] else 0.0
        if first_mean >= PASS_MEAN:
            continue
        got = call_seq(img)
        assert len(got["runs"]) >= 2, (
            "%s：第一档只有 %.3f（不达标）却没有回到全量重扫，失手帧只能拿一行错位格交差" % (
                name, first_mean))
        assert got["out"] == base["out"] and got["grid"] == base["grid"], (
            "%s：重扫后的结果与双档不一致，兜底形同虚设" % name)
        hit += 1
    assert hit >= 1, (
        "样本里一帧『第一档不达标』都没有，C 组是空跑的：兜底有没有接线根本没人知道")


def assert_probe_branch_does_not_early_stop(call_seq_probe, frames):
    """D 组：未声明平台（探针路由）时不许早停。"""
    for name, img in frames:
        got = call_seq_probe(img)
        assert len(got["runs"][0]) >= 2, (
            "%s：探针分支只跑了 %d 档，探针那条路没有『用户已声明平台』顶着，"
            "少跑一档就是拿正确率换速度" % (name, len(got["runs"][0])))


# --------------------------------------------------------------------- 默认套件
@unittest.skipIf(not FRAMES, "缺 37 帧 GT 或真机四帧夹具")
class TestHandCountSeq(unittest.TestCase):

    def setUp(self):
        self.frames = FRAMES
        self.seq = lambda img: strip_once(img, False)      # noqa: E731

    def test_seq_output_is_identical_to_dual(self):
        assert_paths_identical(self.seq, self.frames)

    def test_early_stop_actually_saves_a_row(self):
        assert_early_stop_saves(self.seq, self.frames)

    def test_only_one_row_when_first_row_passes(self):
        assert_single_row_when_first_row_passes(self.seq, self.frames)

    def test_failed_first_row_goes_back_to_the_full_rescan(self):
        assert_fallback_armed(self.seq, self.frames)

    def test_probe_branch_still_tests_every_candidate(self):
        assert_probe_branch_does_not_early_stop(
            lambda img: strip_once(img, False, declared=None), self.frames[:6])


# --------------------------------------------------------------------- 接线契约
WIRING_CONTRACTS = [
    ("对照口子默认关闭：生产走早停（打开它等于整刀作废，成本回到双档）",
     "    hand_count_dual: bool = False",
     "    hand_count_dual: bool = True"),
    ("早停只在「声明了平台 + 没拨回旧行为」时开启",
     "            seq_first = not self.hand_count_dual",
     "            seq_first = True"),
    ("第一档只切一档，不达标由下面那条全量重扫接（实测证伪后不留「只补一档」那级）",
     "        first_cands = [cand_counts[0]] if (seq_first and len(cand_counts) > 1) else list(cand_counts)",
     "        first_cands = list(cand_counts)"),
    ("探针分支明确不早停",
     "            seq_first = False",
     "            seq_first = True"),
    ("失手重扫（全候选全模板）那条兜底不许被这次改动顺手删掉",
     "        if best_standing_mean < 0.55 and probe_styles is not None:",
     "        if False and probe_styles is not None:"),
]

# 负契约：那条已被实测证伪的补跑不许被「好心地」加回来（加回来就是在失手帧上
# 多切一整行，且 41 帧里没有任何输出会因此不同——没人能为它辩护）。
FORBIDDEN = [
    ("只补第二档那级（41 帧实测从未生效，候选集又是重扫的子集）",
     "            rest = [k for k in cand_counts[1:] if k not in first_cands]"),
]


def _assert_no_forbidden(src):
    for name, bad in FORBIDDEN:
        assert bad not in src, f"那条从未生效的兜底又被加回来了：{name}"


def _assert_wiring_holds(src):
    for name, good, bad in WIRING_CONTRACTS:
        assert good in src, f"接线断了/改了写法：{name}"
        assert src.count(good) == 1, f"{name}：契约定位不唯一，改错地方也没人知道"
        assert bad not in src, f"坏写法已经在场上了：{name}"
    _assert_no_forbidden(src)


class TestWiring(unittest.TestCase):

    def test_contracts_are_wired(self):
        _assert_wiring_holds(DET_SRC)


# ------------------------------------------------------------------ 变异检验
def _guard_breaks(assertion) -> bool:
    try:
        assertion()
        return False
    except AssertionError:
        return True


def _det_method_src(name: str) -> str:
    head = f"    def {name}("
    start = DET_SRC.index(head)
    end = DET_SRC.index("\n    def ", start + len(head))
    return DET_SRC[start:end]


def _mutated_strip(old: str, new: str):
    """把 detect_hand_strip 改坏一处，返回可绑定到真实实例的函数。

    只 exec 这一个方法（拿 det 模块的命名空间当 globals），否则每造一个变异体
    都要重载一遍模板库；这个做法与 `test_real_phase_frames_guard.py` 同一条范式。
    方法体里的嵌套 `def`（_scan_window/_try_counts）缩进 8 格，不会被 `"\\n    def "`
    误判成下一个方法。"""
    body = _det_method_src("detect_hand_strip")
    if old not in body:
        raise AssertionError(f"变异模板过期：{old[:70]}")
    mutated = body.replace(old, new, 1)
    if mutated == body:
        raise AssertionError(f"变异无效：{old[:70]}")
    ns = dict(vars(TGD_MOD))
    exec(compile(textwrap.dedent(mutated), "<mutant:detect_hand_strip>", "exec"), ns)
    return ns["detect_hand_strip"]


class TestMutationControls(unittest.TestCase):
    """--mutate：把坏写法塞回生产代码，本守卫必须立刻变红。

    每个变异只在**能证到它的那几帧**上跑（失手帧 / 达标帧 / 探针帧），既快，
    也说清了「这条牙是谁咬住的」。断言函数与默认套件用的是同一批，不另写一套。
    """

    BEHAVIOR_MUTANTS = [
        ("全量重扫门关掉（第一档失手就只交那一行错位格）→ C 组与等价对拍都该红",
         "        if best_standing_mean < 0.55 and probe_styles is not None:",
         "        if False and probe_styles is not None:",
         "fallback", "tencent"),
        ("早停形同虚设（永远切两档）→ 成本牙该红",
         "        first_cands = [cand_counts[0]] if (seq_first and len(cand_counts) > 1) else list(cand_counts)",
         "        first_cands = list(cand_counts)",
         "cost", "tencent"),
        ("早停切了，但切的是错的那一档（拿次近档当第一档）→ 等价对拍该红",
         "        first_cands = [cand_counts[0]] if (seq_first and len(cand_counts) > 1) else list(cand_counts)",
         "        first_cands = [cand_counts[-1]] if (seq_first and len(cand_counts) > 1) else list(cand_counts)",
         "identical", "tencent"),
        ("探针分支也被早停（拿正确率换速度）→ D 组该红",
         "            seq_first = False",
         "            seq_first = True",
         "probe", None),
    ]

    def _frames(self, kind):
        if kind == "fallback":
            names = fallback_names()
            return [(n, img) for n, img in FRAMES if n in names]
        good = [(n, img) for n, img in FRAMES
                if both()[n]["dual"]["runs"] and
                both()[n]["dual"]["runs"][0][0][1] >= PASS_MEAN]
        if kind in ("cost", "identical"):
            return good[:12]
        return FRAMES[:4]

    def test_mutants_are_caught(self):
        missed = []
        for name, old, new, kind, declared in self.BEHAVIOR_MUTANTS:
            fn = _mutated_strip(old, new)
            frames = self._frames(kind)
            if not frames:
                missed.append(name + "（样本里没有能证到它的帧，无法证伪）")
                continue
            call = (lambda img, _fn=fn, _d=declared: strip_once(img, False,
                                                                method=_fn, declared=_d))
            checks = []
            if kind == "fallback":
                checks = [lambda: assert_fallback_armed(call, frames),
                          lambda: assert_paths_identical(call, frames)]
            elif kind == "cost":
                checks = [lambda: assert_early_stop_saves(call, frames),
                          lambda: assert_single_row_when_first_row_passes(call, frames)]
            elif kind == "identical":
                checks = [lambda: assert_paths_identical(call, frames)]
            else:
                checks = [lambda: assert_probe_branch_does_not_early_stop(call, frames)]
            broke = any(_guard_breaks(c) for c in checks)
            det().set_platform_styles("tencent")     # 变异跑完复位，别污染后续
            if broke:
                print(f"[mutate] {name}：同一条断言立刻变红——已接住")
            else:
                missed.append(name)
        self.assertEqual([], missed, "这些变异活了下来：守卫是空转的")


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestHandCountSeq, TestWiring):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
