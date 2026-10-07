# -*- coding: utf-8 -*-
"""按平台打印校准报告里的逐帧识别结果，供与 gtm/ 编号拼图对读定 GT。

拼图（montage_for_gt.py 产出）里每枚裁片标的是**引擎框序号**，所以定 GT 时
必须先看引擎切出几枚、序号从 0 还是 1 起，否则张数对不上会整行错位。

用法: py -3.10 localtest\dump_report.py [平台关键字, 可多个]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "calib_report_trim.jsonl")


def main():
    keys = [a.lower() for a in sys.argv[1:]] or ["tuyou"]
    with open(REPORT, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    for r in rows:
        if not any(k in str(r.get("platform", "")).lower() for k in keys):
            continue
        tiles = r.get("labels") or []
        labels = " ".join(str(t) for t in tiles)
        print(f"[{r.get('idx'):02d}] {r.get('platform'):12} status={r.get('status')} "
              f"count={r.get('count')} top={r.get('top_score')} n={len(tiles)}")
        print(f"     {labels}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
