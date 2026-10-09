# -*- coding: utf-8 -*-
"""A/B（同进程交替）：闸门补打分到底多花多少毫秒。

为什么必须同进程按 old→new→old 交替跑：跨进程比均值/中位数在本仓库出过测量可信度
事故（同配置重复压测 p50 给出 280.7ms 与 646.8ms），所以这里不新建进程，只把
`_rescue_out_of_gate` 换成透传实现来当"关闭态"。
"""
import os
import statistics
import sys

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "localtest"))

import diag_report_frames as DR  # noqa: E402
import engine.engine as E  # noqa: E402

IMGS = [(n, p, m, cv2.imread(os.path.join(DR.SHOT_DIR, n)))
        for n, p, m in DR.FRAMES]
IMGS = [x for x in IMGS if x[3] is not None]
REAL = E._rescue_out_of_gate


def off(detector, image, row):
    return row, []


def run_once():
    out = {"on": [], "off": []}
    for tag, fn in (("on", REAL), ("off", off)):
        E._rescue_out_of_gate = fn
        for name, pf, md, img in IMGS:
            d = DR.run(img, pf, md)
            out[tag].append(float(d.get("elapsed") or 0.0) * 1000.0)
    E._rescue_out_of_gate = REAL
    return out


def main():
    a, b, a2 = run_once(), run_once(), run_once()
    for i, r in enumerate((a, b, a2), 1):
        on, offl = r["on"], r["off"]
        print(f"pass{i}  开补打分 均值={statistics.mean(on):7.1f}ms 中位={statistics.median(on):7.1f}ms"
              f" 最差={max(on):7.1f}ms   关 均值={statistics.mean(offl):7.1f}ms "
              f"中位={statistics.median(offl):7.1f}ms 最差={max(offl):7.1f}ms")
    all_on = a["on"] + b["on"] + a2["on"]
    all_off = a["off"] + b["off"] + a2["off"]
    print(f"合计 开={statistics.mean(all_on):.1f}ms 关={statistics.mean(all_off):.1f}ms "
          f"差额={statistics.mean(all_on) - statistics.mean(all_off):+.1f}ms")


if __name__ == "__main__":
    main()
