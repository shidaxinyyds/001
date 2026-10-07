# -*- coding: utf-8 -*-
"""面板口径实测：同一批 GT 帧、同一套 LOFO 剔模板，量的是 **面板最终给出的手牌**。

为什么要单独有这一个脚本（而不是引用 `eval_new_material.py` 的数字）：
`eval_new_material.py` 直接驱检测器（`detect_hand_strip`），量的是「识别通道的上限」；
面板真正用的是 `Engine.process` 的出口，中间还隔着玩法牌集闸门、`_apply_conf` 门槛、
稳定器、同字互斥与阶段门控。本轮已经坐实这两条口径会差出整批字牌
（广东雀神帧在川麻玩法闸门下少 7 张，见 `localtest/test_mode_gate_guard.py` 的 docstring），
所以**报准确率必须两个数一起报**，只报通道口径就是把闸门问题藏起来。

口径（全部写进报告头，不许只在代码里）：
  样本   LOFO 逐帧留一：评每一帧前先把该帧贡献的模板抽掉（与通道口径同一条尺，
         复用 `eval_new_material.lofo_keep_indices` / `load_provenance`，不另写一份）
  声明   每帧按 GT 的 style 声明平台，玩法由 `layer_cost.mode_for` 选一个**装得下
         该帧 GT 的玩法**（= 用户在该平台会拿到的默认玩法，装不下时退到 supported_modes
         里能装下的最小牌集，并记进「玩法缺口」台账）
  实例   每帧新建 Engine（冷喂）：稳定器没有历史，跨帧状态不互相污染；
         代价是把生产上「连续帧能靠稳定器补回」的那部分算成失误 —— 所以这个数
         是**保守下限**，不是生产已实现值
  计分   GT 里人工标 `?` 的格不计分（与通道口径同一约定），并显式报出该数
  对齐   面板 payload 只有牌串，位置信息在引擎出口已丢；逐位只在「牌串顺序与 GT
         槽位序同为空间序」时给出，否则只报逐张（多重集）与整帧集合精确

用法：
  py -3.10 -X utf8 localtest/eval_panel_lofo.py
  py -3.10 -X utf8 localtest/eval_panel_lofo.py --limit 5      # 先小规模试跑
  py -3.10 -X utf8 localtest/eval_panel_lofo.py --no-lofo      # 样本内上限（对照用）
输出：build/eval_panel_lofo.txt
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
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import engine.engine as E  # noqa: E402
from engine.engine import Engine  # noqa: E402
from eval_base import canon_mpsz, tile_accuracy  # noqa: E402
from eval_new_material import (STYLE_OF, bank_label_sets,  # noqa: E402
                               load_provenance, lofo_keep_indices)
from layer_cost import mode_for  # noqa: E402

GT = os.path.join(HERE, "gt", "shots_b1.json")
# 对照开关：`--with-probe` 置 True = 把「声明平台跳全库探针」拨回旧行为，
# 用来量这个改动在面板口径上到底动了哪几格。默认 False 量的才是现行生产路径。
WITH_PROBE = False


def load_shots(path):
    with open(path, encoding="utf-8") as fp:
        shots = json.load(fp)["shots"]
    return [e for e in shots if e.get("verified")]


def panel_hand(img, platform, mode, lofo=None):
    """按生产口径冷喂一帧，返回 (面板牌串列表, 状态, 剔掉的模板数)。

    `lofo=(style, drop)` 时在该帧的**手牌通道实例**上抽模板：必须从 Engine 里取
    通道而不是自己 new 一个 —— 自己 new 的那个不在链路上，剔了等于没剔（面板照样
    用全库，报出虚高的数字，而这正是本脚本要防的事）。
    """
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    try:
        eng = Engine()
        hand = eng.get_hand_detector()
        if WITH_PROBE:
            # 对照口径：把「声明平台跳全库探针」拨回旧行为（生产口子
            # `probe_when_declared`），用来量这个改动在**面板**上值多少格，而不是
            # 只信通道口径的「标签零差异」。
            hand.probe_when_declared = True
        saved = None
        n_drop = 0
        if lofo:
            style, drop = lofo
            n_drop = len(drop)
            if drop and hasattr(hand, "_cores"):
                cores_all, keys_all, harv_all = (hand._cores, hand._core_keys,
                                                 hand._core_harvested)
                keep = lofo_keep_indices(cores_all, keys_all, harv_all, style, drop)
                hand._cores = [cores_all[i] for i in keep]
                hand._core_keys = [keys_all[i] for i in keep]
                hand._core_harvested = [harv_all[i] for i in keep]
                saved = (cores_all, keys_all, harv_all)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = eng.process(img)
        finally:
            if saved:
                hand._cores, hand._core_keys, hand._core_harvested = saved
        d = json.loads(res.result) if res is not None else {}
        # 面板串保持**引擎给的顺序**：canon_mpsz 会排序，排完就永远看不出
        # 「逐位」是否还有意义，所以这里只拆码不排。
        raw = d.get("hand") or ""
        codes = [raw[i:i + 2] for i in range(0, len(raw), 2)]
        return codes, str(d.get("status")), n_drop
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=GT)
    ap.add_argument("--out", default=os.path.join(REPO, "build", "eval_panel_lofo.txt"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 帧（试跑）")
    ap.add_argument("--no-lofo", dest="lofo", action="store_false",
                    help="不剔模板 = 样本内上限，只能当对照，不许当预期")
    ap.add_argument("--with-probe", dest="with_probe", action="store_true",
                    help="对照口径：拨回「声明平台仍扫全库探针」的旧行为")
    ap.set_defaults(lofo=True)
    a = ap.parse_args()
    global WITH_PROBE
    WITH_PROBE = a.with_probe

    shots = load_shots(a.gt)
    if a.limit:
        shots = shots[:a.limit]
    prov = load_provenance() if a.lofo else {}
    # 结构性缺类的判据要与通道口径同源：剔完模板后该 style 的 bank 里到底还有没有这类牌。
    from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
    det = TencentGridDetector()
    banks_all = bank_label_sets(det)

    lines = [
        "口径：面板 = `Engine.process` 出口（每帧新建 Engine，冷喂，稳定器无历史 ⇒ 保守下限）",
        "样本：" + ("LOFO 逐帧留一（与通道口径同一条尺）" if a.lofo
                    else "全部模板在库（样本内上限，不可当预期）"),
        "声明：每帧按 GT style 声明平台 + 玩法（玩法由 layer_cost.mode_for 选，"
        "选不到该平台默认玩法时会记进「玩法缺口」台账）",
        f"GT：{a.gt}  已钉帧 {len(shots)}",
        "手牌通道：" + ("旧行为（声明平台仍扫全库探针）—— 对照"
                        if WITH_PROBE else
                        "现行（声明平台时排布查表、跳全库探针）"),
        "",
    ]
    tot = hit = exact = 0
    pos_tot = pos_hit = 0
    n_q = 0
    n_extra = 0                 # 面板比 GT 计分格多吐的张数（含落在 `?` 槽位上的）
    exact_clean = frames_clean = 0      # 只算 GT 里没有 `?` 的帧：`?` 槽位不在分母，
                                        # 面板在那儿读出什么都算"多"，混在一起会把
                                        # 整帧精确率压成一个没有意义的数
    per_style = collections.defaultdict(lambda: [0, 0])
    struct_tot = struct_hit = 0
    mode_gap = []
    no_prov = collections.Counter()
    status_cnt = collections.Counter()
    t0 = time.time()

    for e in shots:
        src = os.path.join(REPO, e.get("src") or os.path.relpath(
            os.path.join(REPO, "public", "1"), REPO))
        path = os.path.join(src, e["file"])
        img = cv2.imread(path)
        if img is None:
            lines.append(f"[{e['frame']:02d}] 读不到 {path}，跳过")
            continue
        gt_all = list(e["hand"])
        keep_i = [i for i, x in enumerate(gt_all) if x != "?"]
        gt = [gt_all[i] for i in keep_i]
        n_q += len(gt_all) - len(keep_i)
        platform = STYLE_OF.get(e["style"], "tencent")
        mode, miss = mode_for(platform, sorted(set(gt)))
        if miss:
            mode_gap.append((e["style"], e["frame"], mode, miss))
        drop = prov.get(e["style"], {}).get(e["frame"], set()) if a.lofo else set()
        if a.lofo and e["style"] not in prov:
            no_prov[e["style"]] += 1
        codes, status, _nd = panel_hand(
            img, platform, mode,
            lofo=(e["style"], drop) if a.lofo else None)
        status_cnt[status] += 1

        inter, _den = tile_accuracy(gt, codes)
        ms_gt, ms_panel = collections.Counter(gt), collections.Counter(codes)
        # 逐位只在「面板串长度 == GT 格数」时才有意义（漏框会把后面整排左移，
        # 那属于检测层，已由整帧集合精确与逐张两个数承担，不在这里重复扣分）
        same_len = len(codes) == len(gt)
        pos_hit += sum(1 for x, y in zip(gt, codes) if x == y) if same_len else 0
        pos_tot += len(gt) if same_len else 0
        tot += len(gt)
        hit += inter
        n_extra += sum((ms_panel - ms_gt).values())
        exact += int(sorted(codes) == sorted(gt))
        if len(keep_i) == len(gt_all):
            frames_clean += 1
            exact_clean += int(sorted(codes) == sorted(gt))
        per_style[e["style"]][0] += inter
        per_style[e["style"]][1] += len(gt)

        own = banks_all.get(e["style"], set())
        struct = {i for i, x in enumerate(gt) if x not in own}
        if drop:
            struct |= {i for i, x in enumerate(codes) if x == "<无>"}
        struct_tot += len(struct)
        struct_hit += sum(1 for i in struct if i < len(codes) and codes[i] == gt[i])

        lost = sorted((ms_gt - ms_panel).elements())
        extra = sorted((ms_panel - ms_gt).elements())
        tag = ""
        if a.lofo:
            tag = (f"  [剔{len(drop)}模板/缺类{len(struct)}张]" if drop
                   else "  [无溯源，未剔模板]")
        if len(keep_i) != len(gt_all):
            tag += f"  不计分格{len(gt_all) - len(keep_i)}"
        lines.append(f"[{e['frame']:02d}] {e['style']:8} {platform:11} 玩法{mode:8} "
                     f"GT{len(gt_all)} 面板{len(codes)}  逐张 {inter}/{len(gt)}  "
                     f"整帧{'精确' if sorted(codes) == sorted(gt) else '不精确'}  "
                     f"{status}{tag}")
        lines.append(f"     面板: {' '.join(codes)}")
        if lost or extra:
            lines.append(f"     相对GT 缺:{','.join(lost) or '-'}  "
                         f"多:{','.join(extra) or '-'}")
        lines.append("")

    wall = time.time() - t0
    summ = [f"合计（GT 计分格 {tot}，用时 {wall:.0f}s）：逐张 {hit}/{tot} = "
            f"{100.0 * hit / max(1, tot):.1f}%   整帧集合精确 {exact}/{len(shots)} = "
            f"{100.0 * exact / max(1, len(shots)):.1f}%"]
    summ.append(f"口径提醒：逐张是**召回口径**（分母只有 GT 计分格）。面板比计分格多吐 "
                f"{n_extra} 张，其中一部分落在 {n_q} 个标 `?` 的槽位上 —— `?` 不在分母，"
                f"所以这个数不能当精确率看；按「命中/(命中+多吐)」折算是 "
                f"{100.0 * hit / max(1, hit + n_extra):.1f}%。")
    summ.append(f"整帧集合精确（只算 GT 里没有 `?` 的干净帧）：{exact_clean}/{frames_clean} = "
                f"{100.0 * exact_clean / max(1, frames_clean):.1f}%"
                f"（全帧口径 {exact}/{len(shots)} 被 `?` 槽位压低了，不可直接引用）")
    if pos_tot:
        summ.append(f"逐位（仅框数与 GT 相等的帧，面板串保持引擎给出顺序）："
                    f"{pos_hit}/{pos_tot} = {100.0 * pos_hit / pos_tot:.1f}%")
    if tot - struct_tot:
        summ.append(f"扣除结构性缺类 {struct_tot}/{tot} 格后分母 = {tot - struct_tot}"
                    f"（这些格 LOFO 剔完本帧就没模板，属素材债不是算法债；"
                    f"其中本帧仍判对 {struct_hit} 格）")
    if n_q:
        summ.append(f"人工标 `?` 的不计分格：{n_q} 个（与通道口径同一约定，已出分母）")
    if mode_gap:
        summ.append("玩法缺口台账（默认玩法装不下该帧 GT，已按 mode_for 退让）：")
        summ += [f"    {s}#{f:02d} {m} 装不下 {x}" for s, f, m, x in mode_gap]
    else:
        summ.append("玩法缺口台账：无（每帧的声明玩法都装得下它的 GT）")
    if no_prov:
        summ.append("无溯源（LOFO 剔不到模板）的 style：" +
                    ", ".join(f"{k}×{v}帧" for k, v in sorted(no_prov.items())))
    summ.append("status 分布：" + "  ".join(f"{k}×{v}" for k, v in
                                            sorted(status_cnt.items(), key=lambda kv: -kv[1])))
    summ.append("逐平台（逐张）：" + "  ".join(
        f"{s} {h}/{n}={100.0 * h / max(1, n):.1f}%"
        for s, (h, n) in sorted(per_style.items())))
    summ.append("与通道口径的关系：这里低的那部分包含玩法闸门、置信/牌形/亮度门槛、"
                "稳定器与阶段门控；通道口径（eval_new_material）量不到这些。")
    text = "\n".join(lines + [""] + summ) + "\n"
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fp:
        fp.write(text)
    print("\n".join(summ))
    print(f"报告：{a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
