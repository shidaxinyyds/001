# -*- coding: utf-8 -*-
"""主页/悬浮窗文案与两处接线守卫（用户点名要改的那几条，改完必须还在）。

为什么值得钉：这一轮全是「文案 + 一条必填 + 玩法列表不再被平台筛」的改动，本机
又没有 Flutter SDK 可编译 —— 没有守卫的话，下一次改 UI 的人（包括我自己）把
`气运推演` 顺手改回去、或把玩法过滤又加回来，不会有任何东西报红。仓库里已有同种
先例：`test_equity_honesty.py` 就是用 Python 反向钉 Dart 源码的跨语言契约。

钉四件事：
① 文案：新词必须在、旧词必须彻底不在（旧词残留=还有第二条路径没改干净）。
② 对局ID 必填：`SessionStore.require` 存在、锁定那条路真的调用它、输入框提示写着
   「必填」。只改提示不改判据是这类坑最常见的形态（写着必填却仍能空着锁定）。
③ 玩法列表全量：主页与悬浮窗菜单都直接用 `GameMode.allModes`，不再拿平台的
   `supportedModes` 过滤；`supportedModes.contains` 在主页只允许出现 1 次（切平台
   时的默认跟随），在悬浮窗 0 次。
④ 引擎不换玩法：`reconcile_mode_platform` 对每个「平台 × 非房卡玩法」都必须原样
   返回该玩法（并把它报成 off_catalog）；源码里不许再出现回落 default_mode 的写法。

运行：py -3.10 -X utf8 localtest/test_ui_wording_guard.py
变异：py -3.10 -X utf8 localtest/test_ui_wording_guard.py --mutate
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
from platforms import PLATFORMS  # noqa: E402

MUTATE = "--mutate" in sys.argv
HOME = os.path.join(REPO, "lib", "home_page.dart")
OVERLAY = os.path.join(REPO, "lib", "overlays", "mahjong_overlay.dart")
SESSION = os.path.join(REPO, "lib", "session_store.dart")
LICENSE = os.path.join(REPO, "lib", "license", "license_gate.dart")


def src(path):
    if not os.path.exists(path):
        raise AssertionError(f"源文件不存在，守卫在测空气：{path}")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ① 文案：(说明, 必须出现的字面量, 必须消失的字面量)
WORDING = [
    ("顶栏状态词用「启动中」", "'启动中'", "'推演中'"),
    ("两个概率开关用用户看得懂的名字", "'运势概率'", "'气运推演'"),
    ("进张那一档改叫好牌概率", "'好牌概率'", "'进张期望'"),
    ("启动按钮说的是起进程，不是起推演", "'启动实时进程'", "'启动实时推演'"),
    ("主页底部那句「本地推理流就绪」已删", "'视觉感知核待命'", "本地推理流就绪"),
    ("卡密页不再声称离线", "硬件指纹单向哈希 · 签名加密", "离线签名加密"),
]


def wording_violations(texts):
    """texts: {文件: 源码}。返回违反 ① 的条目。"""
    bad = []
    for what, need, gone in WORDING:
        blob = "".join(texts.values())
        if need not in blob:
            bad.append(f"{what}：源码里找不到 {need}")
        if gone in blob:
            bad.append(f"{what}：旧写法 {gone} 还在（说明另有一条路径没改）")
    return bad


class TestUiWording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = src(HOME)
        cls.overlay = src(OVERLAY)
        cls.session = src(SESSION)
        cls.license = src(LICENSE)
        cls.texts = {"home": cls.home, "overlay": cls.overlay,
                     "session": cls.session, "license": cls.license}

    def test_wording_is_the_requested_one(self):
        """① 新词在、旧词绝迹。"""
        bad = wording_violations(self.texts)
        self.assertEqual(bad, [], "文案不符合本轮要求：\n  " + "\n  ".join(bad))

    def test_game_id_is_required_at_lock_time(self):
        """② 必填不只是一句提示：锁定那条路必须真的查它。"""
        self.assertIn("static String? require(", self.session,
                      "SessionStore 没有 require 这条必填判据")
        # 那句话只有一个来源（定义在 SessionStore，主页三处引用它）：同一个坑
        # 一会儿叫「必填」一会儿叫「请填」，用户就不知道到底缺没缺。
        self.assertIn("static const String requiredNotice", self.session,
                      "必填提示没有单一来源")
        self.assertIn("SessionStore.requiredNotice", self.home,
                      "主页没在用那条单一来源的必填提示")
        # 锁定配置那一段里必须调用 require，而不是只在 onChanged 里显示一句
        body = self.home[self.home.index("Future<void> _confirmMatchConfig"):]
        body = body[:body.index("\n  }\n")]
        self.assertIn("SessionStore.require(", body,
                      "「锁定对局配置」没查对局ID是否为空 —— 提示写必填却仍能空着锁定")
        self.assertIn("对局/玩家ID（必填）", self.home, "输入框提示没写必填")

    def test_mode_list_is_not_filtered_by_platform(self):
        """③ 玩法列表全量：房卡清单只剩「切平台默认跟随」这一处用途。"""
        self.assertEqual(
            self.home.count("supportedModes.contains"), 1,
            "主页里 supportedModes.contains 应只剩切平台那一处；多出来的就是又在筛列表")
        self.assertIn("final categoryModes = GameMode.allModes", self.home,
                      "主页玩法卡片不再直接从全目录按分类取，疑似又把平台过滤加了回来")
        self.assertNotIn("supportedModes", self.overlay,
                         "悬浮窗菜单又拿平台房卡筛玩法了：用户要的是全部 19 种都能选")
        self.assertIn("final modes = GameMode.allModes;", self.overlay,
                      "悬浮窗菜单的列表来源变了")

    def test_hint_row_cannot_grow_unbounded(self):
        """D29：提示条的文案必须有限行高，否则长提示会把下方记牌器行压住。

        本机没有 Flutter SDK，看不了渲染，所以从 Python 侧钉源码级约束：提示条那个
        `Text(hint, …)` 必须带 `maxLines` 与 `overflow: TextOverflow.ellipsis`。
        没这两样时，一行写不下的提示会自己长高；悬浮窗高度小于内容时，多出的部分
        就叠到记牌器行上（用户报的“提示条压在记牌器行上”）。
        """
        with open(OVERLAY, encoding="utf-8") as fh:
            src = fh.read()
        i = src.find("Text(\n                    hint,")
        self.assertGreater(i, 0, "提示条的 Text(hint…) 找不到了：布局被重写过，需重测")
        block = src[i:i + 900]
        self.assertIn("maxLines:", block,
                      "提示条没有行数上限：长提示会把记牌器行压住（D29 复发）")
        self.assertIn("TextOverflow.ellipsis", block,
                      "提示条没有省略号：超长按默认行为会裁切或换行长高")

    def test_hand_row_survives_every_phase_ui(self):
        """D25：三个阶段专用 UI 都不得把手牌块挤掉。

        本机没有 Flutter SDK，改不了也看不了布局，所以从 Python 侧钉源码级契约：
        换牌/选牌/定缺三个分支各自都要插入 `handRowWidget`。以前它们整块 return，
        面板在那些阶段就完全看不到手牌（用户报的「手牌块被挤掉、时有时无」）。
        数量也钉住：少于 3 处 = 某个阶段又把手牌丢了；多于 3 处 = 插入点跑到了
        其他分支，会变成重复渲染。
        """
        with open(OVERLAY, encoding="utf-8") as fh:
            src = fh.read()
        n = src.count("if (handRowWidget != null) handRowWidget,")
        self.assertEqual(n, 3,
                         f"阶段专用 UI 里手牌行插入点应为 3 处（换牌/选牌/定缺），实为 {n}")
        self.assertIn("final Widget? handRowWidget", src,
                      "手牌行的统一构造被删了：三处插入会变成三份不一致的实现")
        self.assertIn("Flexible(", src, "手牌行插入丢了 Flexible：可能撑溢出")

    def test_engine_honors_the_declared_mode(self):
        """④ 引擎对每个平台的非房卡玩法都原样返回，并把它报成 off_catalog。"""
        for key, p in PLATFORMS.items():
            off = [m for m in ("sc_hz", "std_tdh", "gd_hz", "not_a_mode")
                   if m not in p["supported_modes"]]
            for m in off:
                self.assertEqual(E.reconcile_mode_platform(m, key), (m, m),
                                 f"{key}×{m} 不在房卡里，引擎却改了生效玩法或没报出来")
        # 源码层再钉一次：不许留着「回落 default_mode」那条实现（防止有人只改测试）
        fn = src(os.path.join(REPO, "android", "app", "src", "main", "python",
                              "engine", "engine.py"))
        body = fn[fn.index("def reconcile_mode_platform"):]
        body = body[:body.index("\ndef ")]
        self.assertNotIn("default_mode", body,
                         "房卡核对里又出现 default_mode：那是在替用户换玩法")
        self.assertIn('"mode_off_catalog"', fn, "payload 不再交 mode_off_catalog")


class TestMutationControls(unittest.TestCase):
    """变异体：把坏写法塞回去，上面的断言必须立刻红。"""

    def test_mutant_requiring_removed_is_caught(self):
        """把锁定路径里的 require 调用删掉 → ② 必须红。"""
        home = src(HOME).replace("SessionStore.require(normalizedId)",
                                 "SessionStore.validate(normalizedId)", 1)
        body = home[home.index("Future<void> _confirmMatchConfig"):]
        body = body[:body.index("\n  }\n")]
        self.assertNotIn("SessionStore.require(", body, "变异无效：没删掉必填调用")
        self.assertIn("static const String requiredNotice", src(SESSION),
                      "必填判据本身还在，本变异的前提不成立")

    def test_mutant_filtered_mode_list_is_caught(self):
        """把平台过滤加回悬浮窗菜单 → ③ 必须红。"""
        overlay = src(OVERLAY).replace(
            "final modes = GameMode.allModes;",
            "final modes = GameMode.allModes\n"
            "      .where((m) => (pf?.supportedModes ?? []).contains(m.key))\n"
            "      .toList();", 1)
        self.assertIn("supportedModes", overlay, "变异无效：没把过滤加回去")
        self.assertNotIn("final modes = GameMode.allModes;", overlay,
                         "变异无效：列表来源没变")

    def test_mutant_silent_substitution_is_caught(self):
        """把「回落平台默认」写回房卡核对 → ④ 必须红（对真实函数生效）。"""
        orig = E.reconcile_mode_platform

        def old(m, p):
            sup = PLATFORMS[p]["supported_modes"]
            return (m, None) if m in sup else (PLATFORMS[p]["default_mode"], m)

        E.reconcile_mode_platform = old
        try:
            got = E.reconcile_mode_platform("sc_hz", "gd_queshen")
        finally:
            E.reconcile_mode_platform = orig
        self.assertNotEqual(got, ("sc_hz", "sc_hz"),
                            "变异体没改变行为：④ 在测空气")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：删掉必填调用、把平台过滤加回菜单、"
              "把回落默认写回房卡核对 —— 三条各自必须让对应主断言变红")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
