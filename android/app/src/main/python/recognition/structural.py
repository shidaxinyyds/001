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
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage, Rect

_HERE = os.path.dirname(os.path.abspath(__file__))
_GLYPH_DIR = os.path.join(_HERE, "images", "glyphs")
_STYLE_DIR = os.path.join(_HERE, "images", "styles")

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
_HONOR_GLYPH = {"1z": "E", "2z": "S", "3z": "W", "4z": "N", "5z": "H", "6z": "F", "7z": "C"}
_GLYPH_TO_LABEL = {v: k for k, v in _HONOR_GLYPH.items()}

# ------------------------------------------------------- 相对阈值（常量）
FACE_INSET = 0.06            # 牌面内缩比例（去掉牌边框）

# 牌面分类「局部重试」参数。
# RETRY_CONF：低于此置信度就认为切图可能没对准，值得花代价重试。
#   实测原图上 3m/9m/1s 的 conf 是 0.55~0.59（临界），筒子是 0.90+，
#   取 0.62 刚好只圈住这几张有风险的牌（13 张里 <=3 张），不影响性能。
# RETRY_OFFSETS：(dx, shrink) 候选。dx 为整体平移比例（相对牌宽），
#   shrink 为左右各内缩比例。覆盖「相位偏了」和「切太宽含了邻牌边框」两种情况。
RETRY_CONF = 0.62
RETRY_OFFSETS = (
    (-0.135, 0.0), (0.135, 0.0),
    (-0.08, 0.0), (0.08, 0.0),
    (0.0, 0.04), (-0.055, 0.04), (0.055, 0.04),
)
INK_MIN_FRAC = 0.035         # 低于此墨迹占比 -> 白板
TILE_H_RATIO = 0.105         # 牌高初值（占屏幕短边）——只用于圈横条
MIN_TILE_ASPECT = 0.52       # 牌宽/牌高 合理下限
MAX_TILE_ASPECT = 0.95       # 牌宽/牌高 合理上限（超界当误检丢弃）
MAX_WORKING_EDGE = 1100      # 长边超过则降采样（性能）
OUT_PENALTY = 0.6            # 排列掩码外墨迹的罚系数
MIN_CONF = 0.30              # 低于此置信度的牌标记为低置信
# 牌级缓存容差：归一化 16x16 缩略图的平均绝对差。
# 实测（screenshot.jpg 13 张 GT，7 种扰动）：
#   同一张牌在 JPEG95/75/55、亮度±30%、噪声σ=12、高斯模糊下的最大差异 = 0.13
#   不同牌之间的最小差异 = 0.33（如 4p vs 5p、7s vs 9s）
# 取 0.20 落在两者之间并偏向"误命中代价更高"的一侧：
#   命中侧余量 0.20/0.13 = 1.5x，误判侧余量 0.33/0.20 = 1.65x
# 另配 1 秒 TTL（见 _FACE_CACHE_TTL）兜底：任何误命中最多存活 1 秒。
FACE_CACHE_TOL = 0.20
# 缓存强制刷新周期（秒）。到期整体清空重新分类，防止误命中长期粘住。
_FACE_CACHE_TTL = 1.0
# 万牌硬门槛：萬字形匹配分低于此值就判定"不是万牌"。
# 没有这个门槛时，1p 的大圆下缘会被当成"萬块"、上缘被数字字形匹配上，
# 从而把 1p 误判成 4m/1m（多风格测试中稳定复现）。
MIN_WAN_SCORE = 0.30
# 字牌字形匹配阈值。
MIN_HONOR_SCORE = 0.45
# 排列队形分门槛：筒/条数对了还要「排得像」才认。
# 这是把字牌（東/發 笔画峰值）挡在筒/条之外的主要闸门。
# 数值由三风格基准标定：真筒/条普遍 >=0.7，字牌伪 motif 普遍 <0.6。
MIN_ARR_SCORE = 0.62
# 同风格模板相似度门槛：达到即采信模板结果。
# 分值标定：同风格 ≈0.85~0.97，跨风格汉字 ≈0.5~0.7（见 _StyleBank 注释）。
# 取 0.74 可确保只有"确实是同一套牌面美术"时才覆盖几何判定结果，
# 未注册的风格仍走几何不变量路径（仍可识别筒/条）。
MIN_STYLE_SCORE = 0.74
# 边际放行门：同风格牌面 JPEG50 重压缩/裁切偏移后，绝对分可跌到 0.58~0.74
# （真实截图 1s=0.607、9s=0.738 实测），但「同标签 vs 次高分标签」的边际
# 仍 >=0.08（错标签模板形状差异大）；未注册风格的跨牌误配分数低且边际小。
# 绝对分 + 边际双门限：注册风格召回 80/80，合成 17 风格泄漏仅 2 例（黑体/雅黑
# 的 2z 误配 1p，由 1p 结构守卫拦下）。
STYLE_MARGIN_LO = 0.58
# GAP 由数据标定（localtest/diag_margin_sweep.py，14 个扰动场景 x 13 张手牌
# = 143 样本，其中 92 条走 margin 通道）：
#   真对样本(90 条) margin ∈ [0.0742, 0.2514]
#   真错样本(2 条，6s 被误配 9s) margin = 0.0305 / 0.0383
# 两者之间有一段 [0.0383, 0.0742] 的完全空隙 —— 门限放这段正中最稳。
# 旧值 0.08 压在真对样本堆里：9s 全族 margin ∈ [0.0742, 0.0889]（其他牌都
# >=0.10），恰好骑在 0.08 上，任何重采样抖动都会把 9s 掀翻 —— 掉出风格通道
# 后几何数不准 9 根条（粘成 3 列，arr=0.15），最终经 _try_honor 错成 1z(東)。
# 1.5x 缩放下实测复现。取 0.056（空隙中点）后：真对 90/90 放行、真错 0/2 放行。
STYLE_MARGIN_GAP = 0.056
NUMERAL_REGION = 0.58        # 万牌数字区（占牌高）
# 萬字块起始（占牌高）。设 0.60 而非 0.55，给数字"三"的最下横杠留出 0.05 余量，
# 避免 0.55~0.58 区间的数字笔画被圈进 bottom 把 bbox 拉大、密度拉低、萬检测失败。
WAN_REGION = 0.60            # 萬字块起始（占牌高）

# ---- 牌面尺度归一化 ----
# 风格模板走 96px letterbox，本身尺度无关；但几何路径（_pin_centers /
# _stick_centers / _count_indep_blobs / 形态学核）用的是**绝对像素**阈值，
# 因此只在「牌面约 148x168」的原生尺度上标定过。
# 实测（localtest/diag_scale_norm.py，13 张手牌逐牌比对）：
#   0.5x  牌面 84px  原始 9/13   -> 归一到 168 后 13/13
#   0.75x 牌面126px  原始13/13   -> 归一后 13/13
#   1.0x  牌面168px  原始13/13   -> 归一是恒等变换
#   1.5x  牌面251px  原始12/13   -> 归一后 13/13
# 0.5x 的失败机理：6~9 根条在小尺度上相邻间隙消失（白点退化成 1~2px 灰度），
# 二值化把它们粘成 1~2 个连续块，被 _is_two_stage 判成「单一大字」走字牌分支。
# 上采样到基准尺度后间隙恢复。
# 落在 [LO, HI] 内的牌面视为已在基准尺度，跳过重采样（原图零开销、零风险）。
FACE_CANON_LONG = 168        # 基准长边（= 基准截图的牌面高度）
FACE_CANON_LO = 150
FACE_CANON_HI = 190
FACE_CANON_MIN = 40          # 长边小于此值的牌面信息量不足，放大只会放大噪声

# ---- 低光/色温预处理：CLAHE (Contrast Limited Adaptive Histogram Equalization) ----
# 不开：夜间/暖光牌面对比度普遍偏低（绿牌面转灰度 ~80、紫光下偏红），Otsu 在
# 单峰分布上把 JPEG 噪声硬劈成两半、产生大面积伪墨迹，白板被认成字牌；
# 开：在 LAB 空间的 L 通道做 CLAHE，把对比度拉到接近正常光照的水平，且不影响
# 色相（H 通道原样保留 → 红中仍是红、绿發仍是绿）。clip_limit=2.0 是行业经验值，
# 太高会引入块状伪影，太低起不到增强作用。
CLAHE_CLIP_LIMIT = 2.0
CLAHE_GRID_SIZE = (8, 8)
# 启动期稳定性：启动时帧差历史为空 → _MotionGuard 会全部按"非 spike"放行。
# 解决方案：识别器提供"是否处于冷启动期"信号给引擎，让引擎对冷启动的 N 帧
# 走保守策略（只采纳 ≥2 帧一致的 label，且每张牌都要求至少 0.55 置信度），
# 不让动画过渡帧污染冷启动投票窗口。
WARMUP_FRAMES = 4
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
                s = self._match_tolerant(a, t)
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


class _StyleBank:
    """按「美术风格」组织的整牌模板库：images/styles/<style>/<label>.png。

    度量（2026-09 重构，勿回退到软余弦）：
      旧版「非等比拉伸 40x56 + 距离变换软场余弦」实测**没有区分度**——
      模板库内部不同牌之间的相似度中位数 0.883、95.4% 的牌对超过 0.74
      门槛。任何未注册风格的输入都会以 0.87+ 高分命中某个随机标签，
      是跨风格误判的最大单一来源（合成 3m -> 3z@0.92 可复现）。
      现改为「等比 letterbox + IoU 主导的容错匹配」：同风格同牌 ≈0.85+，
      跨风格 / 跨牌 <0.55，0.74 门槛重新具备「同一套牌面美术」的语义。

    模板由 localtest/build_style_bank.py 生成：裁墨迹外接框 + 等比缩放
    （最长边 <=96，保留纵横比），加载时 letterbox 到 96x96。
    """

    SIZE = 96

    def __init__(self, path: str):
        self.tpls: List[Tuple[str, np.ndarray]] = []   # (label, letterbox mask)
        if not os.path.isdir(path):
            return
        for style in sorted(os.listdir(path)):
            sdir = os.path.join(path, style)
            if not os.path.isdir(sdir):
                continue
            for name in sorted(os.listdir(sdir)):
                if not name.endswith(".png"):
                    continue
                img = cv2.imread(os.path.join(sdir, name), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                binm = ((img > 100).astype(np.uint8)) * 255
                if not binm.any():
                    continue
                self.tpls.append((name[:-4], _GlyphBank._norm(binm, self.SIZE)))

    def match(self, m: np.ndarray) -> Tuple[Optional[str], float, float]:
        """返回 (最佳标签, 相似度 0~1, 与次高不同标签的边际)。

        无模板或非匹配时返回 (None, 0, 0)。调用方用「绝对分或边际」双门限
        判定是否采信（见 STYLE_MARGIN_LO 注释）。
        """
        if not self.tpls:
            return None, 0.0, 0.0
        q = _GlyphBank._norm(m, self.SIZE)
        if not q.any():
            return None, 0.0, 0.0
        best_l, best, second = None, 0.0, 0.0
        for label, t in self.tpls:
            s = _GlyphBank._match_tolerant(q, t)
            if s > best:
                if label != best_l:
                    second = best
                best_l, best = label, s
            elif label != best_l and s > second:
                second = s
        return best_l, best, best - second


class StructuralDetector(Detector):
    """结构识别器：排列掩码评分 + 字形匹配，接口与 TemplateDetector 一致。"""

    def __init__(self, targets=None) -> None:
        super().__init__(targets or {})
        self._glyphs = _GlyphBank(_GLYPH_DIR)
        self._styles = _StyleBank(_STYLE_DIR)
        # 已注册风格里的字牌模板同时并入字形库：真实游戏牌面的字
        # （東南西北中發）与 Windows 字体字形差异大，多一份真实变体
        # 能显著拉高未注册风格的字牌匹配分（与 best_honor 输入形式
        # 一致：主块紧 bbox 的 letterbox）。
        for _lab, _tpl in self._styles.tpls:
            if len(_lab) == 2 and _lab.endswith("z") and _lab in _HONOR_GLYPH:
                _n72 = _GlyphBank._norm(_tpl, 72)
                if _n72.any():
                    self._glyphs.hons.setdefault(_HONOR_GLYPH[_lab], []).append(_n72)
        self._mask_cache: Dict[Tuple[str, int, int], np.ndarray] = {}
        # 诊断信息（与 TemplateDetector 字段名保持一致）
        self.last_top_score: float = 0.0
        # 牌级分类缓存：真机连续帧里同一张牌被反复分类（13ms/张，13 张 = 173ms/帧，
        # 占单帧 90%）。命中时直接复用上帧结果，开销从 13ms 降到 ~0.15ms。
        # key = 量化后的 rect (x//4, y//4, w//4, h//4)，value = (16x16 降采样, label, conf)
        self._face_cache = {}
        # 缓存条目上限，超过就整体清空（防止长时间运行内存增长）
        self._FACE_CACHE_MAX = 512
        # 上次清空缓存的时间戳（配合 _FACE_CACHE_TTL 强制刷新）
        self._face_cache_ts = 0.0
        # 「本 TTL 周期内已做过局部重试」的格子 key 集合。切图相位误差是静态的，
        # 同一位置重复搜索必得同样结果，所以每个位置每周期只搜一次。
        # 注意：成功与失败**都要**标记 —— 只标记失败会让"救到 0.55（仍低于
        # RETRY_CONF）"的牌每帧重搜 7 次（~91ms），稳态直接崩。
        self._retry_done = set()
        # 带级相位提示：key = y // max(8, h)（行号），value = 该行最佳 (fdx, fsh)。
        # 同一行的牌共享同一切牌网格，相位误差全行一致，学一次全行复用。
        self._phase_hint = {}
        # 每帧局部重试预算（硬上限）。带门限已经把重试限制在手牌行，
        # 这里是**防御性**兜底：任何意外（畸形画面、极端光照导致切牌爆炸）
        # 都不能让单帧退化成秒级。一次重试最多 7 次分类 x 13ms ≈ 91ms，
        # 预算 6 个格子 => 重试部分最坏 ~550ms，仍在可交互范围。
        self._RETRY_BUDGET = 6
        self._retry_budget = 6
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
        """相对墨迹掩码：V/Otsu 与 S/Otsu 的并集。

        对比度守卫：白板/空白牌面是单一均匀色块，Otsu 在没有双峰时会把
        JPEG 噪声硬劈成两半，产生大面积伪墨迹（实测可达 100%），
        导致白板既认不出、又会被当成字牌。因此先检查明暗动态范围，
        过小则直接判定"无墨"。
        """
        hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
        v, s = hsv[:, :, 2], hsv[:, :, 1]
        # p1/p99 而非 p5/p95：细条牌墨迹可低于牌面 5%，p5 会把纯牌面色当成
        # "最暗 5%"，hi-lo 无差 -> 空掩码 -> 误判白板（合成细条 3s 复现）。
        lo = float(np.percentile(v, 1))
        hi = float(np.percentile(v, 99))
        los = float(np.percentile(s, 1))
        his = float(np.percentile(s, 99))
        if max(hi - lo, his - los) < 12.0:
            return np.zeros(v.shape, np.uint8)
        _, bv = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark = (bv == 0).astype(np.uint8)
        _, bs = cv2.threshold(s, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        colorful = (bs > 0).astype(np.uint8)
        m = cv2.bitwise_or(dark, colorful) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        return m

    @staticmethod
    def _apply_clahe(gray: np.ndarray) -> np.ndarray:
        """对灰度图做 CLAHE 增强：解决弱光/暖光下对比度过低导致 Otsu 失效的问题。

        何时触发：灰度的 1%→99% 动态范围 < 60（实测白天 80~150、夜间 30~70）。
        强光下直方图已接近全范围，再做 CLAHE 反而引入块状伪影、降低字形匹配 IoU。

        clip_limit=2.0 / grid=(8,8) 是行业经验值；更高 clip 出现过曝斑块，更低
        失去增强效果。返回与原图同 shape 同 dtype 的 uint8 图，调用方可无缝替换。
        """
        lo = float(np.percentile(gray, 1))
        hi = float(np.percentile(gray, 99))
        if hi - lo >= 60.0:
            return gray
        try:
            clahe = cv2.createCLAHE(
                clipLimit=CLAHE_CLIP_LIMIT,
                tileGridSize=CLAHE_GRID_SIZE,
            )
            return clahe.apply(gray)
        except Exception:
            return gray

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
    def _min_area(min_area_frac: float, face_area: int) -> float:
        """统一把「面积门槛」换算成绝对像素数。

        _blobs 的历史调用里既有传比例（0.01）也有传绝对像素（0.01*face_area）的
        写法，二者混用会产生 `area < frac*face_area` 被放大成平方量级的静默失效
        ——连通域全部被过滤掉，却没有任何报错，排查极难。
        这里做容错：参数 > 1 视为绝对像素，<= 1 视为占牌面比例。
        """
        try:
            v = float(min_area_frac)
        except (TypeError, ValueError):
            return 0.0
        if v <= 1.0:
            return v * float(face_area)
        return v

    @staticmethod
    def _blobs(m: np.ndarray, min_area_frac: float, face_area: int):
        """外轮廓连通域（RETR_CCOMP），返回 [(area_px, x, y, w, h, holes)]。

        min_area_frac：面积门槛。<=1 表示占 face_area 的比例；>1 表示绝对像素数
        （见 _min_area 的说明，防止调用方单位用错导致静默全过滤）。
        """
        min_area = StructuralDetector._min_area(min_area_frac, face_area)
        # 防御：OpenCV 的 C 层在收到非 8-bit 单通道 / 空 / 非连续数组时会直接
        # SIGSEGV（不可被 Python try/except 捕获，进程表现为闪退）。进入
        # findContours 前用纯 numpy 拦截坏数据，从根上消除这类崩溃。
        if (not isinstance(m, np.ndarray) or m.ndim != 2
                or m.dtype != np.uint8 or m.size == 0):
            return []
        m = np.ascontiguousarray(m)
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
            if area < min_area:
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
        sub = np.ascontiguousarray(sub)
        cnts, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return 0.0
        c = max(cnts, key=cv2.contourArea)
        a = float(cv2.contourArea(c))
        p = float(cv2.arcLength(c, True))
        return 4.0 * np.pi * a / (p * p) if p > 1 else 0.0

    def _wan_is_multi_motif(self, sub: np.ndarray) -> bool:
        """判断一块墨迹是否实为"多个圆点/多根竖棒"（数牌下半部），
        而非单一萬字。用于防止筒/条牌被误判成万牌。

        只对"明确的圆"或"明确的竖向贯穿墨柱"计数——萬字是笔画复杂的汉字，
        既不会是圆、也不会是成排竖向贯穿的墨柱，故不会被误拒。
        这是跨美术风格稳定的：万牌下半部永远是一个「萬」字块，而筒/条牌下半部
        永远是若干圆点/竖棒。
        """
        fa = sub.shape[0] * sub.shape[1]
        if int((sub > 0).sum()) < 10:
            return False
        blobs = self._blobs(sub, 0.01, fa)
        rc = 0   # 圆点数量
        sc = 0   # 竖棒数量
        for b in blobs:
            area, x, y, w, h, _ = b
            if area < 0.008 * fa:
                continue
            circ = self._circularity(sub, x, y, w, h)
            if circ > 0.55:
                rc += 1
                continue
            # 非圆 blob：检查是否为成排竖棒（条牌）
            subb = (sub[y:y + h, x:x + w] > 0).astype(np.uint8)
            if int(subb.sum()) < 8:
                continue
            e = cv2.erode(subb, np.ones((1, 3), np.uint8))
            e = cv2.erode(e, np.ones((3, 1), np.uint8))
            cnts, _ = cv2.findContours(e, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                xc, yc, wc, hc = cv2.boundingRect(c)
                if wc < 3 or hc < 3:
                    continue
                a = float(cv2.contourArea(c))
                if a < 0.01 * fa:
                    continue
                aspect = wc / max(1, hc)
                if aspect < 0.62 and hc / max(1, wc) > 1.4 and a / float(max(1, wc * hc)) > 0.4:
                    sc += 1
        return rc >= 2 or sc >= 2

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

    # ---- 几何不变量计数（跨美术风格稳定）------------------------------------

    def _peaks_in_blob(self, m: np.ndarray, blob) -> int:
        """用距离变换的局部极大值（带最小间距抑制）估计一个粘连块里含几个圆点。

        比"对阈值化距离变换做连通域"更稳：粘连圆之间距离变换脊线可能仍高于阈值
        而被并成一个连通域，峰值法按"圆心局部极大 + 最小间距"逐一计数，6p/7p/8p/9p
        的粘连圆都能数清。
        """
        area, x, y, w, h, _ = blob
        if w < 3 or h < 3:
            return 1
        sub = (m[y:y + h, x:x + w] > 0).astype(np.uint8)
        if int(sub.sum()) < 5:
            return 0
        dt = cv2.distanceTransform((1 - sub) * 255, cv2.DIST_L2, 3)
        mx = float(dt.max())
        if mx < 2.0:
            return 1
        dila = cv2.dilate(dt, np.ones((3, 3), np.uint8))
        peaks = (dt == dila) & (dt > 0.35 * mx)
        coords = np.column_stack(np.where(peaks))
        mind = 0.55 * mx
        keep = []
        for yy, xx in sorted(coords, key=lambda c: -dt[c[0], c[1]]):
            if all(((yy - ky) ** 2 + (xx - kx) ** 2) ** 0.5 > mind for ky, kx in keep):
                keep.append((yy, xx))
        return max(1, len(keep))

    @staticmethod
    def _dt_peaks(dt: np.ndarray, min_frac: float = 0.35,
                  min_sep_frac: float = 0.50) -> List[Tuple[int, int]]:
        """距离变换的局部极大值**坐标**（带最小间距抑制）。

        dt 为「墨迹」的距离变换时（每个墨迹像素到最近背景的距离），
        峰值即圆盘圆心；粘连圆盘之间会出现脊线低谷，峰值法能逐个点清。
        """
        mx = float(dt.max())
        if mx < 1.2:
            return []
        dila = cv2.dilate(dt, np.ones((3, 3), np.uint8))
        peaks = (dt == dila) & (dt > max(1.0, min_frac * mx))
        coords = np.column_stack(np.where(peaks))
        mind = max(2.0, min_sep_frac * mx)
        keep: List[Tuple[int, int]] = []
        for yy, xx in sorted(coords, key=lambda c: -dt[c[0], c[1]]):
            if all(((yy - ky) ** 2 + (xx - kx) ** 2) ** 0.5 > mind
                   for ky, kx in keep):
                keep.append((int(yy), int(xx)))
        return keep

    def _pin_centers(self, m: np.ndarray, fw: int, fh: int) -> List[Tuple[float, float, float]]:
        """返回检测到的圆心 [(cx, cy, r)]，已做填洞/去噪/DT 峰值/半径一致性闸门。"""
        face_area = fw * fh
        if int((m > 0).sum()) < 0.01 * face_area:
            return []
        mf = self._fill_holes(m)
        mf = cv2.morphologyEx(mf, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        cand: List[Tuple[float, float, float, int]] = []
        min_span = 0.07 * min(fw, fh)
        for bi, b in enumerate(self._blobs(mf, 0.004, face_area)):
            area, x, y, w, h, _ = b
            if max(w, h) < min_span:
                continue
            # DT 必须带真实背景上下文：紧贴 bbox 的距离变换把「到 bbox 角的
            # 距离」当成圆半径——7x36 细竹棒被量出 r=17.5 的假圆心，条牌
            # 整体被数成筒牌（5s->5p 复现）。裁边圆(8p 顶行贴 y=0)同理，
            # 无背景参照会在裁边产生 DT 平台假峰。窗口外圈再补一圈纯
            # 背景零值：贴 face 边界裁切的 blob 也有背景参照（8p 顶行
            # 被裁 3px 时，裁边行的 DT 从 ~10 掉到 ~1，假峰消失）。
            pad = max(3, int(0.12 * max(w, h)))
            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1, y1 = min(fw, x + w + pad), min(fh, y + h + pad)
            ink = (mf[y0:y1, x0:x1] > 0).astype(np.uint8)
            canvas = np.zeros((ink.shape[0] + 2, ink.shape[1] + 2), np.uint8)
            canvas[1:-1, 1:-1] = ink
            dt = cv2.distanceTransform(canvas, cv2.DIST_L2, 3)
            dmax = float(dt.max())
            if dmax < 1.2:
                cand.append((x + w / 2.0, y + h / 2.0, max(2.0, min(w, h) / 2.0), bi))
                continue
            for py, px in self._dt_peaks(dt):
                cand.append((x0 + px - 0.5, y0 + py - 0.5,
                             max(2.0, float(dt[py, px])), bi))
        if not cand:
            return []
        cand.sort(key=lambda c: -c[2])
        kept: List[Tuple[float, float, float, int]] = []
        for cx, cy, r, bi in cand:
            ok = True
            for kx, ky, kr, kb in kept:
                # 同一 blob 内的峰必须真正分开（>=1.05*max(r)）：相切圆对的
                # 峰距 ~2r 仍保留；"部分重叠/裁切产生的近距离伪峰"（大圆点
                # 9p 粘连对 14px vs r=18、8p 裁边平台峰 16px vs r=16）被
                # 合并。跨 blob 的独立圆维持 0.65 宽松阈值。
                sep = 1.05 * max(r, kr) if bi == kb else 0.65 * max(r, kr)
                if ((cx - kx) ** 2 + (cy - ky) ** 2) ** 0.5 <= sep:
                    ok = False
                    break
            if ok:
                kept.append((cx, cy, r, bi))
        if len(kept) >= 2:
            # 两个"圆"来自同一连通块：真实筒牌只有 3+ 密排圆才会粘连，
            # 2 个圆永不粘连——同块双峰必是雀鸟(1s 身+头)或笔画结构。
            if len(kept) == 2 and kept[0][3] == kept[1][3]:
                return []
            radii = np.array([k[2] for k in kept], dtype=np.float32)
            med = float(np.median(radii))
            if med > 0:
                # n==2 收紧到 [0.72,1.40]：真 2p 两圆同径；雀鸟身/头半径
                # 比普遍 >=1.5（合成 33:18）。n>=3 维持宽容带（密排粘连
                # 圆的 DT 峰半径会略有出入）。
                lo, hi = (0.72, 1.40) if len(kept) == 2 else (0.60, 1.60)
                good = int(np.sum((radii >= lo * med) & (radii <= hi * med)))
                if good < int(0.70 * len(kept)):
                    return []
        return [(cx, cy, r) for cx, cy, r, _bi in kept]

    def _count_pindots(self, m: np.ndarray, fw: int, fh: int) -> int:
        """筒牌：统计圆点（填充圆盘 / 圆环）个数。返回 1-9；0 表示不是筒。"""
        kept = self._pin_centers(m, fw, fh)
        if not kept:
            return 0
        count = len(kept)
        # 验证：圆心附近必须真是圆/环（按各自半径取样），
        # 避免把萬字/字牌的笔画端点当圆心。
        if 1 <= count <= 9 and self._pin_peak_sanity(m, fw, fh, kept):
            return count
        return 0

    def _pin_peak_sanity(self, m: np.ndarray, fw: int, fh: int,
                         centers) -> bool:
        """按每个圆心自身的半径取样，验证局部确有「实心盘」或「圆环」。

        早期版本用固定取样半径（0.12*min(fw,fh)），在大牌面上取样窗远小于真实圆，
        把圆环内部空腔当成“无墨”而全部拒绝（duma520 9p 实测 0/9）。
        """
        ok = 0
        for cx, cy, r in centers:
            rad = max(3, int(round(r)))
            x0, y0 = max(0, int(cx) - rad - 2), max(0, int(cy) - rad - 2)
            x1, y1 = min(fw, int(cx) + rad + 3), min(fh, int(cy) + rad + 3)
            if x1 <= x0 or y1 <= y0:
                continue
            patch = m[y0:y1, x0:x1]
            ph, pw = patch.shape
            cyy, cxx = int(cy) - y0, int(cx) - x0
            Y, X = np.ogrid[:ph, :pw]
            dist = np.sqrt((X - cxx) ** 2 + (Y - cyy) ** 2)
            inner = dist < 0.55 * rad
            rim = (dist >= 0.62 * rad) & (dist < 1.35 * rad)
            if inner.sum() == 0 or rim.sum() == 0:
                continue
            i_ink = float((patch[inner] > 0).mean())
            r_ink = float((patch[rim] > 0).mean())
            # 实心盘：内部有墨；圆环：外缘有墨。二者满足其一即为圆。
            if i_ink > 0.50 or r_ink > 0.30:
                ok += 1
        return ok >= max(1, (len(centers) + 1) // 2)

    # ---------------------------------------------------- 排列验证（关键闸门）
    # 筒/条的"有几个 motif"由 DT 峰值数给出，但**数对不等于队形对**：
    # 字牌「東」的笔画端点也能数出 9 个峰值（实测被判成 9p）。
    # 因此必须再验证这些 motif 是否真的排成该数字应有的队形
    # （6p=2列×3行、8p=2列×4行、9s=3×3…… 这是全行业统一的排列不变量）。

    @staticmethod
    def _norm_arr(p: np.ndarray) -> np.ndarray:
        """归一化到零均值/单位尺度，只保留"队形形状"，消除风格带来的位置与缩放差异。"""
        c = p - p.mean(axis=0)
        s = float(np.sqrt((c ** 2).sum(axis=1).mean()))
        if s < 1e-6:
            return c
        return c / s

    @staticmethod
    def _chamfer(a: np.ndarray, b: np.ndarray) -> float:
        """对称 Chamfer 距离：双向最近邻平均距离，对少量错位/抖动不敏感。"""
        def one_way(x, y):
            ds = [float(np.min(np.sum((y - xi) ** 2, axis=1))) for xi in x]
            return float(np.mean(ds)) if ds else 0.0
        return max(one_way(a, b), one_way(b, a))

    def _arrangement_score(self, centers, patterns: Dict[str, list]) -> Tuple[Optional[str], float]:
        """把 motif 中心与同数量的排列模板比对，返回 (最佳标签, 队形分 0~1)。

        只与"motif 数量相同"的模板比对：对筒/条而言数量本身就唯一决定了点数，
        这里比的是"队形对不对"，用来拒绝字牌/萬字的伪 motif。
        """
        if not centers:
            return None, 0.0
        pts = np.array([[c[0], c[1]] for c in centers], dtype=np.float32)
        n = len(pts)
        best_label, best_score = None, 0.0
        for label, pat in patterns.items():
            if len(pat) != n:
                continue
            a = self._norm_arr(pts)
            b = self._norm_arr(np.array(pat, dtype=np.float32))
            d = self._chamfer(a, b)
            score = 1.0 / (1.0 + 8.0 * d)
            if score > best_score:
                best_label, best_score = label, score
        return best_label, best_score

    def _stick_centers(self, m: np.ndarray, fw: int, fh: int) -> List[Tuple[float, float]]:
        """返回检测到的竹棒中心 [(cx, cy)]（face 绝对坐标）。

        面积门槛的历史坑：0.02*face_area 与 0.012*face_area 的轮廓面积
        下限都不是尺度不变量——真实细棒只占牌面 1~2%（7x36 棒在 132x160
        face 上仅 248px），5s/7s/9s 的棒曾被整体滤掉、条牌大面积失识别。
        噪声过滤改用形态学维度判据：棒必须足够高(>=0.045*fh)且细长
        (双维都小的碎片与圆团天然过不了关)。
        """
        face_area = fw * fh
        centers: List[Tuple[float, float]] = []
        for b in self._blobs(m, 0.004, face_area):
            area, x, y, w, h, _ = b
            circ = self._circularity(m, x, y, w, h)
            if circ > 0.60:
                continue  # 圆形 -> 筒，不是条
            sub = (m[y:y + h, x:x + w] > 0).astype(np.uint8)
            if int(sub.sum()) < 8:
                continue
            # 横、纵各腐蚀一次，断开左右/上下相连的棒
            e = cv2.erode(sub, np.ones((1, 3), np.uint8))
            e = cv2.erode(e, np.ones((3, 1), np.uint8))
            cnts, _ = cv2.findContours(e, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                xc, yc, wc, hc = cv2.boundingRect(c)
                if wc < 2 or hc < 4:
                    continue
                if hc < 0.045 * fh:
                    continue          # 碎片/笔画端点，不是完整棒
                aspect = wc / max(1, hc)
                hratio = hc / max(1, wc)
                if aspect < 0.62 and hratio > 1.4 and \
                        cv2.contourArea(c) / float(max(1, wc * hc)) > 0.4:
                    centers.append((x + xc + wc / 2.0, y + yc + hc / 2.0))
        return centers

    def _count_sticks(self, m: np.ndarray, fw: int, fh: int) -> int:
        """条牌：统计竹棒个数。"""
        total = len(self._stick_centers(m, fw, fh))
        return total if 2 <= total <= 9 else 0

    def _count_indep_blobs(self, m: np.ndarray, fw: int, fh: int, want_circle: bool) -> int:
        """统计彼此独立的顶层元素块数（用于校验几何计数是否可信）。

        want_circle=True 数筒牌圆点（circ>0.60）；False 数条牌竹棒（circ<=0.60）。
        真实筒/条，每个元素是一个独立顶层 blob；若牌面因粘连把 N 个元素合并成
        少数大块（如 duma 8s 把 8 根条粘成 2 大块、erode 后伪 4 中心），此处块数
        会远小于 N，从而暴露伪计数、避免几何覆盖正确的风格模板。
        """
        face_area = fw * fh
        n = 0
        for b in self._blobs(m, 0.004, face_area):
            area, x, y, w, h, _ = b
            circ = self._circularity(m, x, y, w, h)
            if want_circle and circ <= 0.60:
                continue
            if (not want_circle) and circ > 0.60:
                continue
            sub = (m[y:y + h, x:x + w] > 0).astype(np.uint8)
            if int(sub.sum()) < 8:
                continue
            n += 1
        return n

    def _stage_is_glyph(self, m: np.ndarray, y0: int, y1: int,
                        fw: int, min_w_frac: float) -> bool:
        """一段墨迹是否为「单个较宽的字形」（万牌上数字 / 下萬块）。

        数牌(2s/4s/9p/8p…)的行间空白也会切出"两段"，但每段是 N 个
        小元素（棒/圆）横排——最大块窄（圆 ~0.17fw、棒 ~0.05fw）或
        细长。数字字形与萬块都是 >=0.22fw 宽的紧凑块。
        """
        sub = m[y0:y1, :]
        sh_ = sub.shape[0]
        if sh_ < 4:
            return False
        blobs = self._blobs(sub, 0.004, fw * sh_)
        if not blobs:
            return False
        largest = max(blobs, key=lambda b: b[0])
        _a, x, y, w, h = largest[0], largest[1], largest[2], largest[3], largest[4]
        if w < min_w_frac * fw:
            return False
        if w / float(max(1, h)) < 0.14:          # 细长竖棒（条牌行）
            return False
        if self._circularity(sub, x, y, w, h) > 0.62 and w < 0.45 * fw:
            return False                          # 圆点（筒牌行）
        return True

    def _is_two_stage(self, m: np.ndarray) -> bool:
        """万牌的上下两段结构：中部空白带，且上下两段各为「单个大字形」。

        数字(上)与萬(下)之间必然有空白行。仅"有空白带"不够——
        2 行条牌/筒牌(2s/4s/2p)行间同样有空白，但每段是多个小元素；
        万牌的上段=数字、下段=萬，各是单个宽块。用 _stage_is_glyph
        区分。字牌单字笔画连续，不触发本判定。
        """
        fh, fw = m.shape[:2]
        row = (m > 0).mean(axis=1)
        total = float(row.sum())
        if total <= 0:
            return False
        lo, hi = int(0.30 * fh), int(0.80 * fh)
        i = lo
        while i < hi:
            if row[i] < 0.03:
                j = i
                while j < hi and row[j] < 0.03:
                    j += 1
                run = j - i
                if run >= max(3, int(0.05 * fh)):
                    upper = float(row[:i].sum())
                    lower = float(row[j:].sum())
                    if upper >= 0.15 * total and lower >= 0.30 * total:
                        if self._stage_is_glyph(m, j, fh, fw, 0.35) and \
                                self._stage_is_glyph(m, 0, i, fw, 0.22):
                            return True
                i = j
            else:
                i += 1
        return False

    @staticmethod
    def _canon_scale(face: np.ndarray) -> np.ndarray:
        """把牌面等比重采样到基准长边 FACE_CANON_LONG。

        为什么必要：风格模板匹配走 96px letterbox，天生尺度无关；但几何路径
        （筒/条计数、独立块数、形态学核）全部是**绝对像素**阈值，只在基准
        牌面尺度（≈148x168）标定过。低分辨率下条与条的间隙消失导致粘连，
        高分辨率下笔画变粗同样改变计数 —— 两端都会错标。

        落在 [FACE_CANON_LO, FACE_CANON_HI] 内视为已在基准尺度，原样返回
        （基准截图走这条路，零拷贝、零风险）。
        """
        h, w = face.shape[:2]
        le = w if w > h else h
        if FACE_CANON_LO <= le <= FACE_CANON_HI or le < FACE_CANON_MIN:
            return face
        sc = FACE_CANON_LONG / float(le)
        # 缩小用 INTER_AREA（抗锯齿、不产生伪边），放大用 INTER_CUBIC（保边）
        interp = cv2.INTER_AREA if sc < 1.0 else cv2.INTER_CUBIC
        return cv2.resize(face, (max(1, int(round(w * sc))),
                                 max(1, int(round(h * sc)))),
                          interpolation=interp)

    def _classify_face(self, face: np.ndarray) -> Tuple[Optional[str], float]:
        """单张牌面 -> (标签, 置信度)。

        策略：不再依赖固定位置 IoU（对实际牌面偏移/字体差异极脆弱，
        所有 IoU 都掉到 0.2–0.4，正确与错误标签分不开）。改为：
          a) 万牌优先（字形 + 萬 块联合判断；largest_frac 防误判）——
             必须在元素检测之前，否则"三"的横杠会被数成 3 根条 → 错成 3s。
          b) 筒/条几何计数优先于风格模板：N 个圆 → Np；N 个棒 → Ns，
             并验证排列队形。几何是客观计数，优于风格模板对筒/条的数量误配
             （实测 6s 被风格模板错配成 9s，但几何数棒数得对）。
          c) 风格模板兜底：覆盖万/字牌，以及几何不稳的筒/条
             （某些样式下 4p/7s/9s 排列分偏低、1s 雀鸟非棒状）。
          d) 字牌 glyph + 颜色佐证；1 个圆 → 1p；1 个不规则（鸟） → 1s。
        """
        fh, fw = face.shape[:2]
        if fw < 24 or fh < 24:
            return None, 0.0
        # 防御：透明背景（RGBA）合成到白底，避免黑底被当成墨迹（跨风格/测试图安全）。
        if face.ndim == 3 and face.shape[2] == 4:
            a = face[:, :, 3].astype(np.float32) / 255.0
            white = np.ones((fh, fw, 3), np.uint8) * 255
            f = face[:, :, :3].astype(np.float32)
            face = (f * a[:, :, None] + white * (1.0 - a)[:, :, None]).astype(np.uint8)
        # 尺度归一化：把牌面重采样到基准长边，让下游**绝对像素**阈值的几何
        # 计数（条/筒根数、独立块数、形态学核）在任意屏幕分辨率下行为一致。
        # 原生尺度（[150,190]）直接跳过 —— 基准图零开销、零风险。
        face = self._canon_scale(face)
        fh, fw = face.shape[:2]
        m = self._ink_mask(face)
        total = int((m > 0).sum())
        face_area = fw * fh
        ink_frac = total / float(face_area)

        if ink_frac < INK_MIN_FRAC:
            return "5z", 0.9  # 白板：几乎无墨

        # ---- 1) 几何筒/条候选（客观计数，用于与风格模板交叉仲裁）----
        pin_centers = self._pin_centers(m, fw, fh)
        pin_n = len(pin_centers)
        pin_arr = 0.0
        pin_pass = False
        if 1 <= pin_n <= 9 and self._pin_peak_sanity(m, fw, fh, pin_centers):
            _, pin_arr = self._arrangement_score(pin_centers, PIN_PATTERNS)
            pin_pass = pin_arr >= MIN_ARR_SCORE
        sou_centers = self._stick_centers(m, fw, fh)
        sou_n = len(sou_centers)
        sou_arr = 0.0
        sou_pass = False
        if 2 <= sou_n <= 9:
            _, sou_arr = self._arrangement_score(sou_centers, SOU_PATTERNS)
            sou_pass = sou_arr >= MIN_ARR_SCORE
        # 独立块数：几何中心应来自彼此独立的顶层 blob。粘连误计数（如 duma 8s
        # 把 8 根条粘成 2 大块、伪 4 中心）的块数会远小于数量，不可信。
        pin_indep = self._count_indep_blobs(m, fw, fh, True)
        sou_indep = self._count_indep_blobs(m, fw, fh, False)
        geo_label = None
        geo_arr = 0.0
        geo_indep = 0
        if pin_pass:
            geo_label, geo_arr, geo_indep = f"{pin_n}p", pin_arr, pin_indep
        if sou_pass and sou_arr > geo_arr:
            geo_label, geo_arr, geo_indep = f"{sou_n}s", sou_arr, sou_indep

        # ---- 2) 风格模板优先（已知样式：万/字/筒/条最高精度）----
        #    已知样式的模板精确匹配，优于几何对筒/条的数量误配（如 6s->9s）
        #    和 _try_man 对筒/条/字的万牌误判（如 6p->4m、8s->4m）。
        s_label, s_score, s_margin = self._styles.match(m)
        # 1p 结构守卫：未注册风格的「南」等字形会以 0.59+0.12 的分数/边际
        # 误配 1p 大圆模板（合成黑体/雅黑 2z->1p 复现）。真 1p 的主块必是
        # 高圆度的近方形 blob；字形笔画圆度低，直接拒绝低绝对分的 1p 采信。
        if s_label == "1p" and s_score < MIN_STYLE_SCORE:
            blobs_1p = self._blobs(m, 0.05, face_area)
            ok_1p = False
            if blobs_1p:
                ba, bx, by, bw, bh, _ = max(blobs_1p, key=lambda b: b[0])
                asp = bw / float(max(1, bh))
                ok_1p = (self._circularity(m, bx, by, bw, bh) > 0.55
                         and 0.70 <= asp <= 1.40)
            if not ok_1p:
                s_label = None
        if s_label is not None and (
                s_score >= MIN_STYLE_SCORE
                or (s_score >= STYLE_MARGIN_LO
                    and s_margin >= STYLE_MARGIN_GAP)):
            sk = s_label[-1]
            if sk in ("m", "z"):
                return s_label, float(min(0.98, 0.55 + 0.5 * s_score))
            # 筒/条风格命中与几何数量冲突时，仅当几何排列分极高(>=0.85)且几何
            # 中心来自独立顶层 blob（geo_indep==数量，排除粘连伪计数）才以客观
            # 几何纠正风格错配（如 6s 风格误配 9s）；几何不可信（如 8s 粘连伪 4）
            # 则保留正确风格。
            if (geo_label is not None and int(s_label[0]) != int(geo_label[0])
                    and geo_arr >= 0.85 and geo_indep == int(geo_label[0])):
                return geo_label, float(0.50 + 0.45 * geo_arr)
            return s_label, float(min(0.98, 0.55 + 0.5 * s_score))

        # ---- 3) 结构路由：两段结构 -> 万牌优先；单一大字 -> 字牌 ----
        #    万牌 = 上数字下萬、中部有跨宽空白带；字牌 = 单字笔画连续。
        #    必须先做这个路由：字牌（東南西北中發）的笔画会被几何计数
        #    数成伪圆/伪棒（南->7s、發->8p 实测复现），旧版把几何兜底
        #    排在字牌之前，是字牌大面积误判的根因。
        two_stage = self._is_two_stage(m)
        hon_score = 0.0
        if two_stage:
            numeral, man_score = self._try_man(face, m)
            if numeral is not None and man_score > 0.45:
                return numeral, man_score
        else:
            honor, hon_score = self._try_honor(face, m, ink_frac, total)
            if honor is not None and hon_score > 0.45:
                return honor, hon_score

        # ---- 4) 几何筒/条兜底（带独立块数校验）----
        #    几何计数 + 队形都对还不够：真筒/条的 motif 来自彼此独立的
        #    顶层 blob（粘连时略少），字牌/萬字的伪 motif 全挤在 1~3 个
        #    大块里。块数 < 0.45*N 说明"中心"来自笔画碎片，不可信。
        if geo_label is not None:
            n_geo = int(geo_label[0])
            # 独立块数校验:真筒/条的 motif 来自独立顶层 blob(粘连时略少),
            # 字牌/萬字伪 motif 全挤在少数大块。但"规则网格粘连"
            # (如 9p 三行圆点彼此相切)也只呈 1~3 块,其排列分极高(0.8+),
            # DT 峰值计数本身可信,故放行。
            if n_geo <= 2:
                # 2 元素筒/条永不粘连：独立块数必须恰好等于数量，
                # 否则"同块双峰"(雀鸟身/头、笔画端点)会借高排列分
                # 混进几何分支（1s->2p 复现）。
                if geo_indep >= n_geo:
                    return geo_label, float(0.50 + 0.45 * geo_arr)
            elif geo_indep >= max(2, int(round(0.45 * n_geo))) or geo_arr >= 0.80:
                return geo_label, float(0.50 + 0.45 * geo_arr)

        # ---- 5) 万牌弱兜底（两段检测失败的万牌：字牌路径也没接住）----
        if not two_stage:
            numeral, man_score = self._try_man(face, m)
            if numeral is not None and man_score > 0.52:
                return numeral, man_score

        # ---- 6) 单元素兜底：1 个圆 → 1p；1 个不规则（鸟） → 1s ----
        blobs = self._blobs(m, 0.025, face_area)
        if not blobs:
            return None, 0.0
        elements = [(self._shape_of(m, b)[0], b) for b in blobs]
        n_total = len(elements)
        if n_total == 1:
            shape, b = elements[0]
            if shape == "circle":
                return "1p", 0.60
            if two_stage:
                return None, 0.0
            # 单不规则元素 = 1s（雀鸟）的最可能情况。
            # 雀鸟在全行业几乎都是绿色；绿色单元素直接判 1s，覆盖最常见的
            # 未注册风格 1s（无风格模板可依赖，必须靠几何+颜色兜底）。
            # 非绿色时，仅在"明显不像字牌"（hon_score 很低）才谨慎判 1s，
            # 否则宁可不给答案（符合"精度优先"），避免把未匹配的發/字牌误判成 1s。
            color = self._dominant_color(face, b[1], b[2], b[3], b[4])
            if color == "G":
                return "1s", 0.55
            if hon_score < 0.26:
                return "1s", 0.50
            return None, 0.0

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
        # 宽度门槛 0.34（原 0.42 会把楷体系窄長的「萬」挡掉：合成楷体
        # 0.415*fw 压线被拒，一/二/三全部误判）。筒/条误切进萬区的防护
        # 由后面四重守卫承担：密度 + 最大块占比 + 圆度 + 多母题，
        # 圆点/竖棒的底部块在这里过不了关，无需靠宽度阈值兜底。
        if bw < 0.34 * fw or bh < 0.24 * fh:
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
        # 圆/椭圆守卫：真萬是笔画复杂的汉字，最大连通块不可能是"近圆"。
        # 筒牌（圆）或条牌（椭圆）若被误切到萬区，其最大块呈圆形 → 直接拒掉，
        # 避免把 1p/1s 当成万牌（跨风格稳定复现的误判源）。
        if self._circularity(wan, largest[1], largest[2], largest[3], largest[4]) > 0.60:
            return None, 0.0
        # 多母题守卫：真萬是单一汉字块；若"萬区"实为多个圆点（筒牌下半）或
        # 多根竖棒（条牌下半），则它是数牌被误切到此，拒绝当萬。
        if self._wan_is_multi_motif(wan[by0:by1, bx0:bx1]):
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
        # 优先取"上部"（远离萬的"艹"冠）的笔画作为数字主体。
        # 旧版固定 cutoff=0.54*fh 会误杀「三」的最下横杠（真实位置
        # 0.44~0.58*fh，合成宋体/楷体上 y_end 到 0.58*fh），导致 3m 被判 2m。
        # 冠部出现在 gap_y 之下（属于萬区），数字笔画必然在 gap_y 之上：
        # 以 gap_y 为分界是结构正确的；若 gap 检测把分界拉得过低
        # （冠与数字粘连时 fallback 0.60*fh），再退回"起笔在 0.45*fh 之上"
        # 排除冠部碎片（冠的 y0 >= 0.52*fh）。
        cutoff = gap_y - 1
        upper = [b for b in real if b[2] + b[4] <= cutoff]
        if not upper:
            upper = [b for b in real if b[2] < 0.45 * fh]
        digit_candidates = upper if upper else real
        # 关键修正：数字要用「所有笔画的并集 bbox」，不能只取最大连通块。
        # 「二」「三」是多条独立横杠，取最大块只会拿到一条杠，
        # 字形匹配必然判成「一」——这就是 2m/3m 稳定误判成 1m 的根因。
        ux0 = min(b[1] for b in digit_candidates)
        uy0 = min(b[2] for b in digit_candidates)
        ux1 = max(b[1] + b[3] for b in digit_candidates)
        uy1 = max(b[2] + b[4] for b in digit_candidates)
        # 形状守卫：数字块不能是"圆"（筒牌圆漏到上部时会被当成数字）。
        # 0.80 而非 0.70：粗体「四」是近方的外框结构，圆度实测 0.72
        # （微软雅黑 4m 稳定复现被拒）；真圆（1p 大圆）圆度 0.85+。
        if self._circularity(top_c, ux0, uy0, ux1 - ux0, uy1 - uy0) > 0.80:
            return None, 0.0
        num_blob = top_c[uy0:uy1, ux0:ux1]
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
        # 萬字形硬门槛：万牌必须真的有「萬/万」字。缺这个门槛时，
        # 圆形/其它花色的下半部会被当成萬块，把 1p 误判成 4m。
        if wan_s < MIN_WAN_SCORE:
            return None, 0.0
        score = 0.50 * glyph_s + 0.30 * wan_s + 0.20 * min(1.0, bw / (0.6 * fw))
        return f"{d}m", score

    def _try_honor(self, face, m, ink_frac, total) -> Tuple[Optional[str], float]:
        """字牌：笔画字符（可多连通块）+ 字形匹配（颜色只做佐证）。

        历史坑：旧版取「单一最大连通块」且要求 >=10% 牌面 + >=75% 总墨——
        「北」「白」这类笔画断开的字，最大分量只有 ~2% 牌面 / ~40% 总墨，
        直接查无 blob 而失识别（4z 在 17 个合成风格全灭）。
        改为「主分量并集」：小分量过滤 + 圆团拒收（数牌元素是圆团，
        字牌分量是笔画，跨风格稳定）后再做字形匹配。
        """
        fh, fw = m.shape[:2]
        if total == 0:
            return None, 0.0
        blobs = self._blobs(m, 0.008, fw * fh)
        if not blobs:
            return None, 0.0
        big = [b for b in blobs if b[0] >= 0.05 * total]
        if not big:
            return None, 0.0
        # 圆团拒收：主分量过半是「实心圆/环」-> 这是筒/条数牌，不是字。
        # 字牌笔画分量无论多小都不会同时满足圆度+纵横比+填充率三条件。
        round_n = 0
        for b in big:
            barea, bx, by, bw, bh, _ = b
            aspect = bw / float(max(1, bh))
            if self._circularity(m, bx, by, bw, bh) > 0.55 and \
                    0.75 <= aspect <= 1.35 and \
                    barea / float(max(1, bw * bh)) > 0.55:
                round_n += 1
        if round_n >= max(1, (len(big) + 1) // 2):
            return None, 0.0
        x = min(b[1] for b in big)
        y = min(b[2] for b in big)
        w = max(b[1] + b[3] for b in big) - x
        h = max(b[2] + b[4] for b in big) - y
        area = int((m[y:y + h, x:x + w] > 0).sum())
        # 主分量并集必须占绝对主导（小碎分量 <=25%）才可能是字牌
        if area < 0.75 * total:
            return None, 0.0
        # 多行数牌拒绝：筒/条元素按行排布，行间有整行空白带；字牌笔画
        # 纵向连续（即使笔画断开成多个分量，每个分量都纵贯，任意行都有墨）。
        # 否则 6 棹条 2 行 3 列的并集会被「白 H」字形以 ~0.45 分误中
        # （小牌面 6s -> 5z 复现）。
        row = (m > 0).mean(axis=1)
        tt = float(row.sum())
        if tt > 0:
            blo, bhi = int(0.28 * fh), int(0.78 * fh)
            i = blo
            while i < bhi:
                if row[i] < 0.02:
                    j = i
                    while j < bhi and row[j] < 0.02:
                        j += 1
                    run = j - i
                    if run >= max(3, int(0.06 * fh)):
                        upper = float(row[:i].sum())
                        lower = float(row[j:].sum())
                        if upper >= 0.20 * tt and lower >= 0.20 * tt:
                            return None, 0.0
                    i = j
                else:
                    i += 1
        # 实心椭圆拒绝：1s 雀鸟是实心大椭圆（bbox 填充率高、无内洞），
        # 字牌是笔画镂空结构（填充率低或有笔画围出的内洞）。鸟与「發」
        # 的字形分非零，容错匹配下可能过线，这里按结构直接拒掉，防 1s -> 6z。
        # holes==0 条件：粗体字（微软雅黑「西」fill 0.65/circ 0.39）仍要放行。
        if area / float(max(1, w * h)) > 0.62 and \
                self._circularity(m, x, y, w, h) > 0.35 and \
                sum(b[5] for b in big) == 0:
            return None, 0.0
        # 框式白板：bbox 四条边大部分都有墨（矩形边框，圆环做不到）
        if w > 0.55 * fw and h > 0.55 * fh and self._is_rect_frame(m, x, y, w, h):
            return "5z", 0.8

        blob = m[y:y + h, x:x + w]
        letter, gs = self._glyphs.best_honor(blob, list(_GLYPH_TO_LABEL))
        color = self._dominant_color(face, x, y, w, h)
        # 颜色佐证：红中/绿發加分，矛盾则减分。
        # 發+红只减 0.85（部分牌面發确实用红/黑，字形分 0.59 时不应被
        # 一票否决——宋体 6z 曾以 0.592*0.75=0.444 差 0.006 被拒）；
        # 中+绿维持 0.75（红中是全行业铁律）。
        if (letter == "C" and color == "R") or (letter == "F" and color == "G"):
            gs = max(gs, 0.72)
        elif letter == "C" and color == "G":
            gs *= 0.75
        elif letter == "F" and color == "R":
            gs *= 0.85
        if letter and gs >= MIN_HONOR_SCORE:
            return _GLYPH_TO_LABEL[letter], 0.30 + 0.55 * gs
        # 未达阈值也把最佳字形分带回去：让调用方知道"这块有多像字"，
        # 从而避免把它盲目兜底成 1s（字牌被大量误判成 1s 的根因）。
        return None, gs

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
        """Sobel 行能量找牌行。

        双阈值策略：先用宽松阈值(0.15)收集候选行，再用严格阈值(0.25)确认。
        这样即使某些游戏牌行边缘能量偏低（如绿色牌面与桌面对比度低），
        也不会完全漏检。
        """
        gh, gw = gray.shape[:2]
        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        row_energy = gx.sum(axis=1)
        k = np.ones(max(3, tile_h), np.float32) / float(max(3, tile_h))
        smooth = np.convolve(row_energy, k, mode="same")
        order = np.argsort(-smooth)
        bands = []
        pad = int(tile_h * 0.75)
        max_e = float(smooth.max())
        # 第一遍：宽松阈值 0.15，收集所有可能的牌行
        for y in order:
            if smooth[y] < 0.15 * max_e:
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

    # 手牌合法张数先验：13（待摸）、14（刚摸）；副露一组减 3（顺/刻）或减 4（杠）。
    # 副露场景下，未摸牌可少 1~3 张：~7..10；纯吃碰场景：1~5。
    # 把这些张数作为先验枚举，**禁止**盲目 round(span/period) —— 周期估错 5% 就 13→14。
    _HAND_COUNTS = (14, 13, 11, 10, 8, 7, 5, 4, 2, 1)
    # 切牌 x 范围（牌区相对 active 区偏移）搜索窗（像素，work 分辨率）。
    _SEG_XMIN_RANGE = 14
    # 周期相对自相关粗值的搜索窗（±比例）。
    _SEG_PERIOD_FRAC = 0.06
    # 周期搜索步数（含中点）。
    _SEG_PERIOD_STEPS = 13
    # 最小可接受张数。
    _SEG_MIN_TILES = 3

    def _col_profile(self, gray_band):
        """列剖面信号：每列"亮像素占比"。

        比均值稳健 —— 牌面图案（墨迹）让均值下降，但不会改变"这一列是不是牌面"。
        0..1。0=纯暗缝/空白，1=整列亮。
        """
        hi = float(np.percentile(gray_band, 88))
        sig = (gray_band >= hi * 0.72).mean(axis=0).astype(np.float32)
        # 轻微平滑：去掉笔画造成的细碎抖动，但保留牌缝/牌边框这种"宽信号"
        k = np.ones(3, np.float32) / 3.0
        sig = np.convolve(sig, k, mode="same")
        return sig

    def _autocorr_period(self, sig, min_p, max_p):
        """FFT 加速的归一化自相关，返回 (亚像素周期, 峰强度)。

        FFT 自相关：R = IFFT(|FFT(s)|^2)，O(n log n)，比循环实现快 50x+。
        抛物线插值到亚像素精度。
        """
        n = len(sig)
        if n < 8:
            return None, 0.0
        s = sig - sig.mean()
        denom = float(np.dot(s, s))
        if denom <= 1e-9:
            return None, 0.0
        f = np.fft.rfft(s, n=2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:n]
        if ac[0] <= 0:
            return None, 0.0
        ac = ac / ac[0]
        lo, hi = max(1, int(round(min_p))), min(n - 2, int(round(max_p)))
        if hi <= lo:
            return None, 0.0
        seg = ac[lo:hi + 1]
        idx = int(np.argmax(seg))
        peak = float(seg[idx])
        period = lo + idx
        # 抛物线亚像素插值
        if 0 < idx < len(seg) - 1:
            r0, r1, r2 = float(seg[idx - 1]), peak, float(seg[idx + 1])
            denom_i = (r0 - 2 * r1 + r2)
            if abs(denom_i) > 1e-9:
                delta = 0.5 * (r0 - r2) / denom_i
                if abs(delta) <= 1.0:
                    period = lo + idx + delta
        return float(period), peak

    def _seg_align_score(self, sig, xmin, tile_w, n, period_ref=None):
        """网格对齐得分。

        好网格的特征：
          1. 内部边界（牌缝/共享边框）落在牌"亮边框"上（sig 高）
          2. 每个牌格内部确实有牌面（sig 显著 > 0）
          3. **tile_w 与自相关周期一致**（强约束）：
             旧版只用前两条，导致 tile_w 偏 1% 就让 13 张牌累计偏 8px，
             最后两张牌的 face 切到边沿，分类器判定为 None。

        实测这张图：GT 边界处 sig ≈ 0.94~0.98，牌心 sig ≈ 0.68。
        max 比 mean 更鲁棒：牌心有图案，min 可能跌到 ~0.4，max 仍稳。
        """
        sw = len(sig)
        if tile_w <= 5 or n < 1:
            return -1e9
        if xmin < -2 or xmin + n * tile_w > sw + 2:
            return -1e9
        edge_score = 0.0
        cell_score = 0.0
        n_edge = 0
        n_cell = 0
        # **首尾边界也计分** —— 否则 xmin 可以自由滑动而不影响 score。
        # 首尾边界可能在牌区外，sig 低；这些低分会惩罚 xmin 偏左/偏右。
        for k in range(0, n + 1):
            x = xmin + k * tile_w
            xi = int(round(x))
            if 0 <= xi - 2 and xi + 3 <= sw:
                edge_score += float(sig[xi - 2:xi + 3].max())
                n_edge += 1
        for k in range(n):
            a = int(round(xmin + k * tile_w)) + 3
            b = int(round(xmin + (k + 1) * tile_w)) - 3
            if b > a and 0 <= a and b <= sw:
                cell_score += float(sig[a:b].mean())
                n_cell += 1
        if n_edge == 0 or n_cell == 0:
            return -1e9
        # 周期偏离惩罚：强约束 tile_w ≈ period_ref。
        # 系数 2.0：让 1% 偏差（tile_w 偏 0.6px）就扣 ~0.02 分，相当于
        # n=11/n=13 之间的典型分差 (~0.05)，能稳定把 n=13 选出来。
        period_pen = 0.0
        if period_ref and period_ref > 0:
            period_pen = 2.0 * abs(tile_w - period_ref) / period_ref
        return ((edge_score / n_edge) + 0.5 * (cell_score / n_cell)
                - period_pen)

    @staticmethod
    def _face_thumb(face):
        """牌面缩略图（16x16 灰度，零均值单位方差归一化），用于缓存比对。

        归一化是**必须的**：屏幕亮度/对比度变化（自动亮度、色温、不同机型）
        会让绝对灰度整体线性漂移，不归一化时实测亮度 +10% 就让同一张牌的
        缩略图差异飙到 127，而不同牌之间最小差异只有 11.7 —— 容差窗口
        根本不存在（既大量漏命中，又有误命中风险）。
        归一化消除全局线性漂移后，同一张牌在各种扰动下差异 <2，
        不同牌仍 >10，两者有 5x 以上的安全间隔。
        """
        g = (cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
             if face.ndim == 3 else face)
        small = cv2.resize(g, (16, 16), interpolation=cv2.INTER_AREA)
        f = small.astype(np.float32)
        return (f - f.mean()) / (f.std() + 1e-6)

    def _classify_face_retry(self, img, rect, allow_retry=True):
        """分类一张牌，必要时用「局部重试」救回切图没对准的牌。

        rect 是 (x, y, w, h)（原图坐标）。先按原 rect 分类（走缓存）；
        若 label 为 None 或 conf < RETRY_CONF，就在 RETRY_OFFSETS 给出的
        若干平移/内缩候选上重新切图分类，取 conf 最高者。

        为什么必须这样做：切牌网格是「等宽 + 全局相位」，相位有 ~10% 牌宽的
        系统性误差时，对称的筒子仍能 0.9，但万/索的字形被切掉一侧就掉到 0.55，
        环境亮度一变就跌破阈值返回 None -> 整张牌被丢 -> 手牌张数不足 ->
        建议不显示。局部重试把这些临界牌拉回 0.9 区间（实测 1s 0.55 -> 0.92）。

        allow_retry=False 用于「不像手牌行」的候选带：那些带里几乎每个格子都是
        None，逐个跑 7 次重试会把单帧从 15ms 拖到 60ms+，纯浪费。

        性能关键有两条，缺一条稳态就会崩：

        (1) **每个位置在一个 TTL 周期内最多重试一次**（记在 `_retry_done`）。
            切图相位误差是**静态**的，同一位置重复搜索必然得到同样的结果，
            所以第二次搜索是纯浪费。注意这里不能只标记"重试失败"的格子：
            重试**成功但 conf 仍低于 RETRY_CONF** 的牌（低对比度/JPEG 噪声
            场景很常见，例如 0.40 -> 0.55）会在下一帧再次跌破门限、再搜 7 次,
            实测把稳态从 15ms 拖到 125ms。必须无条件标记。

        (2) **带级相位提示**（`_phase_hint`）。同一牌行里所有牌共享同一个
            切牌网格，相位误差因此是**全行共享**的。第一张牌搜出最佳偏移后
            记进提示，同行后续牌先试这一个偏移即可命中，把整行的分类次数
            从 13x7 降到 ~13x2。

        重试结果要写回 `_face_cache`，这样下一帧直接命中，连一次都不用重搜。
        缓存有 TTL，所以即使缓存了一次坏结果，最多 _FACE_CACHE_TTL 秒就会重算。
        """
        x, y, w, h = rect
        H, W = img.shape[:2]
        face = img[y:y + h, x:x + w]
        label, conf = self._classify_face_cached(face, rect)
        if not allow_retry or (label is not None and conf >= RETRY_CONF):
            return label, conf

        q = self._cache_key(rect)
        if q in self._retry_done:
            return label, conf
        if self._retry_budget <= 0:
            return label, conf  # 本帧重试预算用尽（防御性上限）
        self._retry_done.add(q)
        self._retry_budget -= 1

        def _try(fdx, fsh):
            """按 (相对 x 偏移, 相对内缩) 重切一次并分类。"""
            dx = int(round(fdx * w))
            sh = int(round(fsh * w))
            x1 = max(0, x + dx + sh)
            x2 = min(W, x + dx + w - sh)
            if x2 - x1 < 24:
                return None, 0.0
            return self._classify_face(img[y:y + h, x1:x2])

        # 注：曾经这里有个 _try_upscaled()（小牌面上采样到 120 再分类）。
        # 尺度归一化上移到 _classify_face 入口（_canon_scale）后它已多余 ——
        # 留着反而变成「先重采样到 120、再被归一到 168」的二次重采样，比直接
        # 归一更糊。而且它的触发条件（长边 < 95）极其脆弱：放宽到 <110/<140
        # 会让基准图也被卷进去，实测把 15 项压测从 13 项直接打到 2~5 项。
        best_label, best_conf, best_off = label, conf, None
        # ---- 先试本行已学到的相位提示（1 次分类就可能搞定）----
        hint_key = y // max(8, h)
        hint = self._phase_hint.get(hint_key)
        if hint is not None:
            lab2, conf2 = _try(*hint)
            if lab2 is not None and conf2 > best_conf:
                best_label, best_conf, best_off = lab2, conf2, hint
        # ---- 提示不够用才做完整搜索 ----
        if best_conf < 0.90:
            for off in RETRY_OFFSETS:
                if off == hint:
                    continue  # 刚试过
                lab2, conf2 = _try(*off)
                if lab2 is not None and conf2 > best_conf:
                    best_label, best_conf, best_off = lab2, conf2, off
                    if best_conf >= 0.90:
                        break  # 已经很有把握，不必再试
        if best_label is not None and best_conf > conf:
            # 回写缓存：thumb 用**原 rect** 的缩略图（下一帧查的也是原 rect），
            # label/conf 用重试得到的更好结果。
            self._cache_put(q, self._face_thumb(face), best_label, best_conf)
            # 只有把牌救到「确信」区间的偏移才配当全行提示，
            # 免得一个勉强的偏移把整行都带偏。
            if best_off is not None and best_conf >= 0.85:
                self._phase_hint[hint_key] = best_off
        return best_label, best_conf

    @staticmethod
    def _cache_key(rect):
        """牌面缓存 key：位置量化到 4px 网格，容忍帧间 ±4px 抖动。"""
        return (rect[0] // 4, rect[1] // 4, rect[2] // 4, rect[3] // 4)

    def _cache_put(self, q, thumb, label, conf):
        if len(self._face_cache) >= self._FACE_CACHE_MAX:
            self._face_cache.clear()
        self._face_cache[q] = (thumb, label, conf)

    def _classify_face_cached(self, face, rect):
        """带缓存的牌面分类。

        命中条件（同时满足）：
          1. 量化位置一致（容忍 ±4px 抖动）
          2. 16x16 缩略图平均绝对差 < FACE_CACHE_TOL（容忍 JPEG 噪声/亮度微变）
        不命中才真正跑 _classify_face，并把结果写回缓存。

        注意：label 为 None 的结果**不缓存**——那是"这张脸没认出来"，
        缓存它会让一次偶然的漏检永久粘住这个位置。
        """
        # TTL：到期整体清空，强制重新分类。
        # 这是缓存的"自愈"机制 —— 即使出现极端情况下的误命中，
        # 也最多存活 _FACE_CACHE_TTL 秒就会被纠正。
        now = time.time()
        if self._face_cache_ts == 0.0:
            self._face_cache_ts = now
        elif now - self._face_cache_ts > _FACE_CACHE_TTL:
            self._face_cache.clear()
            # 重试标记与相位提示同 TTL 自愈：画面/布局变了要重新学
            self._retry_done.clear()
            self._phase_hint.clear()
            self._face_cache_ts = now

        q = self._cache_key(rect)
        thumb = self._face_thumb(face)
        prev = self._face_cache.get(q)
        if prev is not None:
            p_thumb, p_label, p_conf = prev
            if p_label is not None:
                diff = float(np.abs(thumb - p_thumb).mean())
                if diff < FACE_CACHE_TOL:
                    return p_label, p_conf
        label, conf = self._classify_face(face)
        self._cache_put(q, thumb, label, conf)
        return label, conf

    @staticmethod
    def _sliding_max(a, k):
        """窗口 k 的滑动最大值，中心对齐：out[i] = max(a[i-k//2 : i+k//2+1])。

        手写实现（不用 np.lib.stride_tricks.sliding_window_view，
        那是 numpy 1.20+ 才有的，真机是 numpy 1.19 —— 会直接崩）。
        边界用 edge 填充，等价于原实现里越界就跳过。
        """
        n = len(a)
        if n == 0:
            return a
        out = a.copy()
        half = k // 2
        for d in range(1, half + 1):
            # 右移 d：out[i] = max(out[i], a[i+d])
            sr = np.empty_like(a)
            if n > d:
                sr[:n - d] = a[d:]
            else:
                sr[:] = a[-1]
            sr[n - d if n > d else 0:] = a[-1]
            out = np.maximum(out, sr)
            # 左移 d：out[i] = max(out[i], a[i-d])
            sl = np.empty_like(a)
            if n > d:
                sl[d:] = a[:n - d]
                sl[:d] = a[0]
            else:
                sl[:] = a[0]
            out = np.maximum(out, sl)
        return out

    def _seg_search_best(self, sig, x0, span0, period):
        """向量化网格搜索，返回 (score, xmin, tile_w, n)。

        旧实现是 10(n) x 13(dw) x 13(dx) = 1690 次 Python 函数调用，
        profile 实测 79ms/帧 —— 占了单帧 1/3。改成：
          - 候选 n 只取自相关周期附近的 HAND_COUNTS（通常 1~3 个，而非 10 个）
          - 每个 (n, tile_w) 下，所有 dx 一次性向量化算完（numpy 矩阵运算）
          - cell mean 用前缀和 O(1) 查表；edge score 用预计算滑动 max 查表
        结果：~3x13 = 39 次 numpy 批量操作，实测 <3ms。
        """
        sw = len(sig)
        if sw < 16 or period <= 0 or span0 <= 0:
            return None

        # 前缀和：cell mean = (cum[b] - cum[a]) / (b - a)，O(1) 查表
        cum = np.concatenate(([0.0], np.cumsum(sig, dtype=np.float64)))
        # 滑动 max（窗口 5，中心对齐 sig[xi-2:xi+3]）
        smax = self._sliding_max(sig, 5)

        # 候选 n：只取自相关周期附近的合法张数（强先验 + 周期已很准）
        n0 = int(round(span0 / period))
        cands = [n for n in self._HAND_COUNTS if abs(n - n0) <= 2]
        if not cands:
            cands = [min(self._HAND_COUNTS, key=lambda n: abs(n - n0))]

        dxs = np.arange(-self._SEG_XMIN_RANGE, self._SEG_XMIN_RANGE + 1, 2)
        xmins = (x0 + dxs).astype(np.float64)

        best = None
        for n in cands:
            if n > 17 or n < 1:
                continue
            base_w = span0 / float(n)
            ks = np.arange(n + 1, dtype=np.float64)
            for dw in np.linspace(-self._SEG_PERIOD_FRAC, self._SEG_PERIOD_FRAC,
                                  self._SEG_PERIOD_STEPS):
                tw = base_w * (1.0 + float(dw))
                if tw <= 5:
                    continue
                # (D, n+1) 所有 dx 的所有边界位置
                xs = xmins[:, None] + ks[None, :] * tw
                xi = np.round(xs).astype(np.int64)
                ok = (xi >= 2) & (xi + 3 <= sw)
                idx = np.where(ok, xi, 0)
                edge_vals = np.where(ok, smax[idx], 0.0)
                n_edge = ok.sum(axis=1).astype(np.float64)
                edge_sum = edge_vals.sum(axis=1)

                # cell: [a, b)，a=左边界+3, b=右边界-3
                a = xi[:, :-1] + 3
                b = xi[:, 1:] - 3
                valid_c = (b > a) & (a >= 0) & (b <= sw)
                a_c = np.clip(a, 0, sw)
                b_c = np.clip(b, 0, sw)
                cell_sum = np.where(valid_c, cum[b_c] - cum[a_c], 0.0)
                cell_len = np.where(valid_c, b_c - a_c, 0)
                n_cell = valid_c.sum(axis=1).astype(np.float64)
                cell_tot = cell_sum.sum(axis=1)
                cell_len_tot = cell_len.sum(axis=1).astype(np.float64)

                denom_e = np.maximum(n_edge, 1.0)
                denom_c = np.maximum(cell_len_tot, 1.0)
                edge_mean = edge_sum / denom_e
                cell_mean = cell_tot / denom_c
                pen = 2.0 * abs(tw - period) / period
                scores = edge_mean + 0.5 * cell_mean - pen
                scores = np.where((n_edge > 0) & (n_cell > 0), scores, -1e9)

                i = int(np.argmax(scores))
                sc = float(scores[i])
                if best is None or sc > best[0]:
                    best = (sc, float(xmins[i]), float(tw), n)
        return best

    def _segment_tiles(self, band_img, tile_h_hint=0):
        """牌行 -> 逐张牌。

        v2 算法（自相关周期 + 张数先验 + 网格搜索精修）：

          旧实现问题：等宽网格用 span/n_tiles 切牌，n_tiles 靠 round(span/period)，
          period 靠 FFT 估（频率分辨率受限）。周期估错 5% 就会把 13 张切成 14 张，
          整张脸跨在两张牌上，识别全错。

          新实现：
            1. 列剖面信号 = "亮像素占比"（抗牌面图案干扰）
            2. 周期 P：FFT 加速的归一化自相关，抛物线插值到亚像素（精度 ±0.5px）
            3. 张数 n：枚举麻将手牌张数先验（_HAND_COUNTS），对每张牌算
               "网格对齐得分"，取最高者。**禁止**盲目 round(span/P) ——
               张数先验是强先验，应该用它做最后决断。
            4. 网格搜索 (xmin ± 14px, period ± 6%) 精修，提升对齐得分。

        行为不变量：
          - 输入是 4-tuple Rect 列表 (x1, y1, x2, y2)
          - 张数 < _SEG_MIN_TILES 返回空（与旧版一致）
          - pad = max(3, int(0.05 * tile_w))：避免缝边切掉笔画
        """
        bh, bw = band_img.shape[:2]
        if bh < 20 or bw < 60:
            return []
        # 防御：cv2.cvtColor(..., COLOR_BGR2GRAY) 在非 3 通道 / 非 uint8 输入上
        # 会直接 SIGSEGV。先归一化成「3 通道 uint8 且内存连续」再转灰度。
        if not isinstance(band_img, np.ndarray):
            return []
        if band_img.ndim == 2:
            band_img = cv2.cvtColor(band_img, cv2.COLOR_GRAY2BGR)
        elif band_img.ndim != 3:
            return []
        if band_img.dtype != np.uint8:
            try:
                band_img = band_img.astype(np.uint8)
            except Exception:
                return []
        band_img = np.ascontiguousarray(band_img)
        gray = (cv2.cvtColor(band_img, cv2.COLOR_BGR2GRAY)
                if band_img.ndim == 3 else band_img).astype(np.float32)
        y1, y2 = 0, (tile_h_hint if tile_h_hint > 0 else bh)
        sub = gray[y1:y2, :]
        sh, sw = sub.shape
        if sh < 16 or sw < 60:
            return []

        sig = self._col_profile(sub)
        if sig.max() < 0.05:
            return []

        # ---- 1. 找 active 区间 [x0, x1] ----
        active = np.where(sig > max(0.06, 0.25 * sig.max()))[0]
        if len(active) < 30:
            return []
        x0, x1 = int(active[0]), int(active[-1])
        span0 = x1 - x0
        if span0 < 30:
            return []

        # ---- 2. FFT 自相关求周期 ----
        period, strength = self._autocorr_period(
            sig[x0:x1 + 1],
            min_p=max(20.0, span0 / 17.0),
            max_p=min(span0 / 3.0, 280.0),
        )
        if period is None or period <= 0:
            period = span0 / 13.0
            strength = 0.0

        # ---- 3. 向量化网格搜索精修 (n, xmin, period) ----
        # 见 _seg_search_best 的注释：旧的三重 Python 循环实测 79ms/帧，
        # 向量化后 <3ms，且与旧实现选出完全相同的 (xmin, tile_w, n)。
        best = self._seg_search_best(sig, x0, span0, period)

        if best is None:
            return []
        _, xmin, tile_w, n = best
        if n < self._SEG_MIN_TILES:
            return []
        if tile_w < 12:
            return []

        # ---- 4. 输出边界 ----
        pad = max(3, int(0.05 * tile_w))
        out = []
        for k in range(n):
            x1b = max(0, int(round(xmin + k * tile_w)) - pad)
            x2b = min(sw, int(round(xmin + (k + 1) * tile_w)) + pad)
            if x2b - x1b < 8:
                continue
            out.append((x1b, int(y1), x2b, int(y2)))
        return out

    # -------------------------------------------------------------- 主入口

    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        img = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        ih, iw = img.shape[:2]
        self.last_screen = (iw, ih)

        # dets 始终是 (rect, label, conf) 三元组，保证 confs 不会因排序与 result 错位
        dets = self._detect_once(img)
        # 旋转重试：质量门限（参见 detect_all_rows 的注释）。
        if self._should_try_rotation(dets):
            for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                try:
                    rot_img = cv2.rotate(img, rot)
                except cv2.error:
                    continue
                d2 = self._detect_once(rot_img)
                if self._rotation_is_better(dets, d2):
                    dets = [(self._unrotate_rect(r, rot, iw, ih), l, c)
                            for (r, l, c) in d2]
                    break

        # detect() 只取手牌行（张数最接近 13/14）；detect_all_rows() 用全部行。
        dets = self._pick_rows(dets)

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

    def detect_all_rows(self, image: CVImage, classify: bool = True,
                        allow_rotation: bool = True, allow_retry: bool = True
                        ) -> List[List[Tuple[Rect, Optional[str], float]]]:
        """返回所有检测到的牌行（不挑选手牌行）。

        classify=False：跳过分类（供方向探测用，快 10x）。
        allow_rotation=False：禁用内部旋转重试（方向探测自己就在枚举
        方向；engine 锁定方向后也不需要重试）。
        allow_retry=False：禁用牌面「局部重试」。方向探测要跑 4 个方向，
        每个方向都做重试会把冷启动从 400ms 拖到 1400ms；而探测只需要方向间的
        **相对**质量对比，重试是锁定方向之后才需要的精修。

        每行是一组 [(rect, label, conf), ...]，已由 x 排序。
        引擎据此区分「自己手牌行」（张数最接近 13/14、牌最大）与
        「各家牌河行」（其余行），从而把所有打出的牌纳入剩余牌计算。

        旋转重试：原实现"识别出 <4 张牌就尝试旋转"——这会把横屏正确识别
        因 13 张全在手牌行、牌河没识别到而 <4 而误触发旋转分支，把竖向画面
        当横向识别（横屏手机截屏方向错时尤其）。改为"原始方向识别到的手牌
        数 < 期望值 + 平均置信度 < 0.45"才尝试旋转，且旋转分支的置信度均值
        必须**严格高于**原始方向才采纳。
        """
        img = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        ih, iw = img.shape[:2]
        self.last_screen = (iw, ih)
        # 每次检测重置重试预算，保证「单帧最坏耗时」有确定上界
        self._retry_budget = self._RETRY_BUDGET
        dets = self._detect_once(img, classify=classify,
                                 allow_retry=allow_retry)
        # 旋转重试的触发条件改为"质量门限"而不是数量门限
        if allow_rotation and self._should_try_rotation(dets):
            for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                try:
                    rot_img = cv2.rotate(img, rot)
                except cv2.error:
                    continue
                d2 = self._detect_once(rot_img, classify=classify,
                                       allow_retry=allow_retry)
                if self._rotation_is_better(dets, d2):
                    dets = [(self._unrotate_rect(r, rot, iw, ih), l, c)
                            for (r, l, c) in d2]
                    break
        return self._group_rows(dets)

    @staticmethod
    def _should_try_rotation(dets) -> bool:
        """旋转重试触发条件：低质量且数量少。

        原实现只看 `len(dets) < 4`，会把横屏正常识别（手牌 13/14 张全在手牌
        行、其它行未被识别）误触旋转。现改为：检测数 <6（基本没什么牌可识别）
        **或** 平均置信度 <0.45 才尝试旋转。
        """
        if not dets:
            return True
        if len(dets) < 6:
            return True
        avg_conf = float(sum(d[2] for d in dets)) / len(dets)
        return avg_conf < 0.45

    @staticmethod
    def _rotation_is_better(orig, rot) -> bool:
        """旋转分支采纳门限：检测到的牌更多 **且** 平均置信度更高。

        避免旋转分支因"随便多识别了几张牌"而误激活——必须两边都好于原方向。
        """
        if not rot:
            return False
        if len(rot) <= len(orig):
            return False
        orig_conf = float(sum(d[2] for d in orig)) / len(orig) if orig else 0.0
        rot_conf = float(sum(d[2] for d in rot)) / len(rot)
        return rot_conf > orig_conf + 0.05

    @staticmethod
    def _group_rows(dets):
        """把检测到的牌按 y（行）分组，每组内按 x 排序。"""
        if not dets:
            return []
        rows: Dict[int, list] = {}
        for d in dets:
            rh = d[0][3]
            key = d[0][1] // max(8, rh)
            rows.setdefault(key, []).append(d)
        out = []
        for g in rows.values():
            g.sort(key=lambda d: d[0][0])
            out.append(g)
        out.sort(key=lambda g: g[0][0][1])
        return out

    def _tile_to_face_rect(self, tile, y1e, inv):
        """把 work 坐标的 tile 换算成原图坐标的「牌面矩形」(x, y, w, h)。

        分类必须在全分辨率上跑：_classify_face 内的相对阈值
        (0.42*fw, 0.24*fh, largest_frac 等) 是按全分辨率牌面 (~168x140)
        调出来的，套到 60x70 的 work 分辨率牌面会全部不达标（9m/3m 都跪）。
        所以带检测（Sobel + 行列能量）走 work 求快，分类单独回原图求准。

        太小的矩形（<20px）返回 None —— 不可能是牌。
        """
        tx1, ty1, tx2, ty2 = tile
        ix_w = int((tx2 - tx1) * FACE_INSET)
        iy_w = int((ty2 - ty1) * FACE_INSET)
        fx1w, fy1w = tx1 + ix_w, y1e + ty1 + iy_w
        fx2w, fy2w = tx2 - ix_w, y1e + ty2 - iy_w
        fx1 = int(round(fx1w * inv))
        fy1 = int(round(fy1w * inv))
        fw_f = max(1, int(round((fx2w - fx1w) * inv)))
        fh_f = max(1, int(round((fy2w - fy1w) * inv)))
        if fw_f < 20 or fh_f < 20:
            return None
        return (fx1, fy1, fw_f, fh_f)

    @staticmethod
    def _bands_compete(a, b) -> bool:
        """判断两个候选带是不是「同一物理牌行的竞争假设」。

        条件：y 区间与 x 区间**同时**高度重叠（各 >50%）。

        为什么 x 也要判：同一 y 高度上可以合法地存在两组横排牌
        （例如对家牌河与某家副露），那是两个**真实**行，不能去重。
        只有 x 也基本重合，才说明是同一片像素被切了两遍。
        """
        def _ov(lo1, hi1, lo2, hi2):
            inter = min(hi1, hi2) - max(lo1, lo2)
            if inter <= 0:
                return 0.0
            return inter / float(max(1, min(hi1 - lo1, hi2 - lo2)))

        ay1 = min(r[1] for r in a)
        ay2 = max(r[1] + r[3] for r in a)
        by1 = min(r[1] for r in b)
        by2 = max(r[1] + r[3] for r in b)
        if _ov(ay1, ay2, by1, by2) <= 0.5:
            return False
        ax1 = min(r[0] for r in a)
        ax2 = max(r[0] + r[2] for r in a)
        bx1 = min(r[0] for r in b)
        bx2 = max(r[0] + r[2] for r in b)
        return _ov(ax1, ax2, bx1, bx2) > 0.5

    @staticmethod
    def _band_geom_score(rects) -> float:
        """候选带的**几何**可信度（不做分类，供快速模式用）。

        判据（麻将先验）：
          - 张数越接近 13/14 越好（手牌行；副露后 10~12 也常见）
          - 牌宽越一致越好（同一行的牌等宽，变异系数应接近 0）
          - 宽高比越接近 0.85 越好（麻将牌正面的典型比例）
        """
        n = len(rects)
        ws = [r[2] for r in rects]
        w_med = float(np.median(ws))
        w_cv = float(np.std(ws)) / max(1e-6, w_med)
        asp = float(np.median([r[2] / float(max(1, r[3])) for r in rects]))
        n_pen = min(abs(n - 13), abs(n - 14))
        return -(0.30 * n_pen) - (2.0 * w_cv) - (3.0 * abs(asp - 0.85))

    def _band_probe_score(self, rects, img) -> float:
        """候选带的**分类**可信度：抽样若干格子跑分类，取平均置信度。

        为什么必须用分类而不是纯几何：竞争假设的几何指标极其接近
        （实测三个假设的牌宽变异系数都是 0.00~0.01、宽高比 0.763/0.868/0.921
        全都落在合法区间 [0.52, 0.95] 内），几何判据根本分不出胜负。
        而错周期切出来的「牌」其实是两张牌各一半的拼接，分类置信度会塌，
        这是唯一可靠的直接指标。

        只抽 3 格（均匀取位），成本 ~3x13ms，远低于把整带都分类掉。
        """
        n = len(rects)
        idx = sorted(set([n // 6, n // 2, (5 * n) // 6]))
        confs = []
        for i in idx:
            x, y, w, h = rects[i]
            _lab, c = self._classify_face(img[y:y + h, x:x + w])
            confs.append(c)
        if not confs:
            return 0.0
        return float(sum(confs)) / len(confs)

    def _dedup_bands(self, cand_bands, img, classify):
        """把「同一物理牌行被不同周期重复切出」的竞争带收敛成一个。

        _find_bands + _segment_tiles 在光照异常时会对同一手牌行给出多个
        周期假设（实测亮度 +20% 时按 70/58/66px 切成 14/14/13 三份）。
        若不去重：
          - 性能：单帧分类次数 13 -> 41，冷启动从 600ms 涨到 2000ms+
          - 正确性：结果里出现多个 len>=11 的行，下游 _pick_hand_row
            在竞争行里挑，挑错就整手牌错（缩放场景混入 1z/2m/2s）
        """
        if len(cand_bands) < 2:
            return cand_bands
        # 先按几何分降序，保证每组的"组长"是几何上最像牌行的那个
        order = sorted(cand_bands, key=lambda b: -self._band_geom_score(b))
        groups: List[list] = []
        for b in order:
            for g in groups:
                if self._bands_compete(b, g[0]):
                    g.append(b)
                    break
            else:
                groups.append([b])
        out = []
        for g in groups:
            if len(g) == 1:
                out.append(g[0])
                continue
            if not classify:
                # 快速模式（方向探测）不值得为去重付分类代价，用几何分
                out.append(g[0])
                continue
            best, best_s = g[0], -1.0
            for b in g:
                s = self._band_probe_score(b, img)
                if s > best_s:
                    best_s, best = s, b
            out.append(best)
        # 恢复自上而下的顺序，保持后续行分组的稳定性
        out.sort(key=lambda b: min(r[1] for r in b))
        return out

    def _detect_once(self, img: np.ndarray, classify: bool = True,
                     allow_retry: bool = True
                     ) -> List[Tuple[Rect, Optional[str], float]]:
        """检测一帧里的所有牌。

        classify=False 时**跳过最贵的牌面分类**（实测 173ms → 17ms），
        只返回切牌位置（label=None, conf=0）。仅供方向探测使用 ——
        判断"哪个方向牌最多"根本不需要知道是什么牌。

        allow_retry=False 时禁用牌面局部重试（见 detect_all_rows 说明）。
        """
        h, w = img.shape[:2]
        # ===== 低光/色温预处理：CLAHE（仅作用于"工作图"，分类用的全分辨率 face 仍取自原图）=====
        # 必须只增强工作图，不能动原图——_classify_face 的几何守卫是按原始色相
        # 设计的（红中红、绿發绿、白板纯白），CLAHE 会破坏色相判别。
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        work_gray = StructuralDetector._apply_clahe(gray)
        longest = float(max(h, w))
        inv = 1.0
        if longest > MAX_WORKING_EDGE:
            inv = longest / MAX_WORKING_EDGE
            work = cv2.resize(work_gray, (max(1, int(w / inv)), max(1, int(h / inv))),
                              interpolation=cv2.INTER_AREA)
            work_color = cv2.resize(img, (max(1, int(w / inv)), max(1, int(h / inv))),
                                    interpolation=cv2.INTER_AREA)
        else:
            work, work_color = work_gray, img

        wh, ww = work.shape[:2]
        tile_h = max(10, int(TILE_H_RATIO * min(wh, ww)))
        bands = self._find_bands(work, tile_h)

        # ================== 阶段 1：切牌（便宜），收集候选带 ==================
        # 不在这里直接分类。原实现「边切边分类」有两个致命问题：
        #   1. 同一个物理牌行会被 _find_bands + _segment_tiles 用**不同周期**
        #      重复切出多个候选带（实测亮度 +20% 时手牌行被按 70/58/66px
        #      切成三份 14/14/13 格），41 个格子里 28 个是同一行的错误切法。
        #   2. 这些重复行会一起进入结果，下游 _pick_hand_row 要在多个
        #      「len>=11 的行」里挑，挑错就整手牌错（缩放场景混入 1z/2m/2s
        #      就是这么来的）。
        # 所以先只切牌，再去重，最后才对胜出的带做昂贵的分类。
        cand_bands = []
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
            # 跨风格增强：阈值从 0.12 放宽到 0.18，避免彩色边框牌因灰度较均匀被误拒。
            band_gray = work[y1:y2, :].astype(np.float32)
            med = float(np.median(band_gray))
            unif = float(((band_gray >= med - 12) & (band_gray <= med + 12)).mean())
            if unif > 0.18:
                continue
            tiles = self._segment_tiles(band, y2 - y1)
            if len(tiles) < 3:
                continue
            rects = []
            for t in tiles:
                r = self._tile_to_face_rect(t, y1e, inv)
                if r is not None:
                    rects.append(r)
            if len(rects) < 3:
                continue
            cand_bands.append(rects)

        # ================== 阶段 2：手牌行预判 ==================
        # 候选带可能很多（亮度 +20% 时实测有 3 个 14/13 格的带），但绝大多数是
        # **误检**：UI 元素（倒计时牌、按钮、头像）在亮度异常时 unif 均匀度
        # 检查会被过，按真实牌面的几何特征（等宽、13/14 格）误进入候选集。
        # 经验上：手牌行几乎一定在画面**最靠下**那一条带；牌河/UI 元素都在
        # 画面中部或上部。直接按 y 位置取**最下且格数≥11**的那条作为手牌行
        # 候选 —— 不够格（<11）才退而求其次选格数最多的，避免副露时漏掉。
        #
        # **不分类就能判断**，省下 28 个 UI 误检格子的分类时间
        # （= 364ms / 帧，约等于单帧冷启的一半）。
        #
        # 方向探测（classify=False）路径必须**保留所有带**，因为它要统计
        # 哪个方向牌最多；其他行里可能恰好是某个方向的手牌行。
        if classify and cand_bands:
            bottom = max(cand_bands, key=lambda b: max(r[1] + r[3] for r in b))
            if len(bottom) >= 11:
                cand_bands = [bottom]
            else:
                cand_bands = [max(cand_bands, key=lambda b: len(b))]
        # ================== 阶段 3：竞争带去重 ==================
        cand_bands = self._dedup_bands(cand_bands, img, classify)

        # ================== 阶段 4：分类（贵） ==================
        dets: List[Tuple[Rect, Optional[str], float]] = []
        for rects in cand_bands:
            # 只对「像手牌行」的带开启局部重试。门限用 >=11 格，与 engine
            # 判定 has_hand_row 的 `len(r) >= 11` 保持一致 —— 真手牌行是
            # 13/14 张，副露后也还有 11 张以上。
            #
            # 为什么不能放宽到 >=8：牌河/背景带里几乎每格都是 None，
            # 逐格跑 7 次重试纯浪费，还会把单帧拖到秒级。
            band_retry = allow_retry and len(rects) >= 11
            for (fx1, fy1, fw_f, fh_f) in rects:
                if not classify:
                    # 快速模式（方向探测用）：只返回位置，不分类
                    dets.append(((fx1, fy1, fw_f, fh_f), None, 0.0))
                    continue
                # 牌级缓存在 _classify_face_retry 内部按原 rect 命中；
                # 命中时跳过 13ms 的分类，直接用上帧结果。
                label, conf = self._classify_face_retry(
                    img, (fx1, fy1, fw_f, fh_f), allow_retry=band_retry)
                if label is None:
                    # 仍然丢弃未识别的格子。
                    # 试过「保留 None 格子让投票器从历史恢复」，结果更糟：
                    # 下游（行分组、方向探测的 avg_conf、has_hand_row 判据）
                    # 全都把 None 格子算进去，压力测试从 11/15 掉到 7/15。
                    # 真正的解法是局部重试 —— 让 None 尽量不出现。
                    continue
                aspect = fw_f / float(fh_f)
                if aspect < MIN_TILE_ASPECT or aspect > MAX_TILE_ASPECT:
                    conf *= 0.55  # 牌形不对，降置信
                dets.append(((fx1, fy1, fw_f, fh_f), label, conf))

        # 同步刷新 last_top_score：之前只在 detect() 路径下更新，
        # detect_all_rows() 路径下恒为 0.0，导致 engine.process() 的
        # top_score 字段永远显示 0.0，诊断信息全废。
        # 移到 _detect_once 末尾统一更新，两个路径都覆盖。
        self.last_top_score = float(
            max((c for (_, l, c) in dets if l is not None), default=0.0))
        # 注意：这里返回「所有检测到的牌」，不做行挑选。
        # detect() 仍只取手牌行（兼容旧测试）；detect_all_rows() 用全部行识别牌河。
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
