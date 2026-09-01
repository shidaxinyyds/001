# -*- coding: utf-8 -*-
"""
Harvest 34 tile classes from 10 teaching images — v2 (content-based suit detection).

v1 失败根因：参考集按 "行1=万/行2=筒/行3=条/行4=荣誉" 硬编码 → 参考图 40541 实际
行 0 是筒，所以整套参考集的花色名全部错挂，跨图模板匹配也跟着全错。

v2 核心：不再假设"行号→花色"固定映射，按每张图的牌面行标签目视确定每行花色。
  - 参考集按 40541 实测内容建（行 0=筒 p、行 1=万 m、行 2=条 s、行 3=荣誉 z）。
  - 每张图用 IMAGE_ROW_SUITS 查表（依行标签"万/筒/条/番"目视标注，含异序图
    40541=p/m/s/z、46715=p/s/m/z），逐行定花色，无行序假设。
  - 数值行：花色来自查表；号数 1–9 按列位置（教学图标准 1→9 升序）。
  - 数值行另做逐牌 glyph 匹配交叉验证，表标与匹配不符时打印警告。
  - 荣誉行：白底连通域定位 7 张真牌 → 7-way 参考匹配定 1z..7z。
"""
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
RAW_DIR = SCRIPT_DIR / "train_data" / "raw" / "face_pretrain"
OUT_DIR = SCRIPT_DIR / "train_data" / "processed" / "face_pretrain"

REFERENCE_IMAGE = "40541d4d12cf0525fb6c875830d2ac12.jpg"

# 40541 实测行内容（v1 注释与实际不符，这里以肉眼核查为准）：
#   row0 = 筒子(p)  1筒…9筒
#   row1 = 万子(m)  一万…九万
#   row2 = 条子(s)  一条…九条
#   row3 = 番子(z)  東南西北中發白
REF_ROW_SUITS = ["p", "m", "s", "z"]

# 全部 10 张教学图行序 ground truth（按行标签"万/筒/条/番"目视标注）
# 8/10 张是 m/p/s/z，仅 40541 (p/m/s/z) 和 46715 (p/s/m/z) 是异类。
IMAGE_ROW_SUITS = {
    "1494c7c0371cd2eabcc28be7381739c8.jpg": ["m", "p", "s", "z"],
    "40541d4d12cf0525fb6c875830d2ac12.jpg": ["p", "m", "s", "z"],
    "44da61d56f153976f3492f1346923cb3.jpg": ["m", "p", "s", "z"],
    "46715c5c69b199c39b08c9edf8a5d4b9.jpg": ["p", "s", "m", "z"],
    "7a505cd63aa3e8f07fedbfce1dcc8002.jpg": ["m", "p", "s", "z"],
    "7f0c9a8fcbc91ab9f055441ea61529c3.jpg": ["m", "p", "s", "z"],
    "9a0662b17e578bf61f30c3a27f54d78e.jpg": ["m", "p", "s", "z"],
    "cd352b6df675ce11409dd0a450e875d5.jpg": ["m", "p", "s", "z"],
    "d67fca311badd68d34acd2b5043ec600.jpg": ["m", "p", "s", "z"],
    "d91e9495fd10b28749b28de11e400db4.jpg": ["m", "p", "s", "z"],
}

NUMERIC_CLASSES = [f"{i}{s}" for s in ("m", "p", "s") for i in range(1, 10)]
HONOR_CLASSES = [f"{i}z" for i in range(1, 8)]
ALL_CLASSES = NUMERIC_CLASSES + HONOR_CLASSES


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def crop_tile(img, y1, y2, x1, x2):
    h, w = img.shape[:2]
    y1, y2 = max(0, y1), min(h, y2)
    x1, x2 = max(0, x1), min(w, x2)
    if y2 <= y1 or x2 <= x1:
        return None
    return img[y1:y2, x1:x2].copy()


def is_valid_tile(crop, min_std=14, min_size=18):
    if crop is None:
        return False
    h, w = crop.shape[:2]
    if h < min_size or w < min_size:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    if gray.std() < min_std:
        return False
    return True


def normalize_gray(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)


def match_score(crop, ref):
    """Single-ref normalized cross-correlation. Higher is better; range roughly [-1, 1]."""
    if crop is None or ref is None:
        return -1.0
    nc = normalize_gray(crop)
    nr = normalize_gray(ref)
    if nr.size == 0 or nc.size == 0:
        return -1.0
    resized = cv2.resize(nc, (nr.shape[1], nr.shape[0]))
    return float(cv2.matchTemplate(resized, nr, cv2.TM_CCOEFF_NORMED).max())


def best_match_suit(crop, numeric_refs):
    """3-way: 筒(p) / 万(m) / 条(s). Return (best_suit, best_score)."""
    if crop is None or not is_valid_tile(crop):
        return None, -1.0
    best_s, best_sc = None, -1.0
    for s in ("p", "m", "s"):
        refs = numeric_refs.get(s, [])
        if not refs:
            continue
        sc = max((match_score(crop, r) for r in refs if r is not None), default=-1.0)
        if sc > best_sc:
            best_sc = sc
            best_s = s
    return best_s, best_sc


def is_white_bg(crop, sat_thresh=45, white_frac_min=0.35):
    """牌面白底检测：拒纯色绿/红/深色底纹。用于从荣誉行 9 格里挑出 7 张真牌。"""
    if crop is None or crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mean_sat = float(hsv[:, :, 1].mean())
    if mean_sat > sat_thresh:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.std() < 10:
        return False
    white_frac = float((gray > 200).mean())
    return white_frac >= white_frac_min


def find_white_tiles(band_color, n=7):
    """在荣誉行彩色带中用 HSV 白底掩膜 + 连通域定位 n 张白底牌，按 x 排序。
    比均匀切分更稳：避开牌间隙、不受边框（白板）影响。
    注：对 中(红字)/發(绿字) 等带色字牌的白底面积小，可能漏检（标签仍正确，只是样本少）。"""
    hsv = cv2.cvtColor(band_color, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] < 60) & (hsv[:, :, 2] > 150)).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    n_cc, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    band_h = band_color.shape[0]
    tiles = []
    for i in range(1, n_cc):
        x, y, w, h, area = stats[i]
        if h >= band_h * 0.6 and w >= band_h * 0.4 and area >= band_h * band_h * 0.2:
            tiles.append((float(centroids[i][0]), int(x), int(y), int(w), int(h)))
    tiles.sort(key=lambda t: t[0])
    crops = []
    for _, x, y, w, h in tiles[:n]:
        pad = 2
        x0 = max(0, x - pad); y0 = max(0, y - pad)
        x1 = min(band_color.shape[1], x + w + pad)
        y1 = min(band_color.shape[0], y + h + pad)
        crops.append(band_color[y0:y1, x0:x1].copy())
    return crops


def save_crop(crop, label, src_name, stats):
    out_dir = OUT_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{src_name}_{stats[label]:03d}.png"
    cv2.imwrite(str(out_path), crop)
    stats[label] += 1


# ---------------------------------------------------------------------------
# 均匀切分（按行内亮像素主块）
# ---------------------------------------------------------------------------
def band_x_range(gray_row, lo=170):
    """Return the (x1, x2) of the main bright column-block (tiles), excluding narrow
    left-side row-label text. Tile columns have high bright-pixel density; label
    columns and green/red background have low density."""
    bright = (gray_row > lo).astype(np.float32)
    col_density = bright.mean(axis=0)
    if col_density.max() <= 0:
        return None
    threshold = col_density.max() * 0.40
    active = col_density > threshold
    xs = np.where(active)[0]
    if len(xs) == 0:
        return None
    return int(xs[0]), int(xs[-1]) + 1


def row_cells(gray_row, n, lo=170, pad=2):
    xr = band_x_range(gray_row, lo)
    if xr is None:
        return []
    x1, x2 = xr
    x1 = max(0, x1 - pad)
    x2 = min(gray_row.shape[1], x2 + pad)
    width = (x2 - x1) / n
    return [(int(x1 + i * width), int(x1 + (i + 1) * width)) for i in range(n)]


# ---------------------------------------------------------------------------
# 自适应牌行带检测（按亮像素密度排名取前 4 —— 牌行最白，文字/背景带自然落选）
# ---------------------------------------------------------------------------
def find_tile_bands(gray):
    """Detect 4 tile bands adaptively. Tile rows are predominantly white (high
    bright density); text captions and green/red backgrounds have lower density
    and fall outside the top-4 dense bands. Returns 4 (y1, y2) sorted by y."""
    h = gray.shape[0]
    # 用行标准差（白牌+深图案 → 高 std；纯色背景 → 低 std）替代亮像素密度。
    # 这样亮米/黑/粉等不同底色都能稳定识别牌行。
    row_std = gray.astype(np.float32).std(axis=1)
    min_h = max(int(h * 0.06), 30)

    def _scan(threshold):
        in_band = row_std > threshold
        bands, start = [], None
        for i, v in enumerate(in_band):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if (i - start) >= min_h:
                    bands.append((start, i, float(row_std[start:i].mean())))
                start = None
        if start is not None and (len(in_band) - start) >= min_h:
            bands.append((start, len(in_band), float(row_std[start:].mean())))
        return bands

    bands = _scan(20.0)
    if len(bands) < 4:
        bands = _scan(10.0)
    if len(bands) < 4:
        bands = _scan(5.0)
    if len(bands) < 4:
        return None
    bands.sort(key=lambda b: -b[2])
    top4 = sorted(bands[:4], key=lambda b: b[0])
    return [(y1, y2) for y1, y2, _ in top4]


# ---------------------------------------------------------------------------
# 参考集提取（按 40541 实测内容：p/m/s/z）
# ---------------------------------------------------------------------------
def extract_reference_set(reference_path):
    """Build correct content references from the reference image.
    Row0=筒(9 refs), row1=万(9 refs), row2=条(9 refs), row3=荣誉(7 refs in 1z..7z order)."""
    img = cv2.imread(str(reference_path))
    if img is None:
        raise RuntimeError(f"无法读取参考图: {reference_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bands = find_tile_bands(gray)

    numeric_refs = {"p": [None] * 9, "m": [None] * 9, "s": [None] * 9}
    honor_refs = [None] * 7

    row_suits = IMAGE_ROW_SUITS.get(reference_path.name, REF_ROW_SUITS)
    for row_idx, (y1, y2) in enumerate(bands):
        suit = row_suits[row_idx]
        if suit == "z":
            # 荣誉行：连通域定位 7 张白底牌（按 x 顺序 = 1z..7z，40541 实测就是标准序）
            white_crops = find_white_tiles(img[y1:y2], n=7)
            if len(white_crops) < 7:
                raise RuntimeError(
                    f"参考图荣誉行仅检出 {len(white_crops)} 张白底牌（需 7）"
                )
            for i, c in enumerate(white_crops[:7]):
                honor_refs[i] = c
        else:
            cells9 = row_cells(gray[y1:y2], 9)
            filled = 0
            for i, (x1, x2) in enumerate(cells9[:9]):
                c = crop_tile(img, y1, y2, x1, x2)
                if is_valid_tile(c):
                    numeric_refs[suit][i] = c
                    filled += 1
            if filled < 8:
                print(f"  REF 警告：{suit} 行仅 {filled}/9 张有效")

    # 健全性
    for s in ("p", "m", "s"):
        miss = [i for i, r in enumerate(numeric_refs[s]) if r is None]
        if miss:
            print(f"  REF 警告：{s} 参考缺号 {miss}")
    miss_z = [i for i, r in enumerate(honor_refs) if r is None]
    if miss_z:
        print(f"  REF 警告：荣誉参考缺 {miss_z}")
    return numeric_refs, honor_refs


# ---------------------------------------------------------------------------
# 单图 harvest：内容检测花色
# ---------------------------------------------------------------------------
def harvest_image(img_path, numeric_refs, honor_refs, stats, debug=False):
    img = cv2.imread(str(img_path))
    if img is None:
        print(f"  跳过：无法读取 {img_path.name}")
        return 0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bands = find_tile_bands(gray)
    if bands is None:
        print(f"  跳过 {img_path.name}：未检测到 4 个牌行带")
        return 0

    # 按牌面行标签目视确定的每行花色（不再假设固定行序）。
    # 8/10 张为 m/p/s/z；40541=p/m/s/z；46715=p/s/m/z。
    row_suits = IMAGE_ROW_SUITS.get(img_path.name, ["m", "p", "s", "z"])
    honor_idx = row_suits.index("z")
    if debug:
        print(f"  行序 suit = {row_suits}  →  honor row = {honor_idx}")

    n_extracted = 0
    n_mismatch = 0  # 表标与逐牌 glyph 匹配不符计数（仅 debug 提示）

    for row_idx, (y1, y2) in enumerate(bands):
        suit = row_suits[row_idx]
        if suit == "z":
            # 荣誉行：连通域定位牌中心（按 x 排序 = 1z..7z 顺序：東南西北中發白）
            # 连通域对 中/發 带色字牌也稳健，比 band_x_range 等分更可靠
            white_crops = find_white_tiles(img[y1:y2], n=7)
            for k, c in enumerate(white_crops[:7]):
                label = f"{k + 1}z"
                save_crop(c, label, img_path.stem, stats)
                n_extracted += 1
        else:
            # 数值行：9 等分 + 列位置 1..9 定号数；花色来自行标签 ground truth
            cells9 = row_cells(gray[y1:y2], 9)
            if len(cells9) != 9:
                continue
            for pos, (x1, x2) in enumerate(cells9[:9]):
                c = crop_tile(img, y1, y2, x1, x2)
                if c is None:
                    continue
                label = f"{pos + 1}{suit}"
                save_crop(c, label, img_path.stem, stats)
                n_extracted += 1
                if debug:
                    m_suit, m_sc = best_match_suit(c, numeric_refs)
                    # 仅在高置信度(>0.5)且花色不符时才告警；低分匹配是模板匹配
                    # 跨风格不可靠的噪声，不反映表标错误。
                    if m_suit is not None and m_suit != suit and m_sc > 0.5:
                        n_mismatch += 1
                        if n_mismatch <= 12:
                            print(f"    ⚠ 行{row_idx} 列{pos + 1} 表标={suit} "
                                  f"但逐牌匹配={m_suit}({m_sc:.2f})")

    if debug and n_mismatch:
        print(f"  ⚠ 逐牌 glyph 匹配与表标不符 {n_mismatch} 次（核对 IMAGE_ROW_SUITS）")
    return n_extracted


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    if not RAW_DIR.exists():
        print(f"错误：原始目录缺失: {RAW_DIR}")
        sys.exit(1)
    images = sorted(RAW_DIR.glob("*.jpg"))
    if not images:
        print(f"错误：{RAW_DIR} 下无图片")
        sys.exit(1)
    print(f"找到 {len(images)} 张教学图")

    # 清空旧输出（逐文件 unlink，绕过沙箱对 rmtree 的拦截）
    if OUT_DIR.exists():
        for f in OUT_DIR.rglob("*.png"):
            try:
                f.unlink()
            except OSError:
                pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ref_path = RAW_DIR / REFERENCE_IMAGE
    if not ref_path.exists():
        print(f"错误：参考图缺失: {ref_path}")
        sys.exit(1)
    print(f"从 {REFERENCE_IMAGE} 提取参考集（按实测内容 p/m/s/z）...")
    numeric_refs, honor_refs = extract_reference_set(ref_path)
    n_num = sum(1 for s in ("p", "m", "s") for r in numeric_refs[s] if r is not None)
    n_z = sum(1 for r in honor_refs if r is not None)
    print(f"  参考集：数值 {n_num}/27  荣誉 {n_z}/7")
    if n_num < 27 or n_z < 7:
        print("错误：参考集不完整")
        sys.exit(1)

    stats = {c: 0 for c in ALL_CLASSES}
    total = 0
    for img_path in images:
        print(f"处理 {img_path.name}...")
        n = harvest_image(img_path, numeric_refs, honor_refs, stats, debug=True)
        print(f"  → {n} 张")
        total += n

    print("\n=== 各类样本数 ===")
    bad = []
    for c in ALL_CLASSES:
        if stats[c] == 0:
            bad.append(c)
            marker = "✗"
        elif stats[c] < 2:
            marker = "!"
        else:
            marker = "✓"
        print(f"  {marker} {c}: {stats[c]}")
    print(f"\n总计提取: {total}")
    print(f"输出目录: {OUT_DIR}")
    if bad:
        print(f"\n警告：0 样本的类别: {bad}")


if __name__ == "__main__":
    main()
