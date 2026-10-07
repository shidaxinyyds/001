# -*- coding: utf-8 -*-
"""A/B：平台已声明时跳过「全库风格探针」，节距模型改查表 —— 输出必须逐帧一致。

背景（实测，build/cost_detail.txt）：探针每帧付 **1273 次全库 matchTemplate**
（594 张模板 × 2~3 枚代表牌，214~284ms/帧，占总耗时 21~28%），而它产出的信息只有两条：
  1) `style == "tencent"` → 用重叠排布那套节距（`bw / (0.0547*iw)`）；否则用相邻排布（`bw / face_w`）；
  2) 分类候选集收窄到探针前两家 —— 但**声明平台时这条早已被 b6b 的守卫抵消**：
     `_resolve_styles` 不许把用户声明平台的专属 bank 挤出去，实测两侧候选集相同
     （见 build/panel_styles.txt：active_styles 与 resolved 逐帧一致）。
也就是说：用户已经说了是哪家平台，探针再猜一遍「像哪家的牌风」就只剩「猜排布方式」这一件事，
而排布方式是平台的物理属性（腾讯重叠、其余相邻），**查表比猜更准也更便宜**。

本脚本分三步，全部用数据说话：
  第一步 逐帧记录探针真实胜出的风格与走了哪条节距分支（不许拍脑袋定表）；
  第二步 按平台取多数派生成查表，如实报出「少数派帧」（那些帧改查表就会变行为）；
  第三步 同进程交替 A/B：现行 vs 查表跳探针，逐帧比对网格输出的 (x, label, conf)
          序列，并给两边的墙钟时间。

判定：只有「差异帧=0」才允许动生产；有差异就如实报差异，不为了省时间放宽比较。

用法：py -3.10 -X utf8 localtest/ab_skip_probe.py [帧数上限]
输出：build/ab_skip_probe.txt
"""
from __future__ import annotations

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

from modes import available_set  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_new_material import STYLE_OF  # noqa: E402
from layer_cost import mode_for  # noqa: E402

GT = os.path.join(HERE, "gt", "shots_b1.json")
OUT = os.path.join(REPO, "build", "ab_skip_probe.txt")
ADJ = "adj"        # 相邻排布（牌面宽 = 节距）
TEC = "tec"        # 重叠排布（腾讯那套 0.0547*iw）


def frames(limit=0):
    with open(GT, encoding="utf-8") as fp:
        shots = [e for e in json.load(fp)["shots"] if e.get("verified")]
    out = []
    for e in shots:
        img = cv2.imread(os.path.join(REPO, e.get("src") or os.path.join("public", "1"), e["file"]))
        if img is None:
            continue
        hand = sorted({x for x in e["hand"] if x != "?"})
        platform = STYLE_OF.get(e["style"], "tencent")
        out.append((e["style"], e["frame"], platform,
                    mode_for(platform, hand)[0], img))
        if limit and len(out) >= limit:
            break
    return out


def run(det, fs, table=None):
    """跑一批帧，返回 (逐帧扁平输出, 墙钟秒)。

    `table=None` = 现行（真探针，扫全库）；给了表就把探针换成查表：
    返回 "tencent" 走重叠排布那档，返回一个库里没有的风格名走相邻排布那档
    （`_resolve_styles` 遇到不允许的会退回白名单，这正是我们要的「候选集=白名单」），
    返回 None 则与现行「探针不定」同形（四档并集）。
    """
    res, secs = [], 0.0
    orig = det._probe_style
    cur = {"style": None}

    def fake(crop, extra=None):
        det.last_probe_ranking = []          # 查表没有排名，第二名不参与
        b = table.get(cur["style"])
        if b == TEC:
            return "tencent"
        return "adj-only" if b == ADJ else None

    if table is not None:
        det._probe_style = fake
    try:
        for style, frame, platform, mode, img in fs:
            cur["style"] = style
            det.set_platform_styles(platform)
            det.set_mode_tiles(available_set(mode))
            det.last_probe_ranking = []
            t0 = time.perf_counter()
            rows = det.detect_all_rows(img, classify=True, allow_rotation=False)
            secs += (time.perf_counter() - t0)
            flat = [(int(r[0]), l, round(float(c), 4))
                    for row in rows for (r, l, c) in row]
            res.append(((style, frame), flat))
    finally:
        det._probe_style = orig
    return res, secs


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    fs = frames(limit)
    det = TencentGridDetector()

    # ---------- 第一步：探针在声明平台的前提下到底选了什么 ----------
    branch = collections.defaultdict(collections.Counter)
    winners = collections.defaultdict(collections.Counter)
    orig_probe = det._probe_style

    def spy(crop, extra=None):
        st = orig_probe(crop, extra)
        spy.last = st
        return st

    spy.last = None
    det._probe_style = spy
    for style, frame, platform, mode, img in fs:
        det.set_platform_styles(platform)
        det.set_mode_tiles(available_set(mode))
        det.detect_all_rows(img, classify=True, allow_rotation=False)
        w = spy.last
        winners[style][w] += 1
        branch[style][TEC if w == "tencent" else (ADJ if w else "both")] += 1
    det._probe_style = orig_probe

    lines = [f"帧数 {len(fs)}（夹具 {GT}），模板 {len(det._cores)} 张", "",
             "第一步 探针胜出风格（前提：平台已按 GT 声明）："]
    for s in sorted(winners):
        lines.append(f"  {s:9} 胜者 {dict(winners[s])}  分支 {dict(branch[s])}")

    # ---------- 第二步：查表（多数派），少数派帧如实列出 ----------
    table = {}
    for s in sorted(branch):
        cnt = branch[s]
        table[s] = cnt.most_common(1)[0][0]
    lines.append("")
    lines.append("第二步 查表结果（平台 -> 节距分支）与少数派帧：")
    for s in sorted(table):
        minority = {k: v for k, v in branch[s].items() if k != table[s]}
        lines.append(f"  {s:9} -> {table[s]:5}   少数派 {minority or '无'}")
    lines.append("  （表按实测多数派生成，不是拍脑袋：tencent 重叠、其余相邻）")

    # ---------- 第三步：同进程交替 A/B ----------
    a, ta = run(det, fs)
    b, tb = run(det, fs, table)

    diffs = []
    for (key, la), (_key2, lb) in zip(a, b):
        if [t[:2] for t in la] != [t[:2] for t in lb]:
            diffs.append((key, la, lb))
    conf_keys = [(key, la, lb) for (key, la), (_k, lb) in zip(a, b)
                 if [t[2] for t in la] != [t[2] for t in lb]]
    conf_diff = len(conf_keys)

    lines.append("")
    lines.append(f"第三步 A/B（同进程交替，{len(fs)} 帧）：")
    lines.append(f"  A 现行（每帧扫全库探针）  {ta:7.1f}s  帧均 {1000 * ta / len(fs):7.1f}ms")
    lines.append(f"  B 查表跳探针              {tb:7.1f}s  帧均 {1000 * tb / len(fs):7.1f}ms"
                 f"   省 {100.0 * (1 - tb / ta):.1f}%")
    lines.append(f"  标签/框序列差异帧：{len(diffs)} / {len(fs)}")
    lines.append(f"  置信度序列不同的帧：{conf_diff}（框与标签相同、分数不同也如实列出）")
    for key, la, lb in diffs[:10]:
        lines.append(f"    差异帧 {key[0]}#{key[1]:02d}：A={[t[1] for t in la]} "
                     f"B={[t[1] for t in lb]}")
    for key, la, lb in conf_keys[:10]:
        d = [(x[1], x[2], y[2]) for x, y in zip(la, lb) if x[2] != y[2]]
        lines.append(f"    仅分数不同帧 {key[0]}#{key[1]:02d}：{d[:6]}")

    lines.append("")
    lines.append("判定：差异帧=0 才允许把「声明平台跳探针」落进生产；否则如实写为不可行。")
    lines.append("适用范围（后来被样本外实测纠正）：本脚本**不剔模板**，量的是全库口径的等价性。"
                 "LOFO 样本外会有 1 格差异（蜀山#01 第 12 枚 4p->5p，方向是变好），"
                 "因为探针把分类候选收窄成本家+第二名，而查表给的是平台白名单。"
                 "别把这里的「差异 0」当样本外结论引用 —— 那条已由 "
                 "localtest/test_skip_probe_guard.py 第 3c 条钉成棘轮。")
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"报告：{OUT}")


if __name__ == "__main__":
    main()
