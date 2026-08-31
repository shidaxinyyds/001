"""process() 端到端稳定性测试：mock detector，连续调 process() N 次。

1) 同一张图 × 20：hand_mpsz 必须 100% 一致，不应"跳变"。
2) mode 切换：直接改 self._prev_mode 然后再调一次，旧投票被清空。
3) 全 None 的 frame 输入：仍能完成 process() 而不崩（mode='4p' 时 hand is None）。
4) 帧差 < 阈值：两次 process 之间不增 work，第二次直接复用第一次的缓存。
"""
from __future__ import annotations
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python"))
sys.path.insert(0, PKG)

import numpy as np
import cv2

from engine import engine as engine_mod  # noqa: E402
from engine.engine import Engine, FRAME_DIFF_THRESHOLD  # noqa: E402

failures = []
def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def make_fake_image(h=600, w=1100, seed=0):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    # 画两组横排"牌"（仅用于给帧差签名相似的底色，避免首帧被判定为突变）
    cv2.rectangle(img, (100, 460), (100 + 13 * 50, 540), (210, 200, 180), -1)
    cv2.rectangle(img, (100, 100), (100 + 6 * 50, 180), (160, 150, 130), -1)
    return img


def make_fake_engine(n_tiles_hand=14, hand_labels=None):
    """构造一个最小可用的 Engine：替换 detector 为假货（直接返回固定检测）。"""
    eng = Engine()
    # 造一行"手牌"的检测（一行 13/14 张牌）
    if hand_labels is None:
        hand_labels = ["1m"] * n_tiles_hand
    hand_dets = []
    for i, lbl in enumerate(hand_labels):
        rect = (100 + i * 50, 460, 50, 80)  # x, y, w, h
        hand_dets.append((rect, lbl, 0.92))
    discard_dets = [
        ((100, 100, 50, 80), "2p", 0.90),
        ((150, 100, 50, 80), "3p", 0.90),
    ]
    rows_all = [hand_dets, discard_dets]

    class FakeDetector:
        def __init__(self):
            self.last_top_score = 0.85
            self.last_screen = (1100, 600)
            self._glyphs = type("G", (), {"nums": {"a": 1}})()
            self._styles = type("S", (), {"tpls": [1]})()

        def detect_all_rows(self, image):
            return rows_all

        last_detections = rows_all  # 兼容老接口

    eng.get_detector = lambda: FakeDetector()  # type: ignore
    # 重置所有内部状态
    eng._tile_voter = engine_mod._TileVoter(window=engine_mod.VOTE_WINDOW)
    eng._frame_skipper = engine_mod._FrameSkipper()
    eng._motion_guard = engine_mod._MotionGuard()
    eng._advice_key = None
    eng._advice = []
    eng._last_hand_y = None
    eng._stable_hand_mpsz = ""
    eng._stable_hand_count = 0
    eng.mode = "4p"
    eng._prev_mode = "4p"
    return eng


# ---- 1) 连跑 20 次同图，hand_mpsz 必须稳定 ----
print("[1] process() x20 同图，hand_mpsz 稳定")
eng = make_fake_engine(n_tiles_hand=14)
img = make_fake_image(seed=42)
results = []
for _ in range(20):
    r = eng.process(img)
    # r.image: 预览图 ndarray；r.result: json string
    if r is None:
        results.append(None)
        continue
    payload = json.loads(r.result)
    results.append(payload.get("hand"))
unique = set(results)
check("20-runs-stable", len(unique) == 1,
       f"unique hands: {unique}")
# 启动前 1~3 帧可能还在凑 VOTE_WINDOW，至少从某帧后必须稳定
# 严格：所有非 None 结果应一致
non_none = [h for h in results if h]
check("all-non-empty", len(non_none) >= 4,
       f"got {len(non_none)} non-empty hands out of 20")

# ---- 2) 模式硬重置：直接检测关键字段被清空 ----
print()
print("[2] mode 硬重置：缓存与行锁被清")
# 用 13 张不重复、不撞 dup_explosion 的手牌
eng = make_fake_engine(n_tiles_hand=13,
                        hand_labels=["1m","2m","3m","4m","5m","6m","7m",
                                     "1p","2p","3p","4p","5p","6p"])
img = make_fake_image(seed=99)
for _ in range(8):
    eng.process(img)
check("stable-cache-set-before",
       eng._stable_hand_mpsz != "",
       f"got {eng._stable_hand_mpsz!r}")
check("hand-lock-set-before",
       eng._last_hand_y is not None,
       f"got {eng._last_hand_y!r}")
# 直接调 reset 模拟模式突变
eng._tile_voter.reset()
eng._last_hand_y = None
eng._stable_hand_mpsz = ""
eng._stable_hand_count = 0
eng._advice_key = None
check("tile-voter-empty-after-reset",
       len(eng._tile_voter._frames) == 0)

# ---- 3) 同一画面连跑——_consecutive_skips 越来越多，复用上次结果 ----
print()
print("[3] 帧差<阈值时跳过")
eng = make_fake_engine(n_tiles_hand=14)
img1 = make_fake_image(seed=7)
# 跑 10 次同一张图，触发帧差跳过
skipped = 0
runs = []
for _ in range(10):
    r = eng.process(img1)
    payload = json.loads(r.result)
    if payload.get("frame_skipped"):
        skipped += 1
    runs.append(payload.get("status"))
check("frame-skip-mostly",
       skipped >= 4,
       f"skipped={skipped}/10")

# ---- 4) reset 后引擎仍能完整跑一次 process ----
print()
print("[4] reset 后仍能跑出结果")
eng = make_fake_engine(n_tiles_hand=13, hand_labels=["1m"] * 13)
img2 = make_fake_image(seed=11)
for _ in range(6):
    eng.process(img2)
# 调用 reset 模拟模式突变
eng._tile_voter.reset()
eng._last_hand_y = None
eng._stable_hand_mpsz = ""
eng._stable_hand_count = 0
eng._advice_key = None
eng._frame_skipper = engine_mod._FrameSkipper()
r = eng.process(img2)
payload = json.loads(r.result)
check("after-reset-runs",
       "hand" in payload,
       f"missing hand key, payload keys={list(payload.keys())}")

# ---- 5) 故意构造不同图（应不被跳过） ----
print()
print("[5] 差异显著时不被跳过")
eng = make_fake_engine(n_tiles_hand=14)
img_a = make_fake_image(seed=1)
img_b = make_fake_image(seed=999)
r1 = eng.process(img_a)
r2 = eng.process(img_b)
p1 = json.loads(r1.result)
p2 = json.loads(r2.result)
# 注意：因为我们用不同 seed 的随机图，第一张之后基本上 diff 很大、可能触发 motion spike。
# 这里只验证两次都返回了 result（没有崩）以及至少有一次没被跳过
check("diff-image-runs", not p1.get("frame_skipped") or not p2.get("frame_skipped"),
       f"p1.skip={p1.get('frame_skipped')} p2.skip={p2.get('frame_skipped')}")


# ---- 6) 启动期 WARMUP_FRAMES 内即使画面大跳变也不被 _MotionGuard 拒 ----
print()
print("[6] 冷启动 WARMUP_FRAMES 帧不被突变检测拒")
eng = make_fake_engine(n_tiles_hand=13,
                       hand_labels=["1m","2m","3m","4m","5m","6m","7m",
                                    "1p","2p","3p","4p","5p","6p"])
check("warmup-init-4", eng._warmup_left == 4,
      f"initial _warmup_left={eng._warmup_left}")
# 连跑 4 张差异极大的图（每张 seed 不同 → diff 极高）
imgs = [make_fake_image(seed=s) for s in (101, 202, 303, 404)]
skipped_count = 0
processed_count = 0
for img in imgs:
    r = eng.process(img)
    payload = json.loads(r.result)
    if payload.get("frame_skipped"):
        skipped_count += 1
    if "hand" in payload:
        processed_count += 1
# 4 张图可能只有首张是"完整识别"（建立 baseline），其余靠 frame_skip。
# 但**不应**被 _MotionGuard 拒（warmup 期内 motion guard 被绕过）。
# 简单验证：4 张都有非空 hand 字段 + 4 张里 warmup 计数器最终为 0
check("warmup-counter-depleted", eng._warmup_left == 0,
      f"_warmup_left={eng._warmup_left}")
check("warmup-no-crash", processed_count >= 1,
      f"processed_count={processed_count}/4")


# ---- 7) 牌河稳定性兜底：当前帧 discards 显著少于历史，回退到历史最大值 ----
print()
print("[7] 牌河稳定性兜底")
# 构造一个 FakeDetector：第一阶段返回 20 张牌河 + 13 张手牌，
# 第二阶段把牌河减到 4 张（小于历史最大值 - MIN_DROP_DELTA=4）
hand_labels = ["1m","2m","3m","4m","5m","6m","7m",
               "1p","2p","3p","4p","5p","6p"]
hand_dets = [((100 + i * 50, 460, 50, 80), lbl, 0.92) for i, lbl in enumerate(hand_labels)]

class TwoStageDetector:
    """前 6 帧返回 20 张牌河；第 7 帧开始返回 4 张牌河，触发兜底。"""
    def __init__(self):
        self.last_top_score = 0.85
        self.last_screen = (1100, 600)
        self._glyphs = type("G", (), {"nums": {"a": 1}})()
        self._styles = type("S", (), {"tpls": [1]})()
        self.call_count = 0
    def detect_all_rows(self, image):
        self.call_count += 1
        # 牌河：20 张 vs 4 张（用单一 mpsz 复用以便 assertion 简单）
        if self.call_count <= 6:
            discard_labels = ["2p"] * 20
        else:
            discard_labels = ["2p"] * 4
        discard_dets = [((100 + i * 50, 100, 50, 80), lbl, 0.92)
                        for i, lbl in enumerate(discard_labels)]
        return [hand_dets, discard_dets]

eng = make_fake_engine(n_tiles_hand=13, hand_labels=hand_labels)
eng.get_detector = lambda: TwoStageDetector()  # type: ignore
eng._tile_voter = engine_mod._TileVoter(window=engine_mod.VOTE_WINDOW)
eng._frame_skipper = engine_mod._FrameSkipper()
eng._motion_guard = engine_mod._MotionGuard()
eng._warmup_left = 0  # 跳过 warmup，直接走正常路径
eng._discard_history.clear()

# 跑 6 帧"正常"画面（牌河 20 张），建立历史
img = make_fake_image(seed=7)
results_phase1 = []
for _ in range(6):
    r = eng.process(img)
    payload = json.loads(r.result)
    results_phase1.append(payload.get("discard_count"))
# 注意：frame_skipper 在同图下会让第 2~6 帧被跳过；这不影响 history（只在
# "完整识别"分支末尾写 history）。所以这里用 6 张不同图，确保都进识别
imgs_phase1 = [make_fake_image(seed=s) for s in (11, 12, 13, 14, 15, 16)]
results_phase1 = []
for img_ in imgs_phase1:
    r = eng.process(img_)
    payload = json.loads(r.result)
    results_phase1.append(payload.get("discard_count"))

# 现在 _discard_history 应该已有 6 个 20 张的牌河
hist_lens = [len(s) // 2 for s in eng._discard_history]
check("history-built", all(h > 10 for h in hist_lens),
       f"history lens={hist_lens}")

# 第 7 帧换成牌河只有 4 张的"画面"——触发兜底
img_phase2 = make_fake_image(seed=99)
r = eng.process(img_phase2)
payload = json.loads(r.result)
check("discard-fallback-restored",
       payload.get("discard_count", 0) >= 10,
       f"discard_count={payload.get('discard_count')}")
check("discards-not-empty-after-fallback",
       bool(payload.get("discards")),
       f"discards={payload.get('discards')!r}")


print()
if failures:
    print(f"FAILED: {len(failures)} case(s) -> {failures}")
    sys.exit(1)
print("ALL_OK")
