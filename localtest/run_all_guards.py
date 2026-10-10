# -*- coding: utf-8 -*-
"""全量守卫回归驱动器：把 localtest/test_*.py 与它们的变异检验一次跑完并对账。

为什么要有这个文件（而不是手敲 25 条命令）：
1. 生产改动（如 `Engine.__init__` 的玩法来源）影响面横跨识别/决策/稳定器，靠记忆
   挑几个跑必然漏；漏掉的守卫看起来是绿的，实际什么都没测。
2. **退出码不够**：unittest 在 `Ran 0 tests` 时也是 rc=0，夹具缺失而脚本自己
   `SkipTest` 时同样是 rc=0。所以这里逐条核对「有没有跑出 N>0 个用例」，
   非 unittest 的脚本（打印 ALL_OK / PHASE4 ACCURACY OK 的那几个）核对各自的成功标记。
3. 变异检验必须跟主守卫一起跑：只有「改回坏写法必红」成立，主守卫才算接了线。
   漏跑变异检验的守卫会在下一轮被悄悄削弱成快照。

判红口径（任一即红）：
  - rc != 0；
  - rc == 0 但输出里找不到成功标记（没跑用例 / 跑了 0 个 / 静默跳过）；
  - 输出里有 OK 却同时有 `FAILED`/`Traceback`；
  - **全部用例都被 skip**（`OK (skipped=N)` 且 N==Ran）：夹具没就位时 unittest
    照样 rc=0 打印 OK，这条最会伪装成绿灯，必须单独判红。部分 skip 如实计数并报出来。

用法：
  py -3.10 -X utf8 localtest/run_all_guards.py            # 全部主守卫
  py -3.10 -X utf8 localtest/run_all_guards.py --mutate   # 只跑变异检验
  py -3.10 -X utf8 localtest/run_all_guards.py test_engine.py test_stability.py
输出：build/guards_all.txt（或 guards_mut.txt）—— 由脚本自己按 UTF-8 写，
      不要用 shell 重定向（PowerShell 会把 UTF-8 中文二次编码成乱码，报告没法读）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT_DIR = os.path.join(REPO, "build")
PY = [sys.executable, "-X", "utf8"]

# 支持 `--mutate` 反向对照的守卫（见各文件 docstring）。这张表靠记忆必然过期，
# 所以 `preflight()` 用源码反查 `--mutate` 是否被解析，两侧不一致就直接判红并非零退出
# ——漏登记的后果是「静默不跑变异检验」，那比不跑更糟。
MUTABLE = {
    "test_mode_gate_guard.py",
    # 非牌局屏不得声称处于「需要手持牌」的阶段：结算页实测被判成 swap 后，面板会在
    # 结算画面上画一套换牌 UI。拿掉出口闸门不会让任何精度守卫变红，只能靠这条。
    "test_nongame_phase_guard.py",
    # 多平台真机 20 帧手牌表：真值来自人眼读屏，读数链路（玩法闸门/平台风格）断了
    # 不会让任何既有精度守卫变红——它只会被当成“识别退化了”。
    "test_multi_hand_guard.py",
    # D 层语义闸门：同一帧不得交两套事实（非定缺玩法不得有定缺信息、换牌阶段不得
    # 给进攻建议/报摸牌、0 进张不得附改打行、非法张数要自报）。文案会变，互斥关系不会。
    "test_semantic_coherence_guard.py",
    # 主页/悬浮窗文案与接线（必填、玩法列表不筛、引擎不换玩法）：本机无 Flutter SDK，
    # 这类契约只能从 Python 侧反向钉 Dart 源码。
    "test_ui_wording_guard.py",
    "test_skip_frame_guard.py",
    "test_non_table_honesty_guard.py",
    "test_hot_path_contracts_guard.py",
    "test_det_ledger.py",
    "test_bank_hygiene.py",
    "test_structural_decide.py",
    "test_eval_alignment.py",
    "test_bank_selection.py",
    "test_skip_probe_guard.py",
    "test_parallel_classify_guard.py",
    # YOLO 通道 honor classify=False（否则牌桌实证每帧白付 ~1s）。
    "test_classify_contract_guard.py",
    # 端到端延迟可测 + 语义判据（P1-e/B4/B5）：这类改动失效时不会有任何精度测试变红。
    "test_latency_contract_guard.py",
    # 局况事实层（阶段/回合/碰杠事件播报）：纯折算逻辑，错了不会拖坏识别，也不会让
    # 任何精度守卫变红——它只会“默默说一句假的”，必须自己的变异检验盯着。
    "test_match_state_guard.py",
    # 手牌条带通道 + 防空窗三件套（缺张/零检测帧/熔断/亮度 None/硬重置 fuse）：
    # 这些判据失效的产物是「少读几张牌」「多等两秒」「把对的东西清掉」，
    # 精度基线逐字不变，只有直接对判据做断言才看得见。
    "test_hand_strip_channel_guard.py",
    # 手牌显示响应（四条采纳通道）：它的失效形式是「摸/打/碰慢一帧」或「单帧误识别
    # 直接上屏」，两边都是用户报的原话，而精度基线一字不变 —— 只能直接断言判据。
    "test_hand_response_guard.py",
    # 真机四帧阶段回归（换牌视觉/徽章自噬/阶段诚实性/全链路 GT）：这四类故障的产物
    # 是「阶段说错」与「有牌不显示」，识别精度基线逐字不变，没有任何其它守卫会红。
    "test_real_phase_frames_guard.py",
    # 风格核预算（声明平台后不再扫别家牌风，且不剥其它家的跨家救援）：它改的是
    # 「每片跑多少次 matchTemplate」，腾讯 37 帧 GT 逐帧零差异——精度守卫一条都不会红，
    # 只能直接断言白名单与核数；而「顺手把蜀山整条限平台」那种过度减法又会真的
    # 掉 zj 的救援（实测 zj#08 七筒变 6p），两头都必须钉住。
    "test_platform_style_budget_guard.py",
    # 张数候选序贯早停（r6lat 延迟第二刀）：它改的是「每帧切几整行牌」，41 帧逐格
    # 对拍全等——精度基线一字不变，只有直接断言提交枚数与重扫门才看得见它有没有接线。
    "test_hand_count_seq_guard.py",
    # 定缺徽章读数的时间稳定门（r6dq，面板自噬的次级防线）：它的失效形式是「缺门在
    # 面板上抽搐」与「新局挂着上一局的缺门」，识别精度基线逐字不变（eval_base 的定缺
    # 基准是单帧单枪，全等窗口天然可信），没有任何其它守卫会红。
    "test_dingque_stability_guard.py",
    # 打分两阶段「粗筛+精算」（r6lat 延迟第四刀）：它裁的是**候选集**而不是判据，
    # 裁错的产物是偶发错判（真答案被粗筛挤出后 `_decide` 读到相邻类 0 分、整条结构
    # 判决静默失效），而 37 帧 GT 的精度基线在对拍全等前提下同样一字不变 —— 只有
    # 逐枚 (label, score) 对拍与闭包表断言才看得见它有没有接错线。
    "test_prefilter_consistency_guard.py",
}

# 每条判绿的标记：unittest 系用 Ran/OK；脚本系用它自己打印的成功行。
RUN_RE = re.compile(r"^Ran (\d+) test", re.M)
SKIP_RE = re.compile(r"skipped=(\d+)")
OK_WORDS = ("OK", "ALL_OK", "PHASE4 ACCURACY OK")
BAD_WORDS = ("FAILED", "Traceback (most recent call last)", "error:")


def declares_mutate(name):
    """该守卫源码里是否自己解析 `--mutate`（比靠记忆维护的清单可靠）。

    MUTABLE 这张表漏一条的后果是**静默不跑**变异检验（过滤器把它剔掉了），所以
    这里用源码反查：声明了 `--mutate` 却不在表里 → 由 preflight 判红，逼着登记。
    """
    with open(os.path.join(HERE, name), encoding="utf-8") as fp:
        return "--mutate" in fp.read()


def preflight():
    """驱动器自身的接线检查，返回红项列表（这些不是被测代码的错，是我的错）。"""
    bad = []
    for f in sorted(os.listdir(HERE)):
        if not (f.startswith("test_") and f.endswith(".py")):
            continue
        if declares_mutate(f) and f not in MUTABLE:
            bad.append(f"{f}: 支持 --mutate 却没登记进 MUTABLE（变异检验会被静默跳过）")
        if f in MUTABLE and not declares_mutate(f):
            bad.append(f"{f}: 在 MUTABLE 里但源码不认 --mutate（清单过期）")
    return bad


def candidates(only):
    files = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    if only:
        missing = [f for f in only if f not in files]
        if missing:
            sys.exit(f"这些守卫文件不存在：{missing}\n现有：{files}")
        return [f for f in files if f in only]
    return files


def run_one(name, mutate):
    argv = PY + [os.path.join(HERE, name)] + (["--mutate"] if mutate else [])
    t0 = time.perf_counter()
    p = subprocess.run(argv, capture_output=True, cwd=REPO)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    dur = time.perf_counter() - t0
    ran = RUN_RE.search(out)
    skipped = SKIP_RE.search(out)
    n_skip = int(skipped.group(1)) if skipped else 0
    marked = any(w in out for w in OK_WORDS)
    bad = any(w in out for w in BAD_WORDS)
    n = int(ran.group(1)) if ran else None
    # 变异检验有两种写法：unittest 版（靠 `Ran N`/`OK`）与脚本版（`sys.exit(mutate_check())`，
    # 自己打印 `[mutate] …被拦下` 的证据行）。脚本版的散文会改字，所以**不匹配散文**，
    # 只要求「有 rc=0 + 至少一条证据行」，并把原句抓进报告让人肉眼可核。
    evidence = [l.strip() for l in out.splitlines() if "[mutate]" in l] if mutate else []
    if p.returncode != 0:
        verdict = "红（退出码 %d）" % p.returncode
    elif bad:
        verdict = "红（成功标记之外还有 FAILED/Traceback）"
    elif n == 0:
        verdict = "红（Ran 0 tests：夹具没就位或在测空气）"
    elif n and n_skip >= n:
        verdict = "红（%d 个用例全被 skip：等于没测）" % n
    elif mutate and not n and not evidence:
        verdict = "红（变异检验既没跑用例也没留证据行）"
    elif n is None and not marked and not evidence:
        verdict = "红（没有任何成功标记，等于没测到东西）"
    else:
        verdict = "绿"
    return verdict, n, n_skip, marked, dur, out, evidence




def tail(out, n=25):
    """失败时只保留最后若干行 + 所有 FAIL:/ERROR: 行，报告才不致于长到没人看。"""
    lines = out.splitlines()
    keys = [l for l in lines if re.match(r"^(FAIL|ERROR):", l)]
    return keys + ["  ..."] + lines[-n:]


def main():
    argv = [a for a in sys.argv[1:]]
    mutate = "--mutate" in argv
    only = [a for a in argv if not a.startswith("--")]
    files = candidates(only)
    if mutate:
        files = [f for f in files if f in MUTABLE]
    out_path = os.path.join(OUT_DIR, "guards_mut.txt" if mutate else "guards_all.txt")
    os.makedirs(OUT_DIR, exist_ok=True)

    lines = ["# 全量%s回归 —— %s" % ("变异检验" if mutate else "守卫",
                                    time.strftime("%Y-%m-%d %H:%M:%S")),
             "命令：py -3.10 -X utf8 localtest/run_all_guards.py%s" %
             (" --mutate" if mutate else ""), ""]
    reds = []
    # 先跑驱动器自检：清单过期时整轮结果都不可信，不能只把「跑了 25 条全绿」交出去。
    for msg in preflight():
        reds.append("驱动器自检")
        lines.append("[×] 自检 —— %s" % msg)
        print("SELF-CHECK 红：%s" % msg, flush=True)
    if reds:
        lines.append("")
        lines.append("驱动器自检未过，本轮不执行被测守卫（先修清单）。")
        with open(out_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
        return 1
    for f in files:
        name = f
        verdict, n, n_skip, marked, dur, out, evidence = run_one(f, mutate)
        cnt = ("%d 用例" % n) if n is not None else ("证据行 %d 条" % len(evidence) if evidence
                                                    else ("成功标记" if marked else "无计数"))
        if n and n_skip:
            cnt += "（skip %d）" % n_skip
        lines.append("[%s] %-32s %-16s  %.1fs" % (
            "绿" if verdict == "绿" else "×", name, cnt, dur))
        if mutate and verdict == "绿":
            lines += ["      " + l[:160] for l in evidence]
        print("%-34s %s (%.1fs)" % (name, verdict, dur), flush=True)
        if verdict != "绿":
            reds.append(name)
            lines += ["      " + l for l in tail(out)]
        lines.append("")
    lines.insert(2, "共 %d 条，红 %d：%s" % (
        len(files), len(reds), ", ".join(reds) or "无"))
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    print("\n红：%s" % (", ".join(reds) or "无"))
    print("报告：%s" % out_path)
    return 1 if reds else 0


if __name__ == "__main__":
    sys.exit(main())
