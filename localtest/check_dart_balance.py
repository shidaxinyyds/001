# -*- coding: utf-8 -*-
"""Dart 源码括号/引号平衡自检。

本机没有 dart/flutter SDK，改动 .dart 无法编译验证；而手工编辑 700 行的 widget 树
最容易犯的就是括号漏配（例如给 fans 段加 `if (...) ...[` 却忘补 `]`）。这类错误
不需要类型系统，纯词法扫描就能抓出来，所以用脚本兜住最低门槛。

不是完整 parser：跳过 // 与 /* */ 注释、'...' 与 "..." 字符串、''' 多行串，并处理
字符串内 ${...} 的嵌套。不能发现类型/空安全错误。
"""
import io
import sys

OPEN = {")": "(", "]": "[", "}": "{"}


def scan(path):
    src = io.open(path, encoding="utf-8").read()
    i, n = 0, len(src)
    stack = []          # 括号栈，元素 (char, line)
    line = 1
    errors = []
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        # 行注释
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        # 块注释
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            if j < 0:
                errors.append(f"{path}:{line}: /* 未闭合")
                break
            line += src.count("\n", i, j)
            i = j + 2
            continue
        # 字符串（含三引号）
        if c in "'\"":
            q = c
            if src[i:i + 3] in ("'''", '"""'):
                q = src[i:i + 3]
            j = i + len(q)
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src.startswith(q, j):
                    break
                if q == "'" and src[j] == "\n":
                    errors.append(f"{path}:{line}: 单引号串跨行未闭合")
                    break
                if src[j] == "$" and j + 1 < n and src[j + 1] == "{":
                    # 插值块：按深度扫到匹配的 }
                    d, k = 0, j + 1
                    while k < n:
                        if src[k] == "{":
                            d += 1
                        elif src[k] == "}":
                            d -= 1
                            if d == 0:
                                break
                        k += 1
                    if d != 0:
                        errors.append(f"{path}:{line}: 字符串内 ${{ 未闭合")
                        break
                    line += src.count("\n", j, k)
                    j = k + 1
                    continue
                if src[j] == "\n":
                    line += 1
                j += 1
            else:
                errors.append(f"{path}:{line}: 字符串未闭合")
                break
            line += src.count("\n", i, min(j, n))
            i = j + len(q)
            continue
        if c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if not stack:
                errors.append(f"{path}:{line}: 多余的 {c}")
            elif stack[-1][0] != OPEN[c]:
                o, ol = stack[-1]
                errors.append(f"{path}:{line}: {c} 与第 {ol} 行的 {o} 不匹配")
                stack.pop()
            else:
                stack.pop()
        i += 1
    for o, ol in stack:
        errors.append(f"{path}:{ol}: {o} 未闭合")
    return errors


def main(argv):
    # Windows 控制台默认 GBK，✓ 这类字符会让 print 抛 UnicodeEncodeError：
    # 表现为“每个文件都 OK，但 exit=1”，把工具自己的编码问题误报成被检文件的失败。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not argv:
        # 不守卫这里就会“没扫描任何文件也返回成功”，在 CI/手动复跑里是假绿。
        print("用法: py -3.10 check_dart_balance.py <文件...>")
        return 2
    bad = []
    for p in argv:
        e = scan(p)
        bad += e
        print(("  OK   " if not e else "  FAIL ") + p)
        for x in e[:12]:
            print("      " + x)
    if bad:
        print(f"\n共 {len(bad)} 处结构性错误")
        return 1
    print("\n括号与字符串字面量配平 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
