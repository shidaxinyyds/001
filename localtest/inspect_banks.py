# -*- coding: utf-8 -*-
"""看附加风格 bank 模块的存储格式（键名、编码方式、张的尺寸），不打印大段正文。

新增平台 bank 必须与之同格式，否则 _build_cores 的 y16:104 / x12:68 切片会错位。

用法: py -3.10 localtest\inspect_banks.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

for mod in ("recognition.templates_data", "recognition.templates_shushan"):
    m = __import__(mod, fromlist=["*"])
    d = getattr(m, "TEMPLATES_BGR", {})
    print(f"\n=== {mod}: {len(d)} 条 ===")
    for i, (k, v) in enumerate(d.items()):
        a = np.asarray(v)
        print(f"  {k:12} dtype={a.dtype} shape={a.shape}"
              + (f" min={a.min()} max={a.max()}" if a.size else ""))
        if i >= 5:
            print("  ...")
            break
    print("  全部键:", " ".join(sorted(d))[:400])
    for extra in ("TEMPLATES_GRAY",):
        if hasattr(m, extra):
            print(f"  另有 {extra}: {len(getattr(m, extra))} 条")
    src = os.path.join(PYROOT, *mod.split(".")) + ".py"
    print(f"  文件 {os.path.getsize(src)/1024:.0f} KB")
sys.exit(0)
