# -*- coding: utf-8 -*-
"""P4 再训练闭环 · 驱动器（采集→训练→评估→晋升/回滚）。

流程：
  1) 度量现任生产模型的合成牌河 joint_acc（基线）与合成手牌 joint_acc（守卫）。
  2) 训练「手牌+牌河混合」模型，导出到 staging（不覆盖生产）。
  3) 度量候选模型的牌河/手牌 joint_acc。
  4) 仅当 牌河提升 >= --margin 且 手牌不回退 时晋升：备份现任到 *.bak，再用候选覆盖生产。
     否则保留 staging、不动生产，并打印原因。

用法:
  py -3.10 localtest/retrain_loop.py --epochs 12 --train-samples 512 --river-frac 0.5
  py -3.10 localtest/retrain_loop.py --dry-run        # 只训练+评估，不晋升
"""
import argparse
import json
import os
import shutil
import sys
import time

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
sys.path.insert(0, REPO)

from localtest.yolo.dataset import (  # noqa: E402
    STRIP_W, STRIP_H, load_all_tile_banks, generate_mahjong_strip,
)
from localtest.yolo.train import train_and_export  # noqa: E402
from localtest.eval_river import (  # noqa: E402
    RiverYOLO, iou, synth_river_metrics, PROD_ONNX,
)

STAGING_ONNX = os.path.join(REPO, "localtest", "yolo", "staging", "yolo_mahjong.onnx")
STAGING_CKPT = os.path.join(REPO, "localtest", "yolo", "staging", "yolo_mahjong.pt")


def synth_hand_metrics(model_path, num=80, seed=11):
    """合成手牌行 joint 精度：防止为牌河牺牲手牌（守卫指标）。"""
    import random
    np.random.seed(seed)
    random.seed(seed)
    bank = load_all_tile_banks(REPO)
    det = RiverYOLO(model_path)
    gt_total = matched_same = 0
    for _ in range(num):
        strip, boxes = generate_mahjong_strip(bank, STRIP_W, STRIP_H)
        dets = det.detect(strip)
        gt_total += len(boxes)
        used = set()
        for d in sorted(dets, key=lambda x: -x[4]):
            best, bi = 0.5, -1
            for gi, g in enumerate(boxes):
                if gi in used:
                    continue
                v = iou(d, [g[1], g[2], g[3] - g[1], g[4] - g[2]])
                if v > best:
                    best, bi = v, gi
            if bi >= 0:
                used.add(bi)
                if d[5] == boxes[bi][0]:
                    matched_same += 1
    return {"num_strips": num, "joint_acc": round(matched_same / max(1, gt_total), 4)}


def promote(incumbent, candidate, margin, dry_run):
    """晋升判定与执行。返回 (promoted: bool, reason: str)。"""
    base_r = synth_river_metrics(incumbent, 120)
    base_h = synth_hand_metrics(incumbent, 80)
    print("\n[基线·现任生产模型]")
    print("  river:", json.dumps(base_r, ensure_ascii=False))
    print("  hand :", json.dumps(base_h, ensure_ascii=False))

    cand_r = synth_river_metrics(candidate, 120)
    cand_h = synth_hand_metrics(candidate, 80)
    print("\n[候选·staging 模型]")
    print("  river:", json.dumps(cand_r, ensure_ascii=False))
    print("  hand :", json.dumps(cand_h, ensure_ascii=False))

    river_gain = cand_r["joint_acc"] - base_r["joint_acc"]
    hand_delta = cand_h["joint_acc"] - base_h["joint_acc"]

    report = {
        "ts": int(time.time()),
        "baseline": {"river": base_r, "hand": base_h},
        "candidate": {"river": cand_r, "hand": cand_h},
        "river_gain": round(river_gain, 4),
        "hand_delta": round(hand_delta, 4),
    }
    rpt_path = os.path.join(REPO, "localtest", "yolo", "staging", "loop_report.json")
    os.makedirs(os.path.dirname(rpt_path), exist_ok=True)
    with open(rpt_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nriver joint_acc {base_r['joint_acc']:.4f} -> {cand_r['joint_acc']:.4f} "
          f"(Δ {river_gain:+.4f}, margin {margin})")
    print(f"hand  joint_acc {base_h['joint_acc']:.4f} -> {cand_h['joint_acc']:.4f} (Δ {hand_delta:+.4f})")

    if river_gain < margin:
        return False, f"牌河提升 {river_gain:+.4f} < margin {margin}，不晋升"
    if hand_delta < -0.02:
        return False, f"手牌回退 {hand_delta:+.4f} 超阈，不晋升"
    if dry_run:
        return False, "dry-run：达标但不实际晋升"

    bak = incumbent + ".bak"
    shutil.copy2(incumbent, bak)
    shutil.copy2(candidate, incumbent)
    return True, f"已晋升：现任备份->{os.path.basename(bak)}，候选覆盖生产"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--train-samples", type=int, default=512)
    ap.add_argument("--river-frac", type=float, default=0.5)
    ap.add_argument("--margin", type=float, default=0.03)
    ap.add_argument("--warm", action="store_true", help="从现任 ckpt 热启动")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(STAGING_ONNX), exist_ok=True)
    incumbent_ckpt = os.path.join(REPO, "localtest", "yolo", "yolo_mahjong.pt")
    init_from = incumbent_ckpt if (args.warm and os.path.isfile(incumbent_ckpt)) else None

    print(f"=== P4 再训练闭环 | epochs={args.epochs} samples={args.train_samples} "
          f"river_frac={args.river_frac} warm={bool(init_from)} dry_run={args.dry_run} ===")
    t0 = time.time()
    # 训练引擎会 print 大量日志；闭环里保留（--epochs 少时可读）
    train_and_export(
        epochs=args.epochs, train_samples=args.train_samples, val_samples=64,
        river_frac=args.river_frac, out_onnx=STAGING_ONNX, ckpt=STAGING_CKPT,
        init_from=init_from, verbose=True,
    )
    print(f"\n训练+导出耗时 {time.time()-t0:.1f}s")

    if not os.path.isfile(PROD_ONNX):
        print("生产 onnx 不存在，无法比较基线；仅产出 staging。")
        return
    ok, reason = promote(PROD_ONNX, STAGING_ONNX, args.margin, args.dry_run)
    print(f"\n晋升结果: {'PROMOTED' if ok else 'KEPT'} -> {reason}")


if __name__ == "__main__":
    main()
