# -*- coding: utf-8 -*-
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage, Rect

# 模板几何基准。`extract_face` 把任意裁片归一到 FACE_W x FACE_H 的画布，
# `_build_cores` 的两刀就按这个画布写；所以**任何**模板（含手绘主库、含各平台
# bank）在切片前都必须先落到同一画布上，否则切出来的核尺寸不齐，
# `matchTemplate` 的分数在不同类之间不可比（实测教训见 _build_cores 的 docstring）。
FACE_W, FACE_H = 80, 120
CORE_X0, CORE_X1 = 12, 68              # 核的横向窗口（两侧剔掉牌框与象牙边）
CORE_Y0, CORE_Y1 = 16, 104             # 普通牌的纵向窗口
CORE_BTN_Y0 = 44                       # 带顶标（癞子/春天）时的纵向上沿
CORE_W, CORE_H = CORE_X1 - CORE_X0, CORE_Y1 - CORE_Y0     # 56 x 88

# ===== 打分第四刀：粗筛 + 精算（两阶段打分）=====
# 打分单价 = 核数 × 每核单价，而 `matchTemplate` 的钱 ≈ 输出像素 × 模板像素，
# 所以想再快一档只有两条路：少扫几核，或让每次比较便宜。本刀把两者串起来：
#   ① 粗筛：牌面图与**全部候选核**一起等比缩到 PREFILTER_SCALE（像素 1/4），
#      跑一遍便宜的 NCC 只做排序；
#   ② 精算：只对上表选出的少数标签跑原来的全尺寸 NCC，最终分数表由此产生。
# 必须图与核**同比例**缩：只缩模板不缩图，输出窗口反而变大（实测 17×13 → 61×57），
# 便宜不了多少（口径来自 `localtest/probe_prefilter.py`）。
#
# 代价与收益（实测 `build/prefilter_gt.txt`：37 帧 verified GT 的**真实调用序列**
# 738 次打分，手牌行/牌河/副露/救援重扫全部包含在内，每次 110 核）：
#   全量扫 11.26ms/枚 → 两阶段 5.71ms/枚（-49.3%；粗筛单枚 4.09ms = 全量的 36.4%，
#   精算平均 4.1 个标签 / 16.2 枚核 = 1.62ms）；
#   「全量 argmax 标签落进精算集」738/738，「过完 `_decide` 的最终 (label, score)
#   与全量逐字全等」738/738。
# 为什么判据看 argmax 召回而不是只看最终一致（这条是硬道理，不是保守）：只要
# 全量 argmax 落在精算集里，它拿的就是精确全尺寸分，于是精算集内的 argmax 与
# 全量 argmax 同类同分（集外标签的全量分不可能更高，否则它就才是 argmax），再
# 加上相邻类闭包让 7 条结构判决的输入分也都精确 ⇒ **最终输出由构造保证一致**。
# 反过来「最终一致 100% 但 argmax 召回不足」是靠运气（被挤掉的类恰好被隔壁捞
# 回来），那种绿不许上线：K=2 时最终一致同样 100%，但粗筛召回只有 99.3%。
PREFILTER_SCALE = 0.5
PREFILTER_TOP_K = 3        # 粗筛前 K 名进精算集
# 与粗筛第一名分差小于这个带的标签**全部**进精算集：粗筛自己都分不清高下时，
# 就没资格替精算做淘汰。0.06 不是新调的参数——`_decide` 判「NCC 分不出 2m/3m」
# 用的就是同一个 0.06（第 1~3 条裁决），这里只是把同一把尺子用到筛选上。它在
# 本批 738 次调用里平均只多放进 0.3 个标签（见上表的 K=3/Δ=0 vs Δ=0.06 两行），
# 买的是陌生牌风/遮挡/低置信帧上「排序整体失稳」时的自我退化。
PREFILTER_MARGIN = 0.06

# `_decide` 里成对出现的形近类。精算集必须对它做**闭包**：任一方入围就把另一方
# 也拉进来，否则 `scores.get("3m", 0.0)` 会读成 0，那条结构判决整条被跳过——这不
# 是理论担心，是写这份代码时必须防住的唯一一条真实失真路径。
# 新增/删除 `_decide` 里的判决时**必须同步这张表**，守卫
# `localtest/test_prefilter_consistency_guard.py` 会从 `_decide` 源码里把成对标签
# 抽出来比对（漏改在离线就被拦下，不等真机认错牌）。
ADJACENT_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("2m", "3m"), ("2s", "3s"), ("2p", "3p"), ("4p", "5p"),
    ("6p", "8p"), ("6s", "9s"), ("4s", "5s"),
)

ADJACENCY: Dict[str, Tuple[str, ...]] = {}
for _a, _b in ADJACENT_PAIRS:
    ADJACENCY.setdefault(_a, []).append(_b)
    ADJACENCY.setdefault(_b, []).append(_a)

# 附加平台风格 bank 清单：(模块名, 风格名)。提成模块常量是为了让离线评测
# 能按名字关掉某一个 bank，做「加/不加」对照（见 localtest/calibrate_shots.py
# 的 --disable-bank）——新增平台时在这里加一行即可。
EXTRA_BANKS: Tuple[Tuple[str, str], ...] = (
    # 腾讯欢乐麻将：`build_platform_bank.py` 会把 `tiles/tencent/` 编译成
    # templates_tencent.py，但它历史上从未被登记 — 真机收割的 83 张牌风全部躺在
    # 一个无引用的模块里，腾讯帧只能拿 34 张手绘主 bank 去读（实测帧 12 的九条
    # 被读成七条、置信 0.88，而本帧就是该类的唯一素材）。不登记 = 白收割。
    ("recognition.templates_tencent", "tencent"),
    # 蜀山牌风：来自真机截图人工核对收割。
    ("recognition.templates_shushan", "shushan"),
    # 雀神四川麻将：其牌风不在任何已有 bank 里时，模板分够不到 yolo_detector
    # 覆盖层的 0.40 放行线，YOLO 的错标签会被原样放行
    # （实测帧 14 纯 YOLO 仅 2/14 命中，补 bank 后 11/14）。
    # 素材：localtest/harvest_style_tiles.py -> localtest/build_platform_bank.py。
    ("recognition.templates_queshen", "queshen"),
    # 途游四川麻将：万子用大写数字（伍萬/陸萬），YOLO 不认；筒子数圈、
    # 条子数根也失稳（帧 34 实测仅 2/12 命中）。
    ("recognition.templates_tuyou", "tuyou"),
    # JJ 麻将：条子与万子有稳定的系统性错读（4s→5s、5s→9s、2s→8s、8m→7m），
    # 五帧实测 30/64；牌风模板入 bank 后由模板抢回正确标签。
    ("recognition.templates_jj", "jj"),
    # 微乐四川麻将：其牌风完全不在任何已有 bank 里，所以模板分够不到覆盖层
    # 放行线，既读错数牌（4m↔5m、8p→2p）也让漏检补槽拿不到分而整槽丢弃。
    ("recognition.templates_weile", "weile"),
    # 指尖四川麻将（血流红中）：牌风里 8m/9s/7z(赖子) 都是高对比笔画密集团，
    # 没有本家 bank 时探针会把它自信地归到“最像的已挂 bank”——实测本批 15 帧
    # **全部**误路由（8 帧→tuyou、4 帧→shushan），所以这一块必须建库。
    # 素材：localtest/gt/shots_b1.json -> harvest_from_gt.py -> build_platform_bank.py。
    ("recognition.templates_zj", "zj"),
)

# 风格 -> 允许它的平台 key。没在这里出现的风格 = 全平台可用（历史行为）。
# queshen 必须限平台：定量泄漏实验（localtest/leak_queshen.py，630 张自带真值的
# 其它风格牌面）显示雀神模板会把腾讯牌风的 5s↔9s、6p↔8p、9p↔6p 抢走，
# 线上口径净修正 9 / 净泄漏 8 —— 全平台开着约等于白送，只对雀神开则是纯收益。
STYLE_PLATFORM_WHITELIST: Dict[str, Tuple[str, ...]] = {
    "queshen": ("gd_queshen",),
    # 途游模板与腾讯牌风同为“圈/根”计数型，最容易互抢，必须限平台。
    "tuyou": ("tuyou",),
    "jj": ("jj",),
    "weile": ("weile",),
    # 指尖牌风与途游/蜀山同为“血流”系且都被探针误路由过，限平台以免串味。
    "zj": ("zj_sichuan",),
}

# 风格 -> 明确不允许它的平台 key（只在白名单之外再做减法）。没出现在任何一张表里
# 的风格 = 全平台可用（历史行为）。
# 为什么蜀山只在「用户声明腾讯」时踢掉，而不是收进它自己的平台：
# 试过整条限平台（"shushan": ("shushan",)），代价是真的：
# `localtest/test_eval_alignment.py` 的 zj#08（LOFO 剔掉本帧 zj 模板）那一格七筒
# 本来是被**蜀山核**抢回来的，限平台后变成 6p；`test_skip_probe_guard.py` 同时变红。
# 那是「本家 bank 覆盖不够时跨家捞一把」的救援池，不该被我为了省时间剥掉。
# 而腾讯这一家不同：它是默认平台、也是真机在用的平台，蜀山核在这里是**纯花费**
# ——A/B（`localtest/ab_style_cores.py`，37 帧 verified GT，逐帧 fresh Engine）
# 裁掉 shushan 后 hand/status/dingque 逐帧差异 0，单帧均值
# 654.6ms → 447.6ms（-31.6%，原始输出 build/ab_none.txt 与
# build/ab_drop_shushan.txt）；普查脚本 `localtest/core_census.py` 量到腾讯
# 平台 + 川麻候选集每片参与 matchTemplate 的核从 186 降到 110（逐平台表见它的输出）。
# 所以减法只做在这一家，其它平台的跨家救援一律保持原样。
STYLE_PLATFORM_DENYLIST: Dict[str, Tuple[str, ...]] = {
    "shushan": ("tencent",),
}

# 手牌**重叠排布**（后一张压住前一张，节距 ≈ 0.0547*屏宽）的平台；不在表里的
# 按相邻排布（节距 ≈ 牌面宽 = 牌高/1.35）。
# 为什么这张表能省掉每帧一次全库探针（实测 `localtest/ab_skip_probe.py`，
# 91 帧 LOFO 夹具，build/ab_skip_probe.txt）：
#   探针在**用户已声明平台**时只做两件好事之一 —— 猜“这帧像哪家的牌风”。
#   实测 91 帧里它每次都把本家排在第一（jj→jj 14/14、queshen→queshen 15/15、
#   tencent→tencent 16/16…只有 2 帧路由到隔壁家，而那两家同为相邻排布，
#   行为完全一致），也就是说**它猜的东西用户已经告诉我们了**；而代价是每帧
#   594 张模板 × 2~3 枚代表牌 = 1273 次 matchTemplate（214~284ms/帧，
#   占总耗时 21~28%，见 build/cost_detail.txt）。
#   等价性 A/B：91 帧标签与框序列**逐帧相同（差异 0）**，总耗时省 17~20%。
#   唯一差别是 queshen#05 一枚 7z 的置信度 0.796→0.815（标签不变）：候选集从
#   「探针收窄的那家」放回「平台白名单」，同类的别家变体核分数更高 —— 这正是
#   `localtest/test_style_probe.py` 第 6 条守卫要求的“声明是硬事实，探针只是提示”，
#   跳探针等于把这条贯彻到底。
#   「逐帧相同」的适用范围（后来被样本外实测纠正过一次）：那份 A/B 用的是**全库**
#   夹具，没做 LOFO 剔模板。LOFO 下会差一格：蜀山帧 01 第 12 枚，旧行为候选被探针
#   收窄成 `shushan` 一家（本帧贡献的 8 张模板刚被剔走，5p 在本家 bank 里没了模板，
#   被形近的 4p 抢走），现行给平台白名单 `shushan+tencent`，主库模板把它救回来
#   （1080 -> 1081/1087，见 build/lofo_channel_b1.txt 与 …_probe.txt 两份对照）。
#   方向是**变好**，所以这里不是拿精度换时间；但它证明「零差异」不能无条件讲，
#   那一格已由 `localtest/test_skip_probe_guard.py` 钉成棘轮。
# 排布方式是平台的物理属性，不是牌风属性：所以查表比猜更准，而且换分辨率不影响。
PLATFORM_OVERLAP_LAYOUT = frozenset({"tencent"})

# 牌墙里真的含完整字牌（东南西北發）的平台。川麻（血流/血战）只用 108 张 +
# 癞子，风牌根本不存在，把 classify_tile 的候选集放开到 34 类会让模板把
# 8p/9p 误配成 東/白——所以放开必须按平台，不能全局改。
# 依据一律以「该平台自己人工钉过的帧里真读出过 1z~6z」为准：
#   weile      帧 04 手牌尾两枚 = 南/北（旧记录写的“帧 06/07”是换批次前的编号，已失效）
#   gd_queshen 帧 09/12 手牌里 東(1z)/西(3z)/發(6z) 共 8 枚
#   tencent    帧 12 一次给齐 東/西/北/中/發/白（本批唯一带字牌的腾讯帧）
# 没进这张表的平台（蜀山/途游/JJ/指尖）在已钉帧里只出现过 7z（红中癞子），
# 没有证据就不放开：给没有字牌的平台放开，代价是把 8p/9p 读成 東/白。
# 实测不放开的代价：雀神的 東→7z、西→1p、發→1s 整族失误，根因就在这里——
# 3z/1z/6z 的模板明明在 bank 里，却连被比较的机会都没有（候选集只有 7z）。
PLATFORM_FULL_HONORS = {"weile", "gd_queshen", "tencent"}

# 手牌逐枚打分的并行度。为什么并行而不是别的：实测每帧 691ms 的手牌通道里，
# 打分是「25 枚互不相关的 NCC 扫描」，而 `build/thread_memo.txt` 量到
#   串行 5563ms/74 枚 -> 2 线程 1.91x、4 线程 5.28x、8 线程 6.90x，
#   且三种并行度下**每枚结果与串行逐枚全等**（并行单位=单帧内的牌，同帧同平台，
#   所以 `active_styles`/`_mode_tiles` 这些共享可变态在提交前就设好、帧内只读）。
# 同一次实测还判掉了另一条看起来更"安全"的路：按牌面像素做精确记忆化只在
# 逐像素静止时命中（1px 位移/亮度±2/JPEG 重压全部失配），对循环回放的压测夹具
# 更是刷分工具，所以不做。
# 上限压到 4：`yolo_detector` 已经把 `cv2.setNumThreads` 设到 min(4, 核数)，
# 再开更多 worker 会和 OpenCV 的内部线程抢同一批核（大.LITTLE 上会倒挂）。
# 手机上的实际加速必须在设备上复测，不能拿 PC 的 5.28x 当承诺。
PARALLEL_WORKERS = 4
PARALLEL_MIN_TILES = 6        # 少于这个张数，线程调度开销大于收益，直接串行


def banked_platforms() -> Set[str]:
    """已挂「本家专属」手牌模板 bank 的平台 key（引擎据此决定手牌能不能走 NCC 通道）。

    限平台的风格按白名单认领（queshen/tuyou/jj/weile/zj）；不限平台的风格（蜀山）把与
    风格同名的平台视为本家。主 bank 固定是 tencent。
    不在这里的平台（只剩 generic）没有可比字模，模板只会拿别家牌风认错，
    手牌应继续走主检测器——所以接入新平台收割完 bank 后这条清单会自动变长。
    """
    out: Set[str] = {"tencent"}
    for _mod, style in EXTRA_BANKS:
        owners = STYLE_PLATFORM_WHITELIST.get(style)
        if owners:
            out |= set(owners)
        else:
            out.add(style)
    return out


# 数牌 27 类是所有玩法共用的底集。
_BASE_TILES: Set[str] = ({f"{i}m" for i in range(1, 10)}
                         | {f"{i}p" for i in range(1, 10)}
                         | {f"{i}s" for i in range(1, 10)})


def resolve_candidate_tiles(avail=None, mode_tiles=None, full_honors: bool = False) -> Set[str]:
    """解析本次分类允许出现哪些牌（mpsz 标签集）。

    优先级：显式 avail 参数 > 当前玩法牌集（set_mode_tiles 注入）> 按平台兜底。
    最后那层兜底原本是**唯一**依据（PLATFORM_FULL_HONORS），但 YOLO 覆盖层、牌桌
    探针、任选牌弹窗这些调用点不传 avail，于是全牌玩法（推倒胡/白板百搭/中发白
    三鬼）在这些路径上始终读不到东/南/西/北/白/发：牌集明明由玩法决定，却按平台
    猜。改成玩法优先后，川麻类玩法依旧只放开 7z（候选集与旧行为一致，不会把
    8p/9p 误配成 東/白），全牌玩法则拿到完整 34 类。
    """
    cand: Optional[Set[str]] = None
    if avail is not None:
        if avail and isinstance(next(iter(avail)), int):
            from trainer.utils.convert import tiles34_index_to_mpsz
            cand = {tiles34_index_to_mpsz(i) for i in avail}
        else:
            cand = set(avail)
    elif mode_tiles:
        cand = set(mode_tiles)
    else:
        cand = _BASE_TILES | ({f"{i}z" for i in range(1, 8)} if full_honors else {"7z"})
    # 空候选集是定时炸弹：下面的扫描循环会一个都不试，scores 为空后
    # next(iter(valid_tiles)) 直接 StopIteration，整帧识别挂掉。
    return cand or (_BASE_TILES | {"7z"})


def _prefilter_core(entry):
    """把一枚全尺寸核等比缩到粗筛尺寸，字段顺序与 `self._cores` 逐字一致。

    用 INTER_AREA 而不是默认的双线性：它在缩小方向上做面积平均，与
    `extract_face`/`canonical_canvas` 归一模板时同一个插子 —— 两处缩放口径不同，
    粗筛分数与精算分数就不再是同一张牌面的两个尺度。"""
    out = [entry[0], entry[1]]
    for k in entry[2:]:
        if k is None:
            out.append(None)
            continue
        kh, kw = k.shape[:2]
        out.append(cv2.resize(k, (max(1, int(kw * PREFILTER_SCALE)),
                                  max(1, int(kh * PREFILTER_SCALE))),
                              interpolation=cv2.INTER_AREA))
    return tuple(out)


class TencentGridDetector(Detector):
    # 类属性兜底：没调用过 set_platform_styles 时（比如牌河探针、离线单测直接
    # new 实例）语义等于“不放开字牌”，与历史行为一致。
    full_honors = False
    # 当前玩法的牌集（mpsz 标签集）；None 表示尚未推入，退回按平台兜底。
    _mode_tiles: Optional[Set[str]] = None
    # 用户声明的平台（set_platform_styles 记录）；None = 没声明，探针才有权路由。
    _declared_platform: Optional[str] = None
    # 调试/对照口子：True = 即使声明了平台也照旧扫全库探针。
    # 留着不是为了生产，是为了让守卫能对拍“跳探针到底有没有改变输出”
    # （`localtest/test_skip_probe_guard.py`），以及真机怀疑排布表错时一键退回旧行为。
    probe_when_declared: bool = False
    # 调试/对照口子：True = 声明平台后仍给「张数候选」每档都切一整行（旧行为）。
    # 与 `probe_when_declared` 同一用途：守卫靠它对拍「序贯早停有没有改变输出」
    # （`localtest/test_hand_count_seq_guard.py`，41 帧逐格对拍），真机怀疑节距估错
    # 时一键退回。默认 False = 生产走早停，这条默认值由该守卫钉住。
    hand_count_dual: bool = False

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
        # 与 tpl_entries 严格同序的**原始 bank 键名**（保留 `#变体` 后缀）。
        # tpl_entries 里的标签被 split("#") 剥过，所以“这张模板是哪一帧割的”
        # 只剩这里能回答：LOFO 评测（localtest/eval_new_material.py --lofo）必须按
        # **模板实例**剔除，按标签剔会把同名的其它帧变体一起误杀（实测把素材缺口
        # 从 19 张夸大成 92 张，导致真正的分类失误被归到“不怪算法”那一档）。
        # 生产路径不读它，只有评测读。
        self.tpl_keys: List[str] = []
        # (lbl, style, btn_core, plain_core, btn_gray, plain_gray)
        self._cores: List[Tuple[str, str, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
        # 与 _cores 严格同序的原始键名（= tpl_keys），供评测按实例剔除。
        self._core_keys: List[str] = []
        # 与 _cores 严格同序：这张模板是不是从 GT 帧收割来的（即 provenance 能不能
        # 回答“它属于哪一帧”）。手绘主库固定 False。**LOFO 剔帧必须同时校这一位**：
        # 主库键名与 bank 键名在同一空间里（实测 34 张手绘腾讯模板里有 33 张的键
        # 名与 b1 收割 bank 的键名撞车），只按键名剔会把本帧之外的手绘知识一起
        # 抽掉——那会把一张本可以判对的牌报成“bank 缺类”而豁免掉，等于自己给自己
        # 发免检牌。生产路径不读它，只有评测与守卫读。
        self._core_harvested: List[bool] = []
        self.last_top_score: float = 0.0
        self.last_screen: Tuple[int, int] = (0, 0)
        self.last_drawn_tile: Optional[str] = None
        # 本次 `detect_hand_strip` 用的手牌局格：(左边界 x, 节距 tw, 格数 k)。
        # 牌带是等宽切出来的（`_try_counts` 里 `x1 = bx + i * tw`），而置信救援会
        # 把低分张**整框丢掉**，所以返回的框数可以少于 k：拿“第几框”当“第几格”会把
        # 后面的牌集体左移一格（实测 zj#08 漏 4 框后，第 10 槽的七筒被当成第 6 槽的
        # 一筒判成失误）。只有离线评测与守卫读它，生产路径不依赖。
        self.last_hand_grid: Optional[Tuple[float, float, int]] = None
        # 当前平台允许扫描的风格集合；None = 不裁剪（未 set_platform 时的历史行为）。
        self.active_styles: Optional[Set[str]] = None
        # 最近一次 `_probe_style` 的风格排名表 [(style, 平均分) 降序]。分类候选集
        # 要靠它留第二名（见 `_probe_runner_up`），所以**每次调用都必须刷新**，
        # 包括提前返回 None 的路径 —— 否则上一帧的排名会决定本帧用哪些字模。
        self.last_probe_ranking: List[Tuple[str, float]] = []
        # 本次 `detect_hand_strip` 走的节距模型分支名 + 两套张数候选，只为守卫/诊断
        # 可读，生产不依赖。为什么非得留这个observable：实测把
        # `PLATFORM_OVERLAP_LAYOUT` 在 91 帧上逐帧翻面，输出改变 **0/91** —— 两种节距
        # 模型的张数候选常常同形，且 `_try_counts` 还会在候选内取均分最优，所以帧级
        # 差异**测不出这张表接没接线**（见 build/layout_table_sensitivity.txt）。
        # 只断言输出的守卫会在这一点上自欺欺人。
        self.last_layout_branch: Optional[str] = None
        self.last_pitch_candidates: Optional[Tuple[List[int], List[int]]] = None
        # 手牌逐枚打分是否走线程池（理由与实测加速比见 `PARALLEL_WORKERS`）。
        # 关掉它就退回逐枚串行——同一条打分码路，结果必须逐枚全等，
        # 所以它是纯粹的调试/对照开关，不是行为开关（守卫靠它做 A/B）。
        self.parallel_classify = True
        # 逐枚打分是否走「粗筛+精算」两阶段（收益/风险与本开关的存在理由见
        # `PREFILTER_SCALE` 上方那段）。关掉它就退回全量扫——同一条打分码路，
        # 最终 (label, score) 必须逐枚全等，所以它和 `parallel_classify` 一样是
        # 纯粹的对照/逃生开关，不是行为开关（新守卫靠它做 A/B）。
        self.prefilter = True
        # 半尺寸粗筛核轨：(_src, _coarse)。_src 是当时那份 `self._cores` 的**对象引用
        # 快照**，用来判断这一轨还对不对得上当前的全尺寸核（见 `_coarse_track`）。
        self._coarse_pair: Optional[Tuple[List, List]] = None
        self._pool: Optional[ThreadPoolExecutor] = None
        self._pool_lock = threading.Lock()
        self._load_templates()

    def _classify_batch(self, crops: List[np.ndarray],
                        styles: Optional[Set[str]]) -> List[Tuple[str, float]]:
        """一组互不相关的牌面 -> [(label, score)]，**严格保序**。

        保序是硬要求：调用方按格位把结果装回 rect 列表，一旦改成
        `as_completed` 之类完成序，牌就会整体错位到别的格上（分数照样很高，
        帧级指标却全乱），这种 bug 不会体现在任何均值上，只能由守卫钉住。

        并行体内**不得**触碰任何共享可变态：`classify_tile` 只读
        `_cores`/`active_styles`/`_mode_tiles`/`full_honors`，`_decide` 实测
        没有任何 `self.x =` 写入。所以调用方必须在本帧提交前就把平台/玩法
        设好，帧内只读——这也是并行单位取「单帧内的牌」而不是「跨帧」的原因。
        """
        if not crops:
            return []
        if not (self.parallel_classify and len(crops) >= PARALLEL_MIN_TILES):
            return [self.classify_tile(c, styles=styles) for c in crops]
        pool = self._pool
        if pool is None:
            with self._pool_lock:
                pool = self._pool
                if pool is None:
                    workers = min(PARALLEL_WORKERS, max(1, os.cpu_count() or 1))
                    pool = ThreadPoolExecutor(max_workers=workers)
                    self._pool = pool
        # pool.map 保序；异常照原样抛出，不静默吞掉（吞掉会让整行退化成
        # 上一帧的结果，比报错更难查）。
        return list(pool.map(lambda c: self.classify_tile(c, styles=styles), crops))

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

        # 4. 附加平台 bank：键名 '#' 后为变体后缀，按基础标签聚合。
        #    主库加载失败也不影响附加库——两者独立来源。
        for mod_name, style in EXTRA_BANKS:
            try:
                mod = __import__(mod_name, fromlist=["TEMPLATES_BGR"])
                added = 0
                for key, bgr in mod.TEMPLATES_BGR.items():
                    self.tpl_entries.append((key.split("#")[0], style, np.asarray(bgr, dtype=np.uint8)))
                    self.tpl_keys.append(key)
                    added += 1
                print(f"[{type(self).__name__}] extra bank {mod_name}: +{added} templates")
            except Exception as e:
                print(f"[{type(self).__name__}] extra bank {mod_name} unavailable: {e}")

        self._build_cores()

    @staticmethod
    def canonical_canvas(tmpl: np.ndarray) -> np.ndarray:
        """把模板落到 FACE_W x FACE_H 画布，已是该尺寸的原件直接返回。

        任何在模板上直接下刀的调用点都该先过这里：numpy 的越界切片不报错而是
        **静默少切**，切出小一号的核以后 `matchTemplate` 的分数在不同类之间就不
        可比了（小核滑窗多、容易虚高，等于尺寸不齐的模板在抢判）。理由与实测数
        字见 `_build_cores` 的 docstring。
        """
        tmpl = np.asarray(tmpl, dtype=np.uint8)
        if tmpl.shape[0] == FACE_H and tmpl.shape[1] == FACE_W:
            return tmpl
        return cv2.resize(tmpl, (FACE_W, FACE_H), interpolation=cv2.INTER_AREA)

    def _build_cores(self):
        """预切片/预归一化匹配核，避免每帧每张牌重复 cvtColor+normalize。

        核区域与旧实现一致：普通 y16:104、带黄色顶标 y44:104、x12:68。
        每个核带风格标签，运行时可按探针胜出的风格只算该 bank，把多平台
        开销压回单平台水平。

        切片前必须把模板归一到 80x120：那两刀是按 120 高的画布写的，numpy 对
        越界的切片**静默少切**而不是报错。实测手绘主库 34 张里 6 张字牌是 80x56 /
        77x56，切出来的核只有 64x44 / 61x44，与其余 88x56 的核在 `matchTemplate`
        下根本不可比——同一帧的六枚字牌全在 0.33~0.48 且正确类进不了前 5，而唯一
        尺寸合规的 7z 拿 0.88（见 build/main_bank_sizes.txt、build/audit_tc12.txt）。
        归一用 INTER_AREA，与 `extract_face` 同源；A/B/C 三版实测 8/13 -> 11/13 ->
        10/13，选本方案（见 build/main_bank_geom.txt）。
        """
        base = [(k.split("#")[0], "tencent", v) for k, v in self.templates_bgr.items()]
        self.tpl_entries = base + self.tpl_entries
        self.tpl_keys = list(self.templates_bgr) + self.tpl_keys
        self._cores = []
        # _core_keys 必须在重建 _cores 前同步清空：否则两轨长度不一致，
        # 评测剔的是“错位的另一个模板”，比不剔更坑。
        self._core_keys = list(self.tpl_keys)
        self._core_harvested = [False] * len(self.templates_bgr) + \
            [True] * (len(self.tpl_keys) - len(self.templates_bgr))
        off_spec = []
        for (lbl, style, tmpl), key in zip(self.tpl_entries, self._core_keys):
            if tmpl.shape[0] != FACE_H or tmpl.shape[1] != FACE_W:
                off_spec.append(key)
                tmpl = self.canonical_canvas(tmpl)
            plain = tmpl[CORE_Y0:CORE_Y1, CORE_X0:CORE_X1]
            btn = tmpl[CORE_BTN_Y0:CORE_Y1, CORE_X0:CORE_X1]
            plain_g = cv2.normalize(cv2.cvtColor(plain, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
            btn_g = cv2.normalize(cv2.cvtColor(btn, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
            self._cores.append((lbl, style, btn, plain, btn_g, plain_g))
        if off_spec:
            # 不静默：尺寸不齐本身是素材债，归一只是让它不再毁掉打分可比性。
            print(f"[{type(self).__name__}] {len(off_spec)} 张模板非 {FACE_W}x{FACE_H}，"
                  f"已按 extract_face 同法归一：{off_spec}")
        if not (len(self._cores) == len(self._core_keys) == len(self._core_harvested)):
            raise RuntimeError(f"模板轨错位：_cores={len(self._cores)} vs "
                               f"_core_keys={len(self._core_keys)} vs "
                               f"_core_harvested={len(self._core_harvested)}"
                               "（LOFO 剔除会按错模板）")
        for (lbl, style, _btn, plain, _bg, _pg) in self._cores:
            if plain.shape[:2] != (CORE_H, CORE_W):
                raise RuntimeError(f"核尺寸错位：{style}/{lbl} 的 plain 核是 "
                                   f"{plain.shape[1]}x{plain.shape[0]}，应为 "
                                   f"{CORE_W}x{CORE_H}（matchTemplate 打分不可比）")

    @property
    def templates(self) -> Dict[str, np.ndarray]:
        return self.templates_gray

    @property
    def is_available(self) -> bool:
        return len(self.templates_bgr) >= 27

    @staticmethod
    def _ivory_body(img: np.ndarray) -> np.ndarray:
        """把裁片收到「牌体本身」的外接框：S<90 & V>120 的最大连通块。

        为什么必须是**最大连通块**而不是全体白像素的外接框：裁片稍宽就会漏进相邻
        牌的一条白边，总外接框直接横跨两张牌（途游实测同帧三张四万两两滑窗 NCC
        只有 0.08~0.29）。

        为什么 `extract_face` 不能只用下面那条「非绿外接框」：蓝色选中光、灰色
        投影、牌顶高光都不是绿地，都会把框撑大，归一到 120x80 后牌面整体缩小并
        偏移。实测途游帧 06 的三萬与本家其他 8 帧的三萬互匹只有 0.34，而同帧另
        一枚一萬互匹 0.996 —— 差的不是牌，是配准。LOFO 一剔本帧模板，正确类连
        0.5 都到不了，就被别类抢走（见 build/align_ab.txt、build/ab_align.txt）。

        这也是 bank 侧 `localtest/style_harvest.face_align` 的现行判据，那边已改成
        直接调本函数：bank 模板与推理面必须同源，两边各写一份迟早漂移，而漂移的
        表现是「分数莫名偏低」，最难查。
        """
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        m = ((hsv[:, :, 1] < 90) & (hsv[:, :, 2] > 120)).astype(np.uint8)
        n, _lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n <= 1:
            return img                      # 掩码失败（压暗帧）：退化为整块
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x, y, w, h = [int(v) for v in stats[i, :4]]
        if h < 20 or w < 12 or h < img.shape[0] * 0.5:
            return img
        return img[y:y + h, x:x + w]

    @staticmethod
    def extract_face(img: np.ndarray, target_size: Tuple[int, int] = (FACE_W, FACE_H)) -> np.ndarray:
        # 先按牌体配准，再走原有的非绿外接框：牌体框内基本没有绿地，这一步
        # 等价于把「外接框被非牌物撑大」这个失效模式关掉。掩码失败时
        # `_ivory_body` 原样返回，行为退回旧实现，不会比原来更差。
        img = TencentGridDetector._ivory_body(img)
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

    def set_platform_styles(self, platform_key: Optional[str]) -> None:
        """按平台裁剪可用模板 bank，避免专属牌风去抢其它平台的牌。"""
        # 声明本身要留档：手牌排布查表（见 `PLATFORM_OVERLAP_LAYOUT`）只认这个事实，
        # 不能反过来用 `active_styles` 推断——未建 bank 的平台白名单里也全是别家。
        self._declared_platform = platform_key or None
        if not platform_key:
            self.active_styles = None
            self.full_honors = False
            return
        allowed = {"tencent"}  # 主 bank（templates_data）的风格名在 _build_cores 里固定
        for _mod, style in EXTRA_BANKS:
            owners = STYLE_PLATFORM_WHITELIST.get(style)
            if owners is None or platform_key in owners:
                allowed.add(style)
        # 定点减法（见 `STYLE_PLATFORM_DENYLIST`）：只踢「用户已经声明了这一家、
        # 却还在扫别家牌风」的那一组，其它平台的跨家救援原样保留。
        # 保底风格「tencent」永不被踢：它是主 bank，踢空会让 scores 为空、
        # classify_tile 退化成「报任意标签 0 分」，整行判废。
        for style, denied in STYLE_PLATFORM_DENYLIST.items():
            if platform_key in denied and style != "tencent":
                allowed.discard(style)
        self.active_styles = allowed
        self.full_honors = platform_key in PLATFORM_FULL_HONORS

    def set_mode_tiles(self, avail) -> None:
        """把当前玩法的可用牌集（34 型索引或 mpsz 标签）推给分类器。

        手牌行/副露区的分类调用本来就带 avail 参数，但 YOLO 覆盖层、牌桌探针、
        任选牌弹窗这些入口不带，之前只能按平台猜字牌是否可能存在（见
        resolve_candidate_tiles）。玩法一切换就必须重推，否则会出现“选了全牌
        玩法、识别候选集还是上一个玩法的”这种跨层不同步。
        """
        self._mode_tiles = resolve_candidate_tiles(avail=avail) if avail else None

    def _resolve_styles(self, styles: Optional[Set[str]]) -> Optional[Set[str]]:
        """把「平台白名单」与「风格探针」两个约束求交后交给扫描循环。

        探针是独立扫全 bank 得到的，可能胜出一个本平台不被允许的风格；
        这时退回白名单，绝不允许收窄成空集——空集会让 scores 为空，
        classify_tile 直接返回任意标签 0 分，等于整行判废。

        探针同样**无权把用户声明平台的专属 bank 排除掉**：声明是硬事实，探针
        只是提示。实测微乐帧 13 探针误路由 tencent 后 inter={tencent}，于是
        “用户已经说了是微乐、系统却只拿腾讯字模认微乐的牌”，6m/7m 双双被读成
        3m（0.59/0.66，均分 0.62 刚好躲过 0.55 的全量重扫门）。

        探针一致时也不是只扫一家：候选集是探针前两名（见 `_probe_runner_up`），
        实测这样能把陌生平台从 92.1% 提到 95.4%。但它仍然只是**全库的一个子集**，
        省开销的路由逻辑没丢。
        """
        allowed = self.active_styles
        if allowed is None:
            return styles
        if styles is None:
            return allowed
        inter = set(styles) & allowed
        if not inter:
            return allowed
        own = allowed - {"tencent"}   # 声明平台的专属 bank（未建 bank 的平台为空）
        return inter if own <= set(styles) else allowed

    # ------------------------------------------------------------------ 两阶段打分
    @staticmethod
    def _core_of(entry, is_grey: bool, has_btn: bool) -> Optional[np.ndarray]:
        """按「灰/彩 × 有/无顶标」四种组合取本次要用的那一枚核。

        单独成一个函数是为了让粗筛与精算**走同一个取法**：两边取的组合不一样，
        粗筛排序就与最终分数无关，它的淘汰理由全是编的。"""
        _lbl, _style, core_btn, core_plain, gcore_btn, gcore_plain = entry
        if is_grey:
            return gcore_btn if has_btn else gcore_plain
        return core_btn if has_btn else core_plain

    def _coarse_tracks(self) -> Tuple[List, List]:
        """与全尺寸核**逐项同序**的半尺寸粗筛核轨；核一变就重建。

        为什么不能在 `_build_cores` 里算完就当它永远不变（本刀最阴的一个坑）：离线
        评测会在运行时就地替换 `det._cores`（LOFO 剔模板：`test_eval_alignment.py`、
        `eval_new_material.py`、`test_skip_probe_guard.py`、`ab_style_cores.py` 等 6
        处），粗筛轨若只在建核时算一次，就会与新的全尺寸轨**逐项错位** —— 拿 A 类
        的粗筛分数去精算 B 类，比不缩还错，而且错得很体面（分数照样很高）。
        校验只用逐元素 `is` 比对象身份，不比内容：numpy 数组的 `==` 返回数组，
        `bool()` 会直接抛 ValueError。
        返回的是快照本身而不是 `self._cores`：并行打分（`_classify_batch`）下即使
        另一线程重建了这一轨，本次调用拿到的那一对也自洽（粗筛核与全尺寸核同序）。
        """
        pair = self._coarse_pair
        if pair is not None:
            src, coarse = pair
            if len(src) == len(self._cores) and all(
                    a is b for a, b in zip(src, self._cores)):
                return src, coarse
        coarse = [_prefilter_core(c) for c in self._cores]
        pair = (list(self._cores), coarse)
        self._coarse_pair = pair
        return pair

    def _scan_full(self, img: np.ndarray, entries: List, is_grey: bool,
                   has_btn: bool) -> Dict[str, float]:
        """全尺寸 NCC：label -> 该标签全部变体/风格里的最高分（与历史逐字一致）。"""
        scores: Dict[str, float] = {}
        for e in entries:
            src = self._core_of(e, is_grey, has_btn)
            s = float(cv2.matchTemplate(img, src, cv2.TM_CCOEFF_NORMED).max())
            if s > scores.get(e[0], 0.0):
                scores[e[0]] = s
        return scores

    def _scan_coarse(self, c_img: np.ndarray, coarse_entries: List, is_grey: bool,
                     has_btn: bool) -> Tuple[Dict[str, float], Set[str]]:
        """粗筛：便宜的缩小版 NCC，只服务排序。

        返回 (label->粗分, 必须强行进精算集的标签)。强进的那批是**缩过之后放不下
        打分窗口**的核：它们没被排序过，就不能当「落后」而被淘汰（否则就是「粗筛
        漏看」撑成「精算拿不到分」，分数会凭空消失而不是变低，那种错最难查）。
        """
        scores: Dict[str, float] = {}
        forced: Set[str] = set()
        for e in coarse_entries:
            k = self._core_of(e, is_grey, has_btn)
            if k is None or k.shape[0] > c_img.shape[0] or k.shape[1] > c_img.shape[1]:
                forced.add(e[0])
                continue
            s = float(cv2.matchTemplate(c_img, k, cv2.TM_CCOEFF_NORMED).max())
            if s > scores.get(e[0], 0.0):
                scores[e[0]] = s
        return scores, forced

    @staticmethod
    def _refine_labels(coarse: Dict[str, float], forced: Set[str],
                       top_k: int = PREFILTER_TOP_K,
                       margin: float = PREFILTER_MARGIN) -> Set[str]:
        """精算集标签 = 粗筛前 top_k ∪ 与第一名分差 < margin 的带 ∪ 它们的相邻类 ∪ forced。

        相邻类闭包不是可选项：`_decide` 会在分数表上读对家的分做结构裁决，把对家
        淘汰掉等于让那条判决整条失效（`scores.get("3m", 0.0)` 读成 0），而那恰恰是
        本刀唯一会造成**错判**而不是**慢**的地方。判据与实测见 `PREFILTER_SCALE` 上方。
        """
        ranked = sorted(coarse.items(), key=lambda kv: -kv[1])
        seeds = {lbl for lbl, _ in ranked[:top_k]}
        if ranked:
            lead = ranked[0][1]
            seeds |= {lbl for lbl, s in coarse.items() if s > lead - margin}
        out = set(seeds) | set(forced)
        for lbl in seeds:
            out.update(ADJACENCY.get(lbl, ()))
        return out

    def _score_crop(self, crop: np.ndarray, avail=None, styles=None):
        """从原始检测框到分数表：先抠牌面，再走 `_score_face`。

        拆成两层是为了让离线诊断能**单独换掉对齐方式**（bank 是用象牙白连通块
        对齐的样本建的，推理用的是去绿底外接框；两者不一致时分数会塌，必须能
        把这一项单独量出来）。推理路径本身的行为不变。
        """
        return self._score_face(self.extract_face(crop), avail, styles)

    def _score_face(self, face: np.ndarray, avail=None, styles=None):
        """已对好的牌面 → (face, label->最高分, 候选集)。核窗口与变体取法与历史一致。

        走不走「粗筛+精算」由 `self.prefilter` 决定，两条路必须给出**同一份最终裁决**
        （判据、实测与为什么只能这么判，见 `PREFILTER_SCALE` 上方的那一整段；逐枚对拍
        由 `localtest/test_prefilter_consistency_guard.py` 钉住）。
        """
        styles = self._resolve_styles(styles)
        hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
        is_grey = (np.mean(hsv[:, :, 1]) < 35)

        is_yellow_btn = (hsv[:40, :, 0] >= 15) & (hsv[:40, :, 0] <= 35) & (hsv[:40, :, 1] > 100)
        has_btn = (np.sum(is_yellow_btn) > 80)
        y_start = 38 if has_btn else 10

        c_face = face[y_start:110, 6:74]

        valid_tiles = resolve_candidate_tiles(avail, self._mode_tiles, self.full_honors)

        if is_grey:
            img = cv2.normalize(cv2.cvtColor(c_face, cv2.COLOR_BGR2GRAY), None, 0, 255, cv2.NORM_MINMAX)
        else:
            img = c_face

        # 候选集 + 风格过滤：全尺寸轨与粗筛轨同序筛出来，两道才能用同一份候选集说话。
        if self.prefilter:
            src, coarse_src = self._coarse_tracks()
        else:
            src, coarse_src = self._cores, None
        entries: List = []
        coarse_entries: List = []
        for i, e in enumerate(src):
            if e[0] not in valid_tiles or (styles is not None and e[1] not in styles):
                continue
            entries.append(e)
            if coarse_src is not None:
                coarse_entries.append(coarse_src[i])

        # 退回全量扫的三种情况：开关关了（A/B 对照与逃生口）、候选标签本来就不到
        # K 个（精算集不可能更小，粗筛只是白付一次排序）、以及粗筛一枚都没排上。
        # 前两种必须与不走两阶段时**逐字同路**，才对得起“对照开关”四个字。
        if coarse_src is None or len({e[0] for e in entries}) <= PREFILTER_TOP_K:
            scores = self._scan_full(img, entries, is_grey, has_btn)
        else:
            ih, iw = img.shape[:2]
            c_img = cv2.resize(img, (max(1, int(iw * PREFILTER_SCALE)),
                                     max(1, int(ih * PREFILTER_SCALE))),
                               interpolation=cv2.INTER_AREA)
            coarse, forced = self._scan_coarse(c_img, coarse_entries, is_grey, has_btn)
            if not coarse:
                scores = self._scan_full(img, entries, is_grey, has_btn)
            else:
                keep = self._refine_labels(coarse, forced)
                scores = self._scan_full(
                    img, [e for e in entries if e[0] in keep], is_grey, has_btn)
        return face, scores, valid_tiles

    def classify_tile(self, crop: np.ndarray, avail=None, styles=None) -> Tuple[str, float]:
        face, scores, valid_tiles = self._score_crop(crop, avail, styles)
        if not scores:
            return next(iter(valid_tiles)) if valid_tiles else "7z", 0.0
        return self._decide(face, scores)

    def classify_tile_debug(self, crop: np.ndarray, avail=None, styles=None) -> Dict:
        """与 `classify_tile` 同一条码路，额外给出逐标签排名与前置判据。

        诊断脚本必须走这里而不是自己再写一遍打分：打分窗口 `face[10:110, 6:74]`、
        灰度/顶标分支、候选集裁剪三者都会显著改变分数（拿整张 face 去匹核实测普遍
        虚高 0.1~0.4），虚高的归因会把“素材不够”说成“模板混类”。
        """
        face, scores, valid_tiles = self._score_crop(crop, avail, styles)
        lbl, sc = self._decide(face, scores) if scores else ("<无候选>", 0.0)
        return {
            "label": lbl, "score": sc,
            "ranking": sorted(scores.items(), key=lambda kv: -kv[1]),
            "n_candidates": len(scores),
            "n_valid_tiles": len(valid_tiles),
        }

    def _decide(self, face: np.ndarray, scores: Dict[str, float]) -> Tuple[str, float]:
        """在分数表上做结构裁决：NCC 最高的两类天生形近时用物理判据定夺。"""
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

        # 7. 4条 vs 5条：五索比四索多的那一根**就压在正中心**，四索的 2x2 排布在
        #    中心留白（与第 4 条的 4p/5p「中心红」同构）。这里不设分差门槛，因为
        #    实测失误恰恰是「错的一方分更高」：tuyou#13 第 2 枚真值四索，5s 拿 0.71、
        #    4s 只有 0.55，差 0.16 —— 任何 `abs(差) < 门槛` 的写法都救不到它。
        #    判据两侧是断开的（build/s45_probe.txt，7 平台 63 格：四索中心暗像素占比
        #    最高 0.044，五索最低 0.547），所以 0.30 落在两堆之间，不靠拟合调参。
        #    沿用第 4~6 条的做法**不动 best_sc**：均分参与 `_try_counts` 的张数选择，
        #    换标签时顺手压低分数会让格子数跟着变，把一个裁决变成两处行为改动。
        if best_lbl in ["4s", "5s"]:
            cy, cx = int(face.shape[0] * 0.5), int(face.shape[1] * 0.5)
            c_gray = cv2.cvtColor(face[cy - 10:cy + 10, cx - 8:cx + 8],
                                  cv2.COLOR_BGR2GRAY)
            has_center_bamboo = float(np.mean(c_gray < 150)) > 0.30
            if not has_center_bamboo and scores.get("4s", 0) > 0.40:
                best_lbl = "4s"
            elif has_center_bamboo and scores.get("5s", 0) > 0.40:
                best_lbl = "5s"

        return best_lbl, round(float(best_sc), 3)

    def set_hand_strip_top(self, frac) -> None:
        """覆盖手牌带上沿比例（[0.3,0.9]）；传 None 恢复默认 0.68 + 自适应兜底。
        供平台/分辨率差异过大时由上层 set_roi 联动覆盖。"""
        if frac is None:
            self._hand_top_frac = 0.68
            self._hand_top_override = False
            return
        try:
            f = float(frac)
        except Exception:
            return
        if 0.3 <= f <= 0.9:
            self._hand_top_frac = f
            self._hand_top_override = True

    def _probe_style(self, crop: np.ndarray,
                     extra_crops: Optional[List[np.ndarray]] = None) -> Optional[str]:
        """用一枚代表牌 + 若干额外代表牌对全部 bank 打分，返回胜出风格（分不足返 None）。

        为什么不再用单枚定路由：单枚 + 单点阈值的实测路由命中率只有 39%（见
        `localtest/probe_style_rules.py` 的 R0 行），选错 bank 等于拿别家平台的
        字模认这家的牌——腾讯底座因此从 100% 被拖到 86%（`localtest/ab_bank_impact.py`，
        声明平台后回到 100%）。改成多枚风格分取平均后，离线牌集命中率 84%，线上
        整帧实切的命中率从 34% 提到 49%（余下未路由的帧退到全库兜底重扫，慢但
        不会硬认错牌）。同风格自配分通常 0.9+，跨风格 <0.65，门槛 0.60 留安全边距。
        """
        self.last_probe_ranking = []   # 先清空：下面每条提前返回的路径都必须留下空表
        if not self._cores:
            return None
        # 风格固定按 bank 顺序遍历：集合并集会打乱顺序，而 `max()` 平局时取先出现的，
        # 顺序不稳定就等于“同一帧两次跑出不同路由”，投票/一致性对拍均不可复现。
        style_order: List[str] = []
        for _lbl, st, *_rest in self._cores:
            if st not in style_order:
                style_order.append(st)
        vecs: List[Dict[str, float]] = []
        for c in [crop] + list(extra_crops or []):
            if c is None or c.size == 0:
                continue
            face = self.extract_face(c)
            hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
            is_yellow_btn = (hsv[:40, :, 0] >= 15) & (hsv[:40, :, 0] <= 35) & (hsv[:40, :, 1] > 100)
            has_btn = (np.sum(is_yellow_btn) > 80)
            c_face = face[38 if has_btn else 10:110, 6:74]
            vec: Dict[str, float] = {}
            for _lbl, style, core_btn, core_plain, _gb, _gp in self._cores:
                src = core_btn if has_btn else core_plain
                s = float(cv2.matchTemplate(c_face, src, cv2.TM_CCOEFF_NORMED).max())
                if s > vec.get(style, 0.0):
                    vec[style] = s
            if not vec:
                # 全零分 = 这枚样本没有任何结构（纯色块，或 extract_face 对退化输入
                # 填的黑图：TM_CCOEFF_NORMED 对方差为零的图像恒返 0）。不投票，
                # 更不能让它把整帧的识别拖成异常（实测纯色裁片曾直接抛 ValueError）。
                continue
            vecs.append(vec)
        if not vecs:
            return None
        # 分母必须是样本数：某风格在那一枚上得 0 分（正交、不匹配）本身就是证据，
        # 当“没打分”剔除就会把多数决变成“各自只在自己的有效样本上平均”——三枚
        # 本家牌也拉不下那一枚别家满分牌，路由会回退到单枚裁决（已踩过，见
        # localtest/test_style_probe.py 的 test_extra_crops_participate_in_the_decision）。
        n = float(len(vecs))
        mean = {st: sum(v.get(st, 0.0) for v in vecs) / n
                for st in style_order if any(st in v for v in vecs)}
        if not mean:
            return None
        # 稳定排序（键只有分数）：平局时保留 style_order 的先后，与原 `max()`
        # “取先出现者”的行为逐字一致 —— 排名表只是把 max 的结果显式化，
        # 不许改变任何一次路由裁决。
        self.last_probe_ranking = sorted(mean.items(), key=lambda kv: -kv[1])
        best_style = self.last_probe_ranking[0][0]
        return best_style if mean[best_style] >= 0.60 else None

    def _probe_runner_up(self, style: Optional[str]) -> Set[str]:
        """探针的第二名属于哪家 bank（只用于放宽分类候选集，不参与节距判断）。

        实测依据是 LOPO 留一平台矩阵（`localtest/eval_unseen_platform.py`）：
        分类候选集由「探针独占一家」放宽到「前两家」，陌生平台逐位
        92.1% -> 95.4%（+3.3pp）、已见 97.9% -> 98.6%（+0.7pp，且与全库打平）。
        之所以有效：探针在没见过的牌风上经常把**别家**排第一（陌生帧实测被路由到
        tencent 23 帧 / zj 13 帧 / weile 12 帧……），只锁一家就等于拿别家字模去认
        这家的牌，而正确答案通常就在第二名上。

        耗时代价按路径分得很开（同进程交替 A/B，`localtest/_ab_probe_runner_cost.py`，
        PC 离线 detect_hand_strip 的 p50）：
          未声明平台（探针自动路由）521ms -> 746ms，**+43%**，这是唯一付全款的路径；
          已声明平台            990ms -> 987ms，**0%**（`_resolve_styles` 因为要保住
          声明平台的专属 bank 会退回整个白名单，候选集本来就有三四家，多一家不改变什么）。
        所以这条改动的钱只在「用户没说平台、系统自己猜」时掏 —— 而那正是陌生平台面前。
        （早期跨进程比较得到的“1.22 倍”是错的：轮间噪声 ±10% 把代价低估了一半。）

        反过来「再加一次主库保底」（top2m）实测与 top2 精度全等、还更慢，故不做；
        「按探针余量 margin 决定是否重扫全库」也已被否证（余量与收窄盈亏不同向）。

        宁缺毋滥：排名表必须来自**本帧**（首项就是本次胜者），否则返回空集。
        跨帧的陈旧第二名会把上一帧的牌风塞进本帧候选集，比不加更难查。
        """
        if style is None or not self.last_probe_ranking:
            return set()
        if self.last_probe_ranking[0][0] != style:
            return set()
        if len(self.last_probe_ranking) < 2:
            return set()
        return {self.last_probe_ranking[1][0]}

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
        blue_mask = ((hsv[:, :, 0] >= 95) & (hsv[:, :, 0] <= 130) & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 100)).astype(np.uint8) * 255

        # 色盘尺寸按取区宽度归一，不用绝对面积。旧写法「400 <= area <= 10000」在
        # 2000x899 真机帧上把筒盘（实测外接框 159x162、轮廓面积 12763）挡在门外，
        # 于是「定缺中」三色盘明明在屏幕中央，is_dingque_phase 却返回 False
        # （逐轮廓取证见 localtest/_diag_dq_disc.py）。
        # 改成「外接框边长 = 取区宽的 3.5%~22%」后与分辨率无关：条盘 117x142、
        # 筒盘 159x162 双双落窗，碎点与整片桌布仍然出局。
        side_lo = max(20.0, sub.shape[1] * 0.035)
        side_hi = max(60.0, sub.shape[1] * 0.22)

        def get_main_center(mask):
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            valid = []
            for c in cnts:
                area = cv2.contourArea(c)
                if area < 400:
                    continue
                bx, by, bw, bh = cv2.boundingRect(c)
                if bw <= 0 or bh <= 0:
                    continue
                if not (side_lo <= bw <= side_hi and side_lo <= bh <= side_hi):
                    continue
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
        bc = get_main_center(blue_mask)

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

        # 4. 蜀山四川麻将等变体：筒盘为蓝色（腾讯为橙色），且条盘绿色常被顶部
        #    装饰/头像角标干扰而漏检（实测蜀山定缺帧绿色抓到顶部装饰块）。改以
        #    「万(红) 与 筒(橙或蓝) 两外盘水平对齐成行」为铁证：两盘同处色盘行
        #    （纵向差<=40）、横向间距占据色盘跨度的合理区间(0.28~0.55 区宽)，
        #    杜绝把相邻小色块误当定缺盘。仅新增判据，不改动上述 1~3 原腾讯判据。
        tc = None
        for cand in (oc, bc):
            if cand and (tc is None or cand[0] > tc[0]):
                tc = cand
        if rc and tc:
            rcx, rcy = rc
            tcx, tcy = tc
            dx = tcx - rcx
            sh = sub.shape[0]
            # 两盘必须落在色盘纵向带（sub 下部 30%~95%）：定缺色盘行居中偏下，
            # 而顶部 y<30% 的红/蓝块多为头像角标/装饰，排除之（防局中帧误判）。
            in_band = (sh * 0.30 <= rcy <= sh * 0.95) and (sh * 0.30 <= tcy <= sh * 0.95)
            if in_band and rcx < tcx and abs(rcy - tcy) <= 40 and sub.shape[1] * 0.28 <= dx <= sub.shape[1] * 0.55:
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
        """检测腾讯欢乐麻将「换三张·选牌中」阶段（金色「换牌」圆按钮）。

        ## 为什么把取区下移、并且不再要求青色「过」按钮

        旧判据「金按钮区 y56%~76% × x61%~73% **且** 右侧青色『过』按钮共存」在真机上
        把两个时刻**用了个反向**（四帧真机图实测，见 localtest/shots_tuyou_select）：

          换三张选牌中：gold=0.0979（过线）cyan=**0.0000**（不过线）⇒ 判 False【漏判】
          局中可杠时  ：gold=0.1787           cyan=0.0254          ⇒ 判 True 【误判】

        两件事各自错在哪：
          1) 青色「过」按钮是**局中动作按钮**（杠/碰/胡 配一个「过」）的证据，从来不是
             换牌阶段的证据——换牌弹窗只有单个金色「换牌」按钮，压根没有「过」。把它当
             必要条件，等于要求「画面里同时存在局中动作按钮」才算换牌阶段。
          2) 取区 y56%~76% 把局中动作按钮（实测中心 y≈0.678）**整个包住**，却把换牌按钮
             （实测中心 y≈0.760，在手牌行正上方）**切掉一半**。金区因此对局中更敏感。

        窗口因此改成实测可分的 y74%~88% × x64%~78%（只覆盖「手牌行正上方」这一条
        低带，物理含义就是换牌按钮的专属位置；动作按钮在牌河中带，落在窗外）。

        ## 但「占比」单独用仍然不够：还得是那个按钮的形状（41 帧真图实测）

        只用占比门槛 0.05 时，一局**加载界面**（沙滩 + 遮阳伞，非牌桌）在同一条低带上
        量到 gold=0.0673 —— 越过门槛，于是引擎对着一个还没进桌面的画面报「换三张」。
        这就是用户说的「明明不在换牌阶段却显示换牌」。占比这条线在真换牌帧上是
        0.0946~0.1000（12 帧实测，散布只有 ±0.003），离 0.0673 太近，调门槛解决不了：
        抬到 0.07 只是把「沙滩」和「换牌按钮」之间那 1.4 倍裕度留着，下一次换张壁纸
        就没了。

        真正分得开的是**形状**：换牌按钮是低带里一个孤立的扁圆盘，而沙滩是一整片
        连通到带边的背景。41 帧（37 帧 GT + 4 帧真机）逐帧量最大金色连通域：

          12 帧真换牌   宽高比 2.77~2.90   外接框填充率 0.651~0.675   高/带高 0.333~0.349
          加载界面(沙滩) 宽高比 15.23      外接框填充率 0.922         高/带高 0.103
          局中可杠帧    宽高比 5.60       外接框填充率 0.636         高/带高 0.040

        三个窗口 [1.8,4.5] / [0.55,0.75] / [0.20,0.60] 把 12 帧真换牌**全部**收进来
        （无一漏），把加载界面三项**全部**打出格。校验脚本见
        localtest/_validate_swap_shape.py。

        视觉在这里只能当「可能」，不能当「一定」：排他证据在 engine 侧（本帧必须真的
        读到手牌、且牌局账本还没进入摸打），见 process 里的开局阶段门控。
        """
        if image_bgr is None or image_bgr.size == 0:
            return False
        ih, iw = image_bgr.shape[:2]
        # 换牌按钮专属低带：紧贴手牌行上方；杠/碰/胡动作按钮在牌河中带（y≈0.68），在窗外
        gold_zone = image_bgr[int(ih * 0.74):int(ih * 0.88), int(iw * 0.64):int(iw * 0.78)]
        if gold_zone.size == 0 or gold_zone.shape[0] < 10 or gold_zone.shape[1] < 10:
            return False
        bh, bw = gold_zone.shape[:2]
        hsv_g = cv2.cvtColor(gold_zone, cv2.COLOR_BGR2HSV)
        gold = ((hsv_g[:, :, 0] >= 14) & (hsv_g[:, :, 0] <= 35)
                & (hsv_g[:, :, 1] >= 120) & (hsv_g[:, :, 2] >= 150)).astype(np.uint8) * 255
        # 真机实测：换牌阶段 0.0946~0.1000，局中杠按钮同窗口 0.0027
        total = int(np.count_nonzero(gold))
        if total == 0 or total / float(bw * bh) < 0.05:
            return False
        # 占比过线只说明「这一带有金色」，不说明「那是按钮」。接着量形状。
        n_lab, _lab, st, _c = cv2.connectedComponentsWithStats(gold, 8)
        if n_lab <= 1:
            return False
        i = int(np.argmax(st[1:, cv2.CC_STAT_AREA])) + 1
        area = int(st[i, cv2.CC_STAT_AREA])
        sw = int(st[i, cv2.CC_STAT_WIDTH])
        sh = int(st[i, cv2.CC_STAT_HEIGHT])
        if area <= 0 or sw <= 0 or sh <= 0:
            return False
        return (1.8 <= sw / float(sh) <= 4.5
                and 0.55 <= area / float(sw * sh) <= 0.75
                and 0.20 <= sh / float(bh) <= 0.60)

    def is_pick_phase(self, image_bgr: np.ndarray) -> bool:
        """检测腾讯欢乐麻将「请任选一张牌」弹窗或选牌确定界面。"""
        if image_bgr is None or image_bgr.size == 0 or not self.is_available:
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
                    # 必须确认为真实麻将牌，排除遮阳伞/冰淇淋等卡通背景误报
                    lbl, sc = self.classify_tile(tile_crop)
                    if sc >= 0.50:
                        return True

        # 形式2：任选牌弹窗（深色圆角弹窗背景 + 内嵌白色候选牌横排）。
        # 严格门控：仅在尚未建立稳定摸打手牌、且真正读出选牌候选时成立
        row = image_bgr[int(ih * 0.56):int(ih * 0.78), int(iw * 0.15):int(iw * 0.92)]
        if row.size > 0:
            hsv = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
            dark_bg = float(np.mean((hsv[:, :, 2] < 80) & (hsv[:, :, 1] < 60)))
            white_tiles = float(np.mean((hsv[:, :, 2] > 180) & (hsv[:, :, 1] < 50)))
            if dark_bg >= 0.18 and white_tiles >= 0.08:
                # 检查候选牌：弹窗内必须实际提取出至少 3 张不同候选牌
                cands = self.detect_pick_candidates(image_bgr)
                if len(cands) >= 3:
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


    def _tile_pitch(self, is_tile: np.ndarray, y_min: int, bx: int, by: int,
                    bw: int, bh: int) -> float:
        """手牌块列剖面的水平周期 = 牌的真实节距；量不到返回 0.0。

        为什么需要它：`face_w = bh/1.35` 把牌宽寄望在合并块的高度上，而合并块
        常被癞子角标/浮起选中牌/贴边 UI 撑高。实测途游帧 03：块高 335（真牌高 246）
        → face_w=248 → raw_adj=7.39，而真张数是 10，于是对网格根本没进候选集，
        只能在错网格上打分（逐 k 实测：对的 k=10 均分 0.914/min 0.838/10 张全
        过门，被选中的 k=11 只有 0.531）。全 91 帧预检：按块高推节距时真张数进
        top-2 候选的只有 79 帧，改用实测节距后是 90 帧，且无一帧倒退。

        两个算法选择都是量出来的，不是随手定的：
        · 自相关而不是找暗色分隔条：牌贴牌时连通块会粘成一段，而分隔条深度受牌面
          花纹影响极大（实测它会把筒子的圆圈当边界）。
        · 取「第一个足够强的峰」而不是全局最高峰：全局峰会选二次谐波（实测微乐
          真节距约 76 会量出 2 倍频），第一个强峰天然免疫倍频。
        用 FFT 而非 `np.correlate`：后者是直交 O(n²)，这函数每帧都跑，在实时链路上。
        """
        r0, r1 = by - y_min, by - y_min + bh
        if r1 <= r0 or bw < 100:
            return 0.0
        prof = is_tile[max(0, r0):min(is_tile.shape[0], r1), bx:bx + bw].mean(axis=0)
        n = int(prof.size)
        if n < 60:
            return 0.0
        m = prof - prof.mean()
        # 补零到 2n 做循环相关 = 线性相关
        f = np.fft.rfft(m, n=2 * n)
        ac = np.fft.irfft(f * np.conjugate(f), n=2 * n)[:n]
        if ac[0] <= 1e-9:
            return 0.0
        ac = ac / ac[0]
        lo = 20                                   # 小于 20px 的周期不可能是牌宽
        hi = min(n - 2, max(lo + 2, bw // 3))     # 节距不会小于带宽 1/3（>30 张）
        if hi <= lo:
            return 0.0
        best = float(ac[lo:hi].max())
        for lag in range(lo, hi):
            if (ac[lag] >= 0.85 * best
                    and ac[lag] >= ac[lag - 1] and ac[lag] >= ac[lag + 1]):
                return float(lag)
        return 0.0

    def detect_hand_strip(self, image_bgr: np.ndarray) -> List[Tuple[Rect, str, float]]:
        # 每次调用先洗掉上一次的局格：中途任何一条早退路径 returning [] 都得留下
        # “没有局格”这个事实，否则评测会拿上一帧的格去对齐本帧的框。
        self.last_hand_grid = None
        # 分支留档同样每帧清空：任何一条早退路径都必须留下“本帧没走查表也没走探针”，
        # 否则守卫会把上一帧的分支当成本帧的证据。
        self.last_layout_branch = None
        self.last_pitch_candidates = None
        # 每档候选的均分留档（一次 `_try_counts` 一组）。它不参与任何判定，只服务
        # 剖析与守卫：「要不要给第二档候选也切一整行」这种减成本的决定，必须能用
        # 真帧数据证明第二档赢不了，而不是靠肉眼相信几何估计够准。
        self.last_count_runs: List[List[Tuple[int, float]]] = []
        if image_bgr is None or image_bgr.size == 0 or not self.is_available:
            return []

        ih, iw = image_bgr.shape[:2]

        def _scan_window(win_y_min):
            win_y_max = int(ih * 0.99)
            win_strip = image_bgr[win_y_min:win_y_max, :]
            win_sh, win_sw = win_strip.shape[:2]
            win_hsv = cv2.cvtColor(win_strip, cv2.COLOR_BGR2HSV)
            win_felt = (win_hsv[:, :, 0] >= 50) & (win_hsv[:, :, 0] <= 110) & (win_hsv[:, :, 1] >= 50)
            win_tile = ~win_felt & (win_hsv[:, :, 2] > 75) & (win_strip[:, :, 0] > 110) & (win_strip[:, :, 1] > 110) & (win_strip[:, :, 2] > 110)
            win_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            win_mask = cv2.morphologyEx(win_tile.astype(np.uint8) * 255, cv2.MORPH_CLOSE, win_kernel)
            win_contours, _ = cv2.findContours(win_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            win_boxes = []
            for c in win_contours:
                x, y, bw, bh = cv2.boundingRect(c)
                # 立牌高度通常占手牌带 35% 以上；`x >= sw*0.05` 是用来滤掉左下角头像/积分牌的，
                # 那些干扰物又窄又贴边。但**手牌行本身也可以铺到最左边**：途游四川麻将
                # 2712x1220 实测主块 x=63、宽 2355（13 张牌连成一整块），高度门 247>132 明明通过，
                # 却被这条贴边限制整块判死 → 屏幕上明摆着 13 张牌却报 0 张。
                # 因此宽度已达带宽 30% 的块不受这条贴边限制（它不可能是头像）；
                # 贴边粘连交给下面的垂直投影修剪处理。
                wide_block = bw >= win_sw * 0.30
                if (bh > win_sh * 0.35 and x < win_sw * 0.95 and bw >= 25
                        and (wide_block or x >= win_sw * 0.05)):
                    # 垂直投影修剪：去除边缘粘连的头像徽章、积分标牌、UI阴影等细窄干扰物
                    sub_mask = win_tile[y:y + bh, x:x + bw]
                    col_counts = np.sum(sub_mask, axis=0)
                    thresh = max(15, int(win_sh * 0.25))
                    valid_cols = np.where(col_counts >= thresh)[0]
                    if len(valid_cols) >= 20:
                        refined_x = x + int(valid_cols[0])
                        refined_bw = int(valid_cols[-1] - valid_cols[0] + 1)
                        win_boxes.append((refined_x, y + win_y_min, refined_bw, bh))
                    else:
                        win_boxes.append((x, y + win_y_min, bw, bh))
            return win_boxes, win_tile, win_y_min, win_sh

        # 手牌带上沿比例：默认 0.68；允许 set_hand_strip_top 覆盖；主块实测自适应兜底
        # （常量窗口取不到牌时放宽到 0.55 再扫一次，解决手牌整体偏高/分辨率差异导致带上沿移位）。
        top_frac = getattr(self, "_hand_top_frac", 0.68)
        overridden = getattr(self, "_hand_top_override", False)
        boxes, is_tile, y_min, sh = _scan_window(int(ih * top_frac))
        if not boxes and not overridden:
            boxes, is_tile, y_min, sh = _scan_window(int(ih * 0.55))

        if not boxes:
            return []

        boxes.sort(key=lambda b: b[0])

        # 水平手牌块合并：当主手牌区被阴影/弹窗边缘遮挡分割成多个相邻块时，
        # 同一底边基线（底边 y 差 <= 22px）且后块宽度大于单张摸牌（> 1.4 * face_w）时合并
        merged_boxes = []
        for b in boxes:
            if not merged_boxes:
                merged_boxes.append(b)
                continue
            prev_b = merged_boxes[-1]
            prev_bot = prev_b[1] + prev_b[3]
            curr_bot = b[1] + b[3]
            gap = b[0] - (prev_b[0] + prev_b[2])
            std_w = b[3] / 1.35 if b[3] >= 30 else 50
            if abs(prev_bot - curr_bot) <= 22 and 0 <= gap <= std_w * 2.5 and b[2] > std_w * 1.4:
                new_x = prev_b[0]
                new_y = min(prev_b[1], b[1])
                new_w = (b[0] + b[2]) - new_x
                new_h = max(prev_bot, curr_bot) - new_y
                merged_boxes[-1] = (new_x, new_y, new_w, new_h)
                continue
            merged_boxes.append(b)
        boxes = merged_boxes

        main_box = max(boxes, key=lambda b: b[2])
        bx, by, bw, bh = main_box
        # 牌面宽度：牌高/1.35 是全平台共性几何（腾讯旧版写死 0.0547*iw，
        # 换平台/换分辨率/发牌动画压扁时张数估计直接崩掉并整行拒识）。
        face_w = bh / 1.35 if bh >= 40 else 0.0547 * iw
        # 实测节距优先：块高会被角标/浮牌撑大（理由见 `_tile_pitch`），而几何那条
        # 只作为失手重扫时的兜底候选保留（下面 `c_geo`），不当主路由。
        face_w_geo = face_w
        pitch = self._tile_pitch(is_tile, y_min, bx, by, bw, bh)
        if pitch and 3.0 <= bw / pitch <= 14.6:
            face_w = pitch
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
        # （腾讯）节距≈0.0547*iw。风格探针路由：探针命中腾讯→只走腾讯节距候选；
        # 命中其他风格→只走相邻候选并把分类限定在该 bank（开销与单平台同级）；
        # 探针不定→两路候选并集全模板兜底。均分不达标时再全量重扫一次。
        raw_adj = bw / face_w
        raw_tec = bw / (0.0547 * iw)
        legal_standing = [1, 3, 4, 6, 7, 9, 10, 13] if drawn_box else [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14]
        c_adj = sorted(legal_standing, key=lambda c: abs(c - raw_adj))[:2]
        c_tec = sorted(legal_standing, key=lambda c: abs(c - raw_tec))[:2]
        # 牌高几何那一档：平时不试（与 c_adj 同形，多试一档就是多扫一整行牌），
        # 只在均分不达标的失手重扫里并进来——实测节距偶发量错时还有退路。
        c_geo = (c_adj if face_w == face_w_geo else
                 sorted(legal_standing, key=lambda c: abs(c - bw / face_w_geo))[:2])

        if self._declared_platform is not None and not self.probe_when_declared:
            # 平台已声明：排布方式查表，不再扫全库猜牌风（依据、代价与等价性实测
            # 都写在 `PLATFORM_OVERLAP_LAYOUT` 的注释里）。
            # 候选集这里给的是**平台白名单本身**而不是 None：None 会被下面
            # “均分不达标就四档重扫”那条兜底当成“已经扫过全集、无需重扫”，
            # 等于顺手把失手救援也关掉 —— 本改动只关探针，不关兜底。
            overlap = self._declared_platform in PLATFORM_OVERLAP_LAYOUT
            cand_counts = (c_tec if overlap else c_adj)
            probe_styles = self.active_styles
            self.last_layout_branch = f"table-{'tec' if overlap else 'adj'}"
            # 只走第一档（几何最近档），失手交给下面那条全量重扫：上面「只关探针」的
            # 理由同样适用于这里——本改动只关**冗余**的那一整行，不关兜底。
            seq_first = not self.hand_count_dual
        else:
            # 未声明平台（用户没说、或调试口子拨回旧行为）：探针才有权路由。
            pcx = int(bx + bw / 2)
            half = max(10, int(face_w * 0.5))
            probe_crop = image_bgr[by:by + bh, max(0, pcx - half):min(iw, pcx + half)]
            # 额外代表牌：两种节距模型各取一枚靠右的内部牌（避开首尾粘连、也避开
            # 左侧头像遮挡）。一枚牌被角标/阴影污染就能把整帧路由到别家 bank，
            # 所以探针要看几枚再取平均（实测命中率 39% -> 84%，见 _probe_style）。
            probe_extra: List[np.ndarray] = []
            for k in dict.fromkeys([c_adj[0], c_tec[0]]):   # 两种节距估到同一张数时不重复扫
                if k < 2:
                    continue
                tw = bw / float(k)
                i = min(k - 1, max(0, int(k * 0.75)))
                x1 = int(round(bx + i * tw))
                x2 = int(round(bx + (i + 1) * tw))
                if x2 - x1 >= 12:
                    probe_extra.append(image_bgr[by:by + bh, x1:min(iw, x2)])
            style = self._probe_style(probe_crop, probe_extra)
            # 节距候选保持「探针独占一家」来定（用哪套牌宽模型取决于最像的那家，
            # 第二名在这个判断上没有意义）；分类候选集额外并上第二名，理由见
            # `_probe_runner_up`。两者分开是本改动的全部范围。
            runner = self._probe_runner_up(style)
            if style == "tencent":
                cand_counts, probe_styles = c_tec, {"tencent"} | runner
                self.last_layout_branch = "probe-tec"
            elif style is not None:
                cand_counts, probe_styles = c_adj, {style} | runner
                self.last_layout_branch = "probe-adj"
            else:
                cand_counts, probe_styles = list(dict.fromkeys(c_adj + c_tec)), None
                self.last_layout_branch = "probe-both"
            # 探针分支不早停：探针本身就是「不确定」的那条路，候选可能有 4 档，
            # 少跑一档就是拿正确率换速度，而这条路上没有「用户已声明平台」这个
            # 硬事实顶着（实测微乐帧 13 探针误路由腾讯就是把 6m/7m 读成 3m 的）。
            seq_first = False

        # 两套候选一并留档：守卫要靠它判断「查表」与「探针猜」是否真的做了同一个决定，
        # 而不是等帧输出碰巧相同才以为接线正确（同形是常态，见 __init__ 处的实测说明）。
        self.last_pitch_candidates = (list(c_adj), list(c_tec))

        def _try_counts(cands: List[int], styles):
            # 两段式：先把**所有**张数候选的格片都切完，一次性交给 `_classify_batch`，
            # 再按候选分别装配取均分。
            # 为什么值得多这一层：实测一帧通常有 2 个张数候选、各 13 枚
            # （build/parallel_hit.txt：每帧 2.0 批 × 平均 13.2 枚）。逐批打分就是 2 个
            # barrier，每批都被本批最慢的那枚拖住；并成 26 个任务后 worker 越多收益越
            # 明显。这**不省一次打分**：同一格片、同一候选集、同一个纯函数，只是换了
            # 提交顺序，所以结果与逐批打分逐格全等（守卫逐格核对 rect+label+conf）。
            plans: List[Tuple[int, float, List[Rect], List[np.ndarray]]] = []
            for k in cands:
                tw = bw / float(k)
                crops: List[np.ndarray] = []
                rects: List[Rect] = []
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

                    crops.append(image_bgr[y_top_c:y_bot_c, x1:x2])
                    rects.append((x1, y_top_c, x2 - x1, y_bot_c - y_top_c))
                plans.append((k, tw, rects, crops))

            flat: List[np.ndarray] = [c for _k, _tw, _r, cs in plans for c in cs]
            scored = self._classify_batch(flat, styles)
            best_dets: List[Tuple[Rect, str, float]] = []
            best_mean = -1.0
            best_geo = None
            run: List[Tuple[int, float]] = []
            pos = 0
            for k, tw, rects, crops in plans:
                # zip 按格位装回：保序是这条路能开并行的前提（见 _classify_batch）。
                dets: List[Tuple[Rect, str, float]] = [
                    (r, lbl, sc) for r, (lbl, sc)
                    in zip(rects, scored[pos:pos + len(crops)])]
                pos += len(crops)
                mean_sc = float(np.mean([d[2] for d in dets])) if dets else 0.0
                run.append((k, mean_sc))
                if mean_sc > best_mean:
                    best_mean = mean_sc
                    best_dets = dets
                    best_geo = (float(bx), float(tw), int(k))
            self.last_count_runs.append(run)
            return best_dets, best_mean, best_geo

        first_cands = [cand_counts[0]] if (seq_first and len(cand_counts) > 1) else list(cand_counts)
        best_standing_dets, best_standing_mean, grid = _try_counts(first_cands, probe_styles)
        self.last_hand_grid = grid

        # 第一档不达标就只走下面那条全候选全模板重扫，**不插「只补第二档」这一级**。
        # 补跑第二档看着像省时间（第二档过了 0.55 就能躲开六档重扫），41 帧实测一次
        # 都没省到：第二档赢的 3 帧均分只有 0.469/0.289/0.249，全都不达标，照旧进重扫，
        # 补跑只是在已经很慢的帧上再多切一整行（+13 枚）。
        # 而重扫的候选集 `c_adj + c_tec + c_geo` 是本档候选集的**超集**，风格集在已声明
        # 平台时与 probe_styles 是同一个（`_resolve_styles`：None → 平台白名单），所以
        # 逐格分数完全相同、胜者不会更差。把补跑整条关掉后 41 帧输出仍与双档逐格全等
        # （`localtest/test_hand_count_seq_guard.py` 的变异检验就是这么量的），所以不留
        # 这条从未生效过的分支——「写了但从未触发过的兜底」本身就是本次要清的坑。

        # 探针路由失误（风格误判/尺度失配致整行低分）→ 全候选全模板重扫一次
        if best_standing_mean < 0.55 and probe_styles is not None:
            fb_dets, fb_mean, fb_geo = _try_counts(
                list(dict.fromkeys(c_adj + c_tec + c_geo)), None)
            if fb_mean > best_standing_mean:
                best_standing_dets, best_standing_mean = fb_dets, fb_mean
                self.last_hand_grid = fb_geo

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

        # 核心防伪与非牌局拦截（已拆除"全有或全无"断崖）：
        # 达标行（均值>=0.55 且峰值>=0.68）与旧行为完全一致，整行返回；
        # 原本会被整行作废的边界场景不再全盘放弃，改逐张置信救援——低于硬地板
        # （非牌局背景峰值通常 <0.5）仍整行放弃，否则保留达标张（>=0.5），
        # 低置信张交上层标灰、不进建议，杜绝"少切 1 张整帧空白"式连锁失败。
        if best_standing_mean >= 0.55 and top_conf >= 0.68:
            return all_dets
        if top_conf < 0.5:
            self.last_drawn_tile = None
            return []
        rescued = [d for d in all_dets if d[2] >= 0.5]
        if len(rescued) < 2:
            self.last_drawn_tile = None
            return []
        return rescued

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
