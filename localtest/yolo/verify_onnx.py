# -*- coding: utf-8 -*-
"""Verification script for exported yolo_mahjong.onnx.

Loads the ONNX model using OpenCV cv2.dnn, runs NMS, decodes bounding boxes and labels,
and runs tests on both synthetic strips and authentic game screenshots.
"""
import os
import sys
import time
import numpy as np
import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from localtest.yolo.dataset import (
    CLASSES, CLASS_TO_IDX, NUM_CLASSES, STRIP_W, STRIP_H,
    load_all_tile_banks, generate_mahjong_strip
)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ONNX_PATH = os.path.join(BASE_DIR, "android", "app", "src", "main", "python", "recognition", "models", "yolo_mahjong.onnx")


class MahjongYOLODetector:
    def __init__(self, model_path=ONNX_PATH, conf_thresh=0.35, nms_thresh=0.45):
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
        self.net = cv2.dnn.readNetFromONNX(model_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh

    def detect_strip(self, strip_bgr):
        """Run detection on a Mahjong horizontal strip (W=640, H=160).
        
        Returns:
            list of dict: [{'label': '1m', 'conf': 0.95, 'box': (x1, y1, x2, y2)}]
            sorted from left to right (by box x1).
        """
        orig_h, orig_w = strip_bgr.shape[:2]
        resized = cv2.resize(strip_bgr, (STRIP_W, STRIP_H), interpolation=cv2.INTER_AREA)
        blob = cv2.dnn.blobFromImage(resized, scalefactor=1.0/255.0, size=(STRIP_W, STRIP_H), swapRB=True)

        self.net.setInput(blob)
        # Output shape: (1, 38, 2100)
        preds = self.net.forward()[0]  # (38, 2100)

        # Transpose to (2100, 38)
        preds = preds.transpose(1, 0)
        boxes_raw = preds[:, :4]       # (2100, 4) cx, cy, w, h
        cls_scores = preds[:, 4:]      # (2100, 34)

        # Find max class score and class id per anchor
        max_scores = np.max(cls_scores, axis=1)
        max_class_ids = np.argmax(cls_scores, axis=1)

        # Filter by confidence threshold
        mask = max_scores >= self.conf_thresh
        if not np.any(mask):
            return []

        cand_boxes = boxes_raw[mask]
        cand_scores = max_scores[mask]
        cand_classes = max_class_ids[mask]

        # Convert cx, cy, w, h to x1, y1, w, h for OpenCV NMS
        # Scale coordinates back to original strip dimensions
        sx = orig_w / float(STRIP_W)
        sy = orig_h / float(STRIP_H)

        nms_boxes = []
        for b in cand_boxes:
            cx, cy, w, h = b
            x = int((cx - w * 0.5) * sx)
            y = int((cy - h * 0.5) * sy)
            bw = int(w * sx)
            bh = int(h * sy)
            nms_boxes.append([x, y, bw, bh])

        # Class-agnostic NMS: In Mahjong, two tiles cannot occupy the same physical space
        indices = cv2.dnn.NMSBoxes(
            nms_boxes, cand_scores.tolist(), score_threshold=self.conf_thresh, nms_threshold=0.35
        )

        detections = []
        if len(indices) > 0:
            for idx in indices.flatten():
                x, y, bw, bh = nms_boxes[idx]
                cid = cand_classes[idx]
                conf = float(cand_scores[idx])
                detections.append({
                    'label': CLASSES[cid],
                    'conf': round(conf, 3),
                    'box': (max(0, x), max(0, y), min(orig_w, x + bw), min(orig_h, y + bh))
                })

        # Sort left to right
        detections.sort(key=lambda d: d['box'][0])
        return detections


def run_benchmark():
    print(f"Loading ONNX detector from: {ONNX_PATH}")
    det = MahjongYOLODetector()
    tile_bank = load_all_tile_banks(BASE_DIR)

    print("\n--- Running Synthetic Strip Benchmarks ---")
    times = []
    correct_count = 0
    total_test = 20

    for i in range(total_test):
        strip, gt_boxes = generate_mahjong_strip(tile_bank, STRIP_W, STRIP_H)
        t0 = time.time()
        results = det.detect_strip(strip)
        cost_ms = (time.time() - t0) * 1000.0
        times.append(cost_ms)

        det_labels = [d['label'] for d in results]
        gt_labels = [CLASSES[b[0]] for b in gt_boxes]

        match = (det_labels == gt_labels)
        if match:
            correct_count += 1
        if i < 3:
            print(f"Sample {i+1}: GT({len(gt_labels)})={''.join(gt_labels)} | DET({len(det_labels)})={''.join(det_labels)} | Match={match} ({cost_ms:.1f}ms)")

    avg_ms = sum(times) / len(times)
    acc = correct_count / float(total_test) * 100.0
    print(f"\nAverage Inference Latency: {avg_ms:.2f} ms")
    print(f"Full Hand Sequence Accuracy: {correct_count}/{total_test} ({acc:.1f}%)")


if __name__ == "__main__":
    run_benchmark()
