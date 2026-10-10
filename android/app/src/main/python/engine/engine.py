from __future__ import annotations
import concurrent.futures
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
from .match_state import MatchPhaseMachine, empty_phase_view
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
from trainer.utils.shanten import calculate_shanten
from trainer.utils.convert import mpsz_to_tile34_index, tiles34_index_to_mpsz, tile_to_chinese
# 同分牌理裁决链的尾部（两个 analyzer / 知识库已共用同一份；双策略路线主键不同，
# 但 tie 时必须用同一套裁决，否则“极速流卡片”与“主推牌”会在同分上各选一张）。
from discards_tiebreak import tie_tail as _tie_tail
# B-P4：名次差解释与危险行动指令都在最终顺序确定后一次性补上，两个口径都只认
# 一个源头（决胜链 + 档位表），面板不得自己拿 ev 做减法。
from discards_tiebreak import advantage_note as _advantage_note
from probability_bands import (
    danger_advice as _danger_advice,
    danger_is_safe as _danger_is_safe,
    danger_rank as _danger_rank,
)

from modes import (
    load_mode,
    load_advice_config,
    set_config_dir as _modes_set_config_dir,
    set_mode_explicit as _modes_set_mode_explicit,
    hand_sizes,
    available_set,
    MODES,
    is_dingque_mode,
    is_sichuan_family,
    get_mode,
    get_laizi_set,
    get_analyzer,
    native_solver_ready,
)

from platforms import (
    load_platform,
    get_platform,
    get_hand_roi,
    get_river_zones,
    get_supported_modes,
    set_platform_explicit,
    PLATFORMS,
    DEFAULT_PLATFORM,
)
from knowledge_base import KnowledgeBase


# 一局中的合法手牌张数：含摸牌、打牌与吃碰杠副露全合法张数
VALID_HAND_SIZES = (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1)

# 预览图最大宽度。原实现每帧都把全屏截图做 PNG 编码（100~300ms），
# 而 Dart 端并没有使用这张图，纯属浪费，是识别卡顿的主因之一。
# 现在 Dart 端实时展示预览，清晰度必须够用户对准识别框。
# 高 DPI 屏（3x）上 240px 会被拉伸到 720 物理像素而模糊，480px 更清晰。
PREVIEW_MAX_WIDTH = 480

# 输入图最长边上限。超过此值先降采样，避免 OpenCV 在超大图上触发 OOM/SIGSEGV。
MAX_INPUT_LONG_EDGE = 3200

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
# 降到 2.0：画面任何细微手牌位移、摸牌、出牌均能毫秒级触发完整检测，绝不跳过真实对局变化。
FRAME_DIFF_THRESHOLD = 2.0

# 帧差块边长（像素，工作分辨率上）。32 块 = 256 块覆盖整屏，约每块 12x12 work px。
FRAME_DIFF_BLOCK = 32

# 智能跳帧阈值（块均值 L1 距离）：低于此值视为画面冻结。
# 静止画面每块噪声极小、求和后仍很低；任何出牌/摸牌/换 UI 都会让牌河或手牌区
# 突变，求和 diff 远超此值。取值刻意保守——宁可不跳也不错跳，避免旧版本"跳到
# 死在旧建议"的回归；且仅在手牌已稳定、稳定器无待确认变化的对局静默期才启用。
FRAME_SKIP_DIFF_THRESH = 3.0

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

# 三条门槛的大小关系是**合同**，不是各自调的旋钮：
#   ENGINE_MIN_CONF(0.50) > ENGINE_MIN_CONF_RELAX(0.42) >= BOOTSTRAP_CONF(0.40)
# 门槛顺序是「先过严格、过不了才轮到补漏」，所以 RELAX 只在 [RELAX, 0.50) 这段里说话。
# 把 RELAX 当成「数值越大越松」抬到 ≥ 0.50：不会报错、不会让任何精度测试变红，
# 它只会把整条补漏通道删掉 —— 产物是「整张牌读不出来」，与需求恰好相反。
# 现场需要更高召回时用调试页「严格识别门槛」开关（它把整条链路降到 RELAX），
# 而不是改这张表。见 localtest/test_hand_strip_channel_guard.py。

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

# 守恒硬门的自愈阈值：连续这么多帧都卡在同一处「同型超过 4 张」上，就判定那不是
# 真的牌、而是牌河账本里混进的误识别，裁掉它。帧率约 3~5fps，12 帧 ≈ 3 秒：
# 足以让“瞬时的单帧抖动”靠下一帧自洽消失，又不至于让误识别把整局闭嘴。
DIRTY_HEAL_FRAMES = 12

# ---------------------------------------------------------------- 手牌稳定化
# 手牌的语义是「多重集」（13/14 张牌的无序集合），不是有序序列。
# 因此稳定化必须在**牌型计数**层面做，绝不能在位置/序列层面做。
#
# 为什么：每次摸牌/打牌后手牌必然重排，每张牌的 x 平移约一个牌宽（真机
# 实测 148px），远超位置投票的归并半径（0.45×牌高 ≈ 75px）。按位置归并
# 的历史帧会**全部**失配，每个位置只剩最新帧 1 票，低于最低票数被判为
# 「未识别」→ 手牌数从 13 掉到 0 → 悬浮窗整段空白 → 两帧后才恢复。
# 这就是用户看到的「一会显示一会不显示」的决定性根因。
#
# 这三个数是同一条判据的三段，不是三个独立旋钮（历史上它们各说各话，
# 读代码的人只能靠猜 —— 现在 `_HandStabilizer.observe` 直接引用它们）：
#   改动在「正常一巡内」（张数差 ≤ HAND_BIG_JUMP，或同张数但只换
#   ≤ HAND_BIG_JUMP 张牌）→ 首帧即采纳，不等共识帧；
#   陌生的大改但最近 HAND_MODE_WINDOW 帧里见过第二次 → 也采纳（识别器稳定
#   漏同一张牌时永远凑不满连续帧，靠这条兜住）；
#   其余陌生突变必须连续 HAND_CONFIRM_FRAMES 帧一致才采纳 —— 这一档是全部
#   「确认延迟」的唯一来源，而它盖住的恰好只有「对局里不该发生的改法」。
HAND_CONFIRM_FRAMES = 2      # 连续多少帧牌型完全一致才采纳陌生的新牌型
HAND_BIG_JUMP = 4            # 「正常一巡内的改动」上限（摸/打/碰/杠/换牌全部在内）
HAND_MODE_WINDOW = 8         # 证据窗口：最近多少帧
HAND_MODE_VOTES = 2          # 窗口内同一陌生牌型出现这么多次即采纳（含本帧）


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
    # 向量化：正常尺寸（>= 一个块）时一次 reshape+mean 完成全部块均值，
    # 消除旧实现每帧 rows*cols（~500+）次 Python patch.mean 循环解释器开销。
    if h >= bh and w >= bh:
        block = np.ascontiguousarray(work_gray[:rows * bh, :cols * bh])
        sig = block.reshape(rows, bh, cols, bh).mean(axis=(1, 3))
        return sig.ravel().astype(np.float32)
    # 极小图（罕见，仅兜底路径）退回逐块循环，保持与原实现一致的部分块语义。
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
        self._history.append(diff)
        # 只有整屏大幅闪烁/全屏菜单切换（diff >= 85.0）才算真实 spike 避让；
        # 摸牌、打牌、手牌位移等常规对局动作（diff 5~60）必须全力即时识别，绝不阻断与丢弃
        return diff >= 85.0


class _FrameSkipper:
    """帧差去重：相邻帧画面几乎相同时跳过完整识别。

    只跳过 detect()，**不**跳过状态回传（界面仍会按时收到"画面无变化"的心跳）。
    这样 CPU 占用直接砍半，但用户体验不变。
    """

    def __init__(self) -> None:
        self._last_sig: Optional[np.ndarray] = None
        # 本帧签名（`diff()` 里刷新）与「上一次真正做了完整识别的那帧」的锚点签名。
        # 锚点是累计漂移门的根基：`_last_sig` 每帧都在往前推（包括被跳过的帧），
        # 只看相邻差就有一个实在的洞 —— 每帧与前一帧几乎一样、连着几十帧后却已经
        # 面目全非（逐帧挪动的动画、亮度缓慢漂移），相邻差照样过门，旧 payload 就
        # 被无限期地当本帧事实发上屏。
        self._cur_sig: Optional[np.ndarray] = None
        self._anchor_sig: Optional[np.ndarray] = None
        # 缓存上一帧"识别到的手牌 + 标签"——画面不变时整个 process() 直接复用。
        # 缓存的是 EngineResult.result（dict 序列化形式），不是 EngineResult 对象本身，
        # 因为后者带 cv2 图像，跨调用持有可能让 Chaquopy 释放不及时。
        self._last_payload: Optional[str] = None
        self._last_top_score: float = 0.0

    def diff(self, work_gray: np.ndarray) -> float:
        self._cur_sig = None            # 算不出来就留在 None：宁可不跳，不拿旧值当“没变”
        cur = _block_diff_signature(work_gray)
        self._cur_sig = cur
        if self._last_sig is None or self._last_sig.shape != cur.shape:
            self._last_sig = cur
            self._anchor_sig = cur
            return float("inf")  # 首帧肯定不跳
        diff = float(np.abs(cur - self._last_sig).sum())
        self._last_sig = cur
        return diff

    @property
    def anchor_drift(self) -> float:
        """本帧相对锚点（上一次真正识别的帧）累计变了多少。

        没锚点/本帧签名算不出来都返 inf（等于强制完整识别，绝不拿猜的值当“没变”）。
        这比“最多跳 N 帧”那种按帧数封顶更强：按帧数封顶时，每帧变化量只要刚好低于
        相邻阈值，100 帧也能始终“看起来没变”；而这里上限就是同一个 3.0，跟帧数无关。"""
        if self._cur_sig is None or self._anchor_sig is None:
            return float("inf")
        if self._cur_sig.shape != self._anchor_sig.shape:
            return float("inf")
        return float(np.abs(self._cur_sig - self._anchor_sig).sum())

    def set_baseline(self, work_gray: np.ndarray) -> None:
        """把基线签名改写成「本帧真正被识别的那张图」（不返回差值）。

        为什么需要：方向验证现在跑在帧差之后，若它推翻了锁定的朝向（反向横屏
        自愈那一类），本帧的 diff 是在旧朝向裁片上算的，基线也就留在了旧朝向上
        —— 下一帧会拿旧朝向的裁片当参照，凭空再付一次完整识别。补一次基线刷新
        （≈1ms）就把这个尾巴收掉。稳态下验证返回的就是同一张图，这条是纯 no-op。

        锚点跟着一起换：这一帧完整识别过了，它的图就是新的“上次真识过的帧”。

        `_cur_sig` 也必须一起换（实测踩过的坑）：本帧末尾 `remember` 会拿 `_cur_sig`
        推进锚点，而 `_cur_sig` 还是**旧朝向**裁片的签名 —— 不换的话锚点就被写回
        成错朝向的图，下一帧的累计漂移是「新朝向 vs 旧朝向」（实测 7000+ 量级），
        漂移门永远过不去，每次方向自愈之后的所有帧都退化成完整识别。
        """
        sig = _block_diff_signature(work_gray)
        self._last_sig = sig
        self._anchor_sig = sig
        self._cur_sig = sig

    def remember(self, payload: str, top_score: float) -> None:
        self._last_payload = payload
        self._last_top_score = top_score
        # 这一帧真的被完整识别了，它就成了新的锚点（漂移从零重新计）。
        # 只在签名新鲜时推进：签名算不出来那一帧 `_cur_sig` 是 None，不能拿旧签名充新。
        if self._cur_sig is not None:
            self._anchor_sig = self._cur_sig

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


def parse_mpsz_tiles(mpsz: str) -> List[str]:
    """解析 mpsz 字符串为单个牌标签列表，例如 '123m' -> ['1m', '2m', '3m']，'1m2m3m' -> ['1m', '2m', '3m']。"""
    tiles = []
    cur_nums = []
    for ch in mpsz:
        if ch.isdigit():
            cur_nums.append(ch)
        elif ch in "mpsz":
            for n in cur_nums:
                tiles.append(f"{n}{ch}")
            cur_nums = []
    return tiles


def _mpsz_to_counter(mpsz: str) -> Counter:
    """mpsz 串 → 牌型计数（已是排序归一化的，可直接当字典键比较）。"""
    return Counter(parse_mpsz_tiles(mpsz))


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
MIN_FACE_BRIGHTNESS = 80.0


# 局中连续多少帧读到 0 张才允许硬重置整局状态。
#
# 为什么不是 3：按本仓实测的单帧识别耗时 1.2~2.5s，3 帧 = 3.6~7.5s，而重置后
# warmup 重建还要再 3 帧 —— 于是面板会出现 3~6 秒的「等待牌局开始」空窗，而这
# 在用户眼里就是「识别莫名其妙的突然坏了」。格网偶发错位一帧就足够凑齐这 3 帧。
# 5 帧是经验平衡：结算/回大厅的真局尾通常 1~2 帧内就会被 `_is_mahjong_table`
# 拦下（那条路不依赖本计数），本 fuse 只兜「弹窗盖住手牌但画面仍像牌桌」这一种。
EMPTY_HAND_RESET_FRAMES = 5


def _face_brightness(image: CVImage, rect) -> Optional[float]:
    """牌面中心区域的灰度均值（避开边框与相邻牌的缝隙）。

    返回 None 表示「这张图里放不下这个框」= **没有意见**，绝不能当成「牌面全黑」。

    为什么这一条必须单独改：旧写法越界时 `return 0.0`，而 0.0 与「真·黑牌面」
    完全同值，调用方分不清。真机报障实测正是这个形状 —— rows 是整屏坐标
    （y≈716）而喂进来的图是 hand_roi 条带（高 261），于是 `ih = 261-741 < 0`，
    **整行每一格都读到 0.0** → 行级自适应判定「这行牌都很暗」→ `use_brightness
    = False` → 防伪闸门被整体关掉 → 头像、副露区这些非牌格子全部放行（面板上
    凭空多出的 7z/2p 就是这么来的）。一个越界读数把「误杀真牌」的保险丝换成了
    「放过假货」，两头都是坑。
    """
    try:
        x, y, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
        ih_, iw_ = image.shape[:2]
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x >= iw_ or y >= ih_:
            return None
        ix = max(0, x + int(w * 0.15))
        iy = max(0, y + int(h * 0.15))
        iw = min(int(w * 0.7), iw_ - ix)
        ih = min(int(h * 0.7), ih_ - iy)
        if iw <= 0 or ih <= 0:
            return None
        patch = image[iy:iy + ih, ix:ix + iw]
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
        return float(gray.mean())
    except Exception:
        return None


# 跳帧不按「连续跳过几帧」封顶，而是按**累计漂移**封顶：帧差门只比相邻两帧，而缓慢
# 漂移（逐帧挪动的动画、亮度渐变）会每帧都刚好低于阈值，无限期复用旧 payload。
# 所以真正的门在 `_FrameSkipper.anchor_drift`（相对上一次真正识别的那帧也没变过
# FRAME_SKIP_DIFF_THRESH）。历史上这里是一个叫 `MAX_SKIP_FRAMES = 2` 的常量，
# 注释写着“最多跳 2 帧就强制全量重检”却从没被任何代码读过——“注释承诺但没接线”
# 本身就是坑；而按帧数封顶要么逼静止画面周期性重付一次完整识别（发热降频），
# 要么照样挡不住慢慢漂，于是换成不花额外开销的漂移门。该常量已删除，不许再接回
# 任何判据（`localtest/test_skip_frame_guard.py` 把它当负契约钉住）。


class _HandStabilizer:
    """手牌多重集稳定器 —— 消除"一会显示一会不显示"的核心。

    手牌在语义上是**多重集**（13/14 张牌的无序集合），不是有序序列。
    所以稳定化必须在牌型计数层面做：把每帧的手牌行标签转成 Counter，
    再对"最近若干帧的 Counter"求共识。这样做之后，

      - 手牌重排 / 整行平移 / 摸牌插入 —— 完全不影响（Counter 不变）
      - 单帧漏识别 1 张    —— 该帧张数不合法，直接不参与共识，稳定值不动
      - 单帧把某张认错     —— 该帧 Counter 与前后都不同，拿不到共识，稳定值不动

    采纳通道（满足任一即把稳定手牌换成本帧牌型），按「对局里多常见」排：
      1) 张数切换 1~HAND_BIG_JUMP 张（摸 13→14、打 14→13、碰 −3、杠 −4）
         → **首帧即采纳**，一帧确认都不等；
      2) 同张数且只换了 ≤ HAND_BIG_JUMP 张（换三张、单张被认错后自愈）
         → 同样首帧即采纳；
      3) 这个陌生牌型在最近 HAND_MODE_WINDOW 帧里已经出现过
         HAND_MODE_VOTES 次（含本帧）→ 采纳（识别器稳定漏同一张牌时
         永远凑不满连续帧，靠这条兜住）；
      4) 其余陌生突变（同张数、一次改 >HAND_BIG_JUMP 张、窗口内没见过）
         → 必须连续 HAND_CONFIRM_FRAMES 帧完全一致才采纳。

    为什么 4) 不能再往「更快」调：那三条快速通道已经把对局里**所有**常规事件
    全接住了，卡在 4) 上的只剩「对局里不该发生」的改法 —— 而它的典型画像就是
    误识别爆发（把整行刷成同一种牌、或牌桌换了却没切到新局）。把它降到 1 帧
    等于拿「phantom 读数直接上屏」换响应速度，而 phantom 正是用户报的「乱识别
    不存在的东西」。本文件里能动的延迟只剩「每帧多快跑一次」，不是「这里等几帧」
    （那条改动已接在跳帧与方向熔断里）。见 localtest/test_hand_response_guard.py。

    输出的 mpsz **永不为空**（除非从未识别到过合法手牌）——这正是
    "识别不出来时界面也不要空着"的保证。
    """

    def __init__(self) -> None:
        # 当前稳定手牌（保留手牌在屏幕上从左到右的真实物理顺序）
        self.stable_mpsz: str = ""
        self._stable_key: str = ""
        # 连续多少帧给出了同一个牌型
        self._streak: int = 0
        self._last_key: str = ""
        # 最近若干帧的合法牌型（供众数兜底）
        self._recent: deque = deque(maxlen=HAND_MODE_WINDOW)
        self.pending: bool = False
        # 两个空帧计数器：必须在 __init__ 就立住（`reset()` 早就在写它们，而
        #   `observe()` 靠 `getattr(..., 0)` 取 —— 属性不在构造时就存在是「只能
        # 靠默认值兼容」的写法，一旦有人把 getattr 换成直接取属性就报错）。
        #   `_empty_frames`：读到 0 张的连续帧数（空帧宽限，见 observe 开头）；
        #   `_empty_streak`：手牌区连不出合法手牌的连续帧数（局末/大厅重置）。
        self._empty_frames: int = 0
        self._empty_streak: int = 0

    def reset(self) -> None:
        """玩法切换 / 朝向变化时硬重置：旧手牌与新场景无关。"""
        self.stable_mpsz = ""
        self._stable_key = ""
        self._streak = 0
        self._last_key = ""
        self._recent.clear()
        self.pending = False
        self._empty_streak = 0
        self._empty_frames = 0

    def observe(self, labels, valid_sizes, avail=None) -> str:
        """喂入本帧手牌行的标签列表，返回应当对外输出的从左到右稳定 mpsz。

        空帧宽限：n==0 不再立即清空稳定手牌，而是保留上一稳定值并把
        _empty_frames 计数交给引擎（由引擎降级为 partial 呈现）；连续
        EMPTY_GRACE_FRAMES(4) 帧空才真正重置，杜绝单帧漏识别闪"未检测到手牌"。"""
        cnt: Counter = Counter()
        ordered_list = []
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
            ordered_list.append(lab)

        n = sum(cnt.values())
        ordered_mpsz = "".join(ordered_list)

        if n == 0:
            # 空帧宽限：保留上一稳定手牌，连续 EMPTY_GRACE_FRAMES 帧空才彻底重置
            self._empty_frames = getattr(self, "_empty_frames", 0) + 1
            if getattr(self, "_empty_frames", 0) >= 4:
                self.reset()
            return self.stable_mpsz

        self._empty_frames = 0

        if n not in valid_sizes and n < 4:
            self._empty_streak = getattr(self, "_empty_streak", 0) + 1
            if self._empty_streak >= 2:
                # 连续 2 帧手牌区无有效手牌（对局结束/未开局/大厅）：彻底重置稳定手牌，绝不跨局残留
                self.stable_mpsz = ""
                self._stable_key = ""
                self._streak = 0
                self._last_key = ""
                self.pending = False
                return ""
            # 未达 2 帧（玩家出牌手指遮挡或摸打过渡）：平滑维持前序稳定手牌，杜绝闪烁空白
            return self.stable_mpsz
        else:
            self._empty_streak = 0

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
        if key == getattr(self, "_stable_key", ""):
            self.pending = False
            return self.stable_mpsz

        # 冷启动：还没有任何稳定手牌时
        if not self.stable_mpsz:
            if n not in valid_sizes:
                return ""
            # 合法张数 → 首帧立即采信并进入稳定态，杜绝首帧延迟与"等待"提示卡顿。
            # （旧写法在这里又写了一遍 `n in valid_sizes or self._streak >= 2`：
            # 上一行已经保证前件为真，那个 `or` 是从不生效的死分支，留着会让人
            # 以为冷启动也可能要等 2 帧。）
            self.stable_mpsz = ordered_mpsz
            self._stable_key = key
            self.pending = False
            return self.stable_mpsz

        # 摸牌(13->14)、打牌(14->13)、吃碰杠副露 (diff in 1..HAND_BIG_JUMP) 或
        # 单张/换三张变动 (diff <= HAND_BIG_JUMP)：首帧即时响应刷新，杜绝出牌/摸牌后的迟钝与卡顿
        hand_len = len(self.stable_mpsz) // 2
        is_count_move = (0 < abs(n - hand_len) <= HAND_BIG_JUMP
                         and n in valid_sizes)
        is_tile_swap = (n == hand_len and n in valid_sizes
                        and _hand_diff_count(key, self._stable_key)
                        <= HAND_BIG_JUMP)
        mode_hits = self._recent.count(key)
        # 四条采纳通道（逐条对上面的类 docstring，改动必须两边一起看）：
        #   1) is_count_move   2) is_tile_swap   3) 窗口内见过第二次
        #   4) 连续 HAND_CONFIRM_FRAMES 帧给出同一牌型
        # 注：旧实现的 `self._streak >= 1` 短路使 HAND_CONFIRM_FRAMES=2 共识形同虚设
        # （任意新牌型首帧即采纳），删掉后只有上面四条还成立，其中三条盖的都是
        # 对局里真会发生的事件；剩下要等帧的只有「对局里不该发生」的陌生大改。
        if (is_count_move or is_tile_swap
                or mode_hits >= HAND_MODE_VOTES
                or self._streak >= HAND_CONFIRM_FRAMES):
            self.stable_mpsz = ordered_mpsz
            self._stable_key = key
            self.pending = False
        else:
            # 看到了新牌型但证据还不够 —— 通知引擎下一帧必须真跑一次，别被跳过
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
      - 防御：若当前行识别结果与稳定手牌差异巨大（>8 张牌不同），说明刚发生换三张或
        重开局，二者处于异步切换期，绝不强制覆盖，保留当前识别以防乱套。
    """
    if not stable_mpsz or not row:
        return row
    row = sorted(row, key=lambda d: d[0][0])
    cnt_row = Counter(d[1] for d in row if d[1] is not None)
    cnt_stable = _mpsz_to_counter(stable_mpsz)
    if cnt_row == cnt_stable:
        return row
    diff = sum((cnt_row - cnt_stable).values()) + sum((cnt_stable - cnt_row).values())
    if diff > 8:
        return row
    labels = [stable_mpsz[i:i + 2] for i in range(0, len(stable_mpsz), 2)]
    out = []
    for i, (rect, lab, conf) in enumerate(row):
        out.append((rect, labels[i] if i < len(labels) else lab, conf))
    return out


def reconcile_mode_platform(mode: str, platform: str) -> Tuple[str, Optional[str]]:
    """玩法**按用户声明的来**，返回 (生效玩法, 该平台房卡未收录的玩法或 None)。

    第二项不再是「被纠正掉的那个玩法」，而是「一句提示」：引擎不替用户换玩法。

    为什么改口径（2026-10 实测，`localtest/measure_mode_coverage.py`）：这里曾经拿
    `supported_modes` 当硬闸门，把列表外的玩法换成平台默认，理由是「那个组合在本平台
    读不出牌」。量完发现这条理由站不住：主 bank（手绘 34 面）在**每个**平台都常驻
    （`STYLE_PLATFORM_DENYLIST` 只做定点减法、永不踢主 bank），34/34 面家家都有——
    「读不出」不是能力事实，而是那份手写房卡清单的猜测。代价实打实落在用户身上：
    玩法列表从 19 种被砍到 3~6 种，且主动选了也会被改回去。

    保留一句提示（而不是把判据删干净），是因为它仍是一条真信号：你选的玩法不在这家
    平台的常见房卡里。识别照你选的牌集走，但算分口径与实桌不一致时要自己核对。

    跨平台残留的旧玩法不靠这里兜：切平台时由 UI 把玩法带到新平台的默认玩法（主页
    `_selectPlatform` 与悬浮窗菜单同一条路径），那才是真知道「用户刚刚换了平台」的位置。
    """
    supported = get_supported_modes(platform)
    if not supported or mode in supported:
        return mode, None
    return mode, mode


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
        "phase_label": "画面识别中",
        "tactical_badge": "识别中",
        "tactical_intent": "正在对齐画面与牌局状态…",
        "shanten": None,
        "advice": [],
        "commentary": None,
        "tiles": [],
        "top_score": 0.0,
        "screen": [0, 0],
        "elapsed": 0.0,
        "message": str(message)[:200],
        # 局况事实层的错误帧出口：没有牌桌读数就只能如实说“暂无局况”。schema 必须
        # 与正常帧逐键一致，否则悬浮窗在这条路径上读到 null 会把整条局况弄丢。
        "match_phase": empty_phase_view("画面识别异常，暂无局况"),
        # 错误帧不喂 native：拿不到可靠手牌时让 C++ 侧继续推算只会覆写脏结果。
        "native_ready": False,
    }
    return EngineResult(
        image=np.zeros((1, 1, 3), dtype=np.uint8),
        result=json.dumps(result),
        stage=None,
    )


def _build_tactical_perception(
    status: str,
    hand_mpsz: str,
    count: int,
    is_drawing: bool,
    drawing_tile: Optional[str] = None,
    shanten: Optional[int] = None,
    is_swap_phase: bool = False,
    is_dq_phase: bool = False,
    is_pick_phase: bool = False,
    swap_advice: Optional[dict] = None,
    rec_suit_name: Optional[str] = None,
    advice: Optional[list] = None,
    best: str = "",
    ting_details: Optional[list] = None,
    mood: Optional[dict] = None,
    danger_flow: Optional[dict] = None,
) -> Tuple[str, str, str]:
    """构建高精度局势感知与下一步战术意图。
    返回: (phase_label, tactical_badge, tactical_intent)
    - phase_label: 当前牌局所处状态（“现在在干什么”）
    - tactical_badge: 紧凑徽标标签（用于 UI 状态微章）
    - tactical_intent: 下一步推荐战术意图（“准备要干什么”）
    """
    if is_swap_phase or status == "swap":
        phase_label = "换三张优化"
        tactical_badge = "换三张"
        if isinstance(swap_advice, dict) and swap_advice.get("tiles"):
            tiles_cn = [tile_to_chinese(t) for t in swap_advice.get("tiles", [])]
            tactical_intent = f"准备换出【{'、'.join(tiles_cn)}】，优化起手门子结构"
        else:
            tactical_intent = "正在评估起手牌型，准备选出三张劣势同门牌换出"
        return phase_label, tactical_badge, tactical_intent

    if is_dq_phase or status == "dingque":
        phase_label = "定缺选门"
        tactical_badge = "定缺抉择"
        if rec_suit_name:
            tactical_intent = f"建议定缺【{rec_suit_name}】门（弱门清整，全力做大优势门）"
        else:
            tactical_intent = "正在评估各门手牌厚度，准备打缺牌张最少的一门"
        return phase_label, tactical_badge, tactical_intent

    if is_pick_phase or status == "pick":
        phase_label = "选牌操作中"
        tactical_badge = "选牌决断"
        tactical_intent = "正在识别候选牌张，请在界面弹窗中确认选牌"
        return phase_label, tactical_badge, tactical_intent

    if status in ("waiting", "no_tiles") or (count == 0 and not hand_mpsz):
        return "", "", ""

    # 对局进行中
    advice_list = advice if isinstance(advice, list) else []
    top_adv = advice_list[0] if (advice_list and isinstance(advice_list[0], dict)) else {}
    best_tile = best or top_adv.get("tile", "")
    best_cn = tile_to_chinese(best_tile) if best_tile else ""
    is_turn = bool(is_drawing or (count % 3 == 2))

    if is_turn:
        # 我方摸牌轮 / 待打决断
        if shanten == 0:
            phase_label = "摸牌决断 · 听牌决胜"
            tactical_badge = "听牌决胜"
            ting_tiles = top_adv.get("ting_tiles", [])
            ukeire = top_adv.get("ukeire", 0)
            if ting_tiles and best_cn:
                ting_cn = "/".join(tile_to_chinese(t) for t in ting_tiles[:4])
                tactical_intent = f"建议切【{best_cn}】，锁定【{ting_cn}】胡牌叫口 (待牌{ukeire}张)"
            elif best_cn:
                tactical_intent = f"建议切【{best_cn}】，锁定听牌胜势，静候胡牌"
            else:
                tactical_intent = "当前已听牌，选择最优叫口锁定胜势"
        elif shanten == 1:
            phase_label = "摸牌决断 · 进听冲刺"
            tactical_badge = "进听冲刺"
            ukeire = top_adv.get("ukeire", 0)
            if best_cn and ukeire > 0:
                tactical_intent = f"建议切【{best_cn}】，锁定最大有效进张 (进张{ukeire}张直接下叫)"
            elif best_cn:
                tactical_intent = f"建议切【{best_cn}】，拆解孤张全力冲刺听牌"
            else:
                tactical_intent = "全力冲刺听牌，保留核心好搭"
        else:
            # C18：旧行为在这里直接 `return "", "", ""` —— 两向听以上（或算不出向听）
            # 就什幺都不写。用户报的「广东麻将那几帧战术标签全空」就是它：那几副牌
            # 离听牌远，全部落在这一支。空标签不等于「没有战术」：牌型还在推进就按
            # 推进说；算不出向听就直说算不出。不编一个档位给人看，也不留一片空白。
            if shanten is None:
                phase_label = "牌型推演中"
                tactical_badge = "推演中"
                tactical_intent = "本帧未取得向听结论，不给档位判断"
            else:
                phase_label = f"牌型推进 · {shanten}向听"
                tactical_badge = f"{shanten}向听"
                if best_cn:
                    tactical_intent = f"建议切【{best_cn}】，优先拆解孤张、保留可控搭子"
                else:
                    tactical_intent = "牌型尚在布局，优先保留可控搭子、拆去孤张"
    else:
        # 候牌轮 / 手牌 13 张等摸或等碰
        if shanten == 0:
            phase_label = "听牌守株 · 待胡中"
            tactical_badge = "已下叫"
            t_list = []
            if ting_details and isinstance(ting_details, list):
                t_list = [tile_to_chinese(td.get("tile", "")) for td in ting_details[:4] if td.get("tile")]
            elif top_adv.get("ting_tiles"):
                t_list = [tile_to_chinese(t) for t in top_adv["ting_tiles"][:4]]
            if t_list:
                tactical_intent = f"当前已下叫！候胡【{'/'.join(t_list)}】，静候自摸或点炮"
            else:
                tactical_intent = "当前已下叫听牌！阵型稳固，静候胡牌张"
        elif shanten == 1:
            phase_label = "一向听待命 · 候牌中"
            tactical_badge = "一向听"
            tactical_intent = "等待摸牌，摸进关键张即可下叫"
        else:
            # 同上 C18：深向听/未知也要说清现在在干什么，而不是留一片空白。
            if shanten is None:
                phase_label = "牌型推演中"
                tactical_badge = "推演中"
                tactical_intent = "本帧未取得向听结论，不给档位判断"
            else:
                phase_label = f"牌型推进 · {shanten}向听候牌"
                tactical_badge = f"{shanten}向听"
                tactical_intent = "牌型尚在布局，优先保留可控搭子、拆去孤张"

    return phase_label, tactical_badge, tactical_intent



# annotate_advice_decisions 会写、也必须每次重写的派生字段。
# 为什么非得先清：analyzer 命中缓存时返回的是同一批 dict 对象，上一帧写进去的
# 名次/危险提示/预演行会留在对象里。候选变少时末位会带着上一帧的「优于次选 X」，
# 14 张帧会留着上一帧 13 张的「摸牌预演」——那是凭空的伪造，必须先删再写。
# 公开名字（不带下划线）是刻意的：测试要拿这份清单逐键断言「上一帧的派生字段
# 必须被清掉」，私有名让测试无法引用，守卫就会退化成手抄一份常量——一改就漏。
DECISION_KEYS = (
    "rank", "rank_total", "advantage", "advantage_reason",
    "danger_hint", "safer_alternatives",
    "predraw_simulated", "predraw_kept", "predraw_line", "predraw_flip_line",
    "predraw_flips",
)


def annotate_advice_decisions(advice: List[Dict], best: str) -> Dict:
    """B-P4 实时决策空白补全：给每条建议补上三件事（就地改 advice）。

    1. 空白 A — `rank` / `advantage` / `advantage_reason`：为什么排在它前面。
    2. 空白 C — `danger_hint` / `safer_alternatives`：这一档风险到底该不该换牌，
       以及换成哪张（只从安全/微危里挑，不给“更安全”的假保证）。
    3. 空白 D — `predraw_line` / `predraw_flip_line`：摸到什么牌会改主意。
       只引用 analyzer 真的跑过的摸牌情景（川麻 13 张预摸路径），
       没有模拟数据一个字段也不写；std 家族没有预摸路径，面板据此不渲染该行。

    为什么放在这一层（不是 analyzer 里）
    --------------------------------
    analyzer 排完序后还有两道会改顺序/改字段的环节：defense_radar 回填
    `defense_level`、`KnowledgeBase.evaluate_tactics` 战术加权重排（都在本文件）。
    名次相关的句子如果在 analyzer 里就写好，到面板上就会与真实顺序对不上
    （“优于次选 3p”而次选已经变成 5m）。所以必须在**最终顺序确定之后、序列化
    之前**一次性算完，并按面板的同一个取首规则对齐顺序。

    口径约束（与 B-P3 一致）：不拿 ev 做减法给用户看（那是合成评分，没有量纲）；
    档位措辞与次序全部走 `probability_bands` / `discards_tiebreak` 的单一来源。
    返回计数统计（走 diag）：字段没接上时 `advantage=0` 会直接暴露出来，
    不会退化成“面板少了一行”那种静默漂移。
    """
    stats = {"n": 0, "advantage": 0, "danger_hint": 0, "alternatives": 0,
             "predraw": 0, "error": None}
    try:
        if not advice:
            return stats
        # 不改动传入列表：`build_advice` 命中缓存时返回的就是 `self._advice` 本体，
        # 就地重排会把引擎缓存的候选序改掉（实测下一帧的 `best` 会跟着漂），
        # 而名次只与展示有关。取首在副本上做，面板那边也会做同样的事。
        ordered = list(advice)
        for item in ordered:
            if isinstance(item, dict):
                for k in DECISION_KEYS:
                    item.pop(k, None)
        if best:
            bi = next((i for i, a in enumerate(ordered)
                       if isinstance(a, dict) and a.get("tile") == best), -1)
            if bi > 0:
                ordered.insert(0, ordered.pop(bi))
        n = len(ordered)
        stats["n"] = n

        for i, item in enumerate(ordered):
            if not isinstance(item, dict):
                continue
            item["rank"] = i + 1
            item["rank_total"] = n

            # ---- 空白 A：相对优势（只与紧排在自己后面的那一条比）----
            nxt = ordered[i + 1] if i + 1 < n else None
            if isinstance(nxt, dict):
                note = _advantage_note(item, nxt)
                tb = str(nxt.get("tile") or "")
                head = (f"与次选 {tb} 等价" if note.get("equivalent")
                        else f"优于次选 {tb}")
                item["advantage"] = note
                item["advantage_reason"] = f"{head}：{note['note']}"
                stats["advantage"] += 1

            # ---- 空白 C：危险分级行动指令 + 替代方案 ----
            flow = item.get("danger_flow")
            if isinstance(flow, dict) and flow.get("danger_level"):
                lvl = str(flow["danger_level"])
                item["danger_hint"] = _danger_advice(lvl)
                stats["danger_hint"] += 1
                # 只有主推且已到中危及以上才找替代：主推本来就安全时拉一行
                # 「建议优先选低危出张」是误报，会把用户推去改一个不需要改的决定。
                if i == 0 and _danger_rank(lvl) >= _danger_rank("medium"):
                    alts = []
                    for other in ordered[1:]:
                        if not isinstance(other, dict) or not other.get("tile"):
                            continue
                        of = other.get("danger_flow")
                        if not isinstance(of, dict):
                            continue
                        if _danger_is_safe(str(of.get("danger_level") or "")):
                            alts.append(other)
                    # 稳定排序：同安全档内保留原名次（名次已经按牌理排过）
                    alts.sort(key=lambda d: _danger_rank(
                        str((d.get("danger_flow") or {}).get("danger_level") or "")))
                    if alts:
                        item["safer_alternatives"] = [
                            {"tile": str(d.get("tile")),
                             "danger_level": str((d.get("danger_flow") or {})
                                                 .get("danger_level") or ""),
                             "danger_band": str((d.get("danger_flow") or {})
                                                .get("danger_band") or "")}
                            for d in alts[:3]]
                        stats["alternatives"] += 1

            # ---- 空白 D：摸牌预演（只给主推一条，避免面板重复同一信息）----
            pd = item.get("predraw")
            if i == 0 and isinstance(pd, dict):
                scen = pd.get("scenarios") or []
                cur = str(item.get("tile") or "")
                kept = 0
                groups: Dict[str, List[str]] = {}
                order: List[str] = []
                for row in scen:
                    if not isinstance(row, (list, tuple)) or len(row) < 3:
                        continue
                    draw, rem, to = str(row[0]), int(row[1] or 0), str(row[2])
                    if not to:
                        continue
                    if to == cur:
                        kept += 1
                        continue
                    if to not in groups:
                        groups[to] = []
                        order.append(to)
                    groups[to].append(f"{tile_to_chinese(draw)}·余{rem}张")
                if scen:
                    item["predraw_simulated"] = len(scen)
                    item["predraw_kept"] = kept
                    # 没算到的情景如实标出来：预摸只取前 6 种高概率摸牌，
                    # 不写这一句，用户会把「6 种里 4 种不改」当成全部可能性。
                    omit = int(pd.get("considered_kinds") or 0) - len(scen)
                    tail = f"（另有 {omit} 种可联络摸牌未参与模拟）" if omit > 0 else ""
                    item["predraw_line"] = (f"预演 {len(scen)} 种摸牌："
                                            f"{kept} 种仍打 {tile_to_chinese(cur)}{tail}")
                    if order:
                        to0 = order[0]
                        extra = len(order) - 1
                        item["predraw_flip_line"] = (
                            f"若摸到 {'、'.join(groups[to0][:3])} 将改打 "
                            f"{tile_to_chinese(to0)}"
                            + (f"（还有 {extra} 种其他改法）" if extra > 0 else ""))
                        item["predraw_flips"] = [{"to": t, "draws": groups[t]}
                                                 for t in order]
                    stats["predraw"] += 1
    except Exception as exc:      # 降级可以发生，但必须可见（写进 diag）
        stats["error"] = f"{type(exc).__name__}: {exc}"
    return stats


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


# NOTE: 像素启发式判「自噬」已被实测证伪并移除（见 detect_dingque 上方说明）。

# 缺门花色名（0=万 1=筒 2=条）：徽章读数、手写 override、稳定门共用这一张表。
DINGQUE_SUIT_NAMES = ('万', '筒', '条')

# 徽章读数的时间稳定窗，单位=**扫描次数**（不是帧数：扫描本身按 4 帧节流，
# 真机一帧≈25~80ms、一个识别周期≈1s，故 3 次扫描≈最近 3 秒的取区证据）。
DQ_STABLE_WINDOW = 3


def detect_dingque(screen_img: np.ndarray) -> Tuple[Optional[int], Optional[str]]:
    """检测四川麻将定缺徽章（头像右上角）。

    返回 (suit_idx, suit_name)：
      suit_idx: 0=万, 1=筒, 2=条
      suit_name: '万', '筒', '条'
    未检测到返回 (None, None)。
    """
    try:
        h, w = screen_img.shape[:2]
        # 定缺徽章位于左下角头像右上侧 (x in 8.8%~13.8%, y in 58.8%~67.5%)
        sx = int(w * 0.088)
        ex = int(w * 0.138)
        sy = int(h * 0.588)
        ey = int(h * 0.675)
        badge_crop = screen_img[sy:ey, sx:ex]
        if badge_crop.size == 0 or badge_crop.shape[0] < 10 or badge_crop.shape[1] < 10:
            return None, None
        hsv = cv2.cvtColor(badge_crop, cv2.COLOR_BGR2HSV)

        green_mask = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
        red_mask1 = (hsv[:, :, 0] >= 0) & (hsv[:, :, 0] <= 10) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
        red_mask2 = (hsv[:, :, 0] >= 170) & (hsv[:, :, 0] <= 180) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
        red_mask = red_mask1 | red_mask2
        blue_mask = (hsv[:, :, 0] >= 95) & (hsv[:, :, 0] <= 135) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
        yellow_mask = (hsv[:, :, 0] >= 14) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 80)

        g_cnt = int(np.sum(green_mask))
        r_cnt = int(np.sum(red_mask))
        b_cnt = int(np.sum(blue_mask))
        y_cnt = int(np.sum(yellow_mask))

        # 真实定缺徽章为集中大色块，而金杯/等级常驻底噪仅 (y~240, b~60)。
        # 万(红) >= 200 为主色；条(绿) >= 200 为主色；
        # 筒(蓝/黄)必须达到 b >= 200 或 y >= 450，远超常驻金杯/等级底噪。
        if r_cnt >= 200 and r_cnt > g_cnt and r_cnt > max(b_cnt, y_cnt):
            return 0, '万'
        elif g_cnt >= 200 and g_cnt > r_cnt and g_cnt > max(b_cnt, y_cnt):
            return 2, '条'
        elif (b_cnt >= 200 or y_cnt >= 450) and max(b_cnt, y_cnt) > max(g_cnt, r_cnt):
            return 1, '筒'
        return None, None
    except Exception:
        return None, None


def _detect_badge_suit(badge_crop: np.ndarray) -> Optional[int]:
    if badge_crop is None or badge_crop.size == 0 or badge_crop.shape[0] < 8 or badge_crop.shape[1] < 8:
        return None
    hsv = cv2.cvtColor(badge_crop, cv2.COLOR_BGR2HSV)
    green_mask = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
    red_mask1 = (hsv[:, :, 0] >= 0) & (hsv[:, :, 0] <= 10) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
    red_mask2 = (hsv[:, :, 0] >= 170) & (hsv[:, :, 0] <= 180) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
    red_mask = red_mask1 | red_mask2
    blue_mask = (hsv[:, :, 0] >= 95) & (hsv[:, :, 0] <= 135) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 60)
    yellow_mask = (hsv[:, :, 0] >= 14) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 80)

    g_cnt = int(np.sum(green_mask))
    r_cnt = int(np.sum(red_mask))
    b_cnt = int(np.sum(blue_mask))
    y_cnt = int(np.sum(yellow_mask))

    if r_cnt >= 200 and r_cnt > g_cnt and r_cnt > max(b_cnt, y_cnt):
        return 0
    elif g_cnt >= 200 and g_cnt > r_cnt and g_cnt > max(b_cnt, y_cnt):
        return 2
    elif (b_cnt >= 200 or y_cnt >= 450) and max(b_cnt, y_cnt) > max(g_cnt, r_cnt):
        return 1
    return None


def detect_opponents_dingque(screen_img: np.ndarray) -> List[int]:
    """检测场上三位对手的定缺徽章（右家/对家/左家），返回对手定缺门列表 [suit_idx, ...]"""
    try:
        h, w = screen_img.shape[:2]
        rois = [
            (int(w * 0.86), int(w * 0.96), int(h * 0.32), int(h * 0.48)),
            (int(w * 0.26), int(w * 0.40), int(h * 0.06), int(h * 0.20)),
            (int(w * 0.60), int(w * 0.74), int(h * 0.06), int(h * 0.20)),
            (int(w * 0.04), int(w * 0.14), int(h * 0.26), int(h * 0.40)),
        ]
        suits = []
        for (sx, ex, sy, ey) in rois:
            crop = screen_img[sy:ey, sx:ex]
            s = _detect_badge_suit(crop)
            if s is not None and s not in suits:
                suits.append(s)
        return suits
    except Exception:
        return []


def infer_opponents_from_discards(disc_counts: List[int]) -> Tuple[List[int], List[int]]:
    """根据牌桌弃牌分布反推对手定缺门（安全门）与主攻门（极度危险门）。"""
    try:
        wan_cnt = sum(disc_counts[0:9])
        tong_cnt = sum(disc_counts[9:18])
        tiao_cnt = sum(disc_counts[18:27])
        total = wan_cnt + tong_cnt + tiao_cnt
        safe_suits = []
        danger_suits = []
        if total >= 8:
            suit_dict = {0: wan_cnt, 1: tong_cnt, 2: tiao_cnt}
            for s, c in suit_dict.items():
                if c >= 6:
                    safe_suits.append(s)
                elif c <= 1 and (total - c) >= 10:
                    danger_suits.append(s)
        return safe_suits, danger_suits
    except Exception:
        return [], []


# 各分区牌河/副露的优先旋转顺序：把最可能朝向的放前面，配合高分早停，
# 使常见朝向一枪命中（省掉其余旋转的全 bank 扫描），罕见朝向仍会回退尝试。
# 输出仍等价于"逐旋转取 max"：早停仅在已高度自信（自配 bank 通常 0.9+）时触发。
_RIVER_ROT_PREF = {
    "bottom": (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE),
    "top":    (None, cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_90_CLOCKWISE),
    "left":   (cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_90_CLOCKWISE, None),
    "right":  (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE, None),
}
_RIVER_CONFIDENT = 0.85   # 达到此分即早停

# 牌河识别可配参数（集中一处）：把原本散落在多处的魔法数字（detect_river_discards
# 单张 0.42 门槛、voted_rows 牌河 0.78 门槛、中央骰子盒排除框比例、牌河行纵向先验带、
# 手牌顶保护线）收敛为一套可配参数。默认值与旧硬编码完全一致（不改现有接受/拒绝行为），
# 仅消除"双门槛漂移"并便于按平台（蜀山/腾讯）整体调参。
RIVER_CONF = {
    "min_conf": 0.42,          # detect_river_discards 单张采信下限
    "vote_conf": 0.78,         # voted_rows 牌河张采信下限（与 min_conf 同属一套配置）
    "center_x": (0.42, 0.58),  # 中央骰子盒排除框（占屏宽）
    "center_y": (0.38, 0.62),  # 中央骰子盒排除框（占屏高）
    "row_yc": (0.20, 0.72),    # 牌河行纵向物理先验带（占屏高）
    "hand_top": 0.76,          # 手牌顶部保护线
}

# 视觉牌河多帧一致回退帧数：牌河只增不减，若某张连续这么多帧不再出现于视觉牌河，
# 才判定此前计数为误检虚高并回退（单帧抖动绝不触发）。
RIVER_REGRET_FRAMES = 5

# 手牌单张「读不出」下限：低于这条 = 这张牌与它**最像**的模板都不够像，屏幕上
# 多半有东西压住了牌面（蜀山「清X色」大字动画实测 0.44）。
# 门槛是量出来的，不是拍的（`localtest/probe_tile_conf.py`：逐张打分的 top 分与
# top2 差距，对 20 帧真机 + 37 帧腾讯 GT 全量）：
#   读数错的那几张：0.443 / 0.442（蜀山两帧被动画压住的 4万→2条）；
#   读数全对的帧里最低：0.588（广东雀神一帧的 9筒，亚军 6筒，差距 0.081）。
# 所以 0.55 落在「错读」与「对读最低」之间。**不用行平均置信度做触发**：实测
# 分不开这两类——读数 100% 正确的广东雀神两帧行均只有 0.76/0.79，读数错的蜀山
# 两帧行均反而有 0.91/0.94；拿一个分不开好坏的数去报「降级」就是假测量。
# 已知边界：同帧另一处错读（7万→3万）top=0.626 但 top2 差距只有 0.006，单看分数
# 抓不住它 —— 抓得住的那张足以把「这帧别信」说出口，剩下的记在守卫里当台账。
HAND_UNREADABLE_CONF = 0.55

# 手牌「放开闸门重打分」的三个门槛。全部来自实测（`localtest/probe_gate_rescue.py`：
# 30 帧、逐枚同时记「闸门内 top1」与「全 34 top1」两份分数，共 381 枚）：
#   真该救的只有 9 枚（广东雀神挂着川麻玩法时屏上的 東/北/北）：
#     東：闸内 7z/0.522 → 全 34 1z/0.982（差 0.460）
#     北：闸内 7s/0.374 → 全 34 4z/0.587（差 0.213）
#   其余 372 枚的最优本来就在闸门内 —— 一次误接受都没有。
# 触发线 0.62：被救的三枚在 0.52 以下，而读数全对的帧里最低那张是 0.588（多打一次
# 分也不改变结论，只是多花几毫秒）。
# 接受线 0.55 与 0.15：门外那张自己得够像（与 HAND_UNREADABLE_CONF 同一条线：低于
# 它的读数不配当事实），且比闸内那个明显更像（实测最小差 0.213，留 1.4 倍余量）。
# 代价量过了（`localtest/ab_gate_rescue.py`，同进程 old→new→old 三趟交替）：
# 10 帧上中位 34.5→56.0ms（+21.5ms），最差帧 268→274ms（没变差）；第一趟的 737ms
# 是 bank 首次加载的冷启动，不能当结论。触发线不敢降到 0.55 以下：被救的 東 在
# 0.522，而读对的帧里最低那张是 0.588 —— 取 0.62 是「宁可多打一次分也不漏救」，
# 多付的那一次只花几毫秒，漏救一张则是用户报障里那行「字牌认不出」。
HAND_GATE_RESCORE_BELOW = 0.62
HAND_GATE_RESCORE_MIN = 0.55
HAND_GATE_RESCORE_MARGIN = 0.15

# 手牌带「被压暗」的判定线：取整条带 V 通道的 **p75**，低于这条就算暗。
# 为什么是 p75 而不是中位数：上一版取中位数，结果 6 帧正常帧被误判成「压暗」——
# 手牌带里大半是桌布（实测占比 0.47~0.64），中位数由桌布决定，跟牌亮不亮无关
# （是守卫的反向检查 `test_report_frames_guard.test_dim_frame_says_why_it_cannot_read`
# 当场把它抓出来的）。牌是带里最亮的东西，所以看高分位数。
# 分位数实测（`localtest/probe_dim_percentiles.py`，10 帧真机）：
#   正常帧 p75 = 194 / 196 / 196 / 209 / 211 / 215 / 223 / 227 / 232
#   弹窗压暗那一帧 p75 = **63**
# 130 落在中间那个空档上：暗侧余量 2.06 倍、亮侧余量 1.49 倍。
# 为什么不拿它去放宽牌面阈值：同一帧里弹窗自己的亮区 V=211，把阈值降到能看见
# 暗牌，就会把别的帧的桌面空地也当成牌（实测桌面空地通过率 4.7%→72%）。
# 所以暗层只拿来**说清为什么读不到**，不拿来改识别判据。
HAND_BAND_DIM_V = 130.0


def _hand_band_is_dim(image: CVImage, roi) -> bool:
    """手牌取区这一条带是不是整体被压暗（弹窗遮罩/回大厅动画）。

    只回答一个事实：这一带里最亮的那 25% 像素有多亮。不参与任何识别判据。
    取区装不下、或带里没像素时返回 False（「没意见」不等于「暗」，这条原则同
    `_face_brightness`）。
    """
    try:
        if image is None or image.size == 0:
            return False
        ih = image.shape[0]
        top, bot = float(roi[0]), float(roi[1])
        y0, y1 = int(ih * top), int(ih * min(1.0, bot))
        band = image[y0:y1]
        if band.size == 0 or band.shape[0] < 8:
            return False
        v = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)[:, :, 2]
        return float(np.percentile(v, 75)) < HAND_BAND_DIM_V
    except Exception:
        return False
ALL_THIRTY_FOUR = ([f"{n}{s}" for s in "mps" for n in range(1, 10)]
                   + [f"{n}z" for n in range(1, 8)])


def _rescue_out_of_gate(detector, image, row):
    """闸门内读不出的框，放开到全 34 面重打分；门外那张明显更像就照屏上事实读。

    为什么需要：玩法牌集是**分类之前**的闸门。用户在广东雀神上挂着川麻玩法（只放开
    7z）打广东麻将时，屏上的 東/北/北 连被模板比较的机会都没有 → 面板 14 张只报 11 张，
    剩下的贴成 7z/9s。这就是用户报的「明明有完整牌面却只识别出几张」与「字牌认不出」
    的同一个根子。

    为什么只改读数、不改玩法：玩法是用户选的（v1.7.5 定下的口径），但**屏上的牌是事实**。
    两者不一致时正确做法是把牌读对、把冲突说出去（`hand_gate_conflict`），而不是替用户
    换玩法，也不是拿一个错玩法去理直气壮地报错牌。

    只对「闸内分数低于触发线」的框补打分（实测每帧 0~3 枚，每枚一次完整分类），
    全牌玩法下门外集合为空，一次也不会多付。
    返回 (新的 row, [[屏上第几张, 门外牌面, 分数], ...])。
    """
    try:
        from recognition.tencent_grid_detector import resolve_candidate_tiles
        gate = resolve_candidate_tiles(None, getattr(detector, "_mode_tiles", None),
                                       bool(getattr(detector, "full_honors", False)))
    except Exception:
        return row, []
    if not gate or not (set(ALL_THIRTY_FOUR) - gate):
        return row, []        # 玩法本来就含全部 34 面，重打分只是白付钱
    if image is None or not hasattr(detector, "classify_tile"):
        return row, []
    ih, iw = image.shape[:2]
    out, conflicts = [], []
    for i, (r, lab, conf) in enumerate(row):
        if lab is not None and lab not in gate:
            # 识别器已经把它救回来了（浮起的摸牌那条路是在 `classify_tile_rescued` 里做的），
            # 不必再打一次分，但**留痕不能省**：否则同一行里“切好的牌”有留痕、
            # “浮起的牌”没留痕，面板上那句「屏上有 N 张不在当前玩法牌集里」就会少报。
            conflicts.append([i + 1, lab, round(float(conf), 2)])
            out.append((r, lab, conf))
            continue
        if lab is None or float(conf) < HAND_GATE_RESCORE_BELOW:
            pass
        else:
            out.append((r, lab, conf))
            continue
        x, y, w, h = [int(v) for v in r]
        x0, y0 = max(0, x), max(0, y)
        crop = image[y0:min(ih, y0 + h), x0:min(iw, x0 + w)]
        if crop.size == 0:
            out.append((r, lab, conf))
            continue
        try:
            fl, fs = detector.classify_tile(crop, avail=ALL_THIRTY_FOUR)
        except Exception:
            fl, fs = None, 0.0
        if (fl and fl not in gate and float(fs) >= HAND_GATE_RESCORE_MIN
                and float(fs) >= (float(conf) if lab is not None else 0.0)
                + HAND_GATE_RESCORE_MARGIN):
            out.append((r, fl, float(fs)))
            conflicts.append([i + 1, fl, round(float(fs), 2)])
        else:
            out.append((r, lab, conf))
    return out, conflicts

# ===== 牌河/副露全图检测的区域差分门控（响应提速的关键修复）=====
# 实测：detect_river_discards + detect_player_melds 单帧合计 600ms~1s（桌面），
# 手机上 1.5~4s；而它们只是给「记牌器/剩余活牌」供数，牌河只在有人出牌时才变（几秒一次）。
# 旧实现却在每个非跳帧的 ok 帧都无条件重跑这两坨全图扫描，把关键路径（牌面+建议）
# 一起拖到秒级——加上 Java 的 busy 互斥锁在处理期间丢弃新截图，表现就是「牌局早变了、
# 弹窗还显示刚才的牌面和建议」。定缺徽章/对手徽章/阶段探测都已按帧节流，唯独这两个
# 最重的全图扫描漏了节流。修复：只对「牌桌中央牌河区」算一个廉价分块签名（~1ms），
# 区内容未变就整块跳过昂贵检测、复用跨帧持久的累计账本（跳过绝不清空记牌器）。
RIVER_SCAN_DIFF_THRESH = 8.0   # 牌河区块均值 L1 距离低于此值视为未变（静止≈0~3，一张弃牌≈30+）
RIVER_SCAN_MAX_INTERVAL = 10   # 即便签名判未变，最多攒这么多帧也强制重扫一次（兜底防漏检漂移）

# 单次牌河扫描最多分类多少个候选。这个上限是被**实测逼出来的**：一次不限制
# 候选的全图扫描要 6.66 秒（`build/river_timing.py`），而后台结果必须在接下来
# 几帧内能被认领才有意义——扫得完但赶不上，面板上就是「记牌器空白」。
# 截断按「白底占比」降序取前 N：丢掉的永远是最不像牌的那批。
RIVER_SCAN_MAX_TILES = 24

# 首选旋转最高分低于此值 → 不再走「全量 bank × 3 旋转」兜底重扫。兜底是给
# 风格探针路由失误留的，不是给碎块噪声留的：那些切片本来就过不了采信线，
# 重扫只会在它们身上把成本乘以四。
RIVER_FALLBACK_MIN_SCORE = 0.30

# 牌河事件源（主检测器的整桌框）采信线。与轮廓法的 0.42 不是同一个量：那边是
# 模板 NCC 得分，这边是目标检测置信度，两者不可比。这个值必须先拿真机帧量过
# 才能定（`localtest/probe_river_events.py`）；量不了之时就宁可保守，宁可少读
# 不成把头像/按钮当成弃牌写进记账——账本只增不减，一张误读会留一整局。
RIVER_EVENT_CONF = 0.45


def _tile_content_key(tile_crop: np.ndarray):
    """切片内容指纹（8x8 缩略 hash）：牌河未变时同一切片命中缓存 → 零分类。"""
    try:
        if tile_crop is None or tile_crop.size == 0:
            return None
        small = cv2.resize(tile_crop, (8, 8), interpolation=cv2.INTER_AREA)
        return hash((small.shape, small.tobytes()))
    except Exception:
        return None


def _probe_styles(detector, cur: np.ndarray):
    """探测代表牌胜出的模板风格，返回限制 classify_tile 扫描的 styles 集合；
    探测不可用或不确定时返回 None（= 全量扫描）。"""
    if not hasattr(detector, "_probe_style"):
        return None
    try:
        st = detector._probe_style(cur)
        return {st} if st else None
    except Exception:
        return None


def _classify_tile_fast(detector, tile_crop, avail, pref_rots, cache):
    """带风格探针 + 高分早停 + 内容缓存的单张分类。

    与"3 旋转 × 全 104 bank 取 max"输出等价（早停只在自信时触发，低分兜底重扫），
    但常见路径把 104 次/张降到单 bank（~34）且多数牌一旋转即止；牌河签名不变时
    命中内容缓存直接零分类。返回 (label, score)，label 保证在 avail 内否则 None。
    """
    key = _tile_content_key(tile_crop)
    if key is not None and key in cache:
        return cache[key]

    best_lbl, best_sc = None, 0.0
    for r in pref_rots:
        cur = tile_crop if r is None else cv2.rotate(tile_crop, r)
        styles = _probe_styles(detector, cur)
        if hasattr(detector, "classify_tile"):
            lbl, sc = detector.classify_tile(cur, avail=avail, styles=styles)
        else:
            lbl, sc = None, 0.0
        try:
            if lbl and mpsz_to_tile34_index(lbl) in avail and sc > best_sc:
                best_sc, best_lbl = sc, lbl
        except Exception:
            pass
        if best_sc >= _RIVER_CONFIDENT:
            break

    # 探针路由失误致全低分（本就低于门槛会被丢弃）→ 全量 bank 兜底重扫一次
    # 但只在「有点像牌」时才兜：首转最高分连 RIVER_FALLBACK_MIN_SCORE 都不到的
    # 切片几乎肯定是碎块/桌布纹，它过不了采信线，而兜底是把成本乘以四。
    if best_sc < 0.42 and best_sc >= RIVER_FALLBACK_MIN_SCORE and hasattr(detector, "classify_tile"):
        for r in pref_rots:
            cur = tile_crop if r is None else cv2.rotate(tile_crop, r)
            lbl, sc = detector.classify_tile(cur, avail=avail, styles=None)
            try:
                if lbl and mpsz_to_tile34_index(lbl) in avail and sc > best_sc:
                    best_sc, best_lbl = sc, lbl
            except Exception:
                pass
            if best_sc >= _RIVER_CONFIDENT:
                break

    result = (best_lbl, best_sc)
    if key is not None:
        if len(cache) >= 4096:
            cache.clear()
        cache[key] = result
    return result


def _sort_hand_mpsz(mpsz: str) -> str:
    """把平铺手牌串按「万→筒→条→字、同门按数字」排好。

    只供展示：所有计算走多重集，排不排序不影响任何结论。屏上顺序直接上屏就是
    用户报的「手牌不排序」（实测见过「中 4条 3条 9条 4条 7条 6筒」）。
    读不出的张（None/空）一律排到末尾，不假装它是「什么都没有」。
    """
    if not mpsz:
        return ""
    labels = [mpsz[i:i + 2] for i in range(0, len(mpsz) - 1, 2)]
    suit_rank = {"m": 0, "p": 1, "s": 2, "z": 3}

    def key(lbl: str):
        if len(lbl) != 2 or lbl[1] not in suit_rank:
            return (9, 9, lbl)      # 读不出/未知牌放最后，不丢信息也不插队
        return (suit_rank[lbl[1]], int(lbl[0]) if lbl[0].isdigit() else 9, lbl)

    return "".join(sorted(labels, key=key))


def detect_river_discards(image: np.ndarray, detector, mode: str = "4p", hand_row=None, platform_key: str = "tencent") -> List[Tuple[str, str]]:
    """高精度牌桌弃牌检测：严格排除中央骰子盒、倒计时、房间名及头像，
    支持多牌相连自适应切片与多角度归一化，精准提取全场四方牌河弃牌。
    返回 (牌面 mpsz, 分区名) 列表，分区名 ∈ {bottom,top,left,right}。"""
    if image is None or detector is None or image.size == 0:
        return []
    try:
        ih, iw = image.shape[:2]
        avail = available_set(mode)
        raw_discards = []
        # 候选收集表（先收集后分类）：(白底占比, x, y, w, h, 区名, 切片)
        candidates: List[tuple] = []
        # 分类缓存挂在 detector 上跨帧复用（牌河未变时命中即零分类）。
        river_cache = getattr(detector, "_river_cls_cache", None)
        if river_cache is None:
            river_cache = {}
            try:
                detector._river_cls_cache = river_cache
            except Exception:
                pass

        # 四方牌河扫描区（避开中央指南针/骰子盒与外围UI，动态适配所选平台）：
        try:
            raw_p_zones = get_river_zones(platform_key)
            zones = [
                (z[0], int(iw * z[1]), int(ih * z[2]), int(iw * z[3]), int(ih * z[4]))
                for z in raw_p_zones
            ]
        except Exception:
            zones = [
                ('bottom', int(iw * 0.30), int(ih * 0.58), int(iw * 0.70), int(ih * 0.74)),
                ('top', int(iw * 0.30), int(ih * 0.16), int(iw * 0.70), int(ih * 0.38)),
                ('left', int(iw * 0.20), int(ih * 0.28), int(iw * 0.42), int(ih * 0.64)),
                ('right', int(iw * 0.58), int(ih * 0.28), int(iw * 0.80), int(ih * 0.64)),
            ]

        for zname, x1, y1, x2, y2 in zones:
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            # 牌面象牙白底特征
            white = (hsv[:, :, 1] < 65) & (hsv[:, :, 2] > 115) & (crop[:, :, 0] > 65) & (crop[:, :, 1] > 65) & (crop[:, :, 2] > 65)
            is_beacon = (hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 120)
            white[is_beacon] = 0

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            mask = cv2.morphologyEx(white.astype('uint8'), cv2.MORPH_CLOSE, kernel)
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for c in cnts:
                bx, by, bw, bh = cv2.boundingRect(c)
                if bw < 14 or bh < 14 or bw * bh < 220 or bw * bh > 6000:
                    continue

                gx, gy = x1 + bx, y1 + by
                # 排除中央指南针/骰子盒物理区域（比例可配，见 RIVER_CONF）
                cx_lo, cx_hi = RIVER_CONF["center_x"]
                cy_lo, cy_hi = RIVER_CONF["center_y"]
                if (cx_lo * iw <= gx + bw / 2 <= cx_hi * iw) and (cy_lo * ih <= gy + bh / 2 <= cy_hi * ih):
                    continue
                # 避免切到手牌顶部
                if gy + bh >= int(ih * RIVER_CONF["hand_top"]):
                    continue

                # 连片牌自适应网格切片（横排与竖排粘连）
                n_horiz = max(1, int(round(bw / float(max(18, bh * 0.85))))) if bw > 1.35 * bh else 1
                n_vert = max(1, int(round(bh / float(max(18, bw * 0.85))))) if bh > 1.35 * bw else 1

                sub_crops = []
                if n_horiz > 1:
                    step = bw / float(n_horiz)
                    for k in range(n_horiz):
                        sub_crops.append((gx + int(k * step), gy, int(step), bh, crop[by:by + bh, int(bx + k * step):int(bx + (k + 1) * step)]))
                elif n_vert > 1:
                    step = bh / float(n_vert)
                    for k in range(n_vert):
                        sub_crops.append((gx, gy + int(k * step), bw, int(step), crop[int(by + k * step):int(by + (k + 1) * step), bx:bx + bw]))
                else:
                    sub_crops.append((gx, gy, bw, bh, crop[by:by + bh, bx:bx + bw]))

                for sx, sy, sw, sh, tile_crop in sub_crops:
                    if tile_crop.size == 0:
                        continue
                    # 先只收集候选，不在此分类。分类是整条牌河路径的绝对成本大头
                    # （实测一次扫描 6.66s → 比采样窗口还长，结果永远赶不上认领）。
                    # 「像不像牌」用白底占比衡量：牌面基本是整块象牙白，低占比的
                    # 碎块/纹理几乎一定是噪声，它们的分类分数也永远过不了采信线。
                    fill = float(np.count_nonzero(
                        mask[by:by + bh, bx:bx + bw])) / float(max(1, bw * bh))
                    candidates.append((fill, sx, sy, sw, sh, zname, tile_crop))

        # 按「像牌程度」降序只分类前 N 个：截断的是最不像牌的那批，而不是任意
        # 截前 N 个。超出预算的候选宁可不读，也不能让整次扫描赶不上认领。
        candidates.sort(key=lambda t: t[0], reverse=True)
        budget = candidates[:RIVER_SCAN_MAX_TILES]
        for _fill, sx, sy, sw, sh, zname, tile_crop in budget:
            pref = _RIVER_ROT_PREF.get(
                zname, (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE))
            best_lbl, best_sc = _classify_tile_fast(
                detector, tile_crop, avail, pref, river_cache)

            # 牌河弃牌置信度门槛（可配，默认 0.42）
            if best_lbl and best_sc >= RIVER_CONF["min_conf"]:
                raw_discards.append((sx, sy, sw, sh, best_lbl, best_sc, zname))

        # 空间非极大值抑制（NMS）
        raw_discards.sort(key=lambda d: d[5], reverse=True)
        final_discards = []
        for d in raw_discards:
            gx, gy, bw, bh, lbl, sc, zn = d
            cx1, cy1 = gx + bw / 2, gy + bh / 2
            if not any(abs(cx1 - (ex[0] + ex[2] / 2)) < 20 and abs(cy1 - (ex[1] + ex[3] / 2)) < 20 for ex in final_discards):
                final_discards.append(d)

        # 按四区返回 (牌面, 分区) 列表：AI 仍消费全局账本（取 label），
        # 分区信息供上层做"谁打的"归属展示。
        return [(d[4], d[6]) for d in final_discards]
    except Exception:
        return []


def detect_player_melds(image: np.ndarray, detector, mode: str = "sc_hz", hand_row=None) -> List[Tuple[str, str, int]]:
    """精准检测全部玩家的副露区域（碰/杠）。
    涵盖左侧、右侧、上方及本家副露，副露必为 3 张同字（碰）或 4 张同字（杠）。
    返回结构化条目 (分区名, 牌面 mpsz, 张数)，供上层分账本计入副露可见量。"""
    if image is None or detector is None or image.size == 0:
        return []
    try:
        ih, iw = image.shape[:2]
        avail = available_set(mode)
        melds: List[Tuple[str, str, int]] = []

        hand_min_x, hand_max_x, hand_min_y = iw, 0, ih
        if hand_row:
            hand_boxes = [d[0] for d in hand_row if len(d) > 0 and len(d[0]) == 4]
            if hand_boxes:
                hand_min_x = min(b[0] for b in hand_boxes)
                hand_max_x = max(b[0] + b[2] for b in hand_boxes)
                hand_min_y = min(b[1] for b in hand_boxes)

        regions = [
            ('left', int(iw * 0.14), int(ih * 0.58), int(iw * 0.26), int(ih * 0.86), cv2.ROTATE_90_COUNTERCLOCKWISE, True),
            ('right', int(iw * 0.74), int(ih * 0.18), int(iw * 0.86), int(ih * 0.48), cv2.ROTATE_90_CLOCKWISE, True),
            ('top', int(iw * 0.60), int(ih * 0.06), int(iw * 0.85), int(ih * 0.22), None, False),
        ]
        if hand_max_x < iw * 0.94:
            regions.append(('bottom', int(max(iw * 0.64, hand_max_x + 8)), int(ih * 0.72), int(iw * 0.98), int(ih * 0.98), None, False))

        for name, x1, y1, x2, y2, rot, is_vert in regions:
            crop = image[y1:y2, x1:x2]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            white = (hsv[:, :, 1] < 60) & (hsv[:, :, 2] > 120) & (crop[:, :, 0] > 70) & (crop[:, :, 1] > 70) & (crop[:, :, 2] > 70)
            cnts, _ = cv2.findContours(white.astype('uint8'), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                bx, by, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if area < 1000 or area > 7500:
                    continue
                aspect = bh / float(bw) if is_vert else bw / float(bh)
                if aspect < 1.8:
                    continue

                gx, gy = x1 + bx, y1 + by
                if gy + bh >= hand_min_y and not (gx + bw < hand_min_x or gx > hand_max_x):
                    continue

                num_t = 4 if aspect > 3.2 else 3
                step = (bh / float(num_t)) if is_vert else (bw / float(num_t))

                cand_scores = []
                for i in range(num_t):
                    if is_vert:
                        t = crop[int(by + i * step):int(by + (i + 1) * step), bx:bx + bw]
                    else:
                        t = crop[by:by + bh, int(bx + i * step):int(bx + (i + 1) * step)]
                    if rot is not None:
                        t = cv2.rotate(t, rot)

                    best_lbl, best_sc = None, 0.0
                    # 1. 分类器（副露区已按分区单向旋转，再用风格探针把全 104 bank 降到单 bank）
                    if hasattr(detector, "classify_tile"):
                        styles = _probe_styles(detector, t)
                        lbl, sc = detector.classify_tile(t, avail=avail, styles=styles)
                        if not (lbl and mpsz_to_tile34_index(lbl) in avail) and styles is not None:
                            # 探针失误致低分 → 全量 bank 兜底重扫一次
                            lbl, sc = detector.classify_tile(t, avail=avail, styles=None)
                        if lbl and mpsz_to_tile34_index(lbl) in avail:
                            best_lbl, best_sc = lbl, sc
                    # 2. 紧凑模板匹配补充（针对俯视平躺牌）
                    if hasattr(detector, "templates_bgr") and best_sc < 0.45:
                        try:
                            face = detector.extract_face(t)
                            to_canvas = getattr(detector, "canonical_canvas", None)
                            for lbl, tmpl in detector.templates_bgr.items():
                                if mpsz_to_tile34_index(lbl) not in avail:
                                    continue
                                if to_canvas is not None:
                                    tmpl = to_canvas(tmpl)
                                t_compact = tmpl[20:98, 12:68]
                                res = cv2.matchTemplate(face, t_compact, cv2.TM_CCOEFF_NORMED)
                                sc_c = float(res.max())
                                if sc_c > best_sc:
                                    best_sc, best_lbl = sc_c, lbl
                        except Exception:
                            pass

                    try:
                        if best_lbl and mpsz_to_tile34_index(best_lbl) in avail and best_sc >= 0.42:
                            cand_scores.append((best_lbl, best_sc))
                    except Exception:
                        pass

                if cand_scores:
                    vote_cnt = Counter([c[0] for c in cand_scores])
                    top_lbl, top_votes = vote_cnt.most_common(1)[0]
                    if top_votes >= 2 or max([c[1] for c in cand_scores if c[0] == top_lbl]) >= 0.48:
                        melds.append((name, top_lbl, num_t))
        return melds
    except Exception:
        return []





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
        # 必须与下一行的 platform 同源（都读持久化声明），不能写死常量：
        # 手牌识别发生在 process 开头的方向验证里，而玩法 reload 在它**之后**
        # （见 process 内"玩法与平台切换硬重置"），所以建引擎时写的常量会
        # 成为**首帧识别实际生效的牌集**。实测（localtest/_probe_panel_styles_blame.py，
        # 广东雀神帧 45329b3e）：DEFAULT_MODE='sc_hz' 的字牌闸门只放开 7z，
        # 于是画面上的 東/發 被强行判成 1p(0.43)/1s(0.41)；把牌集改成声明的
        # 全牌玩法后逐位回到 GT（3z/6z，置信度 1.00）。
        self.mode: str = load_mode()
        self._prev_mode: str = ""
        # 游戏平台预设管理（腾讯、途游、微乐、JJ、通用）
        self.platform: str = load_platform()
        # 建实例时就算一次「房卡未收录」提示：第一帧的 payload 就得带着它，
        # 不能等 process 里那段 reload 之后才算（那样首帧面板会少一句该说的话）。
        # 注意：这一步**不改** self.mode —— 玩法由用户定，引擎只负责把牌集闸门
        # 按他说的装上（判据与为何改口径，见 `reconcile_mode_platform`）。
        self._mode_off_catalog: Optional[str] = None
        self.mode, self._mode_off_catalog = reconcile_mode_platform(self.mode, self.platform)
        self._prev_platform: str = ""
        self._last_doctrine: str = ""
        self._last_mood_state: str = "steady"
        # 给主界面"知道什么时候画面没动"的提示用
        self._consecutive_skips: int = 0
        # 本帧帧差（在 process 每帧重算，供跳帧决策与分阶段诊断读取）
        self._cur_frame_diff: float = float("inf")
        # 启动帧计数：首 WARMUP_FRAMES 帧走保守策略（参见 WARMUP_FRAMES 注释），
        # 避免 _MotionGuard 历史为空导致动画过渡帧漏过。
        self._warmup_left: int = WARMUP_FRAMES
        # 牌河稳定性历史与牌池单调累加器（单局牌池只增不减、108张物理守恒）
        self._discard_history: deque = deque(maxlen=DISCARD_HISTORY_FRAMES)
        self._monotonic_discards: Counter = Counter()
        self._pending_discards: Counter = Counter()  # 视觉牌河连续帧确认投票器
        # 牌河双账本：视觉累计与手牌差分推断分设两套 Counter，再以逐张 max 合并到
        # _monotonic_discards 供显示/消费（防同一次弃牌被两路双计，也防瞬时推断被视觉抹掉）。
        self._visual_discards: Counter = Counter()
        self._inferred_discards: Counter = Counter()
        # 视觉牌河连续缺席计标（多帧一致回退用）
        self._river_absent_streak: Dict[str, int] = {}
        # 四区牌河归属计数（"谁打的"展示；AI 仍按全局账本消费）
        self._river_zone_counts: Dict[str, int] = {"bottom": 0, "top": 0, "left": 0, "right": 0}
        # 牌河/副露区域差分门控状态（见 RIVER_SCAN_DIFF_THRESH 注释）：
        # _river_scan_sig = 上次真正跑牌河检测时的牌河区签名；_river_scan_wait = 距上次
        # 检测攒下的帧数。None / 大值 → 下一帧强制检测（开局/新局/牌桌切换后先扫一次）。
        self._river_scan_sig: Optional[np.ndarray] = None
        self._river_scan_wait: int = RIVER_SCAN_MAX_INTERVAL
        # 本帧提交给后台牌河检测的是哪个检测器（只供错误归因，不参与任何判定）
        self._river_det = None
        # 牌河最近一次的接线错误（None = 正常）。「空牌河」可以是牌局事实，也可以
        # 是接线断了，两者必须在 diag 上分得开。
        self._river_error: Optional[str] = None
        # 本帧事件源（主检测器整桌牌河框）交出的条目数。0 且无扫描结果 = 牌河真的
        # 没读数；而长期为 0 但牌局在跑，就说明事件源不可用（主检测器不是 YOLO）。
        self._river_event_n: int = 0
        # 提交/认领各计一次。没这两个数，「river_zones 全 0」就分不清三种故障：
        # 根本没提交（门一直关着）、提交了但从未被认领（异步链断）、认领了但确实
        # 为空（牌局事实）。三者修法完全不同。
        self._river_submits: int = 0
        self._river_consumes: int = 0
        self._river_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._river_future: Optional[concurrent.futures.Future] = None
        # 副露独立账本（34 型）：碰/杠是全场可见信，但不入牌河，避免污染弃牌计数；
        # 与牌河一样经两帧确认后才递增（碰→杠 3→4 单调递增，同局不回退）。
        self._meld_counts_34: List[int] = [0] * 34
        self._pending_melds_34: List[int] = [0] * 34
        self._visual_discards = Counter()
        self._inferred_discards = Counter()
        self._river_zone_counts = {"bottom": 0, "top": 0, "left": 0, "right": 0}
        self._opponent_discards: Dict[int, List[int]] = {1: [], 2: [], 3: []}
        self._opponent_melds: Dict[int, List[int]] = {1: [], 2: [], 3: []}
        self._match_started: bool = False
        self._last_stable_counter: Counter = Counter()
        self._last_stable_n: int = 0
        self._prev_raw_n: int = 0
        # 阶段检测"命中即清池"的连续帧确认计数器 + 新局指纹跳变检测
        self._phase_confirm_frames: int = 0
        self._prev_raw_n_before_clear: int = 0
        # 单帧识别失败时沿用的上一可信记牌矩阵（stale 呈现，绝不整体清零）
        self._last_trusted_matrix: Optional[Dict] = None
        # 牌局账本（由 trainer 按本帧可见牌生成，engine 只按签名认领）与连续脏帧计数：
        # 守恒违例的帧宁可不答，也不能给一个建立在错账上的答案。
        self._ledger: Optional[Dict] = None
        self._dirty_streak: int = 0
        # 自愈剪除过的牌：该型牌河张数上限被钉住（见 _heal_over_count）。不钉住，
        # 同一张误识牌会在下一次牌河扫描时被重新采纳，把“拒答几秒”变成整局周期性复发。
        self._healed_caps: Dict[str, int] = {}
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
        # 本帧是否「因为熔断而没重探」。必须随帧进 diag：否则面板「没变化」与
        # 「熔断中」在端上看起来一模一样，排查时只能靠猜。
        self._orient_probe_skipped: bool = False
        # 方向探测/快路径时已算出的检测结果，供 process() 复用，
        # 避免同一帧做两次完整检测。用完即清。
        self._cached_rows: Optional[list] = None
        # 这批缓存 rows 出自哪个检测器（手牌通道与主检测器可以不是同一个）。
        self._cached_rows_src: Optional[str] = None
        # 本帧手牌通道实际走了哪条路（full / strip / probe_reject / probe_skipped）
        # + 两套格网各切了几格（-1 = 本帧没试）。「同一帧被两套格网切出不同张数」就
        # 是靠这两个数看出来的，必须随帧进 diag。
        self._hand_channel_diag: Dict[str, int] = {}
        # 下一次真探测到条带时要不要把两套格数打一条日志。开局置 True，每次平台/玩法
        # 切换重置 True。它不主动多付钱（只在本来就要探测的帧上顺路打日志），所以不
        # 需要额外一个「本帧要付几次检测」的开关；要看**每帧**对照请拨 `hand_channel_ab`。
        self._hand_channel_probe_due: bool = True
        # 当前玩法是否在这家平台的房卡清单（`supported_modes`）里。False 不影响识别：
        # 牌集照用户声明的装。它只是给面板/取证一句「这玩法在这家不常见，算分口径自己核」。
        self._mode_supported: bool = True
        # 本帧那个「房卡未收录」的玩法 key（None = 在清单里）。必须上屏：选了一个
        # 不常见的玩法是用户的决定，但面板得让他自己看得见这个决定。
        self._mode_off_catalog: Optional[str] = None
        # 本帧手牌行里「连最像的模板都不够像」的那几张：[[屏上第几张, 分数], ...]。
        # 每帧在手牌刚切完时重算（不能拖到下一帧），非空就把建议降级为「本帧不给建议」。
        self._hand_low_conf: List[List] = []
        # 本帧「闸门内读不出、放开到全 34 才读对」的那几张：[[第几张, 牌面, 分数], ...]。
        # 它是玩法声明与牌桌不符的直接证据（不是识别退化），面板必须把它说出口。
        self._hand_gate_conflict: List[List] = []
        # 本帧手牌行里「有框但一张都没读出来」的框数（救不回也不许静默少报）。
        self._hand_missing: int = 0
        # 本帧手牌取区是否整体被压暗（全屏弹窗遮罩）。它只用来把「读不到」的
        # 原因说对，不参与任何识别判据（判据与实测见 `HAND_BAND_DIM_V`）。
        self._hand_band_dim: bool = False
        # 条带探测的连续不合格计数与停用标。停用后 `_hand_strip` 直接交回整屏，
        # 不再每帧多付一次「注定不起效」的条带检测；平台/玩法切换时重置。
        self._strip_reject_streak: int = 0
        self._strip_disabled: bool = False
        # `_verify_orientation` 里那次手牌识别的耗时（ms）。它必须单独记账：
        # rows 走缓存复用时 `_perf_ms["detect"]` 记到的是 0.0ms，而钱其实付在
        # 方向验证里 —— 前几轮性能排查一直找不到主成本，就是这个盲区害的。
        self._orient_cost_ms: float = 0.0
        # 本帧的「方向再验证」是否还欠着（几何已归一，但识别没跑）。
        # 每帧在 process 开头由 `_settle_orientation` 重新赋值，绝不跨帧生效：
        # 只有跳帧帧会带着 True 提前 return，下一帧开头立刻被覆盖。
        self._orient_verify_pending: bool = False
        # 手牌通道（模板网格 NCC）单独一个实例：主检测器还要供牌河/副露/阶段探测。
        self._hand_detector = None
        self._hand_bank_key: Optional[tuple] = None
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
            # 采集存帧（风格库自举）：真实行为在 Java 侧把原始帧落盘到 files/frames/，
            # 这里仅存档该开关状态，供 set_config 接受。
            "dump_frames": False,
            # 牌河真实帧采集（P4 再训练闭环的数据入口）：真实对局中每当「牌河内容
            # 变化」时，把方向归一后的整帧 JPEG + 元数据 JSON 落盘到
            # set_frame_dump_dir 指定的目录。区别于 dump_frames 的无差别全量存帧，
            # 这里只在牌河状态变化时存（一局 ≈ 几十帧而非几千帧重复垃圾），
            # 供 adb pull → 人工校验标注 → 牌河检测模型再训练。
            "collect_river": False,
            # 牌河 YOLO 影子对比（E）：开启后每帧额外跑一次 YOLO 条带法检测牌河，
            # 只写进 diag 与日志供离线对比，**绝不参与建议/显示**。默认关（有推理开销）。
            "yolo_river": False,
            # 手牌通道：平台已挂模板 bank 时，手牌行走网格 NCC（实测精度显著高于
            # 主检测器，代价是单帧多 ~80ms）。关掉 = 回到“手牌也走主检测器”的旧行为，
            # 仅供 A/B 对拍与排障。依据见 get_hand_detector 的注释。
            "hand_grid": True,
            # 手牌通道格网对照：开着时**每一帧**都额外跑一次底部条带，把两套格网各
            # 切了几格钉进日志（排「识别缺张」时用）。默认关：常开就是每帧多付一次
            # 完整识别（实测翻倍）。它**只加钱和日志、不改采信规则**：条带仍然必须
            # 格数反超整屏且峰值不低才能顶掉整屏读数（拿它采集对比数据不会偷改结果）。
            # 与 hand_grid 同一档次：排障开关，不接任何 UI；必须列在这里，否则
            # `set_config` 会把未知 key 静默丢弃，这个开关就永久只能停在默认值。
            "hand_channel_ab": False,
        }
        # 出牌建议配置（调试页开关，process() 每帧从 mahjong_advice.json reload）。
        # 这里给一份安全默认：显示出牌建议、不过滤进张。即便文件永远不存在，
        # 行为也与接入该配置前完全一致（不会变成"没建议"）。
        self._advice_cfg: Dict[str, object] = {
            "show_advice": True,
            "min_ukeire": 0,
        }
        # 牌桌场景防抖计数：连续 N 帧非牌桌才判定离开对局，防止游戏弹窗遮罩瞬时误触发 waiting
        self._non_table_frames: int = 0
        # ===== 牌河真实帧采集状态（见 _maybe_collect_river）=====
        # 目录由 Java 侧启动引擎时经 set_frame_dump_dir 推入；空串 = 未配置（采集自动失效）。
        self._river_dump_dir: str = ""
        self._river_last_sig: Optional[tuple] = None  # 上一张已存帧的牌河状态签名
        self._river_saved: int = 0
        # ===== 低置信手牌帧采集（见 _maybe_collect_lowconf_hand）=====
        self._lowconf_saved: int = 0
        self._lowconf_last_ts: float = 0.0
        # 纯色底手牌实证缓存（见 _is_mahjong_table）：重检测 4 帧限频
        self._table_ev_tick: int = 0
        self._table_ev_verdict: bool = False
        # YOLO 影子对比实例：None=未创建，False=创建失败（不再重试），对象=可用
        self._yolo_shadow = None
        # ===== 全图辅助检测节流（响应提速）=====
        # 定缺徽章 / 对手徽章 / 开局阶段物理特征都是"局中几乎不变"的场景特征，
        # 旧实现每帧（25~80ms 一帧）都跑一遍全图检测，挤占主识别链路 CPU，
        # 表现为建议出得慢。节流后：每 N 帧重检一次，期间直接复用缓存值。
        self._dq_scan_tick: int = 0
        self._dq_scan_cache: Tuple[Optional[int], Optional[str]] = (None, None)
        # ===== 定缺徽章读数的时间稳定门（面板自噬的次级防线）=====
        # 自家悬浮窗展开时会压住徽章取区，此时 detect_dingque 读到的是面板 chip 的
        # 颜色而不是牌桌事实（真机 s2 报筒、s4 报条）。像素层面区分不了二者——那条
        # 启发式已实测证伪并拆除（见 detect_dingque 上方 NOTE）。还能区分的只剩**时间
        # 轴**：牌桌徽章一局内恒定，面板内容随牌局逐秒变。于是把最近 DQ_STABLE_WINDOW
        # 次扫描攒成窗口，窗口内非 None 读数彼此不一致就整体判「没读准」（宁可不报，
        # 也不让最后那一枪直接上屏——旧写法表现为缺门在万/筒/条之间抽搐）。
        # 彻底解法是面板矩形事实源（需真机验证），本门只是次级防线，不假装解决它。
        self._dq_scan_hist: deque = deque(maxlen=DQ_STABLE_WINDOW)
        self._dq_hist_orient = None      # 窗口是在哪个朝向下攒的（换朝向后读数不可比）
        self._dq_scan_count: int = 0     # 真正扫了几枪（节流下帧数与枪数不等，诊断靠它）
        self._dq_unstable_hits: int = 0  # 因分歧被扣下的扫描次数（诊断计数，不上屏）
        self._opdq_scan_tick: int = 0
        self._opdq_cache: List[int] = []
        self._phase_scan_tick: int = 0
        self._phase_cache: Tuple[bool, bool, bool] = (False, False, False)
        self._pick_cand_cache: List[str] = []
        # 换牌阶段时间迟滞状态：_swap_hold_frames 为剩余保持的检测周期数，
        # _swap_raw 为本帧探测器对换牌的「真实」判定（未经迟滞），供清池计数使用。
        self._swap_hold_frames: int = 0
        self._swap_raw: bool = False
        # ── 牌局阶段与事件状态机（局况**事实**层）──────────────────────────────
        # 与 phase_label/tactical_* 的分工不同：那三个回答「该打哪张」（战术建议），
        # 这台机器回答「现在轮到谁、刚发生了什么、几家碰杠」——而且它恒有输出，
        # 不像建议层在 shanten>=2 时返回空串、被显示层整块收起（用户「看不清牌局在
        # 干嘛」的直接原因）。纯折算，不做任何检测，单帧开销是几十次字典比较。
        self._phase_machine = MatchPhaseMachine(label_fn=tile_to_chinese)
        # 立牌基数（未副露、未摸牌时的手牌张数）。与阶段机共用一个值，两处不同源
        # 就会在杠后各自算出一套基数，阶段与建议开始互相矛盾。
        self.base_hand_tiles: int = 13
        self._phase_machine.base_hand = self.base_hand_tiles
        # 本家手牌差分捕获到的「刚打出的那张」，供状态机播本家出牌；歧义时置 None，
        # 宁可写成「打出一张牌（牌面待确认）」也不猜。
        self._hand_diff_discard: Optional[str] = None
        # 各家副露条目（含本家 bottom）：{seat: [(mpsz, 张数)]}。原有的
        # `_opponent_melds` 只有 1/2/3 家且存 34 型索引，播不出「你碰了 5万」。
        self._seat_meld_entries: Dict[int, List[Tuple[str, int]]] = {
            0: [], 1: [], 2: [], 3: []}
        # 单帧耗时诊断：最近 30 帧滚动窗口，每 60 帧打印一次均值/峰值
        self._proc_ms: deque = deque(maxlen=30)
        self._proc_frames: int = 0
        # 分阶段耗时滚动窗口（decode/detect/orient/river/advice）：每 20 帧输出一行
        # [perf] 并塞进 diag，悬浮窗诊断行直接可见哪一段是瓶颈。
        # orient 必须单独一个键：rows 走缓存复用时 detect 记到 0.0ms，而真机实测
        # 单帧 568~1345ms 的钱几乎全付在方向验证里那次整屏手牌识别上 —— 旧口径下
        # 这笔开销不存在于任何一个阶段里，性能排查因此一直找不到主成本。
        self._perf_ms = {k: deque(maxlen=20)
                         for k in ("decode", "detect", "orient", "river", "advice")}

    def _verify_hand_evidence(self, image: np.ndarray) -> int:
        """从当前画面中获取实体手牌证据张数（兼容 YOLO 检测器与网格切片器）。
        用于打破纯色桌布（微乐等单色率超高画面）与弱桌布（深色/大弹窗遮挡）的牌桌校验死锁。
        """
        if image is None or image.size == 0:
            return 0
        ih = image.shape[0]

        # 1. 优先尝试主检测器（若为 YOLODetector，调用 detect_all_rows 切出底部手牌行）
        det = getattr(self, "_detector", None)
        if det is None:
            try:
                det = self.get_detector()
            except Exception:
                det = None
        if det is not None:
            if hasattr(det, "detect_hand_strip"):
                try:
                    n = len(det.detect_hand_strip(image))
                    if n >= 5:
                        return n
                except Exception:
                    pass
            if hasattr(det, "detect_all_rows"):
                try:
                    rows = det.detect_all_rows(image, classify=False, allow_rotation=False)
                    if rows:
                        for r in rows:
                            if len(r) >= 5:
                                cy = sum(d[0][1] + d[0][3] * 0.5 for d in r) / len(r)
                                if cy >= 0.50 * ih:
                                    return len(r)
                except Exception:
                    pass

        # 2. 尝试手牌专用检测器 (TencentGridDetector)
        hdet = getattr(self, "_hand_detector", None)
        if hdet is None:
            try:
                hdet = self.get_hand_detector()
            except Exception:
                hdet = None
        if hdet is not None and hasattr(hdet, "detect_hand_strip"):
            try:
                n = len(hdet.detect_hand_strip(image))
                if n >= 5:
                    return n
            except Exception:
                pass
        return 0

    def _is_mahjong_table(self, image: np.ndarray) -> bool:
        """检测当前画面是否为真实的麻将牌桌对局场景（排除大厅/主菜单/结算界面/加载画面）。

        真实麻将牌桌具备两大铁证：
        1. 牌桌中央呈现大面积单色平整的麻将桌布（经典绿呢、深青墨绿呢或温润木纹），
           主色调占比必 >= 18%，且彻底排除蓝天白云沙滩等多色混合干扰。
        2. 画面下半部（手牌区）存在实体手牌先验特征。
        """
        if image is None or image.size == 0:
            return False
        h, w = image.shape[:2]
        center = image[int(h * 0.22):int(h * 0.78), int(w * 0.20):int(w * 0.80)]
        if center.size == 0:
            return False
        small = cv2.resize(center, (100, 100), interpolation=cv2.INTER_NEAREST)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

        # 真实麻将桌布单色区间（必须是单一主色调，杜绝风景多色拼接）：
        # 1. 经典绿呢桌布: H in [35, 88], S >= 40, V in [30, 210]
        # 2. 腾讯/雀魂深墨绿/深青呢: H in [88, 112], S >= 45, V in [30, 150] (排除高亮海蓝天蓝)
        # 3. 仿木纹/咖啡色桌布: H in [12, 25], S >= 60, V in [35, 160]
        green_felt = float(np.mean((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 88) & (hsv[:, :, 1] >= 40) & (hsv[:, :, 2] >= 30) & (hsv[:, :, 2] <= 210)))
        cyan_felt = float(np.mean((hsv[:, :, 0] >= 88) & (hsv[:, :, 0] <= 112) & (hsv[:, :, 1] >= 45) & (hsv[:, :, 2] >= 30) & (hsv[:, :, 2] <= 150)))
        wood_felt = float(np.mean((hsv[:, :, 0] >= 12) & (hsv[:, :, 0] <= 25) & (hsv[:, :, 1] >= 60) & (hsv[:, :, 2] >= 35) & (hsv[:, :, 2] <= 160)))

        max_felt = max(green_felt, cyan_felt, wood_felt)

        # 【v4 防误判】纯色背景排除：速配/加载/ splash 页常是铺满全屏的单一
        # 绿底，桌布占比可高达 90%+；而真实牌桌中央必有牌河/副露/玩家头像/
        # 桌面花纹，单一桌布色占比极少超过 90%。
        # 但微乐等极简美术风格的真实牌桌中央桌布占比亦可能达到 90%~95%，
        # 此时通过实体手牌检测验证（支持 YOLO 与网格双通道），只要底部有手牌，立刻放行！
        if max_felt >= 0.90:
            tick = getattr(self, "_table_ev_tick", 0) + 1
            self._table_ev_tick = tick
            if tick % 4 == 1:
                hand_ev = self._verify_hand_evidence(image)
                self._table_ev_verdict = (hand_ev >= 7)
            return bool(getattr(self, "_table_ev_verdict", False))

        # 真实牌桌中央桌布单色占比通常达到 18% 以上
        if max_felt >= 0.18:
            return True

        # 兜底：中央桌布占比不足（被全屏大弹窗/定缺色盘/换牌面板遮挡）时的
        # 「确凿在对局中」实证。核心不变量：真实牌桌在任何阶段（定缺/换牌/选牌/
        # 摸打）底部都必呈现玩家自己的手牌，而大厅/主菜单/结算/加载画面底部没有
        # 手牌（只有菜单图标）。
        hand_n = self._verify_hand_evidence(image)
        if hand_n >= 7:
            return True
        if hand_n >= 5:
            det = getattr(self, "_detector", None)
            if det is None:
                try:
                    det = self.get_detector()
                except Exception:
                    det = None
            if det is not None:
                try:
                    for _phase_fn in ("is_dingque_phase", "is_swap_phase", "is_pick_phase"):
                        fn = getattr(det, _phase_fn, None)
                        if fn is not None and fn(image):
                            return True
                except Exception:
                    pass

        return False

    def _should_scan_river(self, image: np.ndarray) -> bool:
        """牌河/副露全图检测的区域差分门控。返回 True 才跑昂贵的
        detect_river_discards/detect_player_melds；返回 False 表示牌河区木然未变，
        本帧跳过（累计账本跨帧持久，跳过不会清空记牌器）。

        只对牌河纵向带（RIVER_CONF['row_yc']）算分块签名，并把中央骰子/倒计时盒
        （RIVER_CONF['center_x']/['center_y']）抹平——否则倒计时数字每秒变一次会误
        触发“牌河变了”而白白每帧重跑。签名与上次真正扫描时比较，变化超阈值或
        攒满强制间隔才重扫，重扫后刷新签名基准。
        """
        ih, iw = image.shape[:2]
        yc_lo, yc_hi = RIVER_CONF["row_yc"]
        y0 = max(0, int(ih * yc_lo))
        y1 = min(ih, int(ih * yc_hi))
        band = image[y0:y1, :]
        if band.size == 0 or band.shape[0] < 20:
            return True  # 取不到牌河带 → 保守跑完整检测
        # 抹掉中央骰子/倒计时盒（固定中性值），使签名只对“牌河是否多/少一张牌”敏感。
        band = band.copy()
        cx_lo, cx_hi = RIVER_CONF["center_x"]
        cy_lo, cy_hi = RIVER_CONF["center_y"]
        by0 = max(0, int((cy_lo - yc_lo) / max(1e-6, (yc_hi - yc_lo)) * band.shape[0]))
        by1 = min(band.shape[0], int((cy_hi - yc_lo) / max(1e-6, (yc_hi - yc_lo)) * band.shape[0]))
        bx0, bx1 = int(iw * cx_lo), int(iw * cx_hi)
        if by1 > by0 and bx1 > bx0:
            band[by0:by1, bx0:bx1] = 0
        g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
        # 降采样到固定尺度：保证跨帧签名维度一致，且把开销压到 ~1ms。
        gh, gw = g.shape[:2]
        longest = float(max(gh, gw))
        if longest > 600:
            inv = 600.0 / longest
            g = cv2.resize(g, (max(1, int(gw * inv)), max(1, int(gh * inv))),
                           interpolation=cv2.INTER_AREA)
        cur = _block_diff_signature(g)
        self._river_scan_wait += 1
        if (self._river_scan_sig is not None
                and self._river_scan_sig.shape == cur.shape
                and float(np.abs(cur - self._river_scan_sig).sum()) < RIVER_SCAN_DIFF_THRESH
                and self._river_scan_wait < RIVER_SCAN_MAX_INTERVAL):
            return False
        self._river_scan_sig = cur
        self._river_scan_wait = 0
        return True

    def _update_visual_ledger(self, discard_labels) -> None:
        """牌河视觉累计账本：两帧确认递增 + 多帧一致最小值回退，再与手牌差分推断
        账本逐张 max 合并到单调牌池（同局只增不减，但单帧误检虚高可经连续回退）。
        抽成方法以便离线单测双账本合并/限幅/回退语义。"""
        current_frame_discards = Counter(discard_labels)
        # 已被判定为误识的超出部分不得借尸还魂：剪过一次就把上限钉住。否则同一张
        # 误识牌会每 DIRTY_HEAL_FRAMES 帧重新凑满一次“同型 5 张”，把整局变成
        # “拒答 3 秒 → 正常 3 秒”的周期抽风。上限只限制牌河观测张数，不改手牌/副露。
        for lab, cap in self._healed_caps.items():
            if current_frame_discards[lab] > cap:
                current_frame_discards[lab] = cap
        for lab, cnt in current_frame_discards.items():
            if not lab:
                continue
            if cnt > self._visual_discards[lab]:
                if self._pending_discards[lab] >= cnt:
                    self._visual_discards[lab] = cnt
                else:
                    self._pending_discards[lab] = cnt
        for lab in list(self._pending_discards.keys()):
            if current_frame_discards[lab] == 0:
                self._pending_discards[lab] = self._visual_discards[lab]
        for lab in list(self._visual_discards.keys()):
            live = current_frame_discards[lab]
            if live < self._visual_discards[lab] and self._inferred_discards[lab] == 0:
                st = self._river_absent_streak.get(lab, 0) + 1
                self._river_absent_streak[lab] = st
                if st >= RIVER_REGRET_FRAMES:
                    if live <= 0:
                        del self._visual_discards[lab]
                    else:
                        self._visual_discards[lab] = live
                    self._river_absent_streak[lab] = 0
            else:
                self._river_absent_streak[lab] = 0
        for lab in set(self._visual_discards) | set(self._inferred_discards) | set(self._monotonic_discards):
            m = max(self._visual_discards[lab], self._inferred_discards[lab])
            if m <= 0:
                self._monotonic_discards.pop(lab, None)
            else:
                self._monotonic_discards[lab] = m

    def _heal_over_count(self, violations: List[Dict], hand_counts: List[int]) -> List[str]:
        """把「同型已见 > 4」的超出部分从牌河账本里裁掉（守恒硬门的自愈）。

        牌河账本只增不减：一张被误读进去的牌会常驻整局，不裁就会把“本帧拒答”
        变成“整局拒答”。一种牌只有 4 张，超出上限的那几张不可能是真牌，只能删。
        必须三本同裁：_monotonic_discards 每帧由 max(_visual_discards, _inferred_discards)
        重算，只裁单调本会在下一帧被两本子账本顶回来（自愈失效）。
        裁了什么必须告知 UI，绝不静默改数据。
        """
        healed: List[str] = []
        meld = self._meld_counts_34
        for v in violations:
            if str(v.get("kind") or "") != "over_four":
                continue
            lab = str(v.get("tile") or "")
            try:
                idx = mpsz_to_tile34_index(lab)
            except Exception:
                continue
            allowed = max(0, 4 - int(hand_counts[idx]) - int(meld[idx]))
            cur = int(self._monotonic_discards.get(lab, 0))
            if cur <= allowed:
                continue
            for book in (self._visual_discards, self._inferred_discards,
                         self._monotonic_discards):
                if lab in book:
                    if allowed <= 0:
                        book.pop(lab, None)
                    else:
                        book[lab] = allowed
            # 钉住上限：下一帧牌河扫描仍会报回 5 张，不钉住就会重新开始凑脏帧。
            self._healed_caps[lab] = allowed
            healed.append(f"{lab} 剪 {cur - allowed} 张")
        return healed

    def _clear_discard_ledgers(self) -> None:
        """清空全部弃牌账本（单调牌池 + 两子账本 + 确认/缺席计标 + 自愈上限）。

        账本清了就等于“本局从零记账”，硬门计数与钉住上限也必须同步作废：
        留着旧上限会把新一局里真实存在的同型牌河张数裁小（新局与旧局无关）。
        """
        self._monotonic_discards.clear()
        self._pending_discards.clear()
        self._visual_discards.clear()
        self._inferred_discards.clear()
        self._river_absent_streak.clear()
        self._healed_caps.clear()
        # 脏帧计数属于“本局拒答进度”，清池时必须一同作废（否则新局第一帧就可能
        # 因旧局残留的 streak 直接跳进自愈分支）。
        self._dirty_streak = 0
        self._river_zone_counts = {"bottom": 0, "top": 0, "left": 0, "right": 0}
        self._opponent_discards = {1: [], 2: [], 3: []}
        self._opponent_melds = {1: [], 2: [], 3: []}
        # 清池后牌河基准失效：重置区域门控，下一帧强制重扫一次。
        self._river_scan_sig = None
        self._river_scan_wait = RIVER_SCAN_MAX_INTERVAL

    def _reset_game_state(self):
        """重置整局游戏的动态状态（新开局/返回大厅/结算时调用）。"""
        self._clear_discard_ledgers()
        self._meld_counts_34 = [0] * 34
        self._pending_melds_34 = [0] * 34
        self._discard_history.clear()
        self._tile_voter.reset()
        self._hand_stab.reset()
        self._frame_skipper = _FrameSkipper()
        # 新局/回大厅：牌河区域基准失效，重置门控使下一帧强制重扫。
        self._river_scan_sig = None
        self._river_scan_wait = RIVER_SCAN_MAX_INTERVAL
        self._stable_hand_mpsz = ""
        self._stable_hand_count = 0
        self._match_started = False
        self._last_stable_counter = Counter()
        self._last_stable_n = 0
        self._prev_raw_n = 0
        self._phase_confirm_frames = 0
        self._prev_raw_n_before_clear = 0
        self._last_trusted_matrix = None
        self._ledger = None
        self._dirty_streak = 0
        self._partial_mpsz = ""
        self._partial_ttl = 0
        self._last_hand_y = None
        self._orient = 0
        self._orient_zerocount = 0
        self._advice_key = None
        self._advice = []
        self._stable_best = ""
        self._stable_shanten = None
        self._transient_drop_streak = 0
        self._empty_hand_streak = 0
        # 新局/回大厅：局况阶段机与它的输入快照必须一起作废，否则旧局的牌河张数、
        # 副露基数会跨局生效——表现为新一局第一帧就报「下家 打出 …」这种凭空事件。
        self._phase_machine.reset()
        self._hand_diff_discard = None
        # 新局/回大厅：徽章读数的时间窗口必须一起作废，否则上一局已敲定的缺门会
        # 顶替本局「还没定缺」的事实（窗口里全是旧值，而稳定门只认窗口内的读数）。
        self._dq_scan_hist.clear()
        self._dq_scan_cache = (None, None)
        self._dq_hist_orient = None
        self._seat_meld_entries = {0: [], 1: [], 2: [], 3: []}
        self.trainer = None
        self._river_future = None

    def _stable_dingque_read(self) -> Tuple[Optional[int], Optional[str]]:
        """最近几枪徽章扫描的**稳定**读数（判据与存在理由见 _dq_scan_hist 处注释）。

        · 窗口内非 None 读数彼此全等 → 可信，返回该门；
        · 出现分歧 → 不可信，返回 (None, None)。上层因此走「没读到」的出口：既不上屏、
          不锁存 `_match_started`，也不会被 match_state 播成「定缺敲定」事件；
        · 单枪漏读（None）本身不算分歧——徽章一旦敲定就整局常驻，压缩噪声/瞬时遮挡
          造成的漏读不该抹掉已确立的事实；整窗皆 None 时仍返回 (None, None)。
        单轮只扫一枪（fresh Engine、评测逐帧独立）时窗口里只有一个值，自然全等→可信，
        所以这道门不会改变 eval_base 的定缺基准，它只拦「后一枪推翻前一枪」。
        """
        seen = [s for s in self._dq_scan_hist if s is not None]
        if not seen:
            return None, None
        if len(set(seen)) != 1:
            self._dq_unstable_hits += 1
            return None, None
        return seen[0], DINGQUE_SUIT_NAMES[seen[0]]

    def _river_event_detector(self):
        """牌河事件源用的检测器：要的是「整桌牌框」，不是「能 NCC 分类」。

        优先用主检测器（设备上是 YOLO，它本来就是每帧跳的那一个）；拿不到
        `detect_river_strips` 时退回影子对比用的那个实例。两个都不行就返回 None
        （事件路自动失效，回到旧的后台扫描，不会“看似的有了牌河”）。
        """
        for det in (self._detector, getattr(self, "_yolo_shadow", None)):
            if det is not None and det is not False and hasattr(det, "detect_river_strips"):
                return det
        try:
            det = self.get_detector()
        except Exception:
            return None
        return det if det is not None and hasattr(det, "detect_river_strips") else None

    def _river_from_events(self, image):
        """弃牌的事件源：主检测器每帧已经算出的牌河条带框 → [(牌, 区)]。

        为什么必须换掉「后台全表 NCC 扫描」这一条单一入口（实测，2026-10）：
        那条路一次冷扫描要 7~10 秒纯 CPU，而在连续识别的进程里 28 秒墙钟都抢不到
        跑完（`localtest/probe_river_repeat.py`）。任务不报错、不降级，只是永远
        不被认领 → 牌河永远为空 → 记牌器空白、三家 `tenpai_prob` 全等于先验 0.5。
        YOLO 本来每帧就跑，牌河框是它的副产品，拿这个当牌河源几乎不再花额外开销。

        只做三件事：过采信线、按**平台自己的分区几何**归家（而不是 YOLO 内部写死的
        区）、保留中央骰子盒排除——这三条任何一条偷懒都会把“别的东西”当成弃牌。
        读数仍需两帧确认才进账本（`_update_visual_ledger`），所以单帧误框不会
        污染记牌器。
        """
        if image is None or getattr(image, "size", 0) == 0:
            return []
        det = self._river_event_detector()
        if det is None:
            return []
        try:
            dets = det.detect_river_strips(image)
        except Exception:
            return []
        try:
            ih, iw = image.shape[:2]
            zones = [(z[0], int(iw * z[1]), int(ih * z[2]), int(iw * z[3]), int(ih * z[4]))
                     for z in get_river_zones(self.platform)]
        except Exception:
            return []
        out: List[Tuple[str, str]] = []
        cx_lo, cx_hi = RIVER_CONF["center_x"]
        cy_lo, cy_hi = RIVER_CONF["center_y"]
        for rect, lbl, conf in dets:
            if not lbl or conf < RIVER_EVENT_CONF:
                continue
            try:
                x, y, w, h = rect[0], rect[1], rect[2], rect[3]
            except Exception:
                continue
            cx, cy = x + w / 2.0, y + h / 2.0
            # 中央骰子盒/倒计时不算弃牌（与轮廓法同一道排除，不得一边有一边没有）
            if (cx_lo * iw <= cx <= cx_hi * iw) and (cy_lo * ih <= cy <= cy_hi * ih):
                continue
            for zn, x1, y1, x2, y2 in zones:
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    out.append((lbl, zn))
                    break
        return out

    def _apply_river_entries(self, entries, avail):
        """把 (牌, 区) 列表入账：分区计数 + “谁打的”归属 + 返回牌面列表。

        抽成一个方法是为了让**两个牌河来源走同一个口径**：后台 NCC 扫描与事件源
        （主检测器整桌框）。分叉的话很容易一个更新了 `river_zones`、另一个只更新
        牌面，面板上就变成“记牌器有牌但三家归属全空”，而这种不一致比单纯为空更难查。
        玩法闸门（`in avail`）也只能住在这里：两条路共用，不会出现一路把字牌推进账本。
        """
        zone_counts: Dict[str, int] = {"bottom": 0, "top": 0, "left": 0, "right": 0}
        zone_to_seat = {"right": 1, "top": 2, "left": 3}
        seat_discards: Dict[int, List[int]] = {1: [], 2: [], 3: []}
        discards: List[str] = []
        for cd, zn in entries or []:
            if not cd:
                continue
            try:
                t34 = mpsz_to_tile34_index(cd)
            except Exception:
                continue
            if t34 not in avail:
                continue
            discards.append(cd)
            if zn in zone_counts:
                zone_counts[zn] += 1
            seat = zone_to_seat.get(zn)
            if seat and t34 < 27:
                seat_discards[seat].append(t34)
        self._river_zone_counts = zone_counts
        self._opponent_discards = seat_discards
        return discards

    @staticmethod
    def _run_bg_river_and_melds(image, detector, mode, hand_row, platform_key="tencent"):
        """后台异步执行的牌河与副露全图检测，将2~3秒阻塞完全移出主识别流水线。"""
        try:
            r_entries = detect_river_discards(image, detector, mode=mode, hand_row=hand_row, platform_key=platform_key)
        except Exception:
            r_entries = []
        try:
            m_entries = detect_player_melds(image, detector, mode=mode, hand_row=hand_row)
        except Exception:
            m_entries = []
        return r_entries, m_entries

    def set_platform(self, platform_key: str) -> None:
        """显式设置当前游戏平台预设（支持腾讯、途游、微乐、JJ、通用）。"""
        set_platform_explicit(platform_key)
        self.platform = platform_key
        # 平台换了，玩法不跟着换（用户可能就在这一家打那副规则），但房卡提示要重算：
        # 同一个玩法在上一家常用、在这一家可能根本没上过房卡。
        self.mode, self._mode_off_catalog = reconcile_mode_platform(self.mode, self.platform)
        self._tile_voter.reset()
        self._hand_stab.reset()
        self._last_hand_y = None
        self._frame_skipper = _FrameSkipper()
        self._cached_rows = None
        self._cached_rows_src = None
        self._prev_platform = self.platform
        self._apply_platform_styles(self._detector)
        self._hand_bank_key = None

    def _apply_platform_styles(self, detector) -> None:
        """把当前平台的模板 bank 风格 + 当前玩法的牌集推给识别器。

        set_platform 与 get_detector 都要调：前者可能早于 detector 懒加载（那时
        还没有对象可设），后者可能晚于平台切换（新建的实例默认是全量）。
        StructuralDetector 没有这些接口，靠 hasattr 兼容。

        牌集（set_mode_tiles）为什么也要推：手牌行/副露区的分类调用自带 avail 参数，
        但 YOLO 覆盖层、牌桌场景探针、任选牌弹窗那些入口不带，之前只能按平台猜
        “这批牌里可不可能有字牌”。结果是选了全牌玩法（推倒胡/白板百搭/中发白三鬼），
        这些路径上永久读不到东/南/西/北/白/发 —— 牌集明明由玩法决定。推入后候选集
        跟玩法一致，川麻类玩法仍只放开 7z（与旧行为全等，不引入 8p/9p 误配）。
        """
        if detector is None:
            return
        if hasattr(detector, "set_platform_styles"):
            detector.set_platform_styles(self.platform)
        if hasattr(detector, "set_mode_tiles"):
            detector.set_mode_tiles(available_set(self.mode))

    def reset_match(self) -> None:
        """用户或外部显式请求「新对局重置」：瞬间清空牌池、手牌记忆，108张活牌满血恢复。"""
        self._reset_game_state()
        print("[engine] 收到用户显式「新对局重置」请求，已重置牌河、手牌记忆与活牌计数")

    def set_config(self, key: str, value) -> None:
        """调试页开关：实时修改识别策略。未知 key 静默忽略。"""
        if key in self._cfg:
            self._cfg[key] = bool(value)

    def _display_mode_name(self) -> str:
        """面板标题里的玩法名：说清「引擎实际在按哪个玩法算」。

        玩法不再被引擎替换，所以这里只有一个名字；但若它不在这家平台的房卡清单里，
        必须把这件事顺带说出口——用户选的越是冷门玩法，算分口径与实桌不一致时越容易
        把「我算错了」当成「它识别错了」。名字走 `get_mode` 而不是 `MODES.get`：
        后者对别名（sc/4p）会拿不到条目而直接回显 key。
        """
        name = get_mode(self.mode).get("name", self.mode)
        off = getattr(self, "_mode_off_catalog", None)
        if off:
            return f"{name}（该平台房卡未收录，按所选规则推演）"
        return name

    def set_config_dir(self, path) -> None:
        """Java 侧推入真实外部 files 目录（getExternalFilesDir 实际返回值）。

        修复「切了玩法规则不变」：部分机型/虚拟机上外部存储实际路径与
        硬编码 /storage/emulated/0/... 不一致，引擎轮询永远读不到模式文件。
        """
        try:
            _modes_set_config_dir(str(path) if path else "")
        except Exception:
            pass

    def set_mode(self, key) -> None:
        """Java 侧显式推送当前玩法（内存优先级最高，不等磁盘轮询）。"""
        try:
            m = str(key).strip().lower() if key else ""
            if m:
                _modes_set_mode_explicit(m)
                # Java 直推的玩法也走一次房卡核对：不拦它（玩法由用户定），但
                # 递到面板上的那句提示不能只给 UI 点选那一条路——旧版本残留、
                # Java 直接推、磁盘轮询都是同样的口径。
                m, self._mode_off_catalog = reconcile_mode_platform(m, self.platform)
                if m != getattr(self, "mode", None):
                    self.mode = m
                    self._prev_mode = m
                    self._reset_game_state()
                    print(f"[engine] set_mode 立即重置对局并切换生效: {m}")
        except Exception:
            pass

    # 每轮采集上限：牌河状态签名去重后，一次长局约产生 60~150 帧，
    # 1200 足够几天采样用尽前不会撑爆存储（单帧 ≈ 300KB，上限约 360MB 前
    # 用户早该 pull 走了；目录满也有 Java 侧 clear_river 信号手动清空）。
    RIVER_DUMP_LIMIT = 1200

    def set_frame_dump_dir(self, path) -> None:
        """Java 侧推入牌河采集目录（引擎可直接写的应用外部files路径）。"""
        try:
            p = str(path) if path else ""
        except Exception:
            return
        self._river_dump_dir = p
        self._river_last_sig = None
        self._river_saved = 0
        # 重推目录 = 新一轮采集（与 Java 侧同步清理动作配套），低置信配额一并重置
        self._lowconf_saved = 0
        self._lowconf_last_ts = 0.0
        if p:
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                self._river_dump_dir = ""

    def _maybe_collect_river(self, image, result: Dict) -> None:
        """牌河真实帧采集：每当「牌河内容」变化时存一帧整屏 + 元数据。

        设计约束（P4 闭环数据入口）：
        - 签名 = (牌河mpsz, 定缺门)。牌池单调累加器保证牌河只增不减且经两帧
          确认，签名变化 ≡ 观测到新弃牌，天然滤掉静止画面的重复帧；
          手牌变化不触发存帧（牌河训练只关心牌河）。
        - 存方向归一后的整帧（含牌河+手牌+副露），训练标注时可自由裁切；
          ROI 裁剪后的图不能代表全场，不用。
        - 元数据带引擎弱标签（牌河内容/各家张数/手牌/阶段），后续人工只需
          「校验+纠错」而非从零标注，大幅降低标注重。
        - 任何异常都静默吞掉：采集绝不允许影响识别主链路。
        """
        try:
            if not self._cfg.get("collect_river") or not self._river_dump_dir:
                return
            if not isinstance(image, np.ndarray) or image.size == 0:
                return
            # 只采正式对局中且牌桌可见的帧；大厅/结算/过渡动画全部跳过。
            status = result.get("status")
            if status not in ("ok", "incomplete"):
                return
            if self._river_saved >= self.RIVER_DUMP_LIMIT:
                return
            disc = result.get("discards") or ""
            sig = (disc, result.get("dingque_suit"))
            if sig == self._river_last_sig:
                return
            self._river_last_sig = sig  # 存失败也不重试同签名，防风暴
            ok, buf = cv2.imencode(
                ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 98])
            if not ok:
                return
            ts = int(time.time() * 1000)
            stem = os.path.join(
                self._river_dump_dir, f"river_{self._river_saved:04d}_{ts}")
            blob = buf.tobytes()
            with open(stem + ".jpg", "wb") as f:
                f.write(blob)
            # 牌河标签→张数（引擎弱标签，供人工校验对拍）
            river_tally: Dict[str, int] = {}
            for i in range(0, len(disc) - 1, 2):
                lab = disc[i:i + 2]
                river_tally[lab] = river_tally.get(lab, 0) + 1
            meta = {
                "ts": ts,
                "file": stem.rsplit(os.sep, 1)[-1] + ".jpg",
                "mode": result.get("mode"),
                "status": status,
                "hand": result.get("hand") or "",
                "hand_count": result.get("count"),
                "river_mpsz": disc,
                "river_tally": river_tally,
                "dingque_suit": result.get("dingque_suit"),
                "dingque_phase": result.get("dingque_phase"),
                "swap_phase": result.get("swap_phase"),
                "pick_phase": result.get("pick_phase"),
                "remaining": result.get("remaining"),
                "shanten": result.get("shanten"),
                "frame_size": [int(image.shape[1]), int(image.shape[0])],
                "orient": result.get("diag", {}).get("orient"),
                "verified": False,  # 弱标签未经人工核验，下游不得当 GT 用
            }
            with open(stem + ".json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            self._river_saved += 1
            print(f"[collect_river] 存第 {self._river_saved} 帧 牌河{len(river_tally)}种 签名={sig}")
        except Exception:
            traceback.print_exc()

    LOWCONF_SCORE_GATE = 0.80   # 最高模板分低于此 = 当前 bank 对平台样式覆盖不足
    LOWCONF_MIN_INTERVAL = 3.0  # 秒，限流防风暴写盘
    LOWCONF_LIMIT = 300         # 单引擎会话上限（q92 整帧 ≈ 0.3-0.8MB/帧，最坏 ≈ 百MB 量级；
                                # 目录由调试页清理/重推复位，只在该目录存在期间累积）

    def _maybe_collect_lowconf_hand(self, image, result: Dict) -> None:
        """低置信手牌帧采集：真机遇到模板 bank 未覆盖的平台牌面样式（漏检/
        误识的主要数据性根因）时，落盘低分帧 + 引擎弱标签，供离线校验后
        扩充 bank。与 collect_river 共用总开关与目录（hand_lowconf 子目录），
        默认关、限流、限量；任何异常吞掉，绝不影响识别主链路。"""
        try:
            if not self._cfg.get("collect_river") or not self._river_dump_dir:
                return
            status = result.get("status")
            if status not in ("ok", "partial", "incomplete", "no_tiles"):
                return
            # no_tiles（本帧 0 牌）多为方向锁错/ROI 设错而非 bank 覆盖问题，
            # 且 top_score 恒 0 必过门槛——必须本帧真的切出了牌候选才值得回收。
            if status == "no_tiles" and not (result.get("tiles") or []):
                return
            top = float(result.get("top_score") or 0.0)
            if top >= self.LOWCONF_SCORE_GATE:
                return
            now = time.time()
            if (self._lowconf_saved >= self.LOWCONF_LIMIT
                    or now - self._lowconf_last_ts < self.LOWCONF_MIN_INTERVAL):
                return
            if not isinstance(image, np.ndarray) or image.size == 0:
                return
            ok, buf = cv2.imencode(
                ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not ok:
                return
            out_dir = os.path.join(self._river_dump_dir, "hand_lowconf")
            os.makedirs(out_dir, exist_ok=True)
            ts = int(now * 1000)
            stem = os.path.join(out_dir, f"lowconf_{self._lowconf_saved:04d}_{ts}")
            with open(stem + ".jpg", "wb") as f:
                f.write(buf.tobytes())
            meta = {
                "ts": ts,
                "mode": result.get("mode"),
                "status": status,
                "top_score": top,
                "hand": result.get("hand") or "",
                "hand_count": result.get("count"),
                "tile_count": len(result.get("tiles") or []),
                "frame_size": [int(image.shape[1]), int(image.shape[0])],
                "verified": False,
            }
            with open(stem + ".json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            self._lowconf_saved += 1
            self._lowconf_last_ts = now
            print(f"[lowconf] 存第 {self._lowconf_saved} 帧低分帧 top={top}")
        except Exception:
            traceback.print_exc()

    def _maybe_yolo_shadow(self, image, result: Dict) -> None:
        """牌河 YOLO 影子对比：与轮廓法（生产 discards）并跑同一帧，结果只进
        diag 与日志。任何异常吞掉；开关关闭时零开销（仅一次布尔判断）。"""
        try:
            if not self._cfg.get("yolo_river"):
                return
            if not isinstance(image, np.ndarray) or image.size == 0:
                return
            if self._yolo_shadow is None:
                try:
                    from recognition.yolo_detector import YOLODetector
                    inst = YOLODetector()
                    self._yolo_shadow = inst if inst.is_available else False
                except Exception:
                    self._yolo_shadow = False
            if self._yolo_shadow is False:
                return
            dets = self._yolo_shadow.detect_river_strips(image)
            labels = [d[1] for d in dets if d[1]]
            contour = result.get("discards") or ""
            c_labels = [contour[i:i + 2] for i in range(0, len(contour) - 1, 2)]
            agree = sum((Counter(c_labels) & Counter(labels)).values())
            result.setdefault("diag", {})["yolo_river"] = {
                "n": len(labels), "contour_n": len(c_labels), "agree": agree,
                "labels": "".join(sorted(labels)),
            }
            print(f"[yolo_shadow] contour={len(c_labels)} yolo={len(labels)} "
                  f"agree={agree} yolo_labels={''.join(labels)}")
        except Exception:
            traceback.print_exc()

    def start(self):
        pass

    def get_detector(self):
        if self._detector is not None:
            self._apply_platform_styles(self._detector)
            return self._detector
        # 1. 优先使用 YOLO-Mahjong-Nano 端到端目标检测器
        try:
            from recognition.yolo_detector import YOLODetector
            yolo = YOLODetector()
            if yolo.is_available:
                self._detector = yolo
                print("[Engine] Using YOLODetector as primary detection engine.")
                self._apply_platform_styles(self._detector)
                return self._detector
        except Exception as e:
            print(f"[Engine] YOLODetector failed to initialize: {e}")

        # 2. 次选：腾讯欢乐麻将专用网格匹配引擎
        try:
            from recognition.tencent_grid_detector import TencentGridDetector
            grid = TencentGridDetector()
            if grid.is_available:
                self._detector = grid
                print("[Engine] Using TencentGridDetector as fallback detection engine.")
                self._apply_platform_styles(self._detector)
                return self._detector
        except Exception as e:
            print(f"[Engine] TencentGridDetector failed: {e}")

        # 3. 兜底：通用结构识别器
        self._detector = StructuralDetector()
        return self._detector

    def get_hand_detector(self):
        """手牌行走哪个检测通道：平台已挂模板 bank 时走网格 NCC，否则走主检测器。

        实测依据（两通道同一帧、都按平台声明口径，`localtest/layer_cost.py`）：
          腾讯底座 20 帧 253 张：网格 253/253（模板就来自这批帧，属样本内上限），
            YOLO 220/253 = 87.0%；
          新素材 4 帧 52 张（模板未收割过，可外推）：网格 50/52 = 96.2%，
            YOLO 44/52 = 84.6%；逐平台 微乐 100/100、途游 100/92、蜀山 100/77、
            雀神 85/69（雀神那 2 张是 bank 缺类，补样本才能救，不是换算法）。
        代价（`localtest/bench_latency.py --channels`，PC/OpenCV CPU）：手牌行
        p50 网格 193ms vs YOLO 113ms（两条通道都还远未满足 50ms 预算）。

        只换手牌行：牌河/副露/阶段探测继续用主检测器（`detect_river_discards` 需要
        多行与全图分区，网格通道的 `detect_all_rows` 只出一行手牌，供不出牌河）。

        只在主检测器确实是 YOLODetector 时才接管：兜底链里的网格/结构识别器本来就
        是一条通道，再造一条只会多占一份模板内存；而测试与调试页通过覆盖
        `get_detector` 注入的自定义检测器必须被尊重（否则“mock 检测器驱动 process”
        那类守卫会测不到自己注入的假货，面板却走真模板）。
        """
        primary = self.get_detector()
        if (primary is None or primary.__class__.__name__ != "YOLODetector"
                or not self._cfg.get("hand_grid", True)):
            return primary
        try:
            from recognition.tencent_grid_detector import (TencentGridDetector,
                                                          banked_platforms)
        except Exception:
            traceback.print_exc()
            return primary
        if self.platform not in banked_platforms():
            # 该平台没有本家字模（generic / 尚未收割的 platforms.py 条目）：
            # 拿别家牌风认这家的牌比直接认错更糟，维持主检测器。
            return primary
        det = self._hand_detector
        if det is None:
            try:
                det = TencentGridDetector()
            except Exception:
                traceback.print_exc()
                return primary
            if not getattr(det, "is_available", False) or not getattr(det, "_cores", None):
                return primary
            self._hand_detector = det
        key = (self.platform, self.mode)
        if self._hand_bank_key != key:
            # 与 _apply_platform_styles 同一语义：平台/玩法变了就重推白名单与候选牌集。
            if hasattr(det, "set_platform_styles"):
                try:
                    det.set_platform_styles(self.platform)
                except Exception:
                    traceback.print_exc()
            if hasattr(det, "set_mode_tiles"):
                try:
                    det.set_mode_tiles(available_set(self.mode))
                except Exception:
                    traceback.print_exc()
            self._hand_bank_key = key
        return det

    def _cache_rows(self, det, rows) -> None:
        """缓存一批 rows 并记下它出自哪个通道（方向探测顺手算出的 rows 给 process 复用）。

        手牌通道与主检测器可以不是同一个，所以必须记来源：否则方向探测用 YOLO 快检
        缓存的 rows 会被当成网格结果直接上屏，换了通道也等于没换。
        """
        self._cached_rows = rows
        self._cached_rows_src = det.__class__.__name__ if det is not None else None

    def update_trainer(self, hand: TileCollection) -> Optional[str]:
        """根据最新一手牌更新训练器，返回对上一手的中文点评。"""
        # 只有当前玩法的合法手牌张数才是合法手牌。其它张数说明这一帧识别不完整，
        # 直接跳过，避免拿脏数据去点评（原实现在这里 assert，会中断整帧）。
        if len(hand) not in hand_sizes(self.mode):
            print(f"Hand length {len(hand)} is not valid for mode {self.mode}, skipping")
            return None

        if self.trainer is None:
            print(f"Initial hand: {hand}")
            self.trainer = Trainer(hand, mode=self.mode)
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
            self.trainer = Trainer(hand, mode=self.mode)
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
        self.trainer = Trainer(hand, mode=self.mode)
        return None

    @staticmethod
    def _compute_mood_guard(
        shanten: Optional[int],
        advice: Optional[List[Dict]],
        tenpai_alert: Optional[Dict],
        tile_count: int,
    ) -> Optional[Dict]:
        """根据当前起手向听、有效进张与他家听牌态势，研判局势顺逆，提供军师安抚与攻防定位。

        - 顺风（favorable）：听牌或一向听宽进张（>=8张），主动进攻、提速冲刺。
        - 逆风（defensive）：向听较深（>=3向听）或进张极窄（<=2张）或他家疑似听牌，防守避锋、防上头点炮。
        - 平稳（steady）：常规搭子推进中，保持摸打节奏。
        """
        if tile_count < 13 or shanten is None:
            return None

        top_ukeire = 0
        if advice and isinstance(advice, list) and len(advice) > 0 and isinstance(advice[0], dict):
            top_ukeire = int(advice[0].get("ukeire", 0))

        has_alert = bool(tenpai_alert and isinstance(tenpai_alert, dict) and tenpai_alert.get("alert"))

        if shanten >= 3 or top_ukeire <= 2 or has_alert:
            if has_alert:
                desc = "场上对手疑似下叫听牌，当前牌势凶险，优先跟熟避锋，防大番点炮！"
            elif shanten >= 3:
                desc = "起手牌型较散（处于摸牌波谷），切忌急躁，优先扣下生张稳扎稳打。"
            else:
                desc = "当前有效进张较窄，建议调整搭子结构或扣住下家危险牌。"
            return {
                "state": "defensive",
                "badge": "🛡️ 逆风抗压 · 防守保分",
                "desc": desc,
                "level": "orange",
            }

        if shanten == 0 or (shanten == 1 and top_ukeire >= 8) or top_ukeire >= 12:
            if shanten == 0:
                desc = "已达听牌绝佳状态！牌势凌厉，全力锁定胡牌张，乘胜追击！"
            else:
                # top_ukeire 是未现牌计数（牌墙 + 对手手上），说“N 张活牌”会把
                # 上界当确定机会；写清“未现/含对手”才是可核对的说法。
                desc = f"一向听优质大进张（未现 {top_ukeire} 张，含对手手上），全力冲刺下叫！"
            return {
                "state": "favorable",
                "badge": "🌊 牌势顺遂 · 乘胜追击",
                "desc": desc,
                "level": "green",
            }

        return {
            "state": "steady",
            "badge": "⚖️ 局势平稳 · 见机行事",
            "desc": f"当前{shanten}向听（未现进张至多 {top_ukeire} 张），牌局平稳推进中，保持节奏等待良机。",
            "level": "blue",
        }

    def build_advice(self, hand: TileCollection, disc_counts=None, meld_counts=None):
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
        if self.trainer is None or getattr(self.trainer, "mode", None) != self.mode:
            try:
                from trainer.trainer import Trainer
                self.trainer = Trainer(hand, mode=self.mode)
            except Exception:
                pass
        if self.trainer is None:
            return None, []

        if disc_counts is not None:
            self.trainer.set_visible(disc_counts, meld_counts if meld_counts is not None else [0] * 34)

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

        if not show_advice or len(hand) == 0:
            return None, []

        # 不完整手牌：复用缓存。返回一个浅拷贝防止上游改 self._advice。
        if len(hand) not in hand_sizes(self.mode):
            return shanten, list(self._advice)

        # 缓存键必须带上 min_ukeire 与两个 warn 开关：否则调高/调低"好牌机率"
        # 或切换危险牌预警时会命中旧缓存，界面建议列表纹丝不动 → 表现为开关"没生效"。
        key = f"{hand}|{shanten}|{min_ukeire}|{warn_deal_in}|{warn_pon_kong}|{hash(tuple(self.trainer.disc_counts))}|{hash(tuple(self.trainer.meld_counts))}"
        if key == self._advice_key:
            return shanten, self._advice

        advice: List[Dict] = []
        try:
            if len(hand) in hand_sizes(self.mode):
                raw = self.trainer.calculate_discards()

                _sc = (self.trainer.sichuan_results
                       if is_sichuan_family(self.mode) else None)
                _std = (getattr(self.trainer, "std_results", None)
                        if getattr(self.trainer, "analyzer", "") == "std" else None)
                _rule_results = _sc or _std
                if _rule_results:
                    advice = []
                    for sr in _rule_results:
                        u = sr.get("ukeire", 0)
                        if min_ukeire > 0 and u < min_ukeire:
                            continue
                        entry = {
                            "tile": sr.get("tile"),
                            "ukeire": int(u),
                            "shanten": sr.get("shanten", 0),
                            "ev": sr.get("ev", 0.0),
                            "reason": sr.get("reason", ""),
                            "ting_tiles": sr.get("ting_tiles", []),
                            "ting_details": sr.get("ting_details", []),
                            "is_dingque": sr.get("is_dingque", False),
                        }
                        # 账本听口结论与 PVN 可用性标记必须一路透传到 UI：reason 里写了
                        # 「其中 4 张只能自摸」，UI 拿不到对应字段就没法核对，等于让用户
                        # 只能选择相信一句无法验证的话（可追溯性正是本次升级的目的）。
                        # `predraw`（B-P4 空白 D）同理：预摸情景只在 analyzer 内部存在过，
                        # 不透传就会被 annotate 当成“没模拟数据”，面板永远不渲染预演行。
                        for _k in ("ting_chance", "ukeire_chance", "pvn_used",
                                   "analytical_equity", "predraw"):
                            if _k in sr:
                                entry[_k] = sr[_k]
                        if "max_fan" in sr:
                            entry["max_fan"] = sr["max_fan"]
                        if "danger_flow" in sr:
                            entry["danger_flow"] = sr["danger_flow"]
                        if "policy_prob" in sr:
                            entry["policy_prob"] = sr["policy_prob"]
                        if "win_equity" in sr:
                            entry["win_equity"] = sr["win_equity"]
                        if "ev_gauge" in sr:
                            entry["ev_gauge"] = sr["ev_gauge"]
                        advice.append(entry)
                        if len(advice) >= 6:
                            break
                    # 智能保底机制：若按门槛过滤后没有任何推荐（残局或深向听导致无>=min_ukeire打法），
                    # 绝不能留空让界面变白，自动保底回退取最高进张的候选打法，并标注保底提示。
                    if min_ukeire > 0 and not advice and _rule_results:
                        for sr in _rule_results[:3]:
                            u = sr.get("ukeire", 0)
                            rsn = sr.get("reason", "")
                            rsn = (f"{rsn} · 智能保底" if rsn
                                   else f"进张至多 {u} 张 · 智能保底")
                            entry = {
                                "tile": sr.get("tile"),
                                "ukeire": int(u),
                                "shanten": sr.get("shanten", 0),
                                "ev": sr.get("ev", 0.0),
                                "reason": rsn,
                                "ting_tiles": sr.get("ting_tiles", []),
                                "ting_details": sr.get("ting_details", []),
                                "is_dingque": sr.get("is_dingque", False),
                            }
                            # 保底分支同样得透传 predraw：残局/深向听恰恰是最需要
                            # 「摸到什么会改主意」的时刻，少传一个字段就是静默降级。
                            for _k in ("ting_chance", "ukeire_chance", "pvn_used",
                                       "analytical_equity", "predraw"):
                                if _k in sr:
                                    entry[_k] = sr[_k]
                            if "max_fan" in sr:
                                entry["max_fan"] = sr["max_fan"]
                            if "danger_flow" in sr:
                                entry["danger_flow"] = sr["danger_flow"]
                            if "policy_prob" in sr:
                                entry["policy_prob"] = sr["policy_prob"]
                            if "win_equity" in sr:
                                entry["win_equity"] = sr["win_equity"]
                            if "ev_gauge" in sr:
                                entry["ev_gauge"] = sr["ev_gauge"]
                            advice.append(entry)
                elif getattr(self.trainer, "general_results", None):
                    # 通用分析器（无专属规则引擎的平台）只交出进张总数，交不出是哪些牌，
                    # 因此无法像川麻/std 那样用账本拆「牌墙 vs 对手手上」。文案一律标
                    # 「至多/未现」：数字仍是那个上界，但措辞不再把它当成确定机会。
                    advice = []
                    for rank, gr in enumerate(self.trainer.general_results):
                        u = gr.get("ukeire", 0)
                        if min_ukeire > 0 and u < min_ukeire:
                            continue
                        sh = gr.get("shanten", shanten if shanten is not None else 2)
                        t_str = gr.get("tile_str", str(gr.get("tile", "")))
                        ting_details = gr.get("ting_details", [])
                        ting_tiles = gr.get("ting_tiles", [])
                        if sh == 0 and ting_details:
                            wait_names = [td["name"] for td in ting_details]
                            if u > 0:
                                rsn = f"听 {'/'.join(wait_names[:3])} · 未现 {u} 张"
                            else:
                                rsn = f"听 {'/'.join(wait_names[:3])}（绝张）"
                        elif sh == 0:
                            rsn = f"听牌 · 进张至多 {u} 张"
                        elif sh == 1:
                            rsn = f"一向听 · 进张至多 {u} 张"
                        elif sh is not None and sh >= 2:
                            rsn = f"{sh}向听 · 进张至多 {u} 张"
                        else:
                            rsn = f"进张至多 {u} 张"
                        entry = {
                            "tile": t_str,
                            "ukeire": int(u),
                            "shanten": sh,
                            "ev": float(10000 - rank * 100 + int(u) * 10),
                            "reason": rsn,
                            "ting_tiles": ting_tiles,
                            "ting_details": ting_details,
                            "is_dingque": False,
                        }
                        advice.append(entry)
                        if len(advice) >= 6:
                            break
                    if min_ukeire > 0 and not advice and self.trainer.general_results:
                        for rank, gr in enumerate(self.trainer.general_results[:3]):
                            u = gr.get("ukeire", 0)
                            sh = gr.get("shanten", shanten if shanten is not None else 2)
                            t_str = gr.get("tile_str", str(gr.get("tile", "")))
                            entry = {
                                "tile": t_str,
                                "ukeire": int(u),
                                "shanten": sh,
                                "ev": float(10000 - rank * 100 + int(u) * 10),
                                "reason": f"进张至多 {u} 张 · 智能保底",
                                "ting_tiles": gr.get("ting_tiles", []),
                                "ting_details": gr.get("ting_details", []),
                                "is_dingque": False,
                            }
                            advice.append(entry)
                else:
                    # 最底层兜底：只有 count 映射，同样只能按上界表述（理由同上）。
                    items = sorted(raw.items(), key=lambda kv: -kv[1])
                    filtered_items = items
                    is_fallback = False
                    if min_ukeire > 0:
                        cand = [(t, u) for (t, u) in items if int(u) >= min_ukeire]
                        if cand:
                            filtered_items = cand
                        else:
                            filtered_items = items[:3]
                            is_fallback = True

                    advice = []
                    for rank, (t, u) in enumerate(filtered_items[:6]):
                        t_str = str(t)
                        fb_tag = " · 智能保底" if is_fallback else ""
                        if shanten == 0:
                            rsn = f"听牌 · 进张至多 {u} 张{fb_tag}"
                        elif shanten == 1:
                            rsn = f"一向听 · 进张至多 {u} 张{fb_tag}"
                        elif shanten is not None and shanten >= 2:
                            rsn = f"{shanten}向听 · 进张至多 {u} 张{fb_tag}"
                        else:
                            rsn = f"进张至多 {u} 张{fb_tag}"
                        entry = {
                            "tile": t_str,
                            "ukeire": int(u),
                            "shanten": shanten if shanten is not None else 2,
                            "ev": float(10000 - rank * 100 + int(u) * 10),
                            "reason": rsn,
                            "ting_tiles": [],
                            "ting_details": [],
                            "is_dingque": False,
                        }
                        advice.append(entry)

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

        # 调用国手战术知识库，注入国手心法批注与战术加权调优
        if advice:
            try:
                h_counts = [0] * 34
                for t_obj in hand:
                    t_idx = mpsz_to_tile34_index(str(t_obj))
                    if 0 <= t_idx < 34:
                        h_counts[t_idx] += 1
                d_counts = disc_counts if disc_counts is not None else [0] * 34
                m_counts = meld_counts if meld_counts is not None else [0] * 34
                mood_st = getattr(self, "_last_mood_state", "steady")
                kb_res = KnowledgeBase.evaluate_tactics(
                    hand_counts=h_counts,
                    disc_counts=d_counts,
                    meld_counts=m_counts,
                    mode=self.mode,
                    shanten=shanten if shanten is not None else 2,
                    advice_list=advice,
                    danger_flow=None,
                    mood_state=mood_st,
                )
                self._last_doctrine = kb_res.get("doctrine", "")
            except Exception:
                pass

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

    def set_dingque_override(self, suit) -> None:
        """手动指定定缺花色（0=万, 1=筒, 2=条，其它=解除覆盖自动感知）。"""
        try:
            s = int(suit) if suit is not None else -1
            self._dingque_override = s if 0 <= s <= 2 else None
        except Exception:
            self._dingque_override = None

    def process_bytes(self, image_data) -> Optional[EngineResult]:
        _t0 = time.time()
        _td = _t0
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
            try:
                self._perf_ms["decode"].append((time.time() - _td) * 1000.0)
            except Exception:
                pass
            return self.process(image)
        except Exception as e:
            traceback.print_exc()
            return _error_result("py_error", f"process_bytes 异常: {e}")
        finally:
            # 单帧耗时诊断：真机上"建议出得慢"时凭此日志定位哪一段是瓶颈，
            # 每 20 帧打印一行总数 + 分阶段 avg/max（logcat 可搜 [perf]）。
            try:
                self._proc_ms.append((time.time() - _t0) * 1000.0)
                self._proc_frames += 1
                if self._proc_frames % 20 == 0 and self._proc_ms:
                    _ms = list(self._proc_ms)
                    _st = self._perf_snapshot()
                    _brk = " ".join(
                        f"{k}={v['avg']:.0f}/{v['max']:.0f}ms" for k, v in _st.items())
                    print(f"[perf] process avg={sum(_ms) / len(_ms):.0f}ms "
                          f"max={max(_ms):.0f}ms (last {len(_ms)}f) | {_brk}")
            except Exception:
                pass

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

    def _perf_snapshot(self) -> Dict[str, Dict[str, float]]:
        """把各阶段滚动窗口汇成 {stage: {avg, max}}，供 diag 与 logcat [perf] 共用。"""
        out: Dict[str, Dict[str, float]] = {}
        try:
            for k, dq in self._perf_ms.items():
                if dq:
                    vals = list(dq)
                    out[k] = {
                        "avg": round(sum(vals) / len(vals), 1),
                        "max": round(max(vals), 1),
                    }
        except Exception:
            pass
        return out

    def _build_waiting_result(self, image: CVImage) -> EngineResult:
        """非牌桌帧的统一出口：逐字报告「当前画面不是牌桌」，不带任何上一局内容。

        这里绝不复用 `_frame_skipper.cached`：那份缓存是**上一次真的识别过牌桌**时
        的 payload，画面已经切回大厅/结算/聊天时还把它发上屏，面板就在播旧牌局
        （用户看到的「画面 14 张手牌、面板还写着上一局 5 张」即此）。瞬态遮挡的
        防闪职责属于显示层（悬浮窗按**时间**去抖，见 mahjong_overlay 的清空帧门），
        数据层只报事实 —— 让引擎「播旧帧」去掩盖闪烁，等于用错数据换好看的 UI。
        """
        # 局况阶段机也要收到“本帧不在牌桌上”：离开牌桌持续够长才能播“本局结束”，
        # 并在下一局把旧局基准全部作废。跳过不喂的话，“结束”永远播不出且旧基准会活到
        # 新局第一帧（表现为刚开局就报“下家 打出 …”这种凭空事件）。
        try:
            match_phase = self._phase_machine.update({
                "now_ms": int(time.time() * 1000), "status": "waiting", "count": 0,
            })
        except Exception:
            match_phase = empty_phase_view("局况折算异常，不影响识别与建议")
        return EngineResult(
            image=_make_preview(image),
            result=json.dumps({
                "mode": self.mode,
                "mode_name": self._display_mode_name(),
                "platform": self.platform,
                "platform_name": get_platform(self.platform).get("name", self.platform),
                "knowledge_doctrine": "【待机推演】等待牌局开始…",
                "phase_label": "",
                "tactical_badge": "",
                "tactical_intent": "",
                "dingque": None,
                "dingque_suit": None,
                "hand": "",
                "count": 0,
                "status": "waiting",
                "shanten": None,
                "advice": [],
                "best": "",
                "commentary": None,
                "discards": "",
                "discard_count": 0,
                "remaining": get_mode(self.mode).get("wall", 108),
                "dead": 0,
                "remaining_matrix": {},
                "tile_ledger": None,
                "ting_chance": None,
                "is_drawing": False,
                "drawing_tile": None,
                "tiles": [],
                "top_score": 0.0,
                "screen": [int(image.shape[1]), int(image.shape[0])],
                "elapsed": 0.001,
                "frame_skipped": False,
                "message": "等待牌局开始",
                "match_phase": match_phase,
                "native_ready": native_solver_ready(self.mode),
            }, ensure_ascii=False),
            stage=None,
        )

    def _build_skip_result(self, image: CVImage, prev_payload: str) -> EngineResult:
        """画面无变化时复用上一帧结果，但保持 EngineResult 的合约。"""
        try:
            data = json.loads(prev_payload)
        except Exception:
            return _error_result("py_error", "上一帧结果反序列化失败")
        # 复用但标记一下"这一帧没真的识别"
        data["status"] = data.get("status") or "ok"
        data["frame_skipped"] = True
        data["platform"] = getattr(self, "platform", "tencent")
        data["platform_name"] = get_platform(getattr(self, "platform", "tencent")).get("name", "腾讯欢乐麻将")
        if getattr(self, "_last_doctrine", ""):
            data["knowledge_doctrine"] = self._last_doctrine
        return EngineResult(
            image=_make_preview(image),
            result=json.dumps(data),
            stage=None,
        )

    # ---------------------------------------------------------- 引擎侧辅助

    def _apply_conf(self, rect, label, conf, bootstrap=False, is_grid=False):
        """置信 + 牌形比例过滤：低于门槛或牌形不对的牌直接判为「不识别」，
        宁可漏识别也绝不臆测（白板刷屏、半张牌等假命中在此被挡掉）。

        三门槛机制：
          - 严格门槛 ENGINE_MIN_CONF（0.50）：默认走这条，防白板/伪命中。
          - 补漏下限 ENGINE_MIN_CONF_RELAX（0.42）：仅当引擎已建立稳定手牌
            （self._stable_hand_mpsz 非空）时才允许。这是"漏 1 张 → 自动补漏"
            的关键：若投票窗口里有 2~4 张牌稳定为 Xm，但第 N 张本来被投票器
            因 0.50 分拒了，会导致手牌数对（13/14 张）但实际少识别了一张。
            放宽门槛只在"补漏"时启用——启动期仍走严格门槛，避免噪声被当真。
            注意它是**下限**而不是「越大越松」的旋钮：门槛顺序是先过 0.50 才轮到
            它，所以它只在 [RELAX, 0.50) 这段里说话；把它抬到 ≥ ENGINE_MIN_CONF 等于
            把整条补漏通道删掉（想给暗光台面降门槛，用下面那个调试页开关）。
          - bootstrap 门槛 BOOTSTRAP_CONF（0.40）：仅冷启动（尚无稳定手牌）
            且本张属于「手牌行候选」时生效，用于打破上述死锁，详见 BOOTSTRAP_CONF。
          - 网格精密识别门槛（0.38）：针对 TencentGridDetector 整排手牌切片，
            因网格几何已严格确定，0.38 以上候选即可放心放行，彻底消灭单张反光漏识。

        调试页「严格识别门槛」关掉时，整条链路一律降到 RELAX（不再要求已建稳定
        手牌）—— 这是暗光/低画质台面上现场可用的唯一召回逃生口，默认开着。
        """
        if label is None:
            return None
        w, h = rect[2], rect[3]
        if h <= 0:
            return None
        aspect = w / float(h)
        if aspect < MIN_TILE_ASPECT or aspect > MAX_TILE_ASPECT:
            return None
        if is_grid and conf >= 0.38:
            return label
        # 调试页「严格识别门槛」开关（默认开）：开着走严格门槛 0.50；关掉则整条链路
        # 直接降到 RELAX —— 且**不再要求已建稳定手牌**（旧写法把这条放在最后，条件被
        # 上面的补漏分支完全包含，等于开关拨了没反应；而本方法的补漏分支只在「差一档
        # 置信」时才可用，暗光台面上整行都低于严格门槛时它救不了）。
        # 默认路径（strict=True）与改动前逐字相同，不改变任何既有读数。
        if self._cfg.get("strict", True):
            if conf >= ENGINE_MIN_CONF:
                return label
        elif conf >= ENGINE_MIN_CONF_RELAX:
            return label
        # 已建立稳定手牌 + 严格门槛不过 + 放宽门槛过 → 允许（补漏）
        if conf >= ENGINE_MIN_CONF_RELAX and self._stable_hand_mpsz:
            return label
        # 冷启动 bootstrap：让手牌行候选先立住稳定器，之后正常逻辑接管。
        # 受调试页「冷启动」开关控制；关掉则不走此宽门槛。
        if bootstrap and self._cfg.get("bootstrap", True) and conf >= BOOTSTRAP_CONF:
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
    def _hand_row_score(row, img_h=None):
        """返回 (avg_h, y_center, len_score) 三元组，越大越像"手牌行"。

        评判维度：
          - 平均牌高更大（手牌是连续拍摄、单张牌大）
          - y 坐标更靠下（屏幕坐标系原点在左上角，y 越大越靠下）
          - 行长度接近 13/14（手牌行专属；牌河一般 0~12 张）
        物理位置先验约束：玩家手牌必在画面最底部（yc >= 0.68 * img_h）。
        """
        if len(row) < 7:
            return None
        hs = [d[0][3] for d in row if d[0][3] > 0]
        if not hs:
            return None
        avg_h = sum(hs) / len(hs)
        ys = [d[0][1] for d in row]
        yc = sum(ys) / len(ys)
        if img_h is not None and img_h > 0 and yc < 0.68 * img_h:
            return None
        # len_score: 13 张 = 1.0，14 张 = 0.99（都算高分），其它线性衰减
        best13 = 1.0 - min(abs(len(row) - 13), abs(len(row) - 14)) / 13.0
        return (avg_h, yc, best13)

    def _pick_hand_row(self, rows, img_h=None):
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
                s = self._hand_row_score(row, img_h)
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
            s = self._hand_row_score(row, img_h)
            if s is not None:
                ys = [d[0][1] for d in row]
                yc = sum(ys) / len(ys)
                scored.append((s, row, yc))
        if not scored:
            return None
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
        is_landscape = (image.shape[1] > image.shape[0])
        if is_landscape:
            variants = [
                (0, image),
                (180, cv2.rotate(image, cv2.ROTATE_180)),
            ]
        else:
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
            self._cache_rows(det, [])
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
        self._cache_rows(det, best_rows)
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

    def _frame_diff_source(self, image: CVImage) -> np.ndarray:
        """把工作图降采样成帧差签名的输入（与识别器同一份降采样口径）。

        纯 numpy/cv2.resize，代价 ≈1ms，比一次完整识别快 ~100x —— 这正是跳帧
        机制能省钱的前提：判定「画面有没有变」根本不需要先做识别。
        """
        ih, iw = image.shape[:2]
        longest = float(max(ih, iw))
        if longest > 1100:
            inv = longest / 1100.0
            return cv2.resize(
                cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image,
                (max(1, int(iw / inv)), max(1, int(ih / inv))),
                interpolation=cv2.INTER_AREA,
            )
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image

    def _apply_hand_roi(self, image: CVImage) -> CVImage:
        """===== 用户 ROI / 平台预设专属 ROI 裁剪 =====

        优先使用用户手动拖拽指定的 ROI；若未拖拽（None），则自适应套用当前平台
        专属 hand_roi。切片异常（坏比例/坏尺寸）不阻塞主流程，回退整屏识别。

        注意：手牌行通常**不是**在这里被识别的 —— 方向验证 `_verify_orientation`
        算出的 rows 是整屏坐标、且在裁剪之前产出（`_apply_conf` 与 aspect 下限校验
        吃的都是这批整屏框，见 localtest/aspect_floor.py）。本方法裁出来的是给亮度
        校验、预览等下游用的工作图。
        """
        try:
            effective_roi = self._roi
            if effective_roi is None:
                p_roi = get_hand_roi(self.platform)
                effective_roi = (p_roi[0], p_roi[1])
            ih, _ = image.shape[:2]
            y0 = max(0, min(ih, int(effective_roi[0] * ih)))
            y1 = max(y0, min(ih, int(effective_roi[1] * ih)))
            if y1 - y0 >= MIN_ROI_HEIGHT:
                return np.ascontiguousarray(image[y0:y1, :])
        except Exception:
            pass
        return image

    def _hand_strip(self, image: CVImage) -> Tuple[CVImage, int]:
        """按当前 ROI 配置裁出手牌条带，返回 `(条带, 条带在原图中的 y 偏移)`。

        与 `_apply_hand_roi` 同一套裁切口径（只裁 y，x 全宽），差别只在这里把偏移量
        交出来：调用方要把条带上算出的框映射回整屏坐标。裁切不可用（太矮、从 0 开
        头等价于没裁）时原样返回整屏、偏移 0。

        条带被停用（`_strip_disabled`）时也返回整屏：这就是「连续不合格后不再重试」
        的唯一执行点，判据与计数都在 process 里，这里只读那个结论。
        """
        if getattr(self, "_strip_disabled", False):
            return image, 0
        try:
            effective_roi = self._roi
            if effective_roi is None:
                p_roi = get_hand_roi(self.platform)
                effective_roi = (p_roi[0], p_roi[1])
            ih, _ = image.shape[:2]
            y0 = max(0, min(ih, int(effective_roi[0] * ih)))
            y1 = max(y0, min(ih, int(effective_roi[1] * ih)))
            if y0 > 0 and y1 - y0 >= MIN_ROI_HEIGHT:
                return np.ascontiguousarray(image[y0:y1, :]), y0
        except Exception:
            pass
        return image, 0

    #: 整屏读数低于这个格数，才值得为兜底再付一次检测去试底部条带（探测地板线）。
    #: 37 帧已标注 GT 语料实测（build/_ab_channel_gt.py）：整屏读数最低 10 格
    #: （t1/s_43ce/s_a42d…），而报障截图被切到 4~5 格 —— 8 正落在两者的空档里。
    #: 于是正常帧一次检测都不多付（实测 eval_base 逐字回到 444/444、手牌 37/37），
    #: 只有整屏明显被切坏的帧才会去问第二条格网。
    HAND_CHANNEL_PROBE_FLOOR = 8
    #: 条带反标的采信下限：格数**严格多于**整屏、峰值置信**不低于**整屏，且自身
    #: 至少这么多格、至少这么多分。依据同样是那批实测：报障帧 A/B 是条带 8/7 格
    #: @0.805 反超整屏 4 格 @0.778（该信），而 GT 帧 t5 与报障帧 C 是条带多切出
    #: 一格却把峰值压低（11 格@0.96 vs 整屏 10 格@1.00、6 格@0.781 vs 5 格@0.808）——
    #: 那种「多出来的一格」正是幻影牌，只比格数就会把它放进主路径。
    #: `HAND_STRIP_MIN_CONF` 另一重身份是把「ROI 裁错了」和「牌被挡住了」分开：
    #: 配歪时条带里根本凑不出一行牌（格数 0~2）。
    HAND_STRIP_MIN_CELLS = 5
    HAND_STRIP_MIN_CONF = 0.55
    #: 连续多少帧「试了条带却没反超」就停用探测。3 是平衡值：少于 3 会把「结算
    #: 弹窗盖住手牌」这种一两帧的瞬态当成配置错，永久放弃兜底；多于 3 则整屏被
    #: 切坏的帧要每帧多付一次注定没用的条带检测才肯停。
    HAND_STRIP_MAX_REJECTS = 3

    def _hand_channel_rows(self, det, image: CVImage,
                           allow_probe: bool = True,
                           raw: Optional[list] = None) -> list:
        """手牌通道唯一入口：**整屏为主，只在整屏被切坏时才试底部条带**。

        产出永远是**整屏坐标**的 rows（条带被采信时把 y0 加回去），这一帧走了哪条路
        记进 `self._hand_channel_diag`：`chosen` 取 full / strip / probe_reject /
        probe_skipped，`strip_cells=-1` 表示「本帧没试条带」（与「试了但 0 格」必须
        区分，否则排障时会把节流的帧当成裁错的帧）。

        为什么主路径是整屏而不是条带（实测，不是猜测）：格网通道是按图像几何铺
        **等距格网**的切片器，喂整屏与喂 2000x261 条带会切出不同格数、同一格还会
        改名 —— 报障三帧（2000x899）确实是条带更好（4 格@0.778 → 8 格@0.805）。但
        同一个分辨率的 37 帧**已标注** GT 语料上方向完全相反：整屏逐张 444/444
        （100%），条带 389/445（82.5%）且普遍少 1~5 格。把「优先喂条带」当默认就是
        拿全平台精度基线去换一个机型上的个例（踩过：当时 eval_base 从 36/37 掉到
        26/37）。所以现在只做**同帧反超**：整屏读数健康就一字不改地用整屏，低于
        地板线才去问条带，而且条带必须格数更多、峰值不低、不被牌形异常判负才采信。

        代价（实测，不要拿「条带像素少」当免费午餐）：钱花在**格数 x 模板**而不是
        像素上，所以探测一次条带就是多付一个完整识别。地板线把它限在「整屏已经读出
        问题」的帧上：GT 语料 37 帧全部不触发探测（整屏最低 10 格），实测逐张回到
        444/444 且总耗时不变。本改动是**精度修复，不是性能修复**，别把它记成延迟收益
        （延迟那一刀在跳帧与方向熔断里，见 `_perf_ms["orient"]`）。

        两套格数的差异**不每帧都测**：常开对照等于每帧多付一次完整识别（实测翻倍）。
        也不在「配置切换后的首帧免费搭车」：那一帧是用户开屏后的第一眼，拿一次多余
        检测（实测 +0.8~2s）去换一个**不逐帧变**的数是拿启动体感买日志。于是对照改成
        顺路：只要本帧真的探测了条带，两个格数就都已经在手上，此刻打日志不额外付钱
        （`_hand_channel_probe_due` 就是把这次机会留给第一条被切坏的帧）。要看**每帧**
        的两套格数就拨 `hand_channel_ab`（默认关，且必须列进 `_cfg`，否则 `set_config`
        会把未知 key 静默丢弃，开关永远停在默认值）：它只加钱和日志，**不改采信规则**。

        `allow_probe` 把「本帧愿意为兜底多付几次检测」交给调用方：方向快检要连续试
        0° 与 180°，两个朝向都就地探测一次会把单帧从 2 次检测变 4 次（实测），所以
        两路都先只跑整屏；`probe_skipped` 与 `probe_reject` 的差别就是为它留的：
        前者是「本帧故意没试」，把它当成「ROI 配歪」报障会误人。

        `raw` 是「本帧已经跑过的整屏读数」：方向快检先拿整屏判几何，判过了才发现
        读数被切坏（格数低于地板线），此时拿同一批 rows 补一次反超比重新跑整屏便宜
        一个完整识别（实测）：不接这个口子，翻转帧会从 2 次检测变 3 次 —— 那是把
        延迟修复的账折回到它头上。传了 `raw` 就不再跑整屏，也不会重复记账。

        连续不合格会自动停用探测（见 `_strip_reject_streak` / `_strip_disabled`）：
        兜底不起效是**配置**问题而不是逐帧问题，每帧重试等于拿一倍延迟去买同一个已知
        结论。停用后每一帧仍靠整屏读到牌（严禁零检测帧），平台/玩法一切换就重新给机会。
        """
        if raw is None:
            try:
                raw = det.detect_all_rows(image, classify=True,
                                          allow_rotation=False)
            except Exception:
                traceback.print_exc()
                raw = []
        full_cells = max((len(r) for r in raw), default=0)
        full_top = max([c for r in raw for (_d, _l, c) in r], default=0.0)
        self._hand_channel_diag = {
            "chosen": "full", "strip_cells": -1,
            "full_cells": full_cells, "strip_y0": 0}

        # 要不要为兜底再付一次检测：整屏读数低于地板线，或者排障开关要求常开对照。
        ab_on = bool(self._cfg.get("hand_channel_ab", False))
        if not (ab_on or full_cells < self.HAND_CHANNEL_PROBE_FLOOR):
            return raw
        strip, y0 = self._hand_strip(image)
        if y0 <= 0:
            # 条带不可用：该平台没配 hand_roi，或探测已被停用（`_strip_disabled`）。
            # 此时整屏就是唯一读数，不重复跑同一张图。
            return raw
        if not allow_probe:
            self._hand_channel_diag["chosen"] = "probe_skipped"
            self._hand_channel_diag["strip_y0"] = y0
            return raw
        try:
            sraw = det.detect_all_rows(strip, classify=True,
                                       allow_rotation=False)
        except Exception:
            traceback.print_exc()
            sraw = []
        strip_cells = max((len(r) for r in sraw), default=0)
        strip_top = max([c for r in sraw for (_d, _l, c) in r], default=0.0)
        adopted = (strip_cells >= self.HAND_STRIP_MIN_CELLS
                   and strip_top >= self.HAND_STRIP_MIN_CONF
                   and strip_cells > full_cells
                   and strip_top >= full_top
                   and not self._row_shape_anomaly(
                       max(sraw, key=len) if sraw else []))
        if adopted or ab_on or getattr(self, "_hand_channel_probe_due", False):
            # 两套格数都已在手上，打这条日志不额外付钱。
            self._hand_channel_probe_due = False
            print(f"[engine] 手牌通道格网对照 {image.shape[1]}x{image.shape[0]}: "
                  f"整屏 {full_cells} 格/{full_top:.2f} vs 条带 {strip_cells} 格/"
                  f"{strip_top:.2f}（y0={y0}，采信{'条带' if adopted else '整屏'}）")
        self._hand_channel_diag = {
            "chosen": "strip" if adopted else "probe_reject",
            "strip_cells": strip_cells, "full_cells": full_cells, "strip_y0": y0}
        if not adopted:
            # 条带没反超（或只在对照里跑了一遍）：读数仍用整屏，一根格也不丢。
            return raw
        return [[((d[0], d[1] + y0, d[2], d[3]), l, c)
                 for (d, l, c) in r] for r in sraw]

    def _settle_orientation(self, image: CVImage) -> Tuple[CVImage, bool]:
        """纯几何归一：按已锁方向把图旋到规范朝向，**一帧识别都不跑**。

        返回 `(image, needs_verify)`。`needs_verify=True` 表示「方向锁在 0/180，
        本帧的方向再验证还欠着」—— 调用方只有在确认本帧**不跳帧**之后才需要去还
        （见 process 的「跳帧判定 → 补验证」顺序）。

        为什么必须把几何与验证拆开：验证每帧要付一次完整的手牌通道识别
        （实测 ~950ms，占单帧总耗时 96.3%），而跳帧判定所需的帧差签名只依赖几何
        （朝向 + hand_roi 裁片）。混在一起跑的结果是画面冻结的静默帧照样把识别做完，
        跳帧白跳 —— 实测 1000 帧里 100 个跳帧帧仍各付 997ms，一分没省。
        拆开后实测（同进程交替 A/B，localtest/_ab_skip_defer.py）：
          静默期 跳帧帧 903ms -> 5.3ms（-99.4%），非跳帧帧不变，逐帧 payload 一致；
          变化期 跳帧率两侧同为 16.7%（没有多跳一帧），逐帧 payload 一致。

        唯一的行为差在「方向锁被推翻的那一帧」，而且是新语义更保守：老顺序先验证
        再算帧差，纠正完朝向后那张图与上一帧的基线完全一样 → diff=0.0 → 直接跳帧，
        把翻转**之前**的旧 payload 继续端出去；新顺序的帧差是在旧朝向裁片上算的
        （实测 diff=7048，远超阈值）→ 不跳帧 → 老老实实识别一次。实测两侧都在这一
        帧自愈到 180°、手牌/状态/建议完全一致，只有诊断字段 `diag.orient`（记录
        「产出该 payload 那一帧的方向锁」）不同。

        本方法的几何输出与 `_verify_orientation` 逐字一致（0° 是恒等返回、180° 是
        同一次 cv2.ROTATE_180），差别只在「有没有顺手把 rows 算出来」。
        """
        # 手动方向覆盖优先级最高：与验证路径同一个早退分支，本来就不做自动探测。
        if self._orient_override is not None:
            self._orient = self._orient_override
            self._orient_zerocount = 0
            return self._rotate_to(image, self._orient_override), False

        if self._orient is None or self._orient in (90, 270):
            # 未锁定 → 没有「便宜的几何」可言（该探的方向必须探），直接走完整验证，
            # 本帧该付的识别在这里就付掉了；90/270 是竖屏锁定，老代码本来也不跑
            # 每帧快检，等价于一次 `_rotate_to`。
            return self._verify_orientation(image), False

        if not self._cfg.get("auto_orient", True):
            # 调试页关掉自动方向探测：锁定 0°、不做任何验证（与验证路径同语义）
            self._orient = 0
            self._orient_zerocount = 0
            return image, False

        # 横屏且方向已锁：几何免费，识别欠着，等跳帧判定决定要不要付
        return self._rotate_to(image, self._orient), True

    def _apply_orientation(self, image: CVImage) -> CVImage:
        """旧入口：几何归一 + 方向验证一起做（**每帧都付完整识别**）。

        保留这个名字有两个理由：① 外部脚本/测试直接调它拿「归一后的整屏图」；
        ② 它定义了改动前的语义，守卫测试用它做 A/B 的对照侧。生产热路径请走
        `_settle_orientation`（几何）+ 跳帧判定 + `_verify_orientation`（补验证）。
        """
        return self._verify_orientation(image)

    def _verify_orientation(self, image: CVImage) -> CVImage:
        """按当前锁定的方向把图旋到规范横屏朝向，并**顺手算出本帧的手牌 rows**。

        未锁定时做一次探测并锁定。手动方向覆盖（悬浮窗「旋转」按钮）优先级最高：
        直接旋到用户指定的方向，跳过自动探测。自动探测在特殊画面/异常朝向下可能
        选错，用户一眼看到牌被横置时点一下即可校正，无需等自动重探。

        快检（本方法的主体）：先按原方向跑一次 `detect_all_rows(classify=True,
        allow_rotation=False)`。绝大多数情况用户是正常持机的，原方向就是对的 ——
        这时直接锁定 0°，省掉 3 个多余方向的探测和一次重复的方向探测开销。只有原
        方向明显不对（牌数 <8 或没有长牌行）时才走 4 方向全探测。

        注意 classify 必须为 True：下面「同名牌 >4 判倒置」（dup_fail）这条自愈判据
        吃的是标签，`classify=False` 会让它永远不触发，倒置画面就会被锁死在 0°。
        代价实测 ~950ms/帧（不是早先注释里写的 ~17ms —— 那个数字属于 classify=False
        的时代，改成 True 之后注释没跟上）。正因如此，这个方法每帧无条件跑就是
        跳帧空转的根因，改为「只在确定不跳帧的帧上跑」（见 `_settle_orientation`）。
        """
        # 手动方向覆盖优先级最高：跳过自动探测，直接旋到用户指定的方向。
        if self._orient_override is not None:
            self._orient = self._orient_override
            self._orient_zerocount = 0
            return self._rotate_to(image, self._orient_override)

        ih, iw = image.shape[:2]

        if self._orient is None or self._orient in (0, 180):
            # 调试页关掉「自动方向探测」：直接用 0°（或手动覆盖），不做任何方向探测
            if not self._cfg.get("auto_orient", True):
                self._orient = 0
                self._orient_zerocount = 0
                return image

            det = self.get_hand_detector()
            rows0: list = []          # 慢路径熔断路要拿它当“本帧尽量留下的结果”，先置空
            if det is not None:
                try:
                    # 1. 优先快检当前 0° 朝向：手牌行必须在屏幕下半部，且单牌重复不超过4张
                    # 这里用「手牌通道」而不是主检测器：方向已锁定时这段每帧都跑，它顺手
                    # 算出的 rows 正是 process 要直接用的那份。两边不是同一个通道时，
                    # process 会因来源不匹配而重跑一次，等于每帧白付一个检测的钱。
                    # 喂法走统一入口 `_hand_channel_rows`（整屏为主，只在整屏被切坏时
                    # 才试底部条带，坐标一律回整屏）。本帧先只跑整屏：另一个朝向可能
                    # 才对，先探条带等于白付一个完整识别（实测翻转帧 2 次变 3 次）。
                    # 反超的补刀放在方向判据过关之后（见下面那行 `raw=rows0`）。
                    rows0 = self._hand_channel_rows(det, image, allow_probe=False)
                    n0 = sum(len(r) for r in rows0)
                    at_bottom0 = self._longest_row_at_bottom(rows0, ih)
                    has_hand0 = any(len(r) >= 7 for r in rows0)
                    dup_fail0 = False
                    shape_fail0 = False
                    if rows0:
                        longest_r = max(rows0, key=len)
                        labels0 = [d[1] for d in longest_r if d[1]]
                        if any(labels0.count(lab) > 4 for lab in set(labels0)):
                            dup_fail0 = True  # 同名牌超过4张必为倒置误判（如万字倒置误分类为2筒）
                        # 同名牌之外再加一条独立信号：整行牌形集体不对（被转了
                        # 90°/270° 时牌是「躺下」而不是「重复」，旧判据对此全隐形的）。
                        # 网格通道同行框等高等宽，这条永远不触发，不会改变现有主路径。
                        shape_fail0 = self._row_shape_anomaly(longest_r)

                    if (at_bottom0 and (has_hand0 or n0 >= 4)
                            and not dup_fail0 and not shape_fail0):
                        # 方向已定，本帧不会再为 180° 花钱：读数被切坏（格数低于地板线）
                        # 就在这里拿 `raw` 补一次条带反超（不重复跑整屏）。不补的话，
                        # 这批坏读数会被当作 rows 一路缓存给下游，报障帧的缺张永远修不到
                        # （踩过：只把反超放在默认调用里，方向快检却用 allow_probe=False）。
                        rows0 = self._hand_channel_rows(det, image, raw=rows0)
                        self._orient = 0
                        self._orient_zerocount = 0
                        # 方向被本帧证实了 → 重探计数归零。熔断开的是「连续失败」，
                        # 成功不重置就变成「总共只允许多两次全探测」，用户真的转了手机反而救不回来。
                        self._orient_reprobe_count = 0
                        self._orient_probe_skipped = False
                        self._cache_rows(det, rows0)
                        return image

                    # 2. 手机反向横屏（Reverse Landscape，如左插充电线）自愈：
                    # 当 0° 下手牌在顶部或同名牌超额时，快速检测 180° 倒转
                    # `allow_probe=False`：上面已经为「本帧朝向可能不对」跑过整屏了，
                    # 两条都就地探条带会把翻转帧从 2 次检测变 4 次（实测）。
                    img180 = cv2.rotate(image, cv2.ROTATE_180)
                    rows180 = self._hand_channel_rows(det, img180, allow_probe=False)
                    n180 = sum(len(r) for r in rows180)
                    at_bottom180 = self._longest_row_at_bottom(rows180, ih)
                    has_hand180 = any(len(r) >= 7 for r in rows180)
                    dup_fail180 = False
                    shape_fail180 = False
                    if rows180:
                        longest_r180 = max(rows180, key=len)
                        labels180 = [d[1] for d in longest_r180 if d[1]]
                        if any(labels180.count(lab) > 4 for lab in set(labels180)):
                            dup_fail180 = True
                        shape_fail180 = self._row_shape_anomaly(longest_r180)

                    if (at_bottom180 and (has_hand180 or n180 >= 4)
                            and not dup_fail180 and not shape_fail180):
                        # 与 0° 那一支同形：方向定了才补反超（拿 raw 不重复跑整屏）。
                        rows180 = self._hand_channel_rows(det, img180, raw=rows180)
                        self._orient = 180
                        self._orient_zerocount = 0
                        self._orient_reprobe_count = 0
                        self._orient_probe_skipped = False
                        self._cache_rows(det, rows180)
                        return img180
                except Exception:
                    traceback.print_exc()

            # 走到这里说明本方向得不出合格手牌行。若手牌通道与主检测器不是同一个，
            # “看不见”不等于“画面转了”（空手/遮挡/牌风未入 bank 都会让网格报 0 张），
            # 下面的 4 方向全探测仍用主检测器跑（它能看见牌河与其它行，判据更稳）。
            # 代价：这类帧会多付一次手牌通道的检测（慢路径缓存的 rows 因来源不同不复用）。

            # 3. 慢路径：原方向不对，探测 4 个方向
            #
            # 熔断必须也接在这条路上。`MAX_ORIENT_REPROBES` 原来只接在「连续 0 牌
            # 自愈」（见 `_orient_zerocount` 那处），于是本路径完全不设防：换牌/定缺
            # 弹窗把手牌行盖住的每一帧，0°+180° 都读不出合格牌行 → 每帧无条件跑一次
            # 4 方向全探测，单帧从 ~1.2s 变成 ~5-8s。这正是「牌局都变化好久了才显示」
            # 的长尾，也正是上面注释自己点名的低内存机型 OOM/SIGSEGV 主因。
            #
            # 限流只适用于「已有方向锁」的情况：`_orient is None` 是首帧，必须探。
            if (self._orient is not None
                    and self._orient_reprobe_count >= MAX_ORIENT_REPROBES):
                # 已达上限：保持现有方向锁，把本帧勉强读到的 rows 交给下游，绝不再
                # 全探测。宁可不更新这一帧，也不把整条流水线拖死。
                # 必须可见：否则面板“没变化”与“熔断了”在端上看起来一模一样。
                self._orient_probe_skipped = True
                if rows0:
                    self._cache_rows(det, rows0)
                # rows0 为空 = 整屏（以及试过的条带）一个格都没读到。新语义下整屏是
                # 主路径、本帧已经跑过一次，拿同一张图再跑一遍只会重复同一个 0 读数并
                # 多付一个完整识别（旧写法补的是「整屏还没跑过」那一刀，现在它本来就
                # 跑过了）。宁可不缓存也不伪装成“读到了牌”。
                return image
            self._orient_reprobe_count += 1
            self._orient_probe_skipped = False
            rot, image = self._probe_orientation(image)
            self._orient = rot
            self._orient_zerocount = 0
            if rot != 0:
                print(f"[engine] 方向自检锁定 {rot}°（原图疑似被旋转）")
            return image
        return self._rotate_to(image, self._orient)

    def _empty_hand_fuse(self) -> bool:
        """连续空手牌是否已经长到该硬重置（只读状态，不改任何东西）。

        三个前置条件缺一不可，每条都是踩过的坑：
          - `_match_started`：局还没开始就没有「旧局数据残留」可清，不该重置；
          - `_warmup_left <= 0`：warmup 那几帧本来就是「还不认识画面」的阶段，
            拿它当「局结束了」的证据是在拿自己的启动噪声当信号（旧写法靠这条
            把自己撞死：硬重置 + warmup 重建 = 面板空 3~6 秒，就是用户报的
            「好好的一局突然告诉我等待牌局开始」）；
          - `>= EMPTY_HAND_RESET_FRAMES`：一帧实测 1.2~2.5s，旧的 3 帧阈值就是
            3.6~7.5s 的空窗，而格网偶发错位一帧就能凑齐一串触发。

        抽成方法不是为了好看：这条 fuse 写错了不会让任何精度守卫变红（它的产物
        是「把对的东西清掉」），只能直接对这个判据做断言与变异检验。
        """
        return (getattr(self, "_match_started", False)
                and self._warmup_left <= 0
                and getattr(self, "_empty_hand_streak", 0) >= EMPTY_HAND_RESET_FRAMES)

    @staticmethod
    def _row_shape_anomaly(row) -> bool:
        """一行牌里「牌形集体不对」的判据：多数格的高度偏离本行中位数太远。

        为何需要它：现有的倒置自愈只看「同名牌 >4」。但画面转了 90°/270° 时，
        牌不是「重复了」而是「躺下了」—— 高宽关系整个反过来，这在那条判据
        里是完全隐形的（每张牌仍各自不同名）。

        为何对网格通道零误触：网格是按等距格网切片的，同一行的 w/h **逐字相同**，
        偏离中位数永远为 0 —— 这条判据在那条路上根本不可能触发。它只可能
        在 YOLO 这种逐张自由出框的通道上说话，所以加在这里不会改变现有网格主路径
        的任何行为（这正是「不产生副作用」要的可验证形式）。

        取 0.35 而不是更小：手牌牌形本身有连排遮挡、透视、高矮差，低于这个
        就会把常规牌阵当异常；而要报出「整行不对」，偏离必须是大面积且剧烈的。

        两条信号各测一种坏法，缺一条就有一种错判无人看：
          (i) 行内高度彼此偏离（遮挡/半张牌混在行里）；
          (ii) 整行「躺下」（被转了 90°/270°）。只看 (i) 测不到 (ii)：牌一起躺
               下时行内高度仍然很一致，偏离中位数接近 0。麻将牌是竖长的
               （w/h ≈ 1/1.35 ≈ 0.74），躺倒后的框普遍宽大于高，于是用 w/h 判。
        网格通道按 pitch x (pitch*1.35) 切格，w/h 恒为 0.74 —— 两条都在那条路上
        不可能触发，所以接进 `_verify_orientation` 不改变现有网格主路径的行为。
        """
        hs = sorted(float(d[0][3]) for d in row
                    if len(d[0]) >= 4 and d[0][3] > 0 and d[0][2] > 0)
        if len(hs) < 6:
            return False
        med = hs[len(hs) // 2]
        if med <= 0:
            return False
        bad = sum(1 for h in hs if abs(h - med) / med > 0.35)
        if bad >= len(hs) * 0.6:
            return True
        ars = sorted(float(d[0][2]) / float(d[0][3]) for d in row
                     if len(d[0]) >= 4 and d[0][3] > 0 and d[0][2] > 0)
        med_ar = ars[len(ars) // 2]
        wide = sum(1 for a in ars if a >= 1.05)
        return med_ar >= 1.05 and wide >= len(ars) * 0.6

    def _heuristic_discard_advice(self, hand_mpsz: str, mode: str = "sc_hz", dingque_suit: Optional[int] = None, disc_counts=None, opponent_dingque_suits: Optional[List[int]] = None) -> List[Dict]:
        """启发式最优出牌推演：在任何张数、冷启动或非标手牌下，永远给出明确最优解。"""
        if not hand_mpsz or len(hand_mpsz) < 2:
            return []

        # 0. 四川麻将全家族：优先直通完整 SichuanAnalyzer 规则引擎
        if is_sichuan_family(mode):
            try:
                from sichuan import SichuanAnalyzer
                from sichuan.sichuan_analyzer import pool_remaining_from_visible
                hand_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz)
                c28 = SichuanAnalyzer.counts_from_tiles(hand_indices)
                # 兜底路径必须传 pool_remaining：修复 seen_discard 恒 0 导致防守扣分失效，
                # 并使进张按牌河/副露真实扣减绝张。
                pool28 = pool_remaining_from_visible(
                    c28, disc_counts, getattr(self, "_meld_counts_34", None))
                opp_models = []
                try:
                    from sichuan.hand_range import OpponentState
                    for s_idx in (1, 2, 3):
                        odq = opponent_dingque_suits[s_idx - 1] if opponent_dingque_suits and len(opponent_dingque_suits) >= s_idx else None
                        odiscs = getattr(self, "_opponent_discards", {}).get(s_idx, [])
                        omelds = getattr(self, "_opponent_melds", {}).get(s_idx, [])
                        standing = max(1, 13 - len(omelds))
                        opp_models.append(OpponentState(
                            seat=s_idx,
                            name={1: "下家", 2: "对家", 3: "上家"}.get(s_idx, f"对手{s_idx}"),
                            dingque_suit=odq,
                            discards=odiscs,
                            melds=omelds,
                            standing_count=standing,
                        ))
                except Exception:
                    opp_models = None

                sc_res = SichuanAnalyzer.analyze_discards(
                    c28,
                    pool_remaining=pool28,
                    dingque_suit=dingque_suit,
                    opponent_dingque_suits=opponent_dingque_suits,
                    opponents=opp_models,
                )
                if sc_res:
                    return [{
                        "tile": r["tile"],
                        "ukeire": r["ukeire"],
                        "shanten": r["shanten"],
                        "ev": r["ev"],
                        "reason": r["reason"],
                        "ting_tiles": r["ting_tiles"],
                        "ting_details": r.get("ting_details", []),
                        "is_dingque": r["is_dingque"],
                        "win_equity": r.get("win_equity"),
                        "ev_gauge": r.get("ev_gauge"),
                        "danger_flow": r.get("danger_flow"),
                        "policy_prob": r.get("policy_prob"),
                    } for r in sc_res[:6]]
            except Exception:
                pass

        # 0b. 通用地方玩法（analyzer="std"）：34 型数据驱动引擎（赖子/全刻/幺九/开口）
        if get_analyzer(mode) == "std":
            try:
                from std import StdAnalyzer
                rules = get_mode(mode)
                c34 = TileCollection.from_mpsz(hand_mpsz).tiles34
                if sum(c34) % 3 == 2:
                    std_res = StdAnalyzer.analyze_discards(
                        list(c34), rules, sorted(available_set(mode)))
                    if std_res:
                        return [{
                            "tile": r["tile"],
                            "ukeire": r["ukeire"],
                            "shanten": r["shanten"],
                            "ev": r["ev"],
                            "reason": r["reason"],
                            "ting_tiles": r["ting_tiles"],
                            "ting_details": r.get("ting_details", []),
                            "max_fan": r.get("max_fan", 0),
                            "is_dingque": False,
                        } for r in std_res[:6]]
            except Exception:
                pass

        tiles = parse_mpsz_tiles(hand_mpsz)
        if not tiles:
            return []

        # 1. 定缺模式：若有定缺门花色，100% 优先建议打定缺牌
        if is_dingque_mode(mode) and dingque_suit is not None and 0 <= dingque_suit <= 2:
            dq_char = ['m', 'p', 's'][dingque_suit]
            dq_name = ['万', '筒', '条'][dingque_suit]
            dq_tiles = [t for t in tiles if t.endswith(dq_char)]
            if dq_tiles:
                counts = {t: dq_tiles.count(t) for t in set(dq_tiles)}
                # 优先打出现单张的孤牌，且 1/9 优先打
                sorted_dq = sorted(set(dq_tiles), key=lambda t: (counts[t], 0 if t[0] in '19' else 1))
                return [{
                    "tile": t,
                    "ukeire": 0,
                    "shanten": 2,
                    "ev": 99000.0 - i * 100,
                    "reason": f"定缺必打{dq_name}",
                    "ting_tiles": [],
                    "is_dingque": True
                } for i, t in enumerate(sorted_dq)]

        # 2. 通用孤张 / 效用价值评分（分数越高代表越无用、越应优先打出）
        counts = {t: tiles.count(t) for t in set(tiles)}
        scored_tiles = []
        laizi_idx = get_laizi_set(mode)

        for t in set(tiles):
            suit = t[1]
            val = int(t[0]) if t[0].isdigit() else 0
            score = 0
            try:
                t_idx = mpsz_to_tile34_index(t)
            except Exception:
                t_idx = -1

            # 万能赖子百搭牌（红中/白板等）：价值极高，绝不建议弃打！
            if t_idx in laizi_idx:
                score = -999999
            # 字牌（z）：无刻子/对子时价值极低，优先打出
            elif suit == 'z':
                if counts[t] == 1:
                    score += 100
                elif counts[t] == 2:
                    score += 20
                else:
                    score -= 50
            else:
                # 数牌（m, p, s）：检查是否属于已成顺子 (面子保护机制，严防拆散已成顺子)
                in_sequence = False
                if 1 <= val <= 7 and f"{val+1}{suit}" in counts and f"{val+2}{suit}" in counts:
                    in_sequence = True
                if 2 <= val <= 8 and f"{val-1}{suit}" in counts and f"{val+1}{suit}" in counts:
                    in_sequence = True
                if 3 <= val <= 9 and f"{val-2}{suit}" in counts and f"{val-1}{suit}" in counts:
                    in_sequence = True

                if in_sequence and counts[t] == 1:
                    score -= 500  # 严厉保护顺子内不可或缺单张！

                has_adj1 = any(f"{val+d}{suit}" in counts for d in (-1, 1) if 1 <= val+d <= 9)
                has_adj2 = any(f"{val+d}{suit}" in counts for d in (-2, 2) if 1 <= val+d <= 9)

                if counts[t] == 1:
                    if not has_adj1 and not has_adj2:
                        score += 80  # 全孤张
                        if val in (1, 9):
                            score += 15  # 只有在全孤无连时，1/9 幺九孤张才优先弃打
                        elif val in (2, 8):
                            score += 5
                    elif not has_adj1 and has_adj2:
                        score += 40  # 嵌隔张
                    else:
                        score += 10  # 邻近搭子
                elif counts[t] == 2:
                    score += 25  # 对子（多余重叠搭子，优先于顺子拆牌）
                else:
                    score -= 30  # 刻子/暗刻

            # 绝张感知：若全场已见4张，为绝张，毫无进张价值
            if disc_counts and 0 <= t_idx < 34:
                tot_seen = counts[t] + disc_counts[t_idx]
                if tot_seen >= 4:
                    score += 60

            scored_tiles.append((t, score, counts[t], t_idx))

        # 过滤掉保护牌（如赖子 score < -90000），仅在全是赖子时才保留
        valid_candidates = [x for x in scored_tiles if x[1] > -90000]
        if not valid_candidates:
            valid_candidates = scored_tiles
        valid_candidates.sort(key=lambda x: -x[1])

        advice = []
        for rank, (t, sc, cnt, t_idx) in enumerate(valid_candidates[:4]):
            if t_idx in laizi_idx:
                reason = "万能赖子(保留)"
            elif t.endswith('z'):
                reason = "孤张字牌" if cnt == 1 else "多余字牌"
            elif sc >= 80:
                reason = "全孤无连"
            elif sc >= 40:
                reason = "间搭孤张"
            elif sc >= 15:
                reason = "边张/对子"
            else:
                reason = "多余牌"

            advice.append({
                "tile": t,
                "ukeire": max(0, 4 - cnt),
                "shanten": 2,
                "ev": 1000.0 - rank * 100,
                "reason": reason,
                "ting_tiles": [],
                "is_dingque": False
            })
        return advice


    def process(self, image: CVImage) -> Optional[EngineResult]:
        t0 = time.time()
        try:
            # ===== 崩溃兜底（C 层 SIGSEGV 不可被 Python try/except 捕获）=====
            # 在进入任何 cv2 操作前，用纯 numpy 校验图像结构性合法。
            # 畸形/空/坏 dtype 的图像会让 OpenCV 底层直接段错误导致进程闪退，
            # 必须在 cv2 触碰它之前拦截为 _error_result。
            if not _is_valid_image(image):
                return _error_result("decode_error",
                                     "图像数据非法（空/坏尺寸/坏 dtype），已安全跳过")

            # ===== 超大图降采样（防爆内存原生崩溃）=====
            # 必须在方向探测/切牌之前做，保证后续所有几何坐标与缓存完全一致。
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
            except Exception:
                pass

            # ===== 方向归一（旋转鲁棒性）=====
            # 必须在帧差/检测之前做，保证后续所有几何都基于规范朝向。
            # 这里只做**几何**（按已锁方向旋转），识别留给跳帧判定之后再决定要不要
            # 付 —— 否则画面冻结的静默帧也会先跑完一次完整识别才被告知「本帧可跳帧」，
            # 跳帧就成了空转（实测每帧白付 997ms）。
            raw_for_verify = image          # 未经任何旋转的原始帧，补验证的入参
            try:
                image, self._orient_verify_pending = self._settle_orientation(image)
            except Exception as e:
                traceback.print_exc()
                print(f"[engine] 方向归一异常，回退到原图: {e}")
                # 重置方向锁，下一帧重新探测
                self._orient = None
                self._orient_verify_pending = False

            # ===== 牌桌场景校验（过滤大厅/菜单/结算，加入防抖）=====
            if not self._is_mahjong_table(image):
                self._non_table_frames += 1
                # 离开牌桌（回到大厅/聊天/结算退回）：连续 >=3 帧立即硬重置，绝不拖泥带水残留旧局手牌
                need_reset = (self._non_table_frames >= 2) if not getattr(self, "_match_started", False) else (self._non_table_frames >= 3)
                if need_reset:
                    self._reset_game_state()
                # 非牌桌帧不分「首帧/后续」：一律只报待机。旧写法在首帧拿
                # `_build_skip_result(cached)` 回放上一帧牌局来「平滑过渡」，
                # 在单帧 400ms+ 的真机节奏下等于把旧牌局多播一整帧，且画面
                # 已经不是牌桌 —— 播的是错数据，不是过渡（防闪交给显示层）。
                return self._build_waiting_result(image)
            else:
                self._non_table_frames = 0

            # 全图（方向归一后）留作预览与定缺用
            full_for_preview = image

            # ===== 用户 ROI / 平台预设专属 ROI 裁剪 =====
            image = self._apply_hand_roi(image)

            start_time = time.time()

            # ===== 帧差去重 =====
            # 工作区域（与识别器同一份降采样逻辑）作为帧差基线。
            # 这里用纯 numpy 算一个粗签名，代价 ≈ 1ms，比一次完整识别快 100x。
            try:
                diff = self._frame_skipper.diff(self._frame_diff_source(image))
            except Exception:
                diff = float("inf")  # 帧差算不出来就当不动也跑完整识别

            # 帧差 diff 已在上方算出；跳帧决策延后到玩法 reload 与 detector 就绪
            # 之后再判（见下方“智能跳帧”块），确保玩法切换能即时生效、跳帧只发生在牌桌静默帧。
            self._cur_frame_diff = diff

            # ===== 动画突变帧检测（记录 diff，交由稳定器平滑，不再丢弃截断） =====
            # 注：过往版本在此直接丢弃 spike 帧并返回 animation 错误，导致换牌/定缺/选牌
            # 弹窗弹出时因整屏突变（diff高达5000+）被误判为动画突变帧而永久卡在“画面识别中，请稍候…”。
            # 现在彻底移除此丢帧截断，所有帧直接交由主识别器与多重集稳定器处理。
            if self._warmup_left <= 0:
                self._motion_guard.is_spike(diff)

            # ===== 玩法与平台切换硬重置 =====
            # 模式或平台变了 → 投票窗口 / 行锁 / 缓存全部失效，必须清空重建。
            self.mode = load_mode()
            self.platform = load_platform()
            # 房卡核对（每帧一次，口径见 `reconcile_mode_platform`）：它只算提示，
            # 绝不改 self.mode —— 改了就会出现「面板选 A、实际按 B 算」那种看起来
            # 像被篡改的行为。下面的变更检测与重置、候选牌集重推、规则计算全部
            # 拿到的就是用户声明的那个玩法。
            self.mode, self._mode_off_catalog = reconcile_mode_platform(self.mode, self.platform)
            # 出牌建议配置每帧 reload（与 load_mode 同一时机，文件极小，开销可忽略）：
            # "显示出牌建议" 与 "好牌机率(进张下限)" 由调试页经 Java 写入。
            # 注意：改动 min_ukeire 会让 build_advice 的缓存键变化 → 自动重算。
            self._advice_cfg = load_advice_config()
            if self.mode != self._prev_mode or self.platform != self._prev_platform:
                self._tile_voter.reset()
                self._hand_stab.reset()
                self._last_hand_y = None
                self._stable_hand_mpsz = ""
                self._stable_hand_count = 0
                self._advice_key = None
                self._frame_skipper = _FrameSkipper()  # 旧缓存与新玩法/新平台无关
                # 玩法的合法手牌张数/可用牌集合都变了，旧 trainer 里的
                # 历史手牌会让 diff 判定全乱，必须重建（下一帧自动建立）。
                self.trainer = None
                self._prev_mode = self.mode
                self._prev_platform = self.platform
                # 玩法/平台变了就把候选牌集重推给已存在的识别器（detector 还未
                # 创建的情况不用管，get_detector 里会调同一个入口）。
                self._apply_platform_styles(self._detector)
                # 手牌通道的白名单与玩法牌集是按 (platform, mode) 缓存的，跟着作废。
                self._hand_bank_key = None
                # 新配置下两套格网差多少格又变成了一个未知量，重排一次对照。
                self._hand_channel_probe_due = True
                # 新配置下条带可能又是好的，重新给一次机会。
                self._strip_reject_streak = 0
                self._strip_disabled = False
            avail = available_set(self.mode)
            hsizes = hand_sizes(self.mode)
            # 玩法在这家平台的房卡清单里吗。**不影响识别与建议**（牌集照用户
            # 声明的装），只决定面板标题要不要多一句「未收录」。缺字牌时先看
            # 这里与 `mode_off_catalog`，不要先改识别阈值。
            self._mode_supported = self.mode in get_supported_modes(self.platform)

            detector = self.get_detector()
            if detector is None:
                print("No templates available")
                return _error_result("py_error", "识别器初始化失败（模板库为空）")
            # 手牌行走哪个通道（网格 NCC 优先）：只影响下面取 rows 与手牌直通判定，
            # 牌河/副露/阶段探测仍用主检测器（网格通道只出一行手牌，供不出牌河）。
            hand_detector = self.get_hand_detector()

            # ===== 智能跳帧（已恢复，语义安全） =====
            # 仅当同时满足全部前提才复用上一 payload：已过 warmup、有缓存、画面冻结
            # （diff < 阈值）、本局已开始且手牌已稳定（_stable_hand_count>0）、稳定器无
            # 待确认变化（_hand_stab.pending=False）。任一不满足都强制完整识别。
            # 这样跳帧只会发生在“牌桌静默、建议已定”的帧上，杜绝旧版本跳帧死锁在开局
            # 旧建议的回归；frame_skipped 如实置位，让 Java 的 25/80ms 动态采集节奏
            # 真正生效（静默期降频采集，端到端省 CPU）。
            if (
                self._warmup_left <= 0
                and self._frame_skipper.cached is not None
                and self._cur_frame_diff < FRAME_SKIP_DIFF_THRESH
                # 累计漂移门：不是“本帧与上一帧像”，而是“本帧与上一次真的识别过的那帧
                # 像”。两个阈值同一个数，不另造新参数（理由见 `anchor_drift`）。
                and self._frame_skipper.anchor_drift < FRAME_SKIP_DIFF_THRESH
                and getattr(self, "_match_started", False)
                and self._stable_hand_count > 0
                and not self._hand_stab.pending
            ):
                self._consecutive_skips += 1
                return self._build_skip_result(image, self._frame_skipper.cached)
            self._consecutive_skips = 0

            # ===== 补做方向验证（只有确定不跳帧的帧才走到这里）=====
            # 几何已在帧首归一、帧差已判定本帧需要真识别，此时才付那一次完整的手牌
            # 通道识别（实测 ~950ms，占单帧耗时 96.3%）。验证会把**整屏坐标**的 rows
            # 缓存进 `_cached_rows`，下面取 rows 直接复用 —— 与改动前「验证在前、
            # 复用缓存」的产物逐字相同（坐标系、调用参数、来源类名都不变）。
            if self._orient_verify_pending:
                self._orient_verify_pending = False
                _t_orient = time.time()
                try:
                    # 入参必须是**未经 settle 的原始帧**：`_verify_orientation` 自己
                    # 负责判 0°/180° 并旋转，把已旋过的图递给它等于转两次 —— 实测
                    # 会让方向锁在 180↔0 之间逐帧震荡（每一帧都重付一次全套探测）。
                    settled = self._verify_orientation(raw_for_verify)
                except Exception as e:
                    traceback.print_exc()
                    print(f"[engine] 方向归一异常，回退到原图: {e}")
                    self._orient = None
                    settled = full_for_preview
                # 验证的产物才是本帧真正被识别的那张图：预览、ROI 工作图、帧差基线
                # 都要跟着它走。稳态下 settled 与帧首 settle 的产物是同一张图，下面
                # 三行等于 no-op；只有在验证推翻朝向的帧上才真正起作用。
                full_for_preview = settled
                image = self._apply_hand_roi(settled)
                try:
                    self._frame_skipper.set_baseline(self._frame_diff_source(image))
                except Exception:
                    pass
                # 本帧方向验证的耗时单独记账（含异常路径也要记，否则“验证抱错”
                # 在 perf 里表现为空白，又是一个盲区）。同时进滚动窗口（均值/峰值）
                # 与本帧单值（diag.orient_ms）：前者看趋势，后者定位“就是这一帧慢”。
                self._orient_cost_ms = (time.time() - _t_orient) * 1000.0
                self._perf_ms["orient"].append(self._orient_cost_ms)

            # 取「所有牌行」（含各家牌河），不再只取手牌行。
            # 复用方向验证（上面补做的那次）已经算好的结果，避免同帧重复检测。
            # 方向已由 `_settle_orientation`/`_verify_orientation` 锁定，也不需要再让
            # 识别器内部做旋转重试（每次重试都是一次完整检测，很贵）。
            _t_detect = time.time()
            if (self._cached_rows is not None
                    and self._cached_rows_src == hand_detector.__class__.__name__):
                rows = self._cached_rows
                self._cached_rows = None
            else:
                # 缓存没中也要走同一个入口：两条路的坐标系必须一致（整屏）。
                # 旧写法在这里直接把 hand_roi 条带喂进去，产出的 rows 是**条带坐标**，
                # 而缓存那条路产的是整屏坐标 —— 同一个 payload 字段有两种坐标系，
                # 下游亮度校验与「牌行在画面下半部」的判据全在其中一路静默失效。
                rows = self._hand_channel_rows(hand_detector, full_for_preview)
                self._cached_rows = None
            self._perf_ms["detect"].append((time.time() - _t_detect) * 1000.0)

            # 条带探测停用的账在 process 里记（**一帧一次**），不在通道里记：方向快检一帧
            # 会问两次通道，在通道里累加会把「用户转了一次手机」数成「连续两帧不合
            # 格」，一次翻转就把兜底永久关掉 —— 那是拿一个配置自适配换了一个新坑。
            # 只有 `probe_reject`（试了条带却没反超）才是「兜底在这个配置下不起效」的
            # 证据；`probe_skipped` 是调用方主动不付钱、`strip` 是成功，两者都不计数。
            # `full` 要拆成两种：条带已停用时它是唯一读数（不是新证据，账冻结到平台/
            # 玩法切换），否则说明整屏本来就健康，该把连续不合格归零重新给机会。
            # 开着 `hand_channel_ab`（每帧对照排障）时绝不触发停用：那本就是采数据用的
            # 常开探测，被熔断掐掉会留下「日志悄悄断供」的坑（踩过方向：拨开了却只
            # 能拿到前 3 帧）。
            _ch = (self._hand_channel_diag or {}).get("chosen")
            _ab = bool(self._cfg.get("hand_channel_ab", False))
            if _ch == "probe_reject" and not _ab:
                self._strip_reject_streak += 1
                if self._strip_reject_streak >= self.HAND_STRIP_MAX_REJECTS:
                    if not self._strip_disabled:
                        print(f"[engine] 手牌条带连续 {self._strip_reject_streak} 帧没能"
                              f"反超整屏，本配置下不再试它（platform={self.platform} "
                              f"mode={self.mode}）")
                    self._strip_disabled = True
            elif _ch == "strip" or _ab or (
                    _ch in ("full", "probe_skipped") and not self._strip_disabled):
                self._strip_reject_streak = 0
                self._strip_disabled = False

            # 置信过滤 + 牌形降权（低置信牌直接丢弃，宁可不识别也不臆测）。
            #
            # 牌面亮度校验：先看整行牌面是不是"亮底牌"（绝大多数麻将如此）。
            # 只有确认是亮底牌时才启用单格过滤 —— 暗色主题美术下这条判据
            # 不适用，整帧跳过，绝不至于把真牌全砍光（见 MIN_FACE_BRIGHTNESS）。
            #
            # 必须喂 `full_for_preview`：上面两条路产的都是整屏坐标，拿条带图去查
            # 整屏框就是「坐标空间不匹配」，旧实现正是栽在这里（越界读数把防伪闸门
            # 整体关掉）。亮度读数 None = 图里放不下这个框 = 没有意见，不参与均值。
            bright_all = [b for b in (_face_brightness(full_for_preview, r)
                                      for row in rows for (r, l, _c) in row
                                      if l is not None)
                          if b is not None]
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

            # 若为主力 YOLODetector 或 TencentGridDetector，手牌行直接提取，不经过针对乱序单框的 _tile_voter
            # （这里看的是「手牌通道」的类名，不是主检测器：换通道后直通语义必须跟着走）
            is_grid_det = (getattr(hand_detector, "__class__", None)
                           and hand_detector.__class__.__name__ in ("YOLODetector", "TencentGridDetector"))

            filtered = []
            for ri, row in enumerate(rows):
                fr = []
                for (r, l, c) in row:
                    lab = self._apply_conf(r, l, c,
                                          bootstrap=(ri == bootstrap_row_idx),
                                          is_grid=(is_grid_det and ri == 0))
                    # 网格识别器切出的手牌只要模板置信度 >= 0.38 即可采信，避免环境暗/阴影被亮度过滤误杀
                    # （亮度读数 None 时不过滤：图里放不下这个框不代表牌面是黑的）
                    if (lab is not None and use_brightness
                            and not (is_grid_det and ri == 0 and c >= 0.38)):
                        b = _face_brightness(full_for_preview, r)
                        if b is not None and b < MIN_FACE_BRIGHTNESS:
                            lab = None
                    fr.append((r, lab, c))
                filtered.append(fr)

            if is_grid_det:
                # TencentGridDetector 网格高精度切片直通，0 延迟透传，绝不经过历史帧投票器，
                # 确保摸打、吃碰杠、出牌毫秒级实时感知与上屏
                hand_row = filtered[0] if filtered else []
                voted_rows = [hand_row]
                hand_idx = 0 if hand_row else None
            else:
                flat = [(r, l, c) for row in filtered for (r, l, c) in row]
                try:
                    self._tile_voter.push(flat)
                    voted = self._tile_voter.vote()
                except Exception:
                    traceback.print_exc()
                    voted = flat
                voted_rows = StructuralDetector._group_rows(voted)
                hand_row = self._pick_hand_row(voted_rows, image.shape[0])
                hand_idx = None
                for _i, _row in enumerate(voted_rows):
                    if _row is hand_row:
                        hand_idx = _i
                        break

            # ===== 手牌：本帧原始标签 → 多重集稳定器定夺 =====
            raw_labels: List[str] = []
            # 必须在 `_reconcile_hand_tiles` 之前抓：那一步会把逐张标签改成稳定手牌，
            # 改完再看置信度就是「拿已经粉饰过的数据自证清白」。
            self._hand_low_conf = []
            self._hand_gate_conflict = []
            self._hand_missing = 0
            # 每帧开头先清零「沿用」标记：沿用分支会把它置 True，读到牌的正常帧
            # 那么保持 False。不在入口清，上一帧的 True 会泄漏到本帧。
            self._hand_carried_over = False
            if hand_row is not None:
                hand_row = sorted(hand_row, key=lambda d: d[0][0])
                # 先救「闸门外的牌面」：它在闸门内根本不可能读对，不先走这一步就会
                # 被下面的 `_hand_missing` 当成「读不出」，而它其实能读（实测：广东雀神
                # 挂川麻玩法时屏上的 東/北/北，全 34 下 0.98/0.59）。
                hand_row, self._hand_gate_conflict = _rescue_out_of_gate(
                    hand_detector, full_for_preview, hand_row)
                raw_labels = [d[1] for d in hand_row if d[1] is not None]
                # 框在、牌读不出：这就是用户看到的「明明 13 张只显几张」。静默少报
                # 比报错更坑（面板看起来只是“牌少了”），所以必须数出来、说出口。
                self._hand_missing = sum(1 for d in hand_row if d[1] is None)
                # 本帧手牌取区是否整体被压暗：这条事实要在「一行都没检到」时也能拿到，
                # 所以直接量取区本身，而不是量检到的牌（暗层下根本没有牌可量）。
                self._hand_band_dim = _hand_band_is_dim(
                    full_for_preview, get_hand_roi(self.platform))
                # 「第几张的**最好**模板匹配也不像牌」——本帧读数不可信的直接证据。
                # 与行均置信度不是一回事，判据与门槛的实测分布见 `HAND_UNREADABLE_CONF`。
                self._hand_low_conf = [
                    [i + 1, round(float(d[2]), 2)]
                    for i, d in enumerate(hand_row)
                    if d[1] is not None and float(d[2]) < HAND_UNREADABLE_CONF
                ]

            # 摸牌/出牌瞬间（张数切换），立刻清空空间投票历史与帧缓存，保证下一帧无任何幽灵残留
            curr_raw_n = len(raw_labels)
            if curr_raw_n > 0 and getattr(self, "_prev_raw_n", 0) > 0 and curr_raw_n != self._prev_raw_n:
                self._tile_voter.reset()
                self._frame_skipper = _FrameSkipper()
            self._prev_raw_n = curr_raw_n

            # TencentGridDetector 直接采信高精度网格整排切片结果，0 帧延迟
            if is_grid_det:
                valid_sizes_now = hsizes
                if len(raw_labels) in valid_sizes_now:
                    hand_mpsz = "".join(raw_labels)
                    self._hand_stab.stable_mpsz = hand_mpsz
                    self._hand_stab._stable_key = _counter_to_mpsz(Counter(raw_labels))
                    self._hand_stab.pending = False
                elif len(raw_labels) >= 1:
                    # 摸打过渡帧或偶发张数，直接采信当前切片，绝不回退至旧手牌死锁
                    hand_mpsz = "".join(raw_labels)
                    self._hand_stab.stable_mpsz = hand_mpsz
                    self._hand_stab._stable_key = _counter_to_mpsz(Counter(raw_labels))
                    self._hand_stab.pending = False
                else:
                    hand_mpsz = self._hand_stab.observe(raw_labels, valid_sizes_now, avail)
            else:
                hand_mpsz = self._hand_stab.observe(raw_labels, hsizes, avail)

            # 稳定器空帧宽限期内（本帧 0 牌但尚保留上帧稳定值）：引擎层降级为 partial 呈现
            hand_empty_frames = getattr(self._hand_stab, "_empty_frames", 0)

            # ===== 互斥校验接回：同字 >4 判本帧不可信，沿用上一稳定手牌 =====
            # （_check_dup_explosion 定义后曾被长期弃用；白板刷屏误判一旦进了稳定器
            # 会直接污染建议与记牌，这里在出口处把整帧判否。）
            if hand_mpsz and self._check_dup_explosion(hand_mpsz):
                hand_mpsz = self._stable_hand_mpsz or ""
                self._hand_stab.pending = True
                diag_dup_reject = True
            else:
                diag_dup_reject = False

            # ===== 对局结束 / 结算弹窗即时清空 (Instant Settlement Flush) =====
            # 对局中若手牌区连续多帧完全无牌（结算弹窗覆盖、胡牌动画结束、返回大厅），
            # 触发硬重置清空旧手牌、牌河与旧建议，防「明明不在对局却仍显示手牌」。
            #
            # 为什么阈值不能停在 3：一帧实测 1.2~2.5s，3 帧就是 3.6~7.5s 的空窗；
            # 而旧行为在触发时直接 `_reset_game_state()`（连手牌/牌河/建议/方向锁一起
            # 清）+ warmup 重建 3 帧，面板要空 3~6 秒 —— 用户看到的「莫名其妙的
            # 「等待牌局开始」」不是识别突然坏了，是自己把自己撞死了：格网偶发错位
            # 一帧就能凑齐这一串触发。
            #
            # 为什么不能干脆去掉：「结算弹窗仍被判为牌桌」这类帧只能靠这条 fuse，
            # 它是「不打了还显示旧手牌」的唯一兜底（`_is_mahjong_table` 管的是画面
            # 已经不是牌桌那一路）。所以只改长度和触发条件，不拆机制。
            if curr_raw_n == 0:
                self._empty_hand_streak = getattr(self, "_empty_hand_streak", 0) + 1
                if self._empty_hand_fuse():
                    self._reset_game_state()
                    self._empty_hand_streak = 0
            else:
                self._empty_hand_streak = 0

            # 手牌行的逐张标签与稳定手牌对齐（避免"显示的牌"和"建议打的牌"对不上）
            if hand_idx is not None and hand_mpsz:
                voted_rows[hand_idx] = _reconcile_hand_tiles(
                    voted_rows[hand_idx], hand_mpsz)
                hand_row = voted_rows[hand_idx]

            # 定缺与开局特殊阶段检测（四川麻将模式：换三张 / 选一张牌 / 定缺选门）
            # 必须在扫描弃牌前判定阶段，彻底杜绝换牌/定缺阶段中央UI被误认为弃牌
            dingque_suit, dingque_name = None, None
            is_dq_phase = False
            is_pick_phase = False
            is_swap_phase = False
            pick_candidates: List[str] = []

            # ===== 开局前阶段 vs 局中状态严格门控 =====
            # 真实定缺/换牌阶段铁证：全场无确认定缺徽章（尚未敲定选门），且检测到定缺三色色盘或金色换牌按钮；
            # 局中防护：当本局已有确凿弃牌且已有确认定缺徽章时，认定处于局中，屏蔽误触清空；
            # 但若进入真实新对局开局（全场无定缺徽章且出现定缺/换牌物理特征），立即重置旧局残留牌池。
            # 定缺徽章节流：徽章一局至多变一次，非川麻玩法更是没有定缺概念——
            # 旧实现每帧无条件全图检测（含 std 六款），纯烧 CPU 拖慢主链路响应。
            # 现在：非川麻直接 None；川麻每 4 帧重检一次，期间复用缓存。
            if is_dingque_mode(self.mode):
                self._dq_scan_tick += 1
                if self._dq_scan_tick % 4 == 1:
                    # 扫到的只是**原始观测**；能不能当缺门报出去由稳定门决定（r6dq）。
                    suit = detect_dingque(full_for_preview)[0]
                    # 朝向变了：同一个归一化取区对应屏幕上另一块像素，窗口里旧读数与
                    # 本枪不可比 → 作废重攒（单枪不成分歧，因此不会拖慢首次报出）。
                    if self._dq_hist_orient != self._orient:
                        self._dq_scan_hist.clear()
                        self._dq_hist_orient = self._orient
                    self._dq_scan_hist.append(suit)
                    self._dq_scan_count += 1
                    self._dq_scan_cache = self._stable_dingque_read()
            else:
                self._dq_scan_cache = (None, None)
            # ===== 开局前阶段检测：无死锁门控 + 命中需连续帧确认 =====
            # 旧实现用 river_locked（牌池>0 且定缺徽章存在）反向锁死阶段检测唯一入口：
            # 血流成河一家定缺后永不打缺 → 牌池永不清 → 换三张/定缺/新局永不识别。
            # 现在：阶段检测无条件运行（保留 %2 节流）；"命中阶段即清池"改为需连续
            # ≥2 次检测命中才生效，杜绝单帧误检误清新局牌池。
            if is_dingque_mode(self.mode):
                swap_p = dq_p = pick_p = False
                cands = []
                # 1. 优先检测换三张阶段（右侧金色换牌大圆按钮 / 过按钮）
                if hasattr(detector, "is_swap_phase") and detector.is_swap_phase(full_for_preview):
                    swap_p = True
                # 2. 定缺选门阶段（中央万/条/筒三色大圆盘）
                elif hasattr(detector, "is_dingque_phase") and detector.is_dingque_phase(full_for_preview):
                    dq_p = True
                # 3. 选一张牌阶段（屏幕中央大牌确认 或 候选横排）
                elif hasattr(detector, "is_pick_phase") and detector.is_pick_phase(full_for_preview):
                    try:
                        cands = detector.detect_pick_candidates(full_for_preview)
                    except Exception:
                        cands = []
                    # 【选牌阶段不变量】只有「真正能读出候选牌」时才成立。若
                    # is_pick_phase 命中但候选读不出（<2 张），说明是弹窗色盘/
                    # 大厅卡片等误触发，退回其它阶段/正常判定。
                    pick_p = len(cands) >= 2
                raw_swap = swap_p
                # ===== 换牌阶段时间迟滞 =====
                # 迟滞只配吃掉「单帧漏检」，不配延长阶段存在本身。旧值 3：换完牌
                # 之后还要把画面续在换三张上 3 个检测周期（真机一周期≈1s），面板就
                # 在「已换完」之后继续播「换三张」——用户说的「不在换牌阶段却显示换牌」。
                # 现在只续 1 帧，退出另有物理铁证作支撑（见下方张数门控）。
                if raw_swap:
                    self._swap_hold_frames = 1
                elif dq_p or pick_p:
                    self._swap_hold_frames = 0
                elif self._swap_hold_frames > 0:
                    self._swap_hold_frames -= 1
                    swap_p = True  # 迟滞保持：本帧虽漏检，仍视为换牌延续
                self._phase_cache = (swap_p, dq_p, pick_p)
                self._pick_cand_cache = cands
                self._swap_raw = raw_swap
                is_swap_phase, is_dq_phase, is_pick_phase = self._phase_cache
                pick_candidates = list(self._pick_cand_cache)

                # 连续确认以「本帧真实检测」(raw_swap / dq_p) 为准：
                # 迟滞保持出来的 swap 不参与清池计数，避免把上一帧漏检也当成换牌确检反复清池。
                if (getattr(self, "_swap_raw", False) or dq_p):
                    self._phase_confirm_frames += 1
                    if self._phase_confirm_frames >= 2 or (dq_p and curr_raw_n >= 13):
                        self._reset_game_state()
                        self._match_started = True
                        if dq_p:
                            is_dq_phase = True
                else:
                    self._phase_confirm_frames = 0

                # ===== 换三张 / 定缺的排他证据：只承认牌局账本，不承认张数推算 =====
                # 本局已有弃牌/副露账⇒ 已在摸打，两个开局阶段必然已经结束。
                #
                # 为什么必须把「立牌 >= 14 张 ⇒ 已摸牌 ⇒ 不在换牌」这条自己立的
                # 「物理铁律」拆掉（实测而非推理）：37 帧人工标注 GT 里 s_6d33 与
                # t2 两帧同时是「14 张立牌」与「换三张阶段」（整手 14/14 全对），
                # 于是该推论在本平台上为假，它把两帧的真实换牌阶段抹成了 ok。
                # 同理也不得反向用张数肯阶段：换牌弹窗盖住手牌行时只能读到 8 张，
                # 拿「不足 13 张」当反证就是「阶段来回抽搐」的来源（踩过）。
                # 张数在开局阶段只能描述「读到多少」，不能推断「在不在某个阶段」。
                total_discards = sum(self._monotonic_discards.values()) + sum(self._inferred_discards.values())
                total_melds = sum(self._meld_counts_34)
                # 账本必须确认属于本局：`_match_started` 为 False 时这些是上一局的残留，
                # 而新一局的换三张恰恰要靠连续命中去触发清池，不能被旧账锁死。
                in_play_by_ledger = (total_discards > 0 or total_melds > 0) \
                    and getattr(self, "_match_started", False)
                if in_play_by_ledger:
                    is_swap_phase = False
                    self._swap_hold_frames = 0
                    self._swap_raw = False
                    # 定缺三色盘是「这一屏正在选门」的直接视觉证据，比张数推算硬，
                    # 所以它在场时不自此否决。
                    if not dq_p:
                        is_dq_phase = False
                    self._phase_cache = (is_swap_phase, is_dq_phase, is_pick_phase)

            # 定缺状态判定：
            # 若正处于定缺选门交互中（中央出现三色大圆盘），此时玩家正在选门，尚未敲定，保持 None；
            # 其它阶段（摸打、选牌、换牌），支持用户手动 override 或通过头像右上角视觉感知定缺徽章
            if is_dingque_mode(self.mode):
                if is_dq_phase:
                    dingque_suit = None
                    dingque_name = None
                else:
                    override = getattr(self, "_dingque_override", None)
                    if override is not None and 0 <= override <= 2:
                        dingque_suit = override
                        dingque_name = DINGQUE_SUIT_NAMES[dingque_suit]
                        self._match_started = True
                    elif len(raw_labels) >= 4 or getattr(self, "_match_started", False):
                        # 复用本帧节流缓存（detect_dingque 已在上方按 4 帧节奏跑过），
                        # 不再同帧二次全图检测
                        dingque_suit, dingque_name = self._dq_scan_cache
                        if dingque_suit is not None:
                            self._match_started = True

            # 摸打状态确认：当手牌已有 >= 10 张或属于合法手牌张数（如碰/杠后的 7、4 张），且不在换牌/定缺等特殊阶段时，确认为牌局已就绪
            if not (is_dq_phase or is_swap_phase or is_pick_phase) and (len(raw_labels) in hsizes or len(raw_labels) >= 10):
                self._match_started = True


            ih, _ = image.shape[:2]
            discard_labels = []
            # 后台全图扫描的结果先收在本地，与牌行观测合成**一次** sighting 再喂账本
            # （为何必须合成，见下方「牌池双账本」注释）。
            bg_seen: List[str] = []

            # 接收后台异步完成的牌河与副露扫描结果，更新视觉累计账本
            rf = getattr(self, "_river_future", None)
            if rf is not None and rf.done():
                # 计数必须在 result() 之前：上一版放在之后，于是「后台任务抛异常被
                # 外层 except 吞掉」与「任务从未完成」在 diag 上都是 consumes=0，
                # 刚加的诊断当场把自己要查的东西盖住了。
                self._river_consumes = getattr(self, "_river_consumes", 0) + 1
                try:
                    bg_river_entries, bg_meld_entries = rf.result()
                    # 「牌河为空」必须能区分两件事：场上真没人打牌，与分类链断了。
                    # 后者曾因为 `hasattr` 兜底而静默了几百帧，不记就只会留下
                    # “记牌器怎么是空的”这一谜题。判据用提交时存下的那个检测器。
                    _rdet = getattr(self, "_river_det", None)
                    if not bg_river_entries and not hasattr(_rdet, "classify_tile"):
                        self._river_error = (
                            "牌河拿到的检测器没有分类接口（"
                            f"{type(_rdet).__name__ if _rdet is not None else 'None'}），"
                            "牌河不可能有读数")
                    else:
                        self._river_error = None
                    if bg_river_entries:
                        # 入账口径全部收进 `_apply_river_entries`：事件源走同一个方法，
                        # 两个来源不可能出现“一个更新了分区归属、另一个只更新牌面”。
                        bg_seen = self._apply_river_entries(bg_river_entries, avail)
                    if bg_meld_entries:
                        # bottom 也在表内：本家副露原本被丢掉，导致「你碰了/你杠了」
                        # 永远播不出，而且杠过之后算不出正确的立牌基数。
                        zone_to_seat = {"right": 1, "top": 2, "left": 3, "bottom": 0}
                        seat_melds: Dict[int, List[int]] = {1: [], 2: [], 3: []}
                        seat_entries: Dict[int, List[Tuple[str, int]]] = {
                            0: [], 1: [], 2: [], 3: []}
                        frame_melds = [0] * 34
                        for _region, md, n_tiles in bg_meld_entries:
                            try:
                                midx = mpsz_to_tile34_index(md)
                            except Exception:
                                continue
                            if md and midx in avail:
                                frame_melds[midx] += n_tiles
                                seat = zone_to_seat.get(_region)
                                if seat is None:
                                    continue
                                seat_entries[seat].append((md, int(n_tiles)))
                                if midx < 27:
                                    seat_melds[seat].extend([midx] * n_tiles)
                        self._opponent_melds = seat_melds
                        self._seat_meld_entries = seat_entries
                        for i in range(34):
                            if frame_melds[i] > self._meld_counts_34[i]:
                                if self._pending_melds_34[i] >= frame_melds[i]:
                                    self._meld_counts_34[i] = frame_melds[i]
                                else:
                                    self._pending_melds_34[i] = frame_melds[i]
                            elif frame_melds[i] == 0:
                                self._pending_melds_34[i] = self._meld_counts_34[i]
                except Exception as e:
                    # 不能只 pass：后台任务抛异常会被这里吞掉，表现与「牌河是空的」
                    # 完全一样。必须把原因记下来，否则断链永远查不到。
                    self._river_error = (f"认领牌河结果失败: {type(e).__name__}: {e}")[:200]
                self._river_future = None

            # 仅在非定缺/换牌/任选牌阶段且进入正式对局后扫描牌河
            allow_river_scan = (
                not is_dq_phase and not is_swap_phase and not is_pick_phase
                and (not is_dingque_mode(self.mode) or getattr(self, "_match_started", False) or dingque_suit is not None)
            )

            # 牌河/副露检测区域差分门控：未变时跳过；变动或强制间隔时异步重扫
            run_river_scan = allow_river_scan and self._should_scan_river(image)

            if run_river_scan:
                yc_lo, yc_hi = RIVER_CONF["row_yc"]
                for _i, row in enumerate(voted_rows):
                    if _i == hand_idx:
                        continue
                    ys = [d[0][1] for d in row]
                    yc = sum(ys) / len(ys) if ys else 0
                    if yc < yc_lo * ih or yc > yc_hi * ih:
                        continue
                    for (rect, label, conf) in row:
                        if label is not None and conf >= RIVER_CONF["vote_conf"]:
                            try:
                                if mpsz_to_tile34_index(label) in avail:
                                    discard_labels.append(label)
                            except Exception:
                                pass

                # 异步派发全图牌河与副露检测至独立后台线程池，主线程零等待立即返回（单帧耗时从 1~3s 压减至 0ms）
                cur_rf = getattr(self, "_river_future", None)
                if (cur_rf is None or cur_rf.done()) and hand_row and (len(hand_row) >= 4 or len(hand_mpsz) >= 4):
                    # 提交前先把要用的检测器定下来：用**本帧已解析好的手牌通道检测器**，
                    # 不用 `self._detector`。真机上主检测器是 YOLODetector，它没有
                    # `classify_tile`，而牌河/副露路径自己做分区轮廓扫描、唯一需要的就是
                    # 分类；`_classify_tile_fast` 外层那句 hasattr 兜底于是把每张静默成
                    # (None, 0.0) → 牌河永远为空 → 记牌器空白、三家听牌概率全等于先验 0.5。
                    # 实测（`localtest/probe_river_gates.py`）：同一帧四区 8 个候选，主检测器
                    # 打分全 0.0，网格检测器立刻给出 0.178~0.463。
                    # 为什么不在这里现取一个新检测器：`get_hand_detector()` 是懒构造，
                    # 在评测/守卫里会顺手把真实网格检测器建出来并顶掉调用方自己的假
                    # 检测器（实测会把手牌通道从 Fake 切到真的，账本、牌墙、双策略全变）。
                    river_det = hand_detector
                    self._river_det = river_det
                    try:
                        img_bg = full_for_preview.copy()
                        h_row_bg = list(hand_row) if hand_row else None
                        self._river_future = self._river_executor.submit(
                            self._run_bg_river_and_melds, img_bg, river_det,
                            self.mode, h_row_bg, self.platform
                        )
                        self._river_submits = getattr(self, "_river_submits", 0) + 1
                    except Exception:
                        pass

            curr_cnt = _mpsz_to_counter(hand_mpsz) if hand_mpsz else Counter()
            curr_n = sum(curr_cnt.values())

            # ===== 瞬时出牌感知：手牌张数减少（例如 14->13，玩家刚打出一张牌）=====
            # 零延迟从手牌集合差分 (prev_counter - curr_counter) 捕获刚打出的牌，写入
            # 独立的"手牌差分推断"账本（与视觉累计账本分开），毫秒级响应、杜绝牌河视觉滞后。
            # 瞬时差分限幅：该分支本身就是"张数减一"的出牌事件，一次最多计 1 张；
            # 同帧差分出多张必是识别抖动 → 只取一张代表张，其余忽略，杜绝记牌器暴涨。
            if getattr(self, "_last_stable_n", 0) in (14, 11, 8, 5, 2) and curr_n in (13, 10, 7, 4, 1):
                missing_cnt = self._last_stable_counter - curr_cnt
                for missing_tile, m_count in missing_cnt.items():
                    if m_count > 0:
                        self._inferred_discards[missing_tile] = min(self._inferred_discards[missing_tile] + 1, 4)
                        self._match_started = True
                        break
                # 给局况阶段机留一份「刚打的是哪张」：只有一型多出来才敢报牌面。
                # 碰/杠那一次手牌跳变也会走到这里（一次少两张），多型时置 None，
                # 阶段机只会播「打出一张牌（牌面待确认）」而不是张冠李戴。
                _cands = [t for t, c in missing_cnt.items() if c > 0]
                self._hand_diff_discard = _cands[0] if len(_cands) == 1 else None

            # ===== 瞬时摸牌感知：手牌张数增加（例如 13->14，玩家刚摸到一张牌）=====
            drawn_from_diff = None
            if getattr(self, "_last_stable_n", 0) in (13, 10, 7, 4, 1) and curr_n in (14, 11, 8, 5, 2):
                added_cnt = curr_cnt - self._last_stable_counter
                for added_tile, a_count in added_cnt.items():
                    if a_count > 0:
                        drawn_from_diff = added_tile
                        break
                self._match_started = True

            if curr_n in (14, 11, 8, 5, 2):
                self._match_started = True

            if curr_n > 0:
                self._last_stable_counter = curr_cnt
                self._last_stable_n = curr_n

            # ---- 摸牌精确判定 (优先使用检测器独立摸牌检测或多重集差分) ----
            # 旧口径硬编 (14,11,8,5,2)，同余只在「没有杠」时成立：杠从手里抽走 4 张，
            # 4 ≢ 0 (mod 3)，杠过一次后摸进的那一张（10）永远不在这堆数里，「摸牌」
            # 徽标与依赖 is_drawing 的战术层在那一局里再也不会亮。这里按本家副露
            # 实际扣掉的张数校正基数后再判同余：R=0 时与原集合逐值相同（不改变旧行为），
            # 只补上杠后的盲区。
            # R 取阶段机里**已双时确认**的本家副露扣牌数，不直接吃后台扫描的原始快照：
            # 副露区一帧误读就会把 R 抬高 3，未确认值会当场把奇偶判反。
            # 注意：上方那两段「瞬时出牌/摸牌感知」写的是牌河差分账本，故意不动：
            # 碰的那一帧也会呈现为「手牌少两张」，把基数校正放进那里会把副露抽走的牌
            # 当成弃牌记进牌河（污染记牌器），代价远大于收益。
            own_removed = self._phase_machine.own_removed()
            is_drawing = False
            drawing_tile = None
            grid_drawn_tile = getattr(detector, "last_drawn_tile", None)

            if curr_n > 0 and (curr_n - (self.base_hand_tiles - own_removed)) % 3 == 1:
                is_drawing = True
                if grid_drawn_tile is not None:
                    drawing_tile = grid_drawn_tile
                elif drawn_from_diff is not None:
                    drawing_tile = drawn_from_diff
                elif hand_row:
                    # 兜底：取物理最右侧那张手牌的原始标签（而非排序后的标签！）
                    sorted_hand_by_x = sorted(hand_row, key=lambda d: d[0][0])
                    drawing_tile = sorted_hand_by_x[-1][1]

            # 牌池双账本（视觉累计）：同局内只增不减 + 两帧确认 + 多帧一致回退；
            # 与手牌差分推断分账，最后逐张 max 合并到单调牌池。
            # 必须用 run_river_scan（真正跑了检测的帧）才更新：牌河未变而跳过时
            # discard_labels 为空，若仍喂给账本会被当成“牌河消失”误回退，故跳过帧不单独喂。
            # 同帧两路观测（后台全图 + 牌行）绝不能分两次喂：后一次空列表会把前一次
            # 刚凑上的两帧确认抹回 0。网格类识别器只回手牌行（牌行路径恒空），那样
            # 等于“视觉牌河整局永远确认不上 → 记牌器空转”。合成口径 = 逐型取较大值
            # （两路各自报同一张牌的实际张数，相加会把一次弃牌双计）。
            # 牌河事件源：主检测器每帧已经算出的整桌牌河框（几乎不再花额外开销）。
            # 后台 NCC 扫描仍照旧提交（它还供副露账本），但「牌河有没有读数」不再
            # 取决于那个 7~10 秒的任务能否被认领 —— 那正是记牌器空白的根因。
            # 只在扫描本帧没给出条目时才用事件结果覆盖分区归属：扫描路给了东西，
            # 它就是更权威的轮廓读数，事件路不能反过来把它冲掉。
            try:
                _ev_entries = self._river_from_events(full_for_preview)
            except Exception:
                _ev_entries = []
            self._river_event_n = len(_ev_entries)
            if _ev_entries and not bg_seen:
                bg_seen = self._apply_river_entries(_ev_entries, avail)

            if run_river_scan or bg_seen:
                if bg_seen and discard_labels:
                    _c_bg, _c_row = Counter(bg_seen), Counter(discard_labels)
                    _merged: List[str] = []
                    for _lab in set(_c_bg) | set(_c_row):
                        _merged.extend([_lab] * max(_c_bg[_lab], _c_row[_lab]))
                    self._update_visual_ledger(_merged)
                else:
                    self._update_visual_ledger(bg_seen or discard_labels)


            # 计算手牌各牌计数
            hand_counts = [0] * 34
            if hand_mpsz:
                for i in range(0, len(hand_mpsz), 2):
                    try:
                        hand_counts[mpsz_to_tile34_index(hand_mpsz[i:i + 2])] += 1
                    except Exception:
                        pass

            # 牌河记账**不做静默裁剪**：旧写法 disc_counts[idx] = min(cnt, 4-hand) 会把
            # 「同一型看到 5 张」这件物理上不可能的事抹平成 4 张，于是守恒违规永远查
            # 不出来，脏帧照常出建议（“看着对、其实错”的那类坑）。原样记账，
            # 超限与否交给牌局账本的硬门（见下方「守恒硬门」）。
            disc_counts = [0] * 34
            for lab, cnt in self._monotonic_discards.items():
                try:
                    disc_counts[mpsz_to_tile34_index(lab)] += max(0, int(cnt))
                except Exception:
                    pass
            disc_mpsz = self._labels_to_mpsz([lab for lab, cnt in self._monotonic_discards.items() for _ in range(cnt)], avail)

            hand = TileCollection.from_mpsz(hand_mpsz) if hand_mpsz else None

            # 探测对手定缺徽章与根据弃牌反推对手攻防倾向（防点炮预警）
            opponent_dingque_suits: List[int] = []
            opponent_danger_suits: List[int] = []
            if is_dingque_mode(self.mode):
                try:
                    # 对手定缺徽章同样"局中几乎不变"：每 4 帧重检一次，期间复用缓存
                    self._opdq_scan_tick += 1
                    if self._opdq_scan_tick % 4 == 1:
                        self._opdq_cache = detect_opponents_dingque(full_for_preview)
                    op_dq = list(self._opdq_cache)
                    # A8：牌河一条都没有时不得从弃牌反推断门。`disc_counts` 全零时，
                    # “某门没见过弃牌”会被当成“他家缺这一门”——恰好把「没有证据」说成
                    # 「有证据」。用户报的「牌河空、无缺角标，却已有缺万/缺筒」就这条。
                    _river_evidence = sum(max(0, int(c)) for c in disc_counts) > 0
                    disc_safe, disc_danger = infer_opponents_from_discards(disc_counts) \
                        if _river_evidence else ([], [])
                    # A7「定缺徽章跳变」本轮**没修**：`detect_opponents_dingque` 交出的是
                    # 缺门花色的列表，不带座位号；想按座位做“连续 N 次一致才锁存”，
                    # 前提是这个列表能定位到谁家。先写一个按列表下标当座位的锁存，
                    # 等于把“谁缺哪门”配错——那比跳变更糟。要做就得先把返回改成
                    # {seat: suit}，那需要新的真机标注数据。
                    opponent_dingque_suits = list(dict.fromkeys(op_dq + disc_safe))
                    opponent_danger_suits = disc_danger
                    # A8：缺门在「换三张」与「定缺」阶段本身就不可能已敲定，此时
                    # 任何对手断门读数都是凭空的。上一轮只加了「牌河无证据不反推」
                    # 这道门，但实测换三张帧的牌河已经有证据（12/26/9/7），证据门
                    # 拦不住它——真正可靠的是阶段本身。牌河再满，也不能把“还在选”
                    # 说成“已经定了”。
                    if is_swap_phase or is_dq_phase:
                        opponent_dingque_suits = []
                except Exception:
                    traceback.print_exc()

            status = "waiting" if not getattr(self, "_match_started", False) else "no_tiles"
            # 空帧宽限期（稳定手牌仍在，仅本帧 0 牌）：降级 partial 呈现，杜绝"未检测到手牌"闪现
            if (status == "no_tiles" and hand_empty_frames >= 1
                    and (self._stable_hand_mpsz or self._hand_stab.stable_mpsz)):
                status = "partial"
            message: str = "等待牌局开始" if status == "waiting" else ""
            if hand is not None:
                message = "手牌已就绪"
            elif is_dq_phase:
                message = "定缺推演中"
            elif is_swap_phase:
                message = "换牌建议推演中"
            elif is_pick_phase:
                message = "选牌建议推演中"
            commentary: Optional[str] = None
            shanten: Optional[int] = None
            advice: List[Dict] = []
            best: str = ""
            tile_count = 0
            rec_suit_name = None

            if is_swap_phase:
                # 换三张阶段：立即推算手牌并给出换牌建议（打出哪3张最划算）
                status = "swap"
                tile_count = len(hand_mpsz) // 2 if hand_mpsz else 0
                try:
                    from sichuan import SichuanAnalyzer
                    h_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz) if hand_mpsz else []
                    h_counts = SichuanAnalyzer.counts_from_tiles(h_indices)
                    swap_rec = SichuanAnalyzer.recommend_huan_san_zhang(h_counts)
                    if swap_rec and swap_rec.get("viable"):
                        # recommend_huan_san_zhang 返回键为 "tiles"(mpsz) / "tiles_cn"(中文)，
                        # 早期误读 "tiles_to_swap" 恒取空列表 → 本分支 advice 永远为空、
                        # message 拼成「换出 （…）」畸形。此处按真实键取，mpsz 供牌面渲染，
                        # 中文名供文案展示，保证 advice/best/message 三者一致且随牌局动态更新。
                        tiles_to_swap = swap_rec.get("tiles", []) or []
                        tiles_cn = swap_rec.get("tiles_cn", []) or tiles_to_swap
                        reason_str = swap_rec.get("reason", "换掉最孤立的牌")
                        message = f"换牌建议：换出 {' '.join(str(c) for c in tiles_cn)}（{reason_str}）"
                        # 以每张换出牌为一条advice条目
                        for _i, t in enumerate(tiles_to_swap):
                            cn = tiles_cn[_i] if _i < len(tiles_cn) else str(t)
                            advice.append({
                                "tile": t,
                                "ukeire": 0,
                                "shanten": 1,
                                "ev": 10000.0,
                                "reason": f"换出{cn}（{reason_str}）",
                                "ting_tiles": [],
                                "is_swap": True,
                            })
                        best = tiles_to_swap[0] if tiles_to_swap else ""
                    else:
                        message = "换牌阶段：当前手牌较好，建议过（不换）"
                        advice = [{"tile": "", "ukeire": 0, "shanten": 1, "ev": 0.0, "reason": "当前手牌较好，过（不换）", "ting_tiles": [], "is_swap": True}]
                except Exception:
                    message = "换牌建议推演中…"

            elif is_pick_phase:
                # 任选一张牌阶段：从候选牌中推荐最优的一张
                status = "pick"
                tile_count = len(hand_mpsz) // 2 if hand_mpsz else 0
                if pick_candidates and hand_mpsz:
                    try:
                        from mahjong.shanten import Shanten
                        from trainer.utils.ukeire import calculate_ukeire
                        shanten_calc = Shanten()
                        dq_suit_char = None
                        if is_dingque_mode(self.mode):
                            try:
                                from sichuan import SichuanAnalyzer
                                h_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz) if hand_mpsz else []
                                h_cnts = SichuanAnalyzer.counts_from_tiles(h_indices)
                                rec_dq = SichuanAnalyzer.recommend_dingque(h_cnts)
                                dq_suit_char = rec_dq.get("suit_char")
                            except Exception:
                                dq_suit_char = None

                        cand_evals = []
                        for cand in sorted(set(pick_candidates)):
                            try:
                                is_dq = (dq_suit_char is not None and cand.endswith(dq_suit_char))
                                test_mpsz = hand_mpsz + cand
                                test_hand_obj = TileCollection.from_mpsz(test_mpsz)
                                sh = shanten_calc.calculate_shanten(tiles_34=test_hand_obj.tiles34)
                                best_u = 0
                                t_tiles = [test_mpsz[i:i+2] for i in range(0, len(test_mpsz), 2)]
                                seen_discards = set()
                                for idx, d_tile in enumerate(t_tiles):
                                    if d_tile in seen_discards:
                                        continue
                                    seen_discards.add(d_tile)
                                    rem_tiles = "".join(t_tiles[:idx] + t_tiles[idx+1:])
                                    rem_obj = TileCollection.from_mpsz(rem_tiles)
                                    rem_sh = shanten_calc.calculate_shanten(tiles_34=rem_obj.tiles34)
                                    if rem_sh <= sh:
                                        try:
                                            u = calculate_ukeire(rem_obj)
                                            if u > best_u:
                                                best_u = u
                                        except Exception:
                                            pass
                                score_key = (1 if is_dq else 0, sh, -best_u)
                                cand_evals.append((score_key, cand, sh, best_u, is_dq))
                            except Exception:
                                pass

                        if cand_evals:
                            cand_evals.sort(key=lambda x: x[0])
                            _, best_cand, best_shanten, best_u, is_dq = cand_evals[0]
                            c_cn = tile_to_chinese(best_cand)
                            # 选牌阶段的 ukeire 来自 `calculate_ukeire`（按当前手牌+可见牌算），
                            # 它是「未现张数」的上界：对手按住的牌也算在内。写「至多」是
                            # B-P3 的区间口径，不能省略（否则面板把上界说成确定的机会数）。
                            u_str = f" · 进张至多{best_u}张" if best_u > 0 else ""
                            message = f"选牌建议：选 {c_cn}（{best_shanten}向听{u_str}）"
                            advice = []
                            for _, c, s, u, dq in cand_evals:
                                cn = tile_to_chinese(c)
                                reason_u = f"，进张至多{u}张" if u > 0 else ""
                                advice.append({
                                    "tile": c,
                                    "ukeire": u,
                                    "shanten": s,
                                    "ev": 9000.0 - s * 1000.0 + u * 10.0,
                                    "reason": f"选{cn}后{s}向听{reason_u}",
                                    "ting_tiles": [],
                                    "is_pick": True,
                                })
                            best = best_cand
                        else:
                            message = f"候选牌: {' '.join(pick_candidates)}"
                    except Exception:
                        message = f"候选牌: {' '.join(pick_candidates)}" if pick_candidates else "等待选牌…"
                elif pick_candidates:
                    message = f"候选牌: {' '.join(pick_candidates)}"
                else:
                    message = "等待选牌弹窗识别…"

            elif is_dq_phase:

                status = "dingque"
                tile_count = len(hand) if hand else (len(hand_mpsz) // 2 if hand_mpsz else 0)
                try:
                    from sichuan import SichuanAnalyzer
                    h_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz) if hand_mpsz else []
                    h_counts = SichuanAnalyzer.counts_from_tiles(h_indices)
                    rec = SichuanAnalyzer.recommend_dingque(h_counts)
                    rec_suit = rec.get("suit", "筒")
                    rec_cnt = rec.get("count", 0)
                    rec_char = rec.get("suit_char", "p")
                    rec_suit_name = rec_suit
                    message = f"推荐定缺：{rec_suit}（仅持{rec_cnt}张，断门代价最小）"
                    advice = [{
                        "tile": f"1{rec_char}",
                        "ukeire": rec_cnt,
                        "shanten": 2,
                        "ev": 99999.0,
                        "reason": message,
                        "ting_tiles": [],
                        "is_dingque": True,
                    }]
                    best = f"1{rec_char}"
                except Exception:
                    pass
            elif hand is not None and not is_swap_phase and not is_pick_phase:
                self._transient_drop_streak = 0
                tile_count = len(hand)
                status = "ok"
                _t_advice = time.time()
                commentary = self.update_trainer(hand)
                if is_sichuan_family(self.mode) and self.trainer is not None:
                    if dingque_suit is not None:
                        self.trainer.set_dingque(dingque_suit)
                    if opponent_dingque_suits:
                        self.trainer.set_opponent_dingque(opponent_dingque_suits)
                    try:
                        from sichuan.hand_range import OpponentState
                        opp_models = []
                        for s_idx in (1, 2, 3):
                            odq = opponent_dingque_suits[s_idx - 1] if opponent_dingque_suits and len(opponent_dingque_suits) >= s_idx else None
                            odiscs = getattr(self, "_opponent_discards", {}).get(s_idx, [])
                            omelds = getattr(self, "_opponent_melds", {}).get(s_idx, [])
                            standing = max(1, 13 - len(omelds))
                            opp_models.append(OpponentState(
                                seat=s_idx,
                                name={1: "下家", 2: "对家", 3: "上家"}.get(s_idx, f"对手{s_idx}"),
                                dingque_suit=odq,
                                discards=odiscs,
                                melds=omelds,
                                standing_count=standing,
                            ))
                        self.trainer.set_opponents(opp_models)
                    except Exception:
                        pass
                shanten, advice = self.build_advice(hand, disc_counts, meld_counts=self._meld_counts_34)
                self._perf_ms["advice"].append((time.time() - _t_advice) * 1000.0)
                self._stable_hand_mpsz = hand_mpsz
                self._stable_hand_count = tile_count
                if advice and isinstance(advice, list) and len(advice) > 0:
                    self._stable_best = advice[0].get("tile", "")
                self._stable_shanten = shanten
                self._partial_mpsz = ""
                self._partial_ttl = 0
            else:
                # 尚未形成新的完整合法手牌（摸打瞬间、手指遮挡、动画突变或真离场）
                # 阻尼只看「有没有一个已经提交过的稳定手牌」，不再看 `_match_started`：
                # 换三张/定缺阶段 `_match_started` 可以是 False，旧口径把这段时间的
                # 瞬态欠读完全裸露出来——弹窗盖住半排手牌的一帧就直接把 13 张降成
                # 5 张（用户说的「明明 13 张只显示 5 张」），下一帧又跳回 13 张。稳定
                # 手牌在 `_reset_game_state` 里会被清空，不会把上一局的牌带进新局。
                in_active_match = bool(self._stable_hand_mpsz)
                if in_active_match:
                    # 对局中瞬态阻尼保护：摸打出牌瞬间、手指短暂遮挡或动画跳变时，
                    # 连续 1~3 帧维持上一帧稳定决策与手牌，绝不突发闪烁到 "等待开始" 或 "未检测到手牌"！
                    #
                    # `curr_raw_n > 0` 这个前置条件必须拆掉：读到 0 张恰恰是最常见的
                    # 瞬态形状（换牌弹窗整个盖住手牌行、牌河动画闪过），旧行为在这一
                    # 格上直接掉进下面的 `no_tiles` → 面板立刻报「等待牌局开始」。阻尼
                    # 本来就是为这个设计的，把主触发情形漏在门外等于装了个不用来的保险丝。
                    # 超过窗口仍读不出 partial（本帧 raw_labels 为空）就照旧降级 no_tiles，
                    # 不伪造牌、不把旧手牌永远挂屏。
                    self._transient_drop_streak = getattr(self, "_transient_drop_streak", 0) + 1
                    if self._transient_drop_streak <= 3:
                        status = "ok"
                        hand_mpsz = self._stable_hand_mpsz
                        tile_count = self._stable_hand_count
                        advice = list(self._advice)
                        best = getattr(self, "_stable_best", "") or (advice[0]["tile"] if advice else "")
                        shanten = getattr(self, "_stable_shanten", None)
                        # 沿用旧读数时不得声称「本帧就绪」：上一版这里写死
                        # 「手牌已就绪」，而同一帧的降级标签又说「读不到手牌」，
                        # 两者同屏就是用户报的「结论跟当前手牌对不上」。
                        self._hand_carried_over = True
                        self._hand_stale_frames = self._transient_drop_streak
                        message = f"沿用上一帧手牌（本帧被遮挡/未读到，已 {self._transient_drop_streak} 帧）"
                    else:
                        # 超过 3 帧仍未恢复完整手牌，平滑降级为 partial
                        try:
                            partial_now = self._labels_to_mpsz(raw_labels, avail)
                        except Exception:
                            partial_now = ""
                        if partial_now:
                            status = "partial"
                            tile_count = len(partial_now) // 2
                            hand_mpsz = partial_now
                        else:
                            status = "no_tiles"
                            message = ""
                            advice = []
                            best = ""
                            shanten = None
                elif curr_raw_n >= PARTIAL_MIN_TILES:
                    # 连续残缺识别（手牌数 >= 6 张）：提取 partial 作为降级展示
                    try:
                        partial_now = self._labels_to_mpsz(raw_labels, avail)
                    except Exception:
                        partial_now = ""
                    if partial_now:
                        status = "partial"
                        tile_count = len(partial_now) // 2
                        hand_mpsz = partial_now
                        if not advice and tile_count >= PARTIAL_MIN_TILES:
                            try:
                                tentative_hand = TileCollection.from_mpsz(hand_mpsz)
                                t_shanten, t_adv = self.build_advice(tentative_hand, disc_counts, meld_counts=self._meld_counts_34)
                                if t_adv:
                                    advice = t_adv
                                    if t_shanten is not None:
                                        shanten = t_shanten
                            except Exception:
                                pass
                    else:
                        status = "no_tiles"
                        message = ""
                        advice = []
                        best = ""
                        shanten = None
                elif curr_raw_n > 0:
                    # 读到 1~5 张：这是「读到了、但读不全」，不是「没开局」。旧实现在这
                    # 一格直接报「等待牌局开始」并把整块手牌藏起来——牌明明在屏幕上，
                    # 用户看到的是「有手牌却显示等待开局」。partial 会带「手牌仅 N 张」
                    # 遮挡提示，是当时真实情况的描述。
                    try:
                        partial_now = self._labels_to_mpsz(raw_labels, avail)
                    except Exception:
                        partial_now = ""
                    if partial_now:
                        status = "partial"
                        tile_count = len(partial_now) // 2
                        hand_mpsz = partial_now
                    else:
                        status = "no_tiles"
                        message = ""
                        advice = []
                        best = ""
                        shanten = None
                elif not getattr(self, "_match_started", False):
                    # 本帧一张没读到、也没开局：干净处于 waiting 状态
                    self._advice = []
                    self._advice_key = None
                    self._partial_mpsz = ""
                    self._partial_ttl = 0
                    advice = []
                    best = ""
                    shanten = None
                    status = "waiting"
                    # 「读不到牌」有两种完全不同的原因：牌局真没开始，和画面被弹窗/
                    # 暗层压住。把后者说成「等待牌局开始」就是把一个未知说成一个事实，
                    # 而且是会让人误操作的那种（他会以为还要去开局，其实牌局正在跑）。
                    message = ("画面被弹窗或暗层压住，本帧读不到手牌"
                               if getattr(self, "_hand_band_dim", False)
                               else "等待牌局开始")
                else:
                    # 局中且 curr_raw_n == 0：交由上方 empty_hand_streak 判定（达到3帧时硬重置），
                    # 1~2 帧空时先不刷屏
                    status = "no_tiles"
                    message = ""
                    advice = []
                    best = ""
                    shanten = None

            # 最终兜底：仅在对局中且手牌合法时推演（换牌/选牌阶段有专用建议，不触发此兜底）
            if (getattr(self, "_match_started", False) and not advice and hand_mpsz and tile_count >= 4
                    and not is_swap_phase and not is_pick_phase):
                advice = self._heuristic_discard_advice(
                    hand_mpsz,
                    mode=self.mode,
                    dingque_suit=dingque_suit,
                    disc_counts=disc_counts,
                    opponent_dingque_suits=opponent_dingque_suits,
                )
                if shanten is None:
                    shanten = 2

            # ===== 守恒硬门：牌局账本自相矛盾的帧宁可不答，也不给错答案 =====
            # 判据用的是分析器同一份账本（trainer.ledger），不在这里另写一套守恒公式：
            # 两处口径不一致时，“面板说没事、建议说有事”会让用户无从判断该信谁。
            self._ledger = getattr(self.trainer, "ledger", None) if self.trainer is not None else None
            ledger_ok = True
            ledger_violations: List[Dict] = []
            ledger_wall: Optional[int] = None
            if isinstance(self._ledger, dict):
                # 陈旧账本不得参与硬门：拿上一帧的违例把本帧判脏，会把“偶尔少识
                # 一张”放大成“连续几秒不给建议”。签名不符就当本帧无账本（不门控、
                # 也不下发听口拆账）。
                _sig = (tuple(disc_counts), tuple(self._meld_counts_34),
                        sum(hand_counts))
                if self._ledger.get("sig") == _sig:
                    ledger_ok = bool(self._ledger.get("ok", True))
                    ledger_violations = list(self._ledger.get("violations") or [])
                    ledger_wall = int(self._ledger.get("wall_remaining", 0))
                else:
                    self._ledger = None
                    ledger_wall = None
            if not ledger_ok and status == "ok":
                self._dirty_streak = getattr(self, "_dirty_streak", 0) + 1
                advice = []
                best = ""
                _detail = "；".join(str(v.get("detail") or "") for v in ledger_violations[:2])
                message = f"可见牌记账自相矛盾，本帧不给建议（{_detail}）"
                # 牌河账本是单调累加的：一旦某张牌被误识别进去，它会常驻整个牌局，
                # 硬门就会把“本帧拒答”变成“整局拒答”。连续多帧同一处矛盾时，超出实物
                # 上限的那几张一定是误识（一种牌只有 4 张），裁回上限并告知已自愈。
                if self._dirty_streak >= DIRTY_HEAL_FRAMES:
                    healed = self._heal_over_count(ledger_violations, hand_counts)
                    if healed:
                        ledger_violations = []
                        ledger_ok = True
                        self._dirty_streak = 0
                        # 账本已被裁改，本帧不再拿旧账本的牌墙量去驱动末期警报
                        self._ledger = None
                        ledger_wall = None
                        message = (f"已自动剪除误识别的多余牌（{ '、'.join(healed) }），"
                                   f"记账重新自洽")
            else:
                self._dirty_streak = 0

            # ===== 手牌可读性硬门：有牌「连最像的模板都不够像」时宁可不答 =====
            # 与守恒硬门同一条立场：少一句建议的代价远小于给一句错建议。触发条件用
            # **本帧逐张分数**（实测分布写在 `HAND_UNREADABLE_CONF` 上方），不是行平均
            # 置信度——后者分不开「读对但画面暗」与「读错」，拿它当触发就是假测量。
            # 实测 57 帧（20 帧真机 + 37 帧腾讯 GT）：只有被大字动画压住的两帧被标，
            # 其余 55 帧零误报（`localtest/measure_hand_uncertain.py`）；这条判据与
            # 「真值表里只有已知缺陷帧该被标」由 `test_multi_hand_guard` 钉住。
            # ===== 手牌可读性硬门：读数不完整时宁可不答 =====
            _bad = list(getattr(self, "_hand_low_conf", []))
            _miss = int(getattr(self, "_hand_missing", 0))
            _conf = list(getattr(self, "_hand_gate_conflict", []))
            if status == "ok" and (_bad or _miss):
                advice = []
                best = ""
                bits = []
                if _miss:
                    bits.append(f"有 {_miss} 张牌面读不出来")
                if _bad:
                    _pos = "、".join(str(p[0]) for p in _bad)
                    _c = min(float(p[1]) for p in _bad)
                    bits.append(f"第 {_pos} 张读不清（相似度 {_c:.2f}）")
                # 不写「动画过去会自动恢复」这一句：实测有三个成因都会触这道门——
                # 牌面被动画压住、玩法声明与牌桌不符，以及牌面确实不在模板库里。
                # 后两者不会自己恢复，只能用户改玩法/换平台；报错了因等于给了一个
                # 错的行动指令。
                if _conf:
                    _names = "、".join(sorted({p[1] for p in _conf}))
                    tail = (f"屏上 {_names} 不在当前玩法（{get_mode(self.mode).get('name', self.mode)}）"
                            "的牌集里，已按实际牌面读；请把玩法改成你在打的那个")
                else:
                    tail = "若是玩法与牌桌不符，改成实际在打的那个"
                message = "、".join(bits) + f"，本帧读数不完整，不给建议；{tail}"

            # 标记"最优"那张牌（最高 EV 或最高 ukeire），UI 上加"最优"角标
            if advice:
                if is_sichuan_family(self.mode) or getattr(self.trainer, "analyzer", "") == "std":
                    # 规则引擎（川麻/std）已按 EV 排序，advice[0] 即最优
                    best = str(advice[0].get("tile") or "")
                else:
                    top_ukeire = max((a.get('ukeire') or 0) for a in advice)
                    for a in advice:
                        if (a.get('ukeire') or 0) == top_ukeire:
                            best = str(a.get('tile') or "")
                            break

            # ---- 剩余牌 / 绝张统计（基于当前玩法的可见域）----
            # 可见域 = 自己手牌 + 牌河所有打出的牌 + 各家副露（碰/杠亮牌）。
            # 墙内剩余 = 该玩法总牌数 - 可见；绝张 = 某型 4 张已全部可见，
            # 这种牌既不可能摸到、也不该被推荐打出（进张已为 0）。
            meld_counts_out = self._meld_counts_34
            avail_list = sorted(avail)
            known = sum(hand_counts[i] + disc_counts[i] + meld_counts_out[i] for i in avail_list)
            wall_total = len(avail_list) * 4
            remaining = max(0, wall_total - known)
            dead = sum(1 for i in avail_list if hand_counts[i] + disc_counts[i] + meld_counts_out[i] >= 4)

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
            # discard_count 必须与 discards 串同源（持久账本 _monotonic_discards）：
            # 牌河门控跳过的帧 discard_labels 为空，若直接用它会让 discard_count=0 而
            # discards 仍有牌，两字段自相矛盾。改由 disc_mpsz 反算，跨跳过帧稳定一致。
            discarded_labels_out = [lab for lab in _mpsz_to_counter(disc_mpsz).elements()]
            if (len(history_lens) >= DISCARD_HISTORY_MIN_SAMPLES
                    and history_lens and current_disc_len > 0):
                max_hist = max(history_lens)
                # 牌河只增不减；若当前帧少于历史最大值 ≥MIN_DROP_DELTA，视为漏抓
                if max_hist - current_disc_len >= MIN_DROP_DELTA and max_hist > current_disc_len:
                    # 选最长历史 disc_mpsz，但只按逐张差额回补（最多 2 张），绝不整串替换：
                    # 整串替换会把两帧间真实的出牌抹掉，造成牌河"回退又前进"式漂移
                    best_idx = history_lens.index(max_hist)
                    stable_mpsz = self._discard_history[best_idx]
                    cur_c: Counter = Counter()
                    for k in range(0, len(disc_mpsz), 2):
                        if k + 2 <= len(disc_mpsz):
                            cur_c[disc_mpsz[k:k + 2]] += 1
                    fix_c: Counter = Counter()
                    for k in range(0, len(stable_mpsz), 2):
                        if k + 2 <= len(stable_mpsz):
                            fix_c[stable_mpsz[k:k + 2]] += 1
                    restore = fix_c - cur_c
                    if sum(restore.values()) > 2:
                        restore = Counter()  # 差异过大视为历史失真，不回补
                    fallback_labels = []
                    for lab, cnt_r in restore.items():
                        fallback_labels.extend([lab] * cnt_r)
                    fc = list(disc_counts)
                    for lab in fallback_labels:
                        try:
                            fc[mpsz_to_tile34_index(lab)] += 1
                        except Exception:
                            pass
                    disc_mpsz_out = disc_mpsz + "".join(fallback_labels)
                    disc_counts_out = fc
                    discarded_labels_out = list(discard_labels) + fallback_labels
                    # 用稳定值重算 known / remaining / dead（副露账本不变）
                    known = sum(hand_counts[i] + fc[i] + meld_counts_out[i] for i in avail_list)
                    remaining = max(0, wall_total - known)
                    dead = sum(1 for i in avail_list if hand_counts[i] + fc[i] + meld_counts_out[i] >= 4)
            self._discard_history.append(disc_mpsz)

            # ---- 全场记牌矩阵面板（含万/筒/条，以及字牌/红中，仅在对局开始后生成）----
            # 废除"显示级整体清零"：对局中单帧识别失败不再把记牌器/牌河清空，
            # 而是沿用上一可信矩阵并打 stale 标记（Dart 侧另有去抖+信号中断双重保险）。
            in_grace = (status == "partial" and hand_empty_frames >= 1)
            if tile_count == 0 or status in ("waiting", "no_tiles") or not hand_mpsz:
                if self._last_trusted_matrix is not None and (in_grace or self._match_started):
                    stale = self._last_trusted_matrix
                    remaining_matrix = stale.get("matrix") or {}
                    disc_mpsz_out = stale.get("discards", "")
                    discarded_labels_out = [lab for lab in _mpsz_to_counter(
                        disc_mpsz_out).elements()]
                    remaining = stale.get("remaining", remaining)
                    dead = stale.get("dead", dead)
                    # disc_counts_out 沿用本帧（已基于单调池重算，与 stale 牌河同源一致）
                    diag_stale = True
                else:
                    remaining_matrix = {}
                    disc_mpsz_out = ""
                    disc_counts_out = [0] * 34
                    discarded_labels_out = []
                    remaining = get_mode(self.mode).get("wall", 108)
                    dead = 0
                    self._last_trusted_matrix = None
                    diag_stale = False
            else:
                diag_stale = False
                # 重新精准计算当前生效手牌的计数，杜绝 partial 或未归一态导致的 hand_counts 漏计全 4 bug
                hand_counts_final = [0] * 34
                for i in range(0, len(hand_mpsz), 2):
                    try:
                        hand_counts_final[mpsz_to_tile34_index(hand_mpsz[i:i + 2])] += 1
                    except Exception:
                        pass
                def _pool_left(i: int) -> int:
                    """该型牌池未现张数（= 已现的反面，含被人拿着的部分）。

                    不在本玩法牌集里的型恒为 0。旧写法只对字牌做了牌集过滤，数牌直接
                    算 4−已见，于是「二人麻将（筒条）」这种根本没有万子的玩法会给
                    万子报满 4 张活牌——记牌器上凭空多出 36 张不存在的牌。
                    字牌同理：不再硬编码某个玩法 key，而是问牌集本身。
                    """
                    if i not in avail:
                        return 0
                    return max(0, 4 - (hand_counts_final[i] + disc_counts_out[i] + meld_counts_out[i]))

                honor_types = [i for i in range(27, 34) if i in avail]
                z_counts = [_pool_left(i) for i in honor_types]
                z_names = [tile_to_chinese(tiles34_index_to_mpsz(i)) for i in honor_types]

                remaining_matrix = {
                    "m": [_pool_left(i) for i in range(0, 9)],
                    "p": [_pool_left(i) for i in range(9, 18)],
                    "s": [_pool_left(i) for i in range(18, 27)],
                    "z": z_counts,
                    # 字牌格子名按牌集给出（三人扣只含东南西北白中、血流红中只含中），
                    # 不让 UI 靠「长度==1」去猜是哪张。
                    "z_names": z_names,
                }
                known = sum(hand_counts_final[i] + disc_counts_out[i] + meld_counts_out[i] for i in avail_list)
                remaining = max(0, wall_total - known)
                dead = sum(1 for i in avail_list if hand_counts_final[i] + disc_counts_out[i] + meld_counts_out[i] >= 4)
                self._last_trusted_matrix = {
                    "matrix": remaining_matrix,
                    "discards": disc_mpsz_out,
                    "discard_count": len(discarded_labels_out),
                    "remaining": remaining,
                    "dead": dead,
                }
            # ===== 牌局账本下发（每类牌：已现/未现/牌墙可摸/在别人手上(上界)/鬼牌）=====
            # 只下发本帧签名相符的账本（ledger_ok/violations 已由上方硬门按帧认领），
            # 陈旧账本宁不下发，也不能让它去驱动「牌墙还剩几张」这类末期判断。
            tile_ledger_view = None
            if isinstance(self._ledger, dict):
                _cells: Dict[str, Dict] = {}
                for _i, _r in (self._ledger.get("by_type") or {}).items():
                    if int(_r.get("total", 0)) <= 0 and int(_r.get("seen", 0)) <= 0:
                        continue
                    _cells[str(_i)] = {
                        "tile": _r["tile"], "name": _r["name"],
                        "total": _r["total"], "seen": _r["seen"],
                        "unseen": _r["unseen"],
                        "wall": _r["wall_lo"], "wall_hi": _r["wall_hi"],
                        "in_hands": _r["opp_hi"], "wall_only": _r["wall_only"],
                        "is_wild": _r["is_wild"],
                    }
                tile_ledger_view = {
                    "by_type": _cells,
                    "seen_total": self._ledger.get("seen_total", 0),
                    "unseen_total": self._ledger.get("unseen_total", 0),
                    "standing_total": self._ledger.get("standing_total", 0),
                    "wall_remaining": self._ledger.get("wall_remaining", 0),
                    "rounds_left": self._ledger.get("rounds_left", 0),
                    "players": self._ledger.get("players", 0),
                    "wild": self._ledger.get("wild", {}),
                    "opp_known": self._ledger.get("opp_known", False),
                    # 「只能自摸」类牌汇总：定缺门带来的那张结论直接可上面板。
                    # 在 Python 里算好，不让 UI 自己遍历重算一遍（两处口径会飘）。
                    "wall_only_types": sum(
                        1 for _r in _cells.values()
                        if _r["wall_only"] and int(_r["unseen"]) > 0),
                    "wall_only_unseen": sum(
                        int(_r["unseen"]) for _r in _cells.values() if _r["wall_only"]),
                    "ok": ledger_ok,
                    "violations": ledger_violations,
                }

            tenpai_alert = None
            swap_advice = None
            defense_radar = []
            hot_tiles = []
            dead_tiles = []
            bayesian_hand_ranges = []
            top_win_equity = 0.5
            top_ev_gauge = None
            top_danger_flow = None

            if is_sichuan_family(self.mode) and tile_count > 0 and status not in ("waiting", "no_tiles") and hand_mpsz:
                try:
                    from sichuan import SichuanAnalyzer
                    pool_rem_27 = [
                        max(0, 4 - (hand_counts_final[i] + disc_counts_out[i] + meld_counts_out[i]))
                        for i in range(27)
                    ]
                    # 功能A：查大叫 / 查花猪生死避坑雷达
                    # 牌墙真值优先取账本：`remaining` 是「未现总数」，里面还含着三家
                    # 手上攥着的牌（川麻 3 家×10+ 张），拿它当牌墙会把警报整整晚一截 30 张
                    # 才触发——末期该避险的时候面板还是一片绿。牌河稳固兜底回补过几张，
                    # 就从账本牌墙里同量减掉（那几张已变成可见牌）。
                    wall_for_alert = remaining
                    if ledger_wall is not None:
                        wall_for_alert = max(
                            0, ledger_wall - max(0, sum(disc_counts_out) - sum(disc_counts)))
                    tenpai_alert = SichuanAnalyzer.check_tenpai_alert(
                        hand_counts_final[:27],
                        tiles_remaining_in_wall=wall_for_alert,
                        dingque_suit=dingque_suit,
                        pool_remaining=pool_rem_27,
                    )
                    # 功能B：活牌厚度透视表（活跃热张与绝张）
                    matrix_data = SichuanAnalyzer.generate_tile_matrix(
                        hand_counts_final[:27],
                        disc_counts=disc_counts_out[:27],
                        meld_counts=meld_counts_out[:27],
                    )
                    hot_tiles = matrix_data.get("hot_tiles", [])
                    dead_tiles = matrix_data.get("dead_tiles", [])

                    # 功能1：防点炮雷达全息评级
                    # pool_evidence：本局已可靠观测到的弃牌总数，用于证据门控——
                    # 牌河未稳定读入时抑制「未见即危险」的凭空警告。
                    defense_radar = SichuanAnalyzer.evaluate_defense_radar(
                        hand_counts_final[:27],
                        pool_remaining=pool_rem_27,
                        opponents_dingque=opponent_dingque_suits,
                        opponents_danger_suits=opponent_danger_suits,
                        pool_evidence=int(sum(disc_counts_out)),
                    )

                    # 功能2：博弈级换三张推荐（严格限定仅在真正的换牌阶段生成，绝不污染定缺、选牌或摸打阶段）
                    if is_swap_phase:
                        swap_advice = SichuanAnalyzer.recommend_huan_san_zhang(hand_counts_final[:27])

                    # 附加大牌出牌决策安全评级
                    if advice and defense_radar:
                        radar_map = {r["tile"]: r for r in defense_radar}
                        for adv in advice:
                            t = adv.get("tile")
                            if t in radar_map:
                                adv["defense_level"] = radar_map[t]["level"]
                                adv["defense_reason"] = radar_map[t]["reason"]
                    # 组装高精实时对手模型（包含对手实际弃牌、副露、定缺门与听牌推导）
                    opp_models = []
                    try:
                        from sichuan.hand_range import OpponentState
                        for s_idx in (1, 2, 3):
                            odq = opponent_dingque_suits[s_idx - 1] if opponent_dingque_suits and len(opponent_dingque_suits) >= s_idx else None
                            odiscs = getattr(self, "_opponent_discards", {}).get(s_idx, [])
                            omelds = getattr(self, "_opponent_melds", {}).get(s_idx, [])
                            standing = max(1, 13 - len(omelds))
                            opp_models.append(OpponentState(
                                seat=s_idx,
                                name={1: "下家", 2: "对家", 3: "上家"}.get(s_idx, f"对手{s_idx}"),
                                dingque_suit=odq,
                                discards=odiscs,
                                melds=omelds,
                                standing_count=standing,
                            ))
                    except Exception:
                        opp_models = None

                    bayesian_hand_ranges = SichuanAnalyzer.get_bayesian_hand_ranges(
                        hand_counts_final[:27],
                        pool_remaining=pool_rem_27,
                        opponent_dingque_suits=opponent_dingque_suits,
                        opponents=opp_models,
                    )
                    if advice and isinstance(advice, list) and len(advice) > 0:
                        top_win_equity = advice[0].get("win_equity", 0.5)
                        top_ev_gauge = advice[0].get("ev_gauge")
                        top_danger_flow = advice[0].get("danger_flow")
                    # 若单手牌推荐未携带 ev_gauge，根据全局向听与牌池动态补齐
                    if top_ev_gauge is None:
                        try:
                            from sichuan.equity_radar import WinEquityGauge
                            sh_cur = shanten if shanten is not None else 2
                            opp_probs = [opp.estimate_tenpai_probability() for opp in opp_models] if opp_models else None
                            eq = WinEquityGauge.calculate_win_equity(
                                sh_cur, None, 0, pool_rem_27,
                                opp_probs
                            )
                            # 兜底仪表盘也必须带口径：这里的 eq 是纯解析式估值（PVN 不参与），
                            # 事实字段取自 advice[0] 的账本听口结论——拿不到就不写，绝不编一个
                            # 百分比充数（未标定数字当胜率展示是 B-P3 要消除的根治问题）。
                            _fb_tc = advice[0].get("ting_chance") if isinstance(advice[0], dict) else None
                            _fb_facts = []
                            if isinstance(_fb_tc, dict) and _fb_tc.get("text"):
                                _fb_facts.append(str(_fb_tc["text"]))
                            _fb_dlevel = (top_danger_flow.get("danger_level")
                                          if isinstance(top_danger_flow, dict) else None)
                            top_ev_gauge = WinEquityGauge.evaluate_gauge(
                                eq,
                                basis="analytical",
                                facts=_fb_facts,
                                deal_in_level=_fb_dlevel,
                            )
                            top_win_equity = eq
                        except Exception:
                            pass
                except Exception:
                    pass
            elif tile_count > 0 and status not in ("waiting", "no_tiles") and hand_mpsz:
                # 针对非川麻模式（国标、日麻、大众、武汉、长沙、二人麻将等）的全息防点炮雷达
                try:
                    ratings = []
                    for t in range(34):
                        if hand_counts_final[t] <= 0:
                            continue
                        rem = max(0, 4 - (hand_counts_final[t] + disc_counts_out[t]))
                        seen = 4 - rem - hand_counts_final[t]
                        num = (t % 9) + 1 if t < 27 else 0
                        is_edge = (t < 27 and num in (1, 9)) or (t >= 27)
                        is_middle = (t < 27 and num in (4, 5, 6))

                        if rem == 0 or seen >= 3:
                            lvl = "SAFE"
                            reason = f"现物绝张：场上已见 {seen} 张，绝不点炮"
                        elif seen >= 2:
                            lvl = "SAFE"
                            reason = f"安全熟张：场上已见 {seen} 张，常规防守"
                        elif is_middle and seen == 0:
                            lvl = "DANGER"
                            reason = f"高危生张：中心张纯生张(0见)，点炮率高！"
                        elif is_edge and seen >= 1:
                            lvl = "SAFE"
                            reason = f"偏张已见：见 {seen} 张，相对安全"
                        elif seen == 0:
                            lvl = "DANGER" if t < 27 else "SUSPICIOUS"
                            reason = f"生张：尚未出现过，存在暗叫风险"
                        else:
                            lvl = "SUSPICIOUS"
                            reason = f"一般生熟张：场上已见 {seen} 张"

                        t_mpsz = tiles34_index_to_mpsz(t)
                        ratings.append({
                            "tile": t_mpsz,
                            "tile_cn": tile_to_chinese(t_mpsz),
                            "level": lvl,
                            "reason": reason,
                            "seen": seen,
                            "remaining": rem,
                        })
                    defense_radar = ratings
                except Exception:
                    pass

            # 附加出牌决策安全评级
            if advice and defense_radar:
                radar_map = {r["tile"]: r for r in defense_radar}
                for adv in advice:
                    t = adv.get("tile")
                    if t in radar_map:
                        adv["defense_level"] = radar_map[t]["level"]
                        adv["defense_reason"] = radar_map[t]["reason"]

            defense_map = {r["tile"]: r["level"] for r in defense_radar} if defense_radar else {}

            # ===== 听牌·绝张雷达详情推演 =====
            ting_details = []
            if advice and advice[0].get("ting_details"):
                ting_details = advice[0].get("ting_details", [])
            elif shanten == 0 and hand_mpsz and tile_count > 0:
                if is_sichuan_family(self.mode):
                    try:
                        from sichuan import SichuanAnalyzer
                        pool_rem_27 = [
                            max(0, 4 - (hand_counts_final[i] + disc_counts_out[i] + meld_counts_out[i]))
                            for i in range(27)
                        ]
                        w_dict = SichuanAnalyzer.find_waiting_tiles(
                            hand_counts_final[:27], 0, pool_rem_27, dingque_suit)
                        for w_t, rem in w_dict.items():
                            w_str = tiles34_index_to_mpsz(w_t)
                            w_fan = SichuanAnalyzer.calculate_fan(
                                hand_counts_final[:27], w_t, 0, dingque_suit)
                            ting_details.append({
                                "tile": w_str,
                                "name": tile_to_chinese(w_str),
                                "remaining": rem,
                                "is_dead": (rem == 0),
                                "fan": w_fan,
                            })
                    except Exception:
                        pass
                elif get_analyzer(self.mode) == "std":
                    # 通用地方玩法：用 StdAnalyzer 精确推叫口（赖子/全刻/幺九均参与
                    # 试胡），绝不得回退到无规则的旧 Shanten 口算；带番数供 UI 展示。
                    try:
                        from std import StdAnalyzer
                        rules_td = get_mode(self.mode)
                        pool_rem_34 = [
                            max(0, 4 - (hand_counts_final[i] + disc_counts_out[i]))
                            for i in range(34)
                        ]
                        c_td = list(hand_counts_final)
                        w_dict = StdAnalyzer.find_waits(
                            c_td, 0, rules_td, sorted(avail), pool_rem_34)
                        for w_t in sorted(w_dict):
                            w_str = tiles34_index_to_mpsz(w_t)
                            c_test = list(c_td)
                            c_test[w_t] += 1
                            w_fan, w_names = StdAnalyzer.calc_fan(
                                c_test, 0, rules_td)
                            ting_details.append({
                                "tile": w_str,
                                "name": tile_to_chinese(w_str),
                                "remaining": w_dict[w_t],
                                "is_dead": (w_dict[w_t] == 0),
                                "fan": w_fan,
                                "fan_names": w_names,
                            })
                    except Exception:
                        pass
                else:
                    # 历史 2p/3p 兼容玩法（无规则字段）：旧向听逐张估口
                    try:
                        h_tc = TileCollection.from_mpsz(hand_mpsz)

                        own_34 = h_tc.tiles34
                        for d in avail:
                            if own_34[d] >= 4:
                                continue
                            drawn_34 = own_34[:]
                            drawn_34[d] += 1
                            if calculate_shanten(TileCollection(drawn_34)) == -1:
                                rem = max(0, 4 - (own_34[d] + disc_counts_out[d]))
                                w_str = tiles34_index_to_mpsz(d)
                                ting_details.append({
                                    "tile": w_str,
                                    "name": tile_to_chinese(w_str),
                                    "remaining": rem,
                                    "is_dead": (rem == 0),
                                })
                    except Exception:
                        pass

            # ===== 听牌面板结论（牌局账本口径）=====
            # advice[0] 里已带就直接用；engine 兜底推出口（上面那三段）时用同一份账本
            # 补算，保证「听牌雷达卡片上的数字」与建议 reason 里的数字同源。两处数字
            # 不一致比粗一点更糟：用户无从判断该信谁。
            top_ting_chance = None
            if advice and isinstance(advice[0], dict):
                top_ting_chance = advice[0].get("ting_chance")
            if top_ting_chance is None and ting_details and isinstance(self._ledger, dict):
                try:
                    from tile_ledger import ting_chance as _ledger_ting_chance
                    _w_idx: List[int] = []
                    for _td in ting_details:
                        try:
                            _w_idx.append(mpsz_to_tile34_index(str(_td.get("tile") or "")))
                        except Exception:
                            pass
                    if _w_idx:
                        top_ting_chance = _ledger_ting_chance(self._ledger, _w_idx)
                except Exception:
                    top_ting_chance = None

            # ===== 双策略路线推演（稳胡极速流 vs 大番收益流）=====
            fast_advice = None
            big_advice = None
            if advice:
                # 稳胡极速流：向听数最小、进张最多；同进张时走统一决胜链
                sorted_by_speed = sorted(advice, key=lambda a: (
                    a.get("shanten") if isinstance(a.get("shanten"), int) else 2,
                    -int(a.get("ukeire") or 0)) + _tie_tail(a))
                fast_top = sorted_by_speed[0]
                # desc 优先走账本口径：已听牌时「进张 N 张」必须拆成牌墙/对手两部分，
                # 与建议 reason、听牌雷达同源（同一个数字在两张卡片上不一样时，用户
                # 无从判断该信谁）。
                _ftc = fast_top.get("ting_chance")
                # 无叫口可拆时（未听牌）不能写「进张 N 张」这种像事实的措辞：N 来自
                # 未现牌计数，里面含了对手手上按住的牌。标成「至多」才是它的真实含义。
                _fdesc = (str(_ftc.get("text") or "")
                          if isinstance(_ftc, dict) and _ftc.get("text")
                          else f"最快叫听 · 进张至多 {fast_top.get('ukeire', 0)} 张（含对手手上）")
                fast_advice = {
                    "tile": fast_top.get("tile"),
                    "name": tile_to_chinese(fast_top.get("tile", "")),
                    "ukeire": fast_top.get("ukeire", 0),
                    "shanten": fast_top.get("shanten", 0),
                    "tag": "稳胡极速流",
                    "desc": _fdesc,
                    "defense_level": fast_top.get("defense_level", "SAFE"),
                    "defense_reason": fast_top.get("defense_reason", ""),
                }

                # 大番收益流：预期 EV / 番数最高（同 EV 同样走决胜链，不靠枚举序）
                sorted_by_ev = sorted(advice, key=lambda a: (-float(a.get("ev") or 0.0),) + _tie_tail(a))
                big_top = sorted_by_ev[0]
                if big_top.get("tile") != fast_top.get("tile"):
                    big_advice = {
                        "tile": big_top.get("tile"),
                        "name": tile_to_chinese(big_top.get("tile", "")),
                        "ukeire": big_top.get("ukeire", 0),
                        "shanten": big_top.get("shanten", 0),
                        "tag": "大番收益流",
                        "desc": f"博大牌 · 翻倍收益 ({big_top.get('reason', '')})",
                        "defense_level": big_top.get("defense_level", "SAFE"),
                        "defense_reason": big_top.get("defense_reason", ""),
                    }


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

            # 严格门控：仅在确无手牌且处于非对局等待时清空，绝不误杀真实手牌与定缺/换牌阶段合法手牌
            if status in ("waiting", "no_tiles") and (not hand_mpsz or tile_count == 0):
                hand_mpsz = ""
                tile_count = 0
                advice = []
                best = ""
                swap_advice = None
                fast_advice = None
                big_advice = None
                shanten = None
                defense_radar = []
                ting_details = []

            # 牌势感知与军师安抚 (Mood Guard)
            mood = None
            if getattr(self, "_advice_cfg", {}).get("mood_guard", True):
                mood = self._compute_mood_guard(shanten, advice, tenpai_alert, tile_count)

            # 战术知识库终极校准（融合防守雷达与最新局势牌势研判）
            if advice and hand_mpsz and tile_count > 0:
                try:
                    mood_st = mood.get("state", "steady") if mood else "steady"
                    self._last_mood_state = mood_st
                    kb_res = KnowledgeBase.evaluate_tactics(
                        hand_counts=hand_counts_final,
                        disc_counts=disc_counts_out,
                        meld_counts=meld_counts_out,
                        mode=self.mode,
                        shanten=shanten if shanten is not None else 2,
                        advice_list=advice,
                        danger_flow=top_danger_flow,
                        mood_state=mood_st,
                    )
                    self._last_doctrine = kb_res.get("doctrine", "")
                except Exception:
                    pass

            # ===== B-P4：最终顺序已定，补上「为什么是它 / 该不该换 / 摸什么会改」=====
            # 必须在知识库重排之后、`result` 组装之前：名次文案与面板显示顺序同源于
            # 这一步（见 annotate_advice_decisions 的层选择说明）。
            #
            # 先定主推再算名次：上方 `best = advice[0]` 是在知识库战术加权**之前**
            # 定的，而下发给面板的列表是加权**之后**重排的。两者分叉时，面板会把
            # 「建议打」钉在一个已被战术加权否掉的牌上（实测过：同 EV 候选里
            # 【现物防守】+25 的牌升到首位，而 best 仍指旧首位）。定缺/换牌/选牌
            # 三个阶段的 best 是阶段语义（缺门花色、换出顺序），不能跟着列表首位走。
            if (advice and not (is_dq_phase or is_swap_phase or is_pick_phase)
                    and isinstance(advice[0], dict) and advice[0].get("tile")):
                best = str(advice[0]["tile"])
            self._last_decision_stats = annotate_advice_decisions(advice, best)

            # 双策略卡片（稳胡极速流/大番收益流）与主推之间也必须用同一口径说话：
            # 它们走的是不同的主键（向听/进张 vs EV），不补这一句就是「两张卡各自
            # 报一个牌，谁说不出差别」。措辞只声明事实（排名/等价），不声明优势：
            # 大番流常常 EV 高于主推，写「主推优于它」就是假话（advantage_note 里有
            # 「列在前面不是因为有评分优势」这条分支专门处理它）。本牌名写进前缀，
            # 免得 note 里的「本牌」在别的卡片上被读成自己。
            for _alt in (fast_advice, big_advice):
                if not isinstance(_alt, dict) or not best:
                    continue
                if _alt.get("tile") == best:
                    continue
                _it = next((a for a in advice if isinstance(a, dict)
                            and a.get("tile") == _alt.get("tile")), None)
                _main = next((a for a in advice if isinstance(a, dict)
                              and a.get("tile") == best), None)
                if not isinstance(_it, dict) or not isinstance(_main, dict):
                    continue
                _n = _advantage_note(_main, _it)
                _bn = tile_to_chinese(best)
                _alt["main_compare"] = _n
                _alt["advantage_reason"] = (
                    (f"与主推「{_bn}」等价：" if _n.get("equivalent")
                     else f"主推「{_bn}」排名更前：")
                    + str(_n.get("note") or ""))

            # ===== 输出不变量：status 与手牌读数必须自洽 =====
            # 「等待牌局开始」的定义是本帧一张牌都没读到。只要 hand_mpsz 非空却报
            # waiting，端上就会出现「牌明明在屏幕上、面板却说没开局」的自相矛盾帧。
            # 在出口收一次口，悬浮窗就不必自己反推“该不该信这个 waiting”。
            # 只在「本帧真的读到了牌」时才升级：`curr_raw_n == 0` 而 hand_mpsz 还挂着
            # 旧串时不能拿它当读数，否则就是把上一帧的牌永久挂屏（反过来错）。
            if status == "waiting" and hand_mpsz and curr_raw_n > 0:
                status = "partial"
                tile_count = len(hand_mpsz) // 2
            # 构建高精度局势感知与下一步战术意图（现在在干什么，准备要干什么）
            phase_label, tactical_badge, tactical_intent = _build_tactical_perception(
                status=status,
                hand_mpsz=hand_mpsz,
                count=tile_count,
                is_drawing=is_drawing,
                drawing_tile=drawing_tile,
                shanten=shanten,
                is_swap_phase=is_swap_phase,
                is_dq_phase=is_dq_phase,
                is_pick_phase=is_pick_phase,
                swap_advice=swap_advice,
                rec_suit_name=rec_suit_name,
                advice=advice,
                best=best,
                ting_details=ting_details,
                mood=mood,
                danger_flow=top_danger_flow,
            )

            # 局况事实层：把“现在轮到谁/刚刚发生了什么/几家碰杠”折算出来。与上面那
            # 一层分工不同：战术建议在 shanten>=2 时故意不给话（面板因此整块收起），
            # 局况恒有输出——用户问的“到什么阶段了”属于后者，不该被前者的空窗遮住。
            # 只读已算好的账本（牌河分区计数/各家牌面/副露条目），不跑任何检测，
            # 单帧开销是几十次字典比较，对延迟没有可测影响。
            try:
                _rz = getattr(self, "_river_zone_counts", {}) or {}
                _od = getattr(self, "_opponent_discards", {}) or {}
                # 本家牌河（bottom）**不喂**：自家出牌已经由手牌张数转移直接得到，
                # 再喂一份牌河增量会把同一件事播两遍（而且牌河读数比手牌慢一拍）。
                _seat_river = {1: int(_rz.get("right", 0) or 0),
                               2: int(_rz.get("top", 0) or 0),
                               3: int(_rz.get("left", 0) or 0)}
                _adv0 = advice[0] if (advice and isinstance(advice[0], dict)) else {}
                _ting = list(_adv0.get("ting_tiles") or [])
                if not _ting and isinstance(ting_details, list):
                    _ting = [d.get("tile") for d in ting_details
                             if isinstance(d, dict) and d.get("tile")]
                match_phase = self._phase_machine.update({
                    "now_ms": int(time.time() * 1000),
                    "status": status,
                    "count": tile_count,
                    "drawing_tile": drawing_tile,
                    "self_discard": getattr(self, "_hand_diff_discard", None),
                    "swap_phase": bool(is_swap_phase), "dq_phase": bool(is_dq_phase),
                    "pick_phase": bool(is_pick_phase),
                    "swap_tiles": list((swap_advice or {}).get("tiles") or [])
                    if isinstance(swap_advice, dict) else [],
                    "seat_river": _seat_river,
                    "seat_river_tiles": {1: _od.get(1) or [], 2: _od.get(2) or [],
                                         3: _od.get(3) or []},
                    "seat_melds": getattr(self, "_seat_meld_entries", {}),
                    "dingque_suit": dingque_suit,
                    "dingque_name": dingque_name,
                    "dingque_recommend": rec_suit_name,
                    "shanten": shanten,
                    "ting_tiles": _ting,
                })
            except Exception as _pe:
                # 局况折算失败不能拖垮识别：给同 schema 的空视图，异常写进 diag。
                match_phase = empty_phase_view("局况折算异常，不影响识别与建议")
                self._phase_error = str(_pe)[:200]
            else:
                self._phase_error = None

            result = {
                "mode": self.mode,
                "mode_name": self._display_mode_name(),
                # 那个「房卡未收录」的玩法 key（None = 在清单里）。引擎不换玩法，
                # 但必须把“用户选了一个这家不常见的玩法”留成机读事实，供调试页/取证对齐。
                "mode_off_catalog": getattr(self, "_mode_off_catalog", None),
                # Java 侧 native 接管的路由判据（能力而非 key 前缀）：native 求解器
                # 没有鬼牌概念且会丢弃所有 z 字牌，只有它能让当前玩法算对时才允许接管。
                "native_ready": native_solver_ready(self.mode),
                "platform": self.platform,
                "platform_name": get_platform(self.platform).get("name", self.platform),
                "knowledge_doctrine": getattr(self, "_last_doctrine", ""),
                "phase_label": phase_label,
                "tactical_badge": tactical_badge,
                "tactical_intent": tactical_intent,
                "match_phase": match_phase,
                "dingque": dingque_name,
                "dingque_suit": dingque_suit,
                "dingque_phase": is_dq_phase,
                "swap_phase": is_swap_phase,
                "pick_phase": is_pick_phase,
                "pick_candidates": pick_candidates,
                "diag": {
                    "raw": sum(len(r) for r in rows),
                    "rows": row_stats,
                    "raw_hand": len(raw_labels),
                    "stab": bool(self._hand_stab.stable_mpsz),
                    "orient": self._orient,
                    "stale": diag_stale,
                    "dup_reject": diag_dup_reject,
                    "empty_frames": hand_empty_frames,
                    # 手牌通道本帧走了哪条路 + 两套格网各切了几格（-1 = 本帧没试）。
                    # chosen=full 且 strip_cells=-1 是常态（整屏读数健康，一分探条带的
                    # 钱都没花）；chosen=strip 才说明条带反超了整屏；probe_reject /
                    # probe_skipped 分别对应「试了没赢」与「本帧不许试」，报障时别把
                    # 后者当成 ROI 配歪。
                    "hand_channel": dict(getattr(self, "_hand_channel_diag", {})),
                    # 玩法/平台房卡对得上吗。缺字牌时先看这里，不要先改识别阈值。
                    "mode_supported": bool(getattr(self, "_mode_supported", True)),
                    # 未收录的是哪个玩法（None = 在清单里）。与上面那条同一条事实的
                    # 两面：「在清单里、牌集本就如此」与「不在清单里、照用户选的算」。
                    "mode_off_catalog": getattr(self, "_mode_off_catalog", None),
                    # 条带探测是否已被停用（连续试了却没反超整屏）。停用是「这个配置下
                    # 兜底不起效」的确证，不记就只会留下“识别好像变差了”的谜题。
                    "strip_disabled": bool(getattr(self, "_strip_disabled", False)),
                    # 本帧方向验证耗时（ms）。窗口均值见 diag.perf.orient。
                    "orient_ms": round(float(getattr(self, "_orient_cost_ms", 0.0)), 1),
                    # 重型 4 方向全探测是否被熔断拦下。必须可见：静默不探与
                    # 静默探不动，对使用方来说是两种完全不同的故障。
                    "orient_probe_skipped": bool(
                        getattr(self, "_orient_probe_skipped", False)),
                    "orient_reprobe_count": int(
                        getattr(self, "_orient_reprobe_count", 0)),
                    # 四区牌河归属计数（"谁打的"展示）。
                    "river_zones": dict(getattr(self, "_river_zone_counts", {})),
                    # 牌河的接线错误（None = 本轮没出错）。空表与断链必须可分：前者是
                    # 牌局事实，后者是接线问题（传错检测器时它静默了几百帧）。
                    "river_error": getattr(self, "_river_error", None),
                    # 本帧事件源交出的条目数（0 且无扫描结果 = 牌河真的没读数）
                    "river_events": int(getattr(self, "_river_event_n", 0)),
                    # 提交/认领计数：把「没提交」「没认领」「认领了但确实为空」分开。
                    "river_submits": int(getattr(self, "_river_submits", 0)),
                    "river_consumes": int(getattr(self, "_river_consumes", 0)),
                    # 分阶段耗时（ms）：decode/detect/river/advice 各自 avg/max，
                    # 悬浮窗诊断行直接可见当前瓶颈段（跳帧帧不重跑，值不变）。
                    "perf": self._perf_snapshot(),
                    # 牌局账本生成失败原因（None = 正常）。降级可以发生但必须可见：
                    # 否则“听口文案退回旧口径”与“本来就没账本”在端上看起来一模一样。
                    "ledger_error": (getattr(self.trainer, "ledger_error", None)
                                     if self.trainer is not None else None),
                    # B-P4 决策补全的接线体检：advantage/danger_hint/predraw 本该
                    # 在非空 advice 上出现，计数为 0 就说明某一环断开了（而不是“没数据”）。
                    "decisions": getattr(self, "_last_decision_stats", None),
                    # 局况折算一旦异常，必须可见：否则“没局况”与“没到局”在端上看起来一样。
                    "phase_error": getattr(self, "_phase_error", None),
                },
                "hand": hand_mpsz,
                # 本帧哪几张牌「连最像的模板都不够像」（[屏上第几张, 分数]）。
                # 单张分数才有这个分辨力，行均没有（见 `HAND_UNREADABLE_CONF`）。
                "hand_uncertain": list(getattr(self, "_hand_low_conf", [])),
                # 屏上有、但当前玩法牌集里没有的牌（已按实际牌面读）。与
                # `hand_uncertain` 不是一回事：那条说「看不清」，这条说「玩法不符」。
                "hand_gate_conflict": list(getattr(self, "_hand_gate_conflict", [])),
                # 有框却读不出的张数（面板上「手牌(N张)」比屏上少时，这个数就是差额）
                "hand_missing": int(getattr(self, "_hand_missing", 0)),
                # 手牌取区整体被压暗（弹窗遮罩）：区分「没开局」与「看不清」
                "hand_dim": bool(getattr(self, "_hand_band_dim", False)),
                # 本帧手牌是沿用上一帧的（阻尼开启）+ 沿用了多少帧。没这两个字段，
                # 「沿用旧牌」与「本帧真读到」在面板上完全同形，而用户只能从
                # 「弹窗都消失了两帧还是那 4 张」发现它。
                "hand_carried_over": bool(getattr(self, "_hand_carried_over", False)),
                "hand_stale_frames": int(getattr(self, "_hand_stale_frames", 0)),
                "count": tile_count,
                "status": status,
                "message": message,
                "dingque_recommend": rec_suit_name,
                "shanten": shanten,
                "advice": advice,
                "best": best,
                "commentary": commentary,
                "discards": disc_mpsz_out,
                "discard_count": len(discarded_labels_out),
                "remaining": remaining,
                "dead": dead,
                "remaining_matrix": remaining_matrix,
                "tile_ledger": tile_ledger_view,
                "ting_chance": top_ting_chance,
                "tenpai_alert": tenpai_alert,
                "swap_advice": swap_advice,
                "defense_radar": defense_radar,
                "defense_map": defense_map,
                "ting_details": ting_details,
                "fast_advice": fast_advice,
                "big_advice": big_advice,
                "mood": mood,
                "win_equity": round(top_win_equity, 3),
                "ev_gauge": top_ev_gauge,
                "hand_ranges": bayesian_hand_ranges,
                "danger_flow": top_danger_flow,
                "hot_tiles": hot_tiles,
                "dead_tiles": dead_tiles,
                "opponents_dingque": opponent_dingque_suits,
                "is_drawing": is_drawing,
                "drawing_tile": drawing_tile,
                "tiles": all_tiles,
                # 最近一帧的最高模板匹配分（无论是否过阈）。
                "top_score": round(float(getattr(detector, "last_top_score", 0.0)), 3),
                "glyphs": len(getattr(getattr(detector, "_glyphs", None), "nums", {}) or {}),
                "styles": len(getattr(getattr(detector, "_styles", None), "tpls", []) or []),
                "screen": [
                    int(getattr(detector, "last_screen", (0, 0))[0]),
                    int(getattr(detector, "last_screen", (0, 0))[1]),
                ],
                "elapsed": round(time.time() - start_time, 3),
                "frame_skipped": False,
            }

            # ===== D 层语义收口：同一帧不得交两套事实 =====
            # D19~D24、D27、D30 看着是十条文案问题，其实全是「互斥状态同时下发」：
            # 换牌阶段谈点炮、定缺信息跟「进听冲刺」同屏、0 进张还给「若摸到 X 将改打」、
            # 广东玩法里讲川麻断门……收在一处做比在十个渲染点各打补丁可靠：
            # 下游 UI 无论怎么渲染，拿到的就已经是自洽的。
            _dq_mode = is_dingque_mode(self.mode)
            # D23/D20 玩法没有定缺规则，就不能出现任何定缺信息。旧行为只在
            # `opponents_dingque` 上门了，本家 `dingque`/`dingque_suit` 与定缺阶段没门
            # → 广东玩法里照样冒出「下家缺万」。
            if not _dq_mode:
                result["dingque"] = None
                result["dingque_suit"] = None
                result["opponents_dingque"] = []
                result["dingque_phase"] = False
            _special = bool(result.get("swap_phase") or result.get("pick_phase")
                            or result.get("dingque_phase"))
            # D21 换牌/选牌/定缺阶段与无牌帧都不存在「摸」这个动作。
            if _special or int(result.get("count") or 0) <= 0:
                result["is_drawing"] = False
                result["drawing_tile"] = None
            # D24 换牌/选牌阶段不给「点炮高危/改打安全牌」类建议：那时手上的牌不是
            # 打出去的牌，拿防守口径讲进攻选择就是把用户往错方向推。
            if result.get("swap_phase") or result.get("pick_phase"):
                result["advice"] = []
                result["best"] = ""
            # D22 0 进张不得同时给「若摸到 X 将改打」：那张牌不会让牌型前进一步，
            # 两个文案摆在一起就是自相矛盾。
            # D22 「0 进张仍出建议、且与『若摸到 X 将改打』矛盾」——本轮尝试过在
            # ukeire==0 时剥掉改打行，但这个前提被自己的守卫直接证伪：
            # `test_realtime_blanks.test_flip_line_matches_the_simulated_group` 的夹具里，
            # ukeire=0 的条目**可以合法地带改打行**（摸进那张牌改变的是「打哪张」，
            # 不是「能不能进张」）。剥掉它会把正确信息删掉。
            # 因此 D22 本轮不交，且已记入台账：真正要查的是面板把「无进张」与
            # 「有改打盼头」用什么口径并列写出，而不是数据本身矛盾。

            # D27 非法张数必须说出来：本家手牌只能是 13n+1 或 13n+2（副露每组少 3 张）。
            # 不在集合里 = 漏读/多读/副露没读到，无论哪种都不能默默当正常手牌算。
            _n = int(result.get("count") or 0)
            result["hand_count_suspect"] = bool(
                _n > 0 and _n not in {1, 2, 4, 5, 7, 8, 10, 11, 13, 14})
            # D30 玩法选错的逐帧提醒：屏上读到了本玩法牌集里没有的牌，就是直接证据。
            _conf = list(result.get("hand_gate_conflict") or [])
            result["mode_suspect"] = ({
                "mode": self.mode,
                "tiles": _conf[:8],
                "text": (f"屏上有 {'、'.join(str(t) for t in _conf[:4])}，但当前玩法不含这些牌："
                         f"玩法可能选错（现用：{result.get('mode_name')}）"),
            }) if _conf else None
            # D26 排序只供展示，原始屏上顺序仍以 `hand` 交出，不丢信息。
            result["hand_sorted"] = _sort_hand_mpsz(result.get("hand") or "")

            # A1/A2：`hand_carried_over` 必须与“显示的手牌是不是本帧的”真一致。
            # 上一版只在阻尼分支里置 True，于是「本帧读到 0 张、稳定器宽限期把上帧
            # 的 10 张继续下发」这条路（实测：zj_popup_02 raw=0 而面板 10 张、
            # status=ok）标记仍是假 —— 用户看到“手牌冻住、其他块照旧更新”时，
            # 数据层根本没有记下这件事。现在按事实反推：只要本帧读到的张数少于
            # 下发的张数，就是沿用，不管走的哪一条路。
            _raw_n = int((result.get("diag") or {}).get("raw_hand") or 0)
            _shown_n = int(result.get("count") or 0)
            if _shown_n > _raw_n:
                result["hand_carried_over"] = True
                if not (result.get("message") or "").startswith("沿用"):
                    result["message"] = (f"沿用上一帧手牌（本帧读到 {_raw_n} 张，"
                                         f"仍展示 {_shown_n} 张）")

            # 牌河 YOLO 影子对比（默认关；只写 diag/日志，不影响任何显示与建议；
            # 必须在 json.dumps 之前，diag 结果才能随帧送到接料/日志）
            self._maybe_yolo_shadow(full_for_preview, result)

            payload = json.dumps(result)
            self._frame_skipper.remember(payload, result["top_score"])
            # 牌河真实帧采集（仅当调试页「牌河采集」开启；异常已内部吞掉）
            self._maybe_collect_river(full_for_preview, result)
            # 低置信手牌帧采集（同一开关门控：真机 bank 覆盖不足样本回收闭环）
            self._maybe_collect_lowconf_hand(full_for_preview, result)

            res = EngineResult(
                image=_make_preview(full_for_preview),
                result=payload,
                stage=None,
            )
            return res
        except Exception as e:
            traceback.print_exc()
            return _error_result("py_error", f"process 异常: {e}")
