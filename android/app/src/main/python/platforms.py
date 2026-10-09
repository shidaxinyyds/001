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
    "gd_queshen": {
        "key": "gd_queshen",
        "name": "广东雀神",
        "subtitle": "广东雀神专属画幅 · 鸡平胡/红中 · 智能裁切",
        "hand_roi": (0.70, 0.99, 0.00, 1.00),
        "river_zones": (
            ("bottom", 0.32, 0.52, 0.68, 0.72),
            ("top", 0.32, 0.16, 0.68, 0.36),
            ("left", 0.22, 0.30, 0.44, 0.64),
            ("right", 0.56, 0.30, 0.78, 0.64),
        ),
        "supported_modes": [
            "gd_hz", "std_tdh", "hz_bd"
        ],
        # 默认走**全牌**玩法，不再用 gd_hz（广东红中王）。实测依据（守卫
        # localtest/test_mode_gate_guard.py ④）：gd_hz 的牌集是 range(27)+[33]，
        # 字牌只有中(7z)，而本平台 15 帧素材里明摆着出现 東(1z)/西(3z)/發(6z)
        # （localtest/gt/shots_b1.json）。默认玩法就是分类器打分前的牌集闸门
        # （recognition/tencent_grid_detector.py::resolve_candidate_tiles），
        # 挂着 gd_hz 时这些字牌在识别**之前**就被挤出候选，面板必然少字牌——
        # 那正是「面板 84 / 网格 91」那 7 张差额的形状，不是识别退化。
        # hz_bd 也是 34 全牌，但它把白板(5z)当百搭且「每用一张番数翻倍」，
        # 用在广东雀神上等于把识别修好、把决策改错，故选无赖子的 std_tdh。
        # 真打红中王的用户仍可在面板里显式选 gd_hz（保留在 supported_modes）。
        "default_mode": "std_tdh",
    },
    "zj_sichuan": {
        "key": "zj_sichuan",
        "name": "指尖四川",
        "subtitle": "指尖经典画幅 · 血流红中连胡 · 底端精密对齐",
        "hand_roi": (0.69, 0.99, 0.01, 0.99),
        "river_zones": (
            ("bottom", 0.31, 0.51, 0.69, 0.71),
            ("top", 0.31, 0.16, 0.69, 0.36),
            ("left", 0.21, 0.29, 0.43, 0.63),
            ("right", 0.57, 0.29, 0.79, 0.63),
        ),
        "supported_modes": [
            "sc_hz", "sc_xz", "sc_xl"
        ],
        "default_mode": "sc_hz",
    },
    "shushan": {
        "key": "shushan",
        "name": "蜀山四川麻将",
        "subtitle": "蜀山宽屏画幅 · 红中血流八换三 · 专属模板库匹配",
        "hand_roi": (0.68, 0.99, 0.01, 0.99),
        "river_zones": (
            ("bottom", 0.30, 0.50, 0.70, 0.70),
            ("top", 0.30, 0.15, 0.70, 0.35),
            ("left", 0.20, 0.28, 0.42, 0.62),
            ("right", 0.58, 0.28, 0.80, 0.62),
        ),
        "supported_modes": [
            "sc_hz", "sc_xz", "sc_xl"
        ],
        "default_mode": "sc_hz",
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
            "wh_kk", "db_qh", "hz_bd", "gd_hz", "cs_zz",
            "wz_tdh", "hz_all", "fc_all", "zfb_bd", "pp_zz",
            "mj_2p", "mj_3p", "hz_ne", "sc_xz_3p",
        ],
        "default_mode": "sc_hz",
    },
}

_EXPLICIT_PLATFORM: Optional[str] = None
# 显式推送（Java 经 Chaquopy 推入，或本地探针调 `Engine.set_platform`）的时刻。
# 它只用于一个判据：**比这份推送旧的磁盘文件是上一轮残留，不许盖掉刚推进来的值**
#（下面 `load_platform` 里那条）。靠文件自身赋值时它保持 0，磁盘优先的旧口径不变。
_EXPLICIT_PLATFORM_AT = 0.0
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
            if not config_read_allowed(path):
                continue
            if not os.path.exists(path):
                continue
            mtime = os.path.getmtime(path)
            if _EXPLICIT_PLATFORM is not None and mtime <= _EXPLICIT_PLATFORM_AT:
                # 磁盘上这份比显式推送还旧 = 上一轮别的平台留下的残留（实测：仓库外
                # 的 `mahjong_platform.json` 写着 gd_queshen，把腾讯口径的 37 帧基线
                # 静默改成另一个平台的输出）。让推送赢，并把这一份记进缓存，
                # 免得下一帧又再跑一次同样的磁盘扫描。
                _PLATFORM_CACHE["path"] = path
                _PLATFORM_CACHE["mtime"] = mtime
                return _EXPLICIT_PLATFORM
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


def _chaquo_application():
    """Android 应用上下文；非设备环境（本地跑评测/探针）返回 None。"""
    try:
        from com.chaquo.python import Python
        return Python.getPlatform().getApplication()
    except Exception:
        return None


def config_write_allowed(path: str) -> bool:
    """这份配置目标路径允不允许落盘（`save_platform`/`save_mode` 共用这一条闸）。

    真机上候选第一条来自 Chaquopy 上下文（`getExternalFilesDir` 已经把目录建好），
    所以恒真，Java/UI 依旧能从文件读到引擎在用的平台/玩法，行为零变化。

    桌面上必须拦住：本地没有 Chaquopy，候选会退到硬编码的
    `/storage/emulated/0/...`，而 Windows 把以 `/` 开头的路径解析成**当前盘根**
    ——实测探针里一次 `Engine().set_platform(...)`（它走
    `set_platform_explicit`→`save_platform`）就会在仓库之外造出
    `D:\storage\emulated\0\Android\data\com.example.auto_vision\files\mahjong_platform.json`。
    那是一份**全局、跨进程、看不见**的配置：下一轮 `eval_base`（本该是腾讯口径的
    37 帧基线）会静默读到上一支探针留下的「广东雀神」，于是定缺/选牌阶段全部
    变成另一个玩法的输出——两边报的数都不再是它们声称测的东西（2026-10 实测：
    工作树 26 条不匹配 vs 基线工作树 2 条，差的全是阶段，而根因就是这份文件）。

    口径：**写不进去的路径也不该去读**（两条闸共用 `config_read_allowed`），所以
    设备环境照旧写；桌面上 Android 绝对路径直接否掉，其余只写**已经存在**的
    目录（临时配置目录 `set_config_dir(tmp)` 这类有意测试读写链路的场景仍然能跑），
    绝不新建仓库外的目录。内存态（`_EXPLICIT_PLATFORM`）不受影响，本地探针
    拿到的行为一致。
    """
    if not config_read_allowed(path):
        return False
    if _chaquo_application() is not None:
        return True
    d = os.path.dirname(path)
    return not d or os.path.isdir(d)


def config_read_allowed(path: str) -> bool:
    """这条候选配置路径允不允许被当成「用户在面板上选的那个平台」来读。

    只拦一种情况：Windows 上把以 `/` 开头的 Android 绝对路径解析到**当前盘根**。
    `D:\storage\emulated\0\...` 那里只可能是本仓库探针自己 `os.makedirs` 留下的
    残留（写侧已由 `config_write_allowed` 拦住），永远不可能是真机上的用户配置；
    让它参与读取，等于让每一轮本地评测的口径取决于「上一支探针最后设了谁」。

    真机（Android/Linux，`os.name == 'posix'`）逐字不受影响：那条判据直接为真，
    即使 Chaquopy 上下文一时拿不到也照旧能读到硬编码的兜底路径。
    """
    if os.name != "nt":
        return True
    return not str(path).startswith(("/", "\\"))


def save_platform(key: str) -> bool:
    """将平台配置写入共享 JSON 文件。"""
    if key not in PLATFORMS:
        return False
    candidate_paths = _candidate_platform_paths()
    target_path = candidate_paths[0] if candidate_paths else PLATFORM_PATH
    if not config_write_allowed(target_path):
        return False
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
    global _EXPLICIT_PLATFORM, _EXPLICIT_PLATFORM_AT
    k = str(key).strip().lower()
    if k in PLATFORMS:
        _EXPLICIT_PLATFORM = k
        # 记下推送时刻：早于它的磁盘文件从此不再能反过来盖掉这个值。
        _EXPLICIT_PLATFORM_AT = time.time()
        _PLATFORM_CACHE["platform"] = k
        _PLATFORM_CACHE["check_time"] = time.time()
        save_platform(k)
        return True
    return False

