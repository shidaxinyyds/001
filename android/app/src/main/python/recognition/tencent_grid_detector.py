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
        # 多风格模板条目 [(基础标签, 风格名, 80x120 BGR 模板)]：同标签允许多个视觉
        # 变体（腾讯主模板 + 各平台 bank），分类按标签聚合取最高分。
        self.tpl_entries: List[Tuple[str, str, np.ndarray]] = []
        # (lbl, style, btn_core, plain_core, btn_gray, plain_gray)
        self._cores: List[Tuple[str, str, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
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

        # 4. 附加平台 bank（蜀山等）：键名 '#' 后为变体后缀，按基础标签聚合。
        #    主库加载失败也不影响附加库——两者独立来源。
        for mod_name, style in (("recognition.templates_shushan", "shushan"),):
            try:
                mod = __import__(mod_name, fromlist=["TEMPLATES_BGR"])
                added = 0
                for key, bgr in mod.TEMPLATES_BGR.items():
                    self.tpl_entries.append((key.split("#")[0], style, np.asarray(bgr, dtype=np.uint8)))
                    added += 1
                print(f"[{type(self).__name__}] extra bank {mod_name}: +{added} templates")
            except Exception as e:
                print(f"[{type(self).__name__}] extra bank {mod_name} unavailable: {e}")

        self._build_cores()

    def _build_cores(self):
        """预切片/预归一化匹配核，避免每帧每张牌重复 cvtColor+normalize。

        核区域与旧实现一致：普通 y16:104、带黄色顶标 y44:104、x12:68。
        每个核带风格标签，运行时可按探针胜出的风格只算该 bank，把多平台
        开销压回单平台水平。
        """
        base = [(k.split("#")[0], "tencent", v) for k, v in self.templates_bgr.items()]
        self.tpl_entries = base + self.tpl_entries
        self._cores = []
        for lbl, style, tmpl in self.tpl_entries:
            plain = tmpl[16:104, 12:68]
            btn = tmpl[44:104, 12:68]
            plain_g = cv2.normalize(cv2.cvtColor(plain, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
            btn_g = cv2.normalize(cv2.cvtColor(btn, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
            self._cores.append((lbl, style, btn, plain, btn_g, plain_g))

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

    def classify_tile(self, crop: np.ndarray, avail=None, styles=None) -> Tuple[str, float]:
        face = self.extract_face(crop)
        hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
        is_grey = (np.mean(hsv[:, :, 1]) < 35)

        is_yellow_btn = (hsv[:40, :, 0] >= 15) & (hsv[:40, :, 0] <= 35) & (hsv[:40, :, 1] > 100)
        has_btn = (np.sum(is_yellow_btn) > 80)
        y_start = 38 if has_btn else 10

        c_face = face[y_start:110, 6:74]
        scores: Dict[str, float] = {}

        if avail is not None:
            if avail and isinstance(next(iter(avail)), int):
                from trainer.utils.convert import tiles34_index_to_mpsz
                valid_tiles = {tiles34_index_to_mpsz(i) for i in avail}
            else:
                valid_tiles = set(avail)
        else:
            valid_tiles = {f"{i}m" for i in range(1, 10)} | {f"{i}p" for i in range(1, 10)} | {f"{i}s" for i in range(1, 10)} | {"7z"}

        if is_grey:
            c_face_g = cv2.normalize(cv2.cvtColor(c_face, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
            for lbl, style, core_btn, core_plain, gcore_btn, gcore_plain in self._cores:
                if lbl not in valid_tiles or (styles is not None and style not in styles):
                    continue
                src = gcore_btn if has_btn else gcore_plain
                s = float(cv2.matchTemplate(c_face_g, src, cv2.TM_CCOEFF_NORMED).max())
                if s > scores.get(lbl, 0.0):
                    scores[lbl] = s
        else:
            for lbl, style, core_btn, core_plain, _gb, _gp in self._cores:
                if lbl not in valid_tiles or (styles is not None and style not in styles):
                    continue
                src = core_btn if has_btn else core_plain
                s = float(cv2.matchTemplate(c_face, src, cv2.TM_CCOEFF_NORMED).max())
                if s > scores.get(lbl, 0.0):
                    scores[lbl] = s

        if not scores:
            return next(iter(valid_tiles)) if valid_tiles else "7z", 0.0

        sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_lbl = sorted_candidates[0][0]
        best_sc = sorted_candidates[0][1]

        # 1. 2万 vs 3万 物理笔画峰值严格判决（当最高候选为 2m 或 3m 且两者分数极度接近时生效）
        c_2m = scores.get("2m", 0.0)
        c_3m = scores.get("3m", 0.0)
        if best_lbl in ["2m", "3m"] and abs(c_2m - c_3m) < 0.06:
            top_crop = face[20:58, 14:66]
            top_gray = cv2.cvtColor(top_crop, cv2.COLOR_BGR2GRAY)
            # 自适应二值化 / 动态暗阈值，抵抗屏幕亮度变化
            dark_thresh = min(150, max(100, int(np.mean(top_gray) * 0.78)))
            row_dark = np.sum(top_gray < dark_thresh, axis=1)
            num_peaks = self.count_peaks(row_dark, min_height=5, min_prominence=3)
            # 中轴墨迹深度检测：2万两横之间是纯白底，3万中间有实体横画
            h_crop = top_gray.shape[0]
            mid_band = top_gray[int(h_crop * 0.38):int(h_crop * 0.62), :]
            mid_ink_ratio = float(np.mean(mid_band < dark_thresh))

            if num_peaks == 2 or mid_ink_ratio < 0.07:
                best_lbl = "2m"
                best_sc = max(c_2m, best_sc)
            elif num_peaks >= 3 or mid_ink_ratio >= 0.12:
                best_lbl = "3m"
                best_sc = max(c_3m, best_sc)

        # 2. 2条 vs 3条 结构严格判决（当候选包含 2s/3s 且分数极度接近时生效）
        c_2s = scores.get("2s", 0.0)
        c_3s = scores.get("3s", 0.0)
        if best_lbl in ["2s", "3s"] and abs(c_2s - c_3s) < 0.06:
            bot_center = face[75:105, 35:45]
            bot_center_g = cv2.cvtColor(bot_center, cv2.COLOR_BGR2GRAY)
            bot_dark = float(np.mean(bot_center_g < 140))
            if bot_dark > 0.30 and c_2s > 0.32:
                best_lbl = "2s"
                best_sc = max(c_2s, best_sc)
            elif bot_dark <= 0.22 and c_3s > 0.32:
                best_lbl = "3s"
                best_sc = max(c_3s, best_sc)

        # 3. 2筒 vs 3筒 结构严格判决（当候选包含 2p/3p 且分数极度接近时生效）
        c_2p = scores.get("2p", 0.0)
        c_3p = scores.get("3p", 0.0)
        if best_lbl in ["2p", "3p"] and abs(c_2p - c_3p) < 0.06:
            face_hsv = cv2.cvtColor(face[40:80, 20:60], cv2.COLOR_BGR2HSV)
            red_cnt = int(np.sum(((face_hsv[:, :, 0] <= 10) | (face_hsv[:, :, 0] >= 170)) & (face_hsv[:, :, 1] >= 55) & (face_hsv[:, :, 2] >= 45)))
            if red_cnt > 30 and c_3p > 0.32:
                best_lbl = "3p"
                best_sc = max(c_3p, best_sc)
            elif red_cnt <= 25 and c_2p > 0.32:
                best_lbl = "2p"
                best_sc = max(c_2p, best_sc)

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

    def _probe_style(self, crop: np.ndarray) -> Optional[str]:
        """用一枚代表牌对全部 bank 打分，返回胜出模板的风格（分不足返 None）。

        同风格自配分通常 0.9+，跨风格 <0.65，门槛 0.60 留安全边距；
        探针错了也无妨——手牌行均分不达标时会全量兜底重扫。
        """
        if crop is None or crop.size == 0 or not self._cores:
            return None
        face = self.extract_face(crop)
        hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
        is_yellow_btn = (hsv[:40, :, 0] >= 15) & (hsv[:40, :, 0] <= 35) & (hsv[:40, :, 1] > 100)
        has_btn = (np.sum(is_yellow_btn) > 80)
        c_face = face[38 if has_btn else 10:110, 6:74]
        best_style, best_sc = None, 0.0
        for _lbl, style, core_btn, core_plain, _gb, _gp in self._cores:
            src = core_btn if has_btn else core_plain
            s = float(cv2.matchTemplate(c_face, src, cv2.TM_CCOEFF_NORMED).max())
            if s > best_sc:
                best_sc, best_style = s, style
        return best_style if best_sc >= 0.60 else None

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
        # 定缺选门色盘按钮位于中央区域 (y: 40%~75%, x: 25%~75%)
        sub = image_bgr[int(ih * 0.40):int(ih * 0.75), int(iw * 0.25):int(iw * 0.75)]
        if sub.shape[0] < 20 or sub.shape[1] < 20:
            return False
        hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
        red_mask = (((hsv[:, :, 0] <= 10) | (hsv[:, :, 0] >= 170)) & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 100)).astype(np.uint8) * 255
        green_mask = ((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 80)).astype(np.uint8) * 255
        orange_mask = ((hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 32) & (hsv[:, :, 1] >= 75) & (hsv[:, :, 2] >= 90)).astype(np.uint8) * 255

        def get_main_center(mask):
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            valid = []
            for c in cnts:
                area = cv2.contourArea(c)
                if not (400 <= area <= 10000):
                    continue
                bx, by, bw, bh = cv2.boundingRect(c)
                aspect = bw / float(bh)
                if not (0.65 <= aspect <= 1.45):
                    continue
                hull = cv2.convexHull(c)
                if cv2.contourArea(hull) / float(bw * bh) < 0.58:
                    continue
                M = cv2.moments(c)
                if M['m00'] > 0:
                    valid.append(((int(M['m10'] / M['m00']), int(M['m01'] / M['m00'])), area))
            if not valid:
                return None
            return max(valid, key=lambda x: x[1])[0]

        rc = get_main_center(red_mask)
        gc = get_main_center(green_mask)
        oc = get_main_center(orange_mask)

        # 1. 三个按钮全在：万(红) -> 条(绿) -> 筒(黄/橙)
        if rc and gc and oc:
            rcx, rcy = rc
            gcx, gcy = gc
            ocx, ocy = oc
            if rcx < gcx < ocx and max(abs(rcy - gcy), abs(gcy - ocy), abs(rcy - ocy)) <= 25:
                return True

        # 2. 悬浮窗遮挡最左侧'万'（或0万断门）：绿(条) -> 橙(筒)
        if gc and oc:
            gcx, gcy = gc
            ocx, ocy = oc
            if gcx < ocx and abs(gcy - ocy) <= 25 and (ocx - gcx) < sub.shape[1] * 0.40:
                return True

        # 3. 遮挡最右侧'筒'（或0筒断门）：红(万) -> 绿(条)
        if rc and gc:
            rcx, rcy = rc
            gcx, gcy = gc
            if rcx < gcx and abs(rcy - gcy) <= 25 and (gcx - rcx) < sub.shape[1] * 0.40:
                return True

        return False

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


    def is_swap_phase(self, image_bgr: np.ndarray) -> bool:
        """检测腾讯欢乐麻将「换牌中...」换三张阶段。
        真实特征：右侧必定出现金黄色【换牌】大圆按钮 (H in [14, 35], S >= 110, V >= 140)
        以及青色【过】按钮，绝不能检测绿色（绿色为牌桌桌布主色，会导致整局被误判为换牌）。"""
        if image_bgr is None or image_bgr.size == 0:
            return False
        ih, iw = image_bgr.shape[:2]
        # 金黄色换牌按钮专属区域（y: 55%~75%, x: 65%~85%）
        btn_area = image_bgr[int(ih * 0.55):int(ih * 0.75), int(iw * 0.65):int(iw * 0.85)]
        if btn_area.size == 0:
            return False
        hsv = cv2.cvtColor(btn_area, cv2.COLOR_BGR2HSV)
        # 金黄色大圆形按钮颜色区间：色相 14~35，高饱和 S>=110，高明度 V>=140
        gold_btn = (hsv[:, :, 0] >= 14) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 140)
        gold_ratio = float(np.mean(gold_btn))

        # 真实换牌阶段金黄色圆形按钮占比约为 4%~8%，非换牌对局为 0%
        return gold_ratio >= 0.025

    def is_pick_phase(self, image_bgr: np.ndarray) -> bool:
        """检测腾讯欢乐麻将「请任选一张牌」弹窗或选牌确定界面。"""
        if image_bgr is None or image_bgr.size == 0:
            return False
        ih, iw = image_bgr.shape[:2]

        # 形式1：中央大牌与金色「选牌确定」字样（用户点击选牌后状态）
        tile_crop = image_bgr[int(ih * 0.20):int(ih * 0.42), int(iw * 0.44):int(iw * 0.56)]
        if tile_crop.size > 0:
            hsv_tile = cv2.cvtColor(tile_crop, cv2.COLOR_BGR2HSV)
            white_ratio = float(np.mean((hsv_tile[:, :, 2] > 190) & (hsv_tile[:, :, 1] < 50)))

            txt_area = image_bgr[int(ih * 0.41):int(ih * 0.53), int(iw * 0.36):int(iw * 0.64)]
            if txt_area.size > 0:
                hsv_txt = cv2.cvtColor(txt_area, cv2.COLOR_BGR2HSV)
                gold = (hsv_txt[:, :, 0] >= 14) & (hsv_txt[:, :, 0] <= 36) & (hsv_txt[:, :, 1] >= 100) & (hsv_txt[:, :, 2] >= 130)
                gold_ratio = float(np.mean(gold))
                if white_ratio >= 0.15 and gold_ratio >= 0.028:
                    return True

        # 形式2：任选牌弹窗位于屏幕中央（深色弹窗背景+内嵌白色牌面横条）
        row = image_bgr[int(ih * 0.50):int(ih * 0.65), int(iw * 0.15):int(iw * 0.92)]
        if row.size > 0:
            hsv = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
            # 弹窗深色背景（V<80, S<60）
            dark_bg = float(np.mean((hsv[:, :, 2] < 80) & (hsv[:, :, 1] < 60)))
            # 弹窗内白色牌面（V>180, S<50）
            white_tiles = float(np.mean((hsv[:, :, 2] > 180) & (hsv[:, :, 1] < 50)))
            if dark_bg >= 0.15 and white_tiles >= 0.05:
                return True

        return False

    def detect_pick_candidates(self, image_bgr: np.ndarray) -> List[str]:
        """识别「请任选一张牌」弹窗中的候选牌列表（1万~9万 或 条/筒 横排）。"""
        if image_bgr is None or image_bgr.size == 0:
            return []
        ih, iw = image_bgr.shape[:2]
        # 候选牌实际位于屏幕 y: 60%~77%（弹窗主体牌行），x: 15%~92%
        # 注意：左侧约15%有圆形花色标识（万/条），需要在后续过滤
        panel = image_bgr[int(ih * 0.60):int(ih * 0.77), int(iw * 0.15):int(iw * 0.92)]
        if panel.size == 0:
            return []

        ph, pw = panel.shape[:2]
        hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
        # 找到弹窗内的白色/浅色牌面区域
        is_tile = (
            (hsv[:, :, 2] > 140) & (hsv[:, :, 1] < 60) &
            (panel[:, :, 0] > 120) & (panel[:, :, 1] > 120) & (panel[:, :, 2] > 120)
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(is_tile.astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        tiles = []
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            asp = bw / float(max(bh, 1))
            # 候选牌面：宽>=25px，高>=25px，宽高比 0.8~2.5（弹窗牌面略扁）
            # 过滤左侧圆形花色标（直径约35px，x < iw*0.12，即 panel x < 77）
            if bw < 25 or bh < 25 or not (0.7 <= asp <= 2.8):
                continue
            # 过滤掉最左侧的圆形花色标（万/条/筒图标）
            x_abs = int(iw * 0.15) + x
            if x_abs < int(iw * 0.22):
                continue
            crop = panel[y:y + bh, x:x + bw]
            if crop.size == 0:
                continue
            lbl, sc = self.classify_tile(crop)
            if sc >= 0.38:
                tiles.append((x, lbl, sc))

        tiles.sort(key=lambda t: t[0])
        # 去重（同一区域重复检测）
        result = []
        prev_x = -999
        for (tx, lbl, sc) in tiles:
            if tx - prev_x > 20:  # 间隔>20px视为不同牌
                result.append(lbl)
                prev_x = tx
        return result


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
                # 垂直投影修剪：去除边缘粘连的头像徽章、积分标牌、UI阴影等细窄干扰物
                sub_mask = is_tile[y:y+bh, x:x+bw]
                col_counts = np.sum(sub_mask, axis=0)
                thresh = max(15, int(sh * 0.25))
                valid_cols = np.where(col_counts >= thresh)[0]
                if len(valid_cols) >= 20:
                    refined_x = x + int(valid_cols[0])
                    refined_bw = int(valid_cols[-1] - valid_cols[0] + 1)
                    boxes.append((refined_x, y + y_min, refined_bw, bh))
                else:
                    boxes.append((x, y + y_min, bw, bh))

        if not boxes:
            return []

        boxes.sort(key=lambda b: b[0])

        main_box = max(boxes, key=lambda b: b[2])
        bx, by, bw, bh = main_box
        # 牌面宽度：牌高/1.35 是全平台共性几何（腾讯旧版写死 0.0547*iw，
        # 换平台/换分辨率/发牌动画压扁时张数估计直接崩掉并整行拒识）。
        face_w = bh / 1.35 if bh >= 40 else 0.0547 * iw
        std_tw = face_w
        std_bh = std_tw * 1.35

        drawn_box = None
        if len(boxes) >= 2:
            last = boxes[-1]
            gap = last[0] - (bx + bw)
            bot_diff = abs((last[1] + last[3]) - (by + bh))
            # 摸牌判定：必须紧邻主手牌右侧 (间隙 <= 1.2倍牌宽)、底部 y 坐标对齐 (偏差 < 20px)、宽度与牌宽相当
            if 0 <= gap <= std_tw * 1.2 and bot_diff < 20 and last[2] <= std_tw * 1.5:
                drawn_box = last

        # 张数估计双模型：相邻排布平台（蜀山等）节距≈牌面宽；重叠排布平台
        # （腾讯）节距≈0.0547*iw。用主块中央一枚代表牌做风格探针路由：探针
        # 命中腾讯→只走腾讯节距候选（与旧行为完全等价，零回退）；命中其他
        # 风格→只走相邻候选并把分类限定在该 bank（开销与单平台同级）；探针
        # 不定→两路候选并集全模板兜底。均分不达标时再全量重扫一次。
        raw_adj = bw / face_w
        raw_tec = bw / (0.0547 * iw)
        legal_standing = [1, 4, 7, 10, 13] if drawn_box else [1, 2, 4, 5, 7, 8, 10, 11, 13, 14]
        c_adj = sorted(legal_standing, key=lambda c: abs(c - raw_adj))[:2]
        c_tec = sorted(legal_standing, key=lambda c: abs(c - raw_tec))[:2]

        pcx = int(bx + bw / 2)
        half = max(10, int(face_w * 0.5))
        probe_crop = image_bgr[by:by + bh, max(0, pcx - half):min(iw, pcx + half)]
        style = self._probe_style(probe_crop)
        if style == "tencent":
            cand_counts, probe_styles = c_tec, {"tencent"}
        elif style is not None:
            cand_counts, probe_styles = c_adj, {style}
        else:
            cand_counts, probe_styles = list(dict.fromkeys(c_adj + c_tec)), None

        def _try_counts(cands: List[int], styles):
            best_dets: List[Tuple[Rect, str, float]] = []
            best_mean = -1.0
            for k in cands:
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
                    lbl, sc = self.classify_tile(c, styles=styles)
                    rect: Rect = (x1, y_top_c, x2 - x1, y_bot_c - y_top_c)
                    dets.append((rect, lbl, sc))
                mean_sc = float(np.mean([d[2] for d in dets])) if dets else 0.0
                if mean_sc > best_mean:
                    best_mean = mean_sc
                    best_dets = dets
            return best_dets, best_mean

        best_standing_dets, best_standing_mean = _try_counts(cand_counts, probe_styles)

        # 探针路由失误（风格误判/尺度失配致整行低分）→ 全候选全模板重扫一次
        if best_standing_mean < 0.55 and probe_styles is not None:
            fb_dets, fb_mean = _try_counts(
                list(dict.fromkeys(c_adj + c_tec)), None)
            if fb_mean > best_standing_mean:
                best_standing_dets, best_standing_mean = fb_dets, fb_mean

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

        # 核心防伪与非牌局拦截：
        # 真实麻将手牌匹配时，最高置信度通常 >= 0.68，均值通常 >= 0.55。
        # 真机画面可能因分辨率、光线、压缩而使置信度比测试图低约 5-10%，
        # 因此阈值略低于理论值，在实际设备上保留更多有效帧。
        # 在大厅、载入中、结算界面等非牌局场景，背景匹配分通常 0.3~0.5，此门槛仍能有效过滤。
        if best_standing_mean < 0.55 or top_conf < 0.68:
            self.last_drawn_tile = None
            return []

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
        return Stage(result=flat, image=image)
