# -*- coding: utf-8 -*-
"""对比两份 calib_report.jsonl，量化「新增风格 bank」改了哪些帧的标签。

关注两件事：
1. 收益——雀神帧的标签是否变好（配合 ab_pitch_classify.py 的 GT 命中率看）；
2. 风险——**跨风格泄漏**：新 bank 里的雀神模板去抢别的平台（微乐/JJ/途游）的牌，
   表现为非雀神帧的标签发生变化。未预期的变化必须逐条解释，不能默认无害。

用法: py -3.10 localtest\diff_calib.py <before.jsonl> <after.jsonl>
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import STYLE_PLATFORM_WHITELIST  # noqa: E402

# 拥有专属 bank 的平台。不能硬写“只有雀神”：途游/JJ 补库后它们的帧变化同样是
# 预期的，拿旧启发式去标会把正确修复误报成跨风格泄漏。
OWNED = {p for owners in STYLE_PLATFORM_WHITELIST.values() for p in owners}


def tiles(h):
    """把 '1m5m7z' 连写的 MPSZ 串拆成牌列表（按字符数差会错估改动量）。"""
    return re.findall(r"\d+[a-zA-Z]+", h or "")


def load(p):
    out = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out[int(d["idx"])] = d
    return out


def main():
    before = load(sys.argv[1] if len(sys.argv) > 1
                  else os.path.join(HERE, "calib_report_before_bank.jsonl"))
    after = load(sys.argv[2] if len(sys.argv) > 2
                 else os.path.join(HERE, "calib_report.jsonl"))
    ch = []
    for idx in sorted(set(before) | set(after)):
        b, a = before.get(idx, {}), after.get(idx, {})
        if b.get("hand") != a.get("hand") or b.get("count") != a.get("count"):
            ch.append((idx, a.get("platform", "?"), b.get("hand", ""), a.get("hand", ""),
                       b.get("count"), a.get("count")))
    print(f"标签/张数发生变化的帧: {len(ch)}/{len(after)}\n")
    for idx, plat, hb, ha, cb, ca in ch:
        tag = f"预期({plat} 有专属 bank)" if plat in OWNED else "[!] 需逐条解释"
        tb, ta = tiles(hb), tiles(ha)
        n = sum(1 for i in range(min(len(tb), len(ta))) if tb[i] != ta[i])
        print(f"[{idx:02d}] {plat:11} {tag}  张数 {cb}->{ca}  同位不同牌 {n} 张")
        print(f"      前: {hb}")
        print(f"      后: {ha}")
    if not ch:
        print("（无任何变化——说明新 bank 一张牌都没影响，需检查是否真的加载上了）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
