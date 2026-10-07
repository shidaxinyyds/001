# -*- coding: utf-8 -*-
"""从 GT json 收割真机牌面样本（public/1 起的后续批次统一走这条）。

与 harvest_style_tiles.py 的分工：那边的 GT 是**物理张数**（人工看整帧数出来的），
所以检出数可以少于 GT，需要 _align_by_step/_align_by_agreement 补空洞；这边的 GT
标签本身就是逐枚裁片读出来的（make_tile_sheets.py 一帧一表），检出数与标签数
**必须相等**，因此不做任何对齐，只做一条硬门：

    len(检出) != len(GT) -> 整帧拒绝收割

这条门是必要的，不是保守：GT 与 rect 是按序号一一对应的，一旦检测器行为变了
（改几何门、换素材目录、重命名文件），序号对应就断了，恒等对齐会把 A 牌的模板
烧成 B 牌的标签——模板库被错标签污染的代价是**整手消失**（见
build_platform_bank.py 里 drop_outliers 的失败记录），比少收几帧严重得多。
出现拒绝时的正确修法不是放开这个门，而是重跑 make_tile_sheets.py 重新读一遍图。

GT 里写 `?` 表示"这一格人眼读不准"：占位保持对齐，但不落盘。

用法: py -3.10 -X utf8 localtest/harvest_from_gt.py localtest/gt/shots_b1.json [style] [--adopt]
输出: localtest/tiles/<style>/<批次>_f<帧>_<列>_<label>.png
      build/harvest_<style>.txt（覆盖度报告；报告由脚本自己按 UTF-8 落文件，
      因为 PowerShell 5.1 的 `>` 会写成 UTF-16、GBK 控制台又会吞掉中文结论行）
"""
import json
import os
import re
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from style_harvest import face_align  # noqa: E402

ALL_KINDS = [f"{n}{s}" for s in "mps" for n in range(1, 10)] + \
            [f"{n}z" for n in range(1, 8)]


def _emit(buf, line):
    """同时上屏与落盘：line 先攒进 buf，由调用方统一写 UTF-8 文件。"""
    print(line)
    buf.append(line)


def ns_of(gt_path):
    """GT 文件名 -> 裁片命名空间前缀（shots_b1.json -> "b1_"）。

    为什么必须有：同一个 tiles/<style>/ 目录里可以躺着多个收割来源的样本
    （harvest_style_tiles 按它自己的帧号、harvest_from_gt 按 GT 的帧号），两边都
    叫 `f02_00_5m.png` 时“帧 02”指的是两张完全不同的截图。轻则 stale 清理误删
    别人的样本，重则 provenance 把别家的帧号当本帧剔掉。前缀让每个来源只管自己
    那批文件（旧来源没有前缀 = 它自己的命名空间）。
    """
    m = re.search(r"shots_(\w+?)\.json$", os.path.basename(gt_path))
    return (m.group(1) + "_") if m else ""


def harvest(gt_path, only_style=None, log_path=None, adopt=False):
    with open(gt_path, encoding="utf-8") as fp:
        shots = json.load(fp)["shots"]
    ns = ns_of(gt_path)
    det = TencentGridDetector()
    log = []
    stat = {}
    for e in shots:
        style = e["style"]
        if only_style and style != only_style:
            continue
        labs = e["hand"]
        src = e.get("src") or os.path.join("public", "0")
        img = cv2.imread(os.path.join(REPO, src, e["file"]))
        if img is None:
            _emit(log, f"[{e['frame']:02d}] 读不到 {src}/{e['file']} -> 跳过")
            continue
        dets = sorted(det.detect_hand_strip(img) or [], key=lambda d: d[0][0])
        s = stat.setdefault(style, {"out": os.path.join(HERE, "tiles", style),
                                    "seen": {}, "n": 0, "skip_q": 0, "rej": []})
        os.makedirs(s["out"], exist_ok=True)
        if len(dets) != len(labs):
            s["rej"].append((e["frame"], len(dets), len(labs)))
            _emit(log, f"[{e['frame']:02d}] 检出 {len(dets)} != GT {len(labs)} -> 整帧拒绝")
            continue
        hh, ww = img.shape[:2]
        for col, (d, lab) in enumerate(zip(dets, labs)):
            if lab == "?":
                s["skip_q"] += 1
                continue
            x, y, bw, bh = d[0]          # dets 元素是 (rect, label, score)
            crop = img[max(0, y):min(hh, y + bh), max(0, x):min(ww, x + bw)]
            if crop.size == 0:
                continue
            face = face_align(crop)
            p = os.path.join(s["out"], f"{ns}f{e['frame']:02d}_{col:02d}_{lab}.png")
            cv2.imwrite(p, face)
            s["seen"].setdefault(lab, []).append(os.path.basename(p))
            s["n"] += 1
    for style, s in stat.items():
        # 清掉本轮没再产出的旧裁片：帧集一变，同名文件可能带着旧标签留下来被
        # build_platform_bank 收进库（与 harvest_style_tiles.py 同一纪律）。
        # 只清**本批次命名空间**里的文件：同一个 tiles/<style>/ 可以共存其它
        # 收割来源（如 legacy_7m.png、或 harvest_style_tiles 落的无前缀裁片），
        # 它们不由本脚本维护，误删就是默默弄丢生产模板。
        out = s["out"]
        if not os.path.isdir(out):
            continue
        keep = {n for files in s["seen"].values() for n in files}
        if adopt and ns:
            # 一次性收编：把本脚本旧版落的无前缀 `f<帧>_` 裁片改名为带批次前缀。
            # 只在“确定该目录里无前缀文件全部来自本 GT”时用（否则会把别家样本
            # 收编到本批次名下，LOFO 剔帧时会连别人的模板一起剔掉）。
            for f in sorted(os.listdir(out)):
                if not re.match(r"^f\d+_\d+_.*\.png$", f):
                    continue
                src_p, tgt = os.path.join(out, f), os.path.join(out, ns + f)
                if os.path.exists(tgt):
                    # 目标已存在 = 本轮按新命名重新落了同一格。只有字节完全相同
                    # 才能当重复品删；不同则说明两个命名空间不是同一批截图，
                    # 静默删就是弄丢别人的样本，必须停下来。
                    if open(src_p, "rb").read() != open(tgt, "rb").read():
                        raise SystemExit(
                            f"收编冲突：{f} 与 {ns + f} 内容不同——这两个命名空间不是"
                            f"同一批截图，不要用 --adopt（否则会把别家样本当重复品删掉）")
                    os.remove(src_p)
                else:
                    os.rename(src_p, tgt)
                keep.add(ns + f)
        mine = re.compile(rf"^{re.escape(ns)}f\d+_\d+_.*\.png$")
        stale = [f for f in sorted(os.listdir(out))
                 if mine.match(f) and f not in keep]
        for f in stale:
            os.remove(os.path.join(s["out"], f))
        if stale:
            _emit(log, f"  清理旧裁片 {len(stale)} 张：{' '.join(stale[:6])}"
                        f"{' ...' if len(stale) > 6 else ''}")
        _emit(log, f"\n=== {style}: 落盘 {s['n']} 张（读不准跳过 {s['skip_q']} 格，"
                   f"整帧拒绝 {len(s['rej'])} 帧），覆盖 {len(s['seen'])}/{len(ALL_KINDS)} 类 ===")
        for lab in sorted(s["seen"]):
            _emit(log, f"  {lab:3} x{len(s['seen'][lab]):2d}")
        miss = [c for c in ALL_KINDS if c not in s["seen"]]
        _emit(log, f"  缺类({len(miss)}): {' '.join(miss)}")
    if log_path:
        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(log) + "\n")
    return 0


def main():
    argv = sys.argv[1:]
    adopt = "--adopt" in argv
    args = [x for x in argv if not x.startswith("--")]
    if len(args) < 1:
        print("用法: harvest_from_gt.py <gt.json> [style] [--adopt]")
        return 2
    style = args[1] if len(args) > 1 else None
    log = os.path.join(REPO, "build", f"harvest_{style or 'all'}.txt")
    return harvest(args[0], style, log, adopt)


if __name__ == "__main__":
    sys.exit(main())
