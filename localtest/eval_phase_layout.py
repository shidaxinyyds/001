# -*- coding: utf-8 -*-
"""阶段判据的跨平台评测台：把「定缺 / 换三张」两条视觉判据当成分类器打分。

素材 57 帧，真值来源两条，互不混淆：
  * localtest/gt/shots.json 里 verified=true 的 37 帧腾讯真机（status 字段是人工
    读屏钉过的：9 定缺 / 9 换三张 / 6 选牌 / 2 等待 / 11 局中）
  * localtest/shots_multi/ 的 20 帧（jj/途游/蜀山/雀神/指尖/腾讯），真值只认**屏幕
    上的游戏原文**（「请选择定缺的花色」「选择3张同花色手牌」「选牌中」），
    我们自己面板里写了什么一律不算证据 —— 面板是待测对象，不是标注来源。

报告：TP / FP / FN 逐帧列名，并把「漏」和「误」分开算，因为两者的修法相反。

用法: py -3.10 localtest/eval_phase_layout.py
"""
import contextlib
import io
import json
import os
import sys

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

MULTI = os.path.join(REPO, "localtest", "shots_multi")
GT_DIR = os.path.join(REPO, "localtest", "shots")
GT_JSON = os.path.join(REPO, "localtest", "gt", "shots.json")

# 人眼读屏得到的阶段真值（只写屏上有游戏原文的帧）
MULTI_TRUTH = {
    "jj_swap_01.jpg": "swap",            # 「选择3张同花色手牌」+ 金色「换 牌」
    "jj_dingque_01.jpg": "dingque",      # 「请选择定缺的花色（无此花色才能胡牌）」+三色盘
    "shushan_dingque_01.jpg": "dingque",  # 「选择定缺花色,没有该花色才能胡」+三色盘
    "tuyou_dingque_01.jpg": "dingque",   # 「定缺中...」「请选择一种不要的花色」+三色盘
    "tuyou_pick_01.jpg": "swap",         # 「选牌中.」「选择以下任意3张手牌」+金色「确定(2)」
    "tencent_pick_01.jpg": "swap",       # 金色「换牌」按钮在手牌行正上方
    "tencent_pick_02.jpg": "swap",
    "tencent_pick_03.jpg": "swap",
}


def load_frames():
    """返回 [(name, path, truth)]；truth ∈ {dingque, swap, pick, other}。"""
    out = []
    for n in sorted(os.listdir(MULTI)):
        if not n.lower().endswith(".jpg"):
            continue
        out.append((n, os.path.join(MULTI, n), MULTI_TRUTH.get(n, "other")))
    gt = json.load(open(GT_JSON, encoding="utf-8"))["shots"]
    for e in gt:
        if not e.get("verified"):
            continue
        st = e.get("status") or "other"
        out.append((e["file"], os.path.join(GT_DIR, e["file"]),
                    st if st in ("dingque", "swap", "pick") else "other"))
    return out


def main():
    det = TencentGridDetector()
    with contextlib.redirect_stdout(io.StringIO()):
        det.load() if hasattr(det, "load") else None
    rows = load_frames()
    stat = {"dq": [0, 0, 0], "sw": [0, 0, 0]}   # [TP, FP, FN]
    detail = {"dq_fp": [], "dq_fn": [], "sw_fp": [], "sw_fn": []}
    for name, path, truth in rows:
        img = cv2.imread(path)
        if img is None:
            print("!! 读不到", path)
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            dq = bool(det.is_dingque_phase(img))
            sw = bool(det.is_swap_phase(img))
        want_dq = truth == "dingque"
        want_sw = truth in ("swap",)
        if dq and want_dq:
            stat["dq"][0] += 1
        elif dq:
            stat["dq"][1] += 1
            detail["dq_fp"].append(f"{name}(truth={truth})")
        elif want_dq:
            stat["dq"][2] += 1
            detail["dq_fn"].append(name)
        if sw and want_sw:
            stat["sw"][0] += 1
        elif sw:
            stat["sw"][1] += 1
            detail["sw_fp"].append(f"{name}(truth={truth})")
        elif want_sw:
            stat["sw"][2] += 1
            detail["sw_fn"].append(name)

    print(f"{'判据':8s} {'TP':>4s} {'FP':>4s} {'FN':>4s}")
    for k, label in (("dq", "定缺"), ("sw", "换三张")):
        tp, fp, fn = stat[k]
        print(f"{label:8s} {tp:4d} {fp:4d} {fn:4d}")
    for k in ("dq_fp", "dq_fn", "sw_fp", "sw_fn"):
        if detail[k]:
            print(f"\n{k}:")
            for n in detail[k]:
                print("   ", n)
    print(f"\n共 {len(rows)} 帧")


if __name__ == "__main__":
    main()
