# -*- coding: utf-8 -*-
"""决策层还值不值得再切一刀：量 `can_win` 的重复子问题率（而不是猜）。

稳态剖析（`build/probe_frame_profile.txt`，--warm）给出的第二成本中心是决策层：
10 帧里 `build_advice` → `sichuan_analyzer.analyze_discards` 占 0.583s / 2.40s ≈ 24%，
其中 `can_win` 52463 次、`_is_tenpai_cached` 2841 次却**恰好** 2841 次进到
`_is_tenpai_impl` —— 听牌层的 lru 命中率是 0%（键全是唯一的）。
所以「再加一层 memo」有没有用，取决于 `can_win` 的键重复率，必须实测。

运行：py -3.10 -X utf8 localtest/probe_advice_memo.py [--n 12]
输出：build/probe_advice_memo.txt
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from collections import Counter

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "probe_advice_memo.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

import engine.engine as EE  # noqa: E402
import sichuan.sichuan_analyzer as SA  # noqa: E402


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
    n_lim = int(argv[argv.index("--n") + 1]) if "--n" in argv else 12
    fs = frames(n_lim)

    keys = Counter()
    orig = SA.SichuanAnalyzer.can_win

    def counted(cls, counts, num_fixed_melds=0, dingque_suit=None):
        keys[(tuple(counts), num_fixed_melds, dingque_suit)] += 1
        # `orig` 是从类上取下来的 **bound** classmethod（已经带上了 cls），
        # 再传 cls 会把 counts 当成第一个参数（实测会静默把牌型当成 melds 数）。
        return orig(counts, num_fixed_melds, dingque_suit)
    SA.SichuanAnalyzer.can_win = classmethod(counted)

    eng = EE.Engine()
    eng.set_platform("tencent")
    eng.set_mode("sc_hz")
    wall, advice_ms = [], []
    for i, (_f, img) in enumerate(fs):
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            eng.process(img)
        wall.append((time.perf_counter() - t0) * 1000.0)
        dq = eng._perf_ms.get("advice")
        advice_ms.append(list(dq)[-1] if dq else 0.0)
        if i == 0:
            del wall[-1]          # 首帧含初始化，不计
            del advice_ms[-1]
    SA.SichuanAnalyzer.can_win = orig

    total = sum(keys.values())
    uniq = len(keys)
    dup_calls = total - uniq
    top = keys.most_common(5)
    lines = [
        f"帧数 {len(wall)}（tencent/sc_hz，常驻 Engine，已剔首帧）",
        f"单帧均值 {sum(wall) / len(wall):.1f}ms，advice 阶段均值 "
        f"{sum(advice_ms) / len(advice_ms):.1f}ms（占 "
        f"{sum(advice_ms) / sum(wall) * 100:.1f}%）",
        "",
        f"can_win 调用 {total} 次，唯一键 {uniq} 个，重复命中 {dup_calls} 次"
        f"（{dup_calls / total * 100 if total else 0:.1f}%）",
        "→ 重复率就是「再加一层 memo」最多能省下的比例（其余是真·新子问题）。",
        "",
        "重复最多的 5 个键（次数）：",
    ]
    for k, n in top:
        c, m, d = k
        lines.append("  %5d 次  melds=%s dingque=%s 牌型=%s" % (
            n, m, d, "".join(str(x) for x in c[:28])))
    text = "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
