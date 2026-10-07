# -*- coding: utf-8 -*-
"""无监督给一批截图分平台（不依赖任何已挂 bank），再由人工给每组认一次身份。

为什么要有这个工具：风格探针回答的是“这帧牌面最像哪个**已知** bank”，它对从没见过
的平台必然给出**错误但自信**的答案（会被归到最像的旧风格里）。于是“这批素材里有几
个平台、哪些帧属于同一个平台”这个问题，用探针是从原理上答不了的——必须先有一个
**不认得也能分组**的手段。这里用整帧外观（缩略灰度 + 主色调）做余弦相似度聚组：同
一平台的桌布颜色、按钮排布、牌山透视几乎一致，不同平台差异明显，因此分组不需要知道
名字。名字由人看每组一张代表帧来定（每组一次人工判断，而不是每帧一次）。

这条链的产物是 build/frame_clusters_<tag>/：
  groups.txt        每组：成员序号、内聚度、代表帧
  groups_sheet_NN.png  每组一张代表帧拼成的联系表（人只看这几张图就能标完）
  labels.txt        人工填写的 组号=平台key（下一切片的输入）

用法：
    py -3.10 -X utf8 localtest/cluster_frames.py --src public/1
"""
import argparse
import collections
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import platforms  # noqa: E402

# 组内相似度门限：低于它就不合并。0.86 是在 public/1 上标定的——再松会把
# 「途游」与「指尖四川」两张蓝桌合成一组（它们牌山透视确实像），再紧会把同一
# 平台的不同对局阶段（有弹窗/无弹窗）拆开。改这个数必须重跑分组看组数变化。
SIM_MIN = 0.86
THUMB = (64, 36)          # 布局指纹的下采样尺寸（灰度）
HIST_BIN = (8, 8, 8)      # 主色调直方图


def feature(img):
    """整帧外观 = 缩略灰度布局 + HSV 主色调直方图，两段各自 L2 归一后拼接。

    只用直方图会把“蓝桌但牌面不同”的两平台并一组；只用缩略图会被弹窗/结算动画
    带偏。两段拼起来实测能分开（见 groups.txt 的内聚度）。
    """
    small = cv2.resize(img, THUMB, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = (gray - gray.mean()) / (gray.std() + 1e-6)
    hsv = cv2.cvtColor(cv2.resize(img, (64, 64)), cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, list(HIST_BIN),
                        [0, 180, 0, 256, 0, 256]).flatten().astype(np.float32)
    hist = np.log1p(hist)
    hist /= (np.linalg.norm(hist) + 1e-6)
    v = np.concatenate([gray.flatten() / np.sqrt(gray.size), hist])
    return v / (np.linalg.norm(v) + 1e-6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO, "public", "1"))
    ap.add_argument("--sim-min", type=float, default=SIM_MIN)
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    tag = os.path.basename(src) or "root"
    out_dir = os.path.join(REPO, "build", f"frame_clusters_{tag}")
    os.makedirs(out_dir, exist_ok=True)

    files = sorted(f for f in os.listdir(src)
                   if f.lower().endswith((".jpg", ".jpeg", ".png")))
    feats, shapes, ok = [], [], []
    for f in files:
        img = cv2.imread(os.path.join(src, f))
        if img is None:
            continue
        feats.append(feature(img))
        shapes.append(img.shape[:2])
        ok.append(f)
    if not ok:
        print("无可读帧")
        return 2
    X = np.stack(feats)

    # 贪心聚组：按未分配帧依次开新组，吸收所有相似度达标的帧。帧数 141 量级，
    # O(n^2) 完全够，不必引 sklearn 的层次聚类（多一个依赖不如多写十行）。
    assigned = {}
    groups = []
    for i in range(len(ok)):
        if i in assigned:
            continue
        sims = X @ X[i]
        members = [j for j in range(len(ok)) if j != i and sims[j] >= a.sim_min]
        assigned[i] = len(groups)
        for j in members:
            if j not in assigned:
                assigned[j] = len(groups)
        # 内聚度：组内成员对代表帧的平均相似度（1.0=完全一致，越接近门限越杂）
        coh = float(np.mean([sims[j] for j in members])) if members else 1.0
        groups.append({"rep": i, "members": [i] + sorted(members), "coh": coh})

    # 代表帧联系表：一屏 12 组，人只看这几张图就能把组号标成平台
    cell_w, cols = 480, 4
    order = sorted(range(len(groups)), key=lambda g: -len(groups[g]["members"]))
    per = cols * 3
    n_sheets = 0
    for start in range(0, len(order), per):
        chunk = order[start:start + per]
        h0 = int(round(cell_w * shapes[groups[chunk[0]]["rep"]][0]
                       / shapes[groups[chunk[0]]["rep"]][1]))
        canvas = np.full((3 * h0, cols * cell_w, 3), 40, np.uint8)
        for k, g in enumerate(chunk):
            img = cv2.imread(os.path.join(src, ok[groups[g]["rep"]]))
            r, c = divmod(k, cols)
            y0, x0 = r * h0, c * cell_w
            small = cv2.resize(img, (cell_w, min(h0, int(round(
                cell_w * img.shape[0] / img.shape[1])))))
            canvas[y0:y0 + small.shape[0], x0:x0 + cell_w] = small
            cv2.rectangle(canvas, (x0, y0), (x0 + 240, y0 + 66), (0, 0, 0), -1)
            cv2.putText(canvas, f"G{g + 1} n={len(groups[g]['members'])}",
                        (x0 + 6, y0 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)
            cv2.putText(canvas, f"coh={groups[g]['coh']:.2f}",
                        (x0 + 6, y0 + 54), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 255), 2)
        cv2.imwrite(os.path.join(out_dir, f"groups_sheet_{n_sheets + 1:02d}.png"),
                    canvas)
        n_sheets += 1

    with open(os.path.join(out_dir, "groups.txt"), "w", encoding="utf-8") as fp:
        fp.write(f"# 源 {src}  共 {len(ok)} 帧 → {len(groups)} 组  门限 sim>={a.sim_min}\n")
        fp.write("# 组号 = 无监督分组，与平台名无关；名字看 groups_sheet_*.png 人工标\n\n")
        for g in sorted(range(len(groups)), key=lambda x: -len(groups[x]["members"])):
            gr = groups[g]
            res = collections.Counter(f"{shapes[j][1]}x{shapes[j][0]}"
                                      for j in gr["members"])
            fp.write(f"G{g + 1}: {len(gr['members'])} 帧  内聚 {gr['coh']:.3f}  "
                     f"分辨率 {dict(res)}  代表 #{gr['rep'] + 1} {ok[gr['rep']]}\n")
            fp.write("   " + " ".join(f"#{j + 1}" for j in gr["members"]) + "\n")
            fp.write("   " + " ".join(ok[j] for j in gr["members"]) + "\n\n")

    if not os.path.exists(os.path.join(out_dir, "labels.txt")):
        with open(os.path.join(out_dir, "labels.txt"), "w", encoding="utf-8") as fp:
            fp.write("# 人工标注：每行 `组号 平台key`（key 取自 platforms.PLATFORMS，"
                     "整批里没见过的写 unknown）\n")
            fp.write("# 未标的组不参与收割/评测。合法 key：\n")
            fp.write("#   " + " ".join(platforms.PLATFORMS) + "\n\n")
            for g in sorted(range(len(groups)), key=lambda x: -len(groups[x]["members"])):
                fp.write(f"G{g + 1} \n")

    print(f"{len(ok)} 帧 → {len(groups)} 组，联系表 {n_sheets} 屏 → {out_dir}")
    for g in sorted(range(len(groups)), key=lambda x: -len(groups[x]["members"])):
        print(f"  G{g + 1}: {len(groups[g]['members'])} 帧 coh={groups[g]['coh']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
