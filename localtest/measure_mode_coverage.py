# -*- coding: utf-8 -*-
"""量一件事：每个平台的模板 bank 里**真的有哪些牌面**，以及哪些玩法的牌集能被它覆盖。

为什么要量：`platforms.py` 里每条平台的 `supported_modes` 是手写的「这个平台大概开了
哪些房」，从来不是从素材里数出来的。P2-C 却拿它当硬闸门用了两处（UI 只显示列表里的
玩法 + 引擎把列表外的玩法纠正掉），于是用户看到的就是「玩法怎么只剩这几种」。
要判断该放开多少，只能先量事实：玩法牌集是**分类之前**的闸门，某个玩法在这家平台
到底读不读得出来，取决于这家平台的 bank 里有没有那些牌面的字模 —— 这是可以数的。

口径：
  · 平台可用风格 = 主 bank（风格名固定 tencent）+ EXTRA_BANKS 中「白名单含本平台」
    或「风格名==本平台」的那些，再扣掉 STYLE_PLATFORM_DENYLIST 的定点减法；
  · 某玩法可被覆盖 = 它的 `available` 牌集里，每一面在这个平台的 bank 中至少有 1 枚字模；
  · 牌面 index 口径同 modes.ALL_34：0-8 万、9-17 筒、18-26 条、27-33 东南西北中发白。

运行：py -3.10 -X utf8 localtest/measure_mode_coverage.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import importlib

import modes as M
import platforms as P
import recognition.tencent_grid_detector as D

# 34 面 ↔ mpsz 码（与 modes 的 index 口径对齐）
CODES = [f"{n}m" for n in range(1, 10)] + [f"{n}p" for n in range(1, 10)] \
    + [f"{n}s" for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)]


def bank_faces(style: str) -> set:
    """某个风格 bank 里出现过的牌面下标集合。"""
    mod = importlib.import_module(f"recognition.templates_{style}")
    out = set()
    for key in mod.TEMPLATES_BGR:
        base = key.split("#")[0]
        if base in CODES:
            out.add(CODES.index(base))
    return out


def platform_styles(platform: str) -> set:
    """按生产同款规则算出该平台会扫哪些风格。"""
    allowed = {"tencent"}                      # 主 bank 的风格名固定
    for _mod_name, style in D.EXTRA_BANKS:
        owners = D.STYLE_PLATFORM_WHITELIST.get(style)
        if owners is None or platform in owners:
            allowed.add(style)
    for style, denied in D.STYLE_PLATFORM_DENYLIST.items():
        if platform in denied and "tencent" not in denied:
            allowed.discard(style)
    return allowed


def platform_faces(platform: str) -> set:
    faces = set()
    for style in sorted(platform_styles(platform)):
        try:
            faces |= bank_faces(style)
        except ModuleNotFoundError:
            pass                                # 主 bank 不在 templates_<style> 里
    return faces


def main() -> int:
    # 主 bank（templates_data）单独取一次
    td = importlib.import_module("recognition.templates_data")
    base_labels = set()
    for key in getattr(td, "TEMPLATES", getattr(td, "TEMPLATES_BGR", {})):
        b = key.split("#")[0]
        if b in CODES:
            base_labels.add(CODES.index(b))

    print(f"主 bank（手绘模板）覆盖 {len(base_labels)}/34 面")
    for _m, style in D.EXTRA_BANKS:
        try:
            f = bank_faces(style)
            print(f"  bank {style:<9} 覆盖 {len(f):>2}/34 面  "
                  f"缺：{''.join(CODES[i][:2] for i in range(34) if i not in f) or '无'}")
        except ModuleNotFoundError:
            print(f"  bank {style:<9} （模块不存在）")

    bad = 0
    for key in sorted(P.PLATFORMS):
        faces = base_labels | platform_faces(key)
        sup = P.get_supported_modes(key)
        cover = [mk for mk, mv in sorted(M.MODES.items())
                 if mv.get("available") and set(mv["available"]) <= faces]
        miss = [mk for mk in sup if mk not in cover]
        print(f"\n[{key}] declared={len(sup)}  可覆盖={len(cover)}  "
              f"声明了却读不出={miss or '无'}")
        print("  可覆盖：", " ".join(cover))
        if miss:
            bad += 1
    print(f"\n有 {bad} 个平台声明的玩法里存在「bank 根本没有那些字面」的项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
