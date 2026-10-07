# -*- coding: utf-8 -*-
"""真失误归因：把生产检测器在**某一帧、某一个位序**上真正看到的东西摊开。

为什么必须是帧级、而不是裁片级：
  1. `tiles/` 里的裁片是 `face_align()`（象牙白最大连通块）抠的，而生产分类走的是
     `extract_face()`（去绿底外接框）+ 固定核窗口 `face[10:110, 6:74]`。两者对齐
     差几像素，NCC 就差 0.1~0.4。拿裁片做归因 = 在量另一个东西（实测：同一张
     queshen 东风，裁片口径 0.998、生产口径连 0.52 都不到，结论会完全反过来）。
  2. 手牌带不是逐枚检测的，而是主块宽度 ÷ 估出的张数 **等分成网格**
     （`detect_hand_strip::_try_counts`）。所以“框错”表现为整行平移或某张起
     切进邻牌 —— 只有回到原帧才看得见。

两个动作：
  --frame <style> <帧号> [--lofo] [--slots 8,11]   逐位序报 rect / GT / det /
                                                   候选排名，并把生产裁片拼成图
  --pairs <style>                                 全库跨类模板相似度（结构隐患）
  --dups [--thr 0.95]                             跨标签近重复模板（= 贴错名字）
  --xcheck --style <style>                        用 b1 人工样本校 legacy 模板身份

运行: py -3.10 -X utf8 localtest/audit_bank_confusion.py --frame weile 11 --lofo
      py -3.10 -X utf8 localtest/audit_bank_confusion.py --pairs shushan
"""
import argparse
import collections
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
sys.path.insert(0, HERE)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_new_material import (STYLE_OF, load_provenance, bank_label_sets,  # noqa: E402
                               lofo_keep_indices)

GT_PATH = os.path.join(HERE, "gt", "shots_b1.json")
TILES = os.path.join(HERE, "tiles")
CELL_W, CELL_H = 96, 144      # 裁片展示尺寸（含标题条）


def load_shots(path=GT_PATH):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)["shots"]


def find_shot(shots, style, frame):
    for e in shots:
        if e["style"] == style and int(e["frame"]) == int(frame):
            return e
    raise SystemExit(f"GT 里没有 {style}#{frame}")


def apply_lofo(det, style, frame, on):
    """复现评测的 LOFO 状态：把该帧收割的模板实例抽掉。返回 (恢复用快照, 剔掉数)。

    剔除规则直接拿 `eval_new_material.lofo_keep_indices` 那一份：逐格归因工具自己
    重写一遍剔帧，就会得到一个盘上并不存在的库状态（以前就是这里只按键名剔，把与
    本帧撞名的手绘主库模板一并抽掉了 33 张），然后拿这个状态指认“结构性缺类”。
    """
    snap = (det._cores, det._core_keys, det._core_harvested)
    if not on:
        return snap, 0
    cores, keys, harv = snap
    drop = load_provenance().get(style, {}).get(int(frame), set())
    if not drop:
        return snap, 0
    keep = lofo_keep_indices(cores, keys, harv, style, drop)
    det._cores = [cores[i] for i in keep]
    det._core_keys = [keys[i] for i in keep]
    det._core_harvested = [harv[i] for i in keep]
    # provenance 里一个 key 只对应一个实例，所以 len(drop) 就是本帧被抽掉的模板数，
    # 与评测逐行报的“LOFO 剔N模板”同一个数（两边对不上就没法逐格对账）。
    return snap, len(drop)


def frame_report(style, frame, lofo=False, slots=None, gt_path=GT_PATH, top=5):
    det = TencentGridDetector()
    e = find_shot(load_shots(gt_path), style, frame)
    img = cv2.imread(os.path.join(REPO, e.get("src") or os.path.join("public", "0"), e["file"]))
    if img is None:
        raise SystemExit(f"读不到帧图：{e['file']}")
    det.set_platform_styles(STYLE_OF.get(style))          # 生产口径：用户声明了平台
    snap, n_drop = apply_lofo(det, style, frame, lofo)
    dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])

    gt = e["hand"]
    # banks 必须在**剔完之后**算：评测里的“结构性缺类”就是相对 LOFO 库定义的，
    # 拿全库判会把“本帧唯一来源”的牌说成“模板明明在、是分类器错了”。
    # 同理，下面的 classify_tile_debug 也必须在剔完的状态下跑（函数结束才恢复）。
    banks = bank_label_sets(det).get(style, set())
    print(f"=== {style}#{int(frame):02d}  GT{len(gt)}张 det{len(dets)}张  "
          f"LOFO={'剔' + str(n_drop) + '模板' if lofo else '关'}  "
          f"{'!! 数量不等：位序对齐不可信' if len(gt) != len(dets) else ''}")
    if len(gt) != len(dets):
        print("    det: " + " ".join(l for _r, l, _s in dets))
    cells = []
    for i, (rect, lbl, sc) in enumerate(dets):
        if slots and (i + 1) not in slots:
            continue
        x, y, w, h = [int(v) for v in rect]
        crop = img[y:min(img.shape[0], y + h), x:min(img.shape[1], x + w)]
        dbg = det.classify_tile_debug(crop)
        g = gt[i] if i < len(gt) else "?"
        rank = dbg["ranking"]
        rival = next((l for l, _s in rank if l != g), "<无>")
        gap = dict(rank).get(g, 0.0) - dict(rank).get(rival, 0.0)
        mark = "对" if g == lbl else ("缺类" if g not in banks else "错")
        print(f"  第{i + 1:2d}枚 rect=({x},{y},{w}x{h})  GT {g} -> det {lbl} "
              f"{float(sc):.2f} [{mark}]  候选{dbg['n_candidates']}型"
              + (f"  !! 白名单重扫={dbg['label']}:{dbg['score']:.2f}（探针收窄改了结果）"
                 if dbg["label"] != lbl else ""))
        print("      " + "  ".join(f"{l}:{s:.2f}" for l, s in rank[:top])
              + f"   真值-最强干扰 {gap:+.2f}")
        cells.append((i + 1, g, lbl, float(sc), crop, mark))
    if cells:
        out = os.path.join(REPO, "build", f"audit_{style}_{int(frame):02d}{'_lofo' if lofo else ''}.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        cv2.imwrite(out, montage(cells))
        print(f"  裁片图 -> {out}")
    det._cores, det._core_keys, det._core_harvested = snap   # 三轨一起恢复，否则长度错位
    return 0


def montage(cells):
    """一行一格：生产裁片 + 标题（位序 / GT / det）。红框 = 判错，绿框 = 判对。"""
    rows = []
    for i, g, lbl, sc, crop, mark in cells:
        c = crop
        if c.size:
            c = cv2.resize(c, (CELL_W - 6, CELL_H - 26), interpolation=cv2.INTER_NEAREST)
        else:
            c = np.zeros((CELL_H - 26, CELL_W - 6, 3), np.uint8)
        cell = np.full((CELL_H, CELL_W, 3), 255, np.uint8)
        cell[22:22 + c.shape[0], 3:3 + c.shape[1]] = c
        col = (0, 128, 0) if mark == "对" else ((0, 0, 255) if mark == "错" else (200, 120, 0))
        cv2.rectangle(cell, (3, 22), (3 + c.shape[1] - 1, 22 + c.shape[0] - 1), col, 2)
        cv2.putText(cell, f"#{i} {g}->{lbl}", (3, 14), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(cell, f"{sc:.2f}{'' if mark == '对' else ' ' + mark}", (CELL_W - 46, 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1, cv2.LINE_AA)
        rows.append(cell)
    return np.hstack(rows)


def pairs(style, top=15):
    """跨类最相似的模板对：它们在生产里天生会互相抢判（同分时谁在前全看排序）。"""
    det = TencentGridDetector()
    det.set_platform_styles(STYLE_OF.get(style, style))
    bank = collections.defaultdict(list)
    for (lbl, st, _btn, plain, _gb, _gp) in det._cores:
        if st == style:
            bank[lbl.split("#")[0]].append(plain)
    rows = []
    labels = sorted(bank)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            best = max(float(cv2.matchTemplate(ca, cb, cv2.TM_CCOEFF_NORMED).max())
                       for ca in bank[a] for cb in bank[b])
            rows.append((best, a, b))
    rows.sort(reverse=True)
    print(f"=== {style}：跨类模板相似度 TOP{top}（共 {len(labels)} 类）===")
    for s, a, b in rows[:top]:
        print(f"  {a} <-> {b}  {s:.3f}")
    print(f"  共 {len(rows)} 对；>=0.90 的 {sum(1 for r in rows if r[0] >= 0.90)} 对，"
          f">=0.95 的 {sum(1 for r in rows if r[0] >= 0.95)} 对")
    return 0


def dups(thr=0.95, styles=None):
    """跨标签近重复模板：不同标签的两张模板核 NCC >= thr = 至少一张贴错了名字。

    为什么能这样断言：匹配核是牌面中心 88x56，同一张牌的不同帧变体 NCC 通常
    >=0.985（`build_platform_bank.pick_variants` 就用这个阈值归族）；而两个**真
    不同的牌面**实测最高只到 0.79（shushan 4p/5p）。所以 0.95 以上跨标签只会
    是一种原因：模板集里有贴错标签的样本（已坐实：shushan 的 `legacy_7s.png`
    与 `legacy_7s#s4.png` 其实是五索，`legacy_5s.png` 其实是三索）。

    不拚出来就会一直拿“分类失误”去解释一个素材错误：LOFO 一剔掉本帧模板，
    假 7s 就能以 0.99 抢走真 5s，看上去像模型差，其实是库脏。
    """
    det = TencentGridDetector()
    inst = []                       # (style, label, key, core)
    for (lbl, st, _btn, plain, _gb, _gp), key in zip(det._cores, det._core_keys):
        if styles and st not in styles:
            continue
        inst.append((st, lbl.split("#")[0], key, plain))
    rows = []
    for i, (s1, l1, k1, c1) in enumerate(inst):
        for s2, l2, k2, c2 in inst[i + 1:]:
            if s1 != s2 or l1 == l2:
                continue
            v = float(cv2.matchTemplate(c1, c2, cv2.TM_CCOEFF_NORMED).max())
            if v >= thr:
                rows.append((v, s1, l1, k1, l2, k2))
    rows.sort(reverse=True)
    print(f"=== 跨标签近重复模板（同平台、不同标签、核 NCC >= {thr:.2f}）：{len(rows)} 对 ===")
    for v, st, la, ka, lb, kb in rows:
        print(f"  {st:8} {la}/{ka}  <->  {lb}/{kb}   {v:.3f}")
    by_style = collections.Counter(r[1] for r in rows)
    if by_style:
        print("  按平台：" + "  ".join(f"{s}x{n}" for s, n in by_style.most_common()))
    return rows        # 给守卫测试直接断言，不靠解析 stdout


def xcheck(style):
    """用“人工钉过的 b1 样本”给每张模板定身份，报出与文件名标签不一致的。

    为什么只查非 b1 样本：`b1_f*_<label>.png` 的名字是从人工 GT 行序直接写的，错
    一格当场就会被质门（整帧长度比对）拒掉；而 `legacy_*` 是旧坐标脚本产的，名字
    没人核过。实测已坐实它不可信：shushan 的 `legacy_7s.png` 其实是五索。
    拿“可信的”去审“没根据的”，能自动找出贴错名字的样本，不靠肉眼一张看。
    """
    det = TencentGridDetector()
    with open(os.path.join(TILES, style, "provenance.json"), encoding="utf-8") as fp:
        prov = json.load(fp)            # key -> 源文件名
    inst = []
    for (lbl, st, _btn, plain, _gb, _gp), key in zip(det._cores, det._core_keys):
        if st == style:
            inst.append((lbl.split("#")[0], key, prov.get(key, "?"), plain))
    ref = [x for x in inst if x[2].startswith("b1_")]
    if not ref:
        print(f"{style}: 没有 b1 钉过的样本可作参照，无法自查")
        return 0
    ref_by_lab = collections.defaultdict(list)
    for lab, _k, _f, c in ref:
        ref_by_lab[lab].append(c)
    bad, unver, weak = [], [], []
    for lab, key, fn, c in inst:
        if fn.startswith("b1_"):
            continue
        sc = {l: max(float(cv2.matchTemplate(c, r, cv2.TM_CCOEFF_NORMED).max())
                     for r in rs) for l, rs in ref_by_lab.items()}
        if lab not in sc:
            unver.append((fn, lab))
            continue
        best = max(sc, key=sc.get)
        # 必须带置信下限才能叫“贴错名字”：裁片本身坏了（跨两枚牌、只切到半枚）时，
        # 它与任何参照的 NCC 都只有 0.1~0.4，此时“最像 X”这句话没有信息量，报出来
        # 只会把人引向“改名”而不是“丢掉”。实测 shushan legacy 里就有 10 张这种。
        if best != lab and sc[best] >= 0.80:
            bad.append((sc[best] - sc[lab], fn, lab, best, sc[best], sc[lab]))
        elif sc[best] < 0.80:
            weak.append((fn, lab, sc[best]))
    print(f"=== {style}：legacy/手工样本身份自查（参照集 {len(ref)} 张 b1 样本）===")
    for gap, fn, lab, best, sb, sl in sorted(bad, reverse=True):
        print(f"  ✗ {fn} 标 {lab} 但最像 {best}（{sb:.3f} vs 同类 {sl:.3f}，差 {gap:+.3f}）")
    for fn, lab in unver:
        print(f"  ? {fn} 标 {lab}：b1 参照集里没有该类，无法校（保留但不可信）")
    for fn, lab, sb in sorted(weak, key=lambda x: -x[2]):
        print(f"  ✗ 残缺 {fn} 标 {lab}：与最像的参照也只有 {sb:.3f}（<0.80）"
              f"——不是名字错，是裁片本身坏了，该丢不是该改名")
    print(f"  合计：{len(bad)} 张贴错名字，{len(weak)} 张残缺，{len(unver)} 张无法校，"
          f"共扫 {sum(1 for _l, _k, f, _c in inst if not f.startswith('b1_'))} 张非 b1 样本")
    return 1 if (bad or weak) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", nargs=2, metavar=("STYLE", "FRAME"), default=None)
    ap.add_argument("--gt", default=GT_PATH)
    ap.add_argument("--slots", default=None, help="只看指定位序，逗号分隔（1 基）")
    ap.add_argument("--lofo", action="store_true")
    ap.add_argument("--style", default=None, help="--pairs 要扫的平台")
    ap.add_argument("--pairs", action="store_true")
    ap.add_argument("--dups", action="store_true", help="扫跨标签近重复模板（库卫生门）")
    ap.add_argument("--xcheck", action="store_true",
                    help="用 b1 人工样本校 legacy 模板的名字（需 --style）")
    ap.add_argument("--thr", type=float, default=0.95)
    a = ap.parse_args()
    if a.xcheck:
        if not a.style:
            ap.error("--xcheck 需要 --style <平台>")
        return xcheck(a.style)
    if a.frame:
        slots = {int(s) for s in a.slots.split(",")} if a.slots else None
        return frame_report(a.frame[0], int(a.frame[1]), a.lofo, slots, a.gt)
    if a.dups:
        return 1 if dups(a.thr, {a.style} if a.style else None) else 0
    if a.pairs:
        if not a.style:
            ap.error("--pairs 需要 --style <平台>")
        return pairs(a.style)
    ap.error("要么 --frame <style> <frame>，要么 --pairs --style <平台>，"
             "要么 --dups，要么 --xcheck --style <平台>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
