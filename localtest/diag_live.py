# -*- coding: utf-8 -*-
"""直播口径诊断：同一 Engine 连续喂真实帧，量两件事。

1) 耗时分解：每帧总耗时 + decode/detect/river/advice 四段，出 p50/p95/p99。
   冷喂口径（每帧新建引擎）量不出稳定器与跳帧的行为，而用户看到的"面板滞后"
   恰恰只存在于直播口径，所以这里必须复用同一个引擎。
2) 陈旧与滞后：把每帧输出的 hand 与本帧 GT 比，若不等，再看它等于前面第几帧的
   GT —— 等于第 k 帧前的真值就是"面板落后 k 帧"，乘 p50 就是毫秒级滞后。
   另做定向换局实验：同一平台先用 A 局建立稳定手牌，再切到 B 局连续喂，
   数第几帧才追上 B 的真值（这就是"开新的一局，面板还播上一局的牌"）。

用法：py -3.10 -X utf8 localtest/diag_live.py [--gt localtest/gt/shots_b1.json]
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
sys.path.insert(0, HERE)

import engine.engine as E  # noqa: E402
from engine.engine import Engine  # noqa: E402
from eval_new_material import STYLE_OF  # noqa: E402
from layer_cost import mode_for  # noqa: E402

DEFAULT_GT = os.path.join(HERE, "gt", "shots_b1.json")


def quiet(fn, *a, **k):
    """引擎每帧会 print 大量调试行，屏蔽掉以免淹没诊断输出。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def codes_of(mpsz: str):
    return [mpsz[i:i + 2] for i in range(0, len(mpsz), 2)]


def gt_known(hand):
    """GT 里有未标注格（素材债 `?`）。把它们当“无断言”剔掉再比，否则
    任何一帧都必然“不等”，把口径缺陷误读成引擎错判。返回 (已知格多重集,
    未知格数)。"""
    known = sorted(c for c in hand if c and c != "?")
    return known, len(hand) - len(known)


def matches(got, want, n_unknown):
    """输出是否对得上本帧真值：已知格逐张对上（多重集包含），且总张数不多不少。

    未知格不做牌面断言，但仍卡总数 —— 少牌/多牌是硬缺陷，不能拿 `?` 当兼底。
    """
    if len(got) != len(want) + n_unknown:
        return False
    rest = list(got)
    for c in want:
        if c in rest:
            rest.remove(c)
        else:
            return False
    return True


def pct(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    i = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[i]


def load_shots(path):
    with open(path, encoding="utf-8") as fp:
        shots = json.load(fp)["shots"]
    return [e for e in shots if e.get("verified") and e.get("hand")]


# ===== 阶段计时 =====
# 引擎自带的 _perf_ms 只有 decode/detect/river/advice 四个键，而实测最大的一笔
# ——方向验证（内部会跑一次完整手牌通道识别）与牌河/副露扫描——根本不在里面，
# 拿它做分解会把最贵的段归进“其他”。这里在诊断侧包一层计时，不改生产代码。
PROF = collections.defaultdict(float)


def _wrap(target, name, key):
    orig = getattr(target, name, None)
    if orig is None:
        return

    def wrapper(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            PROF[key] += (time.perf_counter() - t0) * 1000.0
    setattr(target, name, wrapper)


def instrument(eng):
    # 检测器是懒创建的：不在这里先取出来，getattr 会拿到 None，
    # 于是整个手牌通道（实测最大的一笔）在报表里显示为“-”。
    eng.get_detector()
    eng.get_hand_detector()
    _wrap(eng, "_verify_orientation", "orient_verify")
    _wrap(eng, "_settle_orientation", "orient_settle")
    _wrap(eng, "_probe_orientation", "orient_probe")   # 4 方向全探测 = 慢路径
    # 牌桌校验的手牌实证：≥.90 分支有 4 帧限频，<0.18 分支没有——必须分开量，
    # 否则它会被归进“其他”，而 cProfile 显示它是 jj/queshen 段的头号开销。
    _wrap(eng, "_verify_hand_evidence", "table_evidence")
    _wrap(eng, "_is_mahjong_table", "table_check")
    _wrap(eng, "build_advice", "advice")
    _wrap(eng, "_apply_hand_roi", "roi")
    for attr in ("_detector", "_hand_detector"):
        det = getattr(eng, attr, None)
        if det is not None:
            _wrap(det, "detect_all_rows", "detect_all_rows")
            _wrap(det, "detect", "detect")
    import engine.engine as _em
    for fn, key in (("detect_river_discards", "river_scan"),
                    ("detect_player_melds", "meld_scan")):
        f = getattr(_em, fn, None)
        if f is not None:
            def mk(f=f, key=key):
                def w(*a, **k):
                    t0 = time.perf_counter()
                    try:
                        return f(*a, **k)
                    finally:
                        PROF[key] += (time.perf_counter() - t0) * 1000.0
                return w
            setattr(_em, fn, mk())


def feed(eng, img, platform, mode):
    """喂一帧，返回 (总耗时ms, 结果dict, 阶段ms)。"""
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    for k in list(PROF):
        PROF[k] = 0.0
    t0 = time.perf_counter()
    try:
        res = quiet(eng.process, img)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm
    ms = (time.perf_counter() - t0) * 1000.0
    # 引擎自带四段只统计“最近 20 帧”，取本帧刚 append 的末尾元素
    own = {k: (dq[-1] if dq else None) for k, dq in getattr(eng, "_perf_ms", {}).items()}
    phases = dict(PROF)
    phases.update({f"own_{k}": v for k, v in own.items() if v})
    d = {}
    if res is not None:
        try:
            d = json.loads(res.result)
        except Exception:
            d = {"status": "unparseable"}
    return ms, d, phases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=DEFAULT_GT)
    ap.add_argument("--out", default=os.path.join(REPO, "build", "live_diag.txt"))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    shots = load_shots(a.gt)
    if a.limit:
        shots = shots[:a.limit]
    out = []

    def say(s=""):
        out.append(s)
        print(s)

    say(f"直播口径：同一 Engine 连续喂 {len(shots)} 帧（按 GT frame 序）")
    eng = Engine()
    quiet(eng.reset_match)
    instrument(eng)
    # 玩法按平台固定（取该平台第一帧 GT 能装下的玩法）：真实用户进平台选一次玩法
    # 就不会再动，逐帧换玩法会把 _hand_stab/_frame_skipper 每帧硬重置，量出来的
    # 滞后就不是滞后而是重置风暴。
    plat_mode = {}

    def plat_of(e):
        return STYLE_OF.get(e["style"], "tencent")

    def mode_of(e):
        p = plat_of(e)
        if p not in plat_mode:
            plat_mode[p] = mode_for(p, sorted(set(e["hand"])))[0]
        return plat_mode[p]

    totals, phases = [], collections.defaultdict(list)
    plat_ms = collections.defaultdict(list)      # 平台 -> 总耗时
    plat_ev = collections.defaultdict(list)      # 平台 -> 牌桌实证耗时
    rows = []
    gt_hist = []  # (index, 归一后的真值集合)
    stale = 0
    lag_hist = collections.Counter()
    skip = 0

    for idx, e in enumerate(shots):
        path = os.path.join(REPO, e["src"], e["file"])
        img = cv2.imread(path)
        if img is None:
            say(f"  #{e['frame']} 读图失败 {path}")
            continue
        plat = plat_of(e)
        mode = mode_of(e)
        ms, d, ph = feed(eng, img, plat, mode)
        totals.append(ms)
        plat_ms[plat].append(ms)
        if ph.get("table_evidence"):
            plat_ev[plat].append(ph["table_evidence"])
        for k, v in ph.items():
            if v is not None:
                phases[k].append(v)
        if d.get("frame_skipped"):
            skip += 1
        got = sorted(codes_of(d.get("hand") or ""))
        want, n_unknown = gt_known(e["hand"])
        gt_hist.append((idx, (want, n_unknown)))
        lag = None
        if matches(got, want, n_unknown):
            lag_hist[0] += 1
        else:
            stale += 1
            # 输出等于前面第几帧的真值？（只认同平台，跳平台相等无意义）
            for back in range(1, min(idx, 6) + 1):
                j = idx - back
                if shots[j]["style"] != e["style"]:
                    break
                w2, u2 = gt_hist[j][1]
                if matches(got, w2, u2):
                    lag = back
                    break
            # -1 = 既不是本帧真值、也不是任何历史帧真值（= 错判/残留，不是滞后）
            lag_hist[lag if lag is not None else -1] += 1
        rows.append((e["frame"], plat, mode, d.get("status"), len(got), len(want) + n_unknown,
                     ms, ph.get("orient_verify"), ph.get("table_evidence"),
                     ph.get("detect_all_rows"), ph.get("advice"), n_unknown,
                     d.get("frame_skipped"), lag))

    say("")
    say("== 耗时（Python 引擎侧，不含 Java 截图/编码/TCP/渲染）==")
    say(f"  n={len(totals)}  p50={pct(totals,50):.1f}  p95={pct(totals,95):.1f}  "
        f"p99={pct(totals,99):.1f}  max={max(totals):.1f}  跳帧={skip}")
    for k in ("orient_verify", "orient_settle", "detect_all_rows", "detect",
              "river_scan", "meld_scan", "advice", "roi"):
        v = phases.get(k) or []
        if v:
            say(f"  {k:<15} p50={pct(v,50):7.1f}  p95={pct(v,95):7.1f}  "
                f"max={max(v):7.1f}   (有样本 {len(v)})")

    say("")
    say("== 分平台（牌桌实证是每帧都付的固定开销，按平台看才知道谁在走限频分支）==")
    for p in sorted(plat_ms):
        ev = plat_ev.get(p) or []
        say(f"  {p:<13} 帧数={len(plat_ms[p]):3} 总p50={pct(plat_ms[p],50):8.1f}  "
            f"实证命中帧={len(ev):3} 实证p50={(pct(ev,50) if ev else 0.0):7.1f}  "
            f"实证占总={(100.0*sum(ev)/max(1.0,sum(plat_ms[p]))):5.1f}%")

    say("")
    say("== 阶段分解（诊断侧包装，p50/p95/max）==")
    for k in ("own_decode", "orient_verify", "orient_probe", "orient_settle",
              "table_check", "table_evidence",
              "detect_all_rows", "detect", "river_scan", "meld_scan", "advice", "roi"):
        v = phases.get(k) or []
        if v:
            say(f"  {k:<16} p50={pct(v,50):7.1f}  p95={pct(v,95):7.1f}  "
                f"max={max(v):7.1f}   (样本 {len(v)})")

    say("")
    say("== 面板真值符合度 ==")
    say(f"  输出手牌 ≠ 本帧 GT（已知格逐张对上且张数一致）：{stale}/{len(totals)} = "
        f"{100.0*stale/max(1,len(totals)):.1f}%")
    say("  滞后分布（0=当前真值，k=落后 k 帧同平台真值，-1=不等任何历史帧）")
    for k in sorted(lag_hist):
        say(f"    {k:>3} 帧 : {lag_hist[k]}")
    p50 = pct(totals, 50)
    worst = max([k for k in lag_hist if k > 0] or [0])
    say(f"  按 p50={p50:.0f}ms 折算：落后 {worst} 帧 ≈ {worst*p50:.0f}ms 陈旧")

    say("")
    say("== 逐帧明细（frame/平台/玩法/状态/输出张数/GT张数/总ms/方向验证/牌桌实证/手牌通道/建议/GT未知格/跳帧/滞后）==")
    for r in rows:
        say("  " + "  ".join(
            ("-" if x is None else (f"{x:.0f}" if isinstance(x, float) else str(x)))
            for x in r))

    # ===== 定向换局实验：A 局建立稳定 → 切 B 局，数几帧追上 =====
    say("")
    say("== 换局实验（同平台，A 局稳定后切 B 局）==")
    by_plat = collections.defaultdict(list)
    for e in shots:
        by_plat[e["style"]].append(e)
    for plat, es in sorted(by_plat.items()):
        if len(es) < 2:
            continue
        A, B = es[0], es[1]
        ia = cv2.imread(os.path.join(REPO, A["src"], A["file"]))
        ib = cv2.imread(os.path.join(REPO, B["src"], B["file"]))
        if ia is None or ib is None or A["style"] != B["style"]:
            continue
        plat = plat_of(A)
        mode = mode_of(A)
        eng2 = Engine()
        quiet(eng2.reset_match)
        for _ in range(3):
            feed(eng2, ia, plat, mode)
        seq = []
        want_b, unk_b = gt_known(B["hand"])
        for n in range(1, 6):
            ms, d, _ph = feed(eng2, ib, plat, mode)
            got = sorted(codes_of(d.get("hand") or ""))
            seq.append((n, ms, matches(got, want_b, unk_b), len(got), d.get("status")))
        hit = next((n for n, _m, ok, _c, _s in seq if ok), None)
        say(f"  {plat:<9} 切局后追上真值：第 {hit} 帧"
            f"   逐帧: " + " ".join(
                f"#{n}{'' if ok else '×'}({ms:.0f}ms,{c}张)" for n, ms, ok, c, _s in seq))
        break  # 一个平台的受控实验足够说明问题，其余平台逐帧成本太高

    with open(a.out, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out) + "\n")
    say("")
    say(f"[已写入 {os.path.relpath(a.out, REPO)}]")


if __name__ == "__main__":
    main()
