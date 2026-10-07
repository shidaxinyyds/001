# -*- coding: utf-8 -*-
"""量化「新增雀神 bank」对其它牌风的跨风格泄漏。

A/B 全量截图跑显示 weile/jj/tuyou 有 11 张牌标签变了，但没有 GT 就判不出方向。
本测试改用**已有干净标注的其它风格素材**做受控实验：同一张牌面，
分别在「只有 tencent+shushan」和「再加 queshen」两套库里判，
看标签往哪个方向漂——变对的算增益，变错的算泄漏。

素材（自带真值，无需再标注）：
  localtest/real_tiles/<label>.png         标准牌面
  localtest/tiles/duma520/<label>.png      duma 牌风（注意其 5z/7z 文件名写反）
  localtest/tiles/tencent_happy/<label>_*.png
  localtest/tiles/screenshot/NN_<label>.png
只测四川麻将词表内的 28 类（1-9m/p/s + 7z），其余类两边都判不对，无信息量。

**生产口径**：yolo_detector 的覆盖层只在 ref_conf >= 0.40 时才用模板标签，
否则回退 YOLO 自己的 label。所以两边都够不到 0.40 的漂移在线上根本不会发生，
必须单独统计，否则会为了安抚一批纸面漂移而引入不必要的平台耦合。

用法: py -3.10 localtest\leak_queshen.py [--gate 0.40]
"""
import glob
import os
import sys
from collections import Counter

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition import tencent_grid_detector as TGD  # noqa: E402

VALID = {f"{n}{s}" for s in "mps" for n in range(1, 10)} | {"7z"}

SOURCES = [
    ("real_tiles", os.path.join(HERE, "real_tiles", "*.png"), lambda b: b),
    ("duma520", os.path.join(HERE, "tiles", "duma520", "*.png"),
     lambda b: {"5z": "7z", "7z": "5z"}.get(b, b)),
    ("tencent_happy", os.path.join(HERE, "tiles", "tencent_happy", "*.png"),
     lambda b: b.split("_")[0]),
    ("screenshot", os.path.join(HERE, "tiles", "screenshot", "*.png"),
     lambda b: b.split("_")[-1]),
]


def gate(argv):
    return float(argv[argv.index("--gate") + 1]) if "--gate" in argv else 0.40


def make_det(keep_styles):
    banks = tuple((m, s) for m, s in TGD.EXTRA_BANKS if s in keep_styles)
    old = TGD.EXTRA_BANKS
    TGD.EXTRA_BANKS = banks
    try:
        return TGD.TencentGridDetector()
    finally:
        TGD.EXTRA_BANKS = old


def to_bgr(img):
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3].astype(np.float32) / 255.0
        white = np.full(img.shape[:2] + (3,), 255, np.uint8)
        return (img[:, :, :3].astype(np.float32) * a[:, :, None]
                + white * (1.0 - a)[:, :, None]).astype(np.uint8)
    return img


def main():
    thr = gate(sys.argv[1:])
    base = make_det({"shushan"})          # 加雀神 bank 之前
    full = make_det({"shushan", "queshen"})  # 之后
    print(f"base 库条目={len(base._cores)}  full 库条目={len(full._cores)}  放行列={thr}")

    tally = Counter()
    eff = Counter()                        # 生产口径
    rows = []
    for style, pat, truth in SOURCES:
        for p in sorted(glob.glob(pat)):
            lbl = truth(os.path.basename(p)[:-4])
            if lbl not in VALID:
                continue
            img = to_bgr(cv2.imread(p, cv2.IMREAD_UNCHANGED))
            if img is None:
                continue
            face = TGD.TencentGridDetector.extract_face(img)
            b, sb = base.classify_tile(face)
            f, sf = full.classify_tile(face)
            tally["n"] += 1

            # ---- 生产口径：模板分不到 0.40 时线上回退 YOLO，本次漂移不生效 ----
            bo = b if sb >= thr else None
            fo = f if sf >= thr else None
            if bo is None and fo is None:
                eff["两边都回退YOLO(无影响)"] += 1
            elif bo == fo:
                eff["最终标签未变"] += 1
            elif bo is None:
                # 新 bank 让模板第一次够到放行线：可能救回 YOLO 错标，也可能
                # 把本来由 YOLO 判对的牌抢错，需人眼复核，单列不计入净收益。
                eff["新bank开始接管(方向待定)"] += 1
                eff["接管后对"] += (1 if fo == lbl else 0)
                eff["接管后错"] += (0 if fo == lbl else 1)
            elif fo is None:
                eff["新bank反而掉出放行线(退给YOLO)"] += 1
            elif fo == lbl and bo != lbl:
                eff["净修正"] += 1
            elif bo == lbl and fo != lbl:
                eff["净泄漏"] += 1
            else:
                eff["换了一种错"] += 1

            if b == f:
                tally["unchanged"] += 1
                if b != lbl:
                    tally["两边同错"] += 1
                continue
            rows.append((style, os.path.basename(p), lbl, b, round(sb, 3), f, round(sf, 3)))
            if b != lbl and f == lbl:
                tally["修正(错->对)"] += 1
            elif b == lbl and f != lbl:
                tally["泄漏(对->错)"] += 1
            else:
                tally["换了一种错"] += 1

    print(f"\n=== {tally['n']} 张其它风格牌面 ===")
    for k in ("unchanged", "修正(错->对)", "泄漏(对->错)", "换了一种错", "两边同错"):
        print(f"  {k:12} {tally[k]}")
    if rows:
        print(f"\n纸面漂移的 {len(rows)} 张（未过放行线的线上不会发生）：")
        for style, fn, lbl, b, sb, f, sf in rows:
            live = "生效" if (sb >= thr or sf >= thr) else "无效"
            mark = "泄漏" if (b == lbl and f != lbl) else ("修正" if f == lbl else "换错")
            print(f"  [{live}][{mark}] {style:14} {fn:22} 真值={lbl}  前={b}({sb})  后={f}({sf})")

    print(f"\n=== 生产口径（覆盖只在 conf>={thr} 时生效）===")
    for k in ("两边都回退YOLO(无影响)", "最终标签未变", "净修正", "净泄漏",
              "换了一种错", "新bank开始接管(方向待定)", "新bank反而掉出放行线(退给YOLO)"):
        print(f"  {k:24} {eff[k]}")
    print(f"  接管样本里：判对 {eff['接管后对']} / 判错 {eff['接管后错']}")
    net = tally["修正(错->对)"] - tally["泄漏(对->错)"]
    print(f"\n纸面净收益 = 修正 - 泄漏 = {net:+d}")
    print(f"线上净收益 = 净修正 - 净泄漏 = {eff['净修正'] - eff['净泄漏']:+d}"
          f"   （待定项 {eff['新bank开始接管(方向待定)']} 张不计入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
