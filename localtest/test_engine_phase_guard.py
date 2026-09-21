"""引擎级回归：开局前阶段误触发的「连续检测」防误清门控（v1.4.8 阶段2 重设计后的契约）。

背景：旧实现用 river_locked 硬门控（牌池非空即绝不清），但血流成河一家定缺后永不打缺
会导致牌池永不清 → 换三张/定缺/新局永不识别（死锁）。阶段2 改为：阶段检测无条件运行，
"命中即清池"改为「探测器连续 ≥2 次真实命中」才清池。

本回归锁死三点契约：
  1) 瞬时单次误检（某帧把 banner/动画误读成换三张面板）绝不清空已累积牌池；
     ——曾因确认计数每帧复用节流缓存、借下一帧自动凑满 2 次而误清，此为回归守卫。
  2) 真新局的换三张/定缺面板会连续多帧命中，必须照常清池（防重新引入 river_locked 死锁）。
  3) 反向对照：牌池为空（真开局前）时定缺阶段应正常单帧生效，门控不误伤。

用法: py -3.10 localtest/test_engine_phase_guard.py
"""
import contextlib
import io
import json
import os
import sys
import unittest
from collections import Counter
from unittest import mock

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS_DIR = os.path.join(HERE, "shots")


def _pick_shot():
    """选一张能稳定识别出 >=10 张手牌的对局帧做回归样本。"""
    import glob
    cands = sorted(glob.glob(os.path.join(SHOTS_DIR, "*.jpg")))
    pref = [p for p in cands if "03ab136d" in os.path.basename(p)]
    return (pref or cands)[0] if (pref or cands) else ""


def _quiet_process(eng, img):
    with contextlib.redirect_stdout(io.StringIO()):
        res = eng.process(img)
    assert res is not None
    return json.loads(res.result)


def _force_full(eng):
    """清掉帧差去重缓存，强制下一帧走完整识别。

    否则同一张静止图第二帧会命中 _frame_skipper 短路口（diff<阈值 + 有缓存），
    直接返回缓存、根本不执行开局前阶段门控代码 → 测试变成「空跑」，
    既证明不了 guard 生效，也证明不了反向对照。
    """
    eng._frame_skipper._last_payload = None


class TestPregamePhaseGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shot = _pick_shot()
        if not shot or not os.path.exists(shot):
            raise unittest.SkipTest(f"missing sample shot under {SHOTS_DIR}")
        img = cv2.imread(shot)
        assert img is not None
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        cls.img = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    def _warmed_engine(self):
        """跑两帧建立稳定手牌与对局状态（不触发新开局重置）。"""
        eng = Engine()
        _quiet_process(eng, self.img)
        _quiet_process(eng, self.img)
        eng._match_started = True
        return eng

    def _advance(self, eng, n):
        """连续喂 n 帧（每帧强制走完整识别，绕开帧差短路）。"""
        for _ in range(n):
            _force_full(eng)
            _quiet_process(eng, self.img)

    def test_transient_false_swap_phase_does_not_wipe_pool(self):
        """瞬时单次误检：探测器只命中一次换三张，牌池必须存活。

        回归守卫：确认计数若每帧复用节流缓存，则单帧误检会借下一帧
        自动凑满 2 次而误清。修复后只在“真跑了探测器”的帧计数。
        """
        eng = self._warmed_engine()
        pool = Counter({"5p": 2, "3s": 1, "9m": 1})
        # _monotonic_discards 已被阶段4 双账本重设为派生值 max(visual, inferred)，
        # 故种子牌池必须落在权威累计账本 _visual_discards 上，才能被合并保留。
        eng._visual_discards = Counter(pool)
        eng._monotonic_discards = Counter(pool)

        det = eng.get_detector()
        hits = {"n": 0}

        def fake_swap(_img):
            hits["n"] += 1
            return hits["n"] == 1  # 仅第一帧误命中，随后均为 False

        with mock.patch.object(det, "is_swap_phase", side_effect=fake_swap), \
                mock.patch.object(det, "is_dingque_phase", return_value=False), \
                mock.patch.object(det, "is_pick_phase", return_value=False), \
                mock.patch.object(eng, "_clear_discard_ledgers",
                                  wraps=eng._clear_discard_ledgers) as clear_spy:
            self._advance(eng, 3)  # 足够暴露旧 bug（缓存自动凑满于第2帧），又低于 RIVER_REGRET_FRAMES
        clear_spy.assert_not_called()
        for lab, cnt in pool.items():
            self.assertGreaterEqual(
                eng._monotonic_discards[lab], cnt,
                f"牌池 {lab}x{cnt} 被瞬时单次换三张误检清零/削减了（回归！）")

    def test_persistent_new_game_swap_phase_clears_pool(self):
        """持续命中：真新局的换三张面板连续多帧出现，必须清池。

        防重新引入 river_locked 死锁：不能因“牌池非空”就永远屏蔽清池。
        """
        eng = self._warmed_engine()
        eng._monotonic_discards = Counter({"5p": 2, "3s": 1, "9m": 1})
        det = eng.get_detector()
        with mock.patch.object(det, "is_swap_phase", return_value=True), \
                mock.patch.object(det, "is_dingque_phase", return_value=False), \
                mock.patch.object(det, "is_pick_phase", return_value=False), \
                mock.patch.object(eng, "_clear_discard_ledgers",
                                  wraps=eng._clear_discard_ledgers) as clear_spy:
            self._advance(eng, 6)
        clear_spy.assert_called()

    def test_pregame_dingque_still_works_when_pool_empty(self):
        """反向对照：牌池为空（真开局前）时，定缺阶段应单帧正常生效，门控不误伤。"""
        eng = self._warmed_engine()
        eng._monotonic_discards = Counter()  # 开局前牌池为空
        det = eng.get_detector()
        _force_full(eng)
        with mock.patch.object(det, "is_dingque_phase", return_value=True):
            data = _quiet_process(eng, self.img)
        self.assertTrue(data.get("dingque_phase"),
                        "牌池为空时定缺阶段应被正常识别（门控不应误伤）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
