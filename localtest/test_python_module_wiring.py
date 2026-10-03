# -*- coding: utf-8 -*-
"""Python 侧模块接线守卫（只在仓库里跑，不需要设备）。

为什么要这么一道门
--------------------
APK 里的 python 由 Chaquopy 打包，而 CI（.github/workflows/build.yml）是先
`actions/checkout@v4` 拿到**已提交树**再构建。于是「本地能跑、APK 里少文件」成为
一种稳定存在的失效模式：新增的顶层模块忘了 `git add`，端上 import 失败；而 analyzer
的调用点普遍包着 `except Exception: pass`，失败表现不是崩溃，而是**静默退回没有
EV/危险度/账本的通用路径**——用户只觉得"建议变笨了"，日志里什么都查不到。

B-P2 把 `discards_tiebreak` 加进了两个 analyzer 与知识库的顶层 import 链，正好落进
这个风险面，所以这道门属于该改动的必要配套，不是额外整理。

检查两件事
----------
（A）`android/app/src/main/python` 下每个 .py 都必须已被 git 跟踪（缺一个就可能整链降级）
（B）每个模块顶层 import 的根级名字都必须能解析到真实文件（引用不存在的模块名 = 打包后炸）

运行：py -3.10 localtest/test_python_module_wiring.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
PY_ROOT = os.path.join(REPO, "android", "app", "src", "main", "python")

# 顶层 import 才要查：函数体内的延迟 import 失败只影响那条分支，不会让模块加载不了
RE_IMPORT = re.compile(r"^(?:import|from)\s+([A-Za-z_][\w]*)", re.MULTILINE)


def local_py_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(PY_ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".gradle", "build")]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def git_tracked():
    """git 已跟踪的 .py 集合；git 不可用时抛异常（调用方据此显式失败，不静默跳过）。"""
    res = subprocess.run(
        ["git", "-C", REPO, "ls-files", "--", "android/app/src/main/python"],
        capture_output=True, text=True, check=True)
    return {os.path.normcase(os.path.abspath(os.path.join(REPO, ln.strip())))
            for ln in res.stdout.splitlines() if ln.strip().endswith(".py")}


def imported_root_names():
    """{模块名: [引用它的文件:行]}，只收顶层 import。"""
    names = {}
    for path in local_py_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in RE_IMPORT.finditer(src):
            name = m.group(1)
            if name == "__future__":
                continue
            names.setdefault(name, []).append(
                f"{os.path.relpath(path, REPO)}:{src[:m.start()].count(chr(10)) + 1}")
    return names


class TestAllLocalModulesTracked(unittest.TestCase):
    def test_every_python_file_is_git_tracked(self):
        if not os.path.isdir(os.path.join(REPO, ".git")):
            self.fail(f"{REPO} 不是 git 仓库，无法确认打包文件是否在树里（本守卫不接受跳过）")
        tracked = git_tracked()
        missing = [os.path.relpath(p, REPO) for p in local_py_files()
                   if os.path.normcase(os.path.abspath(p)) not in tracked]
        self.assertEqual(
            missing, [],
            msg=("这些生产 python 文件还没进 git 树，CI 构建出的 APK 里不存在它们；"
                 "analyzer 的 import 链会 ModuleNotFoundError 并被 except 静默吞掉，"
                 "表现为「建议悄悄变笨」。请 git add：\n  " + "\n  ".join(missing)))


class TestTopLevelImportsResolve(unittest.TestCase):
    def test_every_top_level_import_resolves(self):
        sys.path.insert(0, PY_ROOT)
        bad = []
        for name, sites in sorted(imported_root_names().items()):
            try:
                spec = importlib.util.find_spec(name)
            except (ImportError, ValueError):
                spec = None
            if spec is None:
                bad.append(f"{name}  ← " + ", ".join(sites[:3]))
        self.assertEqual(
            bad, [],
            msg="顶层 import 解析不到模块（打包后必然整链失败）：" + "; ".join(bad))

    def test_tiebreak_module_is_in_the_packaged_tree(self):
        """点名守卫：决胜链是三个排序点的共同依赖，少它=三处同时降级。"""
        path = os.path.join(PY_ROOT, "discards_tiebreak.py")
        self.assertTrue(os.path.isfile(path), "决胜链模块文件不存在")
        self.assertIn(os.path.normcase(os.path.abspath(path)), git_tracked(),
                      msg="discards_tiebreak.py 未进 git 树（APK 里会缺这个文件）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
