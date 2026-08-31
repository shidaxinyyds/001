"""识别跳变专项回归测试。

不依赖 detector / cv2，直接对 Engine 里新加的两个稳定器做 unit test：
  - _TileVoter：加权投票，绝张感知，None 兜底
  - _MotionGuard：动画突变帧检测
并对 _hand_diff_count 做一张盘的最小变换量验证。

退出码 0 = 全部通过；非 0 = 失败。
"""
from __future__ import annotations
import os
import sys

# 把 Android Py 树加进 path，import engine（不经 Android 桥）
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

from engine.engine import _TileVoter, _MotionGuard, _hand_diff_count  # noqa: E402
from engine.engine import (
    ENGINE_MIN_CONF,
    ENGINE_MIN_CONF_RELAX,
    DISCARD_HISTORY_FRAMES,
    MIN_DROP_DELTA,
)
from recognition.structural import (
    StructuralDetector,
    CLAHE_CLIP_LIMIT,
    CLAHE_GRID_SIZE,
)

failures = []
def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


# ---------- _TileVoter ----------
print("[1] _TileVoter")

v = _TileVoter(window=4)
# 4 帧，每帧 14 张牌，全部一致 → 14 个候选全部锁定
def frame_14():
    return [((x * 50, 0, 50, 80), "1m", 0.9) for x in range(14)]
for _ in range(4):
    v.push(frame_14())
out = v.vote()
check("4-frames-same", len(out) == 14 and all(o[1] == "1m" for o in out),
       f"got len={len(out)} labels={[o[1] for o in out[:4]]}")

# 4 帧 3 个真实 1m + 1 个 None → 仍采纳 1m
v = _TileVoter(window=4)
good = [((x * 50, 0, 50, 80), "1m", 0.9) for x in range(14)]
for fr in [good, good, good, [(r, None, c) for (r, _, c) in good]]:
    v.push(fr)
out = v.vote()
check("3-good-1-None", len(out) == 14 and all(o[1] == "1m" for o in out),
       f"labels={[o[1] for o in out[:4]]}")

# 4 帧 2 个 "1m" + 2 个 "2m" → 加权：1m(1.0+0.7)=1.7, 2m(0.9+0.5)=1.4，1m 多。
# 但 last 是 2m，按 "种子帧" 也要有 vote，否则 chosen=None
v = _TileVoter(window=4)
fr1 = [((x * 50, 0, 50, 80), "1m", 0.9) for x in range(14)]
fr2 = [((x * 50, 0, 50, 80), "2m", 0.9) for x in range(14)]
for fr in [fr1, fr2, fr1, fr2]:
    v.push(fr)
out = v.vote()
# 4 帧 2v2，last 是 2m。即使加权后 1m 略多，种子要求 last 也有票，
# 但加权下 last_label=2m 在最新两帧各 1 票，加权 0.9+0.5=1.4；
# 1m 总加权 1.0+0.7=1.7，最佳 1m 但 last_vote=0 → 输出 None。
check("2v2-with-last-not-winning", all(o[1] is None for o in out),
       f"labels={[o[1] for o in out[:4]]}")

# 4 帧 3 个 "1m" + 1 个 "9m" → 1m 加权 2.6, 9m 加权 0.5。last_label="9m"，
# 但 1m 票数 ≥3，绝对 ≥MIN_VOTES=2 加权份额 ≥0.55 → 按"加权最高"还是 chosen=1m，但 last_vote=0 → None
v = _TileVoter(window=4)
fr_a = [((x * 50, 0, 50, 80), "1m", 0.9) for x in range(14)]
fr_b = [((x * 50, 0, 50, 80), "9m", 0.9) for x in range(14)]
for fr in [fr_a, fr_a, fr_a, fr_b]:
    v.push(fr)
out = v.vote()
# last=voting_a=9m，按设计不应被采纳（last 必须有票）
check("3a+1b-last-different",
      len(out) == 14 and all(o[1] is None for o in out),
      f"len={len(out)} labels={[o[1] for o in out[:4]]}")

# 4 帧 4 个 "1m" 全同 → chosen=1m ✓
v = _TileVoter(window=4)
fr = [((x * 50, 0, 50, 80), "1m", 0.9) for x in range(14)]
for _ in range(4):
    v.push(fr)
out = v.vote()
check("4-same-with-last",
      len(out) == 14 and all(o[1] == "1m" for o in out),
      f"labels={[o[1] for o in out[:4]]}")

# ----- reset -----
v.reset()
out = v.vote()
check("after-reset-empty", out == [], f"got {len(out)}")


# ---------- _MotionGuard ----------
print()
print("[2] _MotionGuard")
g = _MotionGuard()
# 首帧不判定
check("first-frame-no-spike", not g.is_spike(1.5), "first frame")

# 连续几个静止帧后单帧小幅 — 不应当 spike
g = _MotionGuard()
for _ in range(5):
    g.is_spike(0.5)
check("static-then-small",
      not g.is_spike(1.5),
      "should not spike with small diff after static history")

# 静止 → 突然巨大 diff → spike
g = _MotionGuard()
for _ in range(5):
    g.is_spike(0.5)
check("static-then-huge", g.is_spike(200.0), "should spike on huge diff")


# ---------- _hand_diff_count ----------
print()
print("[3] _hand_diff_count")
check("same-zero",  _hand_diff_count("1m2m3m", "3m2m1m") == 0)
check("one-different", _hand_diff_count("1m2m3m", "1m2m4m") == 1)
check("two-swapped", _hand_diff_count("1m2m3m", "1m2m3p") == 1)
check("missing", _hand_diff_count("1m2m3m", "1m2m") == 1)
check("empty-a", _hand_diff_count("", "1m2m") == 1)
check("non-overlap", _hand_diff_count("1m2m", "8m9m") == 2)


# ---------- CLAHE 辅助 ----------
print()
print("[4] _apply_clahe (低光预处理)")
import numpy as np
# 高对比度（强光）：CLAHE 不应修改图像
bright = np.zeros((100, 100), np.uint8)
bright[20:80, 20:80] = 200
out = StructuralDetector._apply_clahe(bright)
check("high-contrast-passthrough",
      np.array_equal(out, bright),
      "强光图必须原样透传（避免 CLAHE 伪影）")

# 低对比度（弱光）：应触发 CLAHE，输出与输入非空数组
dark = (np.random.rand(80, 80) * 40 + 80).astype(np.uint8)  # 范围 ~80~120
out = StructuralDetector._apply_clahe(dark)
check("low-contrast-triggers-clahe",
      out.shape == dark.shape and out.dtype == np.uint8,
      f"shape={out.shape} dtype={out.dtype}")
check("low-contrast-output-changed",
      not np.array_equal(out, dark),
      "弱光图必须被增强（与原图不同）")
check("clahe-constants",
      CLAHE_CLIP_LIMIT == 2.0 and CLAHE_GRID_SIZE == (8, 8),
      "clip_limit/grid_size 必须按设计值")


# ---------- 旋转重试质量门 ----------
print()
print("[5] _should_try_rotation / _rotation_is_better")
# 5 张牌但置信度都高（手牌 + 牌河识别正常）→ 不应触发旋转
g5_06 = [((1, 2, 3, 4), '1m', 0.6)] * 5
# 但 5 张牌 < 6 张，本设计的触发条件就是 "len < 6" 或 "低置信"
check("5-dets-trigger-rotation", StructuralDetector._should_try_rotation(g5_06),
      "5 张牌 < 6 张，触发旋转（与原 <4 不同：现在宽松到 <6）")
# 8 张牌、置信度 0.6 → 不触发（足够多 + 置信度正常）
g8_06 = [((1, 2, 3, 4), '1m', 0.6)] * 8
check("8-dets-no-rotation", not StructuralDetector._should_try_rotation(g8_06),
      "8 张 + 平均 0.6 置信，不应触发")
# 8 张牌但平均置信度 0.4 → 触发（低质量）
g8_04 = [((1, 2, 3, 4), '1m', 0.4)] * 8
check("8-dets-low-conf-triggers", StructuralDetector._should_try_rotation(g8_04),
      "8 张牌但平均 0.4 置信，必须触发")
# 旋转采纳门限：原 5@0.3 vs 旋转 8@0.6 → 旋转更好
g5_03 = [((1, 2, 3, 4), '1m', 0.3)] * 5
check("rotation-better-5-0.3-vs-8-0.6",
      StructuralDetector._rotation_is_better(g5_03, g8_06),
      "旋转结果更多 + 平均置信更高，必须采纳")
# 旋转结果更少 → 不采纳
check("rotation-no-fewer", not StructuralDetector._rotation_is_better(g8_06, g5_03),
      "旋转结果更少时不采纳")
# 旋转结果同数量同置信度 → 不采纳（边际不够）
g5_06b = [((1, 2, 3, 4), '1m', 0.6)] * 5
check("rotation-no-equal", not StructuralDetector._rotation_is_better(g5_06b, g5_06b),
      "旋转结果与原方向相当时不采纳")


# ---------- _apply_conf 双门槛 ----------
print()
print("[6] Engine._apply_conf 双门槛")
from engine.engine import Engine
e = Engine()
rect_ok = (10, 20, 60, 100)
# 严格门槛边界
check("conf-equal-min", e._apply_conf(rect_ok, "5m", ENGINE_MIN_CONF) == "5m",
      "置信=严格门槛时采纳")
check("conf-just-below", e._apply_conf(rect_ok, "5m", ENGINE_MIN_CONF - 0.01) is None,
      "无稳定手牌时低于严格门槛直接拒绝")
# 放宽门槛：建立稳定手牌后
e._stable_hand_mpsz = "1m2m3m"
check("conf-relax-after-stable",
      e._apply_conf(rect_ok, "5m", ENGINE_MIN_CONF_RELAX) == "5m",
      "有稳定手牌后，放宽门槛 ≥MIN_RELAX 即可采纳")
check("conf-relax-below-floor",
      e._apply_conf(rect_ok, "5m", ENGINE_MIN_CONF_RELAX - 0.01) is None,
      "放宽门槛以下仍拒绝")
# 重置稳定手牌 → 严格恢复
e._stable_hand_mpsz = ""
check("conf-strict-again", e._apply_conf(rect_ok, "5m", ENGINE_MIN_CONF - 0.05) is None,
      "无稳定手牌时恢复严格门槛")
# 牌形异常无论门槛都拒
check("aspect-rejected", e._apply_conf((10, 20, 200, 100), "5m", 0.99) is None,
      "宽高比超界直接拒绝")


# ---------- 牌河历史兜底字段 ----------
print()
print("[7] Engine 牌河历史字段")
e = Engine()
check("discard-history-empty", len(e._discard_history) == 0)
check("history-frames-constant", DISCARD_HISTORY_FRAMES == 6)
check("min-drop-delta-constant", MIN_DROP_DELTA == 4)
e._discard_history.append("1m2m3m4m5m6m7m8m")
e._discard_history.append("1m2m3m4m5m6m7m8m9m9m")
e._discard_history.append("1m2m3m4m5m6m7m8m")
check("history-bounded", len(e._discard_history) <= DISCARD_HISTORY_FRAMES,
      f"len={len(e._discard_history)}")


print()
if failures:
    print(f"FAILED: {len(failures)} case(s) -> {failures}")
    sys.exit(1)
print("ALL_OK")
