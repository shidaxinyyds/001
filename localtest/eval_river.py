# -*- coding: utf-8 -*-
"""P4 再训练闭环 · 牌河识别度量（闭环的反馈信号）。

两部分：
  1) 合成牌河行精度（全自动 GT，可复跑）：对 generate_river_row 生成的牌河行跑 YOLO，
     按 IoU 贪心匹配，报告 召回率 / 精确率 / 命中同类率。这是再训练的主判据。
  2) 真实截图牌河记录：对 localtest/shots 跑现有轮廓法与 YOLO 法，记录检出张数（无 GT，仅基线参考）。

用法:
  py -3.10 localtest/eval_river.py                       # 用生产 onnx
  py -3.10 localtest/eval_river.py --model <path.onnx>   # 指定模型（如 staging）
  py -3.10 localtest/eval_river.py --num 120 --json out.json
退出码：主判据(命中同类率*召回) < --floor 时返回 1（供闭环判定是否达标）。
"""
import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
sys.path.insert(0, REPO)
from localtest.yolo.dataset import (  # noqa: E402
    CLASSES, STRIP_W, STRIP_H, load_all_tile_banks, generate_river_row,
)

PROD_ONNX = os.path.join(PYROOT, "recognition", "models", "yolo_mahjong.onnx")
SHOT_DIR = os.path.join(REPO, "localtest", "shots")


class RiverYOLO:
    def __init__(self, model_path, conf_thresh=0.35, nms_thresh=0.35):
        if not os.path.isfile(model_path):
            raise FileNotFoundError(model_path)
        self.net = cv2.dnn.readNetFromONNX(model_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.conf = conf_thresh
        self.nms = nms_thresh

    def detect(self, strip):
        h, w = strip.shape[:2]
        blob = cv2.dnn.blobFromImage(strip, 1.0 / 255.0, (STRIP_W, STRIP_H), swapRB=True)
        self.net.setInput(blob)
        preds = self.net.forward()[0].transpose(1, 0)  # (2100,38)
        boxes, scores, cls = preds[:, :4], preds[:, 4:], None
        maxs = np.max(scores, axis=1)
        argmax = np.argmax(scores, axis=1)
        m = maxs >= self.conf
        if not np.any(m):
            return []
        bx, sc, cid = boxes[m], maxs[m], argmax[m]
        sx, sy = w / float(STRIP_W), h / float(STRIP_H)
        cand = []
        for b, s, c in zip(bx, sc, cid):
            cx, cy, bw, bh = b
            cand.append([int((cx - bw / 2) * sx), int((cy - bh / 2) * sy),
                         int(bw * sx), int(bh * sy), float(s), int(c)])
        idx = cv2.dnn.NMSBoxes([d[:4] for d in cand], [d[4] for d in cand],
                               self.conf, self.nms)
        out = []
        if len(idx) > 0:
            for i in np.array(idx).flatten():
                out.append(cand[i])
        return out


def iou(a, b):
    ax1, ay1, aw, ah = a[:4]
    bx1, by1, bw, bh = b[:4]
    ax2, ay2, bx2, by2 = ax1 + aw, ay1 + ah, bx1 + bw, by1 + bh
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def synth_river_metrics(model_path, num, seed=7):
    np.random.seed(seed)
    import random
    random.seed(seed)
    bank = load_all_tile_banks(REPO)
    det = RiverYOLO(model_path)
    gt_total = det_total = matched = matched_same_cls = 0
    for _ in range(num):
        strip, boxes = generate_river_row(bank, STRIP_W, STRIP_H)
        dets = det.detect(strip)
        gt_total += len(boxes)
        det_total += len(dets)
        used = set()
        # 按置信度降序贪心匹配
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
                matched += 1
                if d[5] == boxes[bi][0]:
                    matched_same_cls += 1
    recall = matched / max(1, gt_total)
    precision = matched / max(1, det_total)
    cls_acc = matched_same_cls / max(1, matched)
    joint = matched_same_cls / max(1, gt_total)  # 端到端：GT 牌被正确识别同类比例
    return {
        "num_strips": num, "gt_tiles": gt_total, "det_tiles": det_total,
        "recall": round(recall, 4), "precision": round(precision, 4),
        "cls_acc": round(cls_acc, 4), "joint_acc": round(joint, 4),
    }


def real_shot_log():
    """记录现有轮廓法与 YOLO 法在真实截图上的牌河检出张数（无 GT，仅基线参考）。"""
    from recognition.tencent_grid_detector import TencentGridDetector
    from engine.engine import detect_river_discards
    tgd = TencentGridDetector()
    contour_counts = []
    for p in sorted(glob.glob(os.path.join(SHOT_DIR, "*.jpg"))):
        img = cv2.imread(p)
        riv = detect_river_discards(img, tgd, mode="sc_hz")
        contour_counts.append(len(riv))
    return {"shots": len(contour_counts),
            "contour_river_total": sum(contour_counts),
            "contour_river_mean": round(sum(contour_counts) / max(1, len(contour_counts)), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=PROD_ONNX)
    ap.add_argument("--num", type=int, default=120)
    ap.add_argument("--json", default="")
    ap.add_argument("--floor", type=float, default=0.0, help="joint_acc 达标下限")
    ap.add_argument("--no-real", action="store_true")
    args = ap.parse_args()

    print(f"[eval_river] model = {args.model}")
    synth = synth_river_metrics(args.model, args.num)
    print("合成牌河精度:", json.dumps(synth, ensure_ascii=False))
    result = {"model": args.model, "synth": synth}
    if not args.no_real:
        real = real_shot_log()
        print("真实截图牌河:", json.dumps(real, ensure_ascii=False))
        result["real"] = real
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    ok = synth["joint_acc"] >= args.floor
    print(f"joint_acc={synth['joint_acc']:.4f} floor={args.floor:.4f} -> {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
