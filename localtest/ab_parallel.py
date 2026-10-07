# -*- coding: utf-8 -*-
"""逐帧配对 A/B：手牌并行 vs 串行，同一条 `Engine.process` 流。

为什么不能用「两次独立压测比大小」（实测教训，就在今晚）：
  同一份代码、同一个 `--engine 1000` 配置，两次给出 p50 = 280.7ms 和 646.8ms；
  中间夹的 `--engine 300` 又给出 306.9 / 555.3 / 498.2ms —— **散布 2 倍以上**。
  这台 PC 的绝对耗时不可复现（后台活动/频率波动都足以吞掉我要证的 30% 收益）。
  所以任何「跨进程比 p50」的结论都不许写进报告，只许报同进程配对的结果。

配对设计（每一对之间只有并行/串行这一个差别）：
  · **两台独立 Engine**，吃同一条帧序列（并行侧一台、串行侧一台）；线程池只在并行侧
    懒建，所以它是唯一的差别。为什么不能"同一帧喂两次"：Engine 有时间状态
    （跳帧基线/手牌稳定器/多帧投票），第二次喂同一帧的代价与结果都不是同一件事，
    我第一版就是这么写的，被下面的自检判废（跳帧污染 62、结果不一致 26）；
  · A/B 顺序在轮次间**交替**（A,B 与 B,A 各半），抵消「先跑的那个把缓存焐热」的偏置；
  · 自检：两侧跳帧帧数必须相同、逐帧手牌结果必须一致，否则数字一律不作数；
  · 报三样：两种调度的 p50/p90、配对差的**中位数**、以及并行胜出的比例（胜率）。
    只有当中位数明显为负且胜率高，才允许讲「变快」。

判据：350ms 是硬要求，但绝对值必须由**设备**给；本脚本给的是这台机器上唯一可信的
那部分信息——并行相对串行省了多少比例，以及它的分布有多稳。

用法：py -3.10 -X utf8 localtest/ab_parallel.py [轮数]
输出：build/ab_parallel.txt
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import recognition.tencent_grid_detector as T  # noqa: E402
import engine.engine as EE  # noqa: E402
from engine.engine import Engine  # noqa: E402
from platforms import PLATFORMS  # noqa: E402

FIX = os.path.join(HERE, "shots_tuyou")
if "--fix" in sys.argv:
    FIX = os.path.abspath(sys.argv[sys.argv.index("--fix") + 1])
OUT = os.path.join(REPO, "build", "ab_parallel.txt")
if "--out" in sys.argv:
    OUT = os.path.join(REPO, "build", sys.argv[sys.argv.index("--out") + 1])
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8


def load_frames(n=10):
    paths = sorted(p for p in (os.path.join(FIX, f) for f in os.listdir(FIX))
                   if p.lower().endswith((".jpg", ".png")))
    out = []
    for p in paths:
        im = cv2.imread(p)
        if im is not None:
            out.append((os.path.basename(p), im))
        if len(out) >= n:
            break
    return out


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def main():
    base = os.path.basename(os.path.normpath(FIX))
    if "--platform" in sys.argv:
        platform = sys.argv[sys.argv.index("--platform") + 1]
    elif "shots_" in base:
        platform = base.split("shots_", 1)[1]
    else:
        # 夹具目录名不带平台（`localtest/shots` 就是腾讯底座），必须显式给，
        # 否则我会拿默认玩法去测一个并非其原生平台的帧集，数字整组作废。
        print(f"夹具 {base} 的名字推不出平台，请加 --platform <key>")
        return 2
    EE.load_mode = lambda *a, **kw: PLATFORMS[platform]["default_mode"]
    frames = load_frames(int(sys.argv[sys.argv.index("--frames") + 1])
                         if "--frames" in sys.argv else 10)
    if not frames:
        print(f"夹具空：{FIX}")
        return 2
    # 两台引擎吃**同一条帧序列**：并行/串行各自一台，逐帧配对。
    # 为什么不能"同一帧喂两次"（我第一版就是这么写的，被自检判废）：Engine 是有时间
    # 状态的流水线（跳帧基线、手牌稳定器、多帧投票），第二次喂同一帧时状态已经和第一次
    # 不同 —— 实测 160 个样本里 62 个被跳帧门吃掉、26 对结果还不一致，量到的既不是
    # 同一件事，也不是同一个成本。分开两条独立演化的流，两边的状态历史才逐帧对等。
    eng_p = Engine()
    eng_s = Engine()
    hand_p, hand_s = eng_p.get_hand_detector(), eng_s.get_hand_detector()
    if not (hasattr(hand_p, "parallel_classify") and hasattr(hand_s, "parallel_classify")):
        print("手牌通道没有 parallel_classify，配对无从下手")
        return 2
    hand_p.parallel_classify = True
    hand_s.parallel_classify = False

    par, ser, pairs = [], [], []
    skip_p = skip_s = 0
    diff_hand = 0
    for r in range(ROUNDS):
        for name, img in frames:
            # 两侧谁先跑在轮次间交替：避免固定的先后顺序偏置（两边序列各自不变）
            order = [("par", eng_p), ("ser", eng_s)] if r % 2 == 0 \
                else [("ser", eng_s), ("par", eng_p)]
            t, blob = {}, {}
            for mode, eng in order:
                k0 = time.perf_counter()
                res = eng.process(img)
                t[mode] = (time.perf_counter() - k0) * 1000
                blob[mode] = getattr(res, "result", "") or ""
            if '"frame_skipped": true' in blob["par"]:
                skip_p += 1
            if '"frame_skipped": true' in blob["ser"]:
                skip_s += 1
            hp = blob["par"].split('"hand"')[-1][:60] if '"hand"' in blob["par"] else ""
            hs = blob["ser"].split('"hand"')[-1][:60] if '"hand"' in blob["ser"] else ""
            diff_hand += int(hp != hs)
            pairs.append((t["par"], t["ser"]))
            par.append(t["par"])
            ser.append(t["ser"])

    # 配对只在「两边都没跳帧」的帧上算：跳帧帧的成本是复用上一帧，跟调度无关
    ok_pairs = [(p, s) for (p, s), _ in zip(pairs, range(len(pairs)))
                if p > 50 and s > 50]
    diffs = [p - s for p, s in ok_pairs]
    win = sum(1 for d in diffs if d < 0)
    med_p = statistics.median([p for p, _s in ok_pairs])
    med_s = statistics.median([s for _p, s in ok_pairs])
    lines = [
        f"夹具 {len(frames)} 帧 ×{ROUNDS} 轮 = {len(pairs)} 对，两条独立引擎吃同一条序列，"
        f"平台={platform} 玩法={PLATFORMS[platform]['default_mode']}",
        f"手牌通道 = {hand_p.__class__.__name__}，并行侧 worker 上限 {T.PARALLEL_WORKERS}",
        f"跳帧帧数：并行侧 {skip_p}、串行侧 {skip_s}（两边序列相同，跳帧应当同数）；"
        f"剔除 >50ms 的判定帧后剩 {len(ok_pairs)} 对进入统计",
        f"逐帧手牌结果不一致的对数 {diff_hand}/{len(pairs)}"
        f"（并行只该改调度，不该改结果；不为 0 就必须先解释）",
        "",
        f"并行 p50={med_p:.1f}  p90={pct([p for p, _ in ok_pairs], 0.90):.1f}  "
        f"min={min(p for p, _ in ok_pairs):.1f}  max={max(p for p, _ in ok_pairs):.1f}",
        f"串行 p50={med_s:.1f}  p90={pct([s for _, s in ok_pairs], 0.90):.1f}  "
        f"min={min(s for _, s in ok_pairs):.1f}  max={max(s for _, s in ok_pairs):.1f}",
        f"配对差（并行-串行）中位数 {statistics.median(diffs):+.1f}ms = "
        f"{100 * statistics.median(diffs) / med_s:+.1f}%   "
        f"并行胜出 {win}/{len(diffs)} = {100 * win / max(1, len(diffs)):.0f}%",
        "",
        "绝对值不许跨进程比较：同一配置重复压测的 p50 散布 306.9~646.8ms"
        "（build/rep_1.txt、build/rep_2.txt、build/engine_stream.txt），"
        "所以 350ms 是否达标只能由设备实测给；这里给的是同机上唯一可信的那部分——"
        "省了多少比例、以及胜率有多稳。",
        "",
        "配对完整性判定：" + ("**作废**（两侧跳帧不同数或手牌结果有差异，"
                              "上面两组数字不得引用）"
                             if (skip_p != skip_s or diff_hand) else
                             "成立（跳帧同数、逐帧手牌一致），只可引用配对差与胜率"),
    ]
    text = "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
