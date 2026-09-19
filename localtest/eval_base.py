"""P0 评测底座 · 打分与回归门禁。

读取 localtest/gt/shots.json，对每条 verified=true 的截图跑引擎并与人工标注比对：
  - 手牌（多重集）：精确匹配率 + 逐张准确率
  - 阶段 status、定缺 dingque：精确匹配
  - best（最优决策）：信息性比对（逻辑变更时允许更新 GT，不计入失败）
verified=false 的条目跳过（尚未人工校验，避免"自己测自己"的假绿灯）。

退出码：任一 verified 条目在手牌/阶段/定缺上不匹配 → 1（CI 回归门失败）；否则 0。

用法:
  py -3.10 localtest/eval_base.py            # 全量打分
  py -3.10 localtest/eval_base.py --strict   # best 不匹配也算失败
"""
import argparse
import contextlib
import io
import json
import os
import sys

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from engine.engine import Engine  # noqa: E402

SHOT_DIR = os.path.join(REPO, "localtest", "shots")
GT_PATH = os.path.join(REPO, "localtest", "gt", "shots.json")


def run_engine(img):
    """跑引擎并吞掉其调试 stdout（引擎每帧 print 大字典会污染报表）。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return json.loads(Engine().process(img).result)


def canon_mpsz(s):
    """把 '7z1m3m...' 规范化为排序后的代码列表（多重集）。"""
    if not s:
        return []
    codes = [s[i:i + 2] for i in range(0, len(s), 2)]
    return sorted(codes)


def tile_accuracy(gt_codes, det_codes):
    """逐张准确率：以多重集交集计。"""
    from collections import Counter
    g, d = Counter(gt_codes), Counter(det_codes)
    inter = sum((g & d).values())
    total = max(len(gt_codes), len(det_codes), 1)
    return inter, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="best 不匹配也判失败")
    args = ap.parse_args()

    if not os.path.exists(GT_PATH):
        print("GT 不存在，请先运行 gen_gt.py 并人工校验")
        sys.exit(2)
    with open(GT_PATH, encoding="utf-8") as f:
        gt = json.load(f)["shots"]

    n_verified = n_skipped = 0
    hand_exact = 0
    tile_hit = tile_tot = 0
    status_ok = dingque_ok = status_n = dingque_n = 0
    best_warn = 0
    failures = []

    for e in gt:
        if not e.get("verified"):
            n_skipped += 1
            continue
        n_verified += 1
        path = os.path.join(SHOT_DIR, e["file"])
        img = cv2.imread(path)
        if img is None:
            failures.append((e["file"], "无法读取图片"))
            continue
        # 每张截图独立对局帧：全新 Engine，防止上一帧投票窗口泄漏污染评分
        d = run_engine(img)

        gt_hand = canon_mpsz(e.get("hand", ""))
        det_hand = canon_mpsz(d.get("hand", ""))
        exact = (gt_hand == det_hand)
        # 逐张准确率仅在有可读牌面时计入（遮挡帧 GT 为空，正确输出"空"只算精确匹配）
        if gt_hand:
            inter, total = tile_accuracy(gt_hand, det_hand)
            tile_hit += inter
            tile_tot += total
        else:
            inter, total = (1, 1) if exact else (0, 1)
        if exact:
            hand_exact += 1

        s_match = (d.get("status", "") == e.get("status", ""))
        status_ok += int(s_match)
        status_n += 1

        dq_match = True
        if e.get("dingque") is not None:
            dingque_n += 1
            dq_match = (d.get("dingque") == e.get("dingque"))
            dingque_ok += int(dq_match)

        best_match = True
        if e.get("best"):
            best_match = (d.get("best", "") == e.get("best", ""))
            if not best_match:
                best_warn += 1

        if not exact or not s_match or not dq_match or (args.strict and not best_match):
            st = "OK" if s_match else "X(={})".format(d.get("status"))
            failures.append((
                e["file"],
                "hand{}({}/{}) status{} dq{} best{}".format(
                    "OK" if exact else "X", inter, total, st,
                    "OK" if dq_match else "X", "OK" if best_match else "X",
                ),
            ))

    print("=" * 60)
    print(f"已校验条目: {n_verified} | 跳过(未校验): {n_skipped}")
    if n_verified:
        print(f"手牌精确匹配 : {hand_exact}/{n_verified} = {100*hand_exact/n_verified:.1f}%")
        print(f"逐张牌准确率 : {tile_hit}/{tile_tot} = {100*tile_hit/max(tile_tot,1):.2f}%")
        print(f"阶段 status  : {status_ok}/{status_n} = {100*status_ok/max(status_n,1):.1f}%")
        print(f"定缺 dingque : {dingque_ok}/{dingque_n} = {100*dingque_ok/max(dingque_n,1):.1f}%")
        print(f"best 差异    : {best_warn} (信息性{'，strict计入失败' if args.strict else '，不计失败'})")
    if failures:
        print("\n--- 不匹配明细 ---")
        for fn, msg in failures:
            print(f"  {fn[:16]:16s} {msg}")
        print(f"\nRESULT: FAIL ({len(failures)} 条不匹配)")
        sys.exit(1)
    print("\nRESULT: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
