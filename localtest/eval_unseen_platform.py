# -*- coding: utf-8 -*-
"""b8：把某个平台的专属模板库整个抽掉，测「真机遇到从没见过的平台」会怎样。

为什么用「留一平台（LOPO）」而不是拿 unknown 帧来测：unknown 帧的身份是靠人眼事后
认出来的，而生产上系统不知道它是谁 —— 它能依赖的只有主库（34 类通用字模，挂在
`tencent` 名下）+ 探针自动路由 + 别家平台的字模。把目标 style 的专属 bank 从 _cores
里剔掉，就是在复现这个条件：素材没见过，但几何/配色/UI 都是真的。

要回答的问题不是「精度掉了多少」（一定会掉），而是这三条：
1. 掉到什么程度 —— 陌生牌风下不拒识的逐位精度是多少（逐平台给出与「已见」的差值）；
2. 分数门限能不能兜住 —— 各门限下的覆盖率/保留内精度（宁可拒识也不给错答案）；
3. 探针在陌生平台面前是帮忙还是帮凶 —— 它把陌生帧路由成了**谁家**，以及
   「分类时让不让这个胜出风格收窄候选集」。这条用 A/B 量，不用帧分组比大小。

实验是矩阵而不是单点：库状态 {陌生 LOPO, 已见全库} × 分类候选集策略 {探针一家(现状),
探针前两家, 全库, 用户声明}。只测陌生半边会得出「一律别收窄」这种错结论 —— 收窄换来
的是 3 倍速度（实测 508ms vs 1324ms/帧，PC 离线），而实时预算根本不允许每帧全库打分。
`_probe_style` 的注释也记录了它的来路：单枚路由 39% 命中时，选错 bank 等于拿别家字模
认这家的牌，腾讯底座因此从 100% 被拖到 86%。

所以真正要拍板的是「收窄几族」。**余量（margin）门限这条直觉已被本实验的实测否证**：
陌生条件下余量 <0.05 / 0.05-0.10 / 0.10-0.20 三箱分别亏 5.5 / 6.0 / 2.9pp，亏幅与余量
没有单调关系（余量最大的那箱反而亏得最少，但仍亏）；已见条件下每箱也都亏。也就是说
「探针有多大把握」预测不了「收窄会不会吃亏」，拿它当放开门限是个假判据 —— 于是留下
的结构选项只有「收两家」，本脚本直接把它跑成一组数字，而不是留在推断里。

指标口径上的四个坑（都踩过，所以写进注释）：
  · 「探针认对本家 X/N 帧」在 LOPO 下是**定义必然的 0**：_probe_style 的 style_order
    是从 _cores 现建的，本家 bank 被抽空后本家 style 根本不在候选里，判对不可能。
    有意义的读法是去向分布 + A/B，不是这个比值。
  · 探针只在失手重扫路径被调用（detector 里 `style = self._probe_style(...)` 那一处），
    所以「没触发」与「触发了但 <0.60 判不出」是两回事，混成一个数会让人以为
    陌生平台全都路由失败。这里分三档：未触发 / 判不出 / 判成某家（并记下是谁家）。
  · 拿「收窄帧 91.7% vs 未收窄帧 98.4%」这种帧分组直接比大小是**帧混合效应**：判不出
    的只有 6 帧，它们本来就可能是一批更容易（或更难）的牌。所以 A/B 让**同一批帧**
    各跑两遍，唯一变量是 `_resolve_styles` 是否把探针风格带进分类候选集。探针本身
    照常跑、照常决定节距候选 —— 让探针不跑会连几何候选一起改掉，那就成了两个变量。
  · 门限不能跨库状态比：`_score_face` 的分数是「候选集内取最大」，全库的候选集比单家
    大，同一枚牌的分数天然右移。所以门限表只在同一格内读，换格子要重标门限。

已知结构性事实（写在结果里，不让它被误读成 bug）：手绘主库在 _cores 里的 style 就是
`tencent`，所以对 tencent 而言「抽掉专属 bank」等于连主库一起抽干，它没有「未见」对照组。

顺序必须与 b7 一致：先抽库、再检测。反过来（先检测后抽库）会让检测守卫拿到的模板分
与分类时用的库不是同一个状态，测出来的就不是「同一个陌生平台」了。

运行：py -3.10 -X utf8 localtest/eval_unseen_platform.py \
        --gt localtest/gt/shots_b1.json --out build/eval_unseen.txt
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_new_material import (align_by_slots, bank_label_sets,  # noqa: E402
                               load_provenance, lofo_keep_indices)

# 0.38/0.40/0.42/0.50 是 engine.py 里真实存在的工作点（局格救援 / 冷启动 bootstrap /
# 补漏 RELAX / 严格 MIN_CONF），报上它们才能回答“生产把门限挪到哪会变成什么”；
# 0.45/0.55/0.60 只作对照。纯扫参的门限没有落点，写在报告里就是给人做错决定的工具。
THRESHOLDS = (0.38, 0.40, 0.42, 0.45, 0.50, 0.55, 0.60)
NARROW = ("narrow", "top2", "top2m", "all", "ownmain", "declared")
#   narrow=生产现状（探针胜出的那一家**再加本帧第二名**，见 `_probe_runner_up`）
#   top2=不走生产的排名表，改用本实验上一轮 narrow 记录到的 win/second 直接喂 `_resolve_styles`。
#          「留两家」落地生产之后这一格就是**口径对照**：它的前两名来自 `probe_margin`
#          （`classify_tile(styles={家})` 逐家均分），生产的来自 `_probe_style`（裸
#          matchTemplate 取最大）。两者应当给出同样的前两名；这一格若明显偏离 narrow，
#          说明同一件事有两套量测口径在打架，那是告警而不是“方案差异”。
#   top2m=探针的前两家再并上主库（检验「额外保主库」还有没有增益：若前两名常常已含
#            主库，这一格必然与 top2 打平，那就别为一个空改动去动生产路由）
#   all=全库打分（探针只管几何）
#   ownmain=主库+本家两家，不经探针（这才是「用户声明平台」理论上该有的候选集）
#   declared=走生产声明路径 `_resolve_styles`（active_styles={主库, 本家}）。实得的候选集是
#            探针与白名单求交的结果：本家一定在（b6b 守卫保证），主库只在探针把它排进前两名
#            时才留下 —— 所以这一格量的是「声明后被生产收窄成什么」，不是白名单本身。
#   顺序有要求：top2/top2m 要复用 narrow 那一格量到的路由结果，所以 narrow 必须在它们前面。
LIBS = ("seen", "lopo")           # seen=全库（已见）；lopo=抽掉本平台专属 bank（陌生）
# 这个元组的顺序**就是**报告里的列顺序，表头由 LIBTAG 在同一处生成，不许再手写。
LIBTAG = {"seen": "已见(本家bank在位)", "lopo": "陌生(本家bank抽空)"}

# b7「已见」口径的对照数字（出处：build/eval_b1_conf.txt 的逐平台逐位段）。写死在这里
# 是为了让两份报告能对着读 Δ；b7 重跑后若变了必须一起改 —— 所以顺手把合计也钉成常量，
# 一旦本脚本读到的 GT 帧数/格数与它对不上，报告里会打出 !! 而不是安静地给假数字。
SEEN = {"jj": (152, 152), "queshen": (175, 175), "shushan": (131, 132),
        "tencent": (182, 184), "tuyou": (116, 116), "weile": (127, 127),
        "zj": (197, 201)}
SEEN_TOTAL = (1080, 1087)

# 主库（templates_data 那 34 类）在 _cores 里的风格名。detector 的
# `set_platform_styles` 里同样硬编码这个字符串，两处必须一致 —— 对不上时
# ownmain 那格会退化成「只扫本家」，数字看着正常但完全不是它声称的口径。
MAIN_STYLE = "tencent"


def drop_style(cores, style):
    """抽掉某平台专属 bank 后该保留哪些模板实例下标。

    只按 style 抽，不碰主库：真机遇到陌生平台时，通用字模仍然在场 —— 这正是
    「没见过也能猜个八九不离十」的那点能力来源，把它也抽掉就变成不可能任务。
    """
    return [i for i, c in enumerate(cores) if c[1] != style]


def probe_margin(det, cores, crop, extra, resolve):
    """量这一帧探针的 top1-top2 余量（哪家赢、差多少）。

    口径说明（不写清就会被当成生产探针的分数）：分数走的是 `classify_tile(styles={家})`，
    与 `_probe_style` 内部裸 `matchTemplate` 在同一窗口上同向但不同值。这里要的是
    **相对**余量用于定门限，不拿它当绝对分数用。

    量之前必须临时绕开本实验自己打的两个补丁（`_resolve_styles` 与 active_styles），
    否则「全库打分」那格会把每家的分数都算成全库分，margin 直接失真。
    """
    styles = sorted({c[1] for c in cores})
    av, rs = det.active_styles, det._resolve_styles
    mats = []
    try:
        det.active_styles = None
        det._resolve_styles = resolve
        for c in [crop] + [x for x in (extra or [])
                           if x is not None and getattr(x, "size", 0)]:
            v = {}
            for s in styles:
                _l, sc = det.classify_tile(c, styles={s})
                v[s] = float(sc or 0.0)
            mats.append(v)
    finally:
        det.active_styles, det._resolve_styles = av, rs
    if not mats or len(styles) < 2:
        return None
    mean = {s: sum(m[s] for m in mats) / float(len(mats)) for s in styles}
    order = sorted(mean.items(), key=lambda kv: -kv[1])
    return order[0][0], order[1][0], order[0][1] - order[1][1]


def evaluate(det, shots, cores_all, keys_all, harv_all, own_cnt, prov, route,
             lib, narrow_mode, prev_fd=None):
    """跑矩阵中的一格：lib 见 LIBS，narrow_mode 见 NARROW，prev_fd 供 top2 复用路由结果。"""
    per_style = collections.defaultdict(
        lambda: {"n": 0, "hit": 0, "frames": 0, "cells": [], "fp": 0,
                 "rt_none": 0, "rt_low": 0, "rt_to": collections.Counter()})
    secs = [0.0]      # 只量 detect_hand_strip：取消收窄省不省/费不费全在这一段
    fd = collections.defaultdict(dict)   # 帧级明细：按 (style, file) 与别的格配对
    holder = {"styles": None}
    resolve = det._resolve_styles
    if narrow_mode in ("all", "top2", "top2m", "ownmain"):
        # active_styles 恒为 None（本实验不向面板声明平台），生产语义下这个函数就是把
        # 探针风格原样交给扫描循环；返回 None = 分类候选集不受探针影响，返回一个集合
        # = 只准用这几家的字模（top2 格用「探针第一名 + 第二名」）。
        det._resolve_styles = lambda styles: holder["styles"]
    try:
        for e in shots:
            st = e["style"]
            img = cv2.imread(os.path.join(REPO, e.get("src") or "public/0", e["file"]))
            if img is None:
                per_style["__unreadable__"]["frames"] += 1
                continue
            idx = drop_style(cores_all, st) if lib == "lopo" else list(range(len(cores_all)))
            cs = [cores_all[i] for i in idx]
            ks = [keys_all[i] for i in idx]
            hs = [harv_all[i] for i in idx]
            if lib == "lopo":
                assert len(cs) == len(cores_all) - own_cnt[st], "drop_style 账目对不上"
            drop = prov.get(st, {}).get(e["frame"], set())
            if drop:
                ki = lofo_keep_indices(cs, ks, hs, st, drop)
                cs = [cs[i] for i in ki]
                ks = [ks[i] for i in ki]
                hs = [hs[i] for i in ki]
            det._cores, det._core_keys, det._core_harvested = cs, ks, hs
            if narrow_mode == "declared":
                # 复现「用户在面板上选了正确平台」：白名单 = 主库 + 本家。走的是生产同
                # 一条 `_resolve_styles`，所以 b6b 那条守卫（探针不得把声明平台的专属
                # bank 挤出去）在这里照样生效，测出来的才是声明平台的真实收益。
                det.active_styles = {"tencent", st}
                det.full_honors = False
            else:
                det.set_platform_styles(None)      # 生产上用户不知道该选哪个平台
            if narrow_mode == "ownmain":
                # 声明平台的「理论候选集」：主库 + 本家，完全不经探针。主库风格名在
                # detector 的 set_platform_styles 里硬编码为 tencent，这里同源。
                holder["styles"] = {MAIN_STYLE, st}
            if narrow_mode in ("top2", "top2m"):
                # 本帧允许的两家来自「探针那一格」量到的第一名与第二名：探针没跑出结果
                # 的帧保持 None（= 全库兜底），与生产「判不出就不收窄」同一条路径。
                info = (prev_fd or {}).get((st, e["file"]), {})
                keep = {x for x in (info.get("win"), info.get("second")) if x}
                if keep and narrow_mode == "top2m":
                    keep = keep | {MAIN_STYLE}
                holder["styles"] = keep or None
            route["hit"] = False
            route["style"] = None
            route["crop"] = None
            route["extra"] = []
            t1 = time.perf_counter()
            dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
            secs[0] += time.perf_counter() - t1
            got = [l for _r, l, _s in dets]
            scores = [float(s) for _r, _l, s in dets]
            aligned = align_by_slots(dets, getattr(det, "last_hand_grid", None),
                                     len(e["hand"]))
            got_full, sc_full = (aligned[0], aligned[1]) if aligned else (got, scores)
            banks_now = bank_label_sets(det)
            # 「结构性缺类」在陌生条件下要重新定义：不是「本家库没有这个类」（本家库已被
            # 抽空，那样每一格都算缺类、第二张表会被清成 0 行），而是「剩下的候选集里
            # 根本没有这个类的任何字模」——那才是任何算法都变不出来的硬缺类。
            avail = set().union(*banks_now.values()) if banks_now else set()
            rec = per_style[st]
            rec["frames"] += 1
            fr = fd[(st, e["file"])]
            fr.setdefault("n", 0)
            fr.setdefault("hit", 0)
            fr["probe"] = route["style"] if route["hit"] else None
            # 余量只在生产那一格量：它问的是「探针自己有多大把握」，在别的格里量到
            # 的是被本实验改过的候选集状态，那不是生产会看到的数字。
            if narrow_mode == "narrow" and route["hit"] and route["crop"] is not None:
                m = probe_margin(det, cs, route["crop"], route["extra"], resolve)
                fr["margin"] = m[2] if m else None
                fr["win"] = m[0] if m else None
                fr["second"] = m[1] if m else None
                fr["has_main"] = bool(m) and MAIN_STYLE in (m[0], m[1])
            if not route["hit"]:
                rec["rt_none"] += 1
            elif route["style"] is None:
                rec["rt_low"] += 1
            else:
                rec["rt_to"][route["style"]] += 1
            rec["fp"] += max(0, len(got) - len(e["hand"]))
            for i, x in enumerate(e["hand"]):
                if x == "?":
                    continue
                y = got_full[i] if i < len(got_full) else "<无>"
                sc = float(sc_full[i]) if i < len(sc_full) else 0.0
                rec["n"] += 1
                rec["hit"] += int(x == y)
                rec["cells"].append((sc, x == y, x not in avail))
                fr["n"] += 1
                fr["hit"] += int(x == y)
    finally:
        det._resolve_styles = resolve
    det._cores, det._core_keys, det._core_harvested = cores_all, keys_all, harv_all
    per_style["__secs__"] = secs[0]
    per_style["__frames__"] = sum(r["frames"] for r in plats(per_style).values())
    per_style["__fd__"] = dict(fd)
    return per_style


def plats(per_style):
    """只返回按平台的记录。

    评测结果里混了 `__secs__`/`__frames__` 这类元数据（值是 float），任何
    `r["frames"]`、`r["cells"]` 式的遍历都必须走这里；否则加一个元数据键就要
    改一处过滤，漏一处就是运行期 TypeError。
    """
    return {k: r for k, r in per_style.items() if not k.startswith("__")}


def tot(per_style):
    p = plats(per_style)
    return sum(r["hit"] for r in p.values()), sum(r["n"] for r in p.values())


def pct(h, n):
    return 100.0 * h / max(1, n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=os.path.join(HERE, "gt", "shots_b1.json"))
    ap.add_argument("--out", default=os.path.join(REPO, "build", "eval_unseen.txt"))
    a = ap.parse_args()

    with open(a.gt, encoding="utf-8") as fp:
        shots = json.load(fp)["shots"]

    det = TencentGridDetector()
    cores_all, keys_all, harv_all = det._cores, det._core_keys, det._core_harvested
    own_cnt = collections.Counter(c[1] for c in cores_all)
    assert MAIN_STYLE in own_cnt, (
        f"主库风格名 {MAIN_STYLE!r} 不在 _cores 的 style 里（实有 {sorted(own_cnt)}）："
        "ownmain 那格会静默退化成「只扫本家」，报出来的数字不是它声称的口径")
    prov = load_provenance()
    route = {"hit": False, "style": None, "crop": None, "extra": []}
    _probe = det._probe_style

    def _probe_rec(crop, *aa, **kw):
        # 只记该帧第一次探针：生产上一帧最多重扫一轮，记末次会让「先判对家、
        # 失败后又判成别家」被读成两次独立路由。
        if route["hit"]:
            return _probe(crop, *aa, **kw)
        route["hit"] = True
        route["style"] = _probe(crop, *aa, **kw)
        route["crop"] = crop
        route["extra"] = [x for x in (aa[0] if aa else []) if x is not None and x.size]
        return route["style"]

    det._probe_style = _probe_rec

    n_cells_all = sum(len(e["hand"]) for e in shots)
    lines = ["口径：留一平台（LOPO）——抽掉该平台的专属模板库，且不向面板声明平台"
             "（探针自动路由），复现「真机遇到从没见过的平台」；同帧模板一并剔（LOFO）",
             f"样本：{len(shots)} 帧 / {n_cells_all} 张（GT 里 `?` 为不参与计分格）",
             "主库归属：templates_data 的 34 类在 _cores 里 style 记为 `tencent`，"
             "故 tencent 无「未见」对照组（抽本家=连主库一起抽干）",
             "库账目（LOPO 确实在抽，不是空跑）：" + " ".join(
                 f"{st}:{own_cnt[st]}/{len(cores_all)}" for st in sorted(own_cnt)),
             ""]

    res = {}
    for lib in LIBS:
        for nm in NARROW:
            res[(lib, nm)] = evaluate(det, shots, cores_all, keys_all, harv_all,
                                      own_cnt, prov, route, lib, nm,
                                      prev_fd=res.get((lib, "narrow"), {}).get("__fd__"))
    per_style = res[("lopo", "narrow")]
    skipped = per_style.pop("__unreadable__", {}).get("frames", 0)
    for k in res:
        res[k].pop("__unreadable__", None)

    lines.append(f"逐平台（陌生牌风条件下，探针收窄=生产现状；读不到素材的帧 {skipped}）：")
    lines.append("  平台      帧  格   陌生逐位      已见逐位      Δ(pp)   "
                 "探针：未触发/判不出/去向")
    for st, r in sorted(plats(per_style).items()):
        s_n, s_h = SEEN.get(st, (0, 0))
        u = pct(r["hit"], r["n"])
        k = pct(s_n, s_h)
        to = ",".join(f"{k2}x{v}" for k2, v in r["rt_to"].most_common()) or "-"
        lines.append(f"  {st:8} {r['frames']:3} {r['n']:4}  "
                     f"{r['hit']:4}/{r['n']:4}={u:5.1f}%  "
                     f"{s_n:4}/{s_h:4}={k:5.1f}%  {u - k:+6.1f}   "
                     f"{r['rt_none']}/{r['rt_low']}/{to}")
        if s_h and r["n"] != s_h:
            lines.append(f"    !! 计分格数与 b7 不一致（{r['n']} vs {s_h}），两份报告不同源")
    h, n = tot(per_style)
    lines.append(f"  合计 逐位 {h}/{n} = {pct(h, n):.1f}%"
                 f"   （对照 b7「已见」口径 {SEEN_TOTAL[0]}/{SEEN_TOTAL[1]} = "
                 f"{pct(*SEEN_TOTAL):.1f}%）")
    lines.append("")

    MTAG = {"narrow": "探针前两名（生产现状）",
            "top2": "前两名（实验口径复算）", "top2m": "前两家再并主库",
            "all": "全库打分（探针只管几何）",
            "ownmain": "主库+本家两家（不经探针）",
            "declared": "声明后走生产路径实得的候选集"}
    # 表头与列必须由同一个 LIBS 生成：上一版这里是手写「已见 陌生」而按 LIBS
    # （lopo 在前）填数，两列被对调，结论行（按 key 取）仍对而表格全错——排版错
    # 一格就整表不可信，所以让列顺序只有一处定义。
    lines.append("矩阵：库状态 × 分类候选集由谁决定（同一批 91 帧、探针照常跑，"
                 "唯一变量是候选集怎么定；ms/帧只量 detect_hand_strip，是 PC 离线数，"
                 "不能当手机实测）：")
    lines.append("  候选集决定方式                              " +
                 "  ".join(f"{LIBTAG[lib]:20}" for lib in LIBS) + "  ms/帧")
    for nm in NARROW:
        cells = []
        for lib in LIBS:
            hh, nn = tot(res[(lib, nm)])
            ms_cell = 1000.0 * res[(lib, nm)]["__secs__"] / max(1, res[(lib, nm)]["__frames__"])
            cells.append(f"{hh:4}/{nn:4}={pct(hh, nn):5.1f}% ({ms_cell:5.0f}ms)")
        lines.append(f"  {MTAG[nm]:42} " + "  ".join(cells))
    def ms(lib, nm):
        r = res[(lib, nm)]
        return 1000.0 * r["__secs__"] / max(1, r["__frames__"])

    for lib, lt in (("seen", "已见"), ("lopo", "陌生")):
        h0, n0 = tot(res[(lib, "narrow")])
        fdn = res[(lib, "narrow")]["__fd__"]
        n_main = sum(1 for v in fdn.values() if v.get("has_main"))
        lines.append(f"  {lt}：探针前两名里已含主库的帧 {n_main}/{len(fdn)}"
                     "（这个数越高，top2m 越该与 top2 打平 —— 额外保主库就是空改动）")
        for nm in ("top2", "top2m", "all", "ownmain", "declared"):
            h1, n1 = tot(res[(lib, nm)])
            lines.append(
                f"  {lt}：{MTAG[nm]} 相对 {MTAG['narrow']} "
                f"{pct(h1, n1) - pct(h0, n0):+5.1f}pp，耗时 "
                f"{ms(lib, nm) / max(1e-9, ms(lib, 'narrow')):.2f} 倍")
    h_all_s = tot(res[("seen", "all")])
    h_own_s = tot(res[("seen", "ownmain")])
    h_dec_s = tot(res[("seen", "declared")])
    h_own_l = tot(res[("lopo", "ownmain")])
    lines.append(f"  声明平台的真实价值在「放行一组近邻家」而不在「锁定本家」："
                 f"已见下 主库+本家 {pct(*h_own_s):.1f}% 对比 全库 {pct(*h_all_s):.1f}%"
                 f"（{pct(*h_own_s) - pct(*h_all_s):+.1f}pp）")
    lines.append(f"  而声明之后走生产 `_resolve_styles` 实得的候选集只有 "
                 f"{pct(*h_dec_s):.1f}%（比主库+本家再 {pct(*h_dec_s) - pct(*h_own_s):+.1f}pp："
                 "求交后常常只剩本家一家，主库要探针把它排进前两名才留得住）")
    lines.append(f"  陌生平台被「声明」的后果（本家名下已无字模，等于只剩主库）："
                 f"{h_own_l[0]}/{h_own_l[1]} = {pct(*h_own_l):.1f}%")
    gap = pct(*h_own_s) - pct(*SEEN_TOTAL)
    lines.append(f"  对照 b7 已见口径 {SEEN_TOTAL[0]}/{SEEN_TOTAL[1]}={pct(*SEEN_TOTAL):.1f}%"
                 f"，主库+本家本格 {gap:+.1f}pp（b7 走 STYLE_PLATFORM_WHITELIST 全量白名单，"
                 "放行的是多个近邻平台 bank，本格只放两家，故偏低属口径差不是回归）")
    BINS = ((0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 9.9))
    lines.append("探针余量分箱（top1-top2，classify_tile 口径；问的是「低余量的帧是不是正是"
                 "收窄会吃亏的帧」——是则可用余量门限决定收不收窄，否则余量不配当判据）：")
    for lib in LIBS:
        fn, fa = res[(lib, "narrow")]["__fd__"], res[(lib, "all")]["__fd__"]
        assert set(fn) == set(fa), "帧级配对不齐，分箱会拿错帧的精度"
        lines.append(f"  {LIBTAG[lib]}：")
        lines.append("    余量区间         帧数   探针收窄精度    全库精度        差(pp)")
        for lo, hi in BINS:
            keys = sorted(k for k, v in fn.items()
                          if v.get("margin") is not None and lo <= v["margin"] < hi)
            if not keys:
                continue
            n_ = sum(fn[k]["n"] for k in keys)
            h_ = sum(fn[k]["hit"] for k in keys)
            na = sum(fa[k]["n"] for k in keys)
            ha = sum(fa[k]["hit"] for k in keys)
            lines.append(f"    [{lo:.2f},{hi:.2f})   {len(keys):4}  "
                         f"{h_:4}/{n_:4}={pct(h_, n_):6.1f}%   "
                         f"{ha:4}/{na:4}={pct(ha, na):6.1f}%   "
                         f"{pct(ha, na) - pct(h_, n_):+6.1f}")
        kn = [k for k, v in fn.items() if v.get("margin") is None]
        if kn:
            n_ = sum(fn[k]["n"] for k in kn)
            h_ = sum(fn[k]["hit"] for k in kn)
            na = sum(fa[k]["n"] for k in kn)
            ha = sum(fa[k]["hit"] for k in kn)
            lines.append(f"    没量到（探针没跑或库里只剩一家） {len(kn):4}  "
                         f"{h_:4}/{n_:4}={pct(h_, n_):6.1f}%   "
                         f"{ha:4}/{na:4}={pct(ha, na):6.1f}%   "
                         f"{pct(ha, na) - pct(h_, n_):+6.1f}")
    route_to = collections.Counter()
    for r in plats(per_style).values():
        route_to.update(r["rt_to"])
    if route_to:
        lines.append("  陌生帧被路由成的家（次数）：" + ", ".join(
            f"{k}x{v}" for k, v in route_to.most_common()) +
            "（主库挂在 tencent 名下且始终在场，故 tencent 是最强吸引子）")
    else:
        lines.append("  没有一帧被路由成功")
    lines.append("")

    # 门限表要按**生产现状的候选集**出，同时也给上限口径做对照：分数是「候选集内取
    # 最大」，换成全库就整体右移，同一道门限在两种口径下挡掉的东西完全不同 —— 拿上限
    # 那套的表去调生产门限，等于对着一个不存在的分数分布做决定。生产那套排在前面。
    for nm, note in (("narrow", ""), ("all", "，上限口径非生产")):
        all_cells = [c for r in plats(res[("lopo", nm)]).values() for c in r["cells"]]
        ext = [c for c in all_cells if not c[2]]
        lines.append(f"分数门限能兜住多少（陌生条件下，模式={MTAG[nm]}{note}；"
                     "覆盖=分数达标张数占比，精度=达标内判对占比）：")
        lines.append("  门限   覆盖张数          覆盖率    达标内精度    其中本来错")
        for name, pool in (("全部格", all_cells), ("扣硬缺类后", ext)):
            for th in THRESHOLDS:
                kept = [c for c in pool if c[0] >= th]
                wrong = sum(1 for c in kept if not c[1])
                lines.append(f"  {th:.2f} [{name:6}] {len(kept):4}/{len(pool):4}"
                             f"  {pct(len(kept), len(pool)):6.1f}%"
                             f"     {pct(len(kept) - wrong, len(kept)):6.1f}%"
                             f"      {wrong}")
        lines.append("")
    lines.append("注 1：达标=面板标灰不进建议（等价生产上的低置信丢弃）。这张表回答的是"
                 "「陌生平台面前，门限能不能把错答案挡住」，不是「精度有多高」。")
    lines.append("注 2：门限是分数空间的尺子，而分数是『候选集内取最大』——换模式（换候选集"
                 "大小）分数分布就整体平移，所以这张表的门限不能与已见口径的 0.45/0.55 互抄。")
    lines.append("注 3：`" + MTAG["top2"] + "` 那格略低于 `" + MTAG["narrow"] + "` 不是方案差异，是**口径差**：")
    lines.append("  生产的排名表来自 `_probe_style`（裸 matchTemplate 对全库取最大，与路由同源），")
    lines.append("  实验那格的前两名来自 `probe_margin`（`classify_tile(styles={家})`，含结构裁决），")
    lines.append("  两者偶尔给出不同的第二名。以生产数字为准；实验口径只用作旁证。")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"[ok] -> {a.out}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
