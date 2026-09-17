"""二麻 / 三麻 / 四麻 玩法配置与文件共享态。

三种玩法的差别只在「可用牌集」与「人数 / 手牌张数」：

- 四麻（4p）：国标 / 四川 / 广东 / 日麻 / 台麻 / 雀魂 / 腾讯欢乐麻将。
  34 种牌全用（1-9m / 1-9p / 1-9s / 东南西北白發中），每人 13 张（摸完 14）。
- 三麻（3p）：日式三麻 sanma 标准。去掉 2m 8m 2p 8p 2s 8s 与白(5z)，
  剩 27 种，每种 4 张共 108 张。座风只用 东南西。
- 二麻（2p）：二人麻雀常用变体。只保留 万子 1-9m 与 字牌 东南西北白發中
  （共 16 种），去掉全部筒/条。牌墙 64 张。

说明：二/三麻的具体规则在各 App 间并不统一，这里取「最常见」的一套定义，
全部以**数据**形式写在 MODES 里，改动规则只需改这个字典，逻辑层无需动。

玩法切换的跨层通路：悬浮窗(Dart)把选中玩法写入本文件指向的 JSON，
Python 引擎每帧读取（文件极小，开销可忽略）。路径与 Dart 端保持一致。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Set

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

MODES: Dict[str, Dict] = {
    # 1. 川麻血流系列 (占手游 60%+ 流量)
    "sc_hz": {
        "name": "血流红中",
        "players": 4,
        "available": list(range(27)) + [33],  # 0-26 万筒条各9张 + 33 (7z 红中)
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 112,
        "dingque": True,
        "laizi": 33,  # 7z 红中
    },
    "sc_xz": {
        "name": "川麻·血战到底",
        "players": 4,
        "available": list(range(27)),  # 0-26 纯万筒条108张
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": True,
        "laizi": None,
    },
    "sc_xl": {
        "name": "川麻·血流成河",
        "players": 4,
        "available": list(range(27)),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": True,
        "laizi": None,
    },
    "gy_zj": {
        "name": "贵阳捉鸡",
        "players": 4,
        "available": list(range(27)),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": True,
        "laizi": None,
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
    },
    "wh_kk": {
        "name": "武汉开口翻",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": None,
    },
    "db_qh": {
        "name": "东北穷胡",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": None,
    },
    "hz_bd": {
        "name": "杭州百搭",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 136,
        "dingque": False,
        "laizi": 31,  # 5z 白板
    },

    # 3. 地方顶流系列
    "gd_hz": {
        "name": "广东红中王",
        "players": 4,
        "available": list(range(27)) + [33],
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 100,
        "dingque": False,
        "laizi": 33,  # 7z 红中
    },
    "cs_zz": {
        "name": "长沙转转麻将",
        "players": 4,
        "available": list(range(27)) + [33],
        "hand_sizes": (14, 13, 12, 11, 10, 8, 7, 5, 4, 2, 1),
        "wall": 108,
        "dingque": False,
        "laizi": 33,  # 7z 红中
    },

    # 向下兼容历史别名
    "sc": {
        "name": "川麻·血战到底",
        "players": 4,
        "available": list(range(27)) + [33],
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
        "name": "二人雀神",
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
    "4p": "std_tdh",
}


def get_mode(key: str = DEFAULT_MODE) -> Dict:
    """返回玩法配置 dict（含 name/players/available/hand_sizes/wall/dingque/laizi）。"""
    key = ALIASES.get(key, key)
    return MODES.get(key, MODES[DEFAULT_MODE])


def is_dingque_mode(key: str = DEFAULT_MODE) -> bool:
    """返回该模式是否启用定缺门。"""
    return bool(get_mode(key).get("dingque", False))


def is_sichuan_family(key: str = DEFAULT_MODE) -> bool:
    """返回该模式是否属于川麻血战血流家族（采用 sichuan_analyzer）。"""
    key = ALIASES.get(key, key)
    return key in ("sc_hz", "sc_xz", "sc_xl", "gy_zj", "sc")


def get_laizi(key: str = DEFAULT_MODE) -> Optional[int]:
    """返回该模式的万能赖子牌 34 型索引（33 为 7z 红中，31 为 5z 白板），None 表示无赖子。"""
    return get_mode(key).get("laizi", None)


def available_set(key: str = DEFAULT_MODE) -> Set[int]:
    """返回该玩法「可用牌」的 34 型索引集合。"""
    return set(get_mode(key)["available"])


def hand_sizes(key: str = DEFAULT_MODE) -> tuple:
    return get_mode(key)["hand_sizes"]


def mode_keys() -> List[str]:
    return list(MODES.keys())


def load_mode() -> str:
    """从共享文件读取当前玩法键，文件不存在或损坏时回退默认 sc_hz。"""
    try:
        with open(MODE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        m = data.get("mode", DEFAULT_MODE)
        if m in ALIASES:
            return ALIASES[m]
        if m in MODES:
            return m
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_MODE


def save_mode(key: str) -> bool:
    """把玩法键写入共享文件，供 Python 引擎读取。"""
    if key not in MODES:
        return False
    try:
        d = os.path.dirname(MODE_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(MODE_PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": key}, f)
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


def load_advice_config() -> Dict:
    """读取出牌建议配置。

    字段：
    - show_advice  (bool) ：False 时 build_advice 返回空列表（不出建议）。
    - min_ukeire   (int)  ：>0 时只保留「进张数 >= 该阈值」的打法（调试页"好牌机率"）。
    - warn_deal_in (bool) ：开启后在建议里附「防点炮」危险度（生张/现物）。
    - warn_pon_kong(bool) ：开启后在建议里附「防杠/碰」危险度（基于牌河可见度的粗略信号）。

    与 load_mode 同策略：文件缺失/损坏/字段类型不对时**静默回退默认值**，
    识别链路绝不因配置文件坏掉而抛异常或崩溃。
    """
    show = DEFAULT_SHOW_ADVICE
    minu = DEFAULT_MIN_UKEIRE
    wdi = DEFAULT_WARN_DEAL_IN
    wpk = DEFAULT_WARN_PON_KONG
    try:
        with open(ADVICE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = data.get("show_advice", show)
        if isinstance(v, bool):
            show = v
        n = data.get("min_ukeire", minu)
        # bool 是 int 的子类，这里必须显式排除，避免把 True 当成 1。
        if isinstance(n, int) and not isinstance(n, bool):
            minu = n if n > 0 else DEFAULT_MIN_UKEIRE
        d = data.get("warn_deal_in", wdi)
        if isinstance(d, bool):
            wdi = d
        k = data.get("warn_pon_kong", wpk)
        if isinstance(k, bool):
            wpk = k
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {
        "show_advice": show,
        "min_ukeire": minu,
        "warn_deal_in": wdi,
        "warn_pon_kong": wpk,
    }
