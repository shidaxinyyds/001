# -*- coding: utf-8 -*-
"""风格探针路由准确率实测：已知平台的帧集 → 探针说是哪个平台。

为什么单独量这个：多平台共库后，识别只在探针选中的那个 bank 内 argmax。
**探针选错 bank = 拿别家平台的字模去认这家的牌**，此时缺类、认错、低分全是
下游症状，补模板也治不了。所以路由准确率是准确率的上游变量，必须单独有数。

口径：帧集目录即平台真值（SOP 第 1 步的约定，`localtest/shots_<platform>/`）。
检测器跑一遍 `detect_hand_strip`，用 monkeypatch 记录 `_probe_style` 的返回值
与 `classify_tile` 的 styles 参数——只观测，不改行为。
拿不到手牌行的帧记为「无手牌行」，不计入路由准确率分母（不能拿没测的当对）。

    py -3.10 -X utf8 localtest/probe_route_eval.py
输出：build/probe_route.txt
"""
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

# (目录, 真值风格)：目录名即平台，见 docs/new_platform_onboarding.md 第 1 步
SETS = [
    ("shots", "tencent"),
    ("shots_shushan", "shushan"),
    ("shots_tuyou", "tuyou"),
]
OUT = os.path.join(REPO, "build", "probe_route.txt")


def main():
    det = TencentGridDetector()
    route = {"style": "未测", "full": 0, "n": 0}
    _probe, _cls = det._probe_style, det.classify_tile

    def probe_rec(crop, extra=None):
        route["style"] = _probe(crop, extra)
        return route["style"]

    def cls_rec(crop, **kw):
        route["n"] += 1
        if kw.get("styles") is None:      # styles 默认 None = 跨全部 bank 扫
            route["full"] += 1
        return _cls(crop, **kw)

    det._probe_style = probe_rec
    det.classify_tile = cls_rec

    lines = [f"模板条目 {len(det._cores)}，bank："
             + " ".join(sorted({c[1] for c in det._cores})), ""]
    hit = scored = refused = 0
    for d, truth in SETS:
        folder = os.path.join(HERE, d)
        if not os.path.isdir(folder):
            lines.append(f"[{d}] 目录不存在，跳过")
            continue
        files = sorted(f for f in os.listdir(folder)
                       if f.lower().endswith((".jpg", ".jpeg", ".png")))
        per = {}
        no_strip = 0
        for f in files:
            img = cv2.imread(os.path.join(folder, f))
            if img is None:
                continue
            route["style"], route["full"], route["n"] = "未测", 0, 0
            t0 = time.perf_counter()
            dets = det.detect_hand_strip(img) or []
            ms = (time.perf_counter() - t0) * 1000.0
            got = route["style"]
            if got == "未测" and not dets:
                no_strip += 1
                key = "无手牌行"
            else:
                key = str(got)
                scored += 1
                if got is None:
                    refused += 1            # 拒识：走跨全库兜底（慢但不算选错平台）
                elif got == truth:
                    hit += 1
            per[key] = per.get(key, 0) + 1
            lines.append(f"  {truth:8} {f[:26]:26} 探针→{str(got):9} "
                         f"{len(dets):>2d}张 {ms:7.1f}ms 比对{route['n']}/跨全库{route['full']}")
        lines.append(f"[{d}] 真值={truth} 共 {len(files)} 帧，探针判定分布："
                     + "  ".join(f"{k}x{v}" for k, v in sorted(per.items()))
                     + (f"  （另有 {no_strip} 帧取不到手牌行，不计入分母）" if no_strip else ""))
        lines.append("")
    lines.append(f"路由准确率（可测帧）：{hit}/{scored} = "
                 + (f"{100.0 * hit / scored:.1f}%" if scored else "无样本")
                 + f"（其中探针拒识、退到跨全库兜底 {refused} 帧，归为未命中）")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
