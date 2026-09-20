# -*- coding: utf-8 -*-
"""构建蜀山四川麻将模板 bank（多平台适配流程的第 2 步）。

输入：localtest/shots_shushan/*.jpg（真机截图，手牌 GT 已人工核对）
输出：
  - android/app/src/main/python/recognition/templates_shushan.py  （纯 Python，Chaquopy 免文件 IO）
  - android/app/src/main/python/recognition/images/shushan_exact/*.png（调试/降级备份）

模板键约定：基础标签 或 标签#变体（如 7s#b = 带蓝色「缺」角标变体）。
检测器加载时 '#' 后后缀会被剥离，同一标签允许多个变体模板参与打分。

用法: py -3.10 localtest/build_shushan_bank.py
"""
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

SHOTS = os.path.join(REPO, "localtest", "shots_shushan")
OUT_MOD = os.path.join(PYROOT, "recognition", "templates_shushan.py")
OUT_PNG = os.path.join(PYROOT, "recognition", "images", "shushan_exact")

# (截图, x, y, w, h, 模板键)  —— 手牌行样本坐标来自 diag_shushan 实测 rect，
# 明牌区样本坐标来自 harvest_shushan montage 人工读图定标。
JOBS = [
    # --- 手牌行（高清 ~112x151） ---
    ("s1.jpg", 522, 739, 112, 151, "7z"),      # 赖标红中（本游戏红中恒带赖角标）
    ("s1.jpg", 634, 741, 111, 149, "8m"),
    ("s1.jpg", 968, 739, 112, 151, "9m"),
    ("s1.jpg", 1080, 739, 112, 151, "7p"),
    ("s1.jpg", 1526, 739, 112, 151, "9p"),
    ("s2.jpg", 1526, 739, 112, 151, "7s#b"),   # 带「缺」角标的七条变体
    ("s2.jpg", 1661, 741, 111, 149, "7p#2"),
    ("s2.jpg", 280, 734, 70, 100, "7m"),       # 左下明牌碰：七萬（正立）
    ("s5.jpg", 201, 739, 112, 151, "7z#2"),
    ("s5.jpg", 313, 741, 111, 149, "2m"),
    ("s5.jpg", 536, 741, 111, 149, "3m"),
    ("s5.jpg", 759, 739, 112, 151, "4m"),
    ("s5.jpg", 1094, 739, 112, 151, "5m"),     # 伍萬：旧腾讯模板误读为 3m 的本尊
    ("s5.jpg", 1317, 739, 112, 151, "7s"),     # 七条：旧腾讯模板误读为 5s 的本尊
    # --- 明牌区（弃牌/碰牌，~70px 小样本，作补充变体） ---
    ("s1.jpg", 1430, 600, 70, 90, "9m#2"),
    ("s1.jpg", 1150, 200, 65, 70, "2m#2"),
    ("s1.jpg", 755, 490, 70, 70, "7s#3"),
    ("s4.jpg", 760, 25, 70, 70, "8m#2"),
    ("s4.jpg", 825, 25, 70, 70, "4p"),
    ("s4.jpg", 890, 25, 70, 70, "3p"),
    ("s4.jpg", 955, 25, 70, 70, "1p"),
    ("s4.jpg", 1020, 25, 70, 70, "8p"),
    ("s4.jpg", 1085, 25, 70, 70, "9p#2"),
    ("s4.jpg", 1150, 25, 70, 70, "5s"),
    ("s4.jpg", 1180, 610, 70, 80, "5m#2"),
    ("s4.jpg", 1245, 610, 70, 80, "6m"),
    ("s4.jpg", 1310, 610, 70, 80, "6s"),
    ("s4.jpg", 1375, 610, 70, 80, "3m#2"),
    ("s4.jpg", 1440, 610, 70, 80, "4m#2"),
    # --- s4 发牌过渡态（牌面 ~89x120 小尺度+模糊，模板对尺度敏感，
    #     按变体收入同一标签；新尺度样本到来时同样追加，不改算法） ---
    ("s4.jpg", 376, 763, 89, 120, "7z#s4"),
    ("s4.jpg", 465, 763, 89, 120, "2m#s4"),
    ("s4.jpg", 643, 762, 90, 121, "3m#s4"),
    ("s4.jpg", 822, 763, 89, 120, "4m#s4"),
    ("s4.jpg", 1089, 763, 89, 120, "5m#s4"),
    ("s4.jpg", 1268, 763, 89, 120, "7s#s4"),
]


def main():
    os.makedirs(OUT_PNG, exist_ok=True)
    bgr_map = {}
    for fname, x, y, w, h, key in JOBS:
        img = cv2.imread(os.path.join(SHOTS, fname))
        crop = img[y:y + h, x:x + w]
        face = TencentGridDetector.extract_face(crop)  # (120,80,3)
        bgr_map[key] = face
        cv2.imwrite(os.path.join(OUT_PNG, f"{key}.png"), face)

    gray_map = {k: cv2.cvtColor(v, cv2.COLOR_BGR2GRAY) for k, v in bgr_map.items()}
    with open(OUT_MOD, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write('"""蜀山四川麻将牌面模板（localtest/build_shushan_bank.py 生成，勿手改）。\n\n')
        f.write("键名 '#' 后为同一标签的视觉变体后缀（角标/来源差异），检测器按基础标签聚合。\n\"\"\"\n")
        f.write("TEMPLATES_BGR = ")
        f.write(repr({k: v.tolist() for k, v in sorted(bgr_map.items())}))
        f.write("\n\nTEMPLATES_GRAY = ")
        f.write(repr({k: v.tolist() for k, v in sorted(gray_map.items())}))
        f.write("\n")
    labels = sorted({k.split('#')[0] for k in bgr_map})
    print(f"written {OUT_MOD}: {len(bgr_map)} templates, {len(labels)} labels")
    print("labels:", " ".join(labels))


if __name__ == "__main__":
    main()
