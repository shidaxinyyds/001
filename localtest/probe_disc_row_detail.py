# -*- coding: utf-8 -*-
"""量定缺盘几何通路：它在真定缺页与「局中误报帧」上分别看到了什么。

A7 的主因已定位到识别层：`is_dingque_phase` 在途游的局中帧（r26、r57，牌河都已有
弃牌）上返回 True。状态机上试过的三条改法全部失败（详见
`localtest/probe_dingque_stability.py` 头部），因为它们都在用逻辑修补一个感知错误。

这一轮不再猜，直接把几何通路拆开打：
  `_find_phase_discs`  → 找到哪些圆盘（位置/尺寸/色相）
  `_phase_disc_row`    → 是否配成「一行三个」
  `_phase_disc_row_in_band` → 是否落在定缺带内

只要看到误报帧在哪一步通过，就知道该收哪一条。

正反例（全部已在仓库，不需要新素材）：
  正例 6 张  shots_phase_fix/dq_*.jpg
  反例 3 张  shots_tuyou/r26_*.jpg、r57_*.jpg、shots_tuyou_select/s4_after_swap_cd04.jpg

用法: py -3.10 -X utf8 localtest/probe_disc_row_detail.py
"""
from __future__ import annotations

import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from recognition.tencent_grid_detector import (  # noqa: E402
    _find_phase_discs, _phase_disc_row, _phase_disc_row_in_band,
    _PHASE_DISC_TRIPLE_GAP, TencentGridDetector,
)

POS = [("shots_phase_fix/" + n, "定缺页") for n in (
    "dq_jj_01.jpg", "dq_shushan_01.jpg", "dq_shushan_02.jpg",
    "dq_tencent_01.jpg", "dq_tuyou_01.jpg", "dq_zj_01.jpg")]
NEG = [("shots_tuyou/r26_4cda06de.jpg", "局中(误报)"),
       ("shots_tuyou/r57_ce0dc83c.jpg", "局中(误报)"),
       ("shots_tuyou_select/s4_after_swap_cd04.jpg", "换牌后局中(误报)")]

det = TencentGridDetector()
print(f"{'帧':46s} {'类别':14s} {'盘数':>4s} {'成行':>5s} {'在带内':>6s} "
      f"{'is_dingque_phase':>17s}  盘明细")

for rel, kind in POS + NEG:
    p = os.path.join(HERE, rel)
    img = cv2.imread(p) if os.path.exists(p) else None
    if img is None:
        print(f"{rel:46s} {kind:14s}  缺素材")
        continue
    discs = _find_phase_discs(img)
    # 用真口径：与 is_dingque_phase 自己调用时同一套参数（自己编一套就测不到真行为）。
    row = _phase_disc_row(discs, _PHASE_DISC_TRIPLE_GAP, 0.035)
    in_band = bool(row) and _phase_disc_row_in_band(row)
    final = bool(det.is_dingque_phase(img))
    # 把盘的全部字段打一次：上一版猜键名（'r'）导致半径全显 0，看不出尺寸差异。
    keys = sorted({k for d in (row or discs)[:3] for k in d})
    detail = "  ".join(
        "|".join(f"{k}={d.get(k)}" if not isinstance(d.get(k), float)
                 else f"{k}={d.get(k):.3f}" for k in keys)
        for d in (row or discs)[:3])
    n_row = len(row or [])
    mark = ""
    if kind == "定缺页" and not final:
        mark = "  ✗ 漏判"
    elif kind != "定缺页" and final:
        mark = "  ✗ 误报"
    print(f"[{mark}] {rel}")
    print(f"{rel:46s} {kind:14s} {len(discs):4d} {str(bool(row)):>5s} "
          f"{str(in_band):>6s} {str(final):>17s}  成行{n_row}")
    print(f"{'':46s} {'':14s} 字段={keys}")
    for d in (row or discs)[:3]:
        print(f"{'':48s} " + "  ".join(
            f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
            for k, v in sorted(d.items())))

print("\n看「成行/在带内」两列：误报帧若两项都 True，说明三个盘的几何确实成立，")
print("要收的就不是几何，而是「这屏是不是定缺页」的另一条必要证据（如方位盘共存）。")

# ⚠ 实测结论（本轮量完）：「收紧几何」这条路不成立，不要再试：
#
#   r26 局中误报：三盘 x=0.633/0.756/0.869 → 行中心 0.751（偏右，是头像与金豆那排）
#   r57 局中误报：三盘 x=0.397/0.501/…      → 行中心 ≈0.50，与真定缺页一致
#   真定缺页：  jj 0.498 / 腾讯 0.505 / 途游 0.385
#
#   拿「行中心靠近中轴」能挡住 r26，但 **r57 挡不住**：它的三盘 x 与宽度
#   （w=0.061/0.069）与真定缺页（w=0.041~0.081）完全同量级、同位置。
#   面积也不能用：真途游页三盘里有一个 area=1471，比误报帧的 3312 还小。
#
#   即：途游局中屏上确实存在三个居中、等大、异色的圆形物（对手头像那一排）。
#   几何/颜色/尺寸三个维度都分不开，要分只能靠新证据：
#     A) 定缺提示文字带（「请选择定缺的花色」一类）—— 需中文字模/OCR，现在没有；
#     B) 可靠的「新一局」信号 —— 因为「命中阶段即清池」本身是对的（不清池则新一局
#        会带着上一局牌河），缺的是区分「新一局开局」与「局中误判」的依据。
#   状态机上的三种改法均已实测失败（见 probe_dingque_stability.py 头部），不要再走。
