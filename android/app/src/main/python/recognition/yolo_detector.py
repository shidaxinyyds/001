# -*- coding: utf-8 -*-
"""YOLO-Mahjong-Nano 端到端目标检测器封装。

使用 OpenCV 原生 C++ cv2.dnn 推理，支持 Letterbox 等比缩放与 Class-Agnostic 物理空间互斥 NMS。
完全兼容现有 Detector 接口协议，输出 (rect, label, conf) 检测结果。
"""
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage, Rect

CLASSES = [
    '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m',
    '1p', '2p', '3p', '4p', '5p', '6p', '7p', '8p', '9p',
    '1s', '2s', '3s', '4s', '5s', '6s', '7s', '8s', '9s',
    '1z', '2z', '3z', '4z', '5z', '6z', '7z'
]

MODEL_W = 640
MODEL_H = 160


class YOLODetector(Detector):
    def __init__(self, model_path: Optional[str] = None, conf_thresh: float = 0.40, nms_thresh: float = 0.35):
        super().__init__({})
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh
        self.classes = CLASSES

        if model_path is None:
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.join(cur_dir, "models", "yolo_mahjong.onnx")

        self.model_path = model_path
        self.net = None
        self.last_top_score: float = 0.0
        self.last_screen: Tuple[int, int] = (0, 0)
        self._glyphs = None
        self._styles = None

        if os.path.isfile(model_path):
            try:
                self.net = cv2.dnn.readNetFromONNX(model_path)
                self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                print(f"[YOLODetector] Successfully loaded YOLO-Mahjong-Nano from: {model_path}")
            except Exception as e:
                print(f"[YOLODetector] Failed to load ONNX model via cv2.dnn: {e}")
                self.net = None
        else:
            print(f"[YOLODetector] Model file not found at: {model_path}")

    @property
    def is_available(self) -> bool:
        return self.net is not None

    def detect_strip(self, strip_bgr: np.ndarray, offset_x: int = 0, offset_y: int = 0) -> List[Tuple[Rect, str, float]]:
        """在长条图像区域（手牌行或牌河行）上执行 YOLO 检测。

        返回:
            list of (rect, label, conf), rect=(x, y, w, h)（已映射回全图绝对坐标）
            从左到右按 X 轴坐标严格排序。
        """
        if self.net is None or strip_bgr is None or strip_bgr.size == 0:
            return []

        orig_h, orig_w = strip_bgr.shape[:2]
        if orig_h < 15 or orig_w < 30:
            return []

        # 1. Letterbox 等比缩放到 (MODEL_W, MODEL_H)
        canvas = np.zeros((MODEL_H, MODEL_W, 3), dtype=np.uint8)
        scale = min(MODEL_W / float(orig_w), MODEL_H / float(orig_h))
        nw = int(orig_w * scale)
        nh = int(orig_h * scale)

        res_strip = cv2.resize(strip_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        pad_x = (MODEL_W - nw) // 2
        pad_y = (MODEL_H - nh) // 2
        canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = res_strip

        # 2. 前向推理 (OpenCV cv2.dnn)
        blob = cv2.dnn.blobFromImage(canvas, scalefactor=1.0 / 255.0, size=(MODEL_W, MODEL_H), swapRB=True)
        self.net.setInput(blob)
        preds = self.net.forward()[0]  # (38, 2100)

        preds = preds.transpose(1, 0)  # (2100, 38)
        boxes_raw = preds[:, :4]       # cx, cy, w, h
        cls_scores = preds[:, 4:]      # 34 classes

        max_scores = np.max(cls_scores, axis=1)
        max_class_ids = np.argmax(cls_scores, axis=1)

        mask = max_scores >= self.conf_thresh
        if not np.any(mask):
            return []

        cand_boxes = boxes_raw[mask]
        cand_scores = max_scores[mask]
        cand_classes = max_class_ids[mask]

        # 3. 反算回 strip_bgr 的真实像素坐标
        nms_boxes = []
        valid_indices = []
        for i, b in enumerate(cand_boxes):
            cx, cy, bw, bh = b
            # 去除 padding
            cx_unpad = cx - pad_x
            cy_unpad = cy - pad_y
            if cx_unpad < -bw * 0.5 or cx_unpad > nw + bw * 0.5:
                continue
            if cy_unpad < -bh * 0.5 or cy_unpad > nh + bh * 0.5:
                continue

            rx = (cx_unpad - bw * 0.5) / scale
            ry = (cy_unpad - bh * 0.5) / scale
            rw = bw / scale
            rh = bh / scale

            # 过滤物理长宽比异常的噪点（麻将牌高宽比大约 1.1~1.6）
            if rw <= 0 or rh <= 0:
                continue
            aspect = rw / float(rh)
            if aspect < 0.45 or aspect > 1.05:
                continue

            nms_boxes.append([int(rx), int(ry), int(rw), int(rh)])
            valid_indices.append(i)

        if not nms_boxes:
            return []

        filtered_scores = [float(cand_scores[idx]) for idx in valid_indices]

        # 4. Class-Agnostic 空间非重叠 NMS
        indices = cv2.dnn.NMSBoxes(
            nms_boxes, filtered_scores, score_threshold=self.conf_thresh, nms_threshold=self.nms_thresh
        )
        detections = []
        if len(indices) > 0:
            top_conf = 0.0
            for idx in indices.flatten():
                x, y, bw, bh = nms_boxes[idx]
                orig_idx = valid_indices[idx]
                cid = cand_classes[orig_idx]
                conf = float(cand_scores[orig_idx])
                top_conf = max(top_conf, conf)

                abs_x = max(0, offset_x + x)
                abs_y = max(0, offset_y + y)
                abs_w = min(orig_w - x, bw)
                abs_h = min(orig_h - y, bh)

                if abs_w >= 12 and abs_h >= 16:
                    rect: Rect = (abs_x, abs_y, abs_w, abs_h)
                    detections.append((rect, self.classes[cid], round(conf, 3)))

            self.last_top_score = round(top_conf, 3)

        # 5. 主行 Y 坐标离群点剔除（剔除悬浮在手牌上方的噪点框）
        if len(detections) >= 4:
            median_y = float(np.median([d[0][1] for d in detections]))
            median_h = float(np.median([d[0][3] for d in detections]))
            # 仅保留中心在 median_y +- 0.5 * median_h 范围内的手牌
            detections = [d for d in detections if abs(d[0][1] - median_y) <= 0.55 * median_h]

        # 6. 麻将同牌 <= 4 张刚性物理守卫：超过 4 张自动纠偏
        tile_counts: Dict[str, int] = {}
        guarded_detections = []
        for d in detections:
            rect, lbl, conf = d
            c = tile_counts.get(lbl, 0)
            if c < 4:
                tile_counts[lbl] = c + 1
                guarded_detections.append(d)
            else:
                # 超过 4 张物理上限，尝试替换为该花色相邻牌或跳过
                pass

        detections = guarded_detections

        # 7. 从左至右严格按 X 坐标排序
        detections.sort(key=lambda d: d[0][0])
        return detections

    def detect_all_rows(self, image: CVImage, classify: bool = True, allow_rotation: bool = False, allow_retry: bool = False, **kwargs) -> List[List[Tuple[Rect, Optional[str], float]]]:
        """扫描全图，检出手牌行与牌河各行。"""
        if image is None or image.size == 0 or not self.is_available:
            return []

        h, w = image.shape[:2]
        # 1. 玩家手牌行通常位于底部 y in [0.70, 1.0] * h
        hand_y1 = int(0.70 * h)
        hand_y2 = h
        hand_crop = image[hand_y1:hand_y2, :]

        hand_tiles = self.detect_strip(hand_crop, offset_x=0, offset_y=hand_y1)

        rows = []
        if hand_tiles:
            rows.append(hand_tiles)

        # 2. 牌河区域（中心区域 y in [0.30, 0.70] * h, x in [0.20, 0.80] * w）
        river_y1 = int(0.30 * h)
        river_y2 = int(0.70 * h)
        river_x1 = int(0.20 * w)
        river_x2 = int(0.80 * w)
        river_crop = image[river_y1:river_y2, river_x1:river_x2]
        river_tiles = self.detect_strip(river_crop, offset_x=river_x1, offset_y=river_y1)

        if river_tiles:
            # 按 Y 坐标聚合成多行
            river_tiles.sort(key=lambda d: d[0][1])
            curr_row = []
            last_y = None
            for t in river_tiles:
                cy = t[0][1] + t[0][3] * 0.5
                if last_y is None or abs(cy - last_y) < t[0][3] * 0.4:
                    curr_row.append(t)
                    last_y = cy
                else:
                    if curr_row:
                        curr_row.sort(key=lambda d: d[0][0])
                        rows.append(curr_row)
                    curr_row = [t]
                    last_y = cy
            if curr_row:
                curr_row.sort(key=lambda d: d[0][0])
                rows.append(curr_row)

        return rows

    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        """Detector 契约实现，返回 Stage[DetectionResult]。"""
        rows = self.detect_all_rows(image)
        flat: DetectionResult = []
        for r in rows:
            flat.extend(r)
        return Stage(image=image, detections=flat, stages={})

    # ===== 牌河条带化检测（影子对比路径）=====
    # 旧路把整个中心区 letterbox 进 640x160：四方×多行的网格被压成一条，
    # 与训练时的单行横条分布完全对不上 → 真实牌河检出≈0。
    # 正解：按四方分区（与 engine.detect_river_discards 同一套几何）再按行投影
    # 切条，每个条带只含一行牌（宽高比≈4，与训练条一致），逐条 detect_strip。
    # 侧家牌横放：先把左右分区旋转 90° 使牌面向上，坐标映射回原图。

    # 与 engine.detect_river_discards 的 zones 保持同一比例（那边改了这里要同步）。
    RIVER_ZONES = (
        ("bottom", 0.32, 0.52, 0.68, 0.72),
        ("top", 0.32, 0.16, 0.68, 0.36),
        ("left", 0.22, 0.30, 0.44, 0.64),
        ("right", 0.56, 0.30, 0.78, 0.64),
    )

    @staticmethod
    def _row_bands(strip_white: np.ndarray, min_h: int = 12) -> List[Tuple[int, int]]:
        """在白色掩码上按 y 投影切出牌行带（连续有牌列 > 间隙）。"""
        h, w = strip_white.shape[:2]
        proj = strip_white.sum(axis=1)
        thr = max(3.0, w * 0.03)
        bands: List[Tuple[int, int]] = []
        start = -1
        for y in range(h):
            if proj[y] >= thr:
                if start < 0:
                    start = y
            elif start >= 0:
                if y - start >= min_h:
                    bands.append((start, y))
                start = -1
        if start >= 0 and h - start >= min_h:
            bands.append((start, h))
        # 向上下各外扩 2px 补齐牌面边缘
        return [(max(0, a - 2), min(h, b + 2)) for a, b in bands]

    def _zone_white_mask(self, crop: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        return ((hsv[:, :, 1] < 80) & (hsv[:, :, 2] > 110)).astype('uint8')

    def _detect_zone_rows(self, crop: np.ndarray) -> List[Tuple[Rect, str, float]]:
        """把一个分区（已保证牌面向上）按行切条逐条检测，返回区内坐标。"""
        dets: List[Tuple[Rect, str, float]] = []
        mask = self._zone_white_mask(crop)
        for y0, y1 in self._row_bands(mask):
            strip = crop[y0:y1, :]
            if strip.shape[0] < 12 or strip.shape[1] < 30:
                continue
            dets.extend(self.detect_strip(strip, offset_x=0, offset_y=y0))
        return dets

    def detect_river_strips(
        self, image: CVImage
    ) -> List[Tuple[Rect, Optional[str], float]]:
        """四方牌河条带化检测：返回全图坐标的 (rect, label, conf) 列表。

        仅用于影子对比/离线评测；不改生产牌河链路。"""
        if image is None or image.size == 0 or not self.is_available:
            return []
        ih, iw = image.shape[:2]
        all_dets: List[Tuple[Rect, str, float]] = []
        for name, fx1, fy1, fx2, fy2 in self.RIVER_ZONES:
            x1, y1 = int(iw * fx1), int(ih * fy1)
            x2, y2 = int(iw * fx2), int(ih * fy2)
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            if name in ("left", "right"):
                rot = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
                H0 = crop.shape[0]  # 旋转前高
                # 旋转 CW：new_x = H0-1-y, new_y = x → 逆变换：x=y', y=H0-1-x'-w'
                for (rx, ry, rw, rh), lbl, conf in self._detect_zone_rows(rot):
                    ox = x1 + ry
                    oy = y1 + (H0 - 1 - rx - rw)
                    all_dets.append(((ox, oy, rh, rw), lbl, conf))  # w/h 互换
            else:
                for (rx, ry, rw, rh), lbl, conf in self._detect_zone_rows(crop):
                    all_dets.append(((x1 + rx, y1 + ry, rw, rh), lbl, conf))

        # 分区重叠处同一张牌可能被两侧各检一次：中心距 NMS（与轮廓法同尺度的 22px）
        all_dets.sort(key=lambda d: -d[2])
        kept: List[Tuple[Rect, str, float]] = []
        for (x, y, w, h), lbl, conf in all_dets:
            cx, cy = x + w / 2.0, y + h / 2.0
            if any(abs(cx - (kx + kw / 2.0)) < 22 and abs(cy - (ky + kh / 2.0)) < 22
                   for (kx, ky, kw, kh), _, _ in kept):
                continue
            kept.append(((x, y, w, h), lbl, conf))
        return kept
