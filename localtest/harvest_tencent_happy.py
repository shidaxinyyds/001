"""从干净的腾讯欢乐麻将截图中 harvest 13 张可见手牌模板。

用法: python localtest/harvest_tencent_happy.py
"""
import os
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
IMG = os.path.join(HERE, "game_clean.jpg")
OUT = os.path.join(HERE, "tiles", "tencent_happy")

# 根据 structural detect 输出 + 手牌等宽分布推算的 13 张牌位置。
# 检测到的 11 张位于 x=234 + i*103 (i=0..7, 然后跳过 9s, 再 i=9..11)。
# 补齐首尾的 7z 与 9s。
TW, TH = 104, 145
X0, Y0 = 131, 700
LABELS = [
    "7z",   # 红中
    "1s",   # 一条
    "2s",
    "3s",
    "4s",
    "5s",
    "6s",
    "7s",
    "8s",
    "9s",
    "2p",   # 目测两筒
    "8p",   # 目测八筒
    "3m",   # 三万
]


def main():
    img = cv2.imread(IMG)
    if img is None:
        raise FileNotFoundError(IMG)
    os.makedirs(OUT, exist_ok=True)
    for i, lab in enumerate(LABELS):
        x = X0 + i * TW
        y = Y0
        tile = img[y:y + TH, x:x + TW]
        path = os.path.join(OUT, f"{lab}.png")
        cv2.imwrite(path, tile)
        print(f"[{i:2d}] {lab}: x={x} y={y} w={TW} h={TH} -> {path}")
    print(f"\n共 harvest {len(LABELS)} 张到 {OUT}")


if __name__ == "__main__":
    main()
