# -*- coding: utf-8 -*-
import os
import sys
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage, Rect


class TencentGridDetector(Detector):
    def __init__(self, templates_dir: Optional[str] = None):
        super().__init__({})
        if templates_dir is None:
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            templates_dir = os.path.join(cur_dir, "images", "tencent_exact")

        self.templates_dir = templates_dir
        self.templates_bgr: Dict[str, np.ndarray] = {}
        self.templates_gray: Dict[str, np.ndarray] = {}
        self.meld_templates: Dict[str, np.ndarray] = {}
        self.last_top_score: float = 0.0
        self.last_screen: Tuple[int, int] = (0, 0)
        self._load_templates()

    def _load_templates(self):
        if not os.path.isdir(self.templates_dir):
            print(f"[TencentGridDetector] Templates dir not found: {self.templates_dir}")
            return

        for fname in os.listdir(self.templates_dir):
            if fname.endswith(".png"):
                name = fname[:-4]
                path = os.path.join(self.templates_dir, fname)
                bgr = cv2.imread(path)
                if bgr is not None:
                    self.templates_bgr[name] = bgr
                    self.templates_gray[name] = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        meld_dir = os.path.join(self.templates_dir, "melds")
        if os.path.isdir(meld_dir):
            for fname in os.listdir(meld_dir):
                if fname.endswith(".png"):
                    name = fname[:-4]
                    path = os.path.join(meld_dir, fname)
                    img = cv2.imread(path)
                    if img is not None:
                        self.meld_templates[name] = img

        print(f"[TencentGridDetector] Successfully loaded {len(self.templates_bgr)} templates and {len(self.meld_templates)} meld templates.")

    @property
    def templates(self) -> Dict[str, np.ndarray]:
        return self.templates_gray

    @property
    def is_available(self) -> bool:
        return len(self.templates_bgr) >= 27

    @staticmethod
    def extract_face(img: np.ndarray, target_size=(80, 120)) -> np.ndarray:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        is_not_green = ~((hsv[:, :, 0] >= 50) & (hsv[:, :, 0] <= 110) & (hsv[:, :, 1] >= 50))
        ys, xs = np.where(is_not_green)
        if len(ys) > 50:
            face = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        else:
            face = img
        if face is None or face.size == 0 or face.shape[0] < 5 or face.shape[1] < 5:
            return np.zeros((target_size[1], target_size[0], 3), dtype=np.uint8)
        return cv2.resize(face, target_size, interpolation=cv2.INTER_AREA)

    @staticmethod
    def count_peaks(profile: np.ndarray, min_height: int = 10, min_prominence: int = 6) -> int:
        peaks = 0
        peak_val = 0
        valley_val = 0
        state = 'looking_up'
        for i in range(len(profile)):
            v = profile[i]
            if state == 'looking_up':
                if v > peak_val:
                    peak_val = v
                elif peak_val - v >= min_prominence and peak_val >= min_height:
                    peaks += 1
                    valley_val = v
                    state = 'looking_down'
            elif state == 'looking_down':
                if v < valley_val:
                    valley_val = v
                elif v - valley_val >= min_prominence:
                    peak_val = v
                    state = 'looking_up'
        if state == 'looking_up' and peak_val >= min_height:
            peaks += 1
        return peaks

    def classify_tile(self, crop: np.ndarray) -> Tuple[str, float]:
        face = self.extract_face(crop)
        hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
        is_grey = (np.mean(hsv[:, :, 1]) < 35)

        is_yellow_btn = (hsv[:40, :, 0] >= 15) & (hsv[:40, :, 0] <= 35) & (hsv[:40, :, 1] > 100)
        has_btn = (np.sum(is_yellow_btn) > 80)
        y_start = 38 if has_btn else 10

        c_face = face[y_start:110, 6:74]
        scores: Dict[str, float] = {}

        valid_tiles = {f"{i}m" for i in range(1, 10)} | {f"{i}p" for i in range(1, 10)} | {f"{i}s" for i in range(1, 10)} | {"7z"}

        if is_grey:
            c_face_g = cv2.normalize(cv2.cvtColor(c_face, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
            for lbl, tmpl in self.templates_bgr.items():
                if lbl not in valid_tiles:
                    continue
                t_core = tmpl[y_start + 6:104, 12:68]
                t_core_g = cv2.normalize(cv2.cvtColor(t_core, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
                res = cv2.matchTemplate(c_face_g, t_core_g, cv2.TM_CCOEFF_NORMED)
                scores[lbl] = float(res.max())
        else:
            for lbl, tmpl in self.templates_bgr.items():
                if lbl not in valid_tiles:
                    continue
                t_core = tmpl[y_start + 6:104, 12:68]
                res = cv2.matchTemplate(c_face, t_core, cv2.TM_CCOEFF_NORMED)
                scores[lbl] = float(res.max())

        if not scores:
            return "7z", 0.0

        sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_lbl = sorted_candidates[0][0]
        best_sc = sorted_candidates[0][1]

        if (best_lbl in ["2m", "3m"] or best_lbl in ["7m", "9m"]) and best_sc < 0.75 and not has_btn:
            bot_crop = face[60:105, 14:66]
            bot_hsv = cv2.cvtColor(bot_crop, cv2.COLOR_BGR2HSV)
            has_red_wan = np.sum(((bot_hsv[:, :, 0] <= 12) | (bot_hsv[:, :, 0] >= 165)) & (bot_hsv[:, :, 1] > 60)) > 40
            if has_red_wan or is_grey:
                top_crop = face[20:58, 14:66]
                top_gray = cv2.cvtColor(top_crop, cv2.COLOR_BGR2GRAY)
                row_dark = np.sum(top_gray < 140, axis=1)
                num_peaks = self.count_peaks(row_dark, min_height=8, min_prominence=5)
                if num_peaks == 2 and scores.get("2m", 0) > 0.35:
                    best_lbl = "2m"
                elif num_peaks >= 3 and scores.get("3m", 0) > 0.35:
                    best_lbl = "3m"

        if best_lbl in ["4p", "5p"]:
            cy, cx = int(face.shape[0] * 0.5), int(face.shape[1] * 0.5)
            c_roi = face[max(0, cy - 6):min(face.shape[0], cy + 6), max(0, cx - 6):min(face.shape[1], cx + 6)]
            c_hsv = cv2.cvtColor(c_roi, cv2.COLOR_BGR2HSV)
            has_center_red = np.sum(((c_hsv[:, :, 0] <= 10) | (c_hsv[:, :, 0] >= 170)) & (c_hsv[:, :, 1] > 80)) > 4
            if has_center_red and scores.get("5p", 0) > 0.40:
                best_lbl = "5p"
            elif not has_center_red and scores.get("4p", 0) > 0.40:
                best_lbl = "4p"

        if best_lbl in ["6p", "8p"] and abs(scores.get("6p", 0) - scores.get("8p", 0)) < 0.12:
            mid_slice = face[46:60, 15:65]
            mid_gray = cv2.cvtColor(mid_slice, cv2.COLOR_BGR2GRAY)
            mid_dark_ratio = np.mean(mid_gray < 140)
            if mid_dark_ratio < 0.08:
                best_lbl = "6p"
            else:
                best_lbl = "8p"

        if best_lbl in ["6s", "9s"] and abs(scores.get("6s", 0) - scores.get("9s", 0)) < 0.12:
            mid_tiao = face[52:64, 15:65]
            mid_tiao_g = cv2.cvtColor(mid_tiao, cv2.COLOR_BGR2GRAY)
            mid_t_dark = np.mean(mid_tiao_g < 130)
            if mid_t_dark < 0.12:
                best_lbl = "6s"
            else:
                best_lbl = "9s"

        return best_lbl, round(float(best_sc), 3)

    def _classify_face_retry(
        self,
        face_bgr: np.ndarray,
        rect: Tuple[int, int, int, int] = (0, 0, 0, 0),
        allow_retry: bool = False,
    ) -> Tuple[Optional[str], float]:
        if face_bgr is None or face_bgr.size == 0 or not self.is_available:
            return None, 0.0
        lbl, sc = self.classify_tile(face_bgr)
        return lbl, sc

    def detect_dingque(self, image_bgr: np.ndarray) -> str:
        ih, iw = image_bgr.shape[:2]
        sub = image_bgr[int(ih * 0.57):int(ih * 0.68), int(iw * 0.08):int(iw * 0.13)]
        if sub.shape[0] < 10 or sub.shape[1] < 10:
            return "s"

        hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
        green_mask = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 50) & (hsv[:, :, 2] >= 40)
        red_mask = ((hsv[:, :, 0] <= 10) | (hsv[:, :, 0] >= 165)) & (hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 50)
        orange_mask = (hsv[:, :, 0] >= 12) & (hsv[:, :, 0] <= 28) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 100)

        scores = {
            "s": int(np.sum(green_mask)),
            "m": int(np.sum(red_mask)),
            "p": int(np.sum(orange_mask))
        }
        return max(scores, key=scores.get)

    def detect_hand_strip(self, image_bgr: np.ndarray) -> List[Tuple[Rect, str, float]]:
        if image_bgr is None or image_bgr.size == 0 or not self.is_available:
            return []

        ih, iw = image_bgr.shape[:2]
        y_top = int(ih * 0.81)
        y_bot = int(ih * 0.99)
        hand_strip = image_bgr[y_top:y_bot, :]
        sh, sw = hand_strip.shape[:2]

        hsv = cv2.cvtColor(hand_strip, cv2.COLOR_BGR2HSV)
        is_felt = (hsv[:, :, 0] >= 55) & (hsv[:, :, 0] <= 105) & (hsv[:, :, 1] >= 60)
        is_tile = ~is_felt & (hsv[:, :, 2] > 70)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(is_tile.astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        valid_boxes = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            if bh > sh * 0.5 and bw > 120 and x < sw * 0.65:
                valid_boxes.append((x, y, bw, bh))

        if not valid_boxes:
            valid_boxes = [(int(sw * 0.05), 0, int(sw * 0.70), sh)]

        valid_boxes.sort(key=lambda b: b[2], reverse=True)
        bx, by, bw, bh = valid_boxes[0]

        est_tw = bh / 1.40
        raw_tiles_est = bw / est_tw
        legal_counts = [1, 2, 4, 5, 7, 8, 10, 11, 13, 14]
        best_target_count = min(legal_counts, key=lambda c: abs(c - raw_tiles_est))

        active_strip = hand_strip[:, bx:bx + bw]
        felt_sub = is_felt[:, bx:bx + bw]
        col_felt = np.mean(felt_sub[int(sh * 0.2):int(sh * 0.8), :], axis=0)

        search_start = int(bw * 0.75)
        search_end = int(bw * 0.95)
        is_gap_col = (col_felt[search_start:search_end] > 0.20)
        max_run = 0
        cur_run = 0
        gap_start_in_sub = -1
        for idx_g, v in enumerate(is_gap_col):
            if v:
                cur_run += 1
                if cur_run > max_run:
                    max_run = cur_run
                    gap_start_in_sub = idx_g - cur_run + 1
            else:
                cur_run = 0

        has_draw_gap = (max_run >= 10)
        split_x = (search_start + gap_start_in_sub) if has_draw_gap else -1

        if has_draw_gap:
            best_target_count = min([2, 5, 8, 11, 14], key=lambda c: abs(c - raw_tiles_est))
        else:
            best_target_count = min([1, 4, 7, 10, 13], key=lambda c: abs(c - raw_tiles_est))

        detections: List[Tuple[Rect, str, float]] = []
        top_conf = 0.0

        if has_draw_gap and split_x > 0:
            standing_crop = active_strip[:, :split_x]
            after_gap = active_strip[:, split_x:]
            ag_hsv = cv2.cvtColor(after_gap, cv2.COLOR_BGR2HSV)
            ag_felt = (ag_hsv[:, :, 0] >= 55) & (ag_hsv[:, :, 0] <= 105) & (ag_hsv[:, :, 1] >= 60)
            non_felt_cols = np.where(np.mean(~ag_felt[int(sh * 0.2):int(sh * 0.8), :], axis=0) > 0.35)[0]
            if len(non_felt_cols) > 20:
                drawn_tile_crop = after_gap[:, non_felt_cols[0]:non_felt_cols[-1] + 1]
            else:
                drawn_tile_crop = after_gap

            standing_count = best_target_count - 1
            stw = standing_crop.shape[1] / float(standing_count)
            for i in range(standing_count):
                x1 = int(round(i * stw))
                x2 = int(round((i + 1) * stw))
                c = standing_crop[:, x1:x2]
                lbl, sc = self.classify_tile(c)
                rect: Rect = (bx + x1, y_top + by, x2 - x1, bh)
                detections.append((rect, lbl, sc))
                top_conf = max(top_conf, sc)

            lbl, sc = self.classify_tile(drawn_tile_crop)
            rect14: Rect = (bx + split_x, y_top + by, int(est_tw), bh)
            detections.append((rect14, lbl, sc))
            top_conf = max(top_conf, sc)
        else:
            tw = bw / float(best_target_count)
            for i in range(best_target_count):
                x1 = int(round(i * tw))
                x2 = int(round((i + 1) * tw))
                c = active_strip[:, x1:x2]
                lbl, sc = self.classify_tile(c)
                rect = (bx + x1, y_top + by, x2 - x1, bh)
                detections.append((rect, lbl, sc))
                top_conf = max(top_conf, sc)

        self.last_top_score = top_conf
        return detections

    def detect_all_rows(
        self, image: CVImage, classify: bool = True, allow_rotation: bool = False, **kwargs
    ) -> List[List[Tuple[Rect, Optional[str], float]]]:
        if image is None or image.size == 0 or not self.is_available:
            return []

        h, w = image.shape[:2]
        self.last_screen = (w, h)

        hand_tiles = self.detect_hand_strip(image)
        rows = []
        if hand_tiles:
            rows.append(hand_tiles)
        return rows

    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        rows = self.detect_all_rows(image)
        flat: DetectionResult = []
        for r in rows:
            flat.extend(r)
        return Stage(image=image, detections=flat, stages={})
