"""引擎集成测试：验证 StructuralDetector 接入 Engine 后全流程可用。

用法: python localtest/test_engine.py [截图路径]
"""
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402
from engine.engine import _HandStabilizer, _counter_to_mpsz  # noqa: E402
from collections import Counter  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SHOT = os.path.join(HERE, "screenshot.jpg")

# 期望手牌（与 run_structural 的 GT 一致，去掉花色分隔）
EXPECTED = "3m9m1s6s7s9s9s1p3p3p4p4p5p"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SHOT
    image = cv2.imread(path)
    assert image is not None, f"cannot read {path}"

    # 模拟设备端 JPEG50
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 50])
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    eng = Engine()
    res = eng.process(image)
    assert res is not None, "Engine.process returned None"
    data = json.loads(res.result)

    print("hand    :", data["hand"])
    print("count   :", data["count"])
    print("status  :", data["status"])
    print("tiles   :", len(data["tiles"]))
    print("top_score:", data["top_score"])
    print("screen  :", data["screen"])

    ok_hand = data["hand"] == EXPECTED
    ok_count = data["count"] == 13
    ok_status = data["status"] == "ok"
    ok_tiles = len(data["tiles"]) == 13

    print("\n=== 校验 ===")
    print(f"hand=={EXPECTED}: {ok_hand}")
    print(f"count==13     : {ok_count}")
    print(f"status==ok    : {ok_status}")
    print(f"tiles==13     : {ok_tiles}")

    # 校验 tiles 里低置信位是否被过滤（本图全高置信，应全部保留 label）
    labelled = sum(1 for t in data["tiles"] if t[4] != "")
    print(f"labelled tiles: {labelled}/13")

    assert ok_hand and ok_count and ok_status and ok_tiles, "ENGINE INTEGRATION FAILED"
    print("\nENGINE INTEGRATION OK")
    _test_phase2_state_machine()
    _test_phase3_perf()
    _test_phase4_accuracy()


def _test_phase2_state_machine():
    """阶段 2 状态机纯逻辑断言：空帧宽限 / 互斥校验 / 稳定器 2 帧共识。
    不依赖真实图像，直接测 _HandStabilizer 与 Engine._check_dup_explosion。"""
    print("\n=== 阶段 2 状态机断言 ===")

    # 1) 空帧宽限：建立稳定手牌后单帧空不立即清空，连续 4 帧空才重置
    stab = _HandStabilizer()
    hand = ["3m", "9m", "1s", "6s", "7s", "9s", "9s",
            "1p", "3p", "3p", "4p", "4p", "5p"]  # 13 张
    first = stab.observe(hand, {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
    assert first == "".join(hand), f"冷启动首帧未立住稳定手牌: {first!r}"
    kept = stab.observe([], {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
    assert kept == first, f"单帧空应宽限保留稳定手牌，实得 {kept!r}"
    for _ in range(3):
        stab.observe([], {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
    cleared = stab.observe([], {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
    assert cleared == "", f"连续>=4帧空应彻底重置，实得 {cleared!r}"
    print("[PASS] 空帧宽限：单帧保留稳定手牌，连续4帧空才重置")

    # 2) 2 帧共识：陌生突变牌型首帧不得直接上屏（_streak>=1 短路已删）
    stab2 = _HandStabilizer()
    stab2.observe(["1m", "2m", "3m", "4m", "5m", "6m", "7m",
                   "8m", "9m", "1p", "2p", "3p", "4p"],
                  {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
    # 换成完全不同的 13 张（张数相同、diff>4）：首帧应被拒（返回旧稳定值）
    new_hand = ["5s", "6s", "7s", "8s", "9s", "1s", "2s",
                "3s", "4s", "7p", "8p", "9p", "6p"]
    r = stab2.observe(new_hand, {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
    assert r != "".join(new_hand), "陌生大改牌型首帧不应立即采纳（2帧共识失效）"
    r2 = stab2.observe(new_hand, {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
    assert r2 == "".join(new_hand), "连续 2 帧一致后应采纳新牌型"
    print("[PASS] 稳定器 2 帧共识：陌生大改需连续 2 帧才采纳")

    # 3) 互斥校验 _check_dup_explosion：同字 >4 判 True
    eng = Engine()
    assert eng._check_dup_explosion(_counter_to_mpsz(Counter(["5z"] * 5))), "同字5张应判爆屏"
    assert not eng._check_dup_explosion(_counter_to_mpsz(Counter(["5z"] * 4 + ["1m"] * 4))), "各4张合法"
    assert not eng._check_dup_explosion(""), "空串不判爆屏"
    print("[PASS] 互斥校验：同字>4判本帧不可信")

    print("\nPHASE2 STATE MACHINE OK")


def _test_phase3_perf():
    """阶段 3 性能重构纯逻辑断言：向量化帧差与原循环逐块相等；
    分类快路径的高分早停与内容缓存命中。不依赖真实模板库。"""
    from engine.engine import (
        _block_diff_signature, _classify_tile_fast,
        FRAME_DIFF_BLOCK, _RIVER_ROT_PREF,
    )
    print("\n=== 阶段 3 性能断言 ===")

    # 1) 向量化 _block_diff_signature 必须与旧逐块循环逐元素相等（非整除尺寸）
    def _ref_loop(g, bh):
        h, w = g.shape[:2]
        rows = max(1, h // bh)
        cols = max(1, w // bh)
        sig = np.zeros((rows * cols,), dtype=np.float32)
        for r in range(rows):
            for c in range(cols):
                patch = g[r * bh:(r + 1) * bh, c * bh:(c + 1) * bh]
                sig[r * cols + c] = float(patch.mean()) if patch.size else 0.0
        return sig

    rng = np.random.default_rng(7)
    img = rng.integers(0, 256, size=(100, 140), dtype=np.uint8)
    vec = _block_diff_signature(img)
    ref = _ref_loop(img, FRAME_DIFF_BLOCK)
    assert vec.shape == ref.shape and np.allclose(vec, ref), "向量化帧差与循环不等价"
    # 极小图（小于一个块）走兜底循环，仍等价
    tiny = rng.integers(0, 256, size=(10, 10), dtype=np.uint8)
    assert np.allclose(_block_diff_signature(tiny), _ref_loop(tiny, FRAME_DIFF_BLOCK))
    print("[PASS] 向量化帧差与旧循环逐块相等（含非整除/极小图）")

    # 2) _classify_tile_fast：高分首旋转即早停（1 次分类）+ 同切片命中缓存（第 2 次 0 次分类）
    class _StubDetector:
        def __init__(self):
            self.calls = 0
        def _probe_style(self, crop):
            return "tencent"
        def classify_tile(self, crop, avail=None, styles=None):
            self.calls += 1
            return ("1m", 0.95)

    det = _StubDetector()
    crop = rng.integers(0, 256, size=(30, 24, 3), dtype=np.uint8)
    avail = {0}  # 1m 的 tile34 索引
    pref = _RIVER_ROT_PREF["bottom"]
    cache = {}
    lbl, sc = _classify_tile_fast(det, crop, avail, pref, cache)
    assert lbl == "1m" and sc >= 0.9, f"分类快路径结果异常: {lbl},{sc}"
    assert det.calls == 1, f"高分应早停只分类1次，实得 {det.calls}"
    _classify_tile_fast(det, crop, avail, pref, cache)
    assert det.calls == 1, f"同切片应命中缓存不再分类，实得 {det.calls}"
    print("[PASS] 分类快路径：高分早停 + 内容缓存命中")

    print("\nPHASE3 PERF OK")


def _test_phase4_accuracy():
    """阶段 4 精度加固纯逻辑断言：双账本合并/两帧确认/多帧一致回退、
    grid 投票恢复后的防幽灵透传、RIVER_CONF 默认值、set_hand_strip_top 覆盖。"""
    from engine.engine import _TileVoter, RIVER_CONF, RIVER_REGRET_FRAMES
    from recognition.tencent_grid_detector import TencentGridDetector
    print("\n=== 阶段 4 精度断言 ===")

    # 1) 双账本：视觉两帧确认递增 + 手牌差分独立账本 + max 合并 + 多帧一致回退
    eng = Engine()
    eng._update_visual_ledger(["1m", "1m"])   # 帧1：cnt=2 → pending，视觉账本未确认
    assert eng._visual_discards["1m"] == 0 and eng._pending_discards["1m"] == 2
    eng._update_visual_ledger(["1m", "1m"])   # 帧2：连续确认 → 视觉=2
    assert eng._visual_discards["1m"] == 2 and eng._monotonic_discards["1m"] == 2
    # 手牌差分推断独立账本，与视觉 max 合并
    eng._inferred_discards["5p"] = 3
    eng._update_visual_ledger([])
    assert eng._monotonic_discards["5p"] == 3
    # 1m 无 inferred 支撑、连续缺席 → 回退删除（从单调牌池也同步传播）
    for _ in range(RIVER_REGRET_FRAMES + 1):
        eng._update_visual_ledger([])
    assert "1m" not in eng._monotonic_discards, "虚高视觉计数应经多帧一致回退"
    assert eng._monotonic_discards["5p"] == 3, "有 inferred 支撑的不得回退"
    print("[PASS] 双账本：两帧确认+max合并+多帧一致回退")

    # 2) grid 投票恢复：单帧透传 + 位置突变时新位置透传（不整帧空白）
    v = _TileVoter(window=4)
    f1 = [((0, 100, 40, 60), "1m", 0.9), ((50, 100, 40, 60), "2m", 0.9)]
    v.push(f1)
    assert [d[1] for d in v.vote()] == ["1m", "2m"], "单帧应透传"
    f2 = [((300, 100, 40, 60), "3m", 0.9), ((350, 100, 40, 60), "4m", 0.9)]
    v.push(f2)
    assert [d[1] for d in v.vote()] == ["3m", "4m"], "位置突变应透传而非整帧置空"
    print("[PASS] grid 投票恢复：透传防幽灵")

    # 3) 门槛统一：可配参数默认值与旧硬编码一致（不改接受/拒绝行为）
    assert RIVER_CONF["min_conf"] == 0.42 and RIVER_CONF["vote_conf"] == 0.78
    assert RIVER_CONF["hand_top"] == 0.76 and RIVER_CONF["center_x"] == (0.42, 0.58)
    print("[PASS] 门槛统一：RIVER_CONF 默认与旧魔法数字一致")

    # 4) 手牌带上沿 set_hand_strip_top 覆盖（含非法入参与恢复默认）
    class _Stub:
        pass
    s = _Stub()
    TencentGridDetector.set_hand_strip_top(s, 0.6)
    assert s._hand_top_frac == 0.6 and s._hand_top_override is True
    TencentGridDetector.set_hand_strip_top(s, None)
    assert s._hand_top_frac == 0.68 and s._hand_top_override is False
    TencentGridDetector.set_hand_strip_top(s, 5)  # 超出 [0.3,0.9] → 忽略
    assert s._hand_top_frac == 0.68 and s._hand_top_override is False
    print("[PASS] set_hand_strip_top 覆盖/恢复/非法入参防御")

    print("\nPHASE4 ACCURACY OK")


if __name__ == "__main__":
    main()
