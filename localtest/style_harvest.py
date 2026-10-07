# -*- coding: utf-8 -*-
"""按平台把手牌裁片聚类去重，产出「类代表拼图」供一次性标注牌面。

为什么聚类而不是逐帧读图：41 帧 × 11~14 张 ≈ 500 个裁片，逐帧读图既贵又容易
数错列（网格图无可靠列锚点，实测会把 12 格看成 14 格）。而同一 App 的牌面美术
是固定的——同一张牌在不同帧里几乎像素级重复，聚类后每类只需标一次，
标注量从 ~500 降到每平台 34 以内，且标签天然带多个样本。

产物:
- shots_calib/gtm/cls_<plat>.png     类代表拼图（格子上标 cluster id 与样本数）
- shots_calib/gtm/cls_<plat>.json    cluster id -> {n, samples[[frame,col,path]], mean_vec}

用法: py -3.10 localtest\style_harvest.py [plat ...]
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "android", "app", "src", "main", "python"))
from montage_for_gt import PLATFORM_OF, hand_tiles, OUT, SHOTS  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

# 归一化到该尺寸再做 NCC：牌面结构（几点/几根/几画）在这个尺度上已足够区分
FW, FH = 72, 108
NCC_TH = 0.90  # 占位：--probe 实测同类最低 0.056 < 异类最高 0.578，分布重叠，
#              灰度 NCC 自动聚类不可行；本脚本保留作聚类质量探针，标注仍走
#              人工读 montage_for_gt.py 编号拼图（见 harvest_style_tiles.py）。


def face_align(crop):
    """牌体配准：直接转发生产 `TencentGridDetector._ivory_body`。

    为什么不再自己写一份：bank 模板 = `extract_face(face_align(裁片))`，推理面
    = `extract_face(检测框)`。两边各写一套对齐判据时，任一侧改了阈值就会让
    “同一张牌”在 bank 与推理下落到 120x80 的不同位置，分数塌陷（实测途游帧 06
    的三萬与本家其他帧的三萬互匹只有 0.34）——而现象只是“分数莫名偏低”，
    归因时很容易当成分类器不行。现在生产 `extract_face` 已内置同一函数，
    bank 侧再包一层只为保留旧调用点。
    """
    return TencentGridDetector._ivory_body(crop)


def face_vec(crop):
    g = cv2.cvtColor(face_align(crop), cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (FW, FH), interpolation=cv2.INTER_AREA).astype(np.float32)
    if g.std() < 1e-3:
        return None
    return ((g - g.mean()) / g.std()).ravel()


def collect(plat):
    """返回 (vec, crop, frame_idx, col) 列表。crop 保留原始分辨率用于落盘。"""
    items = []
    all_idx = sorted(int(f.split("_")[1][:2]) for f in os.listdir(SHOTS)
                     if f.lower().endswith(".jpg"))
    for idx in [i for i in all_idx if PLATFORM_OF.get(i, "generic") == plat]:
        img, ts = hand_tiles(idx)
        if img is None or not ts:
            print(f"[{idx:02d}] 无检出")
            continue
        for col, t in enumerate(sorted(ts, key=lambda t: t[0])):
            x, y, w, h = t[:4]
            hh, ww = img.shape[:2]
            crop = img[max(0, y):min(hh, y + h), max(0, x):min(ww, x + w)]
            if crop.size == 0 or crop.shape[0] < 40:
                continue
            v = face_vec(crop)
            if v is None:
                continue
            items.append((v, crop, idx, col, t[4]))
    return items


def cluster(items):
    reps = []  # {vec, crops[], ids[]}
    for v, crop, idx, col, elbl in items:
        best, bs = None, -1.0
        for r in reps:
            s = float(np.dot(v, r["vec"]) / (np.linalg.norm(v) * np.linalg.norm(r["vec"]) + 1e-6))
            if s > bs:
                best, bs = r, s
        if best is not None and bs >= NCC_TH:
            best["items"].append((idx, col, crop, elbl))
            # 滑动更新均值向量，避免早期样本偶然噪声主导
            n = len(best["items"])
            best["vec"] = best["vec"] * (n - 1) / n + v / n
        else:
            reps.append({"vec": v.copy(), "items": [(idx, col, crop, elbl)]})
    reps.sort(key=lambda r: -len(r["items"]))
    return reps


def probe():
    """用 [14] 已人工核对的 GT 量「同类 NCC」与「异类 NCC」分布，据此定阈值。

    GT14 = 7m 7m 7m 8m | 2p 2p 2p | 6p 6p | 8p 8p 8p | 8s 7s（左→右）
    共 C(14,2)=91 对，其中同类 10 对（7m×3 + 2p×3 + 6p×2 + 8p×3）、异类 81 对。
    """
    gt = "7m 7m 7m 8m 2p 2p 2p 6p 6p 8p 8p 8p 8s 7s".split()
    img, ts = hand_tiles(14)
    ts = sorted(ts, key=lambda t: t[0])
    vs, labs = [], []
    for i, t in enumerate(ts):
        x, y, w, h = t[:4]
        crop = img[y:y + h, x:x + w]
        v = face_vec(crop)
        if v is not None and i < len(gt):
            vs.append(v)
            labs.append(gt[i])
    same, diff = [], []
    for i in range(len(vs)):
        for j in range(i + 1, len(vs)):
            s = float(np.dot(vs[i], vs[j]) / (np.linalg.norm(vs[i]) * np.linalg.norm(vs[j])))
            (same if labs[i] == labs[j] else diff).append(s)
    same.sort()
    diff.sort()
    print(f"同类对 n={len(same)}  min={same[0]:.3f} p10={same[len(same)//10]:.3f} "
          f"中位={same[len(same)//2]:.3f} max={same[-1]:.3f}")
    print(f"异类对 n={len(diff)}  max={diff[-1]:.3f} p90={diff[int(len(diff)*0.9)]:.3f} "
          f"中位={diff[len(diff)//2]:.3f}")
    gap_lo, gap_hi = diff[-1], same[0]
    print(f"分离带: 异类最大 {gap_lo:.3f} <-> 同类最小 {gap_hi:.3f}"
          + ("  [ok] 无重叠" if gap_lo < gap_hi else "  [x] 有重叠, 自动聚类不可行"))
    return 0


def main():
    if sys.argv[1:2] == ["--probe"]:
        return probe()
    plats = sys.argv[1:] or ["weile", "shushan", "gd_queshen", "jj", "tuyou"]
    for plat in plats:
        items = collect(plat)
        if not items:
            print(f"{plat}: 无样本")
            continue
        reps = cluster(items)
        print(f"\n=== {plat}: {len(items)} 裁片 -> {len(reps)} 类 ===")
        meta = {}
        repdir = os.path.join(OUT, "rep")
        os.makedirs(repdir, exist_ok=True)
        for cid, r in enumerate(reps):
            crop = r["items"][0][2]  # 首个样本作代表（原始分辨率）
            p = os.path.join(repdir, f"{plat}_c{cid:02d}.png")
            cv2.imwrite(p, crop)
            meta[str(cid)] = {
                "n": len(r["items"]),
                "samples": [[i, c, os.path.relpath(p, REPO_REL)] for i, c, _cr, _e in r["items"]],
                "engine_labels": sorted({e for *_x, e in r["items"] if e}),
            }
            print(f"  c{cid:02d} n={len(r['items']):3d} 引擎给过={meta[str(cid)]['engine_labels']}")
        with open(os.path.join(OUT, f"cls_{plat}.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        # 类代表拼图
        H = 170
        shown = reps[:48]
        rows = []
        for r0 in range(0, len(shown), 6):
            line = []
            for cid in range(r0, min(r0 + 6, len(shown))):
                crop = shown[cid]["items"][0][2]
                sc = H / float(crop.shape[0])
                c2 = cv2.resize(crop, (max(1, int(crop.shape[1] * sc)), H),
                                interpolation=cv2.INTER_AREA)
                canvas = cv2.copyMakeBorder(c2, 30, 6, 6, 6, cv2.BORDER_CONSTANT,
                                            value=(255, 255, 255))
                cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 30), (30, 30, 30), -1)
                cv2.putText(canvas, f"c{cid:02d} x{len(shown[cid]['items'])}",
                            (4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2,
                            cv2.LINE_AA)
                line.append(canvas)
            while len(line) < 6:
                line.append(np.full((H + 36, line[0].shape[1], 3), 220, np.uint8))
            rows.append(np.hstack(line))
        W = max(r.shape[1] for r in rows)
        rows = [np.pad(r, ((0, 0), (0, W - r.shape[1]), (0, 0)), constant_values=255)
                for r in rows]
        out = os.path.join(OUT, f"cls_{plat}.png")
        cv2.imwrite(out, np.vstack(rows))
        print(f"-> {os.path.basename(out)} ({len(shown)} 类)\n")
    return 0


REPO_REL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    sys.exit(main())
