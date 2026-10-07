"""玩法目录（20 种主流玩法 + 4 条 legacy 兼容条目）与文件共享态。

索引约定见下方 34 型注释。规则差别全部以**数据**形式写在本表里，改规则只改字典，
逻辑层不动；分析器只有两个：`sichuan`（28 槽 + 定缺 + 分门 DP）与 `std`（34 型数据
驱动）。无 analyzer 字段的条目是 legacy，只能走旧的通用 Shanten 回退。

选型原则：每条玩法必须与现有玩法至少在「牌集 / 鬼牌 / 结构约束」一个维度上有**真
实差异**，否则只是换个名字凑数。按主差异维度分组：

  定缺 + 鬼牌（川麻家族，analyzer=sichuan）
    sc_xz     108 张无字、定缺、无鬼                sc_xz_3p  三人血战（3 人）
    sc_hz     112 张、+4 张红中(7z)作鬼、定缺        sc_xl     血流成河（同牌集，胡后走向见「未建模」）
    gy_zj     贵阳捉鸡（红中作鬼；捉鸡/豆杠未建模）
  鬼牌种类/张数（analyzer=std）
    std_tdh   无鬼、34 型全牌                       wz_tdh    无鬼、108 张无字
    hz_all    一鬼=红中(7z)、全牌                   fc_all    一鬼=发财(6z)、全牌
    hz_bd     一鬼=白板(5z)、全牌 + 每用一鬼加一番    zfb_bd    三鬼=中发白同时作鬼
    wh_kk     一鬼=红中 + 必须开口（need_open 仅软提示）  cf_wild  鬼由本局翻牌决定（哨兵注入）
  结构约束（关顺子 / 只碰不吃 / 碰碰胡）
    cs_zz     112 张、红中作鬼、sequences=False（转转胡=碰碰胡）
    pp_zz     108 张无字、sequences=False
    hz_ne     全牌、红中作鬼、sequences=False（红中麻将禁吃）
    gd_hz     112 张、红中作鬼、可吃可碰
  牌集规模 / 人数
    mj_3p     三麻：去 2/8 万筒条与白板 → 27 型 108 张、3 人
    mj_2p     二麻（筒条版）：18 型 72 张、2 人
    db_qh     东北穷胡：全牌 + 幺九将约束（need_terminals）

未建模规则（**不要当成已实现**，涉及这些口径的番数/走向会少报或不报）：
  - 番型表只有 calc_fan 里的 6 种（国士16 / 七对4 / 清一色+4 / 混一色+2 / 碰碰胡+2 /
    百搭×N）。平胡翻番、鸡胡、门清、断幺、全中、杠上开花、抢杠、海底、封顶倍数未建模。
  - 买马 / 抓鸟 / 捉鸡豆杠结算、换三张（换牌阶段只推荐不换牌分值）、查叫 / 退税未建模。
  - 血战到底与血流成河的**胡后走向**差异在结算层：手牌分析层两者同构（血流多 4 张红中鬼）。
  - need_open 只做软提示（副露可见性未接进判胡）；need_all_pungs 不是硬门，必须与
    sequences=False 同时设置才等价于「只能碰不能吃」。
  - 花牌 / 144 张牌集（上海、南京麻将）在 34 类识别下不可实现，故未收录。
  - 日本麻将刻意不加：缺役种检查会把无役手牌报成可听牌，属于「能跑但误导」。
  - NativeEngine（C++）只覆盖 sichuan 且无鬼牌玩法；路由判据见 native_solver_ready。

玩法切换的跨层通路：悬浮窗(Dart)把选中玩法写入本文件指向的 JSON，
Python 引擎每帧读取（文件极小，开销可忽略）。路径与 Dart 端保持一致。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Set

# 与 Dart 端 (lib/overlays/mahjong_overlay.dart) 完全一致的绝对路径。
# 这是 Android 上该 App 的「外部私有存储 / files」目录，App 进程内的
# Java / Chaquopy-Python 与 Dart 都能读写，无需任何额外权限。
MODE_PATH = "/storage/emulated/0/Android/data/com.example.auto_vision/files/mahjong_mode.json"

DEFAULT_MODE = "sc_hz"

# 34 型索引约定（与 trainer/utils/convert.py 相同）：
#   0-8   1m..9m
#   9-17  1p..9p
#   18-26 1s..9s
#   27-33 1z..7z（东南西北白發中，31=5z白板，33=7z红中）
ALL_34 = list(range(34))


def _removed_to_available(removed: List[int]) -> List[int]:
    return [i for i in ALL_34 if i not in set(removed)]


# 三麻：去 2m(1) 8m(7) 2p(10) 8p(16) 2s(19) 8s(25) 白(31)
_SANMA_REMOVED = [1, 7, 10, 16, 19, 25, 31]

# 二麻：去全部筒(9-17)与条(18-26)，仅留万(0-8)与字牌(27-33)
_TWOP_REMOVED = list(range(9, 27))

# 运行时鬼牌（财神）。大量地方玩法的财神是**每局翻牌决定**的，写死在 MODES 里
# 必然与真实对局不符，所以那类玩法只存 LAIZI_CONFIG 哨兵，牌面由上层每局注入。
# 刻意不落盘：鬼牌的时效就是一局，重启后必须重新注入；持久化会造成
# 「上一局的财神被当成这一局」这种难查的错推荐。
# 本常量必须定义在 MODES 之前：它在玩法表里是**加载期求值**的引用。
LAIZI_CONFIG = "config"
_LAIZI_EXPLICIT: List[int] = []


MODES: Dict[str, Dict] = {
    # 规则字段说明（供 std 分析器/引擎消费，缺省即关闭）：
    # - analyzer: "sichuan"=川麻家族引擎（默认）; "std"=通用地方玩法引擎
    # - seven_pairs / kokushi: 允许七对 / 国士无双胡型
    # - sequences: 是否允许顺子（转转/碰碰类玩法只能碰杠不能吃）
    # - need_all_pungs / need_terminals / need_open: 胡牌结构约束（碰碰胡/
    #   幺九将/必须开口）；need_open 依赖副露可见性，当前为软提示
    # - fan_wild_per_use: 每用一张赖子加一番（百搭翻倍类）
    # 血战到底 vs 血流成河的胡牌后走向差异在结算阶段，手牌分析层两者
    # 规则同构；但血流成河带 4 张红中赖子（112 张），血战为纯 108 张。
    "sc_hz": {
        "name": "血流红中",
        "players": 4,
        "available": list(range(27)) + [33],  # 0-26 万筒条各9张 + 33 (7z 红中)
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 112,
        "dingque": True,
        "laizi": 33,  # 7z 红中
        "analyzer": "sichuan",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": False,
    },
    "sc_xz": {
        "name": "川麻·血战到底",
        "players": 4,
        "available": list(range(27)),  # 0-26 纯万筒条108张，无字牌无赖子
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": True,
        "laizi": None,
        "analyzer": "sichuan",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": False,
    },
    "sc_xl": {
        "name": "川麻·血流成河",
        "players": 4,
        "available": list(range(27)) + [33],  # 血流成河带 4 张红中赖子
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 112,
        "dingque": True,
        "laizi": 33,  # 红中做赖子，连胡到底
        "analyzer": "sichuan",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": False,
    },
    "gy_zj": {
        "name": "贵阳捉鸡",
        "players": 4,
        "available": list(range(27)) + [33],  # 红中为百搭牌（鸡牌在胡后结算阶段，不入手牌分析）
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 112,
        "dingque": True,
        "laizi": 33,
        "analyzer": "sichuan",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": False,
    },

    # 2. 经典大众系列
    "std_tdh": {
        "name": "大众推倒胡",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": None,
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
    },
    "wh_kk": {
        "name": "武汉开口翻",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": 33,  # 痞子（红中）癞子，不可吃碰打出
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
        "need_open": True,  # 必须开口（吃碰/自摸听）才能胡：软提示，见 engine 消费处
    },
    "db_qh": {
        "name": "东北穷胡",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": None,
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
        "need_terminals": True,  # 胡牌必须带幺九牌（穷胡严格判定）
    },
    "hz_bd": {
        "name": "杭州百搭",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": 31,  # 5z 白板做万能百搭
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
        "fan_wild_per_use": True,  # 每用一张百搭番数翻倍（爆头大番）
    },

    # 3. 地方顶流系列
    "gd_hz": {
        "name": "广东红中王",
        "players": 4,
        "available": list(range(27)) + [33],
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        # 牌库实为 27×4+4=112（部分台版去部分数牌为 100，牌河物理守恒
        # 按每种 4 张计算不受 wall 影响，wall 仅作剩余牌数显示基准）
        "wall": 112,
        "dingque": False,
        "laizi": 33,  # 红中做鬼牌（任搭），不可打出
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
    },
    "cs_zz": {
        "name": "长沙转转麻将",
        "players": 4,
        "available": list(range(27)) + [33],
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 112,
        "dingque": False,
        "laizi": 33,  # 红中赖子；转转胡=碰碰胡，红中必作将
        "analyzer": "std",
        "sequences": False,  # 不能吃，只能碰杠
        "seven_pairs": True,
        "kokushi": False,
        "need_all_pungs": True,  # 转转胡结构：全刻子+将
    },

    # ==== 4. 新增系列（1.4）====
    # 选型原则：每条新玩法与现有玩法至少在「牌集 / 鬼牌 / 结构约束」一个维度上
    # 有真实差异，且该差异能被 analyzer 字段精确表达——否则就是换名字凑数，
    # 给用户的体感是“选了不同玩法、推荐结果一模一样”。
    # 未建模的规则（买马/抓鸟/封顶/番型表细节）逐条写在注释里，不当作已实现。
    "wz_tdh": {
        "name": "无字推倒胡",
        "players": 4,
        "available": list(range(27)),  # 纯万筒条 108 张，无字牌
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": False,
        "laizi": None,
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        # 无字牌⇒国士结构不可能存在，显式置 False（而不是依赖算法自然不命中）：
        # 一旦上游误传了字牌，这里能暴露问题而不是默默多算一种胡型。
        "kokushi": False,
    },
    "hz_all": {
        "name": "红中麻将（全牌）",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": 33,  # 红中作万能鬼牌，不可吃碰打出
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
        # 与广东红中王的差异是牌集：本玩法保留全部字牌（136），后者只到 112。
    },
    "fc_all": {
        "name": "发财麻将",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": 32,  # 6z 发财作鬼牌（部分地区玩法以发财代替红中做赖子）
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
    },
    "zfb_bd": {
        "name": "中发白三鬼",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        # 三类全鬼：需要 laizi 支持列表（StdAnalyzer.laizi_set），单值写法只能支持一鬼。
        "laizi": [31, 32, 33],
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
        # 不置 fan_wild_per_use：三鬼牌本身已是高倍玩法，再逐张加番会虚抬推荐。
    },
    "pp_zz": {
        "name": "碰碰胡（无字）",
        "players": 4,
        "available": list(range(27)),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": False,
        "laizi": None,
        "analyzer": "std",
        "sequences": False,  # 只能碰杠不能吃，牌面必为全刻子+将
        "seven_pairs": True,
        "kokushi": False,
        "need_all_pungs": True,
    },
    "sc_xz_3p": {
        "name": "川麻·三人血战",
        "players": 3,
        "available": list(range(27)),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": True,
        "laizi": None,
        "analyzer": "sichuan",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": False,
        # players 只影响人数展示与结算基数；手牌分析（向听/进张/查叫）与四人血战同构，
        # 所以本条与 sc_xz 的推荐结果相同是**正确行为**，不是凑数。
    },
    "mj_2p": {
        "name": "二人麻将（筒条）",
        "players": 2,
        "available": list(range(9, 27)),  # 只留 1p-9s 共 18 类 72 张
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 72,
        "dingque": False,
        "laizi": None,
        "analyzer": "std",   # 旧 2p 条目无 analyzer 字段，只能走通用 Shanten 回退；本条补齐算番/赖子能力
        "sequences": True,
        "seven_pairs": True,
        "kokushi": False,
    },
    "mj_3p": {
        "name": "三人竞技（去2/8）",
        "players": 3,
        "available": _removed_to_available(_SANMA_REMOVED),  # 去 2m8m2p8p2s8s 与白板，共 27 类 108 张
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": False,
        "laizi": None,
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        # 国士在本牌集下仅部分幺九可用（白板被剔），因此置 False 避免报出做不出的胡型。
        "kokushi": False,
    },

    "hz_ne": {
        "name": "红中麻将（全牌·禁吃）",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": 33,
        "analyzer": "std",
        "sequences": False,  # 只能碰杠；与 hz_all 的唯一差异就是能不能吃
        "seven_pairs": True,
        "kokushi": True,
    },
    "cf_wild": {
        "name": "自选鬼牌（每局指定）",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        # 财神由本局翻牌决定的玩法（温州/江西/内蒙/哈灵/闲来…）都走这一条：
        # 牌面写死必然不准，所以这里存哨兵，实际鬼牌由 set_laizi_explicit() 注入。
        "laizi": LAIZI_CONFIG,
        "analyzer": "std",
        "sequences": True,
        "seven_pairs": True,
        "kokushi": True,
    },

    # 向下兼容历史别名
    "sc": {
        "name": "川麻·血战到底",
        "players": 4,
        # 与 sc_xz 对齐：血战到底无字牌无赖子。此前写成 range(27)+[33]（28 类=112 张）
        # 却配 wall=108 / laizi=None，三项自相矛盾；靠 ALIASES 指向 sc_xz 才没在运行时
        # 暴露，但任何直接读这张表的代码（校验/统计/新工具）都会拿到脏数据。
        "available": list(range(27)),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": True,
        "laizi": None,
    },
    "4p": {
        "name": "大众推倒胡",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": None,
    },
    "3p": {
        "name": "三人竞技",
        "players": 3,
        "available": _removed_to_available(_SANMA_REMOVED),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": False,
        "laizi": None,
    },
    "2p": {
        "name": "二人麻将",
        "players": 2,
        "available": _removed_to_available(_TWOP_REMOVED),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 64,
        "dingque": False,
        "laizi": None,
    },
}


ALIASES = {
    "sc": "sc_xz",
    "sc_xlch": "sc_xl",
    "4p": "std_tdh",
}


def get_mode(key: str = DEFAULT_MODE) -> Dict:
    """返回玩法配置 dict（含 name/players/available/hand_sizes/wall/dingque/laizi）。

    哨兵 LAIZI_CONFIG 在此处解析为运行时注入的鬼牌；注意返回的是**副本**，
    调用方改动不会污染 MODES 原表。
    """
    key = ALIASES.get(key, key)
    m = MODES.get(key, MODES[DEFAULT_MODE])
    if m.get("laizi") == LAIZI_CONFIG:
        m = dict(m)
        m["laizi"] = list(_LAIZI_EXPLICIT)
    return m


def is_dingque_mode(key: str = DEFAULT_MODE) -> bool:
    """返回该模式是否启用定缺门。"""
    return bool(get_mode(key).get("dingque", False))


def is_sichuan_family(key: str = DEFAULT_MODE) -> bool:
    """返回该玩法是否属于川麻血战血流家族（采用 sichuan_analyzer）。

    以 analyzer 字段为唯一判据：早期实现硬编码 key 元组，新增川麻玩法时只要忘记
    同步这里，engine 就会把川麻手牌交给 StdAnalyzer（牌集/定缺查叫全错），
    属于“能跑但算错”的静默故障。
    """
    key = ALIASES.get(key, key)
    return get_analyzer(key) == "sichuan"


def get_laizi(key: str = DEFAULT_MODE) -> Optional[int]:
    """返回该玩法的万能赖子牌 34 型索引（33 为 7z 红中，31 为 5z 白板）。

    注意：多赖子玩法下本函数可能返回 list，**不要拿它做 == 比较**；
    需要“这张牌是不是鬼牌”时用 get_laizi_set()。
    """
    return get_mode(key).get("laizi", None)


def get_laizi_set(key: str = DEFAULT_MODE) -> frozenset:
    """统一返回赖子索引集合（无赖子为空集），兼容单值与列表两种写法。

    engine 里“赖子绝不建议弃打”这类判断必须走这里：若直接拿 get_laizi() 与索引
    相等比较，多赖子玩法下会永远不成立，导致把鬼牌当孤张打出去（不报错但行为错）。
    """
    lz = get_laizi(key)
    if lz is None:
        return frozenset()
    items = lz if isinstance(lz, (list, tuple, set, frozenset)) else (lz,)
    return frozenset(int(i) for i in items if isinstance(i, int) and 0 <= int(i) < 34)


def native_solver_ready(key: str = DEFAULT_MODE) -> bool:
    """该玩法能否交给 C++ NativeEngine 求解（Java 侧接管路由的唯一判据）。

    NativeEngine 只实现了「27 型万筒条 + 定缺」，两点硬缺口：
      1) 完全没有鬼牌概念（sichuan_solver 里没有 laizi/wild 分支）；
      2) ImageProcessor.parseMpszToTiles 只映射 m/p/s，任何字牌（含 7z 红中赖子）
         会被静默丢弃后仍照常算向听，并且 native 结果会**覆盖** Python 的
         shanten/advice/hand —— 血流红中就会拿「少了 4 张红中的手牌」出推荐。
    因此这里要求 analyzer 是 sichuan **且** 牌集里没有鬼牌；不满足就退回 Python。
    这是能力判定，不是 key 前缀判定：Java 早先用 mode.startsWith("sc") 路由，而
    mode 来自被提前清空的 pendingMode，实际恒为兜底值 "sc"，等于给全部玩法开启
    native 接管（贵阳捉鸡、杭州百搭、推倒胡全被川麻口径覆盖）。
    """
    key = ALIASES.get(key, key)
    if get_analyzer(key) != "sichuan":
        return False
    return not get_laizi_set(key)


def is_known_mode(key: str) -> bool:
    """该 key 是否为已登记玩法（含别名）。

    get_mode() 对未知 key 静默回退 DEFAULT_MODE，好处是引擎不会因脏配置崩，
    代价是“选了一个不存在的花样、实际按另一个规则算”会无声无息。本函数供
    测试与写入前校验使用，把“静默降级”可发现化。
    """
    key = str(key or "").strip().lower()
    return ALIASES.get(key, key) in MODES


def get_analyzer(key: str = DEFAULT_MODE) -> str:
    """返回该玩法应使用的规则引擎标识：

    - "sichuan" 川麻家族（SichuanAnalyzer，28 型 + 定缺）
    - "std"     通用地方玩法（StdAnalyzer，34 型数据驱动：赖子/全刻/幺九/开口/七对/国士）
    - ""        历史 2p/3p 等无规则字段的兼容模式（走通用 Shanten 回退）
    """
    return str(get_mode(key).get("analyzer", "") or "")


def available_set(key: str = DEFAULT_MODE) -> Set[int]:
    """返回该玩法「可用牌」的 34 型索引集合。"""
    return set(get_mode(key)["available"])


def hand_sizes(key: str = DEFAULT_MODE) -> tuple:
    return get_mode(key)["hand_sizes"]


def mode_keys() -> List[str]:
    return list(MODES.keys())


_EXPLICIT_MODE: Optional[str] = None
_CONFIG_DIR: Optional[str] = None
# path 与 mtime 联合判缓存命中：候选路径列表可能因 set_config_dir 推送而重排，
# 只有「同一物理文件 + 未变更」才算命中，避免跨路径误共享 mtime。
_MODE_CACHE = {"path": "", "mtime": 0.0, "check_time": 0.0, "mode": DEFAULT_MODE}


def set_config_dir(path: str) -> None:
    """Java 原生层传入真实外部存储 files 目录绝对路径。"""
    global _CONFIG_DIR
    if path and isinstance(path, str):
        _CONFIG_DIR = path
        _MODE_CACHE["check_time"] = 0.0


def set_laizi_explicit(idx) -> bool:
    """注入本局鬼牌（34 型索引，或其列表）。空列表=本局无鬼，也是合法状态。

    只改内存态，不写磁盘：鬼牌只对当局有效。
    """
    global _LAIZI_EXPLICIT
    if idx is None:
        idx = []
    items = idx if isinstance(idx, (list, tuple, set, frozenset)) else [idx]
    clean: List[int] = []
    for i in items:
        try:
            v = int(i)
        except (TypeError, ValueError):
            return False   # 脏输入直接拒绝，不静默丢弃部分牌（避免“只注了一半鬼牌”）
        if not (0 <= v < 34):
            return False
        if v not in clean:
            clean.append(v)
    _LAIZI_EXPLICIT = clean
    return True


def get_laizi_explicit() -> List[int]:
    return list(_LAIZI_EXPLICIT)


def set_mode_explicit(key: str) -> bool:
    """显式设置当前玩法，优先级最高，直接绕过磁盘 IO。"""
    global _EXPLICIT_MODE
    if not key:
        return False
    key = str(key).strip().lower()
    norm = ALIASES.get(key, key)
    if norm in MODES:
        _EXPLICIT_MODE = norm
        _MODE_CACHE["mode"] = norm
        _MODE_CACHE["check_time"] = time.time()
        return True
    return False


def _get_candidate_paths(filename: str) -> List[str]:
    """收集可能的配置文件物理路径列表（兼容各类 Android 版本与虚拟机）。"""
    paths = []
    if _CONFIG_DIR:
        paths.append(os.path.join(_CONFIG_DIR, filename))
    # 尝试从 Chaquopy 获取真实 Android 应用上下文路径
    try:
        from com.chaquo.python import Python
        ctx = Python.getPlatform().getApplication()
        if ctx:
            ext = ctx.getExternalFilesDir(None)
            if ext:
                paths.append(os.path.join(str(ext.getAbsolutePath()), filename))
            int_f = ctx.getFilesDir()
            if int_f:
                paths.append(os.path.join(str(int_f.getAbsolutePath()), filename))
    except Exception:
        pass
    # 兜底硬编码路径
    paths.append(os.path.join("/storage/emulated/0/Android/data/com.example.auto_vision/files", filename))
    paths.append(os.path.join("/sdcard/Android/data/com.example.auto_vision/files", filename))
    paths.append(os.path.join("/data/data/com.example.auto_vision/files", filename))
    paths.append(filename)
    return paths


def load_mode() -> str:
    """从内存显式配置或共享文件读取当前玩法键，带内存与时间戳缓存防每帧磁盘 IO 阻塞。"""
    global _EXPLICIT_MODE

    now = time.time()
    if now - _MODE_CACHE["check_time"] < 0.2:
        return _EXPLICIT_MODE if _EXPLICIT_MODE is not None else _MODE_CACHE["mode"]
    _MODE_CACHE["check_time"] = now

    candidate_paths = _get_candidate_paths("mahjong_mode.json")
    for path in candidate_paths:
        try:
            if not os.path.exists(path):
                continue
            mtime = os.path.getmtime(path)
            if mtime != _MODE_CACHE["mtime"]:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                m = data.get("mode", DEFAULT_MODE)
                res = ALIASES.get(m, m) if m in MODES or m in ALIASES else DEFAULT_MODE
                _MODE_CACHE["path"] = path
                _MODE_CACHE["mtime"] = mtime
                _MODE_CACHE["mode"] = res
                _EXPLICIT_MODE = res
                return res
            elif path == _MODE_CACHE["path"]:
                return _EXPLICIT_MODE if _EXPLICIT_MODE is not None else _MODE_CACHE["mode"]
        except (OSError, ValueError, TypeError):
            continue

    return _EXPLICIT_MODE if _EXPLICIT_MODE is not None else _MODE_CACHE["mode"]


def save_mode(key: str) -> bool:
    """把玩法键写入共享文件，供 Python 引擎读取。"""
    if key not in MODES:
        return False
    candidate_paths = _get_candidate_paths("mahjong_mode.json")
    target_path = candidate_paths[0] if candidate_paths else MODE_PATH
    try:
        d = os.path.dirname(target_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump({"mode": key}, f)
        _MODE_CACHE["check_time"] = 0.0
        return True
    except OSError:
        return False


# ===== 出牌建议配置（调试页开关，与 mode 同目录 / 同机制）=====
# Dart 调试页经 MethodChannel 让 Java 写本文件，Python 引擎每帧读取。
# 与 MODE_PATH 保持同一个包名目录，否则会读不到而静默回退默认值。
ADVICE_PATH = (
    "/storage/emulated/0/Android/data/com.example.auto_vision"
    "/files/mahjong_advice.json"
)

# 默认：显示出牌建议，且不过滤进张数（0 表示不过滤）。
DEFAULT_SHOW_ADVICE = True
DEFAULT_MIN_UKEIRE = 0
# 危险牌预警：默认关闭。开启后引擎对每张候选弃牌附上基于「牌河」的
# 危险度（防点炮 / 防杠）。注意：当前牌河是**全桌合在一起**的一维计数，
# 没有按对手拆分、也没有副露（meld）数据，所以这是**粗略**启发式，
# 不是精确的对战读心。详情见 engine.build_advice 内的 _danger_* 注释。
DEFAULT_WARN_DEAL_IN = False
DEFAULT_WARN_PON_KONG = False
DEFAULT_MOOD_GUARD = True

_ADVICE_CACHE = {
    "path": "",
    "mtime": 0.0,
    "check_time": 0.0,
    "cfg": {
        "show_advice": DEFAULT_SHOW_ADVICE,
        "min_ukeire": DEFAULT_MIN_UKEIRE,
        "warn_deal_in": DEFAULT_WARN_DEAL_IN,
        "warn_pon_kong": DEFAULT_WARN_PON_KONG,
        "mood_guard": DEFAULT_MOOD_GUARD,
    },
}


def load_advice_config() -> Dict:
    """读取出牌建议配置，带时间戳缓存防每帧磁盘 IO 阻塞。

    字段：
    - show_advice  (bool) ：False 时 build_advice 返回空列表（不出建议）。
    - min_ukeire   (int)  ：>0 时只保留「进张数 >= 该阈值」的打法（调试页"好牌机率"）。
    - warn_deal_in (bool) ：开启后在建议里附「防点炮」危险度（生张/现物）。
    - warn_pon_kong(bool) ：开启后在建议里附「防杠/碰」危险度（基于牌河可见度的粗略信号）。
    - mood_guard   (bool) ：开启后根据起手向听与进张面实时研判顺逆风局势并安抚防上头。

    与 load_mode 同策略：文件缺失/损坏/字段类型不对时**静默回退默认值**，
    识别链路绝不因配置文件坏掉而抛异常或崩溃。
    """
    now = time.time()
    if now - _ADVICE_CACHE["check_time"] < 0.5:
        return dict(_ADVICE_CACHE["cfg"])
    _ADVICE_CACHE["check_time"] = now
    show = DEFAULT_SHOW_ADVICE
    minu = DEFAULT_MIN_UKEIRE
    wdi = DEFAULT_WARN_DEAL_IN
    wpk = DEFAULT_WARN_PON_KONG
    mg = DEFAULT_MOOD_GUARD

    candidate_paths = _get_candidate_paths("mahjong_advice.json")
    for path in candidate_paths:
        try:
            if not os.path.exists(path):
                continue
            mtime = os.path.getmtime(path)
            if path == _ADVICE_CACHE["path"] and mtime == _ADVICE_CACHE["mtime"]:
                return dict(_ADVICE_CACHE["cfg"])
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            v = data.get("show_advice", show)
            if isinstance(v, bool):
                show = v
            n = data.get("min_ukeire", minu)
            if isinstance(n, int) and not isinstance(n, bool):
                minu = n if n > 0 else DEFAULT_MIN_UKEIRE
            d = data.get("warn_deal_in", wdi)
            if isinstance(d, bool):
                wdi = d
            k = data.get("warn_pon_kong", wpk)
            if isinstance(k, bool):
                wpk = k
            m = data.get("mood_guard", mg)
            if isinstance(m, bool):
                mg = m
            cfg = {
                "show_advice": show,
                "min_ukeire": minu,
                "warn_deal_in": wdi,
                "warn_pon_kong": wpk,
                "mood_guard": mg,
            }
            _ADVICE_CACHE["path"] = path
            _ADVICE_CACHE["mtime"] = mtime
            _ADVICE_CACHE["cfg"] = cfg
            return dict(cfg)
        except (OSError, ValueError, TypeError, AttributeError):
            continue

    return dict(_ADVICE_CACHE["cfg"])
