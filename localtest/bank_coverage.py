# -*- coding: utf-8 -*-
"""列出每个平台 bank 实际覆盖的标签，以及相对全牌集（34 类）缺哪些。

用途：SOP 第 5 步建库后、以及每次「低分/认错牌」排查时先跑这个。
识别只在探针选中的那个 bank 内 argmax，**bank 里没有的类不可能被认出**，
只能被认成视觉上最像的邻居——所以「缺类」和「分类失误」是两种病，
修法完全不同（前者补模板，后者才谈算法/阈值）。

    py -3.10 -X utf8 localtest/bank_coverage.py [--out build/bank_coverage.txt]
"""
import argparse
import importlib
import os
import sys

# 全牌集：1-9m / 1-9p / 1-9s / 1-7z（7z 位在某些规则里是红中）
FULL = (["%dm" % i for i in range(1, 10)]
        + ["%dp" % i for i in range(1, 10)]
        + ["%ds" % i for i in range(1, 10)]
        + ["%dz" % i for i in range(1, 8)])

# 主 bank 固定是腾讯（它不在 EXTRA_BANKS 里，由 _load_templates 硬挂）
MAIN_BANK = ("recognition.templates_data", "tencent")


def banks():
    """(模块名, 风格名) 清单：直接读生产登记表，绝不在这里手抄第二份。

    这张表原来是一份手写的 BANKS 常量，于是指尖四川建库并注册进 EXTRA_BANKS
    之后，本脚本仍然只看旧六家——排查工具比被排查的对象更容易过期，因为它
    不报错，只是安静地少一行，而它的用途恰恰是回答“这帧为什么认错”。
    """
    from recognition.tencent_grid_detector import EXTRA_BANKS
    return [MAIN_BANK] + list(EXTRA_BANKS)


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="结果写入此文件（UTF-8），同时打到 stdout")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(repo_root(), "android", "app", "src",
                                    "main", "python"))
    from recognition.tencent_grid_detector import STYLE_PLATFORM_WHITELIST
    lines = []
    for mod_name, style in banks():
        owners = STYLE_PLATFORM_WHITELIST.get(style)
        own = ",".join(owners) if owners else f"{style}(不限平台)"
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:                      # 缺库就直接说，别静默
            lines.append("%-9s 导入失败：%s" % (style, exc))
            continue
        keys = list(mod.TEMPLATES_BGR)
        labels = {k.split("#")[0] for k in keys}
        missing = sorted(set(FULL) - labels, key=lambda x: (x[1], int(x[0])))
        variants = sorted(k for k in keys if "#" in k)
        extra = sorted(labels - set(FULL))          # 非 mpsz 键（如副露/特殊标记）
        lines.append("%-9s 认领平台=%-12s 模板=%3d 标签=%2d 变体=%d" % (
            style, own, len(keys), len(labels), len(variants)))
        lines.append("          缺类(%d)：%s" % (
            len(missing), " ".join(missing) or "无"))
        if extra:
            lines.append("          非 mpsz 键：%s" % " ".join(extra))
        if variants:
            lines.append("          变体键：%s" % " ".join(variants))
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
