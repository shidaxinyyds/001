# -*- coding: utf-8 -*-
"""钉 GT 前的质量门：把“这张裁片能不能当模板样本”变成可复现的测量，而不是逐张放大肉眼看。

为什么需要它：public/1 里有一批截图带**半透明 UI 横条**（录屏/播放器的进度条残影），
它横穿手牌行的下缘。人眼看 sheet 时它只是“底部一条暗带”，但收割端 face_align 是按
象牙白掩码取外接框的——横条把白区切断后，框会**在横条处被截断**，于是同一张牌被
拉伸成两种比例进库。这类污染不会报错，只会让该类的模板之间互相 NCC 下降，表现为
“偶尔认错牌”，极难回溯（实测途游帧 06 的 8m/9m 进库后，同帧 3m/5m 全被读成 8m）。

四个信号都是**测量**，不依赖检测器给的标签（读图纪律同样适用于质量判定）：
  aspect   对齐后牌面的宽高比，与**本帧中位数**比。偏 >4% 说明抠出来的不是完整牌面
           （偏大 = 下缘被截断、整张牌被拉伸；偏小 = 混进了邻牌/绿边）。实测途游帧
           06：同帧牌面 151x192，被横条压住的四枚只有 151x172——宽不变、高少 10%。
           为什么必须帧内互比而不是跟全平台中位数比：同一平台常有两套牌风（途游暗色
           帧 rect 181x244、亮色帧 153x206），跨帧中位数会把正常的另一套字模判成污染。
  stripe   匹配核（牌面 13%~87% 行）里最暗一行的暗像素占比——**只当参考，不判死**：
           实测它在一萬/三萬的横笔画、九筒的黑色那行上稳定误报（0.62~0.72），
           而真遮罩几乎总会同时把白区切断、被 aspect 抓到。拿它当门限会批量误杀好牌。
  sharp    边缘陡度（对比度归一后的梯度 p99）：低于本帧中位数 65% = 糊（动画中/压缩重）。
           为什么必须归一：最初的实现用 Laplacian 方差，它**随笔画数量走**而不是随模糊
           走——实测二条/中/白这类稀疏牌面在同帧里稳定只有密集牌面的 0.36~0.5 倍，于是
           质门把“牌面干净但笔画少”当成糊，系统性误杀最缺样本的那几个类（tencent 的
           2s 三帧全中、gd_queshen 的 中×3 全中）。改成 p99(|grad|)/动态范围后，同一批
           格的比值回到 0.94~0.99，而真遮罩/真动画帧仍会掉到 0.6 以下。
  lapvar   Laplacian 方差：**只当参考，不判死**（同上，它是内容量信号不是清晰度信号）。
  bright   平均亮度：帧间差异大 = 压暗态（定缺/高亮），可入库但需知道。

帧级判据：本帧过半格（≥ max(3, 50%)）的 aspect 都偏离本帧中位数 ⇒ 整行透视/倾斜（发牌
动画、斜视角），逐格都不该入库——实测 JJ 帧 08/12/13 的 aspect 从 0.37 连续变到 0.74，
是同一手牌被斜着画出来，直接弃帧比逐格标 `?` 诚实。
为什么不用“帧内极差 >12%”做弃帧判据：UI 横条截断也会把几枚牌的 aspect 同方
向顶高（实测 JJ 帧 06/09 各有 4 枚 0.878、其余 0.781），极差规则会把“只有 4 格坏”
的帧误判成整帧透视，白丢 9~10 张可用样本。弃帧与逐格 `?` 的分界只能是“多少人坏”，
不是“极差多大”。

只读、只报，不改任何数据：判 `?` 还是弃帧仍由人拍板，本脚本给的是拍板的依据。

运行：py -3.10 -X utf8 localtest/qc_tile_quality.py --platform jj
      py -3.10 -X utf8 localtest/qc_tile_quality.py --platform jj --frames 5,6,9
输出：build/qc_<平台>.txt（派生物）
"""
import argparse
import json
import os
import statistics
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from style_harvest import face_align  # noqa: E402

ASPECT_TOL = 0.04      # 与本帧中位数的相对偏差（帧内互比，避免把另一套字模判成污染）
SPREAD_REF = 0.12      # 帧内 aspect 极差的“只报告”门限（背后面是截断还是透视，看占比）
DEV_MAJORITY = 0.5     # 偏离中位数的格占比超过这值（且至少 3 格）才算整帧透视→弃帧
SHARP_RATIO = 0.65     # 归一边缘陡度低于本帧中位数的这个比例算糊
LAP_REF = 0.5          # Laplacian 方差的“只报告”门限（内容量信号，不参与判死）
CORE_LO, CORE_HI = 0.13, 0.87   # 生产 _build_cores 的匹配核行区间（16:104 / 120）
DARK_V = 110           # HSV 的 V 低于此算暗像素
STRIPE_REF = 0.6       # 核内横纹的“只报告”门限（不参与判死，见顶部 stripe 说明）


def measure(img, rect):
    x, y, w, h = rect
    hh, ww = img.shape[:2]
    crop = img[max(0, y):min(hh, y + h), max(0, x):min(ww, x + w)]
    if crop.size == 0:
        return None
    face = face_align(crop)
    v = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)[:, :, 2]
    g = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY).astype(np.float32)
    grad = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3),
                    cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    dark = (v < DARK_V).mean(axis=1)                 # 每行暗像素占比
    lo = int(v.shape[0] * CORE_LO)
    hi = max(lo + 1, int(v.shape[0] * CORE_HI))
    # 动态范围（p99-p1）当分母：把“牌面本身笔画少/对比低”与“边缘被抹平”分开
    dyn = max(1.0, float(np.percentile(g, 99) - np.percentile(g, 1)))
    return {
        "aspect": face.shape[1] / float(face.shape[0]),
        "face_h": int(face.shape[0]),
        "stripe": float(dark[lo:hi].max()),          # 核内最“黑”的一行
        "below": float(dark[hi:].max()) if hi < v.shape[0] else 0.0,
        "sharp": float(np.percentile(grad, 99)) / dyn * 100.0,
        "lapvar": float(cv2.Laplacian(g, cv2.CV_32F).var()),
        "bright": float(v.mean()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True)
    ap.add_argument("--src", default="public/1")
    ap.add_argument("--frames", default=None, help="逗号分隔帧号，默认该目录下全部 rects")
    a = ap.parse_args()
    ann = os.path.join(REPO, "build", f"ann_{a.platform}")
    if not os.path.isdir(ann):
        print(f"没有 {ann}（先跑 make_tile_sheets.py --platform {a.platform} --band）")
        return 2
    if a.frames:
        idxs = [int(x) for x in a.frames.split(",")]
    else:
        idxs = sorted(int(f[6:8]) for f in os.listdir(ann) if f.startswith("rects_"))
    per = {}
    for fr in idxs:
        rp = os.path.join(ann, f"rects_{fr:02d}.json")
        if not os.path.exists(rp):
            print(f"[{fr:02d}] 无 rects，跳过")
            continue
        with open(rp, encoding="utf-8") as fp:
            r = json.load(fp)
        img = cv2.imread(os.path.join(REPO, a.src, r["file"]))
        if img is None:
            print(f"[{fr:02d}] 读不到 {r['file']}")
            continue
        rr = sorted(r["rects"], key=lambda x: x[0])
        ms = [m for m in (measure(img, x) for x in rr) if m]
        if ms:
            per[fr] = ms
    if not per:
        return 2

    lines = [f"平台 {a.platform}（{a.src}）裁片质量门",
             f"判据：aspect 与本帧中位数偏差 >{ASPECT_TOL:.0%}（截断/粘连）、"
             f"偏离格数 ≥max(3,{DEV_MAJORITY:.0%}) （整行透视→弃帧）、"
             f"归一边缘陡度 <本帧中位数 {SHARP_RATIO:.0%}；核内横纹与 Laplacian 方差仅做参考", ""]
    n_flag = 0
    for fr in sorted(per):
        ms = per[fr]
        med_asp = statistics.median([m["aspect"] for m in ms])
        med_sharp = statistics.median([m["sharp"] for m in ms])
        med_lap = statistics.median([m["lapvar"] for m in ms])
        asp = [m["aspect"] for m in ms]
        spread = (max(asp) - min(asp)) / med_asp
        n_dev = len([a for a in asp if abs(a - med_asp) / med_asp > ASPECT_TOL])
        bad = []
        n_dead = 0
        if n_dev >= max(3, int(DEV_MAJORITY * len(ms))):
            bad.append(f"整帧透视：{n_dev}/{len(ms)} 格 aspect 偏离本帧中位数超 "
                       f"{ASPECT_TOL:.0%}（极差 {spread:.0%}）-> 弃帧")
            n_dead = 1
        else:
            for i, m in enumerate(ms, 1):
                why = []
                dead = False
                if abs(m["aspect"] - med_asp) / med_asp > ASPECT_TOL:
                    why.append(f"截断/粘连?aspect {m['aspect']:.3f}({m['face_h']}px)")
                    dead = True
                if m["sharp"] < med_sharp * SHARP_RATIO:
                    why.append(f"糊 {m['sharp']:.0f}/{med_sharp:.0f}")
                    dead = True
                if m["stripe"] > STRIPE_REF:
                    why.append(f"参考:核内横纹 {m['stripe']:.2f}")
                if med_lap and m["lapvar"] < med_lap * LAP_REF:
                    why.append(f"参考:笔画稀 {m['lapvar']:.0f}/{med_lap:.0f}")
                if why:
                    bad.append(f"#{i}:" + ",".join(why))
                    n_dead += dead
        n_flag += n_dead
        br = [m["bright"] for m in ms]
        below = max(m["below"] for m in ms)
        ref = f" 参考:极差 {spread:.0%}>参考线" if spread > SPREAD_REF else ""
        lines.append(f"[{fr:02d}] n={len(ms)} 帧内中位 aspect {med_asp:.3f} "
                     f"范围 {min(asp):.3f}~{max(asp):.3f} 亮度 {min(br):.0f}~{max(br):.0f} "
                     f"下缘横纹峰值 {below:.2f}{ref}  "
                     + ("可疑 " + "  ".join(bad) if bad else "全部干净"))
    lines += ["",
              f"共 {n_flag} 格/帧被判死。注：aspect 偏大=下缘被截断（收割会把它拉伸成另一种"
              "比例，必须标 `?` 或弃帧）；偏小=混进邻牌或牌面被放大（抬起/选中态实测 205px"
              " vs 常规 192px，同样不入库）。只有『下缘横纹』而 aspect 正常的那些，暗带落在"
              "匹配核之外，可入库。带『参考:』字样的不是判死依据。",
              "提示：同一平台出现两种帧内中位 aspect 属正常（两套牌风/两种牌面尺寸），"
              "不要为此剔帧。"]
    text = "\n".join(lines) + "\n"
    out = os.path.join(REPO, "build", f"qc_{a.platform}.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
