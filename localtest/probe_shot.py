# -*- coding: utf-8 -*-
"""单图深挖：打印方向锁定、hand_roi 实际裁切矩形、检测器原始行输出。

用法: py -3.10 localtest/probe_shot.py 09 [weile]
"""
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402
from engine.engine import _face_brightness, MIN_FACE_BRIGHTNESS  # noqa: E402
from engine.engine import MIN_TILE_ASPECT, MAX_TILE_ASPECT  # noqa: E402
from platforms import get_hand_roi  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")

PLATFORM_OF = {i: "weile" for i in range(1, 13)}
PLATFORM_OF[13] = "shushan"
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"


def run(img, platform, roi=None):
    """roi=None 走平台预设；给定 (top,bottom) 则覆盖，用于双裁切对照实验。"""
    eng = Engine()
    eng.set_platform(platform)
    if roi is not None:
        eng._roi = roi
    res = eng.process(img)
    d = json.loads(res.result)
    tiles = [t for t in (d.get("tiles") or []) if len(t) > 5 and t[5] == "hand"]
    labels = [t[4] for t in tiles]
    return eng, d, labels


def main():
    idx = int(sys.argv[1])
    platform = sys.argv[2] if len(sys.argv) > 2 else PLATFORM_OF.get(idx, "generic")
    f = next(x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_"))
    img = cv2.imread(os.path.join(SHOTS, f))
    h, w = img.shape[:2]
    print(f"file={f} 原始 {w}x{h}")

    eng = Engine()
    eng.set_platform(platform)
    roi = get_hand_roi(platform)
    print(f"hand_roi={roi} -> 原图绝对行 y {int(h*roi[0])}..{int(h*roi[1])}, "
          f"列 x {int(w*roi[2])}..{int(w*roi[3])}")

    res = eng.process(img)
    d = json.loads(res.result)
    print(f"orient={eng._orient}  match_started={getattr(eng,'_match_started',None)}  "
          f"status={d.get('status')} count={d.get('count')} top={d.get('top_score')}")
    print(f"screen={d.get('screen')} hand={d.get('hand')}")
    tiles = [t for t in (d.get("tiles") or []) if len(t) > 5 and t[5] == "hand"]
    print(f"hand tiles ({len(tiles)}):")
    for t in tiles:
        print(f"   rect(x={t[0]},y={t[1]},w={t[2]},h={t[3]}) label={t[4]!r}")

    # 阶段门控现场：_match_started 为何始终 False
    from modes import is_dingque_mode  # noqa: E402
    print(f"mode={eng.mode} is_dingque_mode={is_dingque_mode(eng.mode)} "
          f"phase_cache(swap,dq,pick)={getattr(eng,'_phase_cache',None)} "
          f"swap_raw={getattr(eng,'_swap_raw',None)} "
          f"confirm_frames={getattr(eng,'_phase_confirm_frames',None)} "
          f"swap_hold={getattr(eng,'_swap_hold_frames',None)} "
          f"pick_cands={len(getattr(eng,'_pick_cand_cache',[]) or [])} "
          f"stable_n={getattr(eng,'_stable_hand_count',None)} "
          f"non_table={eng._non_table_frames}")

    # 检测器在"方向归一后"的整图上直接跑一遍，看原始行
    det = eng.get_detector()
    norm = eng._apply_orientation(img.copy())
    print(f"方向归一后尺寸 {norm.shape[1]}x{norm.shape[0]}")
    rows = det.detect_all_rows(norm, classify=True, allow_rotation=False)
    for i, r in enumerate(rows):
        ys = [t[0][1] for t in r] or [0]
        print(f"  row{i} n={len(r)} y0={min(ys)} labels={[t[1] for t in r]}")
        print(f"        confs={[round(float(t[2]), 3) for t in r]}")
        bright = [round(float(_face_brightness(norm, t[0])), 1) for t in r]
        print(f"        face_brightness={bright} MIN={MIN_FACE_BRIGHTNESS}")
        asp = [round(t[0][2] / float(t[0][3]), 3) for t in r if t[0][3] > 0]
        print(f"        aspect={asp} 允许区间[{MIN_TILE_ASPECT},{MAX_TILE_ASPECT}] "
              f"越界数={sum(1 for a in asp if a < MIN_TILE_ASPECT or a > MAX_TILE_ASPECT)}")

    # ===== ROI 对照实验：取消裁切 / 放宽上沿，看双裁切是否为失效主因 =====
    print("\nROI 对照（top,bottom -> 引擎报出手牌数）:")
    cases = [("预设", None)]
    for top in (0.50, 0.58, 0.62, 0.66):
        cases.append((f"({top},1.00)", (top, 1.00)))
    cases.append(("不裁切", (0.0, 1.0)))
    for name, r in cases:
        try:
            _e, _d, _l = run(img, platform, r)
            print(f"  {name:12} count={_d.get('count'):>3} "
                  f"status={_d.get('status'):8} {''.join(x for x in _l if x)[:46]}")
        except Exception as e:
            print(f"  {name:12} EXC {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
