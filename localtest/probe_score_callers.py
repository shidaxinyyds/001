# -*- coding: utf-8 -*-
"""钱到底付在谁头上：给 `_score_face` 打上「调用入口」标签再统计。

动机（`build/cost_split.txt` 实测）：37 帧里单帧均值 255ms，`detect_all_rows` /
`detect_hand_strip` 只占 100ms，剩下 ~138ms/帧「未归类」；而逐帧看更怪 ——
t1.jpg 整帧 470ms、条带入口只 51ms，可内层 ` _score_face` 记到 32 次 / 431ms。
说明有一笔同步的牌面打分**不在**手牌行入口下面。本脚本把它找出来：按
「engine.py 里最外层的调用函数 + detector 里最近的公开入口」聚合墙钟与次数。

运行：py -3.10 -X utf8 localtest/probe_score_callers.py [--n 12]
输出：build/probe_score_callers.txt
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
import traceback
from collections import defaultdict

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "probe_score_callers.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

import engine.engine as EE  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector as DET  # noqa: E402

DET_FILE = os.path.join(PYROOT, "recognition", "tencent_grid_detector.py")
ENG_FILE = os.path.join(PYROOT, "engine", "engine.py")
PUBLIC_ENTRY = ("detect_all_rows", "detect_hand_strip", "classify_tile",
                "detect_river", "detect_melds", "_score_row")

ACC = defaultdict(lambda: [0.0, 0])      # tag -> [ms, calls]
CHAIN = defaultdict(lambda: [0.0, 0])    # 叶子链 -> [ms, calls]
CHAIN_SIG = {}                           # eng_fn -> 一条样本链
TOTAL = []


def _tag() -> str:
    """栈上找：engine.py 最外层函数 + detector 里最近的公开入口。

    额外记一条「叶子链」4 帧：光看 `process ← classify_tile` 说不出是谁在同步打分
    （`process` 本体里根本没有 `classify_tile` 调用点，那这个读数到底从哪条子链来），
    归因不到具体行就等于没测。
    """
    stack = traceback.extract_stack()
    eng_fn = "?"
    det_fn = "-"
    for fr in stack:
        if fr.filename == ENG_FILE:
            eng_fn = fr.name
        elif fr.filename == DET_FILE and fr.name in PUBLIC_ENTRY:
            det_fn = fr.name        # 取最靠近叶子的那个（后面的会覆盖前面的）
    chain_f = [fr.name for fr in stack if fr.filename in (ENG_FILE, DET_FILE)]
    tail = "<".join(chain_f[-9:]) or "(无 eng/det 帧)"
    CHAIN.setdefault(tail, [0.0, 0])   # 只建键；次数/时间由 `timed` 的 finally 记（不双计）
    CHAIN_SIG[eng_fn] = tail
    return f"{eng_fn} ← {det_fn}"


def main() -> int:
    argv = sys.argv[1:]
    n_lim = int(argv[argv.index("--n") + 1]) if "--n" in argv else None

    with open(GT_PATH, encoding="utf-8") as fp:
        gt = json.load(fp)["shots"]
    frames = []
    for s in gt:
        if not s.get("verified"):
            continue
        img = cv2.imread(os.path.join(SHOT_DIR, s["file"]))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        frames.append((s["file"], cv2.imdecode(buf, cv2.IMREAD_COLOR)))
        if n_lim and len(frames) >= n_lim:
            break

    orig = DET._score_face

    def timed(self, *a, **kw):
        t0 = time.perf_counter()
        tag = _tag()
        chain = CHAIN_SIG[tag.split(" ← ")[0]]
        try:
            return orig(self, *a, **kw)
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            ACC[tag][0] += dt
            ACC[tag][1] += 1
            CHAIN[chain][0] += dt
            CHAIN[chain][1] += 1
    DET._score_face = timed

    for f, img in frames:
        eng = EE.Engine()
        eng.set_platform("tencent")
        eng.set_mode("sc_hz")
        before = {k: list(v) for k, v in ACC.items()}
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            eng.process(img)
        dt = (time.perf_counter() - t0) * 1000.0
        TOTAL.append((dt, f, {k: (v[0] - before.get(k, [0.0, 0])[0],
                                  v[1] - before.get(k, [0, 0])[1])
                              for k, v in ACC.items()}))

    lines = [f"帧数 {len(TOTAL)}（tencent/sc_hz，逐帧 fresh Engine）",
             f"单帧均值 {sum(d for d, _f, _s in TOTAL) / len(TOTAL):.1f}ms", ""]
    lines.append("按调用入口聚合（墙钟跨线程求和，只用于定位「钱在谁头上」）：")
    tot = sum(v[0] for v in ACC.values())
    for k, (ms, n) in sorted(ACC.items(), key=lambda x: -x[1][0]):
        lines.append("  %-46s %9.1fms  %5d 次  占总分 %.1f%%" % (
            k, ms, n, ms / tot * 100 if tot else 0.0))
    lines.append("")
    lines.append("最慢 6 帧的入口拆解：")
    for dt, f, spent in sorted(TOTAL, reverse=True)[:6]:
        top = sorted(spent.items(), key=lambda x: -x[1][0])[:4]
        lines.append("  %-26s 总 %7.1fms  %s" % (
            f[:26], dt, " | ".join("%s %.0fms/%d" % (k, v[0], v[1]) for k, v in top)))
    lines.append("")
    lines.append("函数链（只串 engine.py / detector 两个文件的帧）—— 回答「到底是谁在同步打分」：")
    for k, (ms, n) in sorted(CHAIN.items(), key=lambda x: -x[1][0])[:12]:
        lines.append("  %9.1fms %5d 次  %s" % (ms, n, k))
    text = "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
