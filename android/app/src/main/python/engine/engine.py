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
from modes import (
    load_mode,
    load_advice_config,
    hand_sizes,
    available_set,
    MODES,
)

# 一局中的合法手牌张数：13 = 待摸牌，14 = 刚摸到牌
VALID_HAND_SIZES = (13, 14)

# 预览图最大宽度。原实现每帧都把全屏截图做 PNG 编码（100~300ms），
# 而 Dart 端并没有使用这张图，纯属浪费，是识别卡顿的主因之一。
# 现在 Dart 端实时展示预览，清晰度必须够用户对准识别框。
# 高 DPI 屏（3x）上 240px 会被拉伸到 720 物理像素而模糊，480px 更清晰。
PREVIEW_MAX_WIDTH = 480

# 输入图最长边上限。超过此值先降采样，避免 OpenCV 在超大图上触发 OOM/SIGSEGV。
MAX_INPUT_LONG_EDGE = 2400

# ROI 裁剪后最小高度（像素）。低于此值放弃裁剪，整屏识别，防止窄条导致 FFT/切牌崩溃。
MIN_ROI_HEIGHT = 80

# 引擎侧的最低置信度：比结构识别器的 MIN_CONF 更严，挡掉"卡在两张牌之间"的假命中。
# 调高到 0.55：漏掉的牌由多帧投票补回（要求 4 帧里 ≥3 帧同标签才采纳），
# 但单帧的"白板刷屏"（筒/索背景浅被错认字牌）会显著减少。
#
# 2026-09-01 实战复盘：在腾讯欢乐麻将真实截图（1920×863 横屏）上，0.55 把 7m
# (conf=0.54) 砍掉，导致 13 张手牌只识别 10 张，连续 5 帧状态都是 incomplete，
# 建议段一直空。降到 0.50 既能补回 7m 类的「差一档」牌，又不会引入白板误判
# （白板判定分一般 <0.35，远远够不到 0.50）。
ENGINE_MIN_CONF = 0.50

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

# 「历史强势否决」门槛：历史众数的加权份额 ≥ 该值、且历史完全不支持最新帧
# 的标签时，才判定最新帧是单帧错认并用历史众数覆盖。取值必须高——
# 绝大多数情况下应当采信最新帧（见 _TileVoter.vote 内的注释）。
VOTE_HIST_DOMINANT = 0.70

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

# 冷启动 bootstrap 门槛：尚未建立稳定手牌时，对「最像手牌行」的那一行用此更低门槛
# 放行，打破「严格门槛(0.50) → 首帧所有牌被砍 → raw_labels 空 → 稳定手牌永远建不起来
# → 放宽门槛(0.42，依赖已建稳定手牌)永不生效」的死锁。真机画面比干净测试图噪声大，
# 单张 best-match conf 常落在 0.42~0.50，没有这层就会永久「识别不出来」。
# 只在冷启动且只对手牌行候选生效；稳定手牌一旦建立，正常严格/放宽逻辑接管，不再用此门槛。
BOOTSTRAP_CONF = 0.40

# ===== 部分识别（partial）兜底：消灭"全有或全无"断崖 =====
# 实测证据（localtest/_probe_encoding.py）：同一张图，切牌只少 1 张（14→13），
# 最终结果就从「13 张全对 status=ok」直接变成「count=0 status=no_tiles 完全空白」。
# 原因是多重集稳定器只接受**张数完全合法**（13/14）的帧，一帧都凑不齐就永远输出空。
# 真机是动态视频（摸打动画、半透明遮挡、模糊、分辨率各异），几乎每帧都会掉 1 张，
# 于是用户看到的是永久空白 —— 这是"识别不出来"比模板标签严重得多的头号根因。
#
# 兜底策略：稳定手牌尚未建立时，只要本帧手牌行认出的牌数达到 PARTIAL_MIN_TILES，
# 就以 status="partial" 把这些牌照实输出（悬浮窗已支持 count<13 的"已识别 N 张"渲染）。
# 关键：**不写入 self._stable_hand_mpsz、不喂 trainer**，因此不会污染建议与向听逻辑，
# 只是把"已经认出来的东西"如实显示出来，而不是一律清空。
PARTIAL_MIN_TILES = 6
# 部分识别的保持帧数（滞后窗口）。没有它，partial 会随帧抖动闪进闪出，
# 观感比空白更糟。命中一次后维持 PARTIAL_TTL_FRAMES 帧，期间被新的 partial 刷新。
PARTIAL_TTL_FRAMES = 10

# 牌河稳定性兜底：避免识别器瞬时漏抓牌河中的几张牌，导致 remaining/dead
# 当帧剧烈变化。追踪最近 N 帧的 disc_mpsz 长度与 mpsz 增量；当前帧若
# 显著少于历史最小值（差距 ≥MIN_DROP_DELTA），回退到历史最大稳定值。
DISCARD_HISTORY_FRAMES = 6
DISCARD_HISTORY_MIN_SAMPLES = 3
MIN_DROP_DELTA = 4

# ---------------------------------------------------------------- 手牌稳定化
# 手牌的语义是「多重集」（13/14 张牌的无序集合），不是有序序列。
# 因此稳定化必须在**牌型计数**层面做，绝不能在位置/序列层面做。
#
# 为什么：每次摸牌/打牌后手牌必然重排，每张牌的 x 平移约一个牌宽（真机
# 实测 148px），远超位置投票的归并半径（0.45×牌高 ≈ 75px）。按位置归并
# 的历史帧会**全部**失配，每个位置只剩最新帧 1 票，低于最低票数被判为
# 「未识别」→ 手牌数从 13 掉到 0 → 悬浮窗整段空白 → 两帧后才恢复。
# 这就是用户看到的「一会显示一会不显示」的决定性根因。
HAND_CONFIRM_FRAMES = 2      # 连续多少帧牌型完全一致才采纳为新稳定手牌
HAND_BIG_JUMP = 4            # 与稳定手牌差异超过这么多张，要求多确认 1 帧
HAND_MODE_WINDOW = 8         # 众数兜底窗口：最近多少帧
HAND_MODE_VOTES = 5          # 众数兜底：窗口内出现这么多次即强制采纳


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
                # 窗口内没有任何历史匹配 —— 说明这个位置是**新出现的**
                # （打牌后手牌重排、牌河新增牌、冷启动首帧、镜头/朝向变化）。
                #
                # 旧实现在这里返回 None，这是"打一张牌后整段建议消失 1~2 秒"的
                # 决定性根因：重排让每张牌平移 ≈148px，远超归并半径 ≈75px，
                # 历史帧全部失配，每格只剩最新帧 1 票 < VOTE_MIN_VOTES(2)
                # → 全部输出 None → 手牌数 13 掉到 0 → 悬浮窗整段空白。
                #
                # 位置投票的职责是「修正单帧错认」，不是「否决新位置」。
                # 后者属多重集稳定器（_HandStabilizer）管，它在牌型计数层面
                # 工作，对平移/重排完全免疫。这里直接透传最新帧的判定。
                out.append((rect, last_label, _last_conf))
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
            ok_share = (best_w / n_total_w) >= VOTE_MIN_FRAC
            ok_abs = label_weight[best_lab] >= VOTE_MIN_VOTES
            if last_label is None:
                # last 没识别，依靠历史加权；放宽要求，但不能松到让单帧噪声混入
                chosen: Optional[str] = best_lab if (ok_share and ok_abs) else None
            else:
                # ===== 最新帧优先 =====
                # 位置对上了、但标签和历史众数不同时，**优先信最新帧**。
                # 旧实现要求 last_label == best_lab 才输出，否则输出 None ——
                # 于是"手牌刚变化"的那一帧必然被否决（历史全是旧标签），
                # 又是一处整帧空白的来源。
                #
                # 唯一的例外是「历史强势否决」：历史票数足够厚、众数份额
                # 压倒性、且历史里一次都没出现过最新帧的标签 —— 三条同时
                # 成立才判定最新帧是单帧错认（典型：筒/索被误认成白板），
                # 用历史众数纠正。否则一律采信最新帧。
                w_last = weights[-1]
                support_last = label_weight.get(last_label, 0.0) - w_last
                hist_w = n_total_w - w_last
                strong_hist = (
                    hist_w >= 1.5
                    and (best_w / n_total_w) >= VOTE_HIST_DOMINANT
                    and support_last <= 1e-9
                )
                chosen = best_lab if strong_hist else last_label
            avg_conf = (
                sum(label_conf[best_lab]) / len(label_conf[best_lab])
                if best_lab in label_conf else 0.0
            )
            out.append((rect, chosen, avg_conf))
        out.sort(key=lambda d: d[0][0])
        return out


def _mpsz_to_counter(mpsz: str) -> Counter:
    """mpsz 串 → 牌型计数（已是排序归一化的，可直接当字典键比较）。"""
    return Counter([mpsz[i:i + 2] for i in range(0, len(mpsz) - 1, 2)])


def _counter_to_mpsz(cnt: Counter) -> str:
    """牌型计数 → 排序归一化的 mpsz 串（同一副手牌永远得到同一个串）。"""
    return "".join(t * n for t, n in sorted(cnt.items()))


# 牌面亮度下限（灰度均值）。切牌器按"张数先验"强制切满 N 张，遇到非牌
# 区域（桌面、UI 元素、牌之间的大缝隙）会把它也切成一张牌，并给出一个
# **自信的错误标签**。实测：把真机截图里的一张牌涂成桌面背景后，切牌器
# 仍切出 13 张，把背景判成了 1p（conf 0.95）——漏识别反而变成了认错一张
# 牌。这种错误在置信度层面完全看不出来（conf 很高），只能看牌面本身。
#
# 判据标定（同一张真机截图 2712x1220 实测）：
#     牌面   灰度均值 170~193，饱和度 28~52
#     桌面   灰度均值  56~ 84，饱和度 117~119
# 中间 85~165 是巨大空档，门槛取 120 落在正中，极稳。
# 另配"整行自适应"：若整行牌面都很暗（暗色主题美术），说明这条判据
# 不适用，整帧跳过亮度过滤，绝不至于把真牌全砍光。
MIN_FACE_BRIGHTNESS = 120.0


def _face_brightness(image: CVImage, rect) -> float:
    """牌面中心区域的灰度均值（避开边框与相邻牌的缝隙）。"""
    try:
        x, y, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
        ih_, iw_ = image.shape[:2]
        ix = max(0, x + int(w * 0.15))
        iy = max(0, y + int(h * 0.15))
        iw = min(int(w * 0.7), iw_ - ix)
        ih = min(int(h * 0.7), ih_ - iy)
        if iw <= 0 or ih <= 0:
            return 0.0
        patch = image[iy:iy + ih, ix:ix + iw]
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
        return float(gray.mean())
    except Exception:
        return 0.0


# 帧差去重最多连续跳过多少帧。
# 麻将画面大部分时间是静止的（等别人出牌），帧差去重能省下大量 CPU；
# 但不能无限期跳过——否则牌河里的新牌、手牌的细微变化永远刷新不了。
# 4 帧 ≈ 1.6s 强制刷一次，CPU 仍省约 75%。
MAX_SKIP_FRAMES = 4


class _HandStabilizer:
    """手牌多重集稳定器 —— 消除"一会显示一会不显示"的核心。

    手牌在语义上是**多重集**（13/14 张牌的无序集合），不是有序序列。
    所以稳定化必须在牌型计数层面做：把每帧的手牌行标签转成 Counter，
    再对"最近若干帧的 Counter"求共识。这样做之后，

      - 手牌重排 / 整行平移 / 摸牌插入 —— 完全不影响（Counter 不变）
      - 单帧漏识别 1 张    —— 该帧张数不合法，直接不参与共识，稳定值不动
      - 单帧把某张认错     —— 该帧 Counter 与前后都不同，拿不到共识，稳定值不动
      - 真实摸牌 / 打牌    —— 连续 2 帧给出同一个新 Counter，立即切换

    采纳规则（满足任一即更新稳定手牌）：
      1) 连续 HAND_CONFIRM_FRAMES 帧给出完全相同的合法牌型；
      2) 最近 HAND_MODE_WINDOW 帧里同一合法牌型出现 ≥HAND_MODE_VOTES 次
         （兜底：应对"识别器稳定漏同一张牌"这类永远凑不满连续帧的情况）。

    大跳变（与当前稳定手牌差异 > HAND_BIG_JUMP 张）额外要求多确认 1 帧，
    因为正常一巡最多变化 1~2 张，一次差 5 张以上几乎一定是误识别爆发。

    输出的 mpsz **永不为空**（除非从未识别到过合法手牌）——这正是
    "识别不出来时界面也不要空着"的保证。
    """

    def __init__(self) -> None:
        # 当前稳定手牌（排序归一化的 mpsz）
        self.stable_mpsz: str = ""
        # 连续多少帧给出了同一个牌型
        self._streak: int = 0
        self._last_key: str = ""
        # 最近若干帧的合法牌型（供众数兜底）
        self._recent: deque = deque(maxlen=HAND_MODE_WINDOW)
        # 本帧是否看到了一个"尚未被采纳"的新牌型。引擎据此**强制**下一帧
        # 跑完整识别（否则帧差去重会把后续帧全跳掉，永远攒不到第二票，
        # 手牌就再也不更新了 —— 实测中这是"识别不出来"的根因之一）。
        self.pending: bool = False

    def reset(self) -> None:
        """玩法切换 / 朝向变化时硬重置：旧手牌与新场景无关。"""
        self.stable_mpsz = ""
        self._streak = 0
        self._last_key = ""
        self._recent.clear()
        self.pending = False

    def observe(self, labels, valid_sizes, avail=None) -> str:
        """喂入本帧手牌行的标签列表，返回应当对外输出的稳定 mpsz。"""
        cnt: Counter = Counter()
        for lab in labels:
            if not lab:
                continue
            if avail is not None:
                try:
                    if mpsz_to_tile34_index(lab) not in avail:
                        continue
                except Exception:
                    continue
            cnt[lab] += 1

        n = sum(cnt.values())
        # 张数不合法（漏识别 / 多识别 / 根本没切到牌）→ 这一帧不参与共识。
        # 稳定手牌原样保留，界面继续显示上一副确定的手牌。
        if n not in valid_sizes:
            self._streak = 0
            self._last_key = ""
            self.pending = False
            return self.stable_mpsz

        # 互斥校验：同字 ≥5 张必是误判（典型：筒/索被刷成白板）。
        if any(c > MAX_DUP_PER_TILE for c in cnt.values()):
            self._streak = 0
            self._last_key = ""
            self.pending = False
            return self.stable_mpsz

        key = _counter_to_mpsz(cnt)
        self._recent.append(key)

        if key == self._last_key:
            self._streak += 1
        else:
            self._streak = 1
            self._last_key = key

        # 已经是稳定值 → 无待确认变化，下一帧可以正常按帧差跳过
        if key == self.stable_mpsz:
            self.pending = False
            return self.stable_mpsz

        # 需要多少帧共识：大跳变额外 +1 帧
        need = HAND_CONFIRM_FRAMES
        if self.stable_mpsz:
            if _hand_diff_count(self.stable_mpsz, key) > HAND_BIG_JUMP:
                need += 1
        # 冷启动：还没有任何稳定手牌时，第一个合法牌型立即采纳。
        # 再等 2 帧共识只会让界面多空白 800ms —— 用户最不能忍的正是"识别不出来"。
        if not self.stable_mpsz:
            self.stable_mpsz = key
            self.pending = False
            return self.stable_mpsz

        # 众数兜底：窗口内出现次数够多，说明识别器已经稳定在（可能不完美的）
        # 这个牌型上，再等下去也不会更好，直接采纳。
        mode_hits = sum(1 for k in self._recent if k == key)
        if self._streak >= need or mode_hits >= HAND_MODE_VOTES:
            self.stable_mpsz = key
            self.pending = False
        else:
            # 看到了新牌型但证据还不够 —— 通知引擎下一帧必须真跑一次，
            # 别被帧差去重跳掉。
            self.pending = True
        return self.stable_mpsz


def _reconcile_hand_tiles(row, stable_mpsz: str):
    """让手牌行的逐张标签与稳定手牌对齐。

    UI 渲染的是 tiles 里的逐张标签，建议/向听用的是 hand 字段。两者来自
    同一帧的不同处理阶段，一旦不一致，用户会看到"显示的牌"和"建议打的牌"
    对不上（建议打 9m，但屏幕上手牌行里根本没有 9m）。

    策略：
      - 逐张标签的牌型计数已经等于稳定手牌 → 原样保留（顺序最真实）。
      - 否则用稳定手牌的排序序列按位置回填。麻将手牌在游戏里本来就是
        排好序的，所以"第 i 个位置 = 排序后第 i 张"在绝大多数 UI 上成立。
    """
    if not stable_mpsz or not row:
        return row
    cnt_row = Counter(d[1] for d in row if d[1] is not None)
    if cnt_row == _mpsz_to_counter(stable_mpsz):
        return row
    labels = [stable_mpsz[i:i + 2] for i in range(0, len(stable_mpsz), 2)]
    out = []
    for i, (rect, lab, conf) in enumerate(row):
        out.append((rect, labels[i] if i < len(labels) else None, conf))
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


# 方向重探「熔断」上限：连续 0 牌触发重探本是好意，但真机若因朝向/画面问题
# 持续 0 牌，无限重探会反复跑重型 4 方向探测 → OOM/SIGSEGV 闪退。限制最多重探
# MAX_ORIENT_REPROBES 次，之后停止重探、优雅报告 no_tiles，绝不让"识别不出来"
# 演变成"程序闪退"。用户可用悬浮窗「旋转」按钮手动指定方向。
MAX_ORIENT_REPROBES = 2


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
        # 多帧投票：把最近 4 帧的同一位置检测做加权多数表决（只负责"修正单帧错认"）
        self._tile_voter = _TileVoter(window=VOTE_WINDOW)
        # 手牌多重集稳定器：真正决定"界面上显示哪副手牌"的地方
        self._hand_stab = _HandStabilizer()
        # 行锁定：上一帧手牌行的 y 坐标，下一帧在 [y - HAND_LOCK_BAND, y + HAND_LOCK_BAND]
        # 范围内挑，避免 13↔14 跳变（手牌行被牌河/记分行抢走）的根因
        self._last_hand_y: Optional[float] = None
        # 最近一次"稳定"的手牌 mpsz 与张数：用于本帧识别失败/可疑时做兜底
        self._stable_hand_mpsz: str = ""
        self._stable_hand_count: int = 0
        # 部分识别兜底（见 PARTIAL_MIN_TILES 注释）：稳定手牌未建立时，把"已经认出的
        # 那几张"如实显示出来，而不是整帧清空。带 TTL 滞后，避免逐帧闪进闪出。
        self._partial_mpsz: str = ""
        self._partial_ttl: int = 0
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
        # ===== 方向自检（旋转鲁棒性）=====
        # 真机截屏：竖屏手机 + 横屏麻将游戏时，MediaProjection 的 VirtualDisplay
        # 被强制成横屏缓冲，横屏游戏在里面被系统旋转 90° 塞入。结果所有牌都"横过来"，
        # 宽高比 < MIN_TILE_ASPECT 全被 _apply_conf 滤掉 → 表现为"一张牌都识别不出"。
        # 这里在 0/90/180/270 四个方向各探一次，锁定"识别到牌最多"的方向；
        # 中途方向变化（用户旋转手机/切换 App）连续 3 帧 0 牌时自动解锁重探。
        self._orient: Optional[int] = None
        self._orient_zerocount: int = 0
        # 手动方向覆盖（悬浮窗「旋转」按钮设置）。非 None 时跳过自动探测，
        # 直接旋到指定方向。0/90/180/270 或 None（解除）。
        self._orient_override: Optional[int] = None
        # 方向重探熔断计数：已达上限后停止重探，避免无限重型探测闪退。
        self._orient_reprobe_count: int = 0
        # 方向探测/快路径时已算出的检测结果，供 process() 复用，
        # 避免同一帧做两次完整检测。用完即清。
        self._cached_rows: Optional[list] = None
        # ===== 用户可调识别区域（ROI）=====
        # 真机游戏美术/布局与训练截图差异大时，自动行检测可能挑错区域
        # （挑到 banner/UI 而非手牌行）→ 表现为"识别不出来"。
        # 悬浮窗里用户拖动"识别框"对准手牌后，通过 set_roi(top,bottom) 把
        # 识别范围收敛到屏幕的 [top,bottom] 纵向比例带内。默认 None = 整屏。
        # 即便用户不动它也是全屏，绝不退化。
        self._roi: Optional[tuple] = None
        # ===== 调试页可调开关（经 set_config 实时修改，不重启引擎）=====
        # auto_orient：自动方向探测（横屏/竖屏旋转归一）。关掉则只用手动覆盖/0°。
        # bootstrap：冷启动宽松门槛（BOOTSTRAP_CONF），用于打破严格门槛死锁。
        # strict：严格门槛开关。关掉则一律走放宽门槛（更易识别出，但更易误识）。
        self._cfg: Dict[str, bool] = {
            "auto_orient": True,
            "bootstrap": True,
            "strict": True,
            # 防封号 / 防平台检测：真实行为在 Java 侧采集循环执行（截屏节奏抖动、
            # 前台感知采样），这里仅存档，供 set_config 接受，不影响识别结果。
            "anti_ban": False,
            "anti_detect": False,
        }
        # 出牌建议配置（调试页开关，process() 每帧从 mahjong_advice.json reload）。
        # 这里给一份安全默认：显示出牌建议、不过滤进张。即便文件永远不存在，
        # 行为也与接入该配置前完全一致（不会变成"没建议"）。
        self._advice_cfg: Dict[str, object] = {
            "show_advice": True,
            "min_ukeire": 0,
        }

    def set_config(self, key: str, value) -> None:
        """调试页开关：实时修改识别策略。未知 key 静默忽略。"""
        if key in self._cfg:
            self._cfg[key] = bool(value)

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

        降级策略：len(hand) 不在合法张数 (13/14) 时，**绝不重算**（实测 12 张牌跑
        calculate_ukeire_ex 全部返回 0 ukeire，无意义）—— 直接复用上次稳定 advice
        (self._advice)。这样 UI 不会因 1~2 帧识别不全而闪空，advice_n 始终 > 0。
        """
        if self.trainer is None:
            return None, []

        if disc_counts is not None:
            self.trainer.set_visible(disc_counts, [0] * 34)

        shanten = self.trainer.get_shanten()

        # ===== 调试页配置（process() 每帧从 mahjong_advice.json reload）=====
        # show_advice=False：关闭出牌建议。必须放在 incomplete 分支**之前**，
        #   否则手牌不完整时会从 self._advice 缓存里又吐出旧建议 → "关了还显示"。
        # min_ukeire>0   ：只保留「进张数 >= 阈值」的打法（即调试页"好牌机率"）。
        cfg = getattr(self, "_advice_cfg", None) or {}
        show_advice = bool(cfg.get("show_advice", True))
        raw_min = cfg.get("min_ukeire", 0)
        # bool 是 int 的子类，必须显式排除，否则 True 会被当成阈值 1。
        min_ukeire = raw_min if (
            isinstance(raw_min, int) and not isinstance(raw_min, bool)) else 0
        if min_ukeire < 0:
            min_ukeire = 0
        # 危险牌预警开关（默认关）。来自 mahjong_advice.json 的 warn_deal_in /
        # warn_pon_kong。这里读到的已是纯 bool（JSON 反序列化结果），无需再排 int。
        warn_deal_in = bool(cfg.get("warn_deal_in", False))
        warn_pon_kong = bool(cfg.get("warn_pon_kong", False))

        if not show_advice:
            return shanten, []

        # 不完整手牌：复用缓存。返回一个浅拷贝防止上游改 self._advice。
        if len(hand) not in hand_sizes(self.mode):
            return shanten, list(self._advice)

        # 缓存键必须带上 min_ukeire 与两个 warn 开关：否则调高/调低"好牌机率"
        # 或切换危险牌预警时会命中旧缓存，界面建议列表纹丝不动 → 表现为开关"没生效"。
        key = f"{hand}|{shanten}|{min_ukeire}|{warn_deal_in}|{warn_pon_kong}|{hash(tuple(self.trainer.disc_counts))}"
        if key == self._advice_key:
            return shanten, self._advice

        advice: List[Dict] = []
        try:
            if len(hand) in hand_sizes(self.mode):
                raw = self.trainer.calculate_discards()
                items = sorted(raw.items(), key=lambda kv: -kv[1])
                if min_ukeire > 0:
                    items = [(t, u) for (t, u) in items if int(u) >= min_ukeire]
                advice = [
                    {"tile": str(t), "ukeire": int(u)}
                    for (t, u) in items
                ][:6]
        except Exception:
            traceback.print_exc()

        # 危险牌预警：基于「当前牌河计数」给每张候选弃牌附上真实危险度。
        # 注意：disc_counts 是**全桌牌河合在一起**的一维计数，没有按对手拆分，
        # 也没有副露（meld）数据（引擎当前 meld 传的是 [0]*34），所以是粗略启发式：
        #   防点炮：牌河里已有该牌 = 现物，任何人不可和此牌 → 点炮安全(safe)；
        #           生张(牌河为 0) → 点炮高危(risky)。
        #   防杠/碰：该牌在牌河出现越少，越可能被某对手握成对子可碰/杠；
        #           ≥3 张基本不可能(safe)，0 张风险最高(risky)，1~2 张中等(mid)。
        # 这两个字段只是「附加数据」，overlay 视自身渲染能力决定是否展示；
        # 开关关闭时不附加，保持 advice 结构向后兼容。
        if (warn_deal_in or warn_pon_kong) and disc_counts is not None:
            for it in advice:
                try:
                    idx = mpsz_to_tile34_index(it["tile"])
                    cnt = disc_counts[idx] if 0 <= idx < 34 else 0
                except Exception:
                    cnt = 0
                if warn_deal_in:
                    it["deal_in"] = "safe" if cnt > 0 else "risky"
                if warn_pon_kong:
                    it["pon_kong"] = (
                        "safe" if cnt >= 3 else ("risky" if cnt == 0 else "mid")
                    )

        self._advice_key = key
        self._advice = advice
        return shanten, advice

    def set_roi(self, top_frac, bottom_frac) -> None:
        """设置识别区域（纵向比例带）。top/bottom ∈ [0,1]，自上而下的屏幕比例。

        由悬浮窗拖动"识别框"时实时调用。参数非法（非数/越界）直接忽略，
        绝不抛异常——这是 Engine 的方法，抛错会经 chaquopy 冒泡到 Java
        再冒泡到 UI 线程，可能触发闪退。
        """
        try:
            t = float(top_frac)
            b = float(bottom_frac)
        except (TypeError, ValueError):
            return
        if not (0.0 <= t <= 1.0 and 0.0 <= b <= 1.0):
            return
        if b - t < 0.02:  # 带太窄没意义，保底 2%
            return
        self._roi = (t, b)

    def set_orient(self, deg) -> None:
        """手动指定方向（0/90/180/270 或 None 解除）。悬浮窗「旋转」按钮调用。

        用于自动方向探测失败（特殊画面/异常朝向）时的兜底：用户一眼看到牌被
        横置，点一下旋转即可校正，无需等自动重探。参数非法直接忽略，绝不抛异常
        （否则会经 chaquopy 冒泡到 Java 再冒泡到 UI 线程，可能触发闪退）。
        """
        try:
            d = None if deg is None else int(deg)
        except (TypeError, ValueError):
            return
        # 约定：0/90/180/270 = 锁定该方向；其它任意值（Java 侧用 -1，Dart 侧「重探
        # 方向」按钮发 -1）= 解除手动覆盖并让自动探测重新跑一遍。
        # 早期实现对 -1 直接 return，导致「重探方向」按钮静默失效，这里必须兜住。
        self._orient_override = d if d in (0, 90, 180, 270) else None
        # 无论锁定还是解除，都要解锁自动方向让下一帧重新定向，并清掉与方向强相关的
        # 状态（历史手牌 y、投票窗口、行缓存），否则旧方向的脏数据会污染新方向。
        self._orient = None
        self._orient_zerocount = 0
        self._orient_reprobe_count = 0
        self._last_hand_y = None
        try:
            self._tile_voter.reset()
        except Exception:
            pass
        self._cached_rows = None

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

    def _apply_conf(self, rect, label, conf, bootstrap=False):
        """置信 + 牌形比例过滤：低于门槛或牌形不对的牌直接判为「不识别」，
        宁可漏识别也绝不臆测（白板刷屏、半张牌等假命中在此被挡掉）。

        三门槛机制：
          - 严格门槛 ENGINE_MIN_CONF（0.50）：默认走这条，防白板/伪命中。
          - 放宽门槛 ENGINE_MIN_CONF_RELAX（0.42）：仅当引擎已建立稳定手牌
            （self._stable_hand_mpsz 非空）时才允许。这是"漏 1 张 → 自动补漏"
            的关键：若投票窗口里有 2~4 张牌稳定为 Xm，但第 N 张本来被投票器
            因 0.50 分拒了，会导致手牌数对（13/14 张）但实际少识别了一张。
            放宽门槛只在"补漏"时启用——启动期仍走严格门槛，避免噪声被当真。
          - bootstrap 门槛 BOOTSTRAP_CONF（0.40）：仅冷启动（尚无稳定手牌）
            且本张属于「手牌行候选」时生效，用于打破上述死锁，详见 BOOTSTRAP_CONF。
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
        # 已建立稳定手牌 + 严格门槛不过 + 放宽门槛过 → 允许（补漏）
        if conf >= ENGINE_MIN_CONF_RELAX and self._stable_hand_mpsz:
            return label
        # 冷启动 bootstrap：让手牌行候选先立住稳定器，之后正常逻辑接管。
        # 受调试页「冷启动」开关控制；关掉则不走此宽门槛。
        if bootstrap and self._cfg.get("bootstrap", True) and conf >= BOOTSTRAP_CONF:
            return label
        # 调试页「严格门槛」开关关掉时，一律放宽到 RELAX 门槛（更易识别出，但更易误识）。
        # 仅当已建立稳定手牌时才允许（避免启动期噪声被当真）。
        if (not self._cfg.get("strict", True)) and self._stable_hand_mpsz and conf >= ENGINE_MIN_CONF_RELAX:
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

    @staticmethod
    def _longest_row_at_bottom(rows, img_h: int) -> bool:
        """最长牌行（=手牌行）是否位于画面下半部分。

        这是区分「正确朝向」与「180° 倒置」的**决定性**信号，而且几乎零成本。
        所有主流麻将 UI（雀魂 / 腾讯欢乐麻将 / 天凤）都把玩家自己的手牌放在
        屏幕底部；图被倒置后手牌行就跑到顶部。实测同一张图：
            正确朝向  手牌行中心 y/H = 0.90
            倒置 180° 手牌行中心 y/H = 0.10
        分离度 0.8，比任何分类质量指标都可靠。

        为什么不能用 avg_conf 判倒置（**踩过的坑**）：筒子牌点阵中心对称，
        倒过来仍以 0.9 高分正确分类；而万牌倒置后直接分类失败被丢弃 ——
        「把读不出的牌剔除」反而**拉高**了平均分。实测倒置 avg_conf=0.882
        竟高于正确朝向的 0.860，是彻底的存活者偏差，方向判据绝不能只看它。
        """
        if not rows or img_h <= 0:
            return False
        longest = max(rows, key=len)
        if not longest:
            return False
        # 行内牌的中心 y（d[0] = (x, y, w, h)）
        cy = sum(d[0][1] + d[0][3] * 0.5 for d in longest) / len(longest)
        return cy >= 0.50 * img_h

    def _probe_orientation(self, image: CVImage):
        """探测最佳方向，规避"竖屏截横屏游戏 → 牌被旋转 90° → 全滤掉"的坑。

        返回 (rot, rotated_image)。rot 为需要施加到原图上的顺时针旋转角度。

        **两阶段**，把冷启动从 1400ms 压到 ~500ms（实测）：

        阶段 A（几何筛选，classify=False，~17ms/方向）：
            4 个方向只切牌不分类，算牌数和"有没有长牌行"。
            正确方向及其 180° 倒置版本都会横排出长行；另两个方向牌
            竖排、宽高比不达标，牌数极少 —— 这一步就能砍掉一半候选。

        阶段 B（分类质检，classify=True，~180ms/候选）：
            只对阶段 A 留下的候选做分类。**必须用分类质量评分**：
            纯几何判据无法区分 0° 和 180°（180° 的牌仍横排、数量不变，
            但图案上下颠倒，分类置信度显著下降，实测 count 13->9）。
            这一步禁用局部重试 —— 方向选择只需要方向间的**相对**质量
            对比，重试属于锁定方向之后的精修。

        **怎么区分 0° 和 180°**（A 阶段砍不掉它俩，几何上完全等价）：
        靠"手牌行必须在画面下半部"这个布局先验（见 _longest_row_at_bottom），
        在总分里加 12 分。不要指望 avg_conf —— 倒置时筒子牌照样高分、万牌
        直接被丢弃，存活者偏差会让倒置的均分**反超**正确朝向（实测 0.882
        vs 0.860）。历史上这里曾用"给 0° 一点点偏置"来凑，靠不住，已移除。

        实测冷启动：旋转 90/270 ~700ms（A 阶段只剩 1 个候选），
        旋转 180 ~1.3s（A 剩 0°/180°，B 各跑一次完整分类）。冷启只发生一次。
        """
        det = self.get_detector()
        if det is None:
            return 0, image
        variants = [
            (0, image),
            (90, cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)),
            (180, cv2.rotate(image, cv2.ROTATE_180)),
            (270, cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)),
        ]

        # ---------- 阶段 A：几何筛选 ----------
        geo = []
        for rot, rim in variants:
            try:
                rows = det.detect_all_rows(rim, classify=False,
                                           allow_rotation=False)
            except Exception:
                traceback.print_exc()
                rows = []
            n = sum(len(r) for r in rows)
            has_long = any(len(r) >= 8 for r in rows)
            geo.append((rot, rim, n, has_long))
        # 有长牌行的方向优先；若一个都没有（画面里根本没牌 / 牌很少），
        # 退化为按牌数取前 2 名，避免直接放弃探测。
        cands = [g for g in geo if g[3]]
        if not cands:
            geo_sorted = sorted(geo, key=lambda g: -g[2])
            cands = [g for g in geo_sorted[:2] if g[2] > 0]
        if not cands:
            self._cached_rows = []
            return 0, image

        # ---------- 阶段 B：仅用几何判据选方向（不做分类，快 ~10x）----------
        # 方向选择只需要"哪个旋转牌最多、牌行最长、且手牌行在下方"的**相对**
        # 对比，分类标签在此阶段毫无用处。分类（最贵的步骤，涉及全模板匹配）
        # 留到锁定方向后的 process() 里只做一遍。这样把启动期 OpenCV 负载
        # 砍掉一大半，显著降低低内存机型上 OOM/SIGSEGV 闪退的概率。
        #
        # 实测：0° 候选几何分（牌数 12 + 长牌行 + 位置 12）远高于 180°（9+8+12）
        # 与 90/270（极少），选向结论与旧"分类质检"完全一致，但快很多。
        def _geo_score(rim):
            try:
                rows = det.detect_all_rows(rim, classify=False,
                                           allow_rotation=False)
            except Exception:
                return -1.0, []
            n = sum(len(r) for r in rows)
            has_long = any(len(r) >= 8 for r in rows)
            pos_bonus = 12.0 if self._longest_row_at_bottom(rows, rim.shape[0]) else 0.0
            return (n + (8 if has_long else 0) + pos_bonus), rows

        best_rot, best_img = cands[0][0], cands[0][1]
        best_score, best_rows = -1.0, []
        for rot, rim, _n, _h in cands:
            s, rows = _geo_score(rim)
            if s > best_score:
                best_score, best_rot, best_img, best_rows = s, rot, rim, rows

        # 锁定方向后，对最优方向补一次「带分类」的检测，作为首帧缓存
        # （process() 直接复用，不重复检测）。allow_retry=False 控制单帧开销上限。
        # 任何异常都不影响：最坏只是首帧无标签，下一帧（已锁方向）会重新分类。
        try:
            best_rows = det.detect_all_rows(best_img, classify=True,
                                            allow_rotation=False,
                                            allow_retry=False)
        except Exception:
            traceback.print_exc()
        # 缓存最优方向的检测结果，process() 直接复用，避免重复检测
        self._cached_rows = best_rows
        return best_rot, best_img

    @staticmethod
    def _rotate_to(image: CVImage, deg) -> CVImage:
        """把图旋到指定角度（0/90/180/270），其余值原样返回。"""
        if deg == 90:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        if deg == 180:
            return cv2.rotate(image, cv2.ROTATE_180)
        if deg == 270:
            return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return image

    def _apply_orientation(self, image: CVImage) -> CVImage:
        """按当前锁定的方向把图旋到规范横屏朝向。未锁定时做一次探测并锁定。

        手动方向覆盖（悬浮窗「旋转」按钮）优先级最高：直接旋到用户指定的方向，
        跳过自动探测。自动探测在特殊画面/异常朝向下可能选错，用户一眼看到牌被
        横置时点一下即可校正，无需等自动重探。

        快路径：先按原方向做一次**快速**检测（classify=False，~17ms）。
        绝大多数情况用户是正常持机的，原方向就是对的 —— 这时直接锁定 0°，
        省掉 3 个多余方向的探测（3x17ms）和一次重复的方向探测开销。
        只有原方向明显不对（牌数 <8 或没有长牌行）时才走 4 方向全探测。
        """
        # 手动方向覆盖优先级最高：跳过自动探测，直接旋到用户指定的方向。
        if self._orient_override is not None:
            self._orient = self._orient_override
            self._orient_zerocount = 0
            return self._rotate_to(image, self._orient_override)
        if self._orient is None:
            # 调试页关掉「自动方向探测」：直接用 0°（或手动覆盖），不做任何方向探测，
            # 避免误旋转，也省下 4 方向探测的开销/崩溃风险。
            if not self._cfg.get("auto_orient", True):
                self._orient = 0
                self._orient_zerocount = 0
                return image
            # 快路径：原方向够好就直接用
            det = self.get_detector()
            if det is not None:
                try:
                    # 完整检测（含分类），结果缓存交给 process() 复用，
                    # 所以这次检测的开销不会被浪费。
                    rows = det.detect_all_rows(image, classify=True,
                                               allow_rotation=False)
                    n = sum(len(r) for r in rows)
                    confs = [d[2] for r in rows for d in r if d[1] is not None]
                    avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                    # 判据必须含分类质量：
                    #  - 旋转 90/270：牌竖排，n=0 或极少 -> 拒绝
                    #  - 旋转 180：牌横排、n=13，但图案倒置 -> avg_conf 低 -> 拒绝
                    #  - 正常：n=13 且 conf 高 -> 接受
                    # 判据 = 存在"手牌行"（行长 >= 11）+ 分类质量达标。
                    # 为什么不能用 n>=8：旋转 180 时筒子牌上下对称、倒过来
                    # 仍能正确分类（conf 0.79），n=9 也能过 n>=8 ——
                    # 但万/索牌倒置后被判成字牌，行长从 13 掉到 9。
                    # 用手牌行长度判据即可区分（原图 13 >= 11，旋转 180 只有 9）。
                    has_hand_row = any(len(r) >= 11 for r in rows)
                    # 位置判据（必需）：手牌行必须在画面下半部。
                    # 只靠 len>=11 + avg_conf 会把 180° 倒置图误判成正确朝向 ——
                    # 倒置时手牌行仍能切出 11 张（万牌被丢弃，13->11）且
                    # avg_conf 因存活者偏差反而更高（0.882 > 0.860）。
                    at_bottom = self._longest_row_at_bottom(rows, image.shape[0])
                    if has_hand_row and avg_conf >= 0.50 and at_bottom:
                        self._orient = 0
                        self._orient_zerocount = 0
                        self._cached_rows = rows
                        return image
                except Exception:
                    traceback.print_exc()
            # 慢路径：原方向不对，探测 4 个方向
            rot, image = self._probe_orientation(image)
            self._orient = rot
            self._orient_zerocount = 0
            if rot != 0:
                print(f"[engine] 方向自检锁定 {rot}°（原图疑似被旋转）")
            return image
        return self._rotate_to(image, self._orient)

    def process(self, image: CVImage) -> Optional[EngineResult]:
        try:
            # ===== 崩溃兜底（C 层 SIGSEGV 不可被 Python try/except 捕获）=====
            # 在进入任何 cv2 操作前，用纯 numpy 校验图像结构性合法。
            # 畸形/空/坏 dtype 的图像会让 OpenCV 底层直接段错误导致进程闪退，
            # 必须在 cv2 触碰它之前拦截为 _error_result。
            if not _is_valid_image(image):
                return _error_result("decode_error",
                                     "图像数据非法（空/坏尺寸/坏 dtype），已安全跳过")

            # ===== 方向归一（旋转鲁棒性）=====
            # 必须在帧差/检测之前做，保证后续所有几何都基于规范朝向。
            # 方向探测涉及 cv2.rotate 和多次完整检测，是最容易触发原生崩溃
            # 的环节之一。先包一层 try/except：即使方向探测崩了，也回退
            # 到原图继续识别，至少不会闪退。
            try:
                image = self._apply_orientation(image)
            except Exception as e:
                traceback.print_exc()
                print(f"[engine] 方向归一异常，回退到原图: {e}")
                # 重置方向锁，下一帧重新探测
                self._orient = None

            # 全图（方向归一后）留作预览用：无论用户是否框选 ROI，发给
            # 悬浮窗的预览都画的是整屏，这样"识别框"的比例才是相对整屏的，
            # 拖动对准才直观。
            full_for_preview = image

            # ===== 超大图降采样（防爆内存原生崩溃）=====
            # 真机某些机型/分辨率下 imdecode 出来的图可能极大（≥4000px 边），
            # 后续 FFT 自相关 / 多帧投票 / 预览会在 C 层吃下远超常量的内存，
            # 触发 OOM 或 SIGSEGV 闪退。这里把最长边压到 MAX_INPUT_LONG_EDGE
            # 以内再继续。注意预览图用同一张降采样后的全图，清晰度已足够。
            try:
                ih, iw = image.shape[:2]
                long_edge = max(ih, iw)
                if long_edge > MAX_INPUT_LONG_EDGE:
                    scale = MAX_INPUT_LONG_EDGE / float(long_edge)
                    new_w = max(1, int(iw * scale))
                    new_h = max(1, int(ih * scale))
                    image = cv2.resize(
                        image, (new_w, new_h),
                        interpolation=cv2.INTER_AREA)
                    full_for_preview = image
            except Exception:
                pass

            # ===== 用户 ROI 裁剪 =====
            # 只识别 [top,bottom] 纵向比例带内的画面。默认 None = 整屏。
            # 切片后显式 .copy() 成 contiguous 数组：OpenCV 某些版本对
            # 非连续（被父数组 stride 影响的）视图做运算会触发 C 层崩溃。
            try:
                if self._roi is not None:
                    ih, _ = image.shape[:2]
                    y0 = max(0, min(ih, int(self._roi[0] * ih)))
                    y1 = max(y0, min(ih, int(self._roi[1] * ih)))
                    if y1 - y0 >= MIN_ROI_HEIGHT:
                        image = np.ascontiguousarray(image[y0:y1, :])
                    # 否则太窄，忽略 ROI，整屏识别
            except Exception:
                # ROI 切片异常（坏比例/坏尺寸）不阻塞主流程，回退整屏识别
                pass

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

            # 强制跑完整识别的两个条件：
            #  1) 连续跳过已达上限 —— 画面可以长时间静止，但牌河/剩余数仍要刷新；
            #  2) 稳定器报告"看到了新牌型但证据还不够"。
            #     缺了第 2 条会有个很隐蔽的死结：麻将画面打完一张牌后就静止了，
            #     帧差低于阈值 → 后续帧全被跳过 → 稳定器永远攒不到第二票 →
            #     手牌再也不更新。用户看到的就是"识别不出来"。
            force_run = (self._consecutive_skips >= MAX_SKIP_FRAMES
                         or self._hand_stab.pending)
            if (not force_run
                    and diff < FRAME_DIFF_THRESHOLD
                    and self._frame_skipper.cached is not None):
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
            # 出牌建议配置每帧 reload（与 load_mode 同一时机，文件极小，开销可忽略）：
            # "显示出牌建议" 与 "好牌机率(进张下限)" 由调试页经 Java 写入。
            # 注意：改动 min_ukeire 会让 build_advice 的缓存键变化 → 自动重算。
            self._advice_cfg = load_advice_config()
            if self.mode != self._prev_mode:
                self._tile_voter.reset()
                self._hand_stab.reset()
                self._last_hand_y = None
                self._stable_hand_mpsz = ""
                self._stable_hand_count = 0
                self._advice_key = None
                self._frame_skipper = _FrameSkipper()  # 旧缓存与新玩法无关
                # 玩法的合法手牌张数/可用牌集合都变了，旧 trainer 里的
                # 历史手牌会让 diff 判定全乱，必须重建（下一帧自动建立）。
                self.trainer = None
                self._prev_mode = self.mode
            avail = available_set(self.mode)
            hsizes = hand_sizes(self.mode)

            detector = self.get_detector()
            if detector is None:
                print("No templates available")
                return _error_result("py_error", "识别器初始化失败（模板库为空）")

            # 取「所有牌行」（含各家牌河），不再只取手牌行。
            # 复用方向探测/快路径已经算好的结果，避免同帧重复检测。
            # 方向已由 _apply_orientation 锁定，也不需要再让识别器
            # 内部做旋转重试（每次重试都是一次完整检测，很贵）。
            if self._cached_rows is not None:
                rows = self._cached_rows
                self._cached_rows = None
            else:
                rows = detector.detect_all_rows(image, allow_rotation=False)

            # 置信过滤 + 牌形降权（低置信牌直接丢弃，宁可不识别也不臆测）。
            #
            # 牌面亮度校验：先看整行牌面是不是"亮底牌"（绝大多数麻将如此）。
            # 只有确认是亮底牌时才启用单格过滤 —— 暗色主题美术下这条判据
            # 不适用，整帧跳过，绝不至于把真牌全砍光（见 MIN_FACE_BRIGHTNESS）。
            bright_all = [_face_brightness(image, r)
                          for row in rows for (r, l, _c) in row if l is not None]
            use_brightness = bool(bright_all) and (
                sum(bright_all) / len(bright_all) >= MIN_FACE_BRIGHTNESS)

            # ===== 冷启动 bootstrap：选「最像手牌行」的那一行，给更低门槛放行 =====
            # 尚未建立稳定手牌时，在 rows 里挑「最靠画面底部 + 11~15 张」的行作为
            # 手牌行候选；只有这一行里的牌允许走 BOOTSTRAP_CONF(0.40) 门槛。
            # 这样真机首帧（噪声大、conf 0.42~0.50）也能攒够票数立住稳定手牌，
            # 之后严格/放宽逻辑正常接管。限定 11~15 张 + 最底部，能避开牌河行
            # （牌河多在屏幕中上部且长度不固定），避免把牌河误当手牌立住。
            bootstrap_row_idx = -1
            if not self._stable_hand_mpsz:
                best_yc = -1.0
                for ri, row in enumerate(rows):
                    if not (11 <= len(row) <= 15):
                        continue
                    ys = [d[0][1] + d[0][3] / 2.0
                          for d in row if len(d[0]) >= 4]
                    if not ys:
                        continue
                    yc = sum(ys) / len(ys)
                    if yc > best_yc:
                        best_yc = yc
                        bootstrap_row_idx = ri

            filtered = []
            for ri, row in enumerate(rows):
                fr = []
                for (r, l, c) in row:
                    lab = self._apply_conf(r, l, c,
                                          bootstrap=(ri == bootstrap_row_idx))
                    if (lab is not None and use_brightness
                            and _face_brightness(image, r) < MIN_FACE_BRIGHTNESS):
                        # 这一格里根本没有牌（是桌面/UI/缝隙），切牌器只是
                        # 按张数先验把它凑成了一张。判为未识别，让它掉出
                        # 张数统计 —— 稳定器会因此保持上一副确定的手牌。
                        lab = None
                    fr.append((r, lab, c))
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

            # 区分手牌行与牌河行（用下标而非对象身份：下面会把手牌行整体
            # 替换成"与稳定手牌对齐"后的新列表，身份比较会失效，进而把
            # 整副手牌误当成牌河算进 discards —— 一个会让剩余牌数归零的坑）。
            hand_row = self._pick_hand_row(voted_rows)
            hand_idx = None
            for _i, _row in enumerate(voted_rows):
                if _row is hand_row:
                    hand_idx = _i
                    break

            # ===== 手牌：本帧原始标签 → 多重集稳定器定夺 =====
            # 稳定器一旦建立起稳定手牌，输出就**永不为空**：单帧抖动、手牌
            # 重排、漏识别都不会让它变空。是否真的变了由"连续帧共识"决定。
            # 这是"界面永远有内容、且不会闪"的根本保证。
            raw_labels: List[str] = []
            if hand_row is not None:
                raw_labels = [d[1] for d in hand_row if d[1] is not None]
            hand_mpsz = self._hand_stab.observe(raw_labels, hsizes, avail)

            # 手牌行的逐张标签与稳定手牌对齐（避免"显示的牌"和"建议打的牌"对不上）
            if hand_idx is not None and hand_mpsz:
                voted_rows[hand_idx] = _reconcile_hand_tiles(
                    voted_rows[hand_idx], hand_mpsz)
                hand_row = voted_rows[hand_idx]

            discard_labels = []
            for _i, row in enumerate(voted_rows):
                if _i == hand_idx:
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

            hand = TileCollection.from_mpsz(hand_mpsz) if hand_mpsz else None

            status = "no_tiles"
            commentary: Optional[str] = None
            shanten: Optional[int] = None
            advice: List[Dict] = []
            best: str = ""
            tile_count = 0

            if hand is not None:
                tile_count = len(hand)
                # 走到这里，稳定器已经保证了三件事：张数合法、无同字刷屏、
                # 连续帧共识。因此**不再做 incomplete 降级** ——
                # 那套"本帧可疑就整帧降级"的旧逻辑是"一会有一会没有"的
                # 另一半根因：UI 收到 status=incomplete 的同时还收到本帧的
                # 脏 hand，于是闪出一副错牌，下一帧又闪回来。
                status = "ok"
                commentary = self.update_trainer(hand)
                shanten, advice = self.build_advice(hand, disc_counts)
                self._stable_hand_mpsz = hand_mpsz
                self._stable_hand_count = tile_count
                # 有合法手牌了，partial 兜底立即让位（否则会与真手牌打架）
                self._partial_mpsz = ""
                self._partial_ttl = 0
            else:
                # 还没建立起任何稳定手牌（冷启动 / 画面里确实没有牌）。
                # 仍然尽量把上一次算好的建议带回去：build_advice 内部对
                # "张数不合法"走复用缓存分支，UI 不会因此空掉。
                if self.trainer is not None:
                    shanten, advice = self.build_advice(
                        TileCollection.from_mpsz(""), disc_counts)

                # ===== 部分识别兜底：消灭"少 1 张 → 整屏空白"的断崖 =====
                # 稳定器只接受张数完全合法（13/14）的帧，真机几乎每帧掉 1 张，
                # 于是永远输出空。这里把本帧手牌行"已经认出来的牌"照实输出。
                # 只做展示：不写 _stable_hand_mpsz、不喂 trainer，不污染建议逻辑。
                try:
                    partial_now = self._labels_to_mpsz(raw_labels, avail)
                except Exception:
                    partial_now = ""
                partial_n = len(partial_now) // 2
                if partial_n >= PARTIAL_MIN_TILES:
                    self._partial_mpsz = partial_now
                    self._partial_ttl = PARTIAL_TTL_FRAMES
                elif self._partial_ttl > 0:
                    # 本帧没认出足够的牌，但滞后窗口内 —— 继续显示上一次的部分结果，
                    # 避免 partial 逐帧闪进闪出（那比空白更难看）。
                    self._partial_ttl -= 1
                else:
                    self._partial_mpsz = ""

                if self._partial_mpsz:
                    status = "partial"
                    tile_count = len(self._partial_mpsz) // 2
                    hand_mpsz = self._partial_mpsz

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

            # ===== 方向自愈：连续 3 帧整帧 0 牌 → 解锁重探方向 =====
            # 用户中途旋转手机/切后台再回来，VirtualDisplay 朝向可能变了，
            # 旧锁定的方向不再适用。此时重新探测，避免永久卡在 0 牌。
            # 必须用**本帧**的检测数，不能用稳定手牌：稳定手牌一旦建立就
            # 永不为空，拿它判 "0 牌" 会让方向自愈永远不触发 —— 用户旋转
            # 手机后就会永久卡在"识别不出来"上。（踩过的坑）
            total_detected = len(raw_labels) + (len(disc_mpsz) // 2)
            if total_detected == 0:
                self._orient_zerocount += 1
                # 熔断：连续 0 牌超过阈值，且重探次数未达上限 → 解锁重探一次。
                # 达上限后停止重探，优雅报告 no_tiles，绝不无限重探致闪退
                # （无限重型 4 方向探测是低内存机型 OOM/SIGSEGV 闪退的主因）。
                # 用户可用悬浮窗「旋转」按钮手动指定方向，绕开自动重探。
                if (self._orient_zerocount >= 3
                        and self._orient_reprobe_count < MAX_ORIENT_REPROBES):
                    print("[engine] 连续 0 牌，解锁方向重探")
                    self._orient = None
                    self._orient_reprobe_count += 1
                    self._last_hand_y = None
                    self._tile_voter.reset()
                    self._hand_stab.reset()
                    self._frame_skipper = _FrameSkipper()
                    self._cached_rows = None
            else:
                self._orient_zerocount = 0
                self._orient_reprobe_count = 0

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

            # ===== 链路诊断 =====
            # 真机"识别不出来"时，光看 count=0 无法判断断在哪一环。这里把
            # 每一环的产出量都带出来，一眼定位：
            #   raw      切牌器切出的牌总数。0 = 根本没找到牌行（多半是朝向错
            #            了，或画面里没有麻将牌）；正常应为 13~40。
            #   rows     每行 [张数, 平均置信, 角色]。看手牌行有没有被挑对、
            #            张数是不是接近 13/14。
            #   raw_hand 本帧手牌行里过了置信门槛的标签数。<13 = 有牌被
            #            置信度砍掉了（ENGINE_MIN_CONF 太高或画质太差）。
            #   stab     稳定手牌是否已建立。
            #   orient   当前锁定的旋转方向；null = 尚未锁定。
            row_stats = []
            for _i, row in enumerate(voted_rows):
                cs = [d[2] for d in row if d[1] is not None]
                row_stats.append([
                    len(row),
                    round(float(sum(cs)) / len(cs), 2) if cs else 0.0,
                    "hand" if _i == hand_idx else "discard",
                ])

            result = {
                "mode": self.mode,
                "mode_name": MODES.get(self.mode, {}).get("name", self.mode),
                "diag": {
                    "raw": sum(len(r) for r in rows),
                    "rows": row_stats,
                    "raw_hand": len(raw_labels),
                    "stab": bool(self._hand_stab.stable_mpsz),
                    "orient": self._orient,
                },
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
                image=_make_preview(full_for_preview),
                result=payload,
                stage=None,
            )
            print(result)
            print(f"Processed in {time.time() - start_time}")
            return res
        except Exception as e:
            traceback.print_exc()
            return _error_result("py_error", f"process 异常: {e}")
