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

DEFAULT_MODE = "4p"

# 34 型索引约定（与 trainer/utils/convert.py 相同）：
#   0-8   1m..9m
#   9-17  1p..9p
#   18-26 1s..9s
#   27-33 1z..7z（东南西北白發中）
ALL_34 = list(range(34))


def _removed_to_available(removed: List[int]) -> List[int]:
    return [i for i in ALL_34 if i not in set(removed)]


# 三麻：去 2m(1) 8m(7) 2p(10) 8p(16) 2s(19) 8s(25) 白(31)
_SANMA_REMOVED = [1, 7, 10, 16, 19, 25, 31]

# 二麻：去全部筒(9-17)与条(18-26)，仅留万(0-8)与字牌(27-33)
_TWOP_REMOVED = list(range(9, 27))

MODES: Dict[str, Dict] = {
    "4p": {
        "name": "四麻",
        "players": 4,
        "available": list(ALL_34),
        "hand_sizes": (13, 14),
        "wall": 136,
    },
    "3p": {
        "name": "三麻",
        "players": 3,
        "available": _removed_to_available(_SANMA_REMOVED),
        "hand_sizes": (13, 14),
        "wall": 108,
    },
    "2p": {
        "name": "二麻",
        "players": 2,
        "available": _removed_to_available(_TWOP_REMOVED),
        "hand_sizes": (13, 14),
        "wall": 64,
    },
}


def get_mode(key: str = DEFAULT_MODE) -> Dict:
    """返回玩法配置 dict（含 name/players/available/hand_sizes/wall）。"""
    return MODES.get(key, MODES[DEFAULT_MODE])


def available_set(key: str = DEFAULT_MODE) -> Set[int]:
    """返回该玩法「可用牌」的 34 型索引集合。"""
    return set(get_mode(key)["available"])


def hand_sizes(key: str = DEFAULT_MODE) -> tuple:
    return get_mode(key)["hand_sizes"]


def mode_keys() -> List[str]:
    return list(MODES.keys())


def load_mode() -> str:
    """从共享文件读取当前玩法键，文件不存在或损坏时回退默认 4p。"""
    try:
        with open(MODE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        m = data.get("mode", DEFAULT_MODE)
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


def load_advice_config() -> Dict:
    """读取出牌建议配置 {"show_advice": bool, "min_ukeire": int}。

    与 load_mode 同策略：文件缺失/损坏/字段类型不对时**静默回退默认值**，
    识别链路绝不因配置文件坏掉而抛异常或崩溃。

    - show_advice：False 时 build_advice 返回空列表（不出建议）。
    - min_ukeire ：>0 时只保留「进张数 >= 该阈值」的打法（调试页"好牌机率"）。
    """
    show = DEFAULT_SHOW_ADVICE
    minu = DEFAULT_MIN_UKEIRE
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
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {"show_advice": show, "min_ukeire": minu}
