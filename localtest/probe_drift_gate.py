# -*- coding: utf-8 -*-
"""临时剖析：慢漂移串里逐帧打印跳帧门的每一项读数（⑤ 的夹具设计依据）。

运行：py -3.10 -X utf8 localtest/probe_drift_gate.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_skip_frame_guard as G  # noqa: E402


def main() -> int:
    thr = G._drift_threshold()
    name0, img0 = G._frames(1)[0]
    full_h = int(img0.shape[0])
    eng = G.quiet_engine()
    warm = G.warm_until_skip(eng, img0, name0, full_h)
    print(f"热身 {len(warm)} 帧后进入静默；阈值={thr}")
    plan, tried = G._drift_plan(eng, img0)
    print(f"标定 plan={plan}  (候选 {len(tried)} 组，窗口={G.DRIFT_STEP_FRAC})")
    if plan is None:
        return 1
    rect, alpha, step = plan
    series = G._drift_series(img0, rect, alpha)
    rows = G._run_drift(eng, img0, series, name0, full_h)
    for i, (d, r) in enumerate(rows):
        print(f"#{i:<2} drift={d:6.2f} adj={r.diff:6.2f} skipped={int(r.skipped)} "
              f"calls={r.calls} pending={int(r.pending)} started={int(r.match_started)} "
              f"stable={r.stable_count} warm={r.warmup_left} ntable={r.non_table} "
              f"count={r.payload.get('count')} conf={r.payload.get('top_score')}")
    print("--- 再喂最后一帧 4 次（锚点是否被识别帧重置）---")
    last = series[-1]
    for k in range(4):
        r = G.FrameLog(f"tail#{k}", eng, last, full_h)
        print(f"tail#{k} adj={r.diff:6.2f} skipped={int(r.skipped)} calls={r.calls} "
              f"pending={int(r.pending)} stable={r.stable_count} ntable={r.non_table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
