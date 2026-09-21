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
    if _EXPLICIT_MODE is not None:
        return _EXPLICIT_MODE

    now = time.time()
    if now - _MODE_CACHE["check_time"] < 0.5:
        return _MODE_CACHE["mode"]
    _MODE_CACHE["check_time"] = now

    candidate_paths = _get_candidate_paths("mahjong_mode.json")
    for path in candidate_paths:
        try:
            if not os.path.exists(path):
                continue
            mtime = os.path.getmtime(path)
            if path == _MODE_CACHE["path"] and mtime == _MODE_CACHE["mtime"]:
                return _MODE_CACHE["mode"]
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            m = data.get("mode", DEFAULT_MODE)
            res = ALIASES.get(m, m) if m in MODES or m in ALIASES else DEFAULT_MODE
            _MODE_CACHE["path"] = path
            _MODE_CACHE["mtime"] = mtime
            _MODE_CACHE["mode"] = res
            return res
        except (OSError, ValueError, TypeError):
            continue

    return _MODE_CACHE["mode"]


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

_ADVICE_CACHE = {
    "path": "",
    "mtime": 0.0,
    "check_time": 0.0,
    "cfg": {
        "show_advice": DEFAULT_SHOW_ADVICE,
        "min_ukeire": DEFAULT_MIN_UKEIRE,
        "warn_deal_in": DEFAULT_WARN_DEAL_IN,
        "warn_pon_kong": DEFAULT_WARN_PON_KONG,
    },
}


def load_advice_config() -> Dict:
    """读取出牌建议配置，带时间戳缓存防每帧磁盘 IO 阻塞。

    字段：
    - show_advice  (bool) ：False 时 build_advice 返回空列表（不出建议）。
    - min_ukeire   (int)  ：>0 时只保留「进张数 >= 该阈值」的打法（调试页"好牌机率"）。
    - warn_deal_in (bool) ：开启后在建议里附「防点炮」危险度（生张/现物）。
    - warn_pon_kong(bool) ：开启后在建议里附「防杠/碰」危险度（基于牌河可见度的粗略信号）。

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
            cfg = {
                "show_advice": show,
                "min_ukeire": minu,
                "warn_deal_in": wdi,
                "warn_pon_kong": wpk,
            }
            _ADVICE_CACHE["path"] = path
            _ADVICE_CACHE["mtime"] = mtime
            _ADVICE_CACHE["cfg"] = cfg
            return dict(cfg)
        except (OSError, ValueError, TypeError, AttributeError):
            continue

    return dict(_ADVICE_CACHE["cfg"])
