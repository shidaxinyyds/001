# -*- coding: utf-8 -*-
"""跨平台阶段 UI 布局探针：把「整屏里的色盘行 / 金色按钮」全部量出来，不含任何
既有窗口假设 —— 用来给非腾讯平台的换三张/定缺判据标定，而不是再抄一组魔数。

素材两组：
  A. localtest/shots_multi/  20 帧真机（jj/途游/蜀山/雀神/指尖/腾讯），阶段来自人眼读屏
  B. localtest/shots/ + gt/shots.json  37 帧腾讯已人工校验 GT（含 9 定缺 / 9 换三张 / 6 选牌）

输出每帧：
  RINGS  合法「圆盘」候选（红/绿/金/蓝四色，尺寸按屏宽归一）→ 以及最优「共线横向串」
  GOLDS  金色连通域候选（整屏，量形状三指标）
判正例/反例靠人工比对这些数值，不在此脚本里下结论。

用法: py -3.10 localtest/probe_phase_layout.py [--frames multi|gt|all] [--top 6]
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MULTI = os.path.join(REPO, "localtest", "shots_multi")
GT_DIR = os.path.join(REPO, "localtest", "shots")
GT_JSON = os.path.join(REPO, "localtest", "gt", "shots.json")

# 人眼读屏得到的阶段事实（只写屏上有游戏原文的帧；面板自述一律不算证据）
TRUTH = {
    "jj_swap_01.jpg": "swap",        # 「选择3张同花色手牌」+ 金色「换 牌」
    "jj_dingque_01.jpg": "dingque",  # 「请选择定缺的花色（无此花色才能胡牌）」+三盘
    "shushan_dingque_01.jpg": "dingque",  # 「选择定缺花色,没有该花色才能胡」+三盘
    "tuyou_dingque_01.jpg": "dingque",    # 「定缺中...」「请选择一种不要的花色」+三盘
    "tuyou_pick_01.jpg": "swap",          # 「选牌中.」「选择以下任意3张手牌」+金色「确定(2)」
}

HUE_RANGES = {
    "red": ((8, 100, 100), (170, 255, 255)),   # 0-10 或 170-180
    "green": ((35, 80, 80), (85, 255, 255)),
    "gold": ((8, 75, 90), (35, 255, 255)),
    "blue": ((95, 100, 100), (130, 255, 255)),
}


def mask_of(hsv, name):
    lo, hi = HUE_RANGES[name]
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    if name == "red":
        return (((h <= lo[0]) | (h >= hi[0])) & (s >= lo[1]) & (v >= lo[2])).astype(np.uint8)
    return ((h >= lo[0]) & (h <= hi[0]) & (s >= lo[1]) & (v >= lo[2])).astype(np.uint8)


def find_rings(image, w_lo=0.030, w_hi=0.130):
    """整屏找「圆盘」候选：单色、近圆、外接框边长 = 屏宽的 w_lo~w_hi。"""
    ih, iw = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    out = []
    for hue in ("red", "green", "gold", "blue"):
        m = mask_of(hsv, hue) * 255
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if area < 300:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            if bw <= 0 or bh <= 0:
                continue
            if not (iw * w_lo <= bw <= iw * w_hi and iw * w_lo <= bh <= iw * w_hi):
                continue
            if not (0.65 <= bw / float(bh) <= 1.45):
                continue
            hull = cv2.convexHull(c)
            if cv2.contourArea(hull) / float(bw * bh) < 0.58:
                continue
            # 归一化必须把「像素偏移 + 半宽」整体除以屏宽。旧写法
            # `x + bw / 2.0 / iw` 等于 x + (bw/2)/iw，量出来是「像素坐标 + 一个
            # 小数」，后面按 0~1 比间距/共线全部落空（探针一开始就 ROW(0)）。
            out.append({"hue": hue, "x": (x + bw / 2.0) / iw, "y": (y + bh / 2.0) / ih,
                        "w": bw / float(iw), "area": area})
    return out


def best_row(rings, gap_lo=0.02, gap_hi=0.22, dy_tol=0.05):
    """在圆盘候选里找最长的一条「横向共线、相邻间距合理、颜色互不相同」的串。"""
    best = None
    rings = sorted(rings, key=lambda r: r["x"])
    n = len(rings)

    def walk(cur, used, start):
        nonlocal best
        if len(cur) >= 2 and (best is None or len(cur) > len(best)):
            best = list(cur)
        if len(cur) >= 4:
            return
        for j in range(start + 1, n):
            r = rings[j]
            if r["hue"] in used:
                continue
            p = cur[-1]
            gap = r["x"] - p["x"]
            if not (gap_lo <= gap <= gap_hi):
                continue
            if abs(r["y"] - p["y"]) > dy_tol:
                continue
            walk(cur + [r], used | {r["hue"]}, j)

    for i in range(n):
        walk([rings[i]], {rings[i]["hue"]}, i)
    return best or []


def dedupe(rings, tol=0.02):
    """同色、横向距离 < tol 的盘只留面积最大者（牌面内光晕会裂成多个轮廓）。"""
    out = []
    for r in sorted(rings, key=lambda t: -t["area"]):
        if any(r["hue"] == o["hue"] and abs(r["x"] - o["x"]) < tol for o in out):
            continue
        out.append(r)
    return sorted(out, key=lambda t: t["x"])


def find_golds(image):
    """整屏找金色连通域，量「占比/宽高比/填充率」三指标（换牌按钮的形状指纹）。"""
    ih, iw = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    gold = ((hsv[:, :, 0] >= 14) & (hsv[:, :, 0] <= 35)
            & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 150)).astype(np.uint8) * 255
    n_lab, lab, st, cen = cv2.connectedComponentsWithStats(gold, 8)
    out = []
    for i in range(1, n_lab):
        area = int(st[i, cv2.CC_STAT_AREA])
        bw = int(st[i, cv2.CC_STAT_WIDTH])
        bh = int(st[i, cv2.CC_STAT_HEIGHT])
        if area < 900 or bw < 20 or bh < 12:
            continue
        if bw >= iw * 0.5 or bh >= ih * 0.5:
            continue
        out.append({"x": cen[i][0] / float(iw), "y": cen[i][1] / float(ih),
                    "ar": bw / float(bh), "fill": area / float(bw * bh),
                    "himg": bh / float(ih), "area": area})
    return sorted(out, key=lambda d: -d["area"])


def fmt_ring(r):
    return f"{r['hue'][:1]}@({r['x']:.3f},{r['y']:.3f},w{r['w']:.3f})"


def load_frames(which):
    fr = []
    if which in ("multi", "all"):
        for n in sorted(os.listdir(MULTI)):
            if n.lower().endswith(".jpg"):
                fr.append((n, os.path.join(MULTI, n), TRUTH.get(n, "play")))
    if which in ("gt", "all"):
        gt = json.load(open(GT_JSON, encoding="utf-8"))["shots"]
        for e in gt:
            fr.append((e["file"], os.path.join(GT_DIR, e["file"]),
                       e.get("status") or "ok"))
    return fr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default="multi", choices=["multi", "gt", "all"])
    ap.add_argument("--top", type=int, default=4)
    args = ap.parse_args()

    for name, path, truth in load_frames(args.frames):
        img = cv2.imread(path)
        if img is None:
            print(f"!! 读不到 {path}")
            continue
        rings = dedupe(find_rings(img))
        row = best_row(rings)
        golds = find_golds(img)
        print(f"\n=== {name}  truth={truth}  rings={len(rings)}")
        for r in rings:
            print("    RING " + fmt_ring(r))
        print(f"    ROW({len(row)}): " + "  ".join(fmt_ring(r) for r in row))
        for g in golds[:args.top]:
            print(f"    GOLD y={g['y']:.3f} x={g['x']:.3f} ar={g['ar']:.2f} "
                  f"fill={g['fill']:.3f} himg={g['himg']:.3f} area={g['area']}")


if __name__ == "__main__":
    main()
