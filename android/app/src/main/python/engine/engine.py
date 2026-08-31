from __future__ import annotations
import hashlib
import traceback
from collections import deque
from typing import Dict, List, Optional, Tuple

import json
import os
import time

import cv2
import numpy as np

from .engine_result import EngineResult
from recognition.stage import DetectionResult
from recognition.structural import (
    StructuralDetector,
    MIN_CONF,
    MIN_TILE_ASPECT,
    MAX_TILE_ASPECT,
)
from utils.stubs import CVImage
from trainer.trainer import Trainer
from trainer.objects.tile_collection import TileCollection
from trainer.objects.tile import Tile
from trainer.utils.convert import mpsz_to_tile34_index, tiles34_index_to_mpsz
from modes import load_mode, hand_sizes, available_set, MODES

# 一局中的合法手牌张数：13 = 待摸牌，14 = 刚摸到牌
VALID_HAND_SIZES = (13, 14)

# 预览图最大宽度。原实现每帧都把全屏截图做 PNG 编码（100~300ms），
# 而 Dart 端并没有使用这张图，纯属浪费，是识别卡顿的主因之一。
PREVIEW_MAX_WIDTH = 240

# 引擎侧的最低置信度：比结构识别器的 MIN_CONF 更严，挡掉"卡在两张牌之间"的假命中。
# 实测：MIN_CONF=0.30 时每帧约 10% 出"白板刷屏"（筒/索背景浅被判成白板），
# 调到 0.40 后这类假阳性基本绝迹（漏检的牌由多帧投票补回）。
ENGINE_MIN_CONF = 0.40

# 同帧互斥上限：手牌里同种牌最多 4 张（4 张相同的合法牌型）。
# 同一帧 mpsz 里出现 ≥MAX_DUP_PER_TILE+1 张同字必是误判，整手拒绝。
MAX_DUP_PER_TILE = 4

# 帧差阈值：相邻两帧的工作区域分块均值差的 L1 范数（256 个 8x8 块、每块均值 0~255）。
# 0.0 = 完全相同；实测牌局"两张相邻牌静止"约 1~3，"出一张牌"约 20~60。
# 阈值取 1.5：低于则视为画面无变化、跳过识别。
FRAME_DIFF_THRESHOLD = 1.5

# 帧差块边长（像素，工作分辨率上）。32 块 = 256 块覆盖整屏，约每块 12x12 work px。
FRAME_DIFF_BLOCK = 32

# 多帧投票窗口：保留最近 N 帧的检测结果，按位置投票得到稳定标签。
VOTE_WINDOW = 3

# 同位置归并半径（work 坐标）。相邻两张牌的中心距 ≈ 牌宽 ≈ tile_h；
# 归并半径取 0.45 * tile_h 让"同一张牌的位置"被并成一组。
VOTE_MERGE_FRAC = 0.45

# 位置投票最低票数：占总票数 ≥ 该比例的标签才采纳。低于则保留最近一帧的标签。
VOTE_MIN_FRAC = 0.5


def get_mpsz(detection: DetectionResult) -> str:
    tiles = sorted(detection, key=lambda x: x[0][0])
    # Ignore tiles that we are unable to detect
    return ''.join(tile[1] for tile in tiles if tile[1] is not None)


def _to_uint8_buffer(image_data) -> np.ndarray:
    """把 Java 传来的图像字节转成 cv2.imdecode 需要的 uint8 一维数组。

    Chaquopy 可能给出 bytes / Java byte[] 序列 / numpy 数组，这里都兼容。
    原实现写的是 np.array(image_data)：当传入 bytes 时得到的是「0 维」数组，
    cv2.imdecode 会直接失败，是识别链路上的一处隐藏断点。
    """
    if isinstance(image_data, np.ndarray):
        return image_data.astype(np.uint8, copy=False)
    if isinstance(image_data, (bytes, bytearray, memoryview)):
        return np.frombuffer(bytes(image_data), dtype=np.uint8)
    return np.asarray(list(image_data), dtype=np.uint8)


def _make_preview(image: CVImage) -> CVImage:
    """生成一张很小的预览图（不再编码全屏截图）。"""
    try:
        h, w = image.shape[:2]
        if w > PREVIEW_MAX_WIDTH:
            new_h = max(1, int(h * PREVIEW_MAX_WIDTH / w))
            return cv2.resize(image, (PREVIEW_MAX_WIDTH, new_h), interpolation=cv2.INTER_AREA)
        return image
    except Exception:
        traceback.print_exc()
        return np.zeros((1, 1, 3), dtype=np.uint8)


def _block_diff_signature(work_gray: np.ndarray) -> float:
    """计算工作区域分块均值差的 L1 范数（用于帧差判等）。

    输入：灰度图（来自 detect 内部降采样后的 work）。
    返回：相邻两次调用的差异度。

    实现：把图切成 FRAME_DIFF_BLOCK 大小的方块，每块取均值，构成一个 256 维向量。
    两帧之间的 L1 距离就是这个数。
    数值经验：
      - 完全静止画面 ≈ 0.0~0.5
      - 出牌、摸牌、UI 微动 ≈ 5~60
      - 大幅重绘（动画、菜单弹出） ≈ 80+
    """
    h, w = work_gray.shape[:2]
    bh = FRAME_DIFF_BLOCK
    # 计算行块数与列块数；图小则整图退化为一块也行
    rows = max(1, h // bh)
    cols = max(1, w // bh)
    sig = np.zeros((rows * cols,), dtype=np.float32)
    for r in range(rows):
        for c in range(cols):
            patch = work_gray[r * bh:(r + 1) * bh, c * bh:(c + 1) * bh]
            sig[r * cols + c] = float(patch.mean()) if patch.size else 0.0
    return sig


class _FrameSkipper:
    """帧差去重：相邻帧画面几乎相同时跳过完整识别。

    只跳过 detect()，**不**跳过状态回传（界面仍会按时收到"画面无变化"的心跳）。
    这样 CPU 占用直接砍半，但用户体验不变。
    """

    def __init__(self) -> None:
        self._last_sig: Optional[np.ndarray] = None
        # 缓存上一帧"识别到的手牌 + 标签"——画面不变时整个 process() 直接复用。
        # 缓存的是 EngineResult.result（dict 序列化形式），不是 EngineResult 对象本身，
        # 因为后者带 cv2 图像，跨调用持有可能让 Chaquopy 释放不及时。
        self._last_payload: Optional[str] = None
        self._last_top_score: float = 0.0

    def diff(self, work_gray: np.ndarray) -> float:
        cur = _block_diff_signature(work_gray)
        if self._last_sig is None or self._last_sig.shape != cur.shape:
            self._last_sig = cur
            return float("inf")  # 首帧肯定不跳
        diff = float(np.abs(cur - self._last_sig).sum())
        self._last_sig = cur
        return diff

    def remember(self, payload: str, top_score: float) -> None:
        self._last_payload = payload
        self._last_top_score = top_score

    @property
    def cached(self) -> Optional[str]:
        return self._last_payload

    @property
    def cached_top_score(self) -> float:
        return self._last_top_score


class _TileVoter:
    """多帧投票：把最近 N 帧的识别结果按 (y, x) 位置归并、多数表决出稳定标签。

    解决单帧识别里最头疼的"筒/索被认成白板"——同样的位置连续 2~3 帧都被识别
    成"白板"是极不可能的（白板是字牌，且牌堆里只有 4 张），投票后这些假阳性
    标签会被真实标签覆盖。

    输入：每帧 detect 出的 [(rect, label, conf), ...]
    输出：投票后的 [(rect, label, avg_conf), ...]（按 x 排序）
    """

    def __init__(self, window: int = VOTE_WINDOW) -> None:
        self._frames: deque = deque(maxlen=window)
        # 缓存归并半径：依赖首帧的牌高估算
        self._merge_radius: Optional[float] = None

    def push(self, dets: List[Tuple[Tuple[int, int, int, int], Optional[str], float]]) -> None:
        self._frames.append(dets)
        # 用最近一帧估算牌高（取检测框高度的均值）作为归并半径参考
        if dets:
            hs = [d[0][3] for d in dets if d[0][3] > 0]
            if hs:
                self._merge_radius = max(20.0, sum(hs) / len(hs) * VOTE_MERGE_FRAC)

    def vote(self) -> List[Tuple[Tuple[int, int, int, int], Optional[str], float]]:
        if not self._frames:
            return []
        # 投票窗口不足（启动初期），直接用最后一帧的结果
        if len(self._frames) < 2:
            return list(self._frames[-1])

        rad = self._merge_radius or 40.0
        # 1. 把所有帧的同一位置的检测合并到同一组（用最后一帧的位置作为种子）
        # 2. 每组内统计 label 的票数
        # 3. 采纳票数 ≥ VOTE_MIN_FRAC * 帧数 的标签；若都不过半，取最末一帧
        all_dets = [d for frame in self._frames for d in frame]
        if not all_dets:
            return []

        # 用最后一帧的检测作为种子（最近的最相关）
        last = list(self._frames[-1])
        out: List[Tuple[Tuple[int, int, int, int], Optional[str], float]] = []
        used = [False] * len(all_dets)
        for (rect, _label, _conf) in last:
            cx, cy = rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0
            # 找所有帧里离这个种子近的检测
            matches: List[Tuple[Tuple[int, int, int, int], Optional[str], float]] = []
            for idx, (r2, l2, c2) in enumerate(all_dets):
                if used[idx]:
                    continue
                cx2 = r2[0] + r2[2] / 2.0
                cy2 = r2[1] + r2[3] / 2.0
                if abs(cx - cx2) < rad and abs(cy - cy2) < rad:
                    matches.append((r2, l2, c2))
                    used[idx] = True
            if not matches:
                continue
            # 统计 label 票数（label is None 不参与投票）
            counts: Dict[str, int] = {}
            confs_by_label: Dict[str, List[float]] = {}
            for (_r, l, c) in matches:
                if l is None:
                    continue
                counts[l] = counts.get(l, 0) + 1
                confs_by_label.setdefault(l, []).append(c)
            n_votes = sum(counts.values())
            n_frames = len(self._frames)
            chosen_label: Optional[str] = None
            if n_votes > 0:
                # 优先选票数最高；票数相同时选平均 conf 高的
                best_count = max(counts.values())
                if best_count >= VOTE_MIN_FRAC * n_frames:
                    cand = [l for l, cnt in counts.items() if cnt == best_count]
                    if len(cand) == 1:
                        chosen_label = cand[0]
                    else:
                        chosen_label = max(cand, key=lambda l: sum(confs_by_label[l]) / len(confs_by_label[l]))
            if chosen_label is None:
                # 都不过半，回退用最后一帧的 label
                chosen_label = matches[0][1]
                avg_conf = matches[0][2]
            else:
                avg_conf = sum(confs_by_label[chosen_label]) / len(confs_by_label[chosen_label])
            # 用最后一帧的 rect（位置最稳）；如果最后一帧这个位置 label 是 None，
            # 但投票选出了别的 label，仍用最后一帧的 rect（位置不变）
            out.append((matches[0][0], chosen_label, avg_conf))
        out.sort(key=lambda d: d[0][0])
        return out


def _error_result(status: str, message: str) -> "EngineResult":
    """构造错误状态的结果。

    以前这些场景（解码失败/识别异常）直接返回 None，Java 端会把整帧
    静默丢弃，悬浮窗永远转圈——用户看到的就是"没有任何反应"。
    现在把错误包装成正常的 EngineResult 发给界面，任何 Python 异常
    都能在悬浮窗上直接看到原因。
    """
    result = {
        "hand": "",
        "count": 0,
        "status": status,
        "shanten": None,
        "advice": [],
        "commentary": None,
        "tiles": [],
        "top_score": 0.0,
        "screen": [0, 0],
        "elapsed": 0.0,
        "message": str(message)[:200],
    }
    return EngineResult(
        image=np.zeros((1, 1, 3), dtype=np.uint8),
        result=json.dumps(result),
        stage=None,
    )


class Engine:
    def __init__(self):
        self.trainer: Optional[Trainer] = None
        # 结构识别器自带字形库，构建一次即可（无需每帧读模板图）。
        self._detector: Optional[StructuralDetector] = None
        # 推荐打法的缓存（按手牌内容），避免每帧重算 34 次向听 + 进张
        self._advice_key: Optional[str] = None
        self._advice: List[Dict] = []
        # 帧差去重：相邻帧几乎相同时跳过完整识别
        self._frame_skipper = _FrameSkipper()
        # 多帧投票：把最近 3 帧的同一位置检测做多数表决
        self._tile_voter = _TileVoter(window=VOTE_WINDOW)
        # 给主界面"知道什么时候画面没动"的提示用
        self._consecutive_skips: int = 0

    def start(self):
        pass

    def get_detector(self) -> Optional[StructuralDetector]:
        if self._detector is not None:
            return self._detector
        # 结构识别器自带字形库，无需外部模板图片，构建一次即可。
        self._detector = StructuralDetector()
        return self._detector

    def update_trainer(self, hand: TileCollection) -> Optional[str]:
        """根据最新一手牌更新训练器，返回对上一手的中文点评。"""
        # 只有当前玩法的合法手牌张数才是合法手牌。其它张数说明这一帧识别不完整，
        # 直接跳过，避免拿脏数据去点评（原实现在这里 assert，会中断整帧）。
        if len(hand) not in hand_sizes(self.mode):
            print(f"Hand length {len(hand)} is not valid for mode {self.mode}, skipping")
            return None

        if self.trainer is None:
            print(f"Initial hand: {hand}")
            self.trainer = Trainer(hand)
            return None

        prev_hand = self.trainer.hand
        if hand == prev_hand:
            return None

        diff = hand.get_difference(prev_hand)
        delta = sum(abs(x) for x in diff.values())

        if delta == 0:
            return None
        if delta > 1:
            print(f"Large change detected, reloading hand: {diff}")
            self.trainer = Trainer(hand)
            return None

        tile, change = list(diff.items())[0]

        if len(hand) == 13 and len(prev_hand) == 14 and change == -1:
            print(f"Discard detected: {tile}")
            return self.trainer.discard(tile)

        if len(hand) == 14 and len(prev_hand) == 13 and change == 1:
            print(f"Draw detected: {tile}")
            return self.trainer.draw(tile)

        # 张数变化不符合"摸牌/打牌"，多半是中途识别跳变，整手重建
        print(f"Unexpected transition {len(prev_hand)} -> {len(hand)}, reloading hand")
        self.trainer = Trainer(hand)
        return None

    def build_advice(self, hand: TileCollection, disc_counts=None):
        """返回 (向听数, 推荐打法列表)。

        向听数每帧都算（单次开销很小），保证界面上一直有反馈；
        推荐打法（绝张感知进张）改为按「手牌 + 牌河可见计数」缓存，
        只有手牌或牌河变了才重算。disc_counts 会刷新到 trainer，使进张按绝张扣减。

        - 14 张：摸到牌后的最优出牌（"打 X → 进张 N 张"）。
        - 13 张：等摸任意牌时的最优出牌（"打 X → 摸到 Z 时进张最多"）。
        """
        if self.trainer is None:
            return None, []

        if disc_counts is not None:
            self.trainer.set_visible(disc_counts, [0] * 34)

        shanten = self.trainer.get_shanten()

        # 缓存键含牌河可见计数（牌河只增不减，变化即重算）；用 hash 压缩长度。
        key = f"{hand}|{shanten}|{hash(tuple(self.trainer.disc_counts))}"
        if key == self._advice_key:
            return shanten, self._advice

        advice: List[Dict] = []
        try:
            if len(hand) in hand_sizes(self.mode):
                raw = self.trainer.calculate_discards()
                advice = [
                    {"tile": str(t), "ukeire": int(u)}
                    for t, u in sorted(raw.items(), key=lambda kv: -kv[1])
                ][:6]
        except Exception:
            traceback.print_exc()

        self._advice_key = key
        self._advice = advice
        return shanten, advice

    def process_bytes(self, image_data) -> Optional[EngineResult]:
        try:
            arr = _to_uint8_buffer(image_data)
            image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if image is None:
                print("Failed to decode image")
                return _error_result("decode_error", "图像解码失败（空字节或坏JPEG）")
            return self.process(image)
        except Exception as e:
            traceback.print_exc()
            return _error_result("py_error", f"process_bytes 异常: {e}")

    def _check_dup_explosion(self, mpsz: str) -> bool:
        """互斥校验：返回 True 表示这手牌"同字刷屏"，必是识别错乱。

        麻将合法牌型里最多 4 张同字（4 张相同叫"四暗刻/四同刻"，极罕见）。
        实战中识别错乱最常见的形式是：筒/索背景浅+图案稀疏 → 被结构识别器当成
        "白板"（5z），一刷刷好几张。同一帧 mpsz 里出现 ≥5 张同字必是误判。
        """
        if not mpsz:
            return False
        tiles = []
        for i in range(0, len(mpsz), 2):
            if i + 2 <= len(mpsz):
                tiles.append(mpsz[i:i + 2])
        counts: Dict[str, int] = {}
        for t in tiles:
            counts[t] = counts.get(t, 0) + 1
        return any(c > MAX_DUP_PER_TILE for c in counts.values())

    def _build_skip_result(self, image: CVImage, prev_payload: str) -> EngineResult:
        """画面无变化时复用上一帧结果，但保持 EngineResult 的合约。"""
        try:
            data = json.loads(prev_payload)
        except Exception:
            return _error_result("py_error", "上一帧结果反序列化失败")
        # 复用但标记一下"这一帧没真的识别"
        data["status"] = data.get("status") or "ok"
        data["frame_skipped"] = True
        return EngineResult(
            image=_make_preview(image),
            result=json.dumps(data),
            stage=None,
        )

    # ---------------------------------------------------------- 引擎侧辅助

    def _apply_conf(self, rect, label, conf):
        """置信 + 牌形比例过滤：低于门槛或牌形不对的牌直接判为「不识别」，
        宁可漏识别也绝不臆测（白板刷屏、半张牌等假命中在此被挡掉）。"""
        if label is None:
            return None
        if conf < ENGINE_MIN_CONF:
            return None
        w, h = rect[2], rect[3]
        if h <= 0:
            return None
        aspect = w / float(h)
        if aspect < MIN_TILE_ASPECT or aspect > MAX_TILE_ASPECT:
            return None
        return label

    @staticmethod
    def _labels_to_mpsz(labels, avail) -> str:
        """把标签列表拼成 mpsz 串，只保留当前玩法可用牌（如三麻的白直接丢弃）。"""
        out = []
        for lab in labels:
            if lab is None:
                continue
            try:
                idx = mpsz_to_tile34_index(lab)
            except Exception:
                continue
            if idx in avail:
                out.append(lab)
        return "".join(out)

    @staticmethod
    def _pick_hand_row(rows):
        """从所有牌行里挑出「自己手牌行」。

        通用启发：手牌行是玩家面前最近的一排，牌最大（离镜头最近）、
        通常位于屏幕最底部，张数接近 13/14。各家的牌河/副露行牌更小、
        更靠上。因此优先取「平均牌高最大」的行；牌高相近时用「更靠下」
        和「张数更接近 13/14」做 tiebreak。

        返回该行（list），供调用方区分 hand vs discard；找不到则返回 None。
        """
        if not rows:
            return None
        best = None
        best_key = (-1.0, -1.0, -1.0)  # (平均牌高, 行中心 y, 张数接近度)
        for row in rows:
            if len(row) < 8:
                continue
            hs = [d[0][3] for d in row if d[0][3] > 0]
            if not hs:
                continue
            avg_h = sum(hs) / len(hs)
            ys = [d[0][1] for d in row]
            yc = sum(ys) / len(ys)
            len_ok = 1.0 - min(abs(len(row) - 13), abs(len(row) - 14)) / 13.0
            key = (avg_h, yc, len_ok)
            if key > best_key:
                best_key = key
                best = row
        if best is not None:
            return best
        # 兜底：选张数最接近 13 的行
        return min(rows, key=lambda g: min(abs(len(g) - 13), abs(len(g) - 14)))

    def process(self, image: CVImage) -> Optional[EngineResult]:
        try:
            start_time = time.time()

            # ===== 帧差去重 =====
            # 工作区域（与识别器同一份降采样逻辑）作为帧差基线。
            # 这里用纯 numpy 算一个粗签名，代价 ≈ 1ms，比一次完整识别快 100x。
            try:
                ih, iw = image.shape[:2]
                longest = float(max(ih, iw))
                if longest > 1100:
                    inv = longest / 1100.0
                    work_gray = cv2.resize(
                        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image,
                        (max(1, int(iw / inv)), max(1, int(ih / inv))),
                        interpolation=cv2.INTER_AREA,
                    )
                else:
                    work_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
                diff = self._frame_skipper.diff(work_gray)
            except Exception:
                diff = float("inf")  # 帧差算不出来就当不动也跑完整识别

            if diff < FRAME_DIFF_THRESHOLD and self._frame_skipper.cached is not None:
                self._consecutive_skips += 1
                return self._build_skip_result(image, self._frame_skipper.cached)
            self._consecutive_skips = 0

            # 每帧读取当前玩法（文件共享态，由悬浮窗写入）。切换玩法后无需重启引擎。
            self.mode = load_mode()
            avail = available_set(self.mode)
            hsizes = hand_sizes(self.mode)

            detector = self.get_detector()
            if detector is None:
                print("No templates available")
                return _error_result("py_error", "识别器初始化失败（模板库为空）")

            # 取「所有牌行」（含各家牌河），不再只取手牌行。
            rows = detector.detect_all_rows(image)

            # 置信过滤 + 牌形降权（低置信牌直接丢弃，宁可不识别也不臆测）。
            filtered = []
            for row in rows:
                fr = [(r, self._apply_conf(r, l, c), c) for (r, l, c) in row]
                filtered.append(fr)

            # 多帧投票：把所有牌（含牌河）按位置投入投票窗口，挡掉单帧抖动
            # （尤其筒/索被误判成白板的刷屏）。跨行 y 差距大不会串。
            flat = [(r, l, c) for row in filtered for (r, l, c) in row]
            try:
                self._tile_voter.push(flat)
                voted = self._tile_voter.vote()
            except Exception:
                traceback.print_exc()
                voted = flat
            voted_rows = StructuralDetector._group_rows(voted)

            # 区分手牌行与牌河行
            hand_row = self._pick_hand_row(voted_rows)
            discard_labels = []
            for row in voted_rows:
                if row is hand_row:
                    continue
                for (rect, label, conf) in row:
                    if label is not None:
                        discard_labels.append(label)
            disc_mpsz = self._labels_to_mpsz(discard_labels, avail)
            disc_counts = [0] * 34
            for lab in discard_labels:
                try:
                    disc_counts[mpsz_to_tile34_index(lab)] += 1
                except Exception:
                    pass

            # 手牌
            hand_mpsz = ""
            hand = None
            if hand_row is not None:
                hand_labels = [d[1] for d in hand_row if d[1] is not None]
                hand_mpsz = self._labels_to_mpsz(hand_labels, avail)
                if hand_mpsz:
                    hand = TileCollection.from_mpsz(hand_mpsz)

            status = "no_tiles"
            commentary: Optional[str] = None
            shanten: Optional[int] = None
            advice: List[Dict] = []
            best: str = ""
            tile_count = 0

            if hand is not None:
                tile_count = len(hand)
                if tile_count in hsizes:
                    # 互斥校验：同字 ≥5 张必是误判（如白板刷屏）。
                    # 把这一帧降级为 incomplete，等下一帧重识别。
                    if self._check_dup_explosion(hand_mpsz):
                        status = "incomplete"
                    else:
                        status = "ok"
                        commentary = self.update_trainer(hand)
                        shanten, advice = self.build_advice(hand, disc_counts)
                else:
                    status = "incomplete"

            # 标记"最优"那张牌（最高 ukeire），UI 上加"最优"角标
            if advice:
                top_ukeire = max((a.get('ukeire') or 0) for a in advice)
                for a in advice:
                    if (a.get('ukeire') or 0) == top_ukeire:
                        best = str(a.get('tile') or "")
                        break

            # ---- 剩余牌 / 绝张统计（基于当前玩法的可见域）----
            # 可见域 = 自己手牌 + 牌河所有打出的牌（副露未知，按 0 计）。
            # 墙内剩余 = 该玩法总牌数 - 可见；绝张 = 某型 4 张已全部可见，
            # 这种牌既不可能摸到、也不该被推荐打出（进张已为 0）。
            hand_counts = [0] * 34
            for i in range(0, len(hand_mpsz), 2):
                try:
                    hand_counts[mpsz_to_tile34_index(hand_mpsz[i:i + 2])] += 1
                except Exception:
                    pass
            avail_list = sorted(avail)
            known = sum(hand_counts[i] for i in avail_list) + sum(disc_counts[i] for i in avail_list)
            wall_total = len(avail_list) * 4
            remaining = max(0, wall_total - known)
            dead = sum(1 for i in avail_list if hand_counts[i] + disc_counts[i] >= 4)

            # 区分每行的角色（手牌行 vs 牌河行），供 UI 渲染与调试。
            rows_out = []
            for row in voted_rows:
                kind = "hand" if row is hand_row else "discard"
                rows_out.append((kind, row))
            all_tiles = [
                [int(v) for v in rect] + [label if label is not None else ""] + [kind]
                for kind, row in rows_out
                for (rect, label, _conf) in row
            ]

            result = {
                "mode": self.mode,
                "mode_name": MODES.get(self.mode, {}).get("name", self.mode),
                "hand": hand_mpsz,
                "count": tile_count,
                "status": status,
                "shanten": shanten,
                "advice": advice,
                "best": best,
                "commentary": commentary,
                "discards": disc_mpsz,
                "discard_count": len(discard_labels),
                "remaining": remaining,
                "dead": dead,
                "tiles": all_tiles,
                # 最近一帧的最高模板匹配分（无论是否过阈）。
                "top_score": round(float(getattr(detector, "last_top_score", 0.0)), 3),
                "glyphs": len(getattr(detector._glyphs, "nums", {}) or {}),
                "styles": len(getattr(detector._styles, "tpls", []) or []),
                "screen": [
                    int(getattr(detector, "last_screen", (0, 0))[0]),
                    int(getattr(detector, "last_screen", (0, 0))[1]),
                ],
                "elapsed": round(time.time() - start_time, 3),
                "frame_skipped": False,
            }

            payload = json.dumps(result)
            self._frame_skipper.remember(payload, result["top_score"])

            res = EngineResult(
                image=_make_preview(image),
                result=payload,
                stage=None,
            )
            print(result)
            print(f"Processed in {time.time() - start_time}")
            return res
        except Exception as e:
            traceback.print_exc()
            return _error_result("py_error", f"process 异常: {e}")
