# -*- coding: utf-8 -*-
"""B-P3 交付实测：同一批真实帧上，旧口径 vs 新口径的面板文案对比。

为什么要脚本化这份对比（设计意图）
--------------------------------
「改造前后有什么差别」如果靠手工回忆或凭印象手写，就成了自证。这里：

* 旧文案不是我编的：`equity_radar` 的旧实现从 `git show HEAD:` 落盘的文件里**真的
  import 回来跑**（`build/_head_radar.py`），其余旧文案的格式串逐字取自 HEAD 版本的
  `mahjong_overlay.dart` / `std/std_analyzer.py`，并在旁边标注来源行号；
* 数字同源：新旧两列喂的是同一帧 payload 里的同一份 win_equity / net_ev /
  deal_in_prob / tenpai_prob / ukeire，差别只可能来自口径，不可能来自取数；
* 帧是真帧：走 `test_tile_ledger_e2e.make_engine` + `process()` 全链路（假 detector），
  不是手搭 analyzer 返回值；
* 不走 payload 的两处（设置页伪指标、选牌阶段 chip）在表里标了「源码级」，
  两列都由 `_literal` 从 HEAD / 工作区源码里抽字符串字面量——抽不到就报错，
  绝不用手写的一句话冒充旧口径。

运行：py -3.10 localtest/report_probability_honesty.py
输出：stdout 的 markdown 表 + build/p3_before_after.md
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PKG = os.path.join(ROOT, "android", "app", "src", "main", "python")
sys.path.insert(0, PKG)
sys.path.insert(0, HERE)

from test_tile_ledger_e2e import make_engine, make_image, run_frames  # noqa: E402

HEAD_RADAR = os.path.join(ROOT, "build", "_head_radar.py")
RADAR_SRC = "android/app/src/main/python/sichuan/equity_radar.py"


def load_head_radar():
    """把改造前的 equity_radar.py 从 git HEAD 取回来，按独立模块加载。

    先落盘再 importlib：源码里 `from probability_bands import ...`（新版才有）不该
    参与旧版渲染，用真实旧文件才能保证旧列是「当时面板真的会说的话」。
    """
    os.makedirs(os.path.dirname(HEAD_RADAR), exist_ok=True)
    blob = subprocess.run(["git", "show", "HEAD:" + RADAR_SRC],
                          cwd=ROOT, capture_output=True).stdout
    assert blob, "git show 没取到旧版 equity_radar（HEAD 变了？本报告的前提就没了）"
    with open(HEAD_RADAR, "wb") as fh:
        fh.write(blob)
    spec = importlib.util.spec_from_file_location("head_equity_radar", HEAD_RADAR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.WinEquityGauge


# ---- 旧版面板格式串（逐字取自 git HEAD，行号见注释） -------------------
# HEAD:lib/overlays/mahjong_overlay.dart:2239  '胜率 $winRate%'
OLD_WIN_CHIP = "胜率 {rate}%"
# HEAD:lib/overlays/mahjong_overlay.dart:2256  '+${netEv}番'（单位错标：那是内部评分）
OLD_EV_CHIP = "+{ev:.1f}番"
# HEAD:lib/overlays/mahjong_overlay.dart:2308  '$dealInPercent%危'
OLD_DANGER = "{p}%危"
# HEAD:lib/overlays/mahjong_overlay.dart:2420  '叫听 $tenpaiRate%'
OLD_TENPAI = "叫听 {p}%"
# HEAD:lib/overlays/mahjong_overlay.dart:2442  '$tileStr $p%'（未归一化相对后验）
OLD_HELD = "{tile} {p}%"
# HEAD:lib/overlays/mahjong_overlay.dart:2876  '进张 $topUkeire 张'
OLD_UKEIRE_CHIP = "进张 {n} 张"
# HEAD:android/app/src/main/python/std/std_analyzer.py:371  f"{sh}向听 · 进张{ukeire}张"
OLD_STD_REASON = "{sh}向听 · 进张{n}张"
# HEAD:android/app/src/main/python/std/std_analyzer.py:365  听牌旧文案（把“未现上界”
# 直接当“余 N 张”报，不拆牌墙/对手）
OLD_STD_TING_REASON = "听 {names} · 余 {n} 张 · 最高 {fan} 番"
# HEAD:android/app/src/main/python/sichuan/sichuan_analyzer.py:764  川麻同位分支
OLD_SC_TING_REASON = "听 {names}，余 {n} 张"

FRAMES = [
    ("A 川麻·好形听牌（牌墙充足）", "sc_xz",
     ["3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "5p", "5p", "5p", "7s"],
     ["1s", "2s", "9p", "9p"], 51),
    ("B 川麻·末期承压（叫口见窄 + 对手高危）", "sc_xz",
     ["1m", "3m", "5m", "7m", "9m", "2p", "4p", "6p", "8p", "1s", "3s", "5s", "7s", "9s"],
     ["1p", "1p", "1p", "2s", "2s", "3p", "4p", "5p", "6p", "7p", "8p", "8p",
      "9p", "9p", "1m", "1m", "4s", "6s"], 52),
    ("C 大众·中局非听牌（进张上界）", "std_tdh",
     ["1m", "2m", "4m", "5m", "7m", "9m", "1p", "2p", "4p", "6p", "8p", "9p", "7z", "7z"],
     ["3s", "6s", "1z"], 53),
    ("D 川麻·早期散牌（低置信度）", "sc_xz",
     ["1m", "9m", "2p", "8p", "3s", "7s", "5s", "1p", "9p", "4m", "6p", "2s", "8s", "5m"],
     ["7p", "3m"], 54),
]


def pct(x):
    return int(round(float(x or 0.0) * 100))


HEAD_FILES = {
    "debug": "lib/debug_page.dart",
    "engine": "android/app/src/main/python/engine/engine.py",
}


def _blob(rev: str, rel: str) -> str:
    """取一份文件正文：rev="HEAD" 走 git，rev="" 读工作区。

    旧列一律从 git 里抽，不靠回忆；新列从磁盘上抽。这样“改了措辞”与
    “旧口径本来长这样”两件事分开，表里每一句话都能回源文件查到行号。
    """
    if rev == "HEAD":
        raw = subprocess.run(["git", "show", "HEAD:" + rel], cwd=ROOT,
                             capture_output=True).stdout
        assert raw, f"git show 没取到 {rel}：旧列前提已经不在"
        return raw.decode("utf-8")
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _literal(src: str, needle: str, where: str) -> str:
    """抠出包含 needle 的那一行的第一个字符串字面量（跳过注释行）。

    抽不到直接报错：宁可缺一行对比，也不把我手写的一句“以前大概是这么说的”
    当成旧口径——那等于自己给自己编证据。
    """
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue                        # 注释里的是历史描述，不是真的展示代码
        if needle in line:
            m = re.search(r"['\"]([^'\"]*)['\"]", line)
            assert m, f"{where} 里 {needle!r} 所在行没有字面量：{line!r}"
            return m.group(1)
    raise AssertionError(f"{where} 里找不到 {needle!r}：HEAD/工作区已变，"
                         f"请同步本脚本，不要手写旧文案")


def source_rows():
    """payload 里抓不到的两处：设置页伪指标、选牌阶段的进张上界。

    这两处不编进逐帧表（它们不走 payload），而是逐字从两边源码里抽字面量，
    并在表里标明“源码级对比”，免得读者把它当成真帧实测数据。
    """
    head_dbg, cur_dbg = _blob("HEAD", HEAD_FILES["debug"]), _blob("", HEAD_FILES["debug"])
    head_eng, cur_eng = _blob("HEAD", HEAD_FILES["engine"]), _blob("", HEAD_FILES["engine"])
    bar = "＋能量条(value: score/100)" if "value: score / 100.0" in head_dbg else ""
    old_dbg = (f"{_literal(head_dbg, '心理胜势指数', 'debug_page HEAD')} · "
               f"{_literal(head_dbg, '$score%', 'debug_page HEAD')}{bar}")
    return [
        ("设置页（源码级）", "心态指标",
         old_dbg, _literal(cur_dbg, '心态签文', 'debug_page 工作区')),
        ("选牌阶段（源码级）", "预摸进张 chip",
         "选 X 后 N向听" + _literal(head_eng, 'u_str = ', 'engine HEAD'),
         "选 X 后 N向听" + _literal(cur_eng, 'u_str = ', 'engine 工作区')),
    ]


def ukeire_chip(item):
    """与 Dart `_ukeireLabel` 逐字同构（那里无 SDK 可编译，错一个字就是面板谎报）：
    进张 0 且 >=2 向听是“还没算出来”，显 —；有账本拆账才能报裸张数。"""
    u = int(item.get("ukeire") or 0)
    sh = int(item.get("shanten") or 0)
    if u == 0 and sh >= 2:
        return "—"
    backed = isinstance(item.get("ting_chance"), dict) or \
        isinstance(item.get("ukeire_chance"), dict)
    return f"{u}张" if backed else f"≤{u}张"


def old_reason(mode, item):
    """重构 HEAD 版这条建议会说什么。

    只重构能确定对应到 HEAD 同一分支的情形：川麻非听牌的旧文案需 incoming 牌名
    列表，而 payload 不交那个列表，拿不到就不编一个旧串来“自证改进了”，直接标
    「无法从 HEAD 重构」，让表里每一句旧话都有出处。
    """
    sh = int(item.get("shanten") or 0)
    u = int(item.get("ukeire") or 0)
    reason = str(item.get("reason") or "")
    tds = item.get("ting_details") or []
    names = "/".join(t.get("name", "") for t in tds[:3])
    if sh <= 0 and tds:
        if mode == "std_tdh":
            return OLD_STD_TING_REASON.format(
                names=names, n=u, fan=max((t.get("fan") or 1) for t in tds))
        return OLD_SC_TING_REASON.format(names=names, n=u)
    if mode == "std_tdh":
        return OLD_STD_REASON.format(sh=sh, n=u)
    if not re.search(r"\d", reason):
        # 川麻定缺/保刻子这类无数字分支：HEAD 与现在的字面量逐字相同（两个
        # 同位分支都是 `f"定缺打{SUIT_NAMES[...]}"`），直接拿新串当旧串是安全的，
        # 而且能说明“本分支本来就没有伪精确，本轮也没弄坏它”。
        # HEAD:android/app/src/main/python/sichuan/sichuan_analyzer.py:811/823
        return reason + "（HEAD 同分支同文案）"
    return "（无法从 HEAD 重构：旧文案需 incoming 牌名列表，payload 不交）"


def rows_for(OldGauge, name, mode, hand, river, seed):
    eng = make_engine(mode, hand, river)
    p = run_frames(eng, make_image(seed=seed), 8)[-1]
    gauge = p.get("ev_gauge") or {}
    advice = (p.get("advice") or [{}])[0]
    df = advice.get("danger_flow") or p.get("danger_flow") or {}
    hrs = p.get("hand_ranges") or []
    out = []

    if gauge:
        eq, ev = gauge["win_equity"], gauge["net_ev"]
        old_insight = OldGauge.evaluate_gauge(
            eq, expected_fan=gauge.get("expected_fan", 1),
            max_deal_in_prob=df.get("deal_in_prob", 0.05))["insight"]
        out.append((name, "胜率数字",
                    OLD_WIN_CHIP.format(rate=pct(eq)), gauge["band"]))
        out.append((name, "期望收益单位",
                    OLD_EV_CHIP.format(ev=ev), f"{ev} {gauge['net_ev_unit']}"))
        out.append((name, "雷达结论", old_insight, gauge["insight"]))
    else:
        # 旧列不能写「胜率 50%」：HEAD 面板本来就要 evGauge['badge'] 非空才渲染这个
        # 区块（HEAD:lib/overlays/mahjong_overlay.dart:2501），std 帧从来不显示它。
        # 把“本帧不会显示的东西”编进旧列当对比，就等于自造一个没发生过的伪精确。
        out.append((name, "胜率数字",
                    "（不显示：HEAD 面板要求 evGauge 非空，std 帧没有该数据）",
                    "（仍不显示；本轮新增的守卫是「引擎不许凭空造仪表盘」，"
                    "由 test_std_frame_does_not_fabricate_a_gauge 锁住）"))

    if df:
        out.append((name, "点炮危险",
                    OLD_DANGER.format(p=pct(df["deal_in_prob"])), df["danger_band"]))
    if hrs:
        out.append((name, "对手听牌倾向",
                    OLD_TENPAI.format(p=pct(hrs[0]["tenpai_prob"])), hrs[0]["tenpai_band"]))
        held = (hrs[0].get("top_held") or [{}])[0]
        if held:
            out.append((name, "对手持牌透视",
                        OLD_HELD.format(tile=held.get("name") or held.get("tile"),
                                        p=pct(held.get("prob"))),
                        f"{held.get('band')}｜{hrs[0].get('held_prob_kind', '')}"))
    if advice:
        u = advice.get("ukeire", 0)
        out.append((name, "进张数（面板 chip）",
                    OLD_UKEIRE_CHIP.format(n=u), ukeire_chip(advice)))
        out.append((name, "建议 reason",
                    old_reason(mode, advice), str(advice.get("reason") or "")))
    return out


def main():
    OldGauge = load_head_radar()
    rows = []
    for spec in FRAMES:
        try:
            rows.extend(rows_for(OldGauge, *spec))
        except Exception as exc:                        # noqa: BLE001
            rows.append((spec[0], "生成失败", "-", f"{type(exc).__name__}: {exc}"))
    rows.extend(source_rows())          # 不走 payload 的两处：抽字面量，不手写
    lines = ["| 帧 | 面板位置 | 旧口径（HEAD 真实现：雷达重跑 / 源码抽串） | 新口径（当前 payload / 工作区源码） |",
             "|---|---|---|---|"]
    for f, k, old, new in rows:
        esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
        lines.append(f"| {esc(f)} | {esc(k)} | {esc(old)} | {esc(new)} |")
    md = "\n".join(lines)
    print(md)
    out = os.path.join(ROOT, "build", "p3_before_after.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    n = sum(1 for _f, _k, old, new in rows if str(old) != str(new))
    print(f"\n[{len(rows)} 项对比，其中 {n} 项口径变化] 已写入 {out}")
    # 逐帧打印新 payload 的关键字段，便于人工核对「新口径确实来自账本/常量」
    for spec in FRAMES:
        eng = make_engine(spec[1], spec[2], spec[3])
        p = run_frames(eng, make_image(seed=spec[4]), 8)[-1]
        print(json.dumps({"frame": spec[0], "ev_gauge": p.get("ev_gauge"),
                          "danger_flow": (p.get("advice") or [{}])[0].get("danger_flow"),
                          "hand_ranges": (p.get("hand_ranges") or [])[:1],
                          "advice0": {k: v for k, v in (p.get("advice") or [{}])[0].items()
                                      if k in ("tile", "shanten", "ukeire", "reason",
                                               "ting_chance", "ukeire_chance")}},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
