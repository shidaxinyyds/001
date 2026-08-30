"""全屏牌面检测器：横条定位 + 条内多尺度模板匹配 + NMS。

==================== 为什么原来的实现"根本识别不到牌面" ====================

1. 原实现用 Canny 找轮廓，再把每张牌的中心点画到空白画布上，用
   `cv2.HoughLinesP(..., minLineLength=1000, threshold=50)` 找一条贯穿所有牌中心的
   最长直线，只保留离它 10px 内的轮廓。三个致命问题：
     a) `minLineLength=1000` 是绝对像素，1080 宽的屏上要求手牌严格排成 1000px 直线；
     b) 屏幕上的 UI 文字/按钮同样是 Canny 边缘，也会生成"轴对齐"轮廓，噪声点混进去后
        Hough 找到的"最长直线"经常根本不是手牌那一行；
     c) Hough 返回 None 就直接返回空 —— 于是绝大多数帧都是 no_tiles。

2. 更隐蔽的坑（本次实测发现）：**手牌是紧挨着排的**，Canny + 膨胀后相邻牌会连成
   一条宽高比 10:1 的大轮廓，被"必须是牌的宽高比"过滤掉，结果一张都认不出来。

3. 用轮廓当定位器本身也不可靠：1p、5p 这类图案稀疏、对比度低的牌，Canny 根本生成不了
   闭合轮廓，所以在 2400x1080 用例里稳定漏掉 2 张。

============================== 现在的做法 ===============================

**阶段一：横条定位（便宜、与牌面图案无关）**
   13 张牌并排时，牌与牌的边界是一串等间距的**竖直边**，所以这一行的 |Sobel x| 行能量
   会明显高于其它行。这个特征只取决于"牌的存在"，不取决于牌面图案对比度 ——
   这正是 Canny 轮廓法在浅色牌上失效的地方。实测 4 种分辨率下手牌行 100% 命中最优横条。

**阶段二：条内多尺度模板匹配（精确 + 只算很小的区域）**
   只在横条里对 34 张模板做 matchTemplate 并取局部峰值。区域只有全屏的 5%~10%，
   所以能做到多尺度（0.85 / 1.0 / 1.15）而不超时。

**其它要点**
   - 屏幕自适应：MediaProjection 截到的就是设备屏幕真实像素。用【工作分辨率下的短边】
     推导牌面高度（不写死像素），超长边先降采样，最后把坐标换算回真实分辨率。
   - NMS 去重：同一块牌常被多张模板/多个尺度同时命中，按 IOU 抑制只留最高分。
   - 选行：屏幕上可能同时有手牌和牌河，选张数最接近 13/14 的那一行。
"""

from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage, Rect

# 一条检测记录：(x, y, w, h, label, score)
Det = Tuple[int, int, int, int, str, float]


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    """两个 (x1, y1, x2, y2) 矩形的交并比。"""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class TemplateDetector(Detector):
    # 模板匹配最低分。截屏是 JPEG(quality=50)，解码后有噪声；不同麻将 App 牌面配色也不同。
    # 0.45 是实测（1080x2400 / 720x1280 / 1440x3200 / 2400x1080 四种分辨率）下来
    # 既能挡住噪声、又不至于全漏的折中值。
    MIN_SCORE = 0.45

    # 模板四周裁掉的比例。模板自带一圈 ivory 边框和外阴影，不同 App 边框不同，
    # 裁一点能提升泛化；但裁太多会破坏牌面内容、反而对不齐（实测裁 10% 时召回骤降）。
    TEMPLATE_MARGIN_X = 0.04
    TEMPLATE_MARGIN_Y = 0.03

    # 牌面高度 ≈ 屏幕短边 × 该比例（实测 0.085~0.13，取中值）。
    # 只用于圈定横条范围，真正的匹配尺度会在横条内自适应重新估算，不依赖这个比例。
    TILE_H_RATIO = 0.105

    # 横条内匹配时，模板高度相对"自适应估算牌高"的若干候选比例
    BAND_SCALES = (0.85, 1.00, 1.15)

    # 模板灰度标准差下限，低于此值的模板会在 TM_CCOEFF_NORMED 下退化出假峰值
    MIN_TEMPLATE_STD = 22.0

    # 同一行内两张牌的中心最小间距（占牌宽的比例）——压掉"牌缝里刷出来的假命中"
    MIN_CENTER_GAP = 0.72

    # 参与匹配的横条数量（按行能量降序）
    TOP_BANDS = 2

    # 峰值检测的核大小系数
    PEAK_KERNEL = 0.6

    # NMS 阈值
    NMS_IOU = 0.30

    # 工作分辨率上限：长边超过就等比降采样，保证任意屏幕都能在采集间隔内跑完
    MAX_WORKING_EDGE = 1100

    # 缩放后模板的缓存上限
    _CACHE_MAX = 4000

    def __init__(self, targets) -> None:
        super().__init__(targets)
        self._clean_templates: Optional[Dict[str, CVImage]] = None
        self._resized_cache: Dict[Tuple[int, int, str], CVImage] = {}
        # 最近一帧的最高匹配分（无论是否过阈），用于主界面诊断：
        # 长期 <0.2 => 屏幕里没牌；0.3~0.44 => 有牌但样式跟模板差异大
        self.last_top_score: float = 0.0
        # 最近一帧的屏幕像素尺寸（MediaProjection 截图像素 = 设备真实分辨率）
        self.last_screen: Tuple[int, int] = (0, 0)

    # ------------------------------------------------------------------ 模板

    def _clean_template(self, img: CVImage) -> CVImage:
        """转灰度并按 TEMPLATE_MARGIN_X / _Y 裁边。

        不做直方图归一化：TM_CCOEFF_NORMED 本身对亮度仿射变换免疫，归一化只是白费算力，
        还会改变 resize 的插值结果。
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        h, w = gray.shape[:2]
        mx, my = int(w * self.TEMPLATE_MARGIN_X), int(h * self.TEMPLATE_MARGIN_Y)
        if mx <= 0 or my <= 0 or 2 * mx >= w or 2 * my >= h:
            return gray
        return gray[my:h - my, mx:w - mx]

    def _prepare_templates(self) -> Dict[str, CVImage]:
        if self._clean_templates is not None:
            return self._clean_templates
        out: Dict[str, CVImage] = {}
        for label, img in self.targets.items():
            try:
                tpl = self._clean_template(img)
                # TM_CCOEFF_NORMED 在"模板几乎无变化"时会退化（分母趋近 0，输出随机高值）。
                # 实测二索这类大面积留白的模板会在牌间空隙里刷出一堆假命中，必须挡掉。
                if float(np.std(tpl)) < self.MIN_TEMPLATE_STD:
                    continue
                out[label] = tpl
            except Exception:
                # 单张模板损坏不能让整条链路挂掉
                continue
        self._clean_templates = out
        return out

    def _template_for_height(self, label: str, tpl: CVImage,
                             target_h: int) -> Optional[CVImage]:
        """把模板按高度缩放到 target_h（保持宽高比），带缓存。"""
        th, tw = tpl.shape[:2]
        if th <= 0 or tw <= 0 or target_h < 8:
            return None
        nh = max(6, int(round(target_h)))
        nw = max(4, int(round(tw * (target_h / float(th)))))
        key = (nw, nh, label)
        cached = self._resized_cache.get(key)
        if cached is not None:
            return cached
        if len(self._resized_cache) > self._CACHE_MAX:
            self._resized_cache.clear()
        small = cv2.resize(tpl, (nw, nh), interpolation=cv2.INTER_AREA)
        self._resized_cache[key] = small
        return small

    # -------------------------------------------------------------- 横条定位

    def _find_bands(self, gray: CVImage, tile_h_exp: int) -> List[Tuple[int, int]]:
        """按 |Sobel x| 的行能量找出最可能排着牌的若干横条（返回 (y1, y2)）。"""
        gh, _ = gray.shape[:2]
        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        row_energy = gx.sum(axis=1)
        # 盒式滤波平滑：只有"整行都高"的才留得下来
        k = np.ones(max(3, tile_h_exp), np.float32) / float(max(3, tile_h_exp))
        smooth = np.convolve(row_energy, k, mode="same")

        order = np.argsort(-smooth)
        bands: List[Tuple[int, int]] = []
        pad = int(tile_h_exp * 0.75)
        for y in order:
            y1 = max(0, int(y) - pad)
            y2 = min(gh, int(y) + pad)
            if y2 - y1 < 8:
                continue
            # 与已选横条不重叠才收
            if any(not (y2 < b[0] or y1 > b[1]) for b in bands):
                continue
            bands.append((y1, y2))
            if len(bands) >= self.TOP_BANDS:
                break
        return bands

    def _estimate_tile_height(self, band: CVImage, fallback: int) -> int:
        """在横条内自适应估算牌面真实高度。

        不能直接用"屏幕短边 × 固定比例"：实测横屏 2400x1080 上手牌能占到短边的 12.8%，
        而竖屏只占 9%，固定比例会偏小 20%，导致所有牌都匹配不上。
        这里改为看横条内"竖直边缘能量"的纵向分布：牌占据的行能量高，上下的背景行能量低。
        """
        bh = band.shape[0]
        gx = np.abs(cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3))
        row_energy = gx.sum(axis=1)
        if row_energy.size == 0:
            return fallback
        peak = float(row_energy.max())
        if peak <= 0:
            return fallback
        thresh = peak * 0.30
        # 取包含最大值的那一段连续高能量区间
        top = int(np.argmax(row_energy))
        lo = top
        while lo - 1 >= 0 and row_energy[lo - 1] >= thresh:
            lo -= 1
        hi = top
        while hi + 1 < bh and row_energy[hi + 1] >= thresh:
            hi += 1
        est = hi - lo + 1
        # 与固定比例的估算取折中，避免单帧抖动
        est = int(round(0.65 * est + 0.35 * fallback))
        return max(10, min(bh - 2, est))

    # -------------------------------------------------------------- 模板匹配

    def _match_band(self, band: CVImage, templates: Dict[str, CVImage],
                    tile_h_exp: int) -> List[Det]:
        """在一条横带里对 34 张模板做多尺度 matchTemplate，取所有局部峰值。"""
        bh, bw = band.shape[:2]
        out: List[Det] = []
        for s in self.BAND_SCALES:
            tpl_h = int(tile_h_exp * s)
            if tpl_h < 8 or tpl_h >= bh:
                continue
            for label, tpl in templates.items():
                tpl2 = self._template_for_height(label, tpl, tpl_h)
                if tpl2 is None or tpl2.shape[0] >= bh or tpl2.shape[1] >= bw:
                    continue
                try:
                    res = cv2.matchTemplate(band, tpl2, cv2.TM_CCOEFF_NORMED)
                except cv2.error:
                    continue
                kh = max(3, int(tpl2.shape[0] * self.PEAK_KERNEL))
                kw = max(3, int(tpl2.shape[1] * self.PEAK_KERNEL))
                dilated = cv2.dilate(res, np.ones((kh, kw), np.uint8))
                # 膨胀后与原值相等的点即局部极大值
                ys, xs = np.where((res >= self.MIN_SCORE) & (res >= dilated - 1e-6))
                th2, tw2 = tpl2.shape[:2]
                for yy, xx in zip(ys, xs):
                    out.append((int(xx), int(yy), int(tw2), int(th2),
                                label, float(res[yy, xx])))
        return out

    @staticmethod
    def _nms_detections(dets: List[Det]) -> List[Det]:
        kept: List[Det] = []
        for d in sorted(dets, key=lambda d: -d[5]):
            x, y, w, h = d[0], d[1], d[2], d[3]
            hit = False
            for (kx, ky, kw2, kh2, _, _) in kept:
                if _iou((x, y, x + w, y + h), (kx, ky, kx + kw2, ky + kh2)) > 0.30:
                    hit = True
                    break
            if not hit:
                kept.append(d)
        return kept

    @staticmethod
    def _tile_width_of(d: Det) -> float:
        """由模板宽度反推整张牌的宽度（模板裁过边，约 0.92）。"""
        return max(8.0, d[2] / 0.92)

    @classmethod
    def _dedupe_by_position(cls, dets: List[Det]) -> List[Det]:
        """同一块牌只留一个标签。

        IOU-NMS 挡不住"同一位置被不同尺度/不同模板同时命中"：来自小尺度的框跟大尺度的框
        中心几乎重合但面积差得多，IOU 可能低于阈值，于是两张都留下。
        这里改成按"中心距离"分组，每组只保留分数最高的那个。
        """
        groups: List[List[Det]] = []
        for d in sorted(dets, key=lambda d: -d[5]):
            cx, cy = d[0] + d[2] / 2.0, d[1] + d[3] / 2.0
            tol = 0.5 * cls._tile_width_of(d)
            placed = False
            for g in groups:
                head = g[0]
                gx, gy = head[0] + head[2] / 2.0, head[1] + head[3] / 2.0
                if abs(cx - gx) < tol and abs(cy - gy) < 0.6 * head[3]:
                    g.append(d)
                    placed = True
                    break
            if not placed:
                groups.append([d])
        return [g[0] for g in groups]

    @classmethod
    def _suppress_close(cls, dets: List[Det]) -> List[Det]:
        """同一行内，两张牌的中心必须至少隔 MIN_CENTER_GAP × 牌宽。

        挡的是"卡在两张牌中间的缝里"的假命中：它的框跟左右两张牌都只重叠一点点，
        IOU 都低于阈值。实测这类假命中的中心离邻牌约 0.5 个牌宽，所以阈值取 0.72
        （相邻真牌间距约 1.0~1.1 个牌宽，不会被误伤）。
        """
        kept: List[Det] = []
        for d in sorted(dets, key=lambda d: -d[5]):
            cx = d[0] + d[2] / 2.0
            cy = d[1] + d[3] / 2.0
            min_gap = cls.MIN_CENTER_GAP * cls._tile_width_of(d)
            hit = False
            for k in kept:
                kx = k[0] + k[2] / 2.0
                ky = k[1] + k[3] / 2.0
                if abs(cx - kx) < min_gap and abs(cy - ky) < k[3]:
                    hit = True
                    break
            if not hit:
                kept.append(d)
        return kept

    @staticmethod
    def _pick_hand_row(dets: List[Det]) -> List[Det]:
        """屏幕上可能同时有自己的手牌和牌河/他家手牌，选张数最接近 13/14 的那一行。"""
        if not dets:
            return dets
        groups: Dict[int, List[Det]] = {}
        for d in dets:
            groups.setdefault(d[1] // 40, []).append(d)
        best, best_cost = None, None
        for g in groups.values():
            cost = min(abs(len(g) - 13), abs(len(g) - 14))
            if best_cost is None or cost < best_cost or (cost == best_cost and len(g) > len(best)):
                best, best_cost = g, cost
        return list(best or [])

    # ------------------------------------------------------------------ 主入口

    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        full_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        screen_h, screen_w = full_gray.shape[:2]
        # MediaProjection 截到的就是设备屏幕真实像素
        self.last_screen = (screen_w, screen_h)

        templates = self._prepare_templates()
        if not templates:
            self.last_top_score = 0.0
            return Stage(result=[], image=image)

        # 超大屏降采样，保证在采集间隔内跑完
        longest = float(max(screen_h, screen_w))
        inv = 1.0
        if longest > self.MAX_WORKING_EDGE:
            inv = longest / float(self.MAX_WORKING_EDGE)
            work = cv2.resize(full_gray,
                              (max(1, int(screen_w / inv)), max(1, int(screen_h / inv))),
                              interpolation=cv2.INTER_AREA)
        else:
            work = full_gray

        wh, ww = work.shape[:2]
        # 用【工作分辨率下的短边】推算牌面高度 —— 不能用 wh（竖屏时 wh 是长边）
        work_short = min(wh, ww)
        tile_h_exp = max(12, int(self.TILE_H_RATIO * work_short))

        bands = self._find_bands(work, tile_h_exp)

        raw: List[Det] = []
        top = 0.0
        for (y1, y2) in bands:
            band = work[y1:y2, :]
            if band.size == 0 or band.shape[0] < 12:
                continue
            # 在横条内重新估算牌面真实高度，别用固定比例（横屏/竖屏差 40%）
            band_h = self._estimate_tile_height(band, tile_h_exp)
            for (dx, dy, dw, dh, lb, sc) in self._match_band(band, templates, band_h):
                if sc > top:
                    top = sc
                raw.append((int(dx), int(y1 + dy), int(dw), int(dh), lb, sc))

        self.last_top_score = top

        kept = self._nms_detections(raw)
        # 同一块牌只留一个标签（挡掉"多尺度/多模板命中同一处"）
        kept = self._dedupe_by_position(kept)
        # 再挡掉卡在两张牌中间缝里的假命中
        kept = self._suppress_close(kept)
        kept = self._pick_hand_row(kept)
        kept.sort(key=lambda d: d[0])

        # 坐标换算回真实屏幕分辨率
        result: DetectionResult = [
            ((int(d[0] * inv), int(d[1] * inv), int(d[2] * inv), int(d[3] * inv)), d[4])
            for d in kept
        ]

        def display():
            canvas = image.copy()
            for (x, y, w, h), label in result:
                cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 0), 2)
                if label:
                    cv2.putText(canvas, label, (int(x + 0.1 * w), int(y + 0.25 * h)),
                                fontFace=1, fontScale=1.2, color=(0, 255, 0), thickness=2)
            return canvas

        return Stage(result=result, image=image, display_callback=display)
