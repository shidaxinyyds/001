"""引擎级回归：开局前阶段（定缺/换牌/选牌）误触发时，绝不清空已累积牌池。

复现的真机 bug：对局进行中若视觉探测器把某个 banner/动画误判成定缺/换牌，
旧代码会无条件执行 _monotonic_discards.clear() + _match_started=False，
把整局牌池与对局状态一把清空 → 记牌器归零、活牌计数错乱、建议僵死乱跳。

修复：牌池非空（river_locked）时，物理上不可能再处于开局前阶段，强制关闭
这三个阶段并跳过其破坏性副作用。

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

    def test_midgame_false_dingque_does_not_wipe_pool(self):
        """对局中误判定缺/换牌/选牌：牌池必须存活，且不得进入开局前阶段。

        验证 river_locked 物理门控：牌池非空时探测器即使误触发，也绝不
        执行 _monotonic_discards.clear()。跨多帧反复误触都不得清池。
        """
        eng = self._warmed_engine()
        pool = Counter({"5p": 2, "3s": 1, "9m": 1})
        eng._monotonic_discards = Counter(pool)

        det = eng.get_detector()
        with mock.patch.object(det, "is_dingque_phase", return_value=True), \
                mock.patch.object(det, "is_swap_phase", return_value=True), \
                mock.patch.object(det, "is_pick_phase", return_value=True):
            for _ in range(3):  # 跨多帧确认：证明牌池真的被物理门控保护
                _force_full(eng)
                data = _quiet_process(eng, self.img)
                self.assertEqual(
                    sum(eng._monotonic_discards.values()), sum(pool.values()),
                    "牌池被开局前阶段误触发清空了（回归！）")

        self.assertFalse(data.get("dingque_phase"), "误判定缺不应生效")
        self.assertFalse(data.get("swap_phase"), "误判换牌不应生效")
        self.assertFalse(data.get("pick_phase"), "误判选牌不应生效")
        self.assertNotIn(data.get("status"), ("dingque", "swap", "pick"))

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
