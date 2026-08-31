"""风格无关的麻将牌结构识别器。

设计原则（为什么它不挑牌面风格）：
  1. 定位只看几何：牌 = 横排的等宽亮色矩形。任何麻将游戏都这样画。
  2. 花色/数字只看"排列"：每个数字的图案排列是全行业统一的
     （6p 永远 2列×3行圆、9s 永远 3×3 棒、7s 永远 1+6……）。
     我们把牌面墨迹与 34 张"合成排列掩码"做区域重合度评分，
     掩码由圆盘/胶囊等几何图元画成，不含任何像素风格信息。
  3. 万牌 = 上部数字 + 下部"萬"块：数字用多字体字形掩码做形状匹配
     （字形掩码打包在 images/glyphs/，不依赖设备字体）。
  4. 所有阈值都是相对量（占牌面尺寸的比例），没有一个绝对像素值。
  5. 识别置信度低的牌直接标记低置信，不参与建议计算——宁可不给答案，
     也不给错误答案。

输出接口与 TemplateDetector 完全一致（detect -> Stage[DetectionResult]），
可无缝替换；另提供 last_conf（每张牌的置信度）供引擎过滤。
"""

import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage, Rect

_HERE = os.path.dirname(os.path.abspath(__file__))
_GLYPH_DIR = os.path.join(_HERE, "images", "glyphs")

# ---------------------------------------------------------------- 排列表
# 元素位置全部为牌面内的归一化坐标 (x, y)。
# 数值来源：对标准模板排列的实测（6p=2列×3行、7p=上斜3+下2×2、
# 8p=2列×4行、8s=2行×4列、9s=3×3 等，均为跨风格不变量）。

def _grid(xs, ys):
    return [(x, y) for y in ys for x in xs]


PIN_PATTERNS: Dict[str, List[Tuple[float, float]]] = {
    "1p": [(0.50, 0.50)],
    "2p": [(0.50, 0.32), (0.50, 0.72)],
    "3p": [(0.27, 0.29), (0.50, 0.52), (0.72, 0.75)],
    "4p": _grid([0.31, 0.69], [0.30, 0.72]),
    "5p": _grid([0.31, 0.69], [0.30, 0.72]) + [(0.50, 0.51)],
    "6p": _grid([0.33, 0.67], [0.21, 0.50, 0.79]),
    "7p": [(0.26, 0.17), (0.52, 0.22), (0.74, 0.33)]
         + _grid([0.29, 0.71], [0.61, 0.86]),
    "8p": _grid([0.33, 0.67], [0.13, 0.37, 0.61, 0.85]),
    "9p": _grid([0.25, 0.50, 0.75], [0.17, 0.50, 0.83]),
}

SOU_PATTERNS: Dict[str, List[Tuple[float, float]]] = {
    "1s": [(0.50, 0.50)],           # 雀鸟：单个大元素
    "2s": [(0.50, 0.32), (0.50, 0.74)],
    "3s": [(0.48, 0.32), (0.26, 0.76), (0.68, 0.76)],
    "4s": _grid([0.31, 0.69], [0.32, 0.76]),
    "5s": _grid([0.25, 0.75], [0.32, 0.76]) + [(0.50, 0.54)],
    "6s": _grid([0.24, 0.50, 0.76], [0.33, 0.77]),
    "7s": [(0.48, 0.25)]
         + _grid([0.24, 0.48, 0.72], [0.54, 0.83]),
    "8s": _grid([0.15, 0.38, 0.62, 0.85], [0.31, 0.77]),
    "9s": _grid([0.22, 0.50, 0.78], [0.20, 0.50, 0.80]),
}

# 字牌字形文件的 ASCII 键（gen_glyphs.py 生成）：東E 南S 西W 北N 中C 發F
_HONOR_GLYPH = {"1z": "E", "2z": "S", "3z": "W", "4z": "N", "6z": "F", "7z": "C"}
_GLYPH_TO_LABEL = {v: k for k, v in _HONOR_GLYPH.items()}

# ------------------------------------------------------- 相对阈值（常量）
FACE_INSET = 0.06            # 牌面内缩比例（去掉牌边框）
INK_MIN_FRAC = 0.035         # 低于此墨迹占比 -> 白板
TILE_H_RATIO = 0.105         # 牌高初值（占屏幕短边）——只用于圈横条
MIN_TILE_ASPECT = 0.52       # 牌宽/牌高 合理下限
MAX_TILE_ASPECT = 0.95       # 牌宽/牌高 合理上限（超界当误检丢弃）
MAX_WORKING_EDGE = 1100      # 长边超过则降采样（性能）
OUT_PENALTY = 0.6            # 排列掩码外墨迹的罚系数
MIN_CONF = 0.30              # 低于此置信度的牌标记为低置信
NUMERAL_REGION = 0.58        # 万牌数字区（占牌高）
# 萬字块起始（占牌高）。设 0.60 而非 0.55，给数字"三"的最下横杠留出 0.05 余量，
# 避免 0.55~0.58 区间的数字笔画被圈进 bottom 把 bbox 拉大、密度拉低、萬检测失败。
WAN_REGION = 0.60            # 萬字块起始（占牌高）
# 面板遮挡补偿（work 坐标）。左 UI 面板会盖住最左牌的内侧 ~24 orig（GT 测得：
# 左牌 x=160 vs 面板右沿 184），但这 24 orig 是纯色面板盖在牌上，画面里"看不到牌
# 内容也无法与面板区分"——若把网格整体左移去补偿，最左牌能找回一点，但中间/右边
# 的牌会一起左移、跨进前一张牌，把原本对的中段判错（实测 3m→1m、3p→2p）。
# 因此左侧**不**做补偿，接受最左牌左侧约 25 orig 被面板遮——3m 的横杠贯穿整宽、
# 丢左端仍可识别；其它花色在最左时同理可承受少量左切。
# 右侧面板（orig x=2105）与最右牌右沿（orig x=2093）之间有 ~12 orig 纯背景间隙，
# 等分网格不该把这间隙算进最右牌（否则最右牌向右膨胀、左侧被切掉一个圆，5p→4p）。
# RIGHT_OVERHANG 取负，把 xr 往左收 ~7 work（≈17 orig），让最右牌右沿贴齐真牌。
# 数值按本游戏 UI 标定；换 UI 需重新量。
LEFT_OVERHANG = 0
RIGHT_OVERHANG = -7          # work 坐标，约 -17 orig


def _pattern_mask(kind: str, pts, fw: int, fh: int) -> np.ndarray:
    """把排列合成为期望墨迹掩码（几何图元，风格无关）。"""
    m = np.zeros((fh, fw), np.uint8)
    if kind == "pin":
        if len(pts) == 1:
            r = 0.35 * min(fw, fh)
            cv2.circle(m, (int(pts[0][0] * fw), int(pts[0][1] * fh)),
                       max(2, int(r)), 255, -1)
            return m
        xs = sorted(set(p[0] for p in pts))
        ys = sorted(set(p[1] for p in pts))
        px = float(np.median(np.diff(xs))) * fw if len(xs) > 1 else float(fw)
        py = float(np.median(np.diff(ys))) * fh if len(ys) > 1 else float(fh)
        r = 0.55 * min(px, py)
        for (x, y) in pts:
            cv2.circle(m, (int(x * fw), int(y * fh)), max(2, int(r)), 255, -1)
    elif kind == "sou":
        if len(pts) == 1:  # 雀鸟
            cv2.ellipse(m, (int(pts[0][0] * fw), int(pts[0][1] * fh)),
                        (max(2, int(0.34 * fw)), max(2, int(0.36 * fh))),
                        0, 0, 360, 255, -1)
            return m
        xs = sorted(set(p[0] for p in pts))
        ys = sorted(set(p[1] for p in pts))
        px = float(np.median(np.diff(xs))) * fw if len(xs) > 1 else float(fw)
        py = float(np.median(np.diff(ys))) * fh if len(ys) > 1 else float(fh)
        w = min(0.16 * fw, 0.78 * px)
        h = min(0.92 * py, 0.90 * fh)
        for (x, y) in pts:
            cv2.ellipse(m, (int(x * fw), int(y * fh)),
                        (max(1, int(w / 2)), max(2, int(h / 2))),
                        0, 0, 360, 255, -1)
    return m


class _GlyphBank:
    """多字体字形掩码缓存：数字(一~九)、字牌(東南西北中發)、萬/万。

    文件名全部 ASCII（避免非 ASCII 路径在某些平台读不出）：
      num_{1..9}_{font}.png   hon_{E,S,W,N,C,F}_{font}.png   wan_{T,S}_{font}.png
    """

    def __init__(self, path: str):
        self.nums: Dict[int, List[np.ndarray]] = {}
        self.hons: Dict[str, List[np.ndarray]] = {}
        self.wans: List[np.ndarray] = []
        try:
            names = sorted(os.listdir(path))
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".png"):
                continue
            img = cv2.imread(os.path.join(path, name), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            m = self._norm(img)
            if not m.any():
                continue
            stem = name[:-4]
            parts = stem.split("_")
            if len(parts) != 3:
                continue
            kind, key, _font = parts
            if kind == "num" and key.isdigit():
                self.nums.setdefault(int(key), []).append(m)
            elif kind == "hon" and len(key) == 1:
                self.hons.setdefault(key, []).append(m)
            elif kind == "wan":
                self.wans.append(m)

    @staticmethod
    def _norm(blob: np.ndarray, size: int = 72) -> np.ndarray:
        """等比居中归一化（letterbox）：先按 bbox 裁出最小外接矩形，再等比缩放居中放到
        size×size 画布上。保留字形纵横比，避免「三」（横杠）被强行拉成正方形再和
        接近正方形的模板比对而失分。
        """
        ys, xs = np.where(blob > 0)
        if len(ys) == 0:
            return np.zeros((size, size), np.uint8)
        crop = (blob[ys.min():ys.max() + 1, xs.min():xs.max() + 1] > 0).astype(np.uint8)
        h, w = crop.shape
        s = min(size / float(h), size / float(w))
        nh, nw = max(1, int(h * s)), max(1, int(w * s))
        r = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
        out = np.zeros((size, size), np.uint8)
        y0, x0 = (size - nh) // 2, (size - nw) // 2
        out[y0:y0 + nh, x0:x0 + nw] = r
        return out

    @staticmethod
    def _match(a: np.ndarray, b: np.ndarray) -> float:
        # IoU 占主导，correlation 仅做细调：避免复杂曲线（如 1s 鸟）靠"笔画密度相似"
        # 在 correlation 上拿高分、误匹配字牌（如「發」）。
        am, bm = a > 0, b > 0
        inter = int((am & bm).sum())
        union = int((am | bm).sum())
        iou = inter / union if union else 0.0
        fa = a.astype(np.float32)
        fb = b.astype(np.float32)
        va, vb = fa - fa.mean(), fb - fb.mean()
        d = float(np.sqrt((va * va).sum() * (vb * vb).sum()))
        corr = float((va * vb).sum() / d) if d > 0 else 0.0
        return 0.7 * iou + 0.3 * max(0.0, corr)

    @staticmethod
    def _match_tolerant(a: np.ndarray, b: np.ndarray) -> float:
        """容错匹配：先对二值掩码做 1 次 3×3 膨胀再算 IoU。

        专用于数字（一~九）字形匹配：不同字体的笔画粗细/曲率差异会让
        原始 IoU 暴跌到 0.1~0.2，膨胀 1px 让"对得上但差 1px"的情形拿到
        合理分数。**不**用于字牌/萬——它们的形状是鉴别关键（1s 鸟 vs 發），
        膨胀会模糊复杂曲线导致误匹配。
        """
        kernel = np.ones((3, 3), np.uint8)
        am = cv2.dilate((a > 0).astype(np.uint8), kernel, iterations=1) > 0
        bm = cv2.dilate((b > 0).astype(np.uint8), kernel, iterations=1) > 0
        inter = int((am & bm).sum())
        union = int((am | bm).sum())
        iou = inter / union if union else 0.0
        fa = a.astype(np.float32)
        fb = b.astype(np.float32)
        va, vb = fa - fa.mean(), fb - fb.mean()
        d = float(np.sqrt((va * va).sum() * (vb * vb).sum()))
        corr = float((va * vb).sum() / d) if d > 0 else 0.0
        return 0.7 * iou + 0.3 * max(0.0, corr)

    def best_numeral(self, blob: np.ndarray) -> Tuple[int, float]:
        """数字块 -> (1..9, 字形匹配分)。0 表示无匹配。

        数字用容错匹配（膨胀 IoU）以吸收笔画粗细/字体差异；
        字牌/萬 仍用严格匹配（_match）以保留 1s 鸟 vs 發 等复杂形状的鉴别力。
        """
        a = self._norm(blob)
        best_d, best = 0, 0.0
        for d, tpls in self.nums.items():
            for t in tpls:
                s = self._match_tolerant(a, t)
                if s > best:
                    best_d, best = d, s
        return best_d, best

    def best_honor(self, blob: np.ndarray, letters) -> Tuple[str, float]:
        """字牌块 + 候选 ASCII 键 -> (键, 匹配分)。空串表示无匹配。"""
        a = self._norm(blob)
        best_l, best = "", 0.0
        for letter in letters:
            for t in self.hons.get(letter, []):
                s = self._match(a, t)
                if s > best:
                    best_l, best = letter, s
        return best_l, best

    def wan_score(self, blob: np.ndarray) -> float:
        # 萬字也用容错匹配：和数字一样存在字体差异，膨胀 1px 后 IoU 更稳。
        # 萬检测已经过了 largest_frac 门限（真萬主体 ≥70% 万区墨迹），
        # 这里的 wan_s 只参与得分，不参与真假萬判定，无误判风险。
        a = self._norm(blob)
        if not self.wans:
            return 0.0
        return max(self._match_tolerant(a, t) for t in self.wans)


class StructuralDetector(Detector):
    """结构识别器：排列掩码评分 + 字形匹配，接口与 TemplateDetector 一致。"""

    def __init__(self, targets=None) -> None:
        super().__init__(targets or {})
        self._glyphs = _GlyphBank(_GLYPH_DIR)
        self._mask_cache: Dict[Tuple[str, int, int], np.ndarray] = {}
        # 诊断信息（与 TemplateDetector 字段名保持一致）
        self.last_top_score: float = 0.0
        self.last_screen: Tuple[int, int] = (0, 0)
        # 每张检出牌的置信度，与 result 一一对应
        self.last_conf: List[float] = []

    # -------------------------------------------------------------- 缓存

    def _mask(self, label: str, fw: int, fh: int) -> np.ndarray:
        key = (label, fw, fh)
        m = self._mask_cache.get(key)
        if m is None:
            if len(self._mask_cache) > 600:
                self._mask_cache.clear()
            if label in PIN_PATTERNS:
                m = _pattern_mask("pin", PIN_PATTERNS[label], fw, fh)
            elif label in SOU_PATTERNS:
                m = _pattern_mask("sou", SOU_PATTERNS[label], fw, fh)
            else:
                m = np.zeros((fh, fw), np.uint8)
            self._mask_cache[key] = m
        return m

    # -------------------------------------------------------------- 基础件

    @staticmethod
    def _ink_mask(face: np.ndarray) -> np.ndarray:
        """相对墨迹掩码：V/Otsu 与 S/Otsu 的并集。"""
        hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
        v, s = hsv[:, :, 2], hsv[:, :, 1]
        _, bv = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark = (bv == 0).astype(np.uint8)
        _, bs = cv2.threshold(s, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        colorful = (bs > 0).astype(np.uint8)
        m = cv2.bitwise_or(dark, colorful) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        return m

    @staticmethod
    def _fill_holes(m: np.ndarray) -> np.ndarray:
        h, w = m.shape[:2]
        ff = m.copy()
        mask2 = np.zeros((h + 2, w + 2), np.uint8)
        seed = None
        for x in range(0, w, max(1, w // 16)):
            if m[0, x] == 0:
                seed = (x, 0)
                break
        if seed is None:
            for y in range(0, h, max(1, h // 16)):
                if m[y, 0] == 0:
                    seed = (0, y)
                    break
        if seed is None:
            return m
        cv2.floodFill(ff, mask2, seed, 255)
        out = m.copy()
        out[ff == 0] = 255
        return out

    @staticmethod
    def _blobs(m: np.ndarray, min_area_frac: float, face_area: int):
        """外轮廓连通域（RETR_CCOMP），返回 [(area_px, x, y, w, h, holes)]。"""
        cnts, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        if hier is None:
            return out
        hier = hier[0]
        for i, c in enumerate(cnts):
            if hier[i][3] != -1:
                continue
            x, y, w, h = cv2.boundingRect(c)
            area = int((m[y:y + h, x:x + w] > 0).sum())
            if area < min_area_frac * face_area:
                continue
            holes = 0
            for j in range(len(cnts)):
                if hier[j][3] == i:
                    holes += 1
            out.append((area, x, y, w, h, holes))
        return out

    @staticmethod
    def _dominant_color(face: np.ndarray, x, y, w, h) -> str:
        patch = face[max(0, y):y + h, max(0, x):x + w]
        if patch.size == 0:
            return "D"
        b, g, r = (patch[:, :, 0].astype(int),
                   patch[:, :, 1].astype(int),
                   patch[:, :, 2].astype(int))
        red = int(((r > 110) & (r - g > 35) & (r - b > 35)).sum())
        green = int(((g > 90) & (g - r > 20) & (g - b > 15)).sum())
        blue = int(((b > 100) & (b - r > 20) & (b - g > 12)).sum())
        mx = max(red, green, blue)
        if mx < 0.10 * (patch.shape[0] * patch.shape[1]):
            return "D"
        return "R" if mx == red else ("G" if mx == green else "B")

    # -------------------------------------------------------------- 分类

    def _circularity(self, m: np.ndarray, x: int, y: int, w: int, h: int) -> float:
        """blob 的真圆度 = 4π·area / perimeter²。圆≈1，鸟/复杂字≈0.3–0.6。"""
        sub = (m[y:y + h, x:x + w] > 0).astype(np.uint8)
        if sub.sum() < 5:
            return 0.0
        cnts, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return 0.0
        c = max(cnts, key=cv2.contourArea)
        a = float(cv2.contourArea(c))
        p = float(cv2.arcLength(c, True))
        return 4.0 * np.pi * a / (p * p) if p > 1 else 0.0

    def _shape_of(self, m: np.ndarray, blob) -> Tuple[str, float, float]:
        """blob -> (shape, circularity, bbox_fill)。shape ∈ {circle, stick, irregular}。

        圆：高圆度 + 接近正方形 bbox；棒：细长 bbox + 高 bbox_fill；
        其余（鸟、萬 字、数字笔画）一律 irregular。
        """
        area, x, y, w, h, _ = blob
        if w < 3 or h < 3:
            return "irregular", 0.0, 0.0
        aspect = w / float(h)
        bbox_fill = area / float(max(1, w * h))
        circ = self._circularity(m, x, y, w, h)
        if circ > 0.72 and 0.70 <= aspect <= 1.45:
            return "circle", circ, bbox_fill
        if (aspect > 1.6 or aspect < 0.62) and bbox_fill > 0.55:
            return "stick", circ, bbox_fill
        return "irregular", circ, bbox_fill

    def _classify_face(self, face: np.ndarray) -> Tuple[Optional[str], float]:
        """单张牌面 -> (标签, 置信度)。

        策略：不再依赖固定位置 IoU（对实际牌面偏移/字体差异极脆弱，
        所有 IoU 都掉到 0.2–0.4，正确与错误标签分不开）。改为：
          a) 万牌优先（字形 + 萬 块联合判断；largest_frac 防误判）——
             必须在元素检测之前，否则"三"的横杠会被数成 3 根条 → 错成 3s。
          b) 元素计数 + 形状：N 个圆 → Np；N 个棒 → Ns。
          c) 1 个圆 → 1p；1 个不规则（鸟） → 1s。
          d) 字牌 glyph + 颜色佐证。
        """
        fh, fw = face.shape[:2]
        if fw < 24 or fh < 24:
            return None, 0.0
        m = self._ink_mask(face)
        total = int((m > 0).sum())
        face_area = fw * fh
        ink_frac = total / float(face_area)

        if ink_frac < INK_MIN_FRAC:
            return "5z", 0.9  # 白板：几乎无墨

        # a) 万牌优先：避免"三"被数成 3 根条
        numeral, man_score = self._try_man(face, m)
        if numeral is not None and man_score > 0.48:
            return numeral, man_score

        # b) 元素检测 → 筒 / 条
        # 注意：相邻条牌粘在一起会被 Otsu 合成一个 blob（横向一排 N 条变 1 个），
        # 单独靠腐蚀会把筒的圆环也切断。改用"行展开"：长条形 blob 按列方向宽度
        # 反算包含几根条，追加到 n_stick。
        blobs = self._blobs(m, 0.025, face_area)
        if not blobs:
            return None, 0.0
        elements = [(self._shape_of(m, b)[0], b) for b in blobs]
        n_circle = sum(1 for s, _ in elements if s == "circle")
        n_stick = sum(1 for s, _ in elements if s == "stick")
        n_total = len(elements)

        # 行展开 + 列展开：相邻条牌可能按行(横长)或列(纵长)粘连。
        # 横长：aspect>2.4 且矮  → bw / 单条宽
        # 纵长：aspect<0.30 且窄（单根条 aspect≈0.37+，不会误中） → bh / (fh/3)
        stick_w_est = max(6, int(0.16 * fw))
        for shape, blob in elements:
            if shape == "circle":
                continue
            _, bx, by, bw, bh = blob[0], blob[1], blob[2], blob[3], blob[4]
            aspect = bw / max(1, bh)
            if aspect > 2.4 and bh < 0.22 * fh:
                extra = max(0, round(bw / float(stick_w_est)) - 1)
                n_stick += extra
            elif aspect < 0.30 and bw < 0.22 * fw:
                n_in_col = max(1, round(bh * 3.0 / fh))
                n_stick += n_in_col - 1

        # 同心圆兜底：1p 在本游戏里画成"大圆环 + 内圆"两个连通块，
        # 元素检测会数成 2 圆。若两圆中心近似重合 → 1p。
        if n_circle == 2 and n_stick == 0:
            cblobs = [b for s, b in elements if s == "circle"]
            (a1, x1, y1, w1, h1, _), (a2, x2, y2, w2, h2, _) = cblobs
            cx1, cy1 = x1 + w1 / 2.0, y1 + h1 / 2.0
            cx2, cy2 = x2 + w2 / 2.0, y2 + h2 / 2.0
            d = ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5
            r_avg = (max(w1, h1) + max(w2, h2)) / 4.0
            if d < 0.35 * r_avg:
                return "1p", 0.65

        if n_total > 1:
            if n_circle >= 2 and n_circle > n_stick and n_circle <= 9:
                return f"{n_circle}p", float(0.55 + 0.05 * n_circle)
            if n_stick >= 2 and n_stick > n_circle and n_stick <= 9:
                return f"{n_stick}s", float(0.55 + 0.05 * n_stick)

        # c) 字牌（单字 glyph 匹配 + 颜色佐证）
        if n_total <= 2:
            honor, hon_score = self._try_honor(face, m, ink_frac, total)
            if honor is not None and hon_score > 0.45:
                return honor, hon_score

        # d) 单元素兜底：1 个圆 → 1p；1 个不规则 → 1s
        if n_total == 1:
            shape, _ = elements[0]
            if shape == "circle":
                return "1p", 0.60
            return "1s", 0.55

        return None, 0.0

    def _try_man(self, face, m) -> Tuple[Optional[str], float]:
        """万牌：上部数字 + 下部「萬」块。

        设计：
          - 先在 [0.40*fh, 0.85*fh] 区间找"数字-萬"之间最长空白行作为分界，
            比固定 WAN_REGION 更稳健：萬 的顶部「艹」冠会侵入 0.55~0.60 区，
            固定切分会把冠切错，空白行检测自然避开冠部。
          - 萬 检测用「底块紧致 bbox + 宽高 + 密度」三重门限，并先剔除贴边的
            牌框竖条，避免边框把 bbox 拉宽、密度拉低而误拒 9m。
          - 数字提取用垂直闭运算连断笔，再剔除全高的边框竖条，取剩余 blob 的
            总 bbox 作为数字。横杠计数（一/二/三）只在"横杠占数字墨迹 ≥80%"
            时强制采用，避免把「七」误判成一。
        """
        fh, fw = m.shape[:2]
        total = int((m > 0).sum())
        if total == 0:
            return None, 0.0
        # 1) 找数字与萬之间的空白行作为分界。
        # 关键：搜索区间限定在 [0.45*fh, 0.72*fh]（数字-萬过渡带）。
        # 九的笔画内部也有 1~2 行的空白（横与钩之间），位于 0.10~0.40*fh，
        # 用宽区间会误中这些笔内空白、把 gap_y 拉得太低、num_blob 被切掉钩。
        lo, hi = int(fh * 0.45), int(fh * 0.72)
        gap_y = int(fh * WAN_REGION)  # fallback (0.60)
        if hi - lo >= 4:
            row_ink = (m > 0).mean(axis=1)
            best_run, best_start = 0, lo
            i = lo
            while i < hi:
                if row_ink[i] < 0.045:
                    j = i
                    while j < hi and row_ink[j] < 0.045:
                        j += 1
                    run_len = j - i
                    above = row_ink[max(0, i - 18):i]
                    below = row_ink[j:min(fh, j + 22)]
                    has_above = (len(above) > 0 and above.max() > 0.05)
                    has_below = (len(below) > 0 and below.max() > 0.05)
                    if run_len > best_run and has_above and has_below:
                        best_run, best_start = run_len, i
                    i = j
                else:
                    i += 1
            if best_run >= 2:
                gap_y = best_start + best_run // 2
        # 2) 分上下区
        top = m[:gap_y, :]
        wan = m[gap_y:, :]
        if top.shape[0] < 12 or wan.shape[0] < 12:
            return None, 0.0
        # 3) 萬 检测：先剔除贴边全高竖条（牌框伪影），再算 bbox + 宽高 + 密度
        bsh, bsw = wan.shape[:2]
        wan_blobs = self._blobs(wan, 0.008, bsw * bsh)
        if not wan_blobs:
            return None, 0.0
        wan_real = [b for b in wan_blobs
                    if not (b[4] > 0.70 * bsh and b[3] < 0.18 * bsw)]
        if not wan_real:
            return None, 0.0
        bx0 = min(b[1] for b in wan_real)
        by0 = min(b[2] for b in wan_real)
        bx1 = max(b[1] + b[3] for b in wan_real)
        by1 = max(b[2] + b[4] for b in wan_real)
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 0.42 * fw or bh < 0.24 * fh:
            return None, 0.0
        bbox_ink = int((wan[by0:by1, bx0:bx1] > 0).sum())
        bbox_frac = bbox_ink / float(max(1, bw * bh))
        if bbox_frac < 0.28:
            return None, 0.0
        # 关键防误判：真萬是单字，墨迹集中在 1~2 个连通块（主体+冠部）；
        # 筒/条牌的底部若被分到萬区，是 2~N 个独立圆/棒，主体最
        # 大块的占比远低于真萬。用 largest_frac 阈值把筒/条假萬拒掉。
        largest = max(wan_real, key=lambda b: b[0])
        total_wan_ink = sum(b[0] for b in wan_real)
        if largest[0] < 0.70 * total_wan_ink:
            return None, 0.0
        # 颜色防误判：1s 雀鸟是绿色（dominant=G），1p 红中也是 R/G 但 1p 走
        # element 路径不会进 _try_man。真萬是黑色墨（D）。萬区出现大块绿色
        # → 必是 1s 鸟被错切到下方，拒绝当萬。
        lx, ly, lw, lh = largest[1], largest[2], largest[3], largest[4]
        color = self._dominant_color(face, lx, gap_y + ly, lw, lh)
        if color == "G":
            return None, 0.0
        # 萬 glyph 匹配：用最大连通块的紧 bbox，而不是所有 blob 的并集。
        # 并集会被侧边竖条/泄漏的冠部碎片拉大，萬字与模板的 IoU 暴跌到 0.2。
        # 最大块就是真正的「萬」主体，匹配分能从 0.25 拉到 0.6+。
        wan_blob = wan[largest[2]:largest[2] + largest[4],
                       largest[1]:largest[1] + largest[3]]
        wan_s = self._glyphs.wan_score(wan_blob)
        # 4) 数字提取：垂直闭运算 + 剔除全高竖条 + 取上部最大连通块
        th = top.shape[0]
        # 用 (1,2) 而非 (1,3) 做垂直闭运算：(1,3) 会把"三"的 3 条横杠在 work 分辨率下
        # 桥接成 1 个 blob（横杠间距缩到 ~2px），导致 3m 被误判为 3 根条/4s。
        # (1,2) 只桥接 1px 的笔画断裂，不会合并本应分离的横杠。
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 2))
        top_c = cv2.morphologyEx(top, cv2.MORPH_CLOSE, kernel)
        blobs = self._blobs(top_c, 0.008, fw * th)
        if not blobs:
            return None, 0.0
        real = [b for b in blobs
                if not (b[4] > 0.70 * th and b[3] < 0.18 * fw)
                and b[0] > 0.012 * fw * th]
        if not real:
            return None, 0.0
        # 优先取"上部"（远离萬的"艹"冠）的最大连通块作为数字主体。
        # 用绝对阈值 0.54*fh 而不是 0.75*gap_y：冠部始终在 0.55~0.60*fh，
        # 与 gap_y 的具体取值无关，num_blob 永远拿不到冠部碎片。
        # 数字笔画最低到 ~0.50*fh（九的钩/撇），0.54 留出安全余量。
        cutoff = int(0.54 * fh)
        upper = [b for b in real if b[2] + b[4] <= cutoff]
        digit_candidates = upper if upper else real
        digit = max(digit_candidates, key=lambda b: b[0])
        x0, y0 = digit[1], digit[2]
        x1, y1 = digit[1] + digit[3], digit[2] + digit[4]
        num_blob = top_c[y0:y1, x0:x1]
        if num_blob.size == 0:
            return None, 0.0
        d, glyph_s = self._glyphs.best_numeral(num_blob)
        # 5) 横杠修正：一/二/三 仅在"横杠占数字墨迹 ≥80%"时强制采用
        bars = [b for b in real if b[3] >= 2.2 * b[4] and b[4] <= 0.20 * fh]
        if 1 <= len(bars) <= 3:
            bar_area = sum(b[0] for b in bars)
            non_bar_area = sum(b[0] for b in real if b not in bars)
            tot_area = bar_area + non_bar_area
            if tot_area > 0 and non_bar_area < 0.20 * tot_area:
                d = len(bars)
                glyph_s = max(glyph_s, 0.55)
        if d < 1 or d > 9:
            return None, 0.0
        score = 0.50 * glyph_s + 0.30 * wan_s + 0.20 * min(1.0, bw / (0.6 * fw))
        return f"{d}m", score

    def _try_honor(self, face, m, ink_frac, total) -> Tuple[Optional[str], float]:
        """字牌：单一大元素 + 字形匹配（颜色只做佐证，不做唯一依据）。"""
        fh, fw = m.shape[:2]
        if total == 0:
            return None, 0.0
        blobs = self._blobs(m, 0.10, fw * fh)
        if not blobs:
            return None, 0.0
        area, x, y, w, h, holes = max(blobs, key=lambda t: t[0])
        # 单一元素占绝对主导才可能是字牌
        if area < 0.75 * total:
            return None, 0.0
        # 框式白板：bbox 四条边大部分都有墨（矩形边框，圆环做不到）
        if w > 0.55 * fw and h > 0.55 * fh and self._is_rect_frame(m, x, y, w, h):
            return "5z", 0.8

        blob = m[y:y + h, x:x + w]
        letter, gs = self._glyphs.best_honor(blob, list(_GLYPH_TO_LABEL))
        color = self._dominant_color(face, x, y, w, h)
        # 颜色佐证：红中/绿發加分，矛盾则减分
        if (letter == "C" and color == "R") or (letter == "F" and color == "G"):
            gs = max(gs, 0.72)
        elif (letter == "C" and color == "G") or (letter == "F" and color == "R"):
            gs *= 0.75
        if letter and gs >= 0.50:
            return _GLYPH_TO_LABEL[letter], 0.30 + 0.55 * gs
        return None, 0.0

    @staticmethod
    def _is_rect_frame(m: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
        """矩形边框检测：bbox 四条边的墨迹覆盖率都高。"""
        if w < 8 or h < 8:
            return False
        sub = (m[y:y + h, x:x + w] > 0).astype(np.uint8)
        top = float(sub[0].mean())
        bot = float(sub[-1].mean())
        left = float(sub[:, 0].mean())
        right = float(sub[:, -1].mean())
        return min(top, bot, left, right) > 0.72

    # -------------------------------------------------------------- 定位

    def _find_bands(self, gray: np.ndarray, tile_h: int,
                    max_bands: int = 6) -> List[Tuple[int, int]]:
        """Sobel 行能量找牌行。"""
        gh, gw = gray.shape[:2]
        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        row_energy = gx.sum(axis=1)
        k = np.ones(max(3, tile_h), np.float32) / float(max(3, tile_h))
        smooth = np.convolve(row_energy, k, mode="same")
        order = np.argsort(-smooth)
        bands = []
        pad = int(tile_h * 0.75)
        for y in order:
            if smooth[y] < 0.25 * float(smooth.max()):
                break
            y1, y2 = max(0, int(y) - pad), min(gh, int(y) + pad)
            if y2 - y1 < 10:
                continue
            if any(not (y2 < b[0] or y1 > b[1]) for b in bands):
                continue
            bands.append((y1, y2))
            if len(bands) >= max_bands:
                break
        return bands

    def _segment_tiles(self, band_img: np.ndarray, tile_h_hint: int = 0) -> List[Rect]:
        """牌行 -> 等宽 N 张牌。

        纵向：用横向 Sobel 边缘（dI/dy）找"牌框"上下两条强横边，从而拿到完整牌面高度。
        不能用"墨迹行"——字符只在中下部，墨迹行起点比牌面顶低约 14px，会把整张脸
        下移、切掉 3m「三」的顶横杠 -> 3m 误读成 1m。

        横向：先定位牌行左右边界（贴边近纯色 UI 面板整列墨迹>0.85，真牌不会；无面板时
        退回含墨列），等宽切成 N（N 贴近 13/14），再把每张牌之间的边界吸附到局部
        "含墨最低列"（牌缝），消除降采样导致的整行右移/扇形发散（否则 5p 端牌被右移
        半张牌误读成 4p/6p）。
        """
        bh, bw = band_img.shape[:2]
        if bh < 20 or bw < 60:
            return []
        gray = cv2.cvtColor(band_img, cv2.COLOR_BGR2GRAY) if band_img.ndim == 3 else band_img
        bh_band, _ = gray.shape[:2]
        # 纵向：face 取整条 band 在"真实牌高"范围内的部分。band 由 _find_bands 给出
        # （峰值 ±0.75 牌高），高度 ≈ 真实牌高。但 _detect_once 又向上多扩了 0.10 牌高
        # （把牌顶补回来），使 band_img 比真实牌高大约 12%。若 face 跟着 band_img 一起取
        # 到 bh_band，会比真牌高出 ~20px（原图）：底端多吞一段桌面背景，稀释墨迹密度、
        # 改变 _try_man 的 bbox_frac 阈值判断（3m 险些从 0.28 掉到 0.276 失败），并让
        # 条牌的连杆检测把一根条判成多根（n_stick 被 row-expansion 推过 9）。
        # 因此 face Y 上限用真实牌高（tile_h_hint），不再跟着扩张的 band 走。
        y1, y2 = 0, (tile_h_hint if tile_h_hint > 0 else bh_band)
        sub = gray[y1:y2, :]
        sh, sw = sub.shape
        if sh < 16 or sw < 60:
            return []
        # 横向：列墨迹剖面
        colprof_ink = (sub < 180).mean(axis=0)
        W = len(colprof_ink)
        # 左右边界：用"整列墨迹 > 0.85"识别近纯色 UI 面板（真牌整列只有 0.2~0.7，
        # 永远不会到 0.85）。牌行跨度 = 左面板右沿 → 右面板左沿。无贴边面板时
        # 退回含墨列收窄。这个方法对 band 纵向范围不敏感（面板始终近实心），比
        # 按"含墨<某阈值"取边界更稳（后者会因 band 变高把中段真牌列误判成实心）。
        solid = colprof_ink > 0.85
        runs = []
        i = 0
        while i < W:
            if solid[i]:
                j = i
                while j < W and solid[j]:
                    j += 1
                runs.append((i, j - 1))
                i = j
            else:
                i += 1
        xl, xr = 0, W
        for (s, e) in runs:
            if s <= int(0.06 * W):          # 贴左边缘 -> 左面板
                xl = max(xl, e + 1)
            if e >= int(0.94 * W):          # 贴右边缘 -> 右面板
                xr = min(xr, s)
        if xl == 0 and xr == W:
            # 无贴边面板：用含墨列收窄（弃掉纯背景边）
            xs = np.where(colprof_ink > 0.05)[0]
            if len(xs) >= 40:
                xl, xr = int(xs.min()), int(xs.max()) + 1
        if xr - xl < 40:
            return []
        # 估算张数：牌宽 ≈ 牌高 × 0.82。
        # 注意：sh 这里用的是 band_img 的高度，而 band_img 在 _detect_once 里已经
        # 做过 0.10/0.02 的上下扩张（把牌顶补回来），会比真实牌高略大 ~12%，
        # 使 span/tile_w_est 偏小、N 被低估成 11 而不是 13 —— 11 个过宽的格子
        # 会跨到相邻真牌上，导致中段若干张分类成 None（全程只剩 7 张）。
        # 因此张数估计必须用"未扩张"的真实牌高（调用方传入的 tile_h_hint），
        # 只让 face 提取用扩张后的 band_img（保住牌顶）。
        sh_hint = tile_h_hint if tile_h_hint > 0 else sh
        tile_w_est = sh_hint * 0.82
        # 降采样把面板/牌行边缘糊几像素，使检测到的牌行边界相对真边界内缩约 0.05~0.1 牌宽；
        # 只补极少余量（0.03）把端牌最外缘留白补回，避免像旧版 0.14 那样把整行拉宽、
        # 扇形发散（5p 端牌被右移半张牌误读成 4p）。
        pad = int(0.03 * tile_w_est)
        xl = max(0, xl - pad)
        xr = min(W, xr + pad)
        # 关键：左 UI 面板是不透明的，会盖住最左一张牌的左缘 ~24 orig（面板右沿
        # 在 orig x=184，但 GT 测得最左牌左缘 x=160，中间 24 orig 被面板遮住）。
        # 若仅用面板右沿作 xl，等分出的 13 张牌会整体右移，最右几张牌的左缘
        # 被截掉一个圆 → 5p 误判成 4p、3p 误判成 2p。被遮的 24 orig 无法从画面恢复
        # （面板是纯色 UI，下面看不到牌），故用一个与游戏 UI 布局对应的常量
        # LEFT_OVERHANG 把网格向左推回。RIGHT_OVERHANG 同理处理右面板与最右牌的间隙。
        xl = max(0, xl - LEFT_OVERHANG)
        xr = min(W, xr + RIGHT_OVERHANG)
        span = xr - xl
        n_raw = span / tile_w_est if tile_w_est > 0 else 13.0
        N = int(round(n_raw))
        if abs(N - 13) <= 1:
            N = 13
        elif abs(N - 14) <= 1:
            N = 14
        else:
            N = max(3, N)
        tw = span / float(N)
        tiles: List[Rect] = []
        for i in range(N):
            tx1 = int(round(xl + i * tw))
            tx2 = int(round(xl + (i + 1) * tw)) if i < N - 1 else xr
            if tx2 - tx1 < 8:
                continue
            tiles.append((tx1, int(y1), tx2, int(y2)))
        return tiles

    # -------------------------------------------------------------- 主入口

    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        img = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        ih, iw = img.shape[:2]
        self.last_screen = (iw, ih)

        # dets 始终是 (rect, label, conf) 三元组，保证 confs 不会因排序与 result 错位
        dets = self._detect_once(img)
        if len(dets) < 4:
            for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                try:
                    rot_img = cv2.rotate(img, rot)
                except cv2.error:
                    continue
                d2 = self._detect_once(rot_img)
                if len(d2) > len(dets):
                    dets = [(self._unrotate_rect(r, rot, iw, ih), l, c)
                            for (r, l, c) in d2]
                    break

        # 先按 x 排序，再拆出 confs / result，保证两者下标严格一一对应。
        # 旋转分支会重排顺序：若先取 confs 再排序，会把 conf 与牌位错位配对。
        dets.sort(key=lambda d: d[0][0])
        confs = [c for (_, _, c) in dets]
        self.last_conf = confs
        self.last_top_score = float(max(confs)) if confs else 0.0
        result: DetectionResult = [(r, l) for (r, l, _c) in dets]

        def display():
            canvas = img.copy()
            for i, (rect, label) in enumerate(result):
                x, y, w, h = rect
                c = confs[i] if i < len(confs) else 0.5
                color = (0, 200, 0) if c >= MIN_CONF else (0, 140, 255)
                cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)
                if label:
                    cv2.putText(canvas, f"{label}", (int(x + 0.1 * w), int(y + 0.3 * h)),
                                fontFace=1, fontScale=1.0, color=color, thickness=2)
            return canvas

        return Stage(result=result, image=image, display_callback=display)

    def _detect_once(self, img: np.ndarray) -> List[Tuple[Rect, Optional[str], float]]:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        longest = float(max(h, w))
        inv = 1.0
        if longest > MAX_WORKING_EDGE:
            inv = longest / MAX_WORKING_EDGE
            work = cv2.resize(gray, (max(1, int(w / inv)), max(1, int(h / inv))),
                              interpolation=cv2.INTER_AREA)
            work_color = cv2.resize(img, (max(1, int(w / inv)), max(1, int(h / inv))),
                                    interpolation=cv2.INTER_AREA)
        else:
            work, work_color = gray, img

        wh, ww = work.shape[:2]
        tile_h = max(10, int(TILE_H_RATIO * min(wh, ww)))
        bands = self._find_bands(work, tile_h)

        dets: List[Tuple[Rect, Optional[str], float]] = []
        for (y1, y2) in bands:
            # 向上多扩、向下几乎不扩：_find_bands 给的 band 已≈牌高，仅顶沿比真牌顶低
            # 约 0.1 牌高，向上补一点把牌顶补回；向下扩太多会吞进牌行下方的桌面。
            bh_band = y2 - y1
            pad_up = int(0.10 * bh_band)
            pad_dn = int(0.02 * bh_band)
            y1e, y2e = max(0, y1 - pad_up), min(wh, y2 + pad_dn)
            band = work_color[y1e:y2e, :]
            # 过滤纯色带（牌墙背面、纯色背景等）：用"均匀度"而非亮度。
            # 手牌行有大量字符/圆点，灰度呈"亮牌面 + 暗墨迹"的双峰分布，
            # 落在中位数窄窗(±12)内的像素占比很低（实测 ≈0.03）。
            # 纯色带（绿色牌墙等）灰度近似单峰，该占比很高（实测 ≈0.20~0.28）。
            # 故用"中位数 ±12 窗内占比"衡量均匀度，过高 => 纯色带 => 排除。
            # 注意：不能用"灰度<150 占比"——绿色牌墙转灰度≈80 会被整片算成"墨"
            # （占比 0.92），反而比手牌（0.46）更像"有内容"而误留。
            band_gray = work[y1:y2, :].astype(np.float32)
            med = float(np.median(band_gray))
            unif = float(((band_gray >= med - 12) & (band_gray <= med + 12)).mean())
            if unif > 0.12:
                continue
            tiles = self._segment_tiles(band, y2 - y1)
            if len(tiles) < 3:
                continue
            for (tx1, ty1, tx2, ty2) in tiles:
                # 在 work 坐标上做 FACE_INSET，得到牌的"内部矩形"在 work 坐标的范围
                ix_w = int((tx2 - tx1) * FACE_INSET)
                iy_w = int((ty2 - ty1) * FACE_INSET)
                fx1w, fy1w = tx1 + ix_w, y1e + ty1 + iy_w
                fx2w, fy2w = tx2 - ix_w, y1e + ty2 - iy_w
                # 映射回原图坐标：分类必须在全分辨率上跑，_classify_face 内的相对阈值
                # (0.42*fw, 0.24*fh, largest_frac 等) 是按全分辨率牌面 (~168x140) 调出来的，
                # 套到 60x70 的 work 分辨率牌面会全部不达标 (9m/3m 在 work 上都跪)。
                # 带"Sobel + 行列能量"走 work（快），分类单独走原图（准）。
                fx1 = int(round(fx1w * inv))
                fy1 = int(round(fy1w * inv))
                fw_f = max(1, int(round((fx2w - fx1w) * inv)))
                fh_f = max(1, int(round((fy2w - fy1w) * inv)))
                fx2, fy2 = fx1 + fw_f, fy1 + fh_f
                if fw_f < 20 or fh_f < 20:
                    continue
                face = img[fy1:fy2, fx1:fx2]
                label, conf = self._classify_face(face)
                if label is None:
                    continue
                aspect = fw_f / float(fh_f)
                if aspect < MIN_TILE_ASPECT or aspect > MAX_TILE_ASPECT:
                    conf *= 0.55  # 牌形不对，降置信
                dets.append(((fx1, fy1, fw_f, fh_f), label, conf))

        # 手牌行 = 张数最接近 13/14 的行（其余行丢弃，见 _pick_rows 注释）
        dets = self._pick_rows(dets)
        return dets

    @staticmethod
    def _pick_rows(dets):
        """手牌行 = 张数最接近 13/14 的那一行（其余行丢弃）。

        平局时优先选择"标签种类最多"的行——牌墙背面是纯色 5z（1 种），
        手牌/牌河是多种花色数字（5~10+ 种），用多样性即可稳健区分。
        再以"行越靠下"和"张数越多"作为后续 tiebreak。
        引擎记牌需求由 trainer 内部的"上一手 diff"覆盖，这里不重复。
        """
        if len(dets) < 3:
            return dets
        groups: Dict[int, list] = {}
        for d in dets:
            row_h = d[0][3]
            groups.setdefault(d[0][1] // max(8, row_h), []).append(d)
        groups_list = list(groups.values())
        if not groups_list:
            return []
        costs = [min(abs(len(g) - 13), abs(len(g) - 14), abs(len(g) - 8))
                 for g in groups_list]
        min_cost = min(costs)
        candidates = [g for g, c in zip(groups_list, costs) if c == min_cost]
        if len(candidates) == 1:
            best = candidates[0]
        else:
            def _score(g):
                unique = len({d[1] for d in g if d[1] is not None})
                yc = sum(d[0][1] for d in g) / float(len(g))
                return (unique, yc, len(g))
            best = max(candidates, key=_score)
        out = list(best)
        out.sort(key=lambda d: (d[0][1], d[0][0]))
        return out

    @staticmethod
    def _unrotate_rect(rect: Rect, rot: int, ow: int, oh: int) -> Rect:
        x, y, w, h = rect
        if rot == cv2.ROTATE_90_CLOCKWISE:
            # 原图 (ow,oh) 顺时针转 90 -> 新图 (oh,ow)
            ny, nx = x, oh - (y + h)
            return (nx, ny, h, w)
        ny, nx = ow - (x + w), y
        return (nx, ny, h, w)
