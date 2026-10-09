# -*- coding: utf-8 -*-
"""分类核普查：总核数、按风格分布、腾讯帧实际参与 matchTemplate 的核数。

目的：把「每片 253 核」拆开看是谁贡献的，找出对当前平台/候选集根本不可能
中标的冗余核（它们只贡献耗时，不贡献分数）。
"""
import os
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import TencentGridDetector  # noqa
from recognition.tencent_grid_detector import resolve_candidate_tiles  # noqa

d = TencentGridDetector()
cores = d._cores
print("is_available:", d.is_available)
print("总核数:", len(cores))
print("按风格:", Counter(s for _l, s, *_r in cores).most_common())
print("本家(tencent)核数:", sum(1 for _l, s, *_r in cores if s == "tencent"))
print("主库/手绘风格名:", [s for s in set(_l and _l or "" for _l, _s, *_r in cores)][:0])
print("风格全集:", sorted({s for _l, s, *_r in cores}))

# 平台声明后的白名单
print("构造时 active_styles:", d.active_styles)
import modes  # noqa
avail = modes.available_set("sc_hz")
for pk in ("tencent", "shushan", "zj_sichuan", "tuyou", "weile", "jj", "gd_queshen"):
    d2 = TencentGridDetector()
    d2.set_platform_styles(pk)
    allowed = d2.active_styles
    vt2 = resolve_candidate_tiles(avail, getattr(d2, "_mode_tiles", None), d2.full_honors)
    n = sum(1 for lbl, style, *_r in d2._cores
            if lbl in vt2 and (allowed is None or style in allowed))
    print(f"平台 {pk:12s} active_styles={sorted(allowed) if allowed else None} "
          f"每片参与匹配的核={n}")
d.set_platform_styles("tencent")
allowed = d.active_styles
print("set_platform_styles(tencent) 后 active_styles:", allowed)

# 川麻血战换三张的候选集
vt = resolve_candidate_tiles(avail, getattr(d, "_mode_tiles", None), d.full_honors)
print("候选标签数:", len(vt))

surv = Counter()
for lbl, style, *_r in cores:
    if lbl in vt and (allowed is None or style in allowed):
        surv[style] += 1
print("腾讯+川麻过滤后参与匹配的核:", sum(surv.values()), dict(surv))

# 每个标签有几个风格提供核（重复覆盖 = 冗余）
per_lbl = Counter()
for lbl, style, *_r in cores:
    if lbl in vt and (allowed is None or style in allowed):
        per_lbl[lbl] += 1
print("单标签最多提供者:", per_lbl.most_common(8))
print("标签数(过滤后):", len(per_lbl))
