# -*- coding: utf-8 -*-
"""平台注册表自检：确认 haling/xianlai 已移除，且剩余平台未被误伤。

移除平台时最怕的不是删不干净，而是删过头——顺手改坏了邻近平台的 dict 结构、
或让某处仍引用旧 key。所以这里除了反向断言「不存在」，还对现存全表做结构校验。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python")))

from platforms import (PLATFORMS, DEFAULT_PLATFORM, get_hand_roi, get_river_zones,
                       get_supported_modes, load_platform, set_platform_explicit)
from recognition.tencent_grid_detector import TencentGridDetector

REMOVED = ("haling", "xianlai")
lines = []
fails = []

lines.append("A) 确认已移除")
for k in REMOVED:
    in_table = k in PLATFORMS
    rc = set_platform_explicit(k)          # 必须被拒绝
    after = load_platform()                # 且不得改动当前生效平台
    ok = (not in_table) and (rc is False)
    lines.append(f"   {k:<8} 在表={in_table} 切换返回={rc} 切换后 load_platform()={after}")
    if in_table:
        fails.append(f"{k} 仍存在于 PLATFORMS")
    if rc is not False:
        fails.append(f"{k} 竟被接受切换")
    if after != "tencent":
        fails.append(f"{k} 的失败切换污染了当前平台（={after}）")

lines.append("")
lines.append("B) 现存平台未被误伤（逐个校结构）")
for k, p in PLATFORMS.items():
    roi = get_hand_roi(k)
    zones = get_river_zones(k)
    modes = get_supported_modes(k)
    bad = []
    if p.get("key") != k:
        bad.append("key 字段与表键不一致")
    if len(roi) != 4 or not (roi[0] < roi[1] and roi[2] < roi[3]):
        bad.append(f"roi 非法 {roi}")
    if len(zones) != 4:
        bad.append(f"牌河区数 {len(zones)}")
    if not modes or p.get("default_mode") not in modes:
        bad.append(f"玩法缺失或不包含 default：{modes}/{p.get('default_mode')}")
    lines.append(f"   {k:<12} roi={roi} 玩法{modes} {'OK' if not bad else '× ' + '; '.join(bad)}")
    if bad:
        fails.append(f"{k}: {'; '.join(bad)}")
lines.append(f"   平台总数={len(PLATFORMS)} 默认={DEFAULT_PLATFORM}")

lines.append("")
lines.append("C) 识别层对已移除 key 的容错（不应抛异常）")
d = TencentGridDetector()
for k in REMOVED + ("tencent",):
    try:
        d.set_platform_styles(k)
        lines.append(f"   {k:<8} -> active_styles={sorted(d.active_styles or [])}")
    except Exception as e:
        lines.append(f"   {k:<8} -> 异常 {type(e).__name__}: {e}")
        fails.append(f"set_platform_styles({k}) 抛异常")

lines.append("")
lines.append("结论：" + ("全部通过 ✓" if not fails else "存在问题 → " + "; ".join(fails)))

out = os.path.join(HERE, "platform_registry_check.txt")
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
print("\n→ %s" % out)
sys.exit(1 if fails else 0)
