# -*- coding: utf-8 -*-
"""变异检验：把实现改坏，确认守卫真的会红。

为什么需要它：守卫只有在"实现错时会红"时才有价值。一个恒绿的断言比没有断言更糟
——它会让人以为这条性质被保护着。本脚本对源码做**最小故意破坏**（逐个变异、跑目标
套件、期望非零退出），然后无条件还原文件，最后核对还原后的字节与原始一致。

用法：
    py -3.10 -X utf8 localtest/mutation_check.py            # 跑全部登记变异
    py -3.10 -X utf8 localtest/mutation_check.py --list     # 只看登记表
    py -3.10 -X utf8 localtest/mutation_check.py --only probe
输出：build/mutation_check.txt

登记表在 MUTATIONS。新增守卫时若断言的是"某个数值门槛/某条接线"，请顺手补一行变异，
否则那条性质等于没锁。
"""
import argparse
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TGDC = os.path.join(REPO, "android", "app", "src", "main", "python",
                    "recognition", "tencent_grid_detector.py")
ENGINE = os.path.join(REPO, "android", "app", "src", "main", "python",
                      "engine", "engine.py")
OUT = os.path.join(REPO, "build", "mutation_check.txt")

# (组名, 说明, 目标文件, 原文片段, 变异后片段, 期望变红的套件)
MUTATIONS = [
    ("probe", "风格门槛从 0.60 放宽到 0.30（脏路由会被放行）", TGDC,
     "return best_style if mean[best_style] >= 0.60 else None",
     "return best_style if mean[best_style] >= 0.30 else None",
     "test_style_probe.py"),
    ("probe", "忽略额外代表牌（退回单枚定路由）", TGDC,
     "for c in [crop] + list(extra_crops or []):",
     "for c in [crop]:",
     "test_style_probe.py"),
    ("probe", "多枚取平均改成取最差值", TGDC,
     "mean = {st: sum(v.get(st, 0.0) for v in vecs) / n",
     "mean = {st: min(v.get(st, 0.0) for v in vecs) + 0.0",
     "test_style_probe.py"),
    ("probe", "纯色裁片不再跳过（空候选字典会让 max() 抛异常）", TGDC,
     "            if not vec:\n                # 全零分 = 这枚样本没有任何结构",
     "            if False:\n                # 全零分 = 这枚样本没有任何结构",
     "test_style_probe.py"),
    ("probe", "调用点不再传额外代表牌（接线丢失）", TGDC,
     "style = self._probe_style(probe_crop, probe_extra)",
     "style = self._probe_style(probe_crop)",
     "test_style_probe.py"),
    ("probe", "探针也去读平台白名单（未声明平台时永久路由失败）", TGDC,
     "            for _lbl, style, core_btn, core_plain, _gb, _gp in self._cores:",
     "            for _lbl, style, core_btn, core_plain, _gb, _gp in (\n"
     "                    c for c in self._cores\n"
     "                    if self.active_styles is None or c[1] in self.active_styles):",
     "test_style_probe.py"),
    # ---- 手牌通道（引擎 get_hand_detector 与其三处接线）----
    ("hand_channel", "整个特性关掉：所有平台手牌都走主检测器", ENGINE,
     "        if self.platform not in banked_platforms():",
     "        if True:",
     "test_hand_channel.py"),
    ("hand_channel", "调试页 hand_grid 回退口子失效（关不掉）", ENGINE,
     "                or not self._cfg.get(\"hand_grid\", True)):",
     "                ):",
     "test_hand_channel.py"),
    ("hand_channel", "不等主检测器是 YOLO 就接管（绕过注入面）", ENGINE,
     "primary.__class__.__name__ != \"YOLODetector\"",
     "False",
     "test_hand_channel.py"),
    ("hand_channel", "不限平台的风格不再把同名平台当本家（蜀山掉出清单）", TGDC,
     "            out.add(style)",
     "            pass",
     "test_hand_channel.py"),
    ("hand_channel", "process 取 rows 又用回主检测器", ENGINE,
     "                rows = hand_detector.detect_all_rows(image, allow_rotation=False)",
     "                rows = detector.detect_all_rows(image, allow_rotation=False)",
     "test_hand_channel.py"),
    ("hand_channel", "删掉 rows 缓存的来源判据（别家算的 rows 直接上屏）", ENGINE,
     "                    and self._cached_rows_src == hand_detector.__class__.__name__):",
     "                    and True):",
     "test_hand_channel.py"),
    ("hand_channel", "方向快检回到主检测器（同帧付两遍检测的钱）", ENGINE,
     "            det = self.get_hand_detector()",
     "            det = self.get_detector()",
     "test_hand_channel.py"),
]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def read_text(path):
    """字节直读并解码，**不**让通用换行模式把 CRLF 吃成 LF。"""
    return open(path, "rb").read().decode("utf-8")


def write_text(path, text):
    with open(path, "wb") as fp:
        fp.write(text.encode("utf-8"))


def apply_mutation(path, old, new):
    src = read_text(path)
    # 锚点里的 \n 要跟文件实际行尾一致，否则多行锚点在 CRLF 文件上永远命中 0 次
    if "\r\n" in src:
        old = old.replace("\n", "\r\n")
        new = new.replace("\n", "\r\n")
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f"锚点命中 {n} 次（需要恰好 1 次），拒绝盲改：{old[:60]!r}")
    write_text(path, src.replace(old, new))


def run_suite(name):
    """跑守卫，返回退出码。子进程强制 UTF-8，避开 PowerShell 5.1 的 GBK 吞字。"""
    p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, name)],
                       cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    return p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只打印登记表")
    ap.add_argument("--only", default="", help="只跑该组名（如 probe）")
    args = ap.parse_args()

    items = [m for m in MUTATIONS if not args.only or m[0] == args.only]
    if args.list:
        for g, desc, _p, _o, _n, suite in items:
            print(f"[{g}] {desc}  ->  期望 {suite} 变红")
        return 0
    if not items:
        print("登记表为空")
        return 2

    backups = {}
    lines = [f"变异检验 · {len(items)} 项", ""]
    ok = 0
    # 先验基线：守卫本来就红时，“变异后也红”说明不了任何事
    base_suites = {m[5] for m in items}
    for suite in sorted(base_suites):
        code = run_suite(suite)
        if code != 0:
            print(f"基线就红：{suite} 退出码 {code}。先修守卫再做变异检验。")
            with open(OUT, "w", encoding="utf-8") as fp:
                fp.write(f"变异检验未执行：基线 {suite} 退出码 {code}\n")
            return 2
        lines.append(f"基线 {suite} 退出码 0")
    lines.append("")
    try:
        for _g, desc, path, old, new, suite in items:
            if path not in backups:
                backups[path] = (sha(path), open(path, "rb").read())
            apply_mutation(path, old, new)
            try:
                code = run_suite(suite)
            finally:
                write_text(path, backups[path][1].decode("utf-8"))
                restored = sha(path) == backups[path][0]
            caught = code != 0
            ok += int(caught and restored)
            lines.append(f"{'已捕获' if caught else '未捕获(守卫无效!)':16} "
                         f"{'已还原' if restored else '还原失败!!':10} {desc}")
            lines.append(f"                 变异后 {suite} 退出码={code}")
            print(lines[-2], flush=True)
            if not restored:
                lines.append("                 源码未被还原，立即停止后续变异")
                break
    finally:
        # 双保险：异常/中途中断也要把每个动过的文件按原始字节写回去
        for path, (digest, blob) in backups.items():
            if sha(path) != digest:
                with open(path, "wb") as fp:
                    fp.write(blob)
                print(f"[restore] {os.path.basename(path)} 事后还原", flush=True)

    lines.insert(1, f"结论：{ok}/{len(items)} 个变异被守卫捕获")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    return 0 if ok == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
