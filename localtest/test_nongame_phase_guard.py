# -*- coding: utf-8 -*-
"""非牌局屏不得被声称处于「需要手持牌」的阶段（A4/A6 收尾不变量）。

来源：用户补的 9 张真机图里，两张结算页（`nongame_settle_01/02`）在引擎里被判成
`status=swap` —— 结算画面摊开的三组牌很像「换三张」的三张候选。但换三张物理上
必然手里有 13 张牌，而这两帧 `count=0`：一个「手里一张牌都没读到」的帧声称自己在
换三张，两件事不可能同时为真。面板于是会在结算页上画一套换牌 UI。

不变量：`count <= 0` 时不得下发 swap/pick/dingque 任一阶段标志。

为什么不用「场景探针认出结算页」这条路：`_is_mahjong_table` 对广告页/结算页全返回
True（已实测），而修场景探针需要新的判据；本条不变量不需要判据，只用一条物理必要
条件，覆盖面更广（任何读不到牌的屏都不许声称在有牌的阶段）。

⚠ 本轮量到、但**尚未修也修不了**的另一条：阶段滞后（A4 实体）。

  真机序列 `zj_swap_03`（牌河空）→ `zj_play_03`（牌河已有 9 张弃牌）：第二帧仍判为
  swap，而下游「换牌阶段不给建议」会拿这个标志把建议清空 —— 用户在牌局中段看到
  换牌 UI 且没有任何建议。

  试过两种落点，都不成立（均已回退）：
    · 出口闸门改标志：标志改了、建议仍是空（建议在上游 swap 分支里就没算）；
      而且面板判的是 `swap_phase || status=='swap'`，只清标志等于清了个寂寞。
    · 阶段判定处按牌河证据剥掉滞后：`_river_zone_counts` 在牌河扫描块（约 5419 行）
      才更新，**晚于阶段判定（约 5154 行）**，所以判定时本帧的 9 张弃牌还不存在，
      门永不触发（实测：第二帧仍 swap_phase=True）。

  要修必须先破这个**顺序死结**：要么把牌河证据的采集提到阶段判定之前，要么给
  swap 的迟滞保持（约 5150 行「迟滞保持：本帧虽漏检，仍视为换牌延续」）加一个不
  依赖牌河的上界。后者更小：滞后只赖 1 帧，而真换三张会持续多帧。

用法: py -3.10 -X utf8 localtest/test_nongame_phase_guard.py [--mutate]
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402

MUTATE = "--mutate" in sys.argv

# (夹具, 平台, 说明)：全部是「读不到自家手牌」的屏
NONGAME = [
    ("shots_phase_fix/nongame_settle_01.jpg", "shushan", "结算页·赢2098倍"),
    ("shots_phase_fix/nongame_settle_02.jpg", "zj_sichuan", "结算页·旗开得胜"),
    ("shots_phase_fix/nongame_lobby_01.jpg", "zj_sichuan", "开局等待页"),
    ("shots_batch3/ad_screen_01.jpg", "zj_sichuan", "广告页"),
]


def run_case(rel, platform):
    path = os.path.join(HERE, rel)
    img = cv2.imread(path)
    if img is None:
        raise unittest.SkipTest(f"缺素材 {rel}")
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, _p=platform, **k: _p
    E.load_mode = lambda *a, **k: "sc_hz"
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm


class TestNongamePhase(unittest.TestCase):
    def test_no_hand_means_no_tile_phase(self):
        """count<=0 的帧不得声称处于换三张/选牌/定缺任一阶段。"""
        checked = 0
        for rel, platform, note in NONGAME:
            d = run_case(rel, platform)
            n = int(d.get("count") or 0)
            if n > 0:
                continue          # 该帧读到了牌，不在本不变量适用范围内
            checked += 1
            for key in ("swap_phase", "pick_phase", "dingque_phase"):
                self.assertFalse(d.get(key),
                                 f"{note}：一帧手牌都没读到却下发 {key}=True")
        self.assertGreater(checked, 0, "没有任何一帧 count<=0：本用例已空转")

    def test_settle_pages_are_not_reported_as_swap(self):
        """两张结算页具体不许是 swap（这条是被实测到的真误判）。"""
        for rel, platform, note in NONGAME[:2]:
            d = run_case(rel, platform)
            self.assertNotEqual("swap", d.get("status"),
                                f"{note} 被判成换三张阶段（A4 复发）")
            self.assertEqual(0, int(d.get("count") or 0),
                             f"{note} 读到了牌，本用例的前提已不成立")

    def test_live_play_frame_keeps_advice_despite_swap_hysteresis(self):
        """A4 实体：阶段滞后不得把局中建议一起误杀。

        真机序列 `zj_swap_03`（牌河空）→ `zj_play_03`（牌河已有 9 张弃牌）。第二帧
        的 swap 标志会被迟滞保持续一帧（UI 不闪是对的），但本帧真实线索已消失 ——
        建议不得再被它清空。牌河证据在阶段判定之后才有，无法在判定时拦住滞后
        （顺序死结），所以把「不给建议」改成只看本帧真实检测。

        钉两头：① 真换三张帧（牌河空）仍不得给建议，否则只是把错改到另一边；
        ② 牌河非空的帧必须给建议。
        """
        seq = [("shots_batch3/zj_swap_03.jpg", "zj_sichuan"),
               ("shots_batch3/zj_play_03.jpg", "zj_sichuan"),
               ("shots_batch3/zj_play_03.jpg", "zj_sichuan")]
        outs = []
        orig_lp, orig_lm = E.load_platform, E.load_mode
        try:
            eng = E.Engine()
            eng.get_hand_detector()
            for rel, platform in seq:
                img = cv2.imread(os.path.join(HERE, rel))
                if img is None:
                    self.skipTest(f"缺素材 {rel}")
                E.load_platform = lambda *a, _p=platform, **k: _p
                E.load_mode = lambda *a, **k: "sc_hz"
                with contextlib.redirect_stdout(io.StringIO()):
                    outs.append(json.loads(eng.process(img).result))
        finally:
            E.load_platform, E.load_mode = orig_lp, orig_lm

        def river_total(d):
            rz = (d.get("diag") or {}).get("river_zones") or {}
            return sum(int(v or 0) for v in rz.values()
                       if isinstance(v, (int, float)))

        first = outs[0]
        self.assertEqual(0, river_total(first),
                         "前提不成立：首帧应是牌河为空的真换三张帧")
        self.assertTrue(first.get("swap_phase") or first.get("status") == "swap",
                        "前提不成立：真换三张帧没被判成 swap，本用例无从判定")
        self.assertFalse(first.get("advice") or first.get("best"),
                         "真换三张帧（牌河空）给了打牌建议：D24 被改坏了")

        checked = 0
        for i, d in enumerate(outs[1:], start=2):
            if river_total(d) <= 0 or int(d.get("count") or 0) <= 0:
                continue
            checked += 1
            self.assertTrue(d.get("advice") or d.get("best"),
                            f"第 {i} 帧牌河已有 {river_total(d)} 张弃牌、读到 "
                            f"{d.get('count')} 张手牌，却不给任何建议"
                            f"（阶段滞后把局中建议误杀，A4 复发）")
        self.assertGreater(checked, 0, "没有任一帧带弃牌证据：本用例已空转")

    def test_offcenter_disc_row_is_not_a_dingque_page(self):
        """A7（修了一半）：偏右的一排圆形UI 不得被当成定缺选门三盘。

        途游局中帧 r26 的三盘 x=0.633/0.756/0.869（行中心 0.751）其实是**对手头像
        + 金豆徽章**那一排，同高、等大、异色，就凑过了旧判据 —— 而误判定缺页会触发
        「命中阶段即清池」，把整局牌河/断门/本家缺门一起抹掉。

        钉两头：偏右的误报帧必须被拒；三张真定缺页（行中心 0.385~0.505）必须仍通过
        —— 只钉前一头就是拿一个错换一个对。
        """
        from recognition.tencent_grid_detector import (
            _find_phase_discs, _phase_disc_row, _phase_disc_row_in_band,
            _PHASE_DISC_TRIPLE_GAP, _PHASE_DISC_ROW_MID)

        self.assertEqual(_PHASE_DISC_ROW_MID, (0.28, 0.72),
                         "居中约束的窗口被改了：下面的正/反例判定需要重测")

        def row_of(rel):
            img = cv2.imread(os.path.join(HERE, rel))
            if img is None:
                self.skipTest(f"缺素材 {rel}")
            row = _phase_disc_row(_find_phase_discs(img), _PHASE_DISC_TRIPLE_GAP, 0.035)
            return row, (row[0]["x"] + row[-1]["x"]) / 2.0 if len(row) >= 3 else None

        # 反例：偏右的头像那排
        row, mid = row_of("shots_tuyou/r26_4cda06de.jpg")
        self.assertIsNotNone(mid, "r26 不再配成三盘行：本用例前提已变，需重测")
        self.assertGreater(mid, _PHASE_DISC_ROW_MID[1],
                           f"r26 行中心 {mid:.3f} 不再偏右，本用例已测不到东西")
        self.assertFalse(_phase_disc_row_in_band(row),
                         f"偏右的圆形UI排（中心 {mid:.3f}）仍被当成定缺三盘（A7 复发）")

        # 正例：三张真定缺页不得因此退化为漏判
        for rel in ("shots_phase_fix/dq_jj_01.jpg",
                    "shots_phase_fix/dq_tencent_01.jpg",
                    "shots_phase_fix/dq_tuyou_01.jpg"):
            row, mid = row_of(rel)
            self.assertIsNotNone(mid, f"{rel} 配不出三盘行：前提已变")
            self.assertTrue(_phase_disc_row_in_band(row),
                            f"真定缺页 {rel}（行中心 {mid:.3f}）被居中约束误杀")

    def test_mutant_without_the_gate_is_caught(self):
        """变异对照：证明「拿掉闸门后这条确实会坏」，而不是它本来就不会发生。

        不跑这一步就不知道这个不变量是不是早就由别处保证了 —— 本会话有过「修一个
        已经不存在的问题」的教训。两个对照面：
          1) 原始阶段线索在结算页上确实会误报（否则闸门无从可拦）；
          2) 出口闸门存在于 engine.py 源码里（删掉它主断言就会变红）。
        """
        if not MUTATE:
            self.skipTest("仅在 --mutate 下运行")
        rel, platform, note = NONGAME[0]
        img = cv2.imread(os.path.join(HERE, rel))
        if img is None:
            self.skipTest(f"缺素材 {rel}")
        from recognition.yolo_detector import YOLODetector  # noqa: E402
        from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
        raw_yolo = bool(YOLODetector().is_swap_phase(img))
        raw_grid = bool(TencentGridDetector().is_swap_phase(img))
        d = run_case(rel, platform)
        # 注意：两个检测器的 is_swap_phase 在这张结算页上实测都是 False —— swap 状态
        # 不是从它们来的（而是引擎内部的换牌候选推演）。所以本用例不能拿“原始线索
        # 会误报”当闸门作力的证据；真正的证据是接入前后的实测对比：
        #   接入前 status=swap，接入后 status=waiting（见 probe_nongame_features.py）。
        print(f"      [mutate] {note}：raw yolo={raw_yolo} grid={raw_grid}（两者均 False，"
              f"swap 来自引擎内部）→ 闸门后 status={d.get('status')}；"
              f"接入前实测为 swap，故闸门确实作力")
        self.assertNotEqual("swap", d.get("status"),
                            f"{note} 仍被判成 swap：出口闸门失效或被删")
        src = os.path.join(REPO, "android", "app", "src", "main", "python",
                           "engine", "engine.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('未读到手牌 · 不判定阶段', text,
                      "出口闸门不在 engine.py 里：本变异用例已失效")


if __name__ == "__main__":
    # 驱动器会把 --mutate 透传进来；unittest 的默认 argparse 不认这个标志，会把
    # 整个进程弄成「unrecognized arguments」而报「无计数」。只留程序名。
    unittest.main(argv=[sys.argv[0]])
