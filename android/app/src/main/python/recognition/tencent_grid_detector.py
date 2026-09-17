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
        self.last_drawn_tile: Optional[str] = None
        self._load_templates()

    def _load_templates(self):
        # 1. 纯内存 Python 模块加载（100% 免疫 Android Chaquopy zip 文件系统限制，0毫秒极速加载）
        try:
            import recognition.templates_data as tdata
            self.templates_bgr.update(tdata.TEMPLATES_BGR)
            self.templates_gray.update(tdata.TEMPLATES_GRAY)
            print(f"[TencentGridDetector] Successfully loaded {len(self.templates_bgr)} templates from pure-python module.")
        except Exception as e:
            print(f"[TencentGridDetector] pure-python templates_data load warning: {e}")

        # 2. 次选预打包 npz
        if len(self.templates_bgr) < 27:
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            npz_path = os.path.join(cur_dir, "tencent_templates.npz")
            if os.path.exists(npz_path):
                try:
                    with open(npz_path, "rb") as f:
                        npz_data = np.load(f)
                        for key in npz_data.files:
                            bgr = npz_data[key]
                            self.templates_bgr[key] = bgr
                            self.templates_gray[key] = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                    print(f"[TencentGridDetector] Successfully loaded {len(self.templates_bgr)} templates from npz.")
                except Exception as e:
                    print(f"[TencentGridDetector] Failed to load npz: {e}")

        # 2. 备用降级：逐文件从 templates_dir 读取，使用 Python open() + cv2.imdecode 绕过 C 层 fopen 限制
        if len(self.templates_bgr) < 27 and os.path.isdir(self.templates_dir):
            try:
                for fname in os.listdir(self.templates_dir):
                    if fname.endswith(".png"):
                        name = fname[:-4]
                        path = os.path.join(self.templates_dir, fname)
                        bgr = None
                        try:
                            with open(path, "rb") as f:
                                buf = np.frombuffer(f.read(), dtype=np.uint8)
                                bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                        except Exception:
                            bgr = cv2.imread(path)
                        if bgr is not None:
                            self.templates_bgr[name] = bgr
                            self.templates_gray[name] = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            except Exception as e:
                print(f"[TencentGridDetector] Error loading templates from {self.templates_dir}: {e}")

        # 3. 载入碰杠搭子模板
        meld_dir = os.path.join(self.templates_dir, "melds")
        if os.path.isdir(meld_dir):
            try:
                for fname in os.listdir(meld_dir):
                    if fname.endswith(".png"):
                        name = fname[:-4]
                        path = os.path.join(meld_dir, fname)
                        img = None
                        try:
                            with open(path, "rb") as f:
                                buf = np.frombuffer(f.read(), dtype=np.uint8)
                                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                        except Exception:
                            img = cv2.imread(path)
                        if img is not None:
                            self.meld_templates[name] = img
            except Exception as e:
                pass

        print(f"[TencentGridDetector] Final active templates: {len(self.templates_bgr)} templates, {len(self.meld_templates)} meld templates.")

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

        # 1. 2万 vs 3万 物理笔画峰值严格判决（仅在最佳候选为 2m 或 3m 时生效）
        if best_lbl in ["2m", "3m"]:
            top_crop = face[20:58, 14:66]
            top_gray = cv2.cvtColor(top_crop, cv2.COLOR_BGR2GRAY)
            row_dark = np.sum(top_gray < 140, axis=1)
            num_peaks = self.count_peaks(row_dark, min_height=8, min_prominence=5)
            if num_peaks == 2 and scores.get("2m", 0) > 0.35:
                best_lbl = "2m"
            elif num_peaks >= 3 and scores.get("3m", 0) > 0.35:
                best_lbl = "3m"

        # 2. 2条 vs 3条 结构严格判决（仅在最佳候选为 2s 或 3s 时生效）
        if best_lbl in ["2s", "3s"]:
            bot_center = face[75:105, 35:45]
            bot_center_g = cv2.cvtColor(bot_center, cv2.COLOR_BGR2GRAY)
            bot_dark = float(np.mean(bot_center_g < 140))
            if bot_dark > 0.35 and scores.get("2s", 0) > 0.35:
                best_lbl = "2s"
            elif bot_dark <= 0.20 and scores.get("3s", 0) > 0.35:
                best_lbl = "3s"

        # 3. 2筒 vs 3筒 结构严格判决（仅在最佳候选为 2p 或 3p 时生效）
        if best_lbl in ["2p", "3p"]:
            face_hsv = cv2.cvtColor(face[40:80, 20:60], cv2.COLOR_BGR2HSV)
            red_cnt = int(np.sum(((face_hsv[:, :, 0] <= 10) | (face_hsv[:, :, 0] >= 170)) & (face_hsv[:, :, 1] >= 60) & (face_hsv[:, :, 2] >= 50)))
            if red_cnt > 50 and scores.get("3p", 0) > 0.35:
                best_lbl = "3p"
            elif red_cnt <= 20 and scores.get("2p", 0) > 0.35:
                best_lbl = "2p"

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

    def is_dingque_phase(self, image_bgr: np.ndarray) -> bool:
        """精准检测腾讯欢乐麻将『定缺中..』选门阶段（中央出现万/条/筒三大色盘按钮）"""
        if image_bgr is None or image_bgr.size == 0:
            return False
        ih, iw = image_bgr.shape[:2]
        # 定缺选门三色盘按钮严格位于中央区域
        sub = image_bgr[int(ih * 0.50):int(ih * 0.72), int(iw * 0.35):int(iw * 0.65)]
        if sub.shape[0] < 20 or sub.shape[1] < 20:
            return False
        hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
        red_mask = (((hsv[:, :, 0] <= 10) | (hsv[:, :, 0] >= 170)) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 120)).astype(np.uint8) * 255
        green_mask = ((hsv[:, :, 0] >= 40) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 100)).astype(np.uint8) * 255
        orange_mask = ((hsv[:, :, 0] >= 12) & (hsv[:, :, 0] <= 25) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 120)).astype(np.uint8) * 255

        def get_main_center(mask):
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            valid = [c for c in cnts if 200 <= cv2.contourArea(c) <= 6000]
            if not valid:
                return None
            c = max(valid, key=cv2.contourArea)
            bx, by, bw, bh = cv2.boundingRect(c)
            # 按钮正圆盘几何约束：真实定缺选门按钮为圆形 (0.75 <= bw/bh <= 1.30)，彻底排除长条形麻将牌 (bw/bh ~ 0.70)
            aspect = bw / float(bh)
            if not (0.75 <= aspect <= 1.30):
                return None
            # 实心度校验：圆盘面积与外接矩形比值应接近 pi/4 (~0.78)，排除中空噪点与牌河文字
            if cv2.contourArea(c) / float(bw * bh) < 0.55:
                return None
            M = cv2.moments(c)
            if M['m00'] == 0:
                return None
            return int(M['m10'] / M['m00']), int(M['m01'] / M['m00'])

        rc = get_main_center(red_mask)
        gc = get_main_center(green_mask)
        oc = get_main_center(orange_mask)

        if not (rc and gc and oc):
            return False

        rcx, rcy = rc
        gcx, gcy = gc
        ocx, ocy = oc

        # 水平必须自左向右依次为：万(红) -> 条(绿) -> 筒(黄/橙)
        if not (rcx < gcx < ocx):
            return False
        # 三个圆盘中心必须处于同一水平线上
        if abs(rcy - gcy) > 16 or abs(gcy - ocy) > 16 or abs(rcy - ocy) > 20:
            return False
        # 间距必须匀称
        d1 = gcx - rcx
        d2 = ocx - gcx
        if not (25 <= d1 <= 140 and 25 <= d2 <= 140 and abs(d1 - d2) <= 40):
            return False
        return True

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
        y_min = int(ih * 0.68)
        y_max = int(ih * 0.99)
        strip = image_bgr[y_min:y_max, :]
        sh, sw = strip.shape[:2]

        hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
        is_felt = (hsv[:, :, 0] >= 50) & (hsv[:, :, 0] <= 110) & (hsv[:, :, 1] >= 50)
        is_tile = ~is_felt & (hsv[:, :, 2] > 75) & (strip[:, :, 0] > 110) & (strip[:, :, 1] > 110) & (strip[:, :, 2] > 110)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(is_tile.astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            # 立牌高度通常占手牌带 35% 以上，过滤左下角头像区域 (x >= sw * 0.05)
            if bh > sh * 0.35 and x < sw * 0.95 and bw >= 25 and x >= sw * 0.05:
                boxes.append((x, y + y_min, bw, bh))

        if not boxes:
            return []

        boxes.sort(key=lambda b: b[0])

        main_box = max(boxes, key=lambda b: b[2])
        bx, by, bw, bh = main_box
        std_tw = 0.0547 * iw
        std_bh = std_tw * 1.35

        drawn_box = None
        if len(boxes) >= 2:
            last = boxes[-1]
            gap = last[0] - (bx + bw)
            bot_diff = abs((last[1] + last[3]) - (by + bh))
            # 摸牌判定：必须紧邻主手牌右侧 (间隙 <= 1.2倍牌宽)、底部 y 坐标对齐 (偏差 < 20px)、宽度与牌宽相当
            if 0 <= gap <= std_tw * 1.2 and bot_diff < 20 and last[2] <= std_tw * 1.5:
                drawn_box = last

        raw_est = bw / std_tw
        legal_standing = [1, 4, 7, 10, 13] if drawn_box else [1, 2, 4, 5, 7, 8, 10, 11, 13, 14]
        cand_counts = sorted(legal_standing, key=lambda c: abs(c - raw_est))[:2]

        best_standing_dets: List[Tuple[Rect, str, float]] = []
        best_standing_mean = -1.0

        for k in cand_counts:
            tw = bw / float(k)
            dets: List[Tuple[Rect, str, float]] = []
            for i in range(k):
                x1 = int(round(bx + i * tw))
                x2 = int(round(bx + (i + 1) * tw))
                # 逐列垂直自适应锚定：精准锁定该张牌真正的上下边界（抵抗换牌选中浮起、换牌按钮遮挡）
                col_mask = is_tile[:, x1:x2]
                row_counts = np.sum(col_mask, axis=1)
                valid_y = np.where(row_counts > (x2 - x1) * 0.35)[0]
                expected_h = int((x2 - x1) * 1.35)
                if len(valid_y) >= 20:
                    y_bot_c = y_min + valid_y[-1] + 1
                    y_top_c = max(0, y_bot_c - expected_h)
                else:
                    y_bot_c = min(ih, by + bh)
                    y_top_c = max(0, y_bot_c - expected_h)

                c = image_bgr[y_top_c:y_bot_c, x1:x2]
                lbl, sc = self.classify_tile(c)
                rect: Rect = (x1, y_top_c, x2 - x1, y_bot_c - y_top_c)
                dets.append((rect, lbl, sc))
            mean_sc = float(np.mean([d[2] for d in dets])) if dets else 0.0
            if mean_sc > best_standing_mean:
                best_standing_mean = mean_sc
                best_standing_dets = dets

        all_dets = list(best_standing_dets)

        # 追加独立摸牌或换牌浮起选牌（必须具有足够置信度，过滤非麻将UI）
        self.last_drawn_tile = None
        if drawn_box is not None:
            dbx, dby, dbw, dbh = drawn_box
            expected_h = int(dbw * 1.35)
            db_bot = dby + dbh
            db_top = max(0, db_bot - expected_h)
            col_strip = image_bgr[db_top:db_bot, dbx:dbx + dbw]
            lbl_d, sc_d = self.classify_tile(col_strip)
            if sc_d >= 0.48:
                rect_d: Rect = (dbx, db_top, dbw, db_bot - db_top)
                all_dets.append((rect_d, lbl_d, sc_d))
                self.last_drawn_tile = lbl_d

        top_conf = max([d[2] for d in all_dets], default=0.0)
        self.last_top_score = top_conf
        return all_dets

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
