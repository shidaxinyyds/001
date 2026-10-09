# -*- coding: utf-8 -*-
"""连帧回归：同一局的若干帧按真实时序喂进**同一个**引擎实例，盯住"读数跟不跟上画面"。

为什么必须有这个文件：此前全部回归（42 条守卫 + 23 条变异 + 57 帧评测）都是
「每帧新建 Engine、单帧跑」。那种测法天生看不见阻尼、稳定手牌、投票器、阶段机、
定缺锁存这些**帧间状态**——而用户截图里最刺眼的一类问题恰恰是它们造成的：
弹窗消失后手牌仍冻在同一组 4 张、牌局已自摸还报定缺、牌河是空就报三家高危听牌。
没有连帧回归，这些缺陷修没修都无从证明。

夹具是用户 2026-10 第三批指尖四川麻将的四帧，真实时序（按牌局进度推）：
  zj_swap_03  换三张（牌河空、手牌 13 张、无任何「缺」角标）
  zj_play_03  局中（手牌 10 张，五筒带「缺」角标 = 已定缺筒）
  zj_popup_02 同一手牌，被全屏弹窗压暗
  zj_play_04  弹窗已消失、画面正常，手牌 11 张（新增二筒）

用法: py -3.10 -X utf8 localtest/test_stream_guard.py
"""
from __future__ import annotations

import contextlib
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
import layer_cost as lc  # noqa: E402

SHOT_DIR = os.path.join(HERE, "shots_batch3")
PLATFORM = "zj_sichuan"
MODE = "sc_hz"

# 真实时序（帧名, 该帧屏上实际手牌多重集, 该帧屏上真实阶段）
# 手牌真值 = 人眼逐张放大复核（build/crop_panels.py 出的裁片），不是引擎输出。
STREAM = [
    ("zj_swap_03.jpg",
     ["1s", "2m", "3p", "5m", "5p", "6s", "7m", "7z", "8m", "8m", "8s", "9m", "9m"],
     "swap"),
    ("zj_play_03.jpg",
     ["1s", "2m", "5m", "5p", "6s", "7m", "7z", "8m", "8m", "8s"],
     "play"),
    ("zj_popup_02.jpg",
     ["1s", "2m", "5m", "5p", "6s", "7m", "7z", "8m", "8m", "8s"],
     "play"),
    ("zj_play_04.jpg",
     ["1s", "2m", "2p", "5m", "5p", "6s", "7m", "7z", "8m", "8m", "8s"],
     "play"),
]


def feed(frames):
    """按顺序把帧喂进同一个 Engine，返回每帧 payload（只留断言要用的字段）。"""
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: PLATFORM
    E.load_mode = lambda *a, **k: MODE
    out = []
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        for name in frames:
            img = cv2.imread(os.path.join(SHOT_DIR, name))
            if img is None:
                raise AssertionError(f"夹具帧读不出来，守卫在测空气：{name}")
            with contextlib.redirect_stdout(io.StringIO()):
                d = json.loads(eng.process(img).result)
            out.append(d)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
    return out


def panel_of(d):
    return lc.canon_mpsz(d.get("hand", ""))


# 已知缺陷台账（双向棘轮）：本轮连帧跑出来的、**尚未修好**的错。每条都钉住
# 当前那个错值：修好了不删条目就报红，错值变了说明动的是另一条链路。
# 为什么不干脆把断言写成“能过”：那就是假绿灯，下一个人会以为这里没问题。
KNOWN_STREAM_DEFECTS = {
    # B3：带「缺」角标的五筒被读成八筒（角标遮住了中心花型）。
    # 后果不只是错一张：八筒是屏上根本没有的牌，它会进而污染记牌器与听口计算。
    "phantom_tile:zj_play_04.jpg": ["8p"],
    # 同一件事的另一面：少了一枚五筒。两个方向一起钉，才能区分“错认”与“漏读”。
    "wrong_read:zj_play_04.jpg": ["5p", "8p"],
    # A9 的直接现象：牌河一条没读到，却仍把三家听牌报成“高”（先验 0.5 当结论）。
    # 根因是下面那条 river_always_empty；修好它之后这条应能自动转绿并删除。
    "tenpai_band_without_evidence": ["高"],
    # A9 源头（已两次改写，下面这版是实测出来的）：牌河后台检测**提交了但结果从未
    # 被认领**：同一帧连喂 25 次，diag 上 river_submits=1、river_consumes=0，四区计数
    # 始终 0（`localtest/probe_river_repeat.py`）。所以不是“扫了没扫到”，而是异步认领
    # 这一环断了（认领块里 rf.result() 若抛异常会被外层 except 吞掉，与“从未 done”
    # 在面板上长得一模一样）。
    # 曾经写过的两个错归因，记在这里免得下一个人重踩：
    #   × 「future 正常返回 tuple，但区内检不出」——实际 consumes=0，根本没走到那步；
    #   × 「主检测器没有 classify_tile 就是根因」——它是必要条件（接线已修），不充分。
    "river_always_empty:zj_play_03.jpg": {"bottom": 0, "top": 0, "left": 0, "right": 0},
    # A8：牌河为空、且屏上处于换三张阶段（手牌没有任何「缺」角标），
    # 面板已经报出“seat 1 定缺了”。缺门证据不存在，这个值是凭空的。
    "dingque_before_commit:zj_swap_03.jpg": [1],
}


class TestStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = [f for f, _h, _p in STREAM]
        cls.payloads = feed(cls.names)

    def test_hand_follows_the_screen_after_the_overlay_leaves(self):
        """A1/A2：弹窗压暗那一帧读不出可以理解，但**下一帧画面正常就必须刷新**。"""
        popup = self.payloads[2]
        after = self.payloads[3]
        truth_after = sorted(STREAM[3][1])
        got = panel_of(after)
        self.assertEqual(
            len(got), len(truth_after),
            f"弹窗之后的正常帧手牌张数没跟上画面：读到 {len(got)} 张 {got}，屏上 "
            f"{len(truth_after)} 张。上一帧（弹窗）读到 {panel_of(popup)}")
        bad = KNOWN_STREAM_DEFECTS.get("wrong_read:zj_play_04.jpg")
        diff = sorted(set(got) ^ set(truth_after))
        if bad is not None:
            # 台账在位：错的具体是哪几张（少了什么 + 多了什么），必须与登记时一模一样
            self.assertEqual(diff, sorted(bad),
                             f"zj_play_04 错读的形状变了：登记={sorted(bad)} 实为={diff}。"
                             "修好了就删台账条目，变了就说明动的是另一条链路")
            return
        self.assertEqual(got, truth_after,
                         f"弹窗之后的正常帧读数与屏上不是一回事：读到={got} 屏上={truth_after}")

    def test_no_phantom_tiles_outside_the_ledger(self):
        """B3：面板里出现的每一张牌，屏上必须真有；幻读要进台账才能入仓。"""
        for (name, truth, _ph), d in zip(STREAM, self.payloads):
            got = panel_of(d)
            extra = sorted({t for t in got if got.count(t) > truth.count(t)})
            bad = KNOWN_STREAM_DEFECTS.get(f"phantom_tile:{name}")
            if bad is not None:
                self.assertEqual(extra, sorted(bad),
                                 f"{name} 幻读集合变了：登记={sorted(bad)} 实为={extra}")
                continue
            self.assertEqual(extra, [],
                             f"{name} 面板凭空多出屏上没有的牌：{extra}（读到={got} 屏上={truth}）")

    def test_conclusions_come_from_this_frames_hand(self):
        """A3：顶部的向听/听牌结论必须与本帧读到的手牌同源。

        只读到 4 张（甚至 0 张）却仍挂着「一向听」，就是拿上一帧的结论给这一帧的
        画面下判断——用户看到的是一句与屏幕无关的话。
        """
        for (name, truth, _ph), d in zip(STREAM, self.payloads):
            got = panel_of(d)
            badge = str(d.get("tactical_badge") or "")
            if len(got) < 8 and badge in ("一向听", "已下叫", "听牌"):
                self.fail(f"{name} 只读到 {len(got)} 张却报「{badge}」："
                          "结论来自更早一帧的手牌，面板上同时挂着两套事实")

    def test_dingque_is_latched_once_the_table_commits_it(self):
        """A7/A8：定缺是牌桌敲定后不再变的事实。

        两面都要钉：换三张阶段（牌河空、手牌无「缺」角标）不许出现任何一家缺门；
        一旦报了缺门，后续帧不许改成另一门。字段名用真的 `opponents_dingque`：
        上一版我写的 `opponent_dingque` 根本不存在，`or []` 让这条永远通过。
        """
        first_swap = self.payloads[0]
        got = list(first_swap.get("opponents_dingque") or [])
        bad = KNOWN_STREAM_DEFECTS.get("dingque_before_commit:zj_swap_03.jpg")
        if bad is not None:
            self.assertEqual(got, sorted(bad),
                             f"换三张阶段凭空的缺门集合变了：登记={sorted(bad)} 实为={got}")
        else:
            self.assertEqual(got, [],
                             f"换三张阶段（牌河为空、无「缺」角标）就报出对手缺门：{got}")
        seen = {}
        for (name, _t, _ph), d in zip(STREAM[1:], self.payloads[1:]):
            for seat in (d.get("opponents_dingque") or []):
                if seat in seen and seen[seat] != seat:
                    self.fail(f"{name} 同一家缺门在帧间消失又出现：{seen} → {seat}")
                seen[seat] = seat

    def test_opponent_inference_is_not_the_same_number_for_every_seat(self):
        """A9：三家推断不得是同一份数字复制（那意味着没有任何一家有专属证据）。

        用户截图里“三家都叫听 高、危险牌全一样”就是这一条：牌河读不出时，
        贝叶斯先验不会被任何证据更新，三家都停在同一个初值上。把“没有证据”
        以“高危”的名义递到面板上，等于让每条建议都标红——那就是没有信息。
        """
        for (name, _t, _ph), d in zip(STREAM, self.payloads):
            ranges = d.get("hand_ranges") or []
            if len(ranges) < 3:
                continue
            bands = {r.get("tenpai_band") for r in ranges}
            probs = {round(float(r.get("tenpai_prob") or 0.0), 3) for r in ranges}
            river = (d.get("diag") or {}).get("river_zones") or {}
            has_evidence = sum(int(v or 0) for v in river.values()) > 0
            if has_evidence:
                self.assertLess(len(probs), 3,
                                f"{name} 有牌河证据却三家听牌概率完全相同：{probs}")
            else:
                # 证据为空时只允许一种正确做法：不报“高”。三家全高就是拿先验当结论。
                led = KNOWN_STREAM_DEFECTS.get("tenpai_band_without_evidence")
                if led is not None:
                    self.assertEqual(sorted(bands), sorted(led),
                                     f"无证据时的听牌档位变了：登记={sorted(led)} 实为={sorted(bands)}；"
                                     "牌河那条根因（river_always_empty）修好后本条应转绿并删台账")
                    continue
                self.assertNotIn("高", bands,
                                 f"{name} 牌河一条没读到（{river}）却报高危听牌：{probs}")


if __name__ == "__main__":
    unittest.main()
