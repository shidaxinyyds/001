# -*- coding: utf-8 -*-
"""非牌桌帧诚实性守卫（B1）：画面不是牌桌时，引擎绝不回放上一帧牌局。

背景（本轮性能排查）：`Engine.process` 在 `_is_mahjong_table` 判 False 后，
旧写法对**首帧**（`_non_table_frames <= 1`）走 `_build_skip_result(image,
self._frame_skipper.cached)` —— 把上一次真的识别过牌桌时的 payload 原样再发一遍。
那份缓存里带着上一局的 `hand`/`advice`/`discards`，而当前画面已经是大厅/结算/聊天。
用户看到的「画面 14 张手牌、面板还写着上一局的 5 张」就是它。

为什么当初这么写：瞬态遮挡（摸打动画、手指划过、弹窗一闪）会让牌桌校验掉一帧，
直接清空会造成面板闪烁。这个诉求是真的，但实现放错了层 —— **数据层不该用错误的
数据去换好看的 UI**，防闪属于显示层：悬浮窗的清空帧去抖已按纯时间门（≥450ms）
兜住瞬态（见 `lib/overlays/mahjong_overlay.dart` 的 `_kClearStatuses` 分支）。

本守卫锁三件事，每条都是「改回坏写法就必红」：
1. 非牌桌首帧（即便 `_frame_skipper.cached` 里有整局牌）出口必须是
   `status=waiting`、`hand=''`、`advice=[]`，且 payload 里不许出现缓存中的牌。
2. 待机出口的字段集合必须齐（`_build_waiting_result` 是从旧内联 JSON 提取的，
   提取时漏一个键，悬浮窗就会读到 null —— 而漏掉的键往往只在错误路径上才走到）。
3. `_non_table_frames >= 2/3` 才硬重置游戏态，本帧只报待机（换回牌桌时牌河账本
   不该被一次瞬态遮挡清掉）。

运行：
  py -3.10 -X utf8 localtest/test_non_table_honesty_guard.py          # 主守卫
  py -3.10 -X utf8 localtest/test_non_table_honesty_guard.py --mutate  # 变异对照
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from engine.engine import Engine  # noqa: E402

MUTATE = "--mutate" in sys.argv
ENGINE_PY = os.path.join(REPO, "android", "app", "src", "main", "python",
                         "engine", "engine.py")

# 缓存 payload 里埋的牌：一旦出现在非牌桌帧的出口，就是「播旧牌局」的实证。
STALE_HAND = "123m456p789s11z"


def make_engine(with_cache: bool = True) -> Engine:
    eng = Engine()
    eng.platform = "tencent"
    eng.mode = "std_tdh"
    if with_cache:
        eng._frame_skipper.remember(json.dumps({
            "status": "ok", "hand": STALE_HAND, "count": 13,
            "advice": [{"tile": "1z", "why": "旧局建议"}],
            "discards": "123m", "phase_label": "立直",
        }), 0.9)
    # 牌桌校验与方向归一都不该参与本用例：只验「非牌桌出口长什么样」。
    eng._is_mahjong_table = lambda image: False
    eng._settle_orientation = lambda image: (image, False)
    return eng


class TestNonTableHonesty(unittest.TestCase):
    def setUp(self) -> None:
        self.img = np.zeros((400, 700, 3), dtype=np.uint8)

    def _payload(self, eng: Engine) -> dict:
        res = eng.process(self.img)
        self.assertIsNotNone(res, "非牌桌帧也必须返回结果，不能返回 None 让面板僵住")
        return json.loads(res.result)

    def test_first_non_table_frame_never_replays_cached_hand(self):
        eng = make_engine()
        d = self._payload(eng)
        self.assertEqual("waiting", d.get("status"),
                         "画面已不是牌桌却仍报 %r —— 播旧帧的写法回来了" % d.get("status"))
        self.assertEqual("", d.get("hand") or "")
        self.assertEqual(0, d.get("count") or 0)
        self.assertFalse(d.get("advice") or [], "非牌桌帧不许把旧局的建议继续挂在面板上")
        self.assertNotIn(STALE_HAND, json.dumps(d, ensure_ascii=False))
        self.assertFalse(d.get("frame_skipped"),
                         "frame_skipped=True 语义是「画面没变」，这里画面已经离开牌桌")

    def test_waiting_payload_keeps_every_panel_field(self):
        d = self._payload(make_engine())
        need = {"mode", "mode_name", "platform", "platform_name", "knowledge_doctrine",
                "phase_label", "tactical_badge", "tactical_intent", "dingque",
                "dingque_suit", "hand", "count", "status", "shanten", "advice",
                "best", "commentary", "discards", "discard_count", "remaining",
                "dead", "remaining_matrix", "tile_ledger", "ting_chance",
                "is_drawing", "drawing_tile", "tiles", "top_score", "screen",
                "elapsed", "frame_skipped", "message", "native_ready"}
        missing = need - set(d)
        self.assertFalse(missing,
                         "待机出口漏字段（面板会读到 null）：%s" % sorted(missing))

    def test_game_state_not_reset_on_first_non_table_frame(self):
        """只报待机，不销毁牌局态：换回牌桌时牌河账本必须还在（瞬态遮挡不该丢局）。"""
        eng = make_engine()
        eng._match_started = True
        eng._non_table_frames = 0
        reset_calls = []
        orig_reset = eng._reset_game_state
        eng._reset_game_state = lambda: (reset_calls.append(1), orig_reset())
        self._payload(eng)
        self.assertEqual(1, eng._non_table_frames)
        self.assertFalse(reset_calls, "首帧非牌桌就硬重置，一次划过手指就会清掉整局账本")
        # 第 2 帧（未开局阈值 2 / 局中阈值 3）：局中仍不该重置
        self._payload(eng)
        self.assertFalse(reset_calls)

    def test_source_has_no_cached_replay_in_non_table_branch(self):
        """源码契约：非牌桌分支里不得再出现 `_build_skip_result(..., cached)`。"""
        branch = extract_table_branch(ENGINE_SRC)
        self.assertNotIn(
            "self._build_skip_result(image, self._frame_skipper.cached)", branch,
            "非牌桌分支又开始回放上一帧缓存了（B1 原样复发）")
        self.assertIn("return self._build_waiting_result(image)", branch,
                      "非牌桌分支不再走统一的待机出口")


def extract_table_branch(src: str) -> str:
    """取 `if not self._is_mahjong_table(image):` 到对应 `else:` 之前的那段源码。"""
    i = src.index("if not self._is_mahjong_table(image):")
    j = src.index("else:", i)
    return src[i:j]


def restore_replay(src: str, on: bool = True) -> str:
    """变异：把「首帧回放缓存」的旧写法塞回非牌桌分支（改回坏写法必须让守卫变红）。"""
    if not on:
        return src
    i = src.index("return self._build_waiting_result(image)")
    return (src[:i] +
            "if self._non_table_frames <= 1 and self._frame_skipper.cached is not None:\n"
            "                    return self._build_skip_result(image, self._frame_skipper.cached)\n"
            "                " + src[i:])


ENGINE_SRC = open(ENGINE_PY, encoding="utf-8").read()


class TestMutationControls(unittest.TestCase):
    """--mutate：把坏写法塞回源码/行为，断言本守卫确实会红（防止守卫只是空转）。"""

    def setUp(self) -> None:
        if not MUTATE:
            self.skipTest("加 --mutate 才跑变异对照")
        self.img = np.zeros((400, 700, 3), dtype=np.uint8)

    def test_replaying_cached_hand_breaks_the_honesty_gate(self):
        eng = make_engine()
        # 旧首帧写法：直接返回缓存 payload
        eng._build_waiting_result = lambda image: eng._build_skip_result(
            image, eng._frame_skipper.cached)
        d = json.loads(eng.process(self.img).result)
        self.assertEqual("ok", d.get("status"), "变异无效：旧写法没能骗过断言")
        self.assertIn(STALE_HAND, json.dumps(d, ensure_ascii=False))

    def test_source_mutation_reintroduces_the_forbidden_call(self):
        mutated = restore_replay(ENGINE_SRC, on=True)
        branch = extract_table_branch(mutated)
        self.assertIn("self._build_skip_result(image, self._frame_skipper.cached)",
                      branch, "变异无效：没能把被禁止的调用塞回非牌桌分支")
        # 现行源码必须不含它
        self.assertNotIn("self._build_skip_result(image, self._frame_skipper.cached)",
                         extract_table_branch(ENGINE_SRC))


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestNonTableHonesty))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
