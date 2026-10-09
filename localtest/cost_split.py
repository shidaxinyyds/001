# -*- coding: utf-8 -*-
"""单帧成本剖析：把「一帧 447ms 花在哪」量出来，而不是猜。

口径与生产一致：platform=tencent、mode=sc_hz，37 帧 verified GT 逐帧 fresh Engine
（与 `eval_base.py` 同一批夹具、同一个 jpeg 质量 50 的退化预处理）。

做法是给候选热点包一层计时壳，跑真 `Engine.process`，再按累计耗时排序；
`未归类` = 单帧总耗时 - 已归类之和（进程内序列化、ROI 裁剪、稳定器这些零碎）。
牌河/副露在后台线程跑（见 engine 的 `_run_field_scan`），所以它的耗时**不在**
端到端关键路径上，表里单独标注，避免把它当成「能省下来响应时间」的目标。

用法：
  py -3.10 -X utf8 localtest/cost_split.py            # 逐帧 fresh Engine
  py -3.10 -X utf8 localtest/cost_split.py --warm     # 一个常驻 Engine 跑完全部帧
  py -3.10 -X utf8 localtest/cost_split.py --n 8      # 只跑前 8 帧（快速对照）

为什么要 `--warm`：逐帧 fresh 会把「一个长命 Engine 只付一次」的钱摊到每一帧上
（cProfile 实测：`readNetFromONNX` + `_load_templates` + import 这些一次性开销在
fresh 口径下高达 ~130ms/帧）。那不是真机的静默期单价，拿它当端到端预算的尺子会
把优化方向带偏；真机是同一个 Engine 连续跑帧。两种口径都要看：fresh 看「首帧代价」，
warm 看「稳定期单价」。

输出：build/cost_split.txt（名字不变，口径写在首行）

阶段记账的口径坑（本段实测）：`_perf_ms` 各阶段**只在真跑了那一段的帧里追加**
（orient 只在待验证帧、advice 只在走到决策的帧），窗口又是 maxlen=20 的滚动 deque。
拿 `list(dq)[-1]` 当本帧读数，会把上一次的值复制给之后每一帧：旧版就这么报出过
“orient 均 88.5ms / advice 均 23.9ms” 两个假主成本（真值是 orient 只在少量帧跑、
advice 均 2.3ms）。现用「追加前后快照比对」归因，并把「本帧没跑」显式记为 None。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import time
from collections import defaultdict

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "cost_split.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

import engine.engine as EE  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector as DET  # noqa: E402

# 关键路径（同步，用户等它）。只放**互相不包含**的函数：包含关系一律进 NEST_INNER，
# 否则同一次钱会被两个行各记一次（钱的账只能一个对象记一次）。
MAIN_HOT = (
    (DET, "detect_all_rows", "手牌行检测+分类"),
    (EE, "_build_tactical_perception", "战术条文案"),
    # 阶段判定/徽章读数：跑在主线程、又不在 `detect_all_rows` 里。它们早先全部落在
    # 「未归类 75ms/帧」里 —— 那不是零碎，是没测到的钱。
    (DET, "detect_dingque", "定缺徽章读数"),
    (DET, "is_swap_phase", "换牌阶段判定"),
    (DET, "is_pick_phase", "摸牌阶段判定"),
    (EE.Engine, "_settle_orientation", "几何归一(帧首)"),
)
# 内层（与上面的函数行有包含关系，单独报、不相加）：
# 打分与条带入口都在 `detect_all_rows`/手牌通道里；手牌通道取行包含整屏/条带两次
# 检测；摸牌候选包含在「摸牌阶段判定」里。
NEST_INNER = (
    (DET, "_score_face", "单枚牌面打分(matchTemplate 循环)"),
    # `detect_hand_strip` 不挂在 `detect_all_rows` 下面（手牌通道可以绕过它直接调），
    # 不单独量就会被当成「未归类」：那是猜，不是测。
    (DET, "detect_hand_strip", "手牌条带入口"),
    (EE.Engine, "_hand_channel_rows", "手牌通道取行"),
    (DET, "detect_pick_candidates", "摸牌候选打分"),
)
# 库级零碎（编码/缩放/序列化）：与函数行有包含关系，只看不计入「主项/未归类」。
LIB_HOT = (
    (cv2, "imencode", "JPEG 编码"),
    (cv2, "resize", "resize"),
    (cv2, "cvtColor", "cvtColor"),
    (json, "dumps", "json 序列化"),
)
# 后台通道（`_run_bg_river_and_melds`）：不占端到端关键路径，但要量出来——
# 它和主线程争 CPU/内存带宽，在手机上同样会拖慢同步那一段。
BG_HOT = (
    (EE, "detect_river_discards", "牌河检测"),
    (EE, "detect_player_melds", "副露检测"),
)

ACC = defaultdict(lambda: [0.0, 0])     # label -> [累计 ms, 调用次数]
NESTED = defaultdict(lambda: [0.0, 0])  # 内层热点：单独看，不与外层相加
LIB = defaultdict(lambda: [0.0, 0])     # 库级零碎：只看包含关系，不参与求和
BG_TID = set()


def _wrap(owner, name, label, bucket=None):
    fn = getattr(owner, name)
    # 不能用 `bucket or ACC`：空的 defaultdict 是 falsy（刚启动时 NESTED/LIB 都是空的），
    # 那样写会把内层/库级的账全记到 ACC 上，同一次钱双计。
    target = ACC if bucket is None else bucket

    def timed(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            in_bg = threading.get_ident() in BG_TID
            k = label + ("（后台）" if in_bg else "")
            target[k][0] += dt
            target[k][1] += 1
    setattr(owner, name, timed)
    return timed


def main() -> int:
    argv = sys.argv[1:]
    n_lim = int(argv[argv.index("--n") + 1]) if "--n" in argv else None
    warm = "--warm" in argv

    with open(GT_PATH, encoding="utf-8") as fp:
        gt = json.load(fp)["shots"]
    frames = []
    for s in gt:
        if not s.get("verified"):
            continue
        p = os.path.join(SHOT_DIR, s["file"])
        img = cv2.imread(p)
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        frames.append((s["file"], cv2.imdecode(buf, cv2.IMREAD_COLOR)))
        if n_lim and len(frames) >= n_lim:
            break

    # 后台入口打标：牌河/副露是异步的，它们的耗时不能算进关键路径
    _orig_bg = EE.Engine._run_bg_river_and_melds

    def bg_with_mark(*a, **kw):
        BG_TID.add(threading.get_ident())
        return _orig_bg(*a, **kw)
    EE.Engine._run_bg_river_and_melds = staticmethod(bg_with_mark)

    for owner, name, label in MAIN_HOT:
        _wrap(owner, name, label)
    for owner, name, label in NEST_INNER:
        _wrap(owner, name, label, bucket=NESTED)
    for owner, name, label in BG_HOT:
        _wrap(owner, name, label)
    for owner, name, label in LIB_HOT:
        _wrap(owner, name, label, bucket=LIB)

    total_ms = []
    per_frame = []
    eng_perf = []
    shared = EE.Engine() if warm else None    # warm：与真机同构（一个 Engine 连续跑帧）
    for f, img in frames:
        eng = shared if warm else EE.Engine()
        eng.set_platform("tencent")
        eng.set_mode("sc_hz")
        mark = {k: list(v) for k, v in ACC.items()}
        nmark = {k: list(v) for k, v in NESTED.items()}
        # 阶段记账的「本帧前快照」：`_perf_ms` 是 maxlen=20 的滚动 deque，而且
        # **只在真的跑了那个阶段的帧里追加**（orient 只在待验证帧、advice 只在走到
        # 决策的帧）。直接拿 `[-1]` 当本帧读数会把上一次的值复制给后面每一帧 ——
        # 实测就造出了“orient 均 88.5ms”这种假主成本。必须用「追加前后快照比对」。
        pre_perf = {k: list(dq) for k, dq in eng._perf_ms.items()}
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            eng.process(img)
        dt = (time.perf_counter() - t0) * 1000.0
        total_ms.append(dt)
        # 每个阶段每帧最多追加一条（grep 过全部 `.append` 调用点），所以「列表变了」
        # 就等于「本帧追加了一条」，新值在末位；没变就是本帧没跑该阶段 → None。
        # fresh 口径下窗口本来就是空的，两种读法结果一致。
        stage = {}
        for k, dq in eng._perf_ms.items():
            now = list(dq)
            stage[k] = now[-1] if now != pre_perf.get(k, []) else None
        eng_perf.append((dt, f, stage))
        spent = {k: (v[0] - mark.get(k, [0.0, 0])[0],
                     v[1] - mark.get(k, [0, 0])[1]) for k, v in ACC.items()}
        nspent = {k: (v[0] - nmark.get(k, [0.0, 0])[0],
                      v[1] - nmark.get(k, [0, 0])[1]) for k, v in NESTED.items()}
        per_frame.append((dt, f, spent, nspent))

    per_frame.sort(reverse=True)
    tot_avg = sum(total_ms) / len(total_ms)
    lines = [f"帧数 {len(total_ms)}（tencent/sc_hz，"
             f"{'常驻 Engine 连续跑帧' if warm else '逐帧 fresh Engine'}）",
             f"单帧 process 均值 {tot_avg:.1f}ms，"
             f"中位 {sorted(total_ms)[len(total_ms)//2]:.1f}ms，"
             f"最差 {max(total_ms):.1f}ms", ""]
    lines.append("累计耗时（按帧均摊与占单帧均值百分比）：")
    for k, (ms, n) in sorted(ACC.items(), key=lambda x: -x[1][0]):
        per = ms / len(total_ms)
        lines.append("  %-30s 总计 %8.1fms  调用 %5d 次  均 %6.2fms/次  "
                     "= 每帧 %6.1fms（占均值 %5.1f%%）" % (
                         k, ms, n, ms / max(n, 1), per, per / tot_avg * 100))
    lines.append("")
    for k, (ms, n) in sorted(NESTED.items(), key=lambda x: -x[1][0]):
        lines.append("  [内层] %-26s %8.1fms  调用 %5d 次  均 %6.2fms/次"
                     "（跨线程求和，可大于单帧墙钟）" % (k, ms, n, ms / max(n, 1)))
    # 未归类 = 单帧均值减去**全部已归类的主项**（ACC 里的项互不包含，后台项也算：
    # 它虽然不占关键路径，但确实花了 CPU 时间，不归它进去会把未归类算多）。
    accounted = sum(v[0] for k, v in ACC.items()) / len(total_ms)
    lines.append("")
    lines.append("  主项合计 %.1fms/帧（ACC 各项互不包含，后台项计入 CPU 账），"
                 "未归类 %.1fms/帧（不在任何一个 wrapper 里的零碎）" % (
                     accounted, tot_avg - accounted))
    if LIB:
        lines.append("  [库级零碎]（与上面的函数行有包含关系，不参与求和）：")
        for k, (ms, n) in sorted(LIB.items(), key=lambda x: -x[1][0]):
            lines.append("    %-14s %8.1fms  调用 %6d 次  均 %6.3fms/次"
                         "  = 每帧 %6.1fms" % (k, ms, n, ms / max(n, 1),
                                               ms / len(total_ms)))
    # 引擎内建阶段：把「未归类」再切一刀（这些钱不在上面任何一个 wrapper 里）。
    # 只统计**真的跑了该阶段的帧**（None = 本帧没跑），否则均值会被“上一次的值
    # 复制过来”的空帧摊薄/推高，两种错法都见过。
    stage_keys = sorted({k for _d, _f, p in eng_perf for k in p})
    lines.append("  引擎内建阶段（`_perf_ms`，只统本帧真跑了该阶段的帧；「记录」列就"
                 "是跑到过的帧数）：")
    for k in stage_keys:
        vals = [p[k] for _d, _f, p in eng_perf if p.get(k) is not None]
        if not vals:
            lines.append("    %-8s 本批一帧都没跑到" % k)
            continue
        vals_s = sorted(vals)
        lines.append("    %-8s 记录 %3d/%d 帧  均 %6.1fms  中位 %6.1fms  最慢 %6.1fms"
                     "  =摊到全帧 %6.1fms" % (
                         k, len(vals), len(eng_perf), sum(vals) / len(vals),
                         vals_s[len(vals_s) // 2], max(vals),
                         sum(vals) / len(eng_perf)))
    worst_perf = sorted(eng_perf, reverse=True)[:5]
    lines.append("    最慢 5 帧的阶段拆解（- 表示本帧没跑该阶段）：")
    for dt, f, p in worst_perf:
        lines.append("      %-26s 总 %7.1f  %s" % (
            f[:26], dt, "  ".join(
                "%s=%s" % (k, "-" if p.get(k) is None else "%.0f" % p[k])
                for k in stage_keys)))
    lines.append("")
    lines.append("最慢 8 帧的逐帧拆解（ms）：")
    keys = sorted({k for _d, _f, sp, _n in per_frame for k in sp if k != "单枚牌面打分(matchTemplate 循环)"})
    lines.append("  %-26s %8s  %s" % ("帧", "总", "  ".join("%-18s" % k for k in keys)))
    for dt, f, spent, nspent in per_frame[:8]:
        # 逐帧内层只取「打分」与「条带入口」两项（其余内层项看上面的累计表）：
        # 它们互相嵌套，混在一起求和会把次数报成两倍（钱的账只能一个对象记一次）。
        sc = nspent.get("单枚牌面打分(matchTemplate 循环)", (0.0, 0))
        st = nspent.get("手牌条带入口", (0.0, 0))
        lines.append("  %-26s %8.1f  %s  [打分 %d 次/%.0fms 条带入口 %.0f/%d]" % (
            f[:26], dt, "  ".join("%-18s" % ("%.0f/%d" % (spent.get(k, (0, 0))[0],
                                                          spent.get(k, (0, 0))[1]))
                                   for k in keys), sc[1], sc[0], st[0], st[1]))
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
