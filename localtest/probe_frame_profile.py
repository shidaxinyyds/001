# -*- coding: utf-8 -*-
"""一帧 255ms 里那「未归类」的 138ms 到底是谁：给 process 做函数级采样。

口径与 `cost_split.py` 一致（platform=tencent、mode=sc_hz、逐帧 fresh Engine、
同一 jpeg 质量 50 预处理），只是换成 cProfile：wrapper 只能量「我提前想到要量的
那些函数」，而 138ms/帧 落在 wrapper 之外 —— 那必然是我没想到的函数。

cProfile 有解释器开销（绝对值会偏大），所以本表只用于**排序与定位**，不当耗时结论；
要钱的账仍然看 `build/cost_split.txt`。

运行：py -3.10 -X utf8 localtest/probe_frame_profile.py [--n 8]
输出：build/probe_frame_profile.txt
"""
from __future__ import annotations

import cProfile
import contextlib
import io
import json
import os
import pstats
import sys
from io import StringIO

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "probe_frame_profile.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

import engine.engine as EE  # noqa: E402


def frames(n_lim):
    with open(GT_PATH, encoding="utf-8") as fp:
        gt = json.load(fp)["shots"]
    out = []
    for s in gt:
        if not s.get("verified"):
            continue
        img = cv2.imread(os.path.join(SHOT_DIR, s["file"]))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        out.append((s["file"], cv2.imdecode(buf, cv2.IMREAD_COLOR)))
        if n_lim and len(out) >= n_lim:
            break
    return out


def main() -> int:
    argv = sys.argv[1:]
    n_lim = int(argv[argv.index("--n") + 1]) if "--n" in argv else 8
    warm = "--warm" in argv
    fs = frames(n_lim)
    pr = cProfile.Profile()
    wall = []
    shared = EE.Engine() if warm else None
    skipped = 0
    for _f, img in fs:
        eng = shared if warm else EE.Engine()
        eng.set_platform("tencent")
        eng.set_mode("sc_hz")
        if warm and skipped < 2:      # 前 2 帧是初始化（ONNX/模板/import），不该混进稳态表
            skipped += 1
            with contextlib.redirect_stdout(io.StringIO()):
                eng.process(img)
            continue
        import time
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            pr.enable()
            eng.process(img)
            pr.disable()
        wall.append((time.perf_counter() - t0) * 1000.0)
    if not wall:
        print("没有可统计的帧（--n 太小，全被当作初始化剔掉了），请用 --n >= 6")
        return 1
    buf = StringIO()
    st = pstats.Stats(pr, stream=buf)
    st.sort_stats("cumulative")
    st.print_stats(45)
    text = "\n".join([
        f"帧数 {len(fs)}（tencent/sc_hz，"
        f"{'常驻 Engine，已剔掉前 2 帧初始化' if warm else '逐帧 fresh'}）—— "
        f"fresh 口径会把只付一次的 `readNetFromONNX`/`_load_templates`/import 摊到每帧，"
        f"看稳态请加 --warm",
        f"本次带采样墙钟均值 {sum(wall) / len(wall):.1f}ms（cProfile 有开销，只看不比）",
        "",
        buf.getvalue(),
    ])
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
