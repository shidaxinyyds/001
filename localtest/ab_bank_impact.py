# -*- coding: utf-8 -*-
"""A/B 实测：多平台 bank 共存对「旧平台是否回退」的影响。

SOP 的立项目标是「新平台 100%、旧平台零回退」。但多 bank 共存后，每帧的分类
被风格探针限定在它选中的那个 bank 内——**探针一旦选错，腾讯帧就会拿别家字模来认**，
这属于"接入新平台把旧平台拖回退"，必须能测出来，不能靠声称等价。

做法：同一批帧、同一套代码，只改两个变量——`_load_templates` 读的 bank 清单、
以及该帧有没有向 detector 声明平台：
  A 全 bank + 不声明平台（离线评测旧口径）
  B 只留 tencent（等价“接新平台之前”的旧世界）
  C 全 bank + 按帧声明平台（生产口径：用户在面板里选了平台）
  D C + 把 shushan 也纳入平台白名单（验证“未限平台的 bank 会串味”）
B 与其余各条的差值就是“多平台共存”这一件事本身的代价/收益，与算法改动无关。
bank 条数会在每轮开头自检（两轮一样说明补丁没生效，结论作废）。
只量识别层（`detect_hand_strip`），不进 Engine 的聚合/投票，所以数值与
`eval_base.py` 不完全等同；但 A/B 的差值仍然成立（两边同口径）。

    py -3.10 -X utf8 localtest/ab_bank_impact.py
输出：build/ab_bank.txt
"""
import collections
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition import tencent_grid_detector as TGD  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_base import canon_mpsz, tile_accuracy  # noqa: E402  复用底座口径

NEW_BANKS = {"shushan", "queshen", "tuyou", "jj", "weile"}
# 风格名 -> 平台 key（platforms.py 里的注册名）：生产上用户在面板里选平台，
# Engine 会把它推给 detector 做 bank 白名单；离线评测不声明就等于“允许串味”。
STYLE_OF = {"tencent": "tencent", "shushan": "shushan", "queshen": "gd_queshen",
            "tuyou": "tuyou", "jj": "jj", "weile": "weile"}
OUT = os.path.join(REPO, "build", "ab_bank.txt")


def load_cases():
    """返回 [(平台, 帧名, 绝对路径, GT 列表)]：腾讯底座 + 已钉的新素材。"""
    cases = []
    gt_path = os.path.join(HERE, "gt", "shots.json")
    with open(gt_path, encoding="utf-8") as fp:
        for e in json.load(fp)["shots"]:
            if not e.get("verified") or not canon_mpsz(e.get("hand", "")):
                continue
            p = os.path.join(HERE, "shots", e["file"])
            if os.path.exists(p):
                cases.append(("tencent", e["file"], p, canon_mpsz(e["hand"])))
    new_gt = os.path.join(HERE, "gt", "new_shots.json")
    if os.path.exists(new_gt):
        with open(new_gt, encoding="utf-8") as fp:
            for e in json.load(fp)["shots"]:
                p = os.path.join(REPO, "public", "0", e["file"])
                if os.path.exists(p):
                    cases.append((e["style"], e["file"], p, sorted(e["hand"])))
    return cases


def run(det, cases, declare=None):
    """declare: 风格→平台 key 的映射（None=不声明平台，等于离线评测旧口径）。"""
    res = {}
    for plat, name, path, gt in cases:
        img = cv2.imread(path)
        if img is None:
            continue
        det.set_platform_styles(declare(plat) if declare else None)
        got = sorted(l for _r, l, _s in (det.detect_hand_strip(img) or []))
        inter, total = tile_accuracy(gt, got)
        res[(plat, name)] = (inter, total, got == gt)
    return res


def summarize(res):
    hit = sum(i for i, _t, _e in res.values())
    tot = sum(t for _i, t, _e in res.values())
    exact = sum(1 for _i, _t, e in res.values() if e)
    per = collections.defaultdict(lambda: [0, 0])
    for (plat, _n), (i, t, _e) in res.items():
        per[plat][0] += i
        per[plat][1] += t
    return hit, tot, exact, per


def main():
    cases = load_cases()
    if not cases:
        print("没有可用 GT 帧")
        return 2
    all_banks = TGD.EXTRA_BANKS
    orig_wl = dict(TGD.STYLE_PLATFORM_WHITELIST)
    lines = [f"待测评测帧 {len(cases)}：" + "  ".join(
        f"{p}x{n}" for p, n in collections.Counter(c[0] for c in cases).items()), ""]

    runs = {}
    for title, disable, declare, wl_shushan in (
            ("A 全 bank + 不声明平台（离线评测旧口径）", set(), None, False),
            ("B 只留 tencent（等价接新平台之前的旧世界）", NEW_BANKS, None, False),
            ("C 全 bank + 按帧声明平台（生产口径）", set(), STYLE_OF.get, False),
            ("D C + 把 shushan 也限平台", set(), STYLE_OF.get, True)):
        TGD.EXTRA_BANKS = tuple((m, s) for m, s in all_banks if s not in disable)
        TGD.STYLE_PLATFORM_WHITELIST = dict(
            orig_wl,
            **({"shushan": ("shushan", "zj_sichuan")} if wl_shushan else {}))
        det = TencentGridDetector()
        n_cores = len(det._cores)
        runs[title] = run(det, cases, declare)
        lines.append(f"{title}：模板条目 {n_cores}")
        hit, tot, exact, per = summarize(runs[title])
        lines.append(f"  逐张 {hit}/{tot} = {100.0 * hit / tot:.2f}%   "
                     f"整帧精确 {exact}/{len(runs[title])} = {100.0 * exact / len(runs[title]):.1f}%")
        lines.append("  逐平台 " + "  ".join(
            f"{s} {h}/{n}={100.0 * h / n:.1f}%" for s, (h, n) in sorted(per.items())))
        lines.append("")
    TGD.STYLE_PLATFORM_WHITELIST = orig_wl

    b_title = "B 只留 tencent（等价接新平台之前的旧世界）"
    ref = runs[b_title]
    for title, res in runs.items():
        if title == b_title:
            continue
        worse = [(k, res[k], ref[k]) for k in res if k in ref and res[k][0] < ref[k][0]]
        better = [(k, res[k], ref[k]) for k in res if k in ref and res[k][0] > ref[k][0]]
        tw = sum(1 for (p, _n) in res if p == "tencent" and (p, _n) in ref
                 and res[(p, _n)][0] < ref[(p, _n)][0])
        lines.append(f"对照 {b_title[:1]}：{title}")
        lines.append(f"  比旧世界差的帧 {len(worse)}（其中腾讯底座 {tw}）；"
                     f"比旧世界好的帧 {len(better)}（新 bank 的真实收益）")
        for k, x, y in worse[:8]:
            lines.append(f"    差 {k[0]:8} {k[1][:26]:26} {x[0]}/{x[1]} -> 旧世界 {y[0]}/{y[1]}")
        for k, x, y in better[:8]:
            lines.append(f"    好 {k[0]:8} {k[1][:26]:26} {x[0]}/{x[1]} -> 旧世界 {y[0]}/{y[1]}")
        lines.append("")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
