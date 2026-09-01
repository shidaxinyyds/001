from __future__ import annotations
import hashlib
import traceback
from collections import Counter, deque
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
    WARMUP_FRAMES,
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
# 调高到 0.55：漏掉的牌由多帧投票补回（要求 4 帧里 ≥3 帧同标签才采纳），
# 但单帧的"白板刷屏"（筒/索背景浅被错认字牌）会显著减少。
ENGINE_MIN_CONF = 0.55

# 同帧互斥上限：手牌里同种牌最多 4 张（4 张相同的合法牌型）。
# 同一帧 mpsz 里出现 ≥MAX_DUP_PER_TILE+1 张同字必是误判，整手拒绝。
MAX_DUP_PER_TILE = 4

# 帧差阈值：相邻两帧的工作区域分块均值差的 L1 范数（256 个 8x8 块、每块均值 0~255）。
# 原来 1.5 过低——吃碰杠瞬间仍被判为"不变"复用旧手牌，导致用户看到的牌和实际不符。
# 取 5.0：完全静止画面 = 0~3；出一张牌 = 20~60；菜单弹出/动画 = 80+。
FRAME_DIFF_THRESHOLD = 5.0

# 帧差块边长（像素，工作分辨率上）。32 块 = 256 块覆盖整屏，约每块 12x12 work px。
FRAME_DIFF_BLOCK = 32

# 突变帧检测：本帧 diff 与最近 N 帧 diff 均值的比值，若超过该倍数视为"动画中"
# （如吃碰杠的牌移动动画），整帧丢弃（不做投票也不出结果）。
MOTION_SPIKE_RATIO = 3.0
MOTION_HISTORY = 5  # 保留最近多少个 diff 用于求均值

# 多帧投票窗口：保留最近 N 帧的检测结果，按位置投票得到稳定标签。
# 4 帧够稳：3 帧过半数即可敲定，剩 1 帧做加权"新近偏置"。
VOTE_WINDOW = 4

# 同位置归并半径（work 坐标）。相邻两张牌的中心距 ≈ 牌宽 ≈ tile_h；
# 归并半径取 0.45 * tile_h 让"同一张牌的位置"被并成一组。
VOTE_MERGE_FRAC = 0.45

# 位置投票最低票数：占总票数 ≥ 该比例的标签才采纳。
VOTE_MIN_FRAC = 0.55

# 单位置最低票数（绝对值）：必须 ≥VOTE_MIN_VOTES 票才采纳；
# 否则该位置输出 None（视为未识别）——这是"一下有一下没有"的根因，
# 原实现在投票不过半时回退到最后一帧的 label，结果就是单帧抖动直接污染输出。
VOTE_MIN_VOTES = 2

# 最近一帧权重比例：越近的帧在投票里权重越高，避免旧帧的位置"残留"。
VOTE_RECENCY_WEIGHTS = (0.5, 0.7, 0.9, 1.0)  # 长度与 VOTE_WINDOW 一致

# 手牌行 y 锁定容差（work 坐标）：上一帧挑了 yc，下一帧优先在 ±band 范围内挑。
# 不锁会因帧间小抖动把"牌河行"和"手牌行"互相切换——这正是 13↔14 跳变的根因之一。
HAND_LOCK_BAND = 30

# 引擎侧的"低置信回退"门槛：单帧低于此门槛的牌本来直接被 _apply_conf 丢弃
# （避免白板/伪命中污染）；但当一帧的"识别出牌数"比稳定手牌少 1 张且差异稳定时，
# 引擎允许以这条更低的门槛再做一次"补漏"扫描，把漏掉的那张牌补回来。
# 仅在已建立稳定手牌后启用——冷启动期（无 _stable_hand_mpsz）仍走严格门槛，
# 避免启动时把噪音当真。
ENGINE_MIN_CONF_RELAX = 0.42

# 牌河稳定性兜底：避免识别器瞬时漏抓牌河中的几张牌，导致 remaining/dead
# 当帧剧烈变化。追踪最近 N 帧的 disc_mpsz 长度与 mpsz 增量；当前帧若
# 显著少于历史最小值（差距 ≥MIN_DROP_DELTA），回退到历史最大稳定值。
DISCARD_HISTORY_FRAMES = 6
DISCARD_HISTORY_MIN_SAMPLES = 3
MIN_DROP_DELTA = 4


def _hand_diff_count(a: str, b: str) -> int:
    """两个 mpsz 串之间的"牌数差异"：把 a 变成 b 最少需改动的张数。

    实现：对 a/b 都按 tile 计数，对应位置做 |cnt_a - cnt_b| 求和再除 2
    （每个差代表改一张，向上取整）。
    例：a="1m2m3m" → {'1m':1,'2m':1,'3m':1}；b="1m2m3p" → {'1m':1,'2m':1,'3p':1}；
    diff = |1-0|+|1-0|+|0-1| = 3 → 3//2 = 1（1 张牌被改了）。
    这种棋盘级最小变换量是判断"这次识别是不是大改"的最稳指标。
    """
    ca = Counter([a[i:i + 2] for i in range(0, len(a), 2)] if a else [])
    cb = Counter([b[i:i + 2] for i in range(0, len(b), 2)] if b else [])
    diff = 0
    for t in set(ca.keys()) | set(cb.keys()):
        diff += abs(ca.get(t, 0) - cb.get(t, 0))
    return (diff + 1) // 2


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


class _MotionGuard:
    """动画突变帧检测。

    牌局里：吃碰杠动作会有 200~400ms 的"牌从手牌跑到牌河"过渡帧，整张图块变化剧烈；
    菜单弹出/关闭、聊天框显隐也类似。这种帧截下来做识别会得出"半成品"——
    比如你的手牌少 1 张、牌河多 1 张的中间状态。如果把这帧结果正常输出，
    悬浮窗会立刻把"丢牌"判定为你打了一张，把 trainer 弄乱。

    做法：维护一个最近 diff 的滑动均值；本帧 diff > 均值 * MOTION_SPIKE_RATIO
    时直接判定为"动画中"，丢弃整帧。
    """

    def __init__(self) -> None:
        self._history: deque = deque(maxlen=MOTION_HISTORY)

    def is_spike(self, diff: float) -> bool:
        # 首帧无历史，绝不算 spike，正常识别。
        if len(self._history) == 0:
            self._history.append(diff)
            return False
        avg = sum(self._history) / len(self._history)
        self._history.append(diff)
        # "静止帧" diff 极小（约 0~3），它的均值会被拉低很多，比较时把它去掉；
        # 否则每一次画面静止都会"训练"出一个极低均值，下一帧稍动就触发误判。
        non_static = [d for d in self._history if d >= 0.5]
        if non_static:
            avg = sum(non_static) / len(non_static)
        return diff > max(8.0, avg * MOTION_SPIKE_RATIO)


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
    """多帧投票：把最近 N 帧的识别结果按 (y, x) 位置归并、按时间加权投票得到稳定标签。

    解决单帧识别里最头疼的两个问题：
      1) "筒/索被认成白板"——同位置 2~3 帧都认白板的概率几乎为 0，投票后被真实标签覆盖；
      2) "一下有一下没有"——投票不过半时，**不再回退到最后一帧的 label**，
         而是输出 None。下游 UI 把它当成"未识别"，表现就是出现一次闪烁后立刻稳定。

    加权：越近的帧权重越大（VOTE_RECENCY_WEIGHTS 从旧到新递增）。

    输入：每帧 detect 出的 [(rect, label, conf), ...]
    输出：投票后的 [(rect, label|None, avg_conf), ...]（按 x 排序）
    """

    def __init__(self, window: int = VOTE_WINDOW) -> None:
        self._frames: deque = deque(maxlen=window)
        # 缓存归并半径：依赖首帧的牌高估算
        self._merge_radius: Optional[float] = None

    def push(self, dets: List[Tuple[Tuple[int, int, int, int], Optional[str], float]]) -> None:
        # 只保留窗口大小；老帧自然被挤掉
        self._frames.append(dets)
        # 用最近一帧估算牌高（取检测框高度的均值）作为归并半径参考
        if dets:
            hs = [d[0][3] for d in dets if d[0][3] > 0]
            if hs:
                self._merge_radius = max(20.0, sum(hs) / len(hs) * VOTE_MERGE_FRAC)

    def reset(self) -> None:
        """玩法突变 / 大场景切换时硬重置，清空投票窗口。

        不这样做的话：4 帧前手牌是 1m2m3m，模式突变之后画面里的位置完全不一样了，
        老窗口里那 4 帧的"旧位置"会继续和最新帧的"新位置"归并投票，输出混乱。
        """
        self._frames.clear()
        self._merge_radius = None

    def vote(self) -> List[Tuple[Tuple[int, int, int, int], Optional[str], float]]:
        if not self._frames:
            return []
        # 单帧起步：直接透传（没有"投票"可言）。但**仍然**过滤 None label 的不稳定牌——
        # 启动第一帧不该被信任。
        if len(self._frames) < 2:
            return [(r, l, c) for (r, l, c) in self._frames[-1] if l is not None]

        rad = self._merge_radius or 40.0

        # 把每帧带"帧索引 + 权重"展开。weights[i] 对应第 i 帧（从旧到新），
        # VOTE_RECENCY_WEIGHTS 同样按从旧到新递增。
        weights = VOTE_RECENCY_WEIGHTS
        # 长度不匹配时（理论上不会发生），用 1.0 兜底
        if len(weights) != len(self._frames):
            weights = tuple(1.0 for _ in self._frames)

        # 用最后一帧的检测作为种子（位置最稳）。
        last = list(self._frames[-1])
        out: List[Tuple[Tuple[int, int, int, int], Optional[str], float]] = []
        n_frames = len(self._frames)
        for (rect, last_label, _last_conf) in last:
            cx, cy = rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0
            # 归并：每个 (frame, cx, cy) 只计一次。如果两个 detection 落得极近，
            # 取先看到的那个（后面遇到再被 round 取整就被并掉）。
            matches_w: List[Tuple[float, str, float]] = []
            seen: set = set()
            for fi, (w, frame) in enumerate(zip(weights, self._frames)):
                for d in frame:
                    r2, l2, c2 = d
                    cx2 = r2[0] + r2[2] / 2.0
                    cy2 = r2[1] + r2[3] / 2.0
                    if abs(cx - cx2) >= rad or abs(cy - cy2) >= rad:
                        continue
                    pos_key = (fi, round(cx2, 1), round(cy2, 1))
                    if pos_key in seen:
                        continue
                    seen.add(pos_key)
                    if l2 is not None:
                        matches_w.append((w, l2, c2))

            if not matches_w:
                # 窗口内没有任何 label（说明新位置刚出现）——以前投票不过半也会回退到
                # last 的 label，那是跳变的根因。现在返回 None，让该位置当作"未识别"，
                # UI 自然会保持上次的状态。
                out.append((rect, None, 0.0))
                continue

            # 按 label 求加权和
            label_weight: Dict[str, float] = {}
            label_conf: Dict[str, List[float]] = {}
            for (w, l, c) in matches_w:
                label_weight[l] = label_weight.get(l, 0.0) + w
                label_conf.setdefault(l, []).append(c)
            n_total_w = sum(label_weight.values())
            best_l = max(label_weight.items(), key=lambda kv: kv[1])
            best_lab, best_w = best_l[0], best_l[1]
            # 采纳条件（全部满足才认，否则输出 None）：
            #   - 加权份额 ≥ VOTE_MIN_FRAC
            #   - 加权票数 ≥ VOTE_MIN_VOTES
            # 另：种子（最后一帧）的 label 必须与 best_lab 一致——
            #     否则意味着"最新帧刚换了"，投票还没稳，宁可不输出。
            #     但如果 last_label 是 None（新位置刚出现/单帧漏检），宽容：
            #     只要历史加权份额达标就认，否则永远无法起步。
            last_vote_w = label_weight.get(last_label, 0.0) if last_label else 0.0
            ok_share = (best_w / n_total_w) >= VOTE_MIN_FRAC
            ok_abs = label_weight[best_lab] >= VOTE_MIN_VOTES
            if last_label is None:
                # last 没识别，依靠历史加权；放宽要求，但不能松到让单帧噪声混入
                chosen: Optional[str] = best_lab if (ok_share and ok_abs) else None
            else:
                ok_last = (last_label == best_lab and last_vote_w > 0.0)
                chosen = best_lab if (ok_share and ok_abs and ok_last) else None
            avg_conf = (
                sum(label_conf[best_lab]) / len(label_conf[best_lab])
                if best_lab in label_conf else 0.0
            )
            out.append((rect, chosen, avg_conf))
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


def _is_valid_image(img) -> bool:
    """纯 numpy 校验图像结构性合法，绝不调用 cv2。

    OpenCV 的 C 层在遇到畸形/空/坏 dtype 的 ndarray 时会直接 SIGSEGV，
    这种崩溃无法被 Python 的 try/except 捕获，进程表现为"闪退"。
    因此在进入任何 cv2 操作之前，用纯 numpy 把坏数据拦截成
    _error_result("decode_error")，从根上消除 C 层崩溃闪退。
    """
    try:
        if img is None:
            return False
        if not isinstance(img, np.ndarray):
            return False
        if img.ndim not in (2, 3):
            return False
        if img.dtype != np.uint8:
            return False
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return False
        if img.size == 0 or img.nbytes == 0:
            return False
        return True
    except Exception:
        return False


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
        # 动画突变帧（吃碰杠的牌移动帧、菜单弹出帧）：整帧丢弃
        self._motion_guard = _MotionGuard()
        # 多帧投票：把最近 4 帧的同一位置检测做加权多数表决
        self._tile_voter = _TileVoter(window=VOTE_WINDOW)
        # 行锁定：上一帧手牌行的 y 坐标，下一帧在 [y - HAND_LOCK_BAND, y + HAND_LOCK_BAND]
        # 范围内挑，避免 13↔14 跳变（手牌行被牌河/记分行抢走）的根因
        self._last_hand_y: Optional[float] = None
        # 最近一次"稳定"的手牌 mpsz 与张数：用于本帧识别失败/可疑时做兜底
        self._stable_hand_mpsz: str = ""
        self._stable_hand_count: int = 0
        # 当前模式（process() 每帧 reload，对比是否变了）
        self.mode: str = "4p"
        self._prev_mode: str = self.mode
        # 给主界面"知道什么时候画面没动"的提示用
        self._consecutive_skips: int = 0
        # 启动帧计数：首 WARMUP_FRAMES 帧走保守策略（参见 WARMUP_FRAMES 注释），
        # 避免 _MotionGuard 历史为空导致动画过渡帧漏过。
        self._warmup_left: int = WARMUP_FRAMES
        # 牌河稳定性历史：保留最近 DISCARD_HISTORY_FRAMES 帧的 disc_mpsz 长度。
        # 用于"当前帧 discards 显著少于历史最小值"时回退到历史最大稳定值。
        self._discard_history: deque = deque(maxlen=DISCARD_HISTORY_FRAMES)

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
            # 双保险：imdecode 偶尔会对坏数据返回非 None 但尺寸/ dtype 异常的对象，
            # 进入 cv2 前再用纯 numpy 校验一次，避免 C 层段错误闪退。
            if not _is_valid_image(image):
                return _error_result("decode_error", "解码成功但图像数据非法，已安全跳过")
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
        宁可漏识别也绝不臆测（白板刷屏、半张牌等假命中在此被挡掉）。

        双门槛机制：
          - 严格门槛 ENGINE_MIN_CONF（0.55）：默认走这条，防白板/伪命中。
          - 放宽门槛 ENGINE_MIN_CONF_RELAX（0.42）：仅当引擎已建立稳定手牌
            （self._stable_hand_mpsz 非空）时才允许。这是"漏 1 张 → 自动补漏"
            的关键：若投票窗口里有 2~4 张牌稳定为 Xm，但第 N 张本来被投票器
            因 0.50 分拒了，会导致手牌数对（13/14 张）但实际少识别了一张。
            放宽门槛只在"补漏"时启用——启动期仍走严格门槛，避免噪声被当真。
        """
        if label is None:
            return None
        w, h = rect[2], rect[3]
        if h <= 0:
            return None
        aspect = w / float(h)
        if aspect < MIN_TILE_ASPECT or aspect > MAX_TILE_ASPECT:
            return None
        if conf >= ENGINE_MIN_CONF:
            return label
        # 已建立稳定手牌 + 严格门槛不过 + 放宽门槛过 → 允许（但仅一次）
        if conf >= ENGINE_MIN_CONF_RELAX and self._stable_hand_mpsz:
            return label
        return None

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
    def _hand_row_score(row):
        """返回 (avg_h, y_center, len_score) 三元组，越大越像"手牌行"。

        评判维度：
          - 平均牌高更大（手牌是连续拍摄、单张牌大）
          - y 坐标更靠下（屏幕坐标系原点在左上角，y 越大越靠下）
          - 行长度接近 13/14（手牌行专属；牌河一般 0~12 张）
        长度 <8 直接返回 None（不太可能是手牌行）。
        """
        if len(row) < 8:
            return None
        hs = [d[0][3] for d in row if d[0][3] > 0]
        if not hs:
            return None
        avg_h = sum(hs) / len(hs)
        ys = [d[0][1] for d in row]
        yc = sum(ys) / len(ys)
        # len_score: 13 张 = 1.0，14 张 = 0.99（都算高分），其它线性衰减
        best13 = 1.0 - min(abs(len(row) - 13), abs(len(row) - 14)) / 13.0
        return (avg_h, yc, best13)

    def _pick_hand_row(self, rows):
        """从所有牌行里挑出「自己手牌行」。

        通用启发（原有）：手牌行是玩家面前最近的一排，牌最大、靠下、张数接近 13/14。
        新增（这一轮）：**行锁定**。优先保留上一帧挑中的 y（±HAND_LOCK_BAND），挡掉
        "手牌行被牌河/记分行临时抢走"造成的 13↔14 跳变。
        """
        if not rows:
            return None
        # 1) 行锁定：如果上一帧挑了 y，先看本帧是否有落在 [y-band, y+band] 内且长度合理的候选
        lock_y = self._last_hand_y
        if lock_y is not None:
            band = HAND_LOCK_BAND
            candidates = []
            for row in rows:
                s = self._hand_row_score(row)
                if s is None:
                    continue
                ys = [d[0][1] for d in row]
                yc = sum(ys) / len(ys)
                if abs(yc - lock_y) <= band:
                    candidates.append((s, row, yc))
            if candidates:
                # 同分数时优先选 y 更接近 lock 的（再保险）
                candidates.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2],
                                              abs(x[2] - lock_y)))
                chosen = candidates[0][1]
                self._last_hand_y = sum(d[0][1] for d in chosen) / len(chosen)
                return chosen

        # 2) 兜底：所有行里选最佳
        scored = []
        for row in rows:
            s = self._hand_row_score(row)
            if s is not None:
                ys = [d[0][1] for d in row]
                yc = sum(ys) / len(ys)
                scored.append((s, row, yc))
        if not scored:
            return min(rows, key=lambda g: min(abs(len(g) - 13), abs(len(g) - 14)))
        scored.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2]))
        chosen = scored[0][1]
        self._last_hand_y = sum(d[0][1] for d in chosen) / len(chosen)
        return chosen

    def process(self, image: CVImage) -> Optional[EngineResult]:
        try:
            # ===== 崩溃兜底（C 层 SIGSEGV 不可被 Python try/except 捕获）=====
            # 在进入任何 cv2 操作前，用纯 numpy 校验图像结构性合法。
            # 畸形/空/坏 dtype 的图像会让 OpenCV 底层直接段错误导致进程闪退，
            # 必须在 cv2 触碰它之前拦截为 _error_result。
            if not _is_valid_image(image):
                return _error_result("decode_error",
                                     "图像数据非法（空/坏尺寸/坏 dtype），已安全跳过")

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

            # ===== 动画突变帧检测（吃碰杠过渡帧 / 菜单弹出帧） =====
            # 整帧丢弃，等下一帧稳定画面。直接复用上次缓存的 payload（避免 UI 抽搐）。
            # 启动期（前 WARMUP_FRAMES 帧）禁用突变检测：_MotionGuard._history 为空，
            # 首帧必然被判定为非 spike——但首帧有可能是吃碰杠动画刚开始的瞬间，
            # 会污染投票窗口。冷启动期一律走完整识别、把投票窗口灌满再说。
            if self._warmup_left <= 0 and self._motion_guard.is_spike(diff):
                self._consecutive_skips += 1
                if self._frame_skipper.cached is not None:
                    return self._build_skip_result(image, self._frame_skipper.cached)
                # 没有任何历史稳定结果可复用，必须发一个明确的"等待"信号给 UI，
                # 不能让 UI 看到旧的 hand/advice（场景可能完全变了）
                self._tile_voter.reset()
                return _error_result("animation",
                                     "画面突变中（动画/菜单），等下一帧稳定再识别")

            # ===== 玩法切换硬重置 =====
            # 模式变了 → 投票窗口 / 行锁 / 缓存全部失效，必须清空重建。
            self.mode = load_mode()
            if self.mode != self._prev_mode:
                self._tile_voter.reset()
                self._last_hand_y = None
                self._stable_hand_mpsz = ""
                self._stable_hand_count = 0
                self._advice_key = None
                self._frame_skipper = _FrameSkipper()  # 旧缓存与新玩法无关
                self._prev_mode = self.mode
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

            # ===== TFLite 二次确认（可选、可热更、未来 hook）=====
            # 在 structural.py 几何判定后、投票前，对每张已通过 _apply_conf 的牌
            # 再用 TFLite 模型"看一眼"。**TFLite 任何故障都静默 fallback**：
            #   - 模型未部署（is_available()=False）→ 整段跳过，behavior 退化为纯 structural
            #   - predict 异常/超时/None → 该牌保留 structural 标签
            #   - tflite conf < HIGH_CONF → 不覆盖，保留 structural
            # 仅当 tflite conf ≥ HIGH_CONF（极高置信度）才覆盖 structural 的标签，
            # 防止低 acc 模型污染识别。
            # 当前 v3 模型（272 张/34 类）acc ≈14%、top-1 conf ≤0.09，远低于门槛，
            # **本轮对线上识别 0 贡献**——但代码完整，未来 fine-tune 提升 acc 后自动启用。
            try:
                from recognition import tflite_classifier  # noqa: E402
                HIGH_CONF = 0.70
                if tflite_classifier.is_available():
                    crops: list = []
                    crop_locs: list = []  # (row_idx, det_idx)
                    for ri, row in enumerate(filtered):
                        for di, (r, l, c) in enumerate(row):
                            if l is None:
                                continue
                            x, y, w, h = r
                            if h <= 0 or w <= 0:
                                continue
                            crop = image[y:y + h, x:x + w]
                            if crop.size == 0:
                                continue
                            crops.append(crop)
                            crop_locs.append((ri, di))
                    if crops:
                        preds = tflite_classifier.predict_batch(crops)
                        n_overridden = 0
                        for (ri, di), p in zip(crop_locs, preds):
                            if p is None:
                                continue
                            if p["confidence"] < HIGH_CONF:
                                continue
                            r, l_orig, c_orig = filtered[ri][di]
                            if p["tile"] != l_orig:
                                # 用 tflite 高置信覆盖；同时提升 conf，避免被
                                # 后续 _apply_conf 二次过滤掉
                                filtered[ri][di] = (r, p["tile"],
                                                     max(c_orig, p["confidence"]))
                                n_overridden += 1
                        if n_overridden:
                            print(f"[engine] tflite 高置信覆盖 {n_overridden} 张")
            except Exception as e:  # noqa: BLE001
                # 任何导入/调用异常 → 静默降级，不影响主流程
                pass

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
                        # 行级稳定性兜底：本帧 hand_mpsz 与上一次稳定手牌张数相同
                        # 但差异 ≥3 张——典型"识别跳变"（帧间某张被误改了 label）。
                        # 这种本帧降级为 incomplete 并沿用上次 stable_hand，避免
                        # UI 出现一闪而过的错误手牌。
                        diff_with_stable = -1
                        if (self._stable_hand_mpsz
                                and self._stable_hand_count == tile_count):
                            diff_with_stable = _hand_diff_count(
                                self._stable_hand_mpsz, hand_mpsz)
                        stable_threshold = max(2, tile_count // 4)
                        if (diff_with_stable >= 0
                                and diff_with_stable > stable_threshold):
                            status = "incomplete"
                        else:
                            status = "ok"
                            commentary = self.update_trainer(hand)
                            shanten, advice = self.build_advice(hand, disc_counts)
                            self._stable_hand_mpsz = hand_mpsz
                            self._stable_hand_count = tile_count
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

            # ===== 牌河稳定性兜底 =====
            # 牌河瞬时漏抓（吃碰杠时对方刚打出的牌被动画遮挡、动画未结束）会让
            # discards 长度瞬间掉一截，remaining/dead 当帧剧烈变化，UI 闪烁。
            # 解决：保留最近 DISCARD_HISTORY_FRAMES 帧的 disc_mpsz 长度；若当前帧
            # 显著少于历史最大值（差 ≥MIN_DROP_DELTA，且历史样本够多），把 discards
            # 回退到历史最大稳定值（同样按 tile 计数填充 disc_counts）。
            current_disc_len = len(disc_mpsz) // 2
            history_lens = [len(s) // 2 for s in self._discard_history]
            disc_mpsz_out = disc_mpsz
            disc_counts_out = disc_counts
            discarded_labels_out = discard_labels
            if (len(history_lens) >= DISCARD_HISTORY_MIN_SAMPLES
                    and history_lens and current_disc_len > 0):
                max_hist = max(history_lens)
                # 牌河只增不减；若当前帧少于历史最大值 ≥MIN_DROP_DELTA，视为漏抓
                if max_hist - current_disc_len >= MIN_DROP_DELTA and max_hist > current_disc_len:
                    # 选最长历史 disc_mpsz
                    best_idx = history_lens.index(max_hist)
                    stable_mpsz = self._discard_history[best_idx]
                    # 按 mpsz 重新算 disc_counts 与 discard_labels
                    fallback_labels = []
                    for k in range(0, len(stable_mpsz), 2):
                        if k + 2 <= len(stable_mpsz):
                            fallback_labels.append(stable_mpsz[k:k + 2])
                    fc = [0] * 34
                    for lab in fallback_labels:
                        try:
                            fc[mpsz_to_tile34_index(lab)] += 1
                        except Exception:
                            pass
                    disc_mpsz_out = stable_mpsz
                    disc_counts_out = fc
                    discarded_labels_out = fallback_labels
                    # 用稳定值重算 known / remaining / dead
                    known = sum(hand_counts[i] for i in avail_list) + sum(fc[i] for i in avail_list)
                    remaining = max(0, wall_total - known)
                    dead = sum(1 for i in avail_list if hand_counts[i] + fc[i] >= 4)
            self._discard_history.append(disc_mpsz)

            # ===== 冷启动递减 =====
            # 每完整识别一帧（命中"实际跑了 _detect_once"的路径），递减；扣到 0 后
            # _MotionGuard / _FrameSkipper 才开始按正常策略工作。
            if self._warmup_left > 0:
                self._warmup_left -= 1

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
                "discards": disc_mpsz_out,
                "discard_count": len(discarded_labels_out),
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
