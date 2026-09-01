"""build_majsoul_bank.py — 为雀魂/Mahjong Soul 风格构建整牌字形模板。

与 build_style_bank.py 的区别：雀魂截图中牌周围常有绿色牌桌背景。
直接 det._ink_mask(face) 会把绿色（饱和度>0）当成 colorful 墨迹污染模板
（详见 structural._ink_mask：S/Otsu > 0 的像素被并入墨迹掩码）。
本脚本在 _ink_mask 之前把绿色背景置 0，保证模板只含牌本身的墨迹。

产物：android/app/src/main/python/recognition/images/styles/majsoul/<label>.png
      （二值墨迹图，96x96 letterbox，结构性 v2 已自动加载）

输入目录约定（与 build_style_bank.py 一致）：
    tiles/<label>/*.png
  其中 <label> ∈ {1m..9m, 1p..9p, 1s..9s, 1z..7z}。
  每个 label 的目录里放几张该种牌的清晰 crop（从游戏截图切出的单张）。
  脚本取 Laplacian 最清晰的若干张生成模板（多张时取各自生成的模板）。

用法：
    # 1. 从游戏截图提取单牌 crop（参考 extract_screenshot_tiles.py /
    #    localtest/harvest_majsoul.py），按 label 存到：
    #    localtest/_majsoul_raw/1m/1.png, 2.png, ...
    # 2. 跑本脚本：python localtest/build_majsoul_bank.py
    # 3. 验证：python localtest/_verify_majsoul.py
"""

import os
import sys
import glob

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition.structural import StructuralDetector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(PYROOT, "recognition", "images", "styles", "majsoul")
SRC = os.path.join(HERE, "_majsoul_raw")  # 用户放入按 label 分类的 raw crops

# 绿色牌桌背景阈值 (HSV)：：雀魂绿色牌桌大致在这个区间，置 0 排除污染
GREEN_LO = (30, 40, 60)
GREEN_HI = (95, 240, 240)


def _strip_green(face: np.ndarray) -> np.ndarray:
    """把 face 中绿色牌桌背景置 0（黑色），避免 _ink_mask 把它当墨迹。"""
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, GREEN_LO, GREEN_HI)
    out = face.copy()
    out[green > 0] = 0
    return out


def _to_tpl(det: StructuralDetector, img: np.ndarray) -> np.ndarray:
    """build_style_bank.py 的处理流程 + 绿色背景剥离。返回二值 96x96 模板。"""
    fh, fw = img.shape[:2]
    ix, iy = int(fw * 0.06), int(fh * 0.06)
    face = img[iy:fh - iy, ix:fw - ix]
    face = _strip_green(face)
    m = det._ink_mask(face)
    ys, xs = np.where(m > 0)
    if len(ys) == 0:
        return np.zeros((1, 1), np.uint8)
    crop = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ch, cw = crop.shape
    sc = 96.0 / float(max(ch, cw))
    nh, nw = max(1, int(ch * sc)), max(1, int(cw * sc))
    tpl = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
    _, tpl = cv2.threshold(tpl, 100, 255, cv2.THRESH_BINARY)
    return tpl


def main() -> None:
    if not os.path.isdir(SRC):
        print(f"[ERR] 缺少源目录: {SRC}")
        print(f"      请先把按 label 分类的 raw crops 放入，例如:")
        print(f"        {SRC}/1m/1.png, 2.png, ...")
        print(f"        {SRC}/1p/1.png, ...")
        sys.exit(1)

    os.makedirs(OUT, exist_ok=True)
    # 清旧
    for old in glob.glob(os.path.join(OUT, "*.png")):
        os.remove(old)

    det = StructuralDetector()  # 复用 _ink_mask
    n_total = 0
    n_lab = 0
    for label_dir in sorted(os.listdir(SRC)):
        d = os.path.join(SRC, label_dir)
        if not os.path.isdir(d):
            continue
        files = sorted(glob.glob(os.path.join(d, "*.png")))
        if not files:
            continue
        label = label_dir
        # 每个 label 取所有样本中**最清晰**的一帧作模板
        # （build_style_bank.py 同样每 label 取单张）
        def sharp(p: str) -> float:
            img = cv2.imread(p)
            if img is None:
                return 0.0
            return float(
                cv2.Laplacian(
                    cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F
                ).var()
            )

        files.sort(key=sharp, reverse=True)
        best = files[0]
        img = cv2.imread(best)
        tpl = _to_tpl(det, img)
        out_path = os.path.join(OUT, f"{label}.png")
        cv2.imwrite(out_path, tpl)
        n_total += 1
        n_lab += 1
        print(f"  {label}: {os.path.basename(best)} -> {out_path}")
    print(f"\n共生成 {n_total} 张 majsoul 模板（{n_lab} 类）-> {OUT}")


if __name__ == "__main__":
    main()