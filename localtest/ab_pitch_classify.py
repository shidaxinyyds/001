# -*- coding: utf-8 -*-
"""消融实验：手牌标签到底是谁给的——YOLO 还是「等距节距重切 + 模板分类」覆盖层。

生产链路（recognition/yolo_detector.py:322-356）在 YOLO 出框后会：
  1) 假设手牌等距，用首尾框算 pitch 重新切出 standing_count 个 patch；
  2) 用 TencentGridDetector.classify_tile（腾讯牌风的模板分类器）判每个 patch；
  3) ref_conf >= 0.40 时**无条件覆盖** YOLO 自己的 label。

雀神/途游的立牌在换三张/定缺/选牌阶段被抬高且不等距，等距网格会错位切牌，
于是模板分类拿到的是横跨两张牌的裁片——这可能才是真正的误识别源，
而不是 YOLO 没见过这些牌风。本脚本对照两条链路，不改动生产代码。

用法: py -3.10 localtest\ab_pitch_classify.py [idx ...]
"""
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")

PLATFORM_OF = {i: "weile" for i in range(1, 13)}
PLATFORM_OF[13] = "shushan"
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"

# 人工核对过的 GT（空格分隔，左→右）。其余帧只做两链路对照，暂不打分。
GT = {
    14: "7m 7m 7m 8m 2p 2p 2p 6p 6p 8p 8p 8p 8s 7s",
    15: "2p 2p 2p 6p 6p 8p 8p 8p 1s 6s 7s 8s 8s",
    16: "qs 2p 2p 3p 3p 4p 5p 5p 6p 8p 6s",
    17: "1m 6m 7m qs 2p 2p 3p 3p 4p 5p 5p 6p 8p 6s",
    18: "5m 2p 3p 3p 5p 5p 6p 6p 7p 7p 9s",
    # 途游：`?` = 被相邻牌/手指/UI 遮挡或条子根数数不准，该位不计分。
    # 34/36 的 GT 于 2026-10 按**整条手牌带**重标：之前是从引擎裁片拼图上读的，
    # 而这两帧 YOLO 各漏检 2 张（物理 14 张只出 12 框），裁片本身就只有 12 格，
    # 于是漏检被静默吸收成“GT 少两张”，指标含义失真。现在按物理张数写全。
    34: "7z 3m 4m 4m 4m 5m 5m 5m 6m 6m 7m 3p 1s 1m",
    36: "7z 1p 2p 3p 5p 5p 5p 8p 4s 7s 7s 5m 8m 9s",
    40: "? 2p 2p 3p 3p 3p 4p 4p 4p 6p 6p 8p 9s",
    # JJ：五帧均为对局中的立牌正视图（23/24 是换三张的 3D 斜牌，不用于打分）。
    28: "7z 3s 4s 5s 7s 2s 4p 5p 7p 8p 3m 4m 8m",
    29: "7z 4s 5s 7s 2s 9s 4p 5p 6p 7p 7p 8p 8m",
    30: "1m 3m 4m 4m 5m 6m 7m 7m 2p 4p 8p 5s 7s",
    31: "1s 2s 3s 3s 4s 4s 5s 5s 2m 5m 6m 9m",
    32: "1s 2s 3s 3s 4s 4s 5s 5s 5p 6p 6p 9p 5m",
    # 途游 33：末位裁片拍到的是牌河的 3D 斜牌（不是手牌），写 `?` 不计分。
    33: "7z 1p 1p 3p 5p 7p 8p 3s 7s 2s 9s ?",
    # 微乐：这两帧带完整字牌（東/西/發），而 classify_tile 默认候选集只认 7z，
    # 字牌位现在是「结构性必错」。先登记下来，作为将来放开词表的基线。
    6: "2m 5m 6m 8m 6s 6s 1p 5p 9p 1z 3z 7z 6z 5s",
    7: "4m 8m 4s 2s 9s 9s 3p 6p 7p 9p 2z 2z 6z",
    # 微乐另补 6 帧对局帧。均按**整条手牌带**读（strip_montage.py），不读引擎裁片；
    # 左侧斜放的牌河/带箭头的他家弃牌不计入。
    # 错法集中在萬子汉字（4m↔5m、7m→3m）与 8p→2p，而这几帧**一张风牌都没有**：
    # 说明上一轮把微乐归因成“字牌问题”是被 06/07 这两个恰好含字牌的样本带偏了，
    # 真正的主因是微乐牌风根本不在 bank 里。
    1: "7z 7z 6m 6m 6s ? 8s 9s 2p 3p",
    2: "7z 4m 5m 6m 7m 4s 4s 1p 2p 3p 7p 8p 9p",
    3: "7z 7z 8m 8m 3s 3s 1p 3p 5p 6p 8p 8p 8p",
    5: "2m 9m 1p 2p 4p 4p 5p 5p 6p 7p 8p 9p 9p",
    8: "5m 6m 7m 1s 3s 3s 4s 6s 6s 6s 7s 8s 9s 3s",
    10: "3s 6s 6s 1p 2p 4p 5p 5p 6p 7p",
    # 途游再补 4 帧对局帧（同样按整条手牌带读）。帧 26 带 1s/4s/7s/9s、帧 38 带
    # 3s/7s/9s，正是途游 bank 最缺的条子类；帧 35 带 1m~7m 全套大写萬子。
    # 帧 37 是定缺界面（/// 遮罩 + 副露牌混在上排）、帧 41 右侧有 3 张被换出的牌
    # 混进条带——两者均不属“立牌正视图”输入域，不登记 GT 也不用于收割
    # （收进去会让模板库学到斜牌投影，反而拉低正视立牌的匹配分）。
    26: "7z 2p 2p 2p 3p 3p 3p 4p 4p 4p 1s 4s 7s 9s",
    35: "7z 1m 2m 3m 4m 4m 4m 5m 5m 5m 6m 6m 7m 7m",
    38: "7z 1p 2p 2p 3p 5p 5p 5p 7p 8p 3s 7s 7s 9s",
    39: "7z 3m 4m 4m 4m 5m 5m 5m 6m 6m 7m 3p 1s",
}

# 模块级劫持 classify_tile：不管引擎内部重建多少个 detector 实例都会生效，
# 且能统计它到底被调了几次（比只置 _phase_helper=None 可靠）。
from recognition import tencent_grid_detector as TGD  # noqa: E402

_ORIG_CLASSIFY = TGD.TencentGridDetector.classify_tile
CALLS = {"n": 0}


def _stub_classify(self, crop, avail=None, styles=None):
    CALLS["n"] += 1
    return "", 0.0  # ref_conf < 0.40 → 生产代码会回退到 YOLO 自己的 label


def run_img(img, idx, variant):
    """对已解码的帧跑一次链路。拆出来是为了让扫参类脚本（sweep_gate）
    能复用解码结果：同一帧的几何与参数无关，重复解码纯属浪费。"""
    CALLS["n"] = 0
    if variant == "no_template":
        TGD.TencentGridDetector.classify_tile = _stub_classify
    else:
        TGD.TencentGridDetector.classify_tile = _ORIG_CLASSIFY

    eng = Engine()
    eng.set_platform(PLATFORM_OF.get(idx, "generic"))
    d = None
    for _ in range(4):
        d = json.loads(eng.process(img).result)
        if (d.get("count") or 0) > 0 and d.get("status") not in ("waiting", "no_tiles"):
            break
    TGD.TencentGridDetector.classify_tile = _ORIG_CLASSIFY
    tiles = sorted([t for t in (d.get("tiles") or []) if len(t) > 5 and t[5] == "hand"],
                   key=lambda t: t[0])
    return [t[4] for t in tiles if t[4]], CALLS["n"], d


def run(idx, variant):
    f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
    if not f:
        return None
    img = cv2.imread(os.path.join(SHOTS, f))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return run_img(img, idx, variant)


def _lcs(a, b):
    """保序最长公共匹配长度（滚动数组）。"""
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            cur[j] = prev[j - 1] + 1 if a[i - 1] == b[j - 1] else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def score_detail(lbls, gt):
    """保序匹配计分，返回 {hit, n, missed, extra}；无法评分时返回 None。

    为什么不用逐位比对（之前的口径）：逐位会把一次漏检**株连成整行错位**。
    实测途游帧 34：物理 14 张、引擎 13 槽，漏 1 张之后引擎尾部的 3p/1s/1m
    明明认对了，却被拿去和 7m/3p/1s 比而全判错——1 张漏检被算成 7 位损失，
    于是“补槽/定位”这类改动的真实收益全部被尺子吞掉，还会把“槽内容一点没变”
    误判成回归（已经为此追打过一次不存在的 bug）。

    为什么分母取 max(物理, 引擎) 而不是物理张数：只按物理张数的话，
    把牌河当手牌多检出来的牌不扣分（实测微乐帧 10 会虚高到 9/10=90%，
    而它其实多算了 2 张）。取 max 让**漏检与多检各自扣 1、互不株连**。

    代价（必须知道）：保序匹配不惩罚“牌认对但顺序乱”。手牌是排序展示的，
    顺序错一般意味着定位错，所以 missed/extra 与 detected_band 要另看一眼，
    不能只看一个百分比。
    """
    if gt is None or lbls is None:
        return None
    g = [x for x in gt.split() if x != "?"]
    if not g and not lbls:
        return None
    hit = _lcs(list(lbls), g)
    n = max(len(g), len(lbls))
    return {"hit": hit, "n": n, "missed": max(0, len(g) - len(lbls)),
            "extra": max(0, len(lbls) - len(g))}


def score(lbls, gt):
    """保序命中率，形如 "10/14"；gt 为空格分隔的 MPSZ 串，`?` 位不计分。"""
    d = score_detail(lbls, gt)
    return "-" if d is None else f"{d['hit']}/{d['n']}"


def main():
    args = [int(a) for a in sys.argv[1:] if a.isdigit()]
    idxs = args or sorted(int(f.split("_")[1][:2]) for f in os.listdir(SHOTS)
                          if f.lower().endswith(".jpg"))
    print(f"{'idx':>3} {'plat':11} {'生产(节距+模板)':>20}  {'纯YOLO':>20}  模板调用  GT命中")
    diff = 0
    for idx in idxs:
        p, _, _ = run(idx, "prod")
        y, ncalls, _ = run(idx, "no_template")
        ph = " ".join(p) if p else "-"
        yh = " ".join(y) if y else "-"
        if ph != yh:
            diff += 1
        gt = GT.get(idx)
        sc = f"{score(p, gt)} / {score(y, gt)}" if gt else ""
        star = "  ≠" if ph != yh else ""
        print(f"{idx:3d} {PLATFORM_OF.get(idx,'-'):11} {ph:>20}  {yh:>20}  {ncalls:5}  "
              f"{sc}{star}")
    print(f"\n两链路标签不同的帧数: {diff}/{len(idxs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
