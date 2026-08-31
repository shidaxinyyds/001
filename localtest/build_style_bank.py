"""构建「牌面风格模板库」：把每种风格的 34 张牌归一化成小尺寸二值墨迹模板。

为什么需要它：
  筒/条可以靠"几何不变量"（数圆心/数竹棒）跨风格稳健识别，但
  万牌数字(一~九)和字牌(東南西北中發白)是**汉字**：不同字体的同一字
  形状差异，和不同字之间的差异是同一量级——实测硬 IoU 与软相似度
  top-1 都只有 50~60%，且分差极小(margin<0.05)，无法可靠判别。
  而一款麻将 App 的牌面美术是固定的，因此"同风格精确模板匹配"
  是唯一能达到可用精度的办法（同风格 IoU 普遍 0.85+）。

产物：recognition/images/styles/<style>/<label>.png（40x56 二值墨迹图）

用法: python localtest/build_style_bank.py
"""
import os
import sys
import glob
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import cv2
import numpy as np

from recognition.structural import StructuralDetector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(REPO, "android", "app", "src", "main", "python",
                   "recognition", "images", "styles")

# 模板尺寸：足够保留字形结构，又足够小（34 张 × N 风格 的内存/体积可控）
TW, TH = 40, 56

# 风格名 -> (目录, 真值解析函数)
def _truth_plain(p):
    return os.path.basename(p)[:-4]


def _truth_screenshot(p):
    return os.path.basename(p)[:-4].split("_", 1)[1]


def _truth_duma(p):
    lab = os.path.basename(p)[:-4]
    # duma520 下载器把 中/白 的文件名写反了
    return {"5z": "7z", "7z": "5z"}.get(lab, lab)


STYLES = [
    ("duma520", os.path.join(HERE, "tiles", "duma520"), _truth_duma),
    ("wikipedia", os.path.join(HERE, "real_tiles"), _truth_plain),
    ("app_shot", os.path.join(HERE, "tiles", "screenshot"), _truth_screenshot),
]


def to_bgr(img):
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3].astype(np.float32) / 255.0
        white = np.ones((img.shape[0], img.shape[1], 3), np.uint8) * 255
        f = img[:, :, :3].astype(np.float32)
        return (f * a[:, :, None] + white * (1.0 - a)[:, :, None]).astype(np.uint8)
    return img


def main():
    det = StructuralDetector()
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    total = 0
    for style, src, truth_fn in STYLES:
        files = sorted(glob.glob(os.path.join(src, "*.png")))
        if not files:
            print(f"[skip] {style}: 无素材 ({src})")
            continue
        dst = os.path.join(OUT, style)
        os.makedirs(dst, exist_ok=True)
        n = 0
        for p in files:
            label = truth_fn(p)
            img = to_bgr(cv2.imread(p, cv2.IMREAD_UNCHANGED))
            if img is None:
                continue
            fh, fw = img.shape[:2]
            ix, iy = int(fw * 0.06), int(fh * 0.06)
            face = img[iy:fh - iy, ix:fw - ix]
            m = det._ink_mask(face)
            # 裁到墨迹外接框再等比缩放：去掉风格间的留白差异
            ys, xs = np.where(m > 0)
            if len(ys) == 0:
                tpl = np.zeros((TH, TW), np.uint8)
            else:
                crop = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                tpl = cv2.resize(crop, (TW, TH), interpolation=cv2.INTER_AREA)
                _, tpl = cv2.threshold(tpl, 100, 255, cv2.THRESH_BINARY)
            cv2.imwrite(os.path.join(dst, f"{label}.png"), tpl)
            n += 1
        total += n
        print(f"[ok] {style}: {n} 张 -> {dst}")
    print(f"\n共生成 {total} 张模板 -> {OUT}")


if __name__ == "__main__":
    main()
