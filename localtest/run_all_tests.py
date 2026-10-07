# -*- coding: utf-8 -*-
"""一把跑完 localtest 全部套件与评测门禁，并把结论写成 UTF-8 报告。

为什么要有这个文件：PowerShell 5.1 的代码页是 GBK，中文结论行会被吞掉；
更早的坑是"逐个手敲命令 → 记不清上次到底跑了哪几个"，于是每次改动都要重新
试探一遍，回归覆盖面变成了凭印象。这里做成**自动发现**：
  - 套件 = `localtest/test_*.py` 全量（新增守卫无需登记，天然纳入）
  - 门禁 = `localtest/eval_*.py`（准确率类，红/绿由退出码决定）
这样"加了守卫但没人跑"这条路被堵死。

口径说明（避免把报告读成 CI 绿灯）：
  - eval_* 的退出码 1 表示"该门禁当前不达标"，可能是本次改动造成，也可能是
    改前就红。判断回退必须对比改前/改后，不能只看这一轮全绿与否。
  - 本脚本只在本地跑：CI（.github/workflows/build.yml）不执行 localtest，
    因为仓库不跟踪任何测试图片（见 docs/new_platform_onboarding.md）。

用法：
    py -3.10 -X utf8 localtest/run_all_tests.py
    py -3.10 -X utf8 localtest/run_all_tests.py --only probe      # 只跑名字含 probe 的
    py -3.10 -X utf8 localtest/run_all_tests.py --only test_ --only eval_
输出：build/all_tests.txt
"""
import argparse
import fnmatch
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "build", "all_tests.txt")
# 单套件超时：评测类要扫几十帧、每帧几百 ms，给足余量但绝不放行挂死
TIMEOUT_S = 1800
TAIL_LINES = 20
# OpenCV 的 dnn 后端告警每帧一行，会把真正有用的结论行挤出 tail（实测把
# eval_base 的失败明细全遮掉了），报告里直接丢掉。
NOISE = ("[ WARN:", "[warn:", "setPreferableTarget")


def discover(only):
    """返回 [(标题, 绝对路径)]：test_* 是行为守卫，eval_* 是准确率门禁。"""
    items = []
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py"):
            continue
        if not (name.startswith("test_") or name.startswith("eval_")):
            continue
        if name == os.path.basename(__file__):
            continue
        if only and not any(fnmatch.fnmatch(name, p + "*") for p in only):
            continue
        kind = "门禁" if name.startswith("eval_") else "守卫"
        items.append((f"[{kind}] localtest/{name}", os.path.join(HERE, name)))
    return items


def run_one(path):
    """跑一个套件，返回 (退出码, 全文, 耗时秒)。用文本模式并强制 UTF-8。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", path], cwd=REPO,
                           env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        code = p.returncode
        text = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
    except subprocess.TimeoutExpired as e:
        # 挂死也必须留下证据，否则报告里只剩“没跑完”三个字
        code = -1
        out = (e.stdout or b"").decode("utf-8", "replace")[-2000:]
        text = f"[超时 {TIMEOUT_S}s] {out}"
    return code, text, time.time() - t0


def tail(text):
    lines = [ln.rstrip() for ln in text.strip().splitlines()
             if ln.strip() and not any(n in ln for n in NOISE)]
    return lines[-TAIL_LINES:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[],
                    help="只跑文件名以该前缀开头的套件（可重复）")
    args = ap.parse_args()

    items = discover(args.only)
    if not items:
        print("没有匹配的套件")
        return 2
    lines = [f"localtest 回归 · {time.strftime('%Y-%m-%d %H:%M:%S')} · "
             f"Python {sys.version.split()[0]} · 共 {len(items)} 项", ""]
    bad = []
    for title, path in items:
        code, text, cost = run_one(path)
        state = "PASS" if code == 0 else f"FAIL({code})" if code != -1 else "TIMEOUT"
        if code != 0:
            bad.append(title)
        lines.append(f"{state:12} {cost:7.1f}s  {title}")
        for ln in tail(text):
            lines.append(f"      | {ln[:150]}")
        lines.append("")
        print(f"{state:12} {cost:7.1f}s  {title}", flush=True)

    lines.insert(1, f"结论：{len(items) - len(bad)}/{len(items)} 通过"
                    + (f"；失败 {len(bad)}：{', '.join(bad)}" if bad else "（全绿）"))
    lines.insert(2, "")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(f"\n报告：{OUT}")
    if bad:
        print(f"失败 {len(bad)} 项：{', '.join(bad)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
