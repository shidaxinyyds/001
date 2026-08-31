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

print()
if failures:
    print(f"FAILED: {len(failures)} case(s) -> {failures}")
    sys.exit(1)
print("ALL_OK")
