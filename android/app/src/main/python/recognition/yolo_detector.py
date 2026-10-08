# -*- coding: utf-8 -*-
"""YOLO11-Nano (2024年底最新一代架构) 端到端目标检测与轻量推理引擎。

集成移动端量化与推理加速：
1. 原生 OpenCV cv2.dnn 多线程并行 (setNumThreads) 与半精度 FP16 计算单元 (DNN_TARGET_CPU_FP16)；
2. 全流程 NumPy 矢量化候选框逆透视还原与物理长宽比剪枝 (50x加速)；
3. Class-Agnostic 空间非重叠 NMS 与相邻手牌 1D 物理节距精密对齐；
4. 完美兼容现有 Detector 接口协议，输出 (rect, label, conf) 检测结果。
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

# 空白条带护栏：detect_strip 把输入等比 letterbox 到全 0（纯黑）画布上，
# 而 YOLO 在整幅纯黑输入上会凭空产出一批等间距高置信框（实测黑边竖屏
# 截屏的 899x600 纯黑条带产出 5 个框，相应手牌 3p3p5p5p，conf 0.95，整帧报 status=ok）。
# 实测喂进 detect_strip 的条带灰度>120 占比：纯黑条带 0.52%，而真条带最低
# 7.8%（牌河）、手牌条带最低 34.1%（雀神定缺压暗帧）——0.52% 与 7.8% 之间
# 取 2%，拦幻觉的余量 3.8 倍，误杀真条带的余量 3.9 倍。
BLANK_STRIP_BRIGHT_FRAC = 0.02
BLANK_STRIP_V = 120


class YOLODetector(Detector):
    # 模板覆盖层的放行线：classify_tile 的 ref_conf 高于此值才夺走 YOLO 标签。
    # 与 __init__ 的 conf_thresh 是两回事（后者管"要不要这个框"，前者管
    # "框里的牌信谁"），数值巧合相同，改一个不等于改另一个。
    COVER_GATE = 0.40

    def __init__(self, model_path: Optional[str] = None, conf_thresh: float = 0.40, nms_thresh: float = 0.35):
        super().__init__({})
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh
        self.classes = CLASSES

        if model_path is None:
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            candidates = [
                os.path.join(cur_dir, "models", "yolo11n_mahjong.onnx"),
                os.path.join(cur_dir, "models", "yolo11_mahjong.onnx"),
                os.path.join(cur_dir, "models", "yolo_mahjong.onnx"),
            ]
            for cand in candidates:
                if os.path.isfile(cand):
                    model_path = cand
                    break
            if model_path is None:
                model_path = candidates[-1]

        self.model_path = model_path
        self.net = None
        self.engine_type = "none"
        self.last_top_score: float = 0.0
        self.last_screen: Tuple[int, int] = (0, 0)
        self.last_drawn_tile: Optional[str] = None
        self._glyphs = None
        self._styles = None

        try:
            from .tencent_grid_detector import TencentGridDetector
            self._phase_helper = TencentGridDetector()
        except Exception:
            self._phase_helper = None

        # 优化多核并发推理
        try:
            threads = min(4, max(1, os.cpu_count() or 4))
            cv2.setNumThreads(threads)
        except Exception:
            pass

        if os.path.isfile(model_path):
            try:
                self.net = cv2.dnn.readNetFromONNX(model_path)
                self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                # 移动端半精度 FP16 NEON 加速适配，不支持时平滑降级至 CPU FP32
                target_fp16 = getattr(cv2.dnn, "DNN_TARGET_CPU_FP16", None)
                if target_fp16 is not None:
                    try:
                        self.net.setPreferableTarget(target_fp16)
                        self.engine_type = "OpenCV-DNN-FP16 (YOLO11-Nano)"
                    except Exception:
                        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                        self.engine_type = "OpenCV-DNN-FP32 (YOLO11-Nano)"
                else:
                    self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                    self.engine_type = "OpenCV-DNN-FP32 (YOLO11-Nano)"
                print(f"[YOLODetector] Successfully loaded YOLO11-Nano [{self.engine_type}] from: {model_path}")
            except Exception as e:
                print(f"[YOLODetector] Failed to load ONNX model via cv2.dnn: {e}")
                self.net = None
        else:
            print(f"[YOLODetector] Model file not found at: {model_path}")

    def set_platform_styles(self, platform_key) -> None:
        """把平台风格白名单转给手牌覆盖层用的模板分类器。

        _phase_helper 是本类自己 new 的实例，外部拿不到，所以必须在这里转发——
        否则手牌行的「等距节距重切 + 模板覆盖」仍会扫全 bank，雀神牌风会去
        抢微乐/途游/JJ 的牌。
        """
        helper = getattr(self, "_phase_helper", None)
        if helper is not None and hasattr(helper, "set_platform_styles"):
            helper.set_platform_styles(platform_key)

    def set_mode_tiles(self, avail) -> None:
        """把当前玩法牌集转给手牌覆盖层用的模板分类器（同 set_platform_styles）。

        本类的覆盖层调 classify_tile 时不传 avail（helper 自己 new 的，外部拿不到），
        不转发则全牌玩法在这些调用点上永远读不到字牌。
        """
        helper = getattr(self, "_phase_helper", None)
        if helper is not None and hasattr(helper, "set_mode_tiles"):
            helper.set_mode_tiles(avail)

    def is_dingque_phase(self, image_bgr: np.ndarray) -> bool:
        if self._phase_helper is not None:
            return self._phase_helper.is_dingque_phase(image_bgr)
        return False

    def is_swap_phase(self, image_bgr: np.ndarray) -> bool:
        if self._phase_helper is not None:
            return self._phase_helper.is_swap_phase(image_bgr)
        return False

    def is_pick_phase(self, image_bgr: np.ndarray) -> bool:
        if self._phase_helper is not None:
            return self._phase_helper.is_pick_phase(image_bgr)
        return False

    def detect_pick_candidates(self, image_bgr: np.ndarray) -> List[str]:
        if self._phase_helper is not None:
            return self._phase_helper.detect_pick_candidates(image_bgr)
        return []

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

        # 0. 空白裁片护栏（必须在推理前）：黑边/空桌面/全暗区域直接判“无牌”，
        # 绝不允许把纯黑裁片喂给模型——它会在黑画布 letterbox 上臆造整排牌。
        try:
            _g = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2GRAY)[::4, ::4]
            if _g.size and float(np.count_nonzero(_g > BLANK_STRIP_V)) / _g.size < BLANK_STRIP_BRIGHT_FRAC:
                return []
        except Exception:
            pass

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

        # 3. 矢量化反算真实像素坐标与物理长宽比剪枝 (NumPy 50x 高速并行)
        cx = cand_boxes[:, 0] - pad_x
        cy = cand_boxes[:, 1] - pad_y
        bw = cand_boxes[:, 2]
        bh = cand_boxes[:, 3]

        in_bounds = (cx >= -bw * 0.5) & (cx <= nw + bw * 0.5) & (cy >= -bh * 0.5) & (cy <= nh + bh * 0.5)
        rw = bw / scale
        rh = bh / scale
        valid_dim = (rw > 0) & (rh > 0)
        aspect = np.divide(rw, np.maximum(rh, 1e-5))
        valid_aspect = (aspect >= 0.45) & (aspect <= 1.05)

        keep = in_bounds & valid_dim & valid_aspect
        if not np.any(keep):
            return []

        rx = ((cx[keep] - bw[keep] * 0.5) / scale).astype(int)
        ry = ((cy[keep] - bh[keep] * 0.5) / scale).astype(int)
        rw_k = rw[keep].astype(int)
        rh_k = rh[keep].astype(int)

        nms_boxes = np.stack([rx, ry, rw_k, rh_k], axis=1).tolist()
        filtered_scores = [float(s) for s in cand_scores[keep]]
        filtered_classes = [int(c) for c in cand_classes[keep]]

        # 4. Class-Agnostic 空间非重叠 NMS
        indices = cv2.dnn.NMSBoxes(
            nms_boxes, filtered_scores, score_threshold=self.conf_thresh, nms_threshold=self.nms_thresh
        )
        cand_dets = []
        if len(indices) > 0:
            for idx in indices.flatten():
                x, y, bw_val, bh_val = nms_boxes[idx]
                cid = filtered_classes[idx]
                conf = filtered_scores[idx]

                abs_x = max(0, offset_x + x)
                abs_y = max(0, offset_y + y)
                abs_w = min(orig_w - x, bw_val)
                abs_h = min(orig_h - y, bh_val)

                if abs_w >= 12 and abs_h >= 16:
                    rect: Rect = (abs_x, abs_y, abs_w, abs_h)
                    cand_dets.append((rect, self.classes[cid], round(conf, 3)))

        # 4b. 1D 水平中心距 NMS：相邻手牌中心距不可过密（小于 0.55 * 平均牌宽）
        cand_dets_sorted = sorted(cand_dets, key=lambda d: -d[2])
        detections = []
        for d in cand_dets_sorted:
            x1, y1, w1, h1 = d[0]
            cx1 = x1 + w1 * 0.5
            suppressed = False
            for k in detections:
                x2, y2, w2, h2 = k[0]
                cx2 = x2 + w2 * 0.5
                avg_w = (w1 + w2) * 0.5
                if abs(cx1 - cx2) < avg_w * 0.55:
                    suppressed = True
                    break
            if not suppressed:
                detections.append(d)

        top_conf = max([d[2] for d in detections], default=0.0)
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
                pass

        detections = guarded_detections

        # 7. 从左至右严格按 X 坐标排序
        detections.sort(key=lambda d: d[0][0])
        return detections

    @staticmethod
    def _pitch_consistent(tiles) -> bool:
        """手牌排版是否真的等距（决定能不能按首尾框算 pitch 重切）。

        有漏检或有牌被抬高时，相邻步进会成倍偏离中位；此时强行等距重切，
        切出的每个 patch 都横跨两张牌，模板分类在垃圾输入上给出的标签
        比 YOLO 自己的更差（途游帧 34 实测：步进中位 142px 而最大偏差
        126px，补风格 bank 反而让命中从 4/12 掉到 2/12）。
        半个步进的容差：真等距排版受检测抖动影响通常在 ±10px 量级。
        """
        xs = sorted(d[0][0] for d in tiles)
        if len(xs) < 3:
            return True
        steps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        med = float(np.median(steps))
        if med <= 0:
            return False
        return max(abs(s - med) for s in steps) <= med * 0.5

    @staticmethod
    def _slot_band(tiles, cx, fallback_top, fallback_bot):
        """槽位的纵向范围：以本张框的中心为准，高度取该行中位。

        重切只解决 x 方向的对齐；y 方向必须跟着每张牌自己的位置走，否则
        一行里有牌抬高时，所有 patch 都会被包络撑高，牌面在 patch 里的
        尺度与位置同时错位。
        为何用中心而不是顶部：顶到中心这一半恰好抵消框自身的偏矮/偏高
        （按顶部对齐时，一个偏矮的框会把牌底整块切掉；实测微乐帧 06 丢 1 张，
        而按行中位高 + 顶部对齐又会让雀神帧 14 从 14/14 回退到 13/14）。
        同一行牌物理等高，所以中位数是高度的稳定估计。
        """
        if not tiles:
            return fallback_top, fallback_bot - fallback_top
        hs = sorted(d[0][3] for d in tiles)
        med_h = int(hs[len(hs) // 2])
        best, bd = tiles[0][0], 1 << 30
        for d in tiles:
            x, y, w, _h = d[0]
            dist = abs(x + w / 2.0 - cx)
            if dist < bd:
                bd, best = dist, d[0]
        cy = best[1] + best[3] // 2
        return cy - med_h // 2, med_h

    def _classify_slots(self, image, tiles, face_mask):
        """不等距手牌行的判读：逐张原框分类 + 在异常步进处补出漏检的槽。

        为何必须补槽：途游帧 34/36 物理各 14 张，YOLO 只出 12 框，而所有框
        conf 都在 0.95~1.00、框宽无异常，把 conf_thresh 从 0.40 一路降到 0.20
        检出数纹丝不动——所以漏检不是置信度问题，而是抬高牌（摸牌位/杠后
        补牌位）位置上根本没有 proposal。分类器再准也读不到没切出来的牌。
        实测补槽让帧 34 从 3/14 升到 9/14、帧 36 从 5/14 升到 10/14。

        两条约束各自来自一次失败尝试：
          * 只做「局部插空」，原框一律保留。曾用单一 pitch 重排整行，把本来
            正确的帧 40 从 11/12 崩到 5/12——抬高区与立牌区的间距本来就不等，
            用全局 pitch 外推必漂。
          * 步进一律用**框中心**差。用起点差时帧 40 被误插成 14 槽（物理只有
            13 张）：它最左一枚带「赖」角标、框宽 206 而其它约 150，起点步进
            被抻到 197 而触发。中心差对框宽抖动免疫（帧 40 降到 173=1.27x
            不触发，而真漏检的 259/228/242 仍是 1.8x/1.6x/1.8x 照常触发）。
        """
        if not tiles:
            return []
        order = sorted(tiles, key=lambda d: d[0][0])
        centers = [d[0][0] + d[0][2] / 2.0 for d in order]
        if len(centers) >= 3:
            csteps = sorted(centers[i + 1] - centers[i] for i in range(len(centers) - 1))
            pitch = csteps[len(csteps) // 2]
        else:
            pitch = float(np.median([d[0][2] for d in order]))
        if pitch <= 0:
            return list(order)

        y_top_s = min(d[0][1] for d in order)
        y_bot_s = max(d[0][1] + d[0][3] for d in order)

        # 牌面占比闸门：步进大既可能是“漏检了一张牌”，也可能是“相邻框向外扩
        # 造成的假空档”。前者插入点真的盖着牌面（低饱和高亮度），后者是桌布。
        # 实测这道闸门单独不够用（帧 40 的误插点落在真牌上），所以它只是中心
        # 步进判据的第二道防线，不能反过来拿它当主判据。
        def face_ratio(cx):
            a = max(0, int(cx - pitch / 2))
            b = min(face_mask.shape[1], int(cx + pitch / 2))
            return float(face_mask[:, a:b].mean()) if b > a else 0.0

        rr = sorted(face_ratio(c) for c in centers)
        ref_med = rr[len(rr) // 2] if rr else 0.0

        plan = []  # (cx, filled, src_rect, yolo_lbl, yolo_conf)
        for i, c in enumerate(centers):
            plan.append((c, False, order[i][0], order[i][1], order[i][2]))
            if i + 1 >= len(centers):
                continue
            step = centers[i + 1] - c
            k = int(round(step / pitch)) - 1
            # 只在步进明显过大（≥ 1.45x）时补槽，避免把正常抖动当漏检
            if k < 1 or step < 1.45 * pitch:
                continue
            for j in range(1, k + 1):
                cx = c + step * j / (k + 1.0)
                if ref_med > 0.05 and face_ratio(cx) < 0.6 * ref_med:
                    continue
                plan.append((cx, True, None, None, 0.0))

        scored = []  # (rect, lbl, conf, filled)
        for cx, filled, src_rect, yolo_lbl, yolo_conf in plan:
            if not filled:
                # 原框槽：切法与原逐张兜底**完全一致**。补槽是新增能力，
                # 不该顺手改掉已有槽的切法。两个轴上试过“顺便修一下”，都是负的：
                #   * 横向改中心对齐：帧 40（本来就没漏检）11/12 → 9/12，
                #     右侧抬高区的间距大于行 pitch；
                #   * 纵向改 _slot_band：帧 40 再跌到 5/12，而行中位高对抬高
                #     牌偏矮会把牌面切掉；换来的只是帧 36 的 +1。
                # 原则：只对“本来读不到”的槽新增能力，不动已经能读的槽。
                rx, ry, rw, rh = src_rect
                patch = image[ry:ry + rh, rx:rx + rw]
                if patch.size == 0:
                    scored.append((src_rect, yolo_lbl, yolo_conf, False))
                    continue
                ref_lbl, ref_conf = self._phase_helper.classify_tile(patch)
                lbl = ref_lbl if (ref_lbl and ref_conf and ref_conf >= self.COVER_GATE) else yolo_lbl
                scored.append((src_rect, lbl, max(yolo_conf, ref_conf or 0.0), False))
                continue
            # 补出来的槽：没有自己的框，只能按几何中心切
            x1 = max(0, int(round(cx - pitch / 2)))
            x2 = int(round(cx + pitch / 2))
            sy, sh = self._slot_band(order, cx, y_top_s, y_bot_s)
            patch = image[sy:sy + sh, x1:x2]
            if patch.size == 0:
                continue
            ref_lbl, ref_conf = self._phase_helper.classify_tile(patch)
            # 补出来的槽没有 YOLO 兜底：模板不达标就整槽丢弃。给一个
            # 没证据的标签比承认这里读不到更糟——它会污染引擎的同牌计数
            # 与后续推荐。
            if ref_lbl and ref_conf and ref_conf >= self.COVER_GATE:
                scored.append(((x1, sy, x2 - x1, sh), ref_lbl, float(ref_conf), True))

        # 补槽后必须复查同牌数：多插的槽可能与相邻框盖住同一张牌，而引擎的
        # 物理守卫是“同牌 >4 就整手拒绝”——那样补槽反而让这一手彻底读不出来。
        # 必须先用原框槽建立基线计数，再逐个决定插槽：插槽在序列里落在中间，
        # 单遍累加会让后面的原框槽把计数推过 4（实测测出一个这样的错）。
        # 原框槽一律保留，只回滚补出来的槽。
        counts = {}
        for rect, lbl, conf, filled in scored:
            if lbl and not filled:
                counts[lbl] = counts.get(lbl, 0) + 1
        out = []
        for rect, lbl, conf, filled in scored:
            if lbl and filled and counts.get(lbl, 0) >= 4:
                continue
            if lbl:
                counts[lbl] = counts.get(lbl, 0) + 1
            out.append((rect, lbl, conf))
        return out

    def detect_all_rows(self, image: CVImage, classify: bool = True, allow_rotation: bool = False, allow_retry: bool = False, **kwargs) -> List[List[Tuple[Rect, Optional[str], float]]]:
        """扫描全图，检出手牌行与牌河各行。

        `classify=False` 表示「只要几何，不要牌面」：跳过模板精修与补槽判读。
        这个参数过去只是签名里有、函数体从不读 —— 于是两个明确声明「不需要标签」
        的调用方（`Engine._verify_hand_evidence` 拿张数判牌桌、
        `Engine._probe_orientation` 阶段 A 的几何筛选）每帧都照付全套模板匹配：
        jj 平台实测 997.8ms/帧，占该帧总耗时 56.6%（`build/live_diag_before.txt`）。
        `recognition/structural.py` 的同名参数一直是真跳过的（173ms → 17ms），
        本次把 YOLO 通道对齐到同一语义。

        跳过的是「在 YOLO 框之上做的模板精修」，YOLO 自带的类别标签与几何过滤
        （牌高筛选 / 无标签框剔除 / 牌河分行）一律保留，所以返回的行列结构与
        精修路径同源，只有补槽（仅在步进异常时新增槽位）不再发生。
        """
        if image is None or image.size == 0 or not self.is_available:
            return []

        h, w = image.shape[:2]
        self.last_drawn_tile = None

        # 1. 动态自适应手牌条带定位 (基于 HSV 牌面掩码锁定手牌横排核心区)
        y1 = int(0.70 * h)
        roi = image[y1:h, :]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 130)).astype(np.uint8)
        col_counts = mask.sum(axis=0)
        valid_x = np.where(col_counts > (h - y1) * 0.15)[0]

        if len(valid_x) > 50:
            x_min, x_max = valid_x[0], valid_x[-1]
            row_counts = mask[:, x_min:x_max].sum(axis=1)
            valid_y = np.where(row_counts > (x_max - x_min) * 0.10)[0]
            y_top = y1 + max(0, valid_y[0] - 12) if len(valid_y) > 0 else y1
            y_bot = min(h, y1 + valid_y[-1] + 12) if len(valid_y) > 0 else h
            x_left = max(0, x_min - 12)
            x_right = min(w, x_max + 12)
        else:
            y_top, y_bot = y1, h
            x_left, x_right = 0, w

        hand_crop = image[y_top:y_bot, x_left:x_right]
        hand_tiles = self.detect_strip(hand_crop, offset_x=x_left, offset_y=y_top)

        # 手牌行会圈进自家牌河/被换出的弃牌：残局手牌少的时候尤其明显。
        # 用“牌高 / 行高中位数”筛：实测途游帧 41 右侧两张弃牌只有 0.70/0.72，
        # 而真被抬高的摸牌最矮也有 0.87（帧 34），两者之间有清晰空隙。
        # 摸牌只是整体上移、牌高不变，所以这条判据不会误伤它。
        # 反例同样记下：微乐帧 10 混进来的弃牌与立牌同高、同距（h 完全相等），
        # 这条判据对它无效——那种牌桌态只能靠上游 ROI，不在几何上强求。
        if len(hand_tiles) >= 4:
            hs = sorted(d[0][3] for d in hand_tiles)
            h_med = hs[len(hs) // 2]
            kept = [d for d in hand_tiles if d[0][3] >= 0.78 * h_med]
            if len(kept) >= 2:
                hand_tiles = kept

        # 拿不到 label 的框（检出了位置但分类器给不出牌面）不该留在手牌输出里：
        # 实测微乐帧 01 混进来的 2 张弃牌就是这种（rect 存在、label 为空）。
        # 它们会让 count 与真实张数脱节，还会参与后面的等距判定与补槽步进，
        # 把几何判据带偏。保留“至少 2 张”的下限：全行都读不到时应当整行作废
        # 走多帧确认，而不是只剩一张牌去凑一手。
        labelled = [d for d in hand_tiles if d[1]]
        if len(labelled) >= 2:
            hand_tiles = labelled

        if hand_tiles and classify:
            k = len(hand_tiles)
            has_drawn = False
            # 摸牌判定
            if k in (2, 5, 8, 11, 14):
                xs = [d[0][0] for d in hand_tiles]
                ws = [d[0][2] for d in hand_tiles]
                if len(xs) >= 2:
                    diffs = [xs[i+1] - (xs[i] + ws[i]) for i in range(len(xs) - 1)]
                    median_w = float(np.median(ws))
                    if diffs[-1] > max(15.0, median_w * 0.25):
                        has_drawn = True

            # 物理节距精密对齐与分类校准（仅在排版确实等距时）
            if self._phase_helper is not None and k >= 2 and self._pitch_consistent(hand_tiles):
                standing_count = k - 1 if has_drawn else k
                standing_tiles = hand_tiles[:standing_count]
                x_start = standing_tiles[0][0][0]
                x_end = standing_tiles[-1][0][0] + standing_tiles[-1][0][2]
                bw = max(1.0, float(x_end - x_start))
                pitch = bw / float(standing_count)

                y_top_s = min([d[0][1] for d in standing_tiles])
                y_bot_s = max([d[0][1] + d[0][3] for d in standing_tiles])

                calibrated = []
                for i in range(standing_count):
                    x1 = int(round(x_start + i * pitch))
                    x2 = int(round(x_start + (i + 1) * pitch))
                    # 纵向范围逐张取，不用整行包络：包络只在“桌布是绿色”时
                    # 安全（extract_face 能把背景剔掉）。牌被抬高时包络会把
                    # 背景一并塞进 patch，而非绿桌布（JJ 蓝紫）下背景抠不掉，
                    # 牌在 patch 里只剩 150/197 高，尺度错位把万子读成 9p/2z
                    # （帧 30 实测：两路都在同样位置给同样的错）。
                    sy, sh = self._slot_band(standing_tiles, (x1 + x2) // 2, y_top_s, y_bot_s)
                    patch = image[sy:sy + sh, x1:x2]
                    ref_lbl, ref_conf = self._phase_helper.classify_tile(patch)
                    rect = (x1, sy, x2 - x1, sh)
                    yolo_lbl = standing_tiles[i][1]
                    yolo_conf = standing_tiles[i][2]
                    lbl = ref_lbl if (ref_lbl and ref_conf and ref_conf >= self.COVER_GATE) else yolo_lbl
                    conf = max(yolo_conf, ref_conf or 0.0)
                    calibrated.append((rect, lbl, conf))

                if has_drawn:
                    # 摸牌独立切片
                    d_rect, d_yolo_lbl, d_yolo_conf = hand_tiles[-1]
                    dx, dy, dw, dh = d_rect
                    patch_d = image[dy:dy+dh, dx:dx+dw]
                    ref_d_lbl, ref_d_conf = self._phase_helper.classify_tile(patch_d)
                    d_lbl = ref_d_lbl if (ref_d_lbl and ref_d_conf and ref_d_conf >= self.COVER_GATE) else d_yolo_lbl
                    calibrated.append((d_rect, d_lbl, max(d_yolo_conf, ref_d_conf or 0.0)))
                    self.last_drawn_tile = d_lbl

                hand_tiles = calibrated
            elif self._phase_helper is not None:
                # 守卫不通过（有漏检或抬高导致不等距）时的路径：逐张用各自原框
                # 判读，并在异常步进处把漏检的槽补回来。
                hand_tiles = self._classify_slots(image, hand_tiles, mask)

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
