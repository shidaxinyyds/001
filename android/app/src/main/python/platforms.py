# -*- coding: utf-8 -*-
"""游戏平台预设与多端画幅自适应管理模块。

支持主流麻将平台（腾讯欢乐麻将、途游麻将、微乐麻将、JJ比赛麻将、通用平台）的
专属视觉截屏 ROI 裁切校准、牌河几何分区、以及平台与玩法双级联动映射。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

PLATFORM_PATH = "/storage/emulated/0/Android/data/com.example.auto_vision/files/mahjong_platform.json"
DEFAULT_PLATFORM = "tencent"

PLATFORMS: Dict[str, Dict] = {
    "tencent": {
        "key": "tencent",
        "name": "腾讯欢乐麻将",
        "subtitle": "官方标准画幅 · 手牌占底 30% · 经典四方对齐",
        # 屏幕相对坐标 (top, bottom, left, right)
        "hand_roi": (0.70, 1.00, 0.00, 1.00),
        "river_zones": (
            ("bottom", 0.32, 0.52, 0.68, 0.72),
            ("top", 0.32, 0.16, 0.68, 0.36),
            ("left", 0.22, 0.30, 0.44, 0.64),
            ("right", 0.56, 0.30, 0.78, 0.64),
        ),
        "supported_modes": [
            "sc_hz", "sc_xz", "sc_xl", "std_tdh", "gd_hz", "wh_kk"
        ],
        "default_mode": "sc_hz",
    },
    "tuyou": {
        "key": "tuyou",
        "name": "途游四川麻将",
        "subtitle": "紧凑手牌排版 · 预设上移 2% · 宽阔牌河",
        "hand_roi": (0.68, 0.98, 0.02, 0.98),
        "river_zones": (
            ("bottom", 0.30, 0.50, 0.70, 0.70),
            ("top", 0.30, 0.15, 0.70, 0.35),
            ("left", 0.20, 0.28, 0.42, 0.62),
            ("right", 0.58, 0.28, 0.80, 0.62),
        ),
        "supported_modes": [
            "sc_hz", "sc_xz", "cs_zz", "db_qh"
        ],
        "default_mode": "sc_hz",
    },
    "weile": {
        "key": "weile",
        "name": "微乐地方麻将",
        "subtitle": "微乐经典宽屏 · 地方特色牌面 · 精密节距对齐",
        "hand_roi": (0.69, 0.98, 0.01, 0.99),
        "river_zones": (
            ("bottom", 0.31, 0.51, 0.69, 0.71),
            ("top", 0.31, 0.16, 0.69, 0.36),
            ("left", 0.21, 0.29, 0.43, 0.63),
            ("right", 0.57, 0.29, 0.79, 0.63),
        ),
        "supported_modes": [
            "sc_xz", "cs_zz", "db_qh", "std_tdh", "hz_bd"
        ],
        "default_mode": "sc_xz",
    },
    "jj": {
        "key": "jj",
        "name": "JJ比赛麻将",
        "subtitle": "专业竞技大厅 · 高对比牌桌 · 紧凑出牌排版",
        "hand_roi": (0.71, 0.99, 0.00, 1.00),
        "river_zones": (
            ("bottom", 0.33, 0.53, 0.67, 0.71),
            ("top", 0.33, 0.17, 0.67, 0.35),
            ("left", 0.23, 0.31, 0.43, 0.63),
            ("right", 0.57, 0.31, 0.77, 0.63),
        ),
        "supported_modes": [
            "std_tdh", "sc_xz", "sc_hz"
        ],
        "default_mode": "std_tdh",
    },
    "generic": {
        "key": "generic",
        "name": "通用平台自适应",
        "subtitle": "HSV 掩码全局动态寻界 · 兼容所有平台与变体",
        "hand_roi": (0.70, 1.00, 0.00, 1.00),
        "river_zones": (
            ("bottom", 0.32, 0.52, 0.68, 0.72),
            ("top", 0.32, 0.16, 0.68, 0.36),
            ("left", 0.22, 0.30, 0.44, 0.64),
            ("right", 0.56, 0.30, 0.78, 0.64),
        ),
        "supported_modes": [
            "sc_hz", "sc_xz", "sc_xl", "gy_zj", "std_tdh",
            "wh_kk", "db_qh", "hz_bd", "gd_hz", "cs_zz"
        ],
        "default_mode": "sc_hz",
    },
}

_EXPLICIT_PLATFORM: Optional[str] = None
_PLATFORM_CACHE = {"path": "", "mtime": 0.0, "check_time": 0.0, "platform": DEFAULT_PLATFORM}


def get_platform(key: str = DEFAULT_PLATFORM) -> Dict:
    """返回游戏平台配置字典。"""
    key = str(key).strip().lower()
    return PLATFORMS.get(key, PLATFORMS[DEFAULT_PLATFORM])


def get_hand_roi(key: str = DEFAULT_PLATFORM) -> Tuple[float, float, float, float]:
    """返回 (top, bottom, left, right) 相对坐标裁切比例。"""
    return get_platform(key).get("hand_roi", PLATFORMS[DEFAULT_PLATFORM]["hand_roi"])


def get_river_zones(key: str = DEFAULT_PLATFORM) -> Tuple:
    """返回四方牌河几何条带定义。"""
    return get_platform(key).get("river_zones", PLATFORMS[DEFAULT_PLATFORM]["river_zones"])


def get_supported_modes(key: str = DEFAULT_PLATFORM) -> List[str]:
    """返回该平台推荐的玩法 key 列表。"""
    return get_platform(key).get("supported_modes", PLATFORMS[DEFAULT_PLATFORM]["supported_modes"])


def _candidate_platform_paths() -> List[str]:
    paths = []
    # 动态获取 Android 应用外部 files 目录
    try:
        from com.chaquo.python import Python
        ctx = Python.getPlatform().getApplication()
        if ctx:
            ext = ctx.getExternalFilesDir(None)
            if ext:
                paths.append(os.path.join(str(ext.getAbsolutePath()), "mahjong_platform.json"))
            int_f = ctx.getFilesDir()
            if int_f:
                paths.append(os.path.join(str(int_f.getAbsolutePath()), "mahjong_platform.json"))
    except Exception:
        pass
    paths.append(PLATFORM_PATH)
    paths.append(os.path.join("/sdcard/Android/data/com.example.auto_vision/files", "mahjong_platform.json"))
    paths.append("mahjong_platform.json")
    return paths


def load_platform() -> str:
    """从本地文件或内存缓存加载当前选择的平台，带 200ms 防抖缓存。"""
    global _EXPLICIT_PLATFORM

    now = time.time()
    if now - _PLATFORM_CACHE["check_time"] < 0.2:
        return _EXPLICIT_PLATFORM if _EXPLICIT_PLATFORM is not None else _PLATFORM_CACHE["platform"]
    _PLATFORM_CACHE["check_time"] = now

    for path in _candidate_platform_paths():
        try:
            if not os.path.exists(path):
                continue
            mtime = os.path.getmtime(path)
            if mtime != _PLATFORM_CACHE["mtime"]:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                p = data.get("platform", DEFAULT_PLATFORM)
                res = p if p in PLATFORMS else DEFAULT_PLATFORM
                _PLATFORM_CACHE["path"] = path
                _PLATFORM_CACHE["mtime"] = mtime
                _PLATFORM_CACHE["platform"] = res
                _EXPLICIT_PLATFORM = res
                return res
            elif path == _PLATFORM_CACHE["path"]:
                return _EXPLICIT_PLATFORM if _EXPLICIT_PLATFORM is not None else _PLATFORM_CACHE["platform"]
        except Exception:
            continue

    return _EXPLICIT_PLATFORM if _EXPLICIT_PLATFORM is not None else _PLATFORM_CACHE["platform"]


def save_platform(key: str) -> bool:
    """将平台配置写入共享 JSON 文件。"""
    if key not in PLATFORMS:
        return False
    candidate_paths = _candidate_platform_paths()
    target_path = candidate_paths[0] if candidate_paths else PLATFORM_PATH
    try:
        d = os.path.dirname(target_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump({"platform": key}, f)
        _PLATFORM_CACHE["check_time"] = 0.0
        return True
    except OSError:
        return False


def set_platform_explicit(key: str) -> bool:
    """由 Java/UI 直接推入平台，绕过文件轮询即时生效。"""
    global _EXPLICIT_PLATFORM
    k = str(key).strip().lower()
    if k in PLATFORMS:
        _EXPLICIT_PLATFORM = k
        _PLATFORM_CACHE["platform"] = k
        _PLATFORM_CACHE["check_time"] = time.time()
        save_platform(k)
        return True
    return False

