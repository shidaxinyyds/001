# -*- coding: utf-8 -*-
"""新平台素材评测：人工 GT（localtest/gt/new_shots.json）vs 生产检测器。

与 eval_base.py 的分工：eval_base 守的是**腾讯底座不回退**（37 帧、已进门禁）；
本文件守的是**新接入平台的真实数字**，并把"错在哪一层"拆开——这一层拆分会直接
决定下一步该做什么：

  逐张（多重集）——"这 13 张认对几张"，与 eval_base 同口径，可横向比较；
  逐位（左→右对齐）——"面板上每个位置的牌是否正确"，能暴露整行平移错位
    （逐张口径下"一张认错 + 一张漏检"可能互相抵消，逐位不会）；
  bank 缺类——该风格库里根本没有这张牌：这是**样本缺口**，换分类器也救不了，
    只有按 SOP 第 4/5 步收割补库；
  分数分离度 + 拒识门限曲线——检测分数能不能当"我不知道"的信号用。
    这决定"零失误商用"是否成立：若错例分数都显著低于对例，那么低分张标灰、
    不进建议即可达成"给出的每张都对"，代价只是覆盖率；若重叠，这条路就是假的。

默认只出报告（新素材帧数少，当门禁会因样本量而非质量变红）；
`--gate 0.98` 才按"保留内精度"判退出码。

口径（重要，否则测到的是不可达的数字）：生产上用户一定在面板里选过平台，
Engine 会把平台推给 detector 做 bank 白名单（`engine.py::_apply_platform_styles`），
所以**探针猜错也会被白名单救回来**。本脚本默认按帧声明平台（=生产口径）；
`--no-declare` 才回到旧的"不声明"口径，那个口径下多 bank 互相串味，数字显著变差
（两种口径的完整对照由 `localtest/ab_bank_impact.py` 的 A/C 两行负责）。

运行：py -3.10 -X utf8 localtest/eval_new_material.py [--gate 0.98] [--no-declare]
输出：build/new_material_report.txt
"""
import argparse
import collections
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from build_platform_bank import frame_of  # noqa: E402  源帧归属的唯一一把尺

SRC = os.path.join(REPO, "public", "0")
GT_PATH = os.path.join(HERE, "gt", "new_shots.json")
TILES = os.path.join(HERE, "tiles")
OUT = os.path.join(REPO, "build", "new_material_report.txt")
THRESHOLDS = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
# 风格名 -> 平台 key（platforms.py 注册名）：GT 里的 style 就是生产上选的那个平台。
STYLE_OF = {"tencent": "tencent", "shushan": "shushan", "queshen": "gd_queshen",
            "tuyou": "tuyou", "jj": "jj", "weile": "weile", "zj": "zj_sichuan"}


def bank_label_sets(det):
    sets = collections.defaultdict(set)
    for lbl, style, *_rest in det._cores:
        sets[style].add(lbl.split("#")[0])
    return sets


def lofo_keep_indices(cores, keys, harvested, style, drop):
    """LOFO 一折里该保留哪些模板实例（评测与守卫共用这一份，各自重写就会又不一样）。

    两条条件都是实测坐实的坑：
    - 必须按**实例**而不是按标签剔：同名变体来自多帧，按标签剔会把素材缺口
      报成算法缺口（实测 19 张被夸大成 92 张）；
    - 必须同时校 `harvested`：手绘主库的键名与 bank 键名同空间（实测 34 张腾讯
      手绘模板有 33 张撞名），只按键名剔会把“本帧之外的知识”也抽掉，把一个本
      可以判对的格报成“bank 缺类”而豁免——那是自己给自己发免检牌。
    """
    return [i for i, ((c, k), h) in enumerate(zip(zip(cores, keys), harvested))
            if not (c[1] == style and h and k in drop)]


# 错位的可行动归因：把每一张真失误分到一个桶里，因为不同桶的修法完全不同
# （数数失误要补该花色相邻号的对比样本，跨花色抢判要剔形近模板，漏框是检测层
# 的事、跟分类器无关）。不先分桶就只能说“错了 16 张”，说不了下一步做什么。
BLAME_SAME = {"p": "筒子圈数±1", "s": "索子根数±1", "m": "萬字笔画±1", "z": "字牌邻号"}


def blame(x, y):
    """一张 GT→det 的错位归到哪个因。x 是 GT，y 是识别结果。"""
    if y == "<无>":
        return "漏框/未检出"
    if x[-1] != y[-1]:
        return "跨花色形近抢判"
    if abs(int(x[0]) - int(y[0])) == 1:
        return BLAME_SAME[x[-1]]
    return "同花色错号"


def confusion_report(conf_st, styles):
    """把 (style, GT, det) 计数摊成逐平台的混淆行文本（只列判错过的 GT 类）。

    为什么不是 34x34 全矩阵：本批 1087 格绝大多数落在对角线上，全矩阵打出来几百行
    空格，人不会看、diff 也看不出变化。这里只摊「错分过的类」，同时报该平台判对的
    总格数 —— 后者能和逐位总分对账（混淆矩阵漏计或多计一格就会对不上，见调用处）。
    错分里 `<无>` 是「这格根本没框到」，它属于检测层，不该混进「认错了」的语义里，
    所以照原样出现在去向上而不是被抹平。
    """
    by_style = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for (s, g, d), n in conf_st.items():
        by_style[s][g][d] += n
    out, diag = [], 0
    for s in sorted(styles):
        m = by_style.get(s, {})
        n_ok = sum(c.get(g, 0) for g, c in m.items())
        diag += n_ok
        bad = []
        for g in sorted(m):
            c = m[g]
            wrong = {d: n for d, n in c.items() if d != g}
            if wrong:
                bad.append(f"{g} {sum(c.values()) - sum(wrong.values())}/"
                           f"{sum(c.values())} -> "
                           + "+".join(f"{d}x{n}" for d, n in sorted(wrong.items())))
        out.append(f"  {s:8} 判对 {n_ok} 张，混淆 {len(bad)} 类"
                   + ("：" + "  ".join(bad) if bad else "（无：所有出现过的类都全对）"))
    return out, diag


def box_accounting(n_slots, n_boxes, n_missed):
    """框级（检测层）口径的唯一一处公式：返回 (TP, FP, 漏槽, FN)。

    分母是「人工确认在立牌行里、正面朝上」的张数 = GT 槽 + `+` 台账；FN 由两部分
    组成，必须分开返回才看得懂债在谁身上：
      - 漏槽：GT 里有标签却一个框都没拿到（检测器自己少框，或被 LOFO 剔模板后少框）；
      - 台账漏框：牌连 GT 都没进（`missed`），只有人工看图才看得见。
    FP 是「检出框多于 GT 槽」，它可能是真误检（一枚被劈成两框），也可能是行尾摸牌
    被扫进 hand —— 两者的归因由 grid_slot_outliers 分开，不在这里下结论。
    FN 作为第四项一并返回：口径只许在这一处定义，让调用方各自加一遍就会漂。
    """
    tp = min(n_boxes, n_slots)
    fp = max(0, n_boxes - n_slots)
    miss_slot = max(0, n_slots - n_boxes)
    return tp, fp, miss_slot, miss_slot + n_missed


def grid_slot_outliers(dets, grid):
    """数出「不在检测器自报局格里的框」与「挤在同一槽的框」。

    为什么不用「末框间隙远大于节距」当判据（旧版就是这么写的，已被实测否掉）：
    微乐 weile#04 的摸牌位只比正常节距多 30px（83 → 113，1.36 倍），腾讯的摸牌位
    间隙只有 42px（牌宽 80）—— 两个平台的「空档」在像素上根本没有共同点，按倍数
    判就漏掉了本该解释的那一框（见 build/weile04_gap.txt 的逐框坐标）。
    但「它不在这排格的槽位里」这件事两边都成立：检测器自己在 last_hand_grid 里就
    报了 (首格 x, 节距, 槽数)，拿它当尺子比任何经验阈值都直接。

    返回 (局格外的框数, 同槽重复的框数)；grid 拿不到时返回 None —— 判不准就照实
    报「待查」，不猜一个归类把口径差洗成误检。
    """
    if not grid:
        return None
    x0, pitch, n = grid
    if pitch <= 0 or n <= 0:
        return None
    out = dup = 0
    seen = set()
    for r, _l, _s in sorted(dets, key=lambda d: d[0][0]):
        slot = round((r[0] - x0) / pitch)
        if not 0 <= slot < n:
            out += 1
        elif slot in seen:
            dup += 1
        else:
            seen.add(slot)
    return out, dup


def shift_runs(gt, got_k):
    """返回由“整段平移”造成的错位下标（0 基，针对 gt/got_k 这两个等长序列）。

    为什么必须单独识别它：检测层把一枚牌劈成两框（或多框、漏框）时，det 序列会相对
    GT 整体左移一格，于是“7p→6p、8p→7p、9p→8p”连着报三错——看起来像筒子圈数
    数错，实际分类器一张都没错，错的是框。实测 weile#06 与 tencent#08 就是这种。
    不摘掉它就会去攻一个不存在的分类问题（归因错一次，下一轮就白跑）。
    判据：gt[k] == got[k+1] 且同向连续 ≥2 格（单格相同只是碰巧，不算）。
    这些格**仍计入失误总数**（账不能靠改归因做小），只是换到检测层那个桶。
    """
    out, k, n = set(), 0, len(gt)
    while k < n - 1:
        if gt[k] != got_k[k]:
            run, j = [], k
            while j < n - 1 and gt[j] == got_k[j + 1] and gt[j] != got_k[j]:
                run.append(j)
                j += 1
            if len(run) >= 2:
                out.update(run)
                k = j
                continue
        k += 1
    return out


def align_by_slots(dets, grid, n_gt):
    """把检测框按**物理槽位**对齐到 GT 位序，而不是按“它是第几框”。

    为什么必须按槽位：`detect_hand_strip` 是把牌带等宽切成 k 格，而置信救援会把低分
    张整框丢掉 —— 于是“检出的第 j 框”并不等于“第 j 格”。实测 zj#08 漏 4 框后，第 10 格
    的七筒被当成第 6 格的一筒判成一条真失误（看图定案：那一框里就是七筒，见
    build/audit_zj_08_lofo.png）。拿序号对齐去算逐位，一个漏框会把后面整排牌集体
    左移一格，看起来像“筒子圈数数错”，实际分类器一张都没错。

    不自洽就返回 None（调用方退回序号对齐并在报告里写明）：宁可少修一条，也不要
    拿一个猜出来的对齐去改分数。`grid` = 生产侧 `last_hand_grid` = (左边界 x, 节距 tw,
    格数 k)；主格之外的框（摸牌/换牌浮牌）按 x 顺序接在 k、k+1… 上。
    """
    if not dets or not grid:
        return None
    bx, tw, k = grid
    if tw <= 0 or k <= 0:
        return None
    slots = [int(round((float(r[0]) - bx) / tw)) for (r, _l, _s) in dets]
    tail = 0
    for i, s in enumerate(slots):        # 局格之外的浮牌按顺序接到尾部
        if s >= k:
            slots[i] = k + tail
            tail += 1
    if len(set(slots)) != len(slots) or min(slots) < 0 or max(slots) > n_gt - 1:
        return None
    labs = ["<无>"] * n_gt
    scs = [0.0] * n_gt
    for sl, (_rect, lbl, sc) in zip(slots, dets):
        labs[sl] = lbl
        scs[sl] = float(sc)
    holes = [i + 1 for i, v in enumerate(labs) if v == "<无>"]
    note = (f"按槽位对齐（局格 k={k}、检出 {len(dets)}/{n_gt} 槽"
            + (f"、漏槽 {holes}" if holes else "、无漏槽") + "）")
    return labs, scs, note


def load_provenance():
    """style -> {源帧号 -> 该帧贡献的模板 key 集}，读 tiles/<style>/provenance.json。

    这是 LOFO 能算“样本外”的前提：没有溯源信息就不知道该剔哪些模板。
    旧 bank（本轮之前建的）没有 provenance，返回里没有该 style——那些
    style 的帧不会被剔除，报告里会标成“无溯源（未剔模板）”而不是默默当成 LOFO。

    归属只认 `build_platform_bank.frame_of` 一把尺（建库端就是用它写 provenance 的）。
    这里曾内联过一份 `re.match(r"\\w+_f(\\d+)_")`，而卫生守卫那边又用
    `re.search(r"f(\\d+)_")` 数“无帧归属”的张数：同一批样本量出 17 / 145 两个数。
    口径分叉一次，之后每个“剔帧后读数”都得附带一句“按哪把尺剔的”，等于没有口径。
    无前缀（`f03_10_8p.png`）与 `legacy_*` 都返回 None = 不落选：它们的帧号属于
    另一批次（public/0），与本批帧号撞号，按帧号剔会误删别家模板（weile 的 79 张
    旧样本与本批 13 帧已按 md5 证实不重叠），所以宁可承认“它在每一折都在场”。
    """
    prov = collections.defaultdict(dict)
    if not os.path.isdir(TILES):
        return prov
    for st in sorted(os.listdir(TILES)):
        p = os.path.join(TILES, st, "provenance.json")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fp:
            d = json.load(fp)
        for key, fn in d.items():
            fr = frame_of(fn)
            if fr is not None:
                prov[st].setdefault(fr, set()).add(key)
    return prov


def unattributed_counts():
    """{style: 不落选模板张数}：文件名拿不到源帧归属的模板。

    LOFO 拿它们没办法 —— 每一折评测它们都白拿“见过的素材”，所以样本外估计是
    偏乐观的。这个数字必须每次评测都印在报告头上（而不是只躺在守卫的棘轮里），
    否则“LOFO 口径”这个词会被读成比实际更硬的东西。张数上限由
    `localtest/test_bank_hygiene.py` 的棘轮钉住（只许降不许升）。
    """
    out = {}
    if not os.path.isdir(TILES):
        return out
    for st in sorted(os.listdir(TILES)):
        p = os.path.join(TILES, st, "provenance.json")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fp:
            d = json.load(fp)
        n = sum(1 for fn in d.values() if frame_of(fn) is None)
        if n:
            out[st] = n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", type=float, default=None,
                    help="按【保留内精度】判退出码（如 0.97），同时受 --min-cover 约束")
    ap.add_argument("--min-cover", type=float, default=0.90,
                    help="门禁附带的覆盖率下限（默认 0.90）。不加这一条的话，“精度 97%%”"
                         "可以靠只输出 20%% 最有把握的牌刷出来——那是拒识不是识别。")
    ap.add_argument("--gt", action="append", default=None,
                    help="GT json，可重复传多个合并评测（默认只跑 gt/new_shots.json）")
    ap.add_argument("--out", default=None,
                    help="报告落盘路径（默认 build/new_material_report.txt；"
                         "换批次跑时指个新名字，不会把上一批结论顶掉）")
    ap.add_argument("--lofo", action="store_true",
                    help="逐帧留一（Leave-One-Frame-Out）：评每一帧前先把该帧贡献的"
                         "模板从 bank 抽掉。bank 往往就是从这批帧收割的，不开 LOFO 就是"
                         "样本内评测（上限），开了才是可外推的样本外估计。")
    ap.add_argument("--no-declare", dest="declare", action="store_false",
                    help="不按帧声明平台（回到离线旧口径，数字更差且生产不可达）")
    ap.add_argument("--with-probe", dest="with_probe", action="store_true",
                    help="对照口径：声明平台时仍扫全库探针（拨回本改动之前的行为）")
    ap.set_defaults(declare=True)
    a = ap.parse_args()
    out = a.out or OUT

    gt_paths = a.gt or [GT_PATH]
    shots = []
    for gp in gt_paths:
        if not os.path.exists(gp):
            print(f"GT 不存在：{gp}")
            return 2
        with open(gp, encoding="utf-8") as fp:
            shots += json.load(fp)["shots"]
    if not shots:
        print("GT 里没有帧，无法评测")
        return 2

    det = TencentGridDetector()
    # 对照口径：`--with-probe` 把「声明平台跳全库探针」拨回旧行为（生产口子
    # `probe_when_declared`）。面板口径量到 1 格差异（shushan#01 的 4p→5p），
    # 必须能在这把尺上复现/否证，才知道差异出在通道还是门槛层。
    det.probe_when_declared = a.with_probe
    banks = bank_label_sets(det)
    prov = load_provenance() if a.lofo else {}

    # 路由探针：均分低到底是因为“进了对的 bank 但模板不够”，还是因为
    # 真的走了跨 bank 的兜底全扫？两者的修法完全不同。
    # 必须看**解析后**的候选集：声明平台后 `classify_tile(styles=None)` 会被
    # `_resolve_styles` 换成白名单，拿“入参是 None”当“跳全库”会把本家候选
    # 报成兜底全扫（旧版就错在这里，导致报告把缺类问题说成路由问题）。
    route = {"style": "未测", "n": 0, "full": 0, "libs": collections.Counter()}
    _probe = det._probe_style
    _cls = det.classify_tile

    def _probe_rec(crop, *a, **kw):
        route["style"] = _probe(crop, *a, **kw)
        return route["style"]

    def _cls_rec(crop, **kw):
        route["n"] += 1
        st = det._resolve_styles(kw.get("styles"))
        if st is None:
            route["full"] += 1
            route["libs"]["<全库>"] += 1
        else:
            route["libs"]["+".join(sorted(st))] += 1
        return _cls(crop, **kw)

    det._probe_style = _probe_rec
    det.classify_tile = _cls_rec

    def _route_note():
        route["style"], route["n"], route["full"] = "未测", 0, 0
        route["libs"] = collections.Counter()

    lines = [f"口径：" + ("按帧声明平台（生产口径）" if a.declare else "不声明平台（离线旧口径，生产不可达）"),
             f"样本：" + ("LOFO 逐帧留一（样本外估计）" if a.lofo else "全部模板在库（样本内，是上限不可当预期）"),
             f"人工已钉帧：{len(shots)}  各 bank 标签数："
             + "  ".join(f"{s}:{len(v)}" for s, v in sorted(banks.items()))]
    lines.insert(2, "手牌通道：" + ("旧行为（声明平台仍扫全库探针）—— 对照"
                                    if a.with_probe else
                                    "现行（声明平台时排布查表、跳全库探针）"))
    if a.lofo:
        sticky = unattributed_counts()
        lines.append(f"不落选模板（每折都在场，下面的样本外估计对它们偏乐观）："
                     + ("  ".join(f"{s}:{n}" for s, n in sorted(sticky.items()))
                        + f"  合计 {sum(sticky.values())} 张" if sticky else "无"))
    lines.append("")
    tot = hit_ms = hit_pos = 0
    struct_tot = struct_hit = 0     # 因剔模板而该帧独有的牌（结构性缺类，非算法失误）
    n_q = 0                         # 人工标 `?` 的不计分格总数（必须在汇总里报出来）
    no_prov = collections.Counter()  # 开了 LOFO 但无溯源可剔的 style
    per_style = collections.defaultdict(lambda: [0, 0])
    per_style_pos = collections.defaultdict(lambda: [0, 0])   # 逐位口径（对齐后才计）
    conf = collections.Counter()
    conf_st = collections.Counter()   # (style, GT, det)：逐平台混淆矩阵
    bank_gap = collections.Counter()
    foreign = collections.Counter()   # 检出的标签不在本风格 bank 内 ⇒ 走了跨库全扫
    per_tile = []          # (分数, 逐位对不对, 是否属结构性缺类)：量"低分=不确定"可不可靠
    real_conf = collections.Counter()     # 真失误的 GT→det 配对（已剔结构性缺类）
    real_blame = collections.Counter()    # 真失误按归因分桶
    real_rows = []                        # (style, 帧, 位序, GT, det, 分数, 归因)
    real_by_style = collections.defaultdict(lambda: [0, 0])   # 真失误数/可外推格数
    n_slot_align = [0]      # 多少帧用了槽位对齐（报告里要印：它是逐位数字的对齐口径）
    # 框级（检测层）账：style -> [TP, FP, FN]。它和逐位是两个正交的问题——逐位问
    # “框里的牌认对没”，框级问“这张牌有没有被框住”。逐位只比检出的格，漏框的牌
    # 既不进分子也不进分母：不单独记账的话，“逐位 99%”和“物理上少认了两张”可以同时成立。
    det_box = collections.defaultdict(lambda: [0, 0, 0])
    det_rows = []           # 有 FP/FN 的帧：(style, 帧, GT槽, 检出框, 台账漏框, FP)
    missed_tot = 0          # 人工 `+` 台账的总张数（GT 之外的漏框）

    for e in shots:
        # 后期批次的素材不在 public/0，GT 条目自带 src 目录；缺省才回到 public/0。
        src = os.path.join(REPO, e.get("src") or os.path.relpath(SRC, REPO))
        path = os.path.join(src, e["file"])
        img = cv2.imread(path)
        if img is None:
            lines.append(f"[{e['frame']:02d}] 读不到 {path}，跳过")
            continue
        # 生产口径：用户在面板里选了这个平台，分类候选集被白名单收窄
        det.set_platform_styles(STYLE_OF.get(e["style"]) if a.declare else None)
        # LOFO：先把本帧贡献的模板抽掉，剩下的 bank 等于“没见过这一帧”的状态。
        cores_all, keys_all = det._cores, det._core_keys
        harv_all = det._core_harvested
        drop = prov.get(e["style"], {}).get(e["frame"], set())
        if a.lofo and not drop and e["style"] not in prov:
            no_prov[e["style"]] += 1
        if drop:
            # 按**模板实例**剔，并且只剔从 GT 帧收割的那些：具体两条理由见
            # `lofo_keep_indices` 的 docstring（都是实测坐实的坑）。
            keep = lofo_keep_indices(cores_all, keys_all, harv_all, e["style"], drop)
            det._cores = [cores_all[i] for i in keep]
            det._core_keys = [keys_all[i] for i in keep]
            det._core_harvested = [harv_all[i] for i in keep]
        banks_now = bank_label_sets(det) if drop else banks
        _route_note()
        dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
        rs = route["style"]
        rt = f"探针→{'本平台' if rs == e['style'] else (rs or '未路由')}"
        if a.declare:
            rt += f"  声明={STYLE_OF.get(e['style'])}"
        rt += (f"  比对{route['n']}次/候选库 "
               + ", ".join(f"{k}×{v}" for k, v in route["libs"].most_common()))
        got = [l for _r, l, _s in dets]
        scores = [float(s) for _r, _l, s in dets]
        gt_all = e["hand"]
        # `?` = 人工显式标的「不参与计分格」：牌面读得清，但形态不适合当样本
        # （摸牌位倾斜抬高、被角标/选中遮罩盖住）。harvest 端同一记号已跳过落盘，
        # 这里同步跳过计分；否则唯一的替代方案是弃掉整帧十几张——拿素材量换一个格。
        # 只有人工写出 `?` 才生效，漏标就当普通格算，所以不会悄悄丢样本。
        keep = [i for i, x in enumerate(gt_all) if x != "?"]
        n_q += len(gt_all) - len(keep)
        gt = [gt_all[i] for i in keep]
        # 先试物理槽位对齐；拿不到局格（或局格与 GT 长度不自洽）才退回序号对齐。
        aligned = align_by_slots(dets, getattr(det, "last_hand_grid", None), len(gt_all))
        if aligned:
            got_full, sc_full, align_note = aligned
            n_slot_align[0] += 1
        else:
            got_full, sc_full = got, scores
            align_note = ("按序号对齐（无局格或局格不自洽：漏框会把后面整排牌左移）"
                          if len(got) != len(gt_all) else "按序号对齐（框数与 GT 相等）")
        got_k = [got_full[i] for i in keep]
        sc = [sc_full[i] for i in keep]
        gm, dm = collections.Counter(gt), collections.Counter(got_k)
        inter = sum((gm & dm).values())
        pos = sum(1 for x, y in zip(gt, got_k) if x == y)
        tot += len(gt)
        hit_ms += inter
        hit_pos += pos
        per_style[e["style"]][0] += inter
        per_style[e["style"]][1] += len(gt)
        per_style_pos[e["style"]][0] += pos
        per_style_pos[e["style"]][1] += len(gt)
        # 框级口径：分母是「人工确认在立牌行里、正面朝上」的张数 = GT 槽 + `+` 台账。
        # 行尾单独摆的摸牌与整排背面牌不算（它们没有可判的牌面，见 labels_b1.txt 里
        # tencent#12 / gd_queshen#04 两条注释），所以不进这里任何一项。
        missed = list(e.get("missed", []))
        missed_tot += len(missed)
        n_tp, n_fp, n_fs, n_fn = box_accounting(len(gt_all), len(got), len(missed))
        det_box[e["style"]][0] += n_tp
        det_box[e["style"]][1] += n_fp
        det_box[e["style"]][2] += n_fn
        if n_fp or n_fn:
            note = []
            if n_fp:
                # 多框有两种：真的误检（一枚被劈成两框），和「摸牌位被扫进 hand」。
                # 后者是口径差（b6k），把它当误检扣 precision 会去攻一个不存在的缺陷。
                ol = grid_slot_outliers(dets, getattr(det, "last_hand_grid", None))
                if ol is None:
                    note.append("多框待查（拿不到局格，不猜归因）")
                elif ol[0]:
                    note.append(f"{ol[0]} 框落在局格外的行尾摸牌位"
                                f"（口径差 b6k，非误检）")
                elif ol[1]:
                    note.append(f"{ol[1]} 框挤在同一槽（一枚被劈两框，真误检）")
                else:
                    note.append("多框待查（均落在局格内）")

            if n_fs and drop:
                note.append("漏槽发生在 LOFO 剔模板后（模板分是手牌带守卫的输入，"
                            "属素材债；全库时能检出）")
            det_rows.append((e["style"], e["frame"], len(gt_all), len(got),
                             len(missed), n_fs, n_fp, "；".join(note)))
        # 本帧独有的牌：LOFO 剔完后该 style bank 里真的没这个类了。
        # 这类失误是“素材不够”而不是“识别不够”，必须与真失误分开报，否则会把
        # 样本量的债当成算法的债（而修法完全不同：前者是补帧，后者是改分类）。
        struct = {i for i, x in enumerate(gt)
                  if x not in banks_now.get(e["style"], set())}
        if drop:
            # 还包括“剔完模板后连框都拿不到”的位置（got_k 里的 `<无>`）：模板分是
            # 手牌带守卫的输入之一，本帧牌类被剔光会让检测器直接少框。它同样是
            # 素材债不是分类债（实测同一帧全库时 harvest 硬门能检出全部牌）。
            # 只在确实剔了模板时才这么归因：没剔模板还漏框就是真的检测失误。
            struct |= {i for i, y in enumerate(got_k) if y == "<无>"}
        st_idx = sorted(struct)
        struct_tot += len(st_idx)
        struct_hit += sum(1 for i in st_idx if i < len(got_k) and got_k[i] == gt[i])
        tag = ""
        if a.lofo:
            tag = (f"  [LOFO 剔{len(drop)}模板/结构性缺类{len(st_idx)}张]"
                   if drop else "  [无溯源，未剔模板=样本内]")
        if len(keep) != len(gt_all):
            tag += f"  不计分格 {len(gt_all) - len(keep)}（`?`）"
        if missed:
            tag += f"  漏框台账 {len(missed)}({' '.join(missed)})"
        lines.append(f"[{e['frame']:02d}] {e['style']:8} GT{len(gt_all)}张 det{len(got)}张  "
                     f"逐张 {inter}/{len(gt)}  逐位 {pos}/{len(gt)}  "
                     f"均分 {sum(sc) / len(sc) if sc else 0:.2f}{tag}")
        lines.append(f"     {rt}")
        lines.append(f"     GT : {' '.join(gt_all)}")
        lines.append(f"     det: {' '.join(got_full)}   {align_note}")
        for lab in (gm - dm):
            if lab not in banks_now.get(e["style"], set()):
                bank_gap[(e["style"], lab)] += gm[lab]
        own = banks_now.get(e["style"], set())
        for lab in got_k:
            if lab not in own:
                foreign[(e["style"], lab)] += 1
        bad = []
        n_real = 0
        shift_idx = shift_runs(gt, got_k)
        for j, (x, y) in enumerate(zip(gt, got_k)):
            per_tile.append((sc[j], x == y, j in struct))
            conf_st[(e["style"], x, y)] += 1
            if x == y:
                continue
            conf[(x, y)] += 1
            if j in struct:
                # 这张牌的模板就是本帧供的，LOFO 剔完就没了：是素材债不是分类债，
                # 所以只标 `[缺]` 不进真失误账（否则会把补帧能还的债算到算法头上）
                bad.append(f"#{keep[j] + 1} GT{x}→det{y}({sc[j]:.2f})[缺]")
                continue
            n_real += 1
            bl = "多框/切分平移（检测层）" if j in shift_idx else blame(x, y)
            real_conf[(x, y)] += 1
            real_blame[bl] += 1
            real_rows.append((e["style"], e["frame"], keep[j] + 1, x, y, sc[j], bl))
            bad.append(f"#{keep[j] + 1} GT{x}→det{y}({sc[j]:.2f})"
                       + ("[移]" if j in shift_idx else ""))
        real_by_style[e["style"]][0] += n_real
        real_by_style[e["style"]][1] += len(gt) - len(struct)
        if bad:
            lines.append("     错位：" + "  ".join(bad))
        lines.append("")
        det._cores, det._core_keys = cores_all, keys_all   # 恢复全库：下一帧的 LOFO 只剔它自己那一帧
        det._core_harvested = harv_all                    # 三轨必须一起恢复，不然长度错位

    summ = [f"合计：逐张 {hit_ms}/{tot} = {100.0 * hit_ms / tot:.1f}%   "
            f"逐位 {hit_pos}/{tot} = {100.0 * hit_pos / tot:.1f}%"]
    # 逐位是个对齐口径上的数字，必须说清算法拿到的 det 行是怎么贴到 GT 行上的：
    # 槽位对齐修的是“漏框把后面整排牌左移”这个测量假案，序号对齐仍会把它记成失误。
    summ.append(f"逐位对齐：{n_slot_align[0]}/{len(shots)} 帧按物理槽位对齐"
                f"（其余框数与 GT 相等或局格不自洽，退回序号对齐）")
    if n_q:
        # 必须显式报出：`?` 是从分母里剔东西的口子，不给它一个数就等于允许
        # “把不好读的牌全标成 ?”来刷精度。
        summ.append(f"人工标 `?` 的不计分格：{n_q} 个（读得清但不适合当样本，已出分母；"
                    f"这个数持续变大就说明素材质量在下滑）")
    if a.lofo:
        if struct_tot:
            adj_t, adj_h = tot - struct_tot, hit_pos - struct_hit
            summ.append(f"LOFO：{struct_tot}/{tot} 张属结构性缺类（本帧是唯一含该牌的帧，"
                        f"剔完就没模板；含因模板被剔光而连框都拿不到的位置）"
                        f"——这是样本缺口不是算法失误，这些位置本帧仍判对 "
                        f"{struct_hit} 张（靠跨库兜底偶然得对，不能当能力）")
            if adj_t > 0:
                summ.append(f"扣除结构性缺类后的可外推逐位精度：{adj_h}/{adj_t} = "
                            f"{100.0 * adj_h / adj_t:.1f}%")
        else:
            summ.append("LOFO：无结构性缺类（每个牌类至少两帧供模板，剔一帧仍有余）")
        if no_prov:
            summ.append("无溯源可剔（数字仍为样本内）："
                        + "  ".join(f"{s}×{n}帧" for s, n in sorted(no_prov.items())))
        # 自检：错位必须恰好被“缺类 + 真失误”两笔账分完。对不上就是归因逻辑
        # 漏了（比如 struct 跟真失误集重叠），宁可在报告里叫也不拿错口径过门禁。
        n_bad_all = tot - hit_pos
        n_struct_bad = struct_tot - struct_hit
        n_real_all = sum(v[0] for v in real_by_style.values())
        if n_bad_all != n_struct_bad + n_real_all:
            summ.append(f"!! 自检失败：错位 {n_bad_all} != 缺类 {n_struct_bad} "
                        f"+ 真失误 {n_real_all}（归因集合有重叠或漏计）")
        elif real_rows:
            adj_n = tot - struct_tot
            summ.append(f"真失误（扣掉缺类后的算法/检测债）：{n_real_all}/{adj_n} = "
                        f"{100.0 * n_real_all / adj_n:.1f}%   "
                        + "  ".join(f"{s}:{v[0]}" for s, v in sorted(real_by_style.items()) if v[0]))
            summ.append("  按归因：" + "  ".join(f"{k}x{n}" for k, n in real_blame.most_common()))
    lines[3:3] = summ

    lines.append("逐平台（逐张口径）：" + "  ".join(
        f"{s} {h}/{n}={100.0 * h / n:.1f}%" for s, (h, n) in sorted(per_style.items())))
    if a.lofo:
        lines.append("逐平台（逐位口径，与门禁同口径）：" + "  ".join(
            f"{s} {v[0]}/{v[1]}={100.0 * v[0] / v[1]:.1f}%"
            for s, v in sorted(per_style_pos.items())))
    # 检测层单独一栏：它不能与逐位合并成一个百分比（合并后“漏一张牌”会被当成
    # “认对率上升”，而这两件事的修法完全不同——一个是改检测 ROI/节距，一个是改打分）。
    # 97% 门禁只按逐位识别口径卡；这里的数字是用来暴露“逐位看不见的洞”的。
    lines.append("逐平台检测层（框级 TP/FP/FN；只算立牌行内的正面牌，"
                 "行尾摸牌与背面灰置牌不算）：")
    for s, (tp, fp, fn) in sorted(det_box.items()):
        rec = 100.0 * tp / (tp + fn) if tp + fn else 100.0
        prc = 100.0 * tp / (tp + fp) if tp + fp else 100.0
        lines.append(f"  {s:8} TP{tp:4d} FP{fp:3d} FN{fn:3d}"
                     f"  recall {rec:5.1f}%  precision {prc:5.1f}%")
    _tp = sum(v[0] for v in det_box.values())
    _fp = sum(v[1] for v in det_box.values())
    _fn = sum(v[2] for v in det_box.values())
    lines.append(f"  合计 TP{_tp} FP{_fp} FN{_fn}"
                 f"  recall {100.0 * _tp / max(1, _tp + _fn):.1f}%"
                 f"  precision {100.0 * _tp / max(1, _tp + _fp):.1f}%"
                 f"（其中 {missed_tot} 张 FN 来自人工 `+` 台账，其余是 GT 槽拿不到框）")
    for s, fr, ng, nd, nm, fs, fp, note in sorted(det_rows):
        lines.append(f"    {s:8}#{fr:02d}  GT槽{ng} 检出{nd} 台账漏{nm} 漏槽{fs} "
                     f"多框{fp}" + (f"  {note}" if note else ""))
    lines.append("混淆矩阵（按平台；`类 n对/总 -> 去向`，`<无>`=该格没框到属检测层）：")
    conf_lines, conf_diag = confusion_report(conf_st, per_style_pos)
    lines += conf_lines
    if conf_diag != hit_pos:
        lines.append(f"!! 混淆矩阵自检失败：对角 {conf_diag} != 逐位判对 {hit_pos}"
                     f"（矩阵漏计或多计了格子，上面的逐平台混淆不可信）")
    if conf:
        lines.append("逐位错配对（GT→det）："
                     + "  ".join(f"{x}->{y}x{n}" for (x, y), n in conf.most_common()))
    if bank_gap:
        lines.append("bank 缺类（该风格库里没有这张牌，属样本缺口而非算法失误）："
                     + "  ".join(f"{s}/{l}x{n}" for (s, l), n in bank_gap.most_common()))
    if foreign:
        lines.append("候选库内非本家标签（结果不在本风格 bank 内 ⇒ 被主 bank/别家模板抢走；"
                     "拿别家平台的字模认这家的牌，既慢又易错）："
                     + "  ".join(f"{s}/{l}x{n}" for (s, l), n in foreign.most_common()))

    if real_rows:
        lines.append("")
        lines.append("真失误清单（逐张可定位；结构性缺类已排除，这些都是补素材以外"
                     "必须改分类/检测的）：")
        for s, fr, i, x, y, v, bl in sorted(real_rows, key=lambda r: (r[6], r[0], r[1])):
            lines.append(f"  {s:8}#{fr:02d} 第{i:2d}枚  GT {x} -> det {y}  "
                         f"分数 {v:.2f}  归因：{bl}")
        lines.append("  真失误配对 TOP：" + "  ".join(
            f"{x}->{y}x{n}" for (x, y), n in real_conf.most_common(20)))

    ok = [s for s, good, _k in per_tile if good]
    bad = [s for s, good, _k in per_tile if not good]
    lines.append("")
    lines.append(f"分数分离度：正确 n={len(ok)} min={min(ok):.2f}  "
                 f"错误 n={len(bad)} max={max(bad) if bad else 0:.2f}  "
                 f"-> {'可分（错例全在低分区）' if bad and min(ok) > max(bad) else '有重叠，门限会误伤/漏放'}")
    lines.append("  门限   保留张数   保留内精度   被拒张数  其中本来错")
    curve = []
    for th in THRESHOLDS:
        keep = [g for s, g, _k in per_tile if s >= th]
        rej = [g for s, g, _k in per_tile if s < th]
        k_bad = sum(1 for g in keep if not g)
        acc = 100.0 * (len(keep) - k_bad) / len(keep) if keep else 0.0
        curve.append((th, acc, 100.0 * len(keep) / len(per_tile)))
        lines.append(f"  {th:.2f}   {len(keep):3d}/{len(per_tile)}      "
                     f"{acc:5.1f}%      {len(rej):3d}      {sum(1 for g in rej if not g)}")
    perfect = [c for c in curve if c[1] >= 99.9]
    if perfect:
        top = max(perfect, key=lambda c: c[2])
        lines.append(f"  零失误工作点：门限 {top[0]:.2f} -> 保留 {top[2]:.1f}% 的牌，保留内 100% 正确")
    lines.append("  注：保留内精度=只统计分数达标的张；被拒张不进建议（面板标灰），不计入失误。")
    if a.lofo and struct_tot:
        # 同一张分数曲线在“扣掉结构性缺类”的格集上再跑一遍：它才是“把素材补齐
        # 后能到多少”的估计。不给这一条，就只能拿一个被素材债锁死的数字去判算法。
        adj = [(s, g) for s, g, k in per_tile if not k]
        lines.append("  【扣除结构性缺类后的同一曲线（可外推口径）】")
        lines.append("  门限   保留张数   保留内精度   被拒张数  其中本来错")
        for th in THRESHOLDS:
            keep = [g for s, g in adj if s >= th]
            rej = [g for s, g in adj if s < th]
            k_bad = sum(1 for g in keep if not g)
            acc = 100.0 * (len(keep) - k_bad) / len(keep) if keep else 0.0
            lines.append(f"  {th:.2f}   {len(keep):3d}/{len(adj)}      "
                         f"{acc:5.1f}%      {len(rej):3d}      {sum(1 for g in rej if not g)}")
    raw_acc = 100.0 * hit_pos / tot if tot else 0.0
    lines.append(f"  不拒识口径（面板直接给出的每张都算）：逐位 {hit_pos}/{tot} = {raw_acc:.1f}%"
                 f"；逐张 {hit_ms}/{tot} = {100.0 * hit_ms / tot:.1f}%")

    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    if a.gate is not None:
        hit = [c for c in curve if c[1] / 100.0 >= a.gate and c[2] / 100.0 >= a.min_cover]
        if not hit:
            best = max(curve, key=lambda c: c[1]) if curve else None
            print(f"门禁未达：精度 >= {a.gate:.0%} 且覆盖率 >= {a.min_cover:.0%} 的门限不存在"
                  + (f"（最好一档仅 {best[1]:.1f}% / 覆盖 {best[2]:.1f}%）" if best else ""))
            return 1
        top = max(hit, key=lambda c: c[2])
        print(f"门禁达标：门限 {top[0]:.2f} 下保留内精度 {top[1]:.1f}%，覆盖率 {top[2]:.1f}%"
              f"（不拒识口径 {raw_acc:.1f}%）")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

