# -*- coding: utf-8 -*-
"""「决策层再 memo 一层」到底值多少钱：三方对照实测，不靠推算。

背景（本段实测）：`build/probe_frame_profile.txt`（--warm）显示第二成本中心是决策层
`build_advice → sichuan_analyzer.analyze_discards` ≈24%，`can_win` 52463 次/10 帧；
`localtest/probe_advice_memo.py` 量到 `can_win` 的键重复率 56.8%。但**重复率高不等于
省时间**：构造键（`tuple(counts)` 28 个整数 + 哈希）本身要钱，`can_win` 的下层
（`_min_wild_melds` / `_min_wild_pair_melds`）已经是 lru 的，剩下的计算量可能和建键
同量级 —— 上一版探针的 advice 均值 69.9ms 远高于 `cost_split --warm` 的 23.9ms，
怀疑就是「计数器壳子」自己的钱。所以必须把壳子的成本单独量出来。

三个变体（同一批帧、同一个常驻 Engine 口径、先跑一遍预热、再轮转跑序各测 `--repeat` 遍）：
  base     原样
  keyonly  原样 + 只建键计数（不动计算）⇒ 它与 base 之差 = **建键的纯开销**
  memo     建键 + lru 记忆（命中就跳过计算）⇒ 它与 base 之差 = **这一刀净值**
判读规则：只有 `memo < base` 且差值有实际量级（≥MIN_GAIN_MS/帧）才值得落地；若
`keyonly` 已经比 base 慢，说明「建键」这条路本身就不划算，得换成「少产生子问题」。

必须预热（本段实测踩到的口径坑）：`_min_wild_melds` / `_is_tenpai_cached` 是进程级
lru，不随 Engine 重置。不预热时固定跑序下第一个变体替全局付冷缓存（实测 base 帧均
191.9ms / 决策 50.0ms，而后跑的 keyonly 165.4 / 5.7 —— 相差十倍），拿它当净值会得出
完全相反的结论。

行为不变量：三个变体的**决策子集**（hand/count/status/best/shanten/dingque_suit/
swap_phase/pick_phase/frame_skipped/match_phase.phase/advice 三元组）逐帧不一致的帧数
**不能超过噪声底**（= base 自己跑多遍互相不同的帧数）。整串 payload 不能当不变量：
实测 base 同帧连跑三遍都有帧不同（后台牌河线程赶不赶上本帧 + `elapsed` 墙钟），
拿不成立的不变量判红会把噪声误读成「memo 改了行为」。另：子集能成立本身就说明
后台不确定只影响牌河类字段，不影响决策。

运行：py -3.10 -X utf8 localtest/probe_advice_memo_ab.py [--n 20] [--repeat 3]
输出：build/probe_advice_memo_ab.txt
"""
from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import statistics
import sys
import time
from collections import Counter

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "probe_advice_memo_ab.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

import engine.engine as EE  # noqa: E402
import sichuan.sichuan_analyzer as SA  # noqa: E402

MEMO_MAXSIZE = 65536
# 落地判据：memo 相对 base 的每帧均值至少要快这么多毫秒
MIN_GAIN_MS = 5.0

ORIG_CAN_WIN = SA.SichuanAnalyzer.can_win    # bound classmethod（不要再传 cls）
VARANTS = ("base", "keyonly", "memo")


def load_frames(n_lim):
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


@functools.lru_cache(maxsize=MEMO_MAXSIZE)
def _memo_can_win(counts_key, num_fixed_melds, dingque_suit):
    """记忆层：键覆盖 (牌型多重集, 固定面子数, 定缺) —— `can_win` 是它的纯函数。"""
    return ORIG_CAN_WIN(list(counts_key), num_fixed_melds, dingque_suit)


def run_pass(frames, variant):
    """跑一遍全部帧，返回 (逐帧墙钟 ms, 逐帧决策 ms, 逐帧 payload, 建键统计)。

    决策阶段直接包 `Engine.build_advice` 计时，不用 `_perf_ms["advice"]`：那个 deque
    `maxlen=20`，超过 20 帧后「长度增量」会静默失效（漏掉后半段），而 37 帧才是
    稳态样本；自己计时也不受那层记账的分支影响。两个调用点（4997 主决策、5062
    试探态）都会计上，作为「决策阶段总量」是对的。

    补丁以 **staticmethod** 挂到类属性上，函数签名不带 cls：内部调用一律是
    `cls.can_win(counts, melds, dingque)`，三个位置参数直接落到函数上。挂普通函数
    会错（本探针实测踩过）：类属性上的普通函数**不会**自动绑 cls（那是实例访问才有的
    描述符行为），于是 counts 被当成 cls、melds 被当成 counts，`ORIG_CAN_WIN` 收到
    垃圾参数抛异常，被引擎的 try/except 吞掉 → 表面“payload 不一样”，实际是假补丁。
    所以挂完必须当场打一发做生效自检，不能等报表去猜。
    """
    keys = Counter()

    def key_only(counts, num_fixed_melds=0, dingque_suit=None):
        keys[(tuple(counts), num_fixed_melds, dingque_suit)] += 1
        return ORIG_CAN_WIN(counts, num_fixed_melds, dingque_suit)

    def memo(counts, num_fixed_melds=0, dingque_suit=None):
        key = (tuple(counts), num_fixed_melds, dingque_suit)
        keys[key] += 1
        return _memo_can_win(*key)

    patch = {"base": None, "keyonly": key_only, "memo": memo}[variant]
    if variant == "memo":
        _memo_can_win.cache_clear()

    build_ms = []
    _orig_build = EE.Engine.build_advice

    def build_timed(self, *a, **kw):
        t1 = time.perf_counter()
        try:
            return _orig_build(self, *a, **kw)
        finally:
            build_ms.append((time.perf_counter() - t1) * 1000.0)

    EE.Engine.build_advice = build_timed
    if patch is not None:
        SA.SichuanAnalyzer.can_win = staticmethod(patch)
        SA.SichuanAnalyzer.can_win([0] * 28, 0, None)   # 生效自检
        if not keys:
            SA.SichuanAnalyzer.can_win = ORIG_CAN_WIN
            EE.Engine.build_advice = _orig_build
            raise RuntimeError("补丁没挂上：can_win 调用没被记到（假测量，当场失败）")
        keys.clear()
    try:
        eng = EE.Engine()
        eng.set_platform("tencent")
        eng.set_mode("sc_hz")
        wall, payloads = [], []
        for _f, img in frames:
            t0 = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                res = eng.process(img)
            wall.append((time.perf_counter() - t0) * 1000.0)
            payloads.append(res.result)
        info = _memo_can_win.cache_info() if variant == "memo" else None
        return wall, build_ms, payloads, keys, info
    finally:
        EE.Engine.build_advice = _orig_build
        SA.SichuanAnalyzer.can_win = ORIG_CAN_WIN


def summarize(vals):
    """逐帧分布摘要（用于全样本墙钟行）。"""
    if not vals:
        return "无"
    return ("均 %6.1f 中位 %6.1f 最差 %6.1f" % (
        sum(vals) / len(vals), statistics.median(vals), max(vals)))


DECISION_KEYS = ("hand", "count", "status", "best", "shanten", "dingque_suit",
                 "swap_phase", "pick_phase", "frame_skipped")


def canon(pay: str) -> str:
    """取「决策可比子集」。

    为什么不直接比整串 payload：payload 里有 `elapsed`（墙钟）与后台线程（牌河/副露）
    赶不赶得上本帧这两个不确定源，整串字节相同这个不变量在本项目里**本来就不成立**
    （实测：base 自己连跑三遍，37 帧里每遍都有若干帧整串不同）。拿不成立的不变量
    去判红会把「噪声」误读成「memo 改了行为」，所以：子集当硬不变量，整串当信息性。
    """
    try:
        d = json.loads(pay)
    except Exception:
        return "<unparseable>"
    mp = d.get("match_phase") or {}
    adv = d.get("advice") or []
    sub = {k: d.get(k) for k in DECISION_KEYS}
    sub["phase"] = mp.get("phase")
    sub["adv"] = [(a.get("tile"), a.get("ukeire"), a.get("shanten")) for a in adv]
    return json.dumps(sub, sort_keys=True, ensure_ascii=False)


def mismatches(ref, other):
    return sum(1 for a, b in zip(ref, other) if a != b)


def main() -> int:
    argv = sys.argv[1:]

    def flag(name, dflt=None):
        return argv[argv.index(name) + 1] if name in argv else dflt

    n_lim = int(flag("--n")) if flag("--n") else None
    repeat = int(flag("--repeat", "3"))
    frames = load_frames(n_lim)

    # 预热：下层 `_min_wild_melds` / `_is_tenpai_cached` 是**进程级** lru，不随
    # Engine 重置。实测踩过的坑：不预热时“base→keyonly→memo”固定跑序下，第一
    # 个变体替后面所有变体付了冷缓存（12 帧：base 191.9ms/决策 50.0ms，keyonly
    # 165.4/5.7），结论会完全反了。所以先跑一遍丢弃，再轮转跑序抵消残余漂移。
    run_pass(frames, "base")

    passes = {v: [] for v in VARANTS}
    payloads, raw_payloads = {}, {}
    for rp in range(repeat):
        order = VARANTS[rp % len(VARANTS):] + VARANTS[:rp % len(VARANTS)]
        for v in order:
            wall, advice, pays, keys, info = run_pass(frames, v)
            passes[v].append((wall, advice, keys, info))
            payloads.setdefault(v, []).append([canon(p) for p in pays])
            raw_payloads.setdefault(v, []).append(pays)
            print("[预热后 %d/%d] %-8s 帧均 %.1fms build_advice 帧均 %.1fms" % (
                rp + 1, repeat, v, sum(wall) / len(wall),
                sum(advice) / len(advice) if advice else 0.0))

    # ---- 噪声底：同一变体（base）跑多遍，决策子集也必须逐帧相同 ----
    # 不自检就拿「三变体互比」当结论是假的：引擎本身不确定时，比较什么都是白比较。
    noise = sum(mismatches(payloads["base"][0], pays)
                for pays in payloads["base"][1:])
    noise_raw = sum(mismatches(raw_payloads["base"][0], pays)
                    for pays in raw_payloads["base"][1:])
    # ---- 硬不变量：子集上的不一致量不能超过噪声底（超了 = 行为真被改了）----
    variant_diff = {}
    for v in VARANTS[1:]:
        variant_diff[v] = sum(mismatches(payloads["base"][pi], pays)
                              for pi, pays in enumerate(payloads[v]))
    diff_frames = {v: n for v, n in variant_diff.items() if n > noise}

    def mean_wall(v):
        return sum(sum(p[0]) / len(p[0]) for p in passes[v]) / len(passes[v])

    def mean_stage(v):
        vals = [x for p in passes[v] for x in p[1]]
        return sum(vals) / len(vals) if vals else 0.0

    def per_pass_wall(v):
        return " ".join("%.1f" % (sum(p[0]) / len(p[0])) for p in passes[v])

    all_wall = [x for p in passes["base"] for x in p[0]]
    pooled_keys = Counter()
    for p in passes["memo"]:
        pooled_keys.update(p[2])
    info = passes["memo"][-1][3]
    base_wall, ko_wall, mm_wall = (mean_wall("base"), mean_wall("keyonly"),
                                   mean_wall("memo"))
    base_stage, mm_stage = mean_stage("base"), mean_stage("memo")
    n_calls = sum(pooled_keys.values())
    n_uniq = len(pooled_keys)

    lines = [
        "帧数 %d（tencent/sc_hz，常驻 Engine，先跑 1 遍预热进程级 lru，再轮转跑序各测 %d 遍）"
        % (len(frames), repeat),
        "",
        "单帧墙钟均值（ms）：",
    ]
    for v in VARANTS:
        lines.append("  %-8s 均 %6.1f（各遍：%s）" % (v, mean_wall(v), per_pass_wall(v)))
    lines += [
        "  → 建键纯开销（keyonly - base）%+0.1fms/帧；memo 净值（memo - base）%+0.1fms/帧"
        % (ko_wall - base_wall, mm_wall - base_wall),
        "",
        "决策阶段（包 `build_advice` 直接计时，只统计走到决策的帧）：",
        "  base   均 %5.1fms（最差 %5.1f）  样本 %d 次调用" % (
            base_stage,
            max((max(p[1]) for p in passes["base"] if p[1]), default=0.0),
            sum(len(p[1]) for p in passes["base"])),
        "  memo   均 %5.1fms   ← 净值 %+0.1fms/帧" % (mm_stage, mm_stage - base_stage),
        "",
        "can_win 调用 %d 次，唯一键 %d 个（重复率 %.1f%%）" % (
            n_calls, n_uniq, (n_calls - n_uniq) / n_calls * 100 if n_calls else 0.0),
    ]
    if info:
        lines.append("lru 命中 %d / 未命中 %d（命中率 %.1f%%），占用 %d/%d" % (
            info.hits, info.misses,
            info.hits / max(info.hits + info.misses, 1) * 100,
            info.currsize, info.maxsize))
    gain = base_wall - mm_wall
    lines += [
        "",
        "行为一致性（决策子集不一致帧数，上限 = 噪声底 %d）：%s" % (
            noise, "、".join("%s=%d" % (v, n) for v, n in variant_diff.items())),
        "  信息性：整串 payload 连 base 自己跑多遍都有 %d 帧不同（后台线程/elapsed），"
        "所以整串不能当不变量" % noise_raw,
        "全样本单帧墙钟（base，%d 遍合起来）：%s" % (
            len(passes["base"]), summarize(all_wall)),
        "判据：memo 净值 ≥ %.1fms/帧、子集不一致不超噪声底、补丁确实生效（调用>0）"
        % MIN_GAIN_MS,
        "结论：%s" % (
            "净值 %.1fms/帧 → 值得落地" % gain
            if (gain >= MIN_GAIN_MS and not diff_frames and n_calls > 0)
            else "净值 %.1fms/帧 → 此路不通（决策层不是稳态成本中心，"
                 "继续看手牌行识别与 orient）%s" % (
                gain, "" if n_calls else "；且补丁没生效，本次测量无效")),
    ]
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print()
    print(text)
    if n_calls == 0:
        print("[红] can_win 调用 0 次：补丁没生效，本次测量无效")
        return 2
    return 1 if diff_frames else 0


if __name__ == "__main__":
    sys.exit(main())
