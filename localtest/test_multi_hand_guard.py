# -*- coding: utf-8 -*-
"""多平台真机手牌守卫：用户拍的 20 帧，面板读数必须逐张等于人眼真值。

为什么单独一份（`eval_base` 只管腾讯 37 帧、`test_hand_channel` 只管 public 素材）：
用户这轮报障的原话是「指尖四川和jj麻将、广东雀神等平台识别不精准、完整，会漏识别、
错误识别，一些特殊的牌识别不出来，比如东西南北风」。**报障里有一半（JJ / 指尖 / 途游 /
腾讯那 15 帧）在修法前后读数都是对的**——没有这张表，这句话就只能靠印象争，也没人
能证明「修雀神那两帧没把别家修坏」。

真值怎么来的（顺序很重要，反过来就是自证）：
  1. `localtest/montage_hands.py` 把 20 帧的手牌行拼成带编号联屏，人眼逐帧读；
  2. 有分歧的那一帧再用 `localtest/zoom_hand.py` 分区放大复核（tuyou_swap_01 就是这么
     定下来的：人眼初读把一对 4条 记成了 5条，放大后确认是 3 张 3条 + 2 张 4条）；
  3. 然后才拿引擎读数去对。表在 `localtest/gt/shots_multi.json`。

钉三件事：
① 每帧按**表里声明的那个玩法**跑，手牌多重集逐张等于真值（已知缺陷帧除外）。
② 生效玩法必须**逐字等于**表里声明的玩法（v1.7.5 起引擎不再替用户换玩法），且房卡
   清单内的玩法不许报 `mode_off_catalog`——否则「未收录」会喊成狼来了。
③ `KNOWN_DEFECTS` 是双向棘轮：登记在册的错读必须**错得一模一样**；哪天读对了，
   这条就报红逼着把台账删掉。留空断言/删条目当"修好了"是假绿灯。
④ 错读不许装确定：那两帧必须同时交出 `hand_uncertain`（哪几张连最像的模板都
   不够像）并且**不给建议**；而其余 18 帧（读数全对）必须一个不被标 —— 门槛
   （engine 的 `HAND_UNREADABLE_CONF`）一旦漂到把读对的帧也标红，面板就会开始
   报一个假的「遮挡」，用户很快就不再信它说的任何话。

变异检验（`py -3.10 -X utf8 localtest/test_multi_hand_guard.py --mutate`）：
  · 把 shushan 那帧的玩法换成同平台合法但字牌更窄的 sc_xz → 7z 必须掉出来
    （证明 ① 的读数真的走玩法闸门，不是缓存或写死的表）；
  · 把生效玩法写死成某一条（绕过声明值）→ 声明不是它的那些帧必须对不上（证明 ②
    在量真链路，不是把表抄一遍）；
  · 把逐张门槛改成 0（永不标）→ 那两帧必须重新给出建议（证明 ④ 的「不给建议」
    真是这条门槛拦的，不是别处恰好算空）。

运行：py -3.10 -X utf8 localtest/test_multi_hand_guard.py
"""
from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import engine.engine as E  # noqa: E402
import layer_cost as lc  # noqa: E402
from platforms import PLATFORMS  # noqa: E402

MUTATE = "--mutate" in sys.argv
GT_PATH = os.path.join(HERE, "gt", "shots_multi.json")
SHOT_DIR = os.path.join(HERE, "shots_multi")

# 已知错读台账（帧 -> 当前引擎的**错误**读数，人眼真值见 GT 表）。
# 蜀山这两帧：屏幕中段正压着「清800色 / 清9315色」大字动画，一张 4条 位置上的
# 4万 被贴成 2条（play_01 还多丢一张 7万→3万）。同一局的 play_03 没被压住，
# 读数与真值逐张一致 —— 所以真值不是猜的。
# 这条台账同时记着**第二个**问题：错读本身已经不许装确定 —— 这两帧必须自报
# `hand_uncertain` 并被可读性硬门拦下建议（见 ④）。剩下的「把遮挡牌读对」是
# 识别侧的活（模板/去遮挡），修好后删条目。
KNOWN_DEFECTS = {
    "shushan_play_01.jpg": ["2m", "2m", "3m", "3m", "3m", "2s", "4m", "3m",
                            "7m", "9m", "9m", "4m", "7z"],
    "shushan_play_02.jpg": ["2m", "2m", "3m", "3m", "3m", "2s", "4m", "7m",
                            "7m", "9m", "9m", "4m", "7z"],
}


def load_gt():
    if not os.path.exists(GT_PATH):
        raise AssertionError(f"真值表不存在，守卫在测空气：{GT_PATH}")
    with open(GT_PATH, encoding="utf-8") as f:
        return json.load(f)["shots"]


def run_frame(img, platform, mode):
    """按声明的 (平台, 玩法) 跑一帧，返回完整 payload。

    只改 engine 命名空间里的 `load_platform`/`load_mode`，**绝不写磁盘**：写磁盘会
    在仓库外留下一份全局配置，把下一轮不钉口径的评测静默改成别的平台
    （`eval_base.pin_config` 的 `ghost_configs()` 就是为这个而存在的）。
    每帧新建 Engine：跨帧复用会把上一局的牌账带进来，那测的就不是这一帧。
    """
    orig_lp, orig_lm = E.load_platform, E.load_mode
    E.load_platform = lambda *a, **k: platform
    E.load_mode = lambda *a, **k: mode
    try:
        eng = E.Engine()
        eng.get_hand_detector()
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(eng.process(img).result)
    finally:
        E.load_platform, E.load_mode = orig_lp, orig_lm


def read_frame(name):
    p = os.path.join(SHOT_DIR, name)
    img = cv2.imread(p) if os.path.exists(p) else None
    if img is None:
        raise AssertionError(f"真机夹具帧缺失/读不出来，守卫在测空气：{p}")
    return img


def panel_of(d):
    return lc.canon_mpsz(d.get("hand", ""))


class TestMultiHandGT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shots = load_gt()
        cls.panels = {}     # file -> payload（跑一次，②③ 复用，避免 20×2 次识别）

    def payload(self, e):
        if e["file"] not in self.panels:
            self.panels[e["file"]] = run_frame(read_frame(e["file"]), e["platform"], e["mode"])
        return self.panels[e["file"]]

    def test_table_covers_all_20_frames(self):
        """表必须覆盖夹具目录里的每一帧：漏一帧 = 那帧没人管。"""
        on_disk = {f for f in os.listdir(SHOT_DIR)
                   if f.endswith(".jpg") and not f.startswith("montage")}
        in_table = {e["file"] for e in self.shots}
        self.assertEqual(len(self.shots), 20, f"应有 20 帧，实为 {len(self.shots)}")
        self.assertEqual(in_table, on_disk,
                         f"真值表与夹具目录不一致：只在表={sorted(in_table - on_disk)} "
                         f"只在盘={sorted(on_disk - in_table)}")
        for e in self.shots:
            self.assertTrue(e.get("verified"), f"{e['file']} 未人眼校验，不许进表")

    def test_hand_matches_human_truth(self):
        """① 逐张等于真值（已知缺陷帧由 ③ 单独钉，不许混在这里当绿）。"""
        cls = type(self)
        bad = []
        for e in cls.shots:
            if e["file"] in KNOWN_DEFECTS:
                continue
            got = panel_of(self.payload(e))
            if got != sorted(e["hand"]):
                lost = sorted(collections.Counter(e["hand"]) - collections.Counter(got))
                extra = sorted(collections.Counter(got) - collections.Counter(e["hand"]))
                bad.append(f"{e['file']}({e['platform']}/{e['mode']}) 缺{lost} 多{extra} "
                           f"读到{got}")
        self.assertEqual(bad, [], "这些帧的面板读数不等于人眼真值：\n  " + "\n  ".join(bad))

    def test_effective_mode_is_the_declared_one(self):
        """② 生效玩法 == 声明玩法，无一例外；房卡内的玩法不许报「未收录」。"""
        for e in type(self).shots:
            d = self.payload(e)
            self.assertNotIn("effective_mode", e,
                             "表里不该再有 effective_mode 例外：引擎已不替用户换玩法")
            self.assertEqual(d.get("mode"), e["mode"],
                             f"{e['file']} 声明 {e['mode']}，引擎却按 {d.get('mode')} 跑："
                             "v1.7.5 之后玩法由用户定，引擎只负责把牌集照他说的装上")
            if e["mode"] in PLATFORMS[e["platform"]]["supported_modes"]:
                self.assertIsNone(d.get("mode_off_catalog"),
                                  f"{e['file']} 的 {e['mode']} 就在这家房卡里却被报未收录："
                                  "一个天天误报的提示很快就会被用户忽略")

    def test_known_defects_are_exactly_the_registered_ones(self):
        """③ 双向棘轮：错读必须错得登记时一模一样；读对了就该删条目。"""
        for name, wrong in KNOWN_DEFECTS.items():
            e = next((x for x in type(self).shots if x["file"] == name), None)
            self.assertIsNotNone(e, f"{name} 已从真值表里消失，台账条目该一起删掉")
            got = panel_of(self.payload(e))
            self.assertNotEqual(got, sorted(e["hand"]),
                                f"{name} 已经读对了：这条已知缺陷该从 KNOWN_DEFECTS 删掉"
                                "（留着就是历史，不是现状）")
            self.assertEqual(got, sorted(wrong),
                             f"{name} 的错读形状变了：登记={sorted(wrong)} 实为={got}。"
                             "形状变了说明动的是另一条链路，别把新问题塞进旧台账")

    def test_uncertainty_is_announced_and_advice_is_pulled(self):
        """④ 错读不许装确定：被压住的那两帧必须自报不可信并不给建议，其余帧零误报。"""
        silent, false_alarm = [], []
        for e in type(self).shots:
            d = self.payload(e)
            unc = d.get("hand_uncertain") or []
            if e["file"] in KNOWN_DEFECTS:
                if not unc:
                    silent.append(e["file"])
                    continue
                self.assertEqual(d.get("advice") or [], [],
                                 f"{e['file']} 已自报不可信却仍给建议："
                                 "面板上一句错建议的代价大于少一句（引擎的可读性硬门）")
                self.assertEqual(d.get("best") or "", "",
                                 f"{e['file']} advice 空了但 best 还留着：两头口径不一致")
                self.assertIn("不给建议", str(d.get("message") or ""),
                              f"{e['file']} 降级了却没说为什么：{d.get('message')!r}")
            elif unc:
                false_alarm.append(f"{e['file']} 被标 {unc}")
        self.assertEqual(silent, [],
                         f"这些帧错读了却没自报不可信（还在装确定）：{silent}")
        self.assertEqual(false_alarm, [],
                         "读数全对的帧被标成不可信：门槛漂了。误报「遮挡」比不报更糟——"
                         "它会把一个真信号喊成狼来了：\n  " + "\n  ".join(false_alarm))


class TestMutationControls(unittest.TestCase):
    """变异体：证明上面的断言在测真链路，不是在抄表。"""

    def test_mutant_narrower_gate_drops_the_laizi(self):
        """同平台换一条合法但字牌更窄的玩法（sc_xz 连 7z 都不放开）→ 7z 必须掉。"""
        e = next(x for x in load_gt() if x["file"] == "shushan_play_03.jpg")
        gt = panel_of(run_frame(read_frame(e["file"]), e["platform"], "sc_hz"))
        self.assertIn("7z", gt, "夹具帧本身没有 7z，本对照不成立")
        narrow = panel_of(run_frame(read_frame(e["file"]), e["platform"], "sc_xz"))
        self.assertNotIn("7z", narrow,
                         f"换成更窄的牌集后面板仍有 7z（{narrow}）：玩法根本没进闸门，"
                         "① 的读数与表怎么对上都说明不了识别正确")

    def test_mutant_hardcoded_mode_breaks_the_wiring(self):
        """把生效玩法写死成 std_tdh（绕过声明值）→ 声明不是它的帧必须对不上。

        ② 若只把表里的值抄回来，写死也不会红；红了就说明量的是引擎真在按哪个玩法跑。"""
        rows = [x for x in load_gt() if x["mode"] != "std_tdh"]
        self.assertTrue(rows, "表里全是 std_tdh，本对照不成立")
        e = rows[0]
        orig = E.reconcile_mode_platform
        E.reconcile_mode_platform = lambda m, p: ("std_tdh", None)
        try:
            d = run_frame(read_frame(e["file"]), e["platform"], e["mode"])
        finally:
            E.reconcile_mode_platform = orig
        self.assertEqual(d.get("mode"), "std_tdh",
                         "写死生效玩法却没改变 payload：② 读的不是这条链路")
        self.assertNotEqual(d.get("mode"), e["mode"],
                            "写死的值竟等于声明值：本行对照不成立，换一行")

    def test_mutant_never_flag_breaks_the_readability_gate(self):
        """逐张门槛改成 0（永不标）→ 被动画压住的那帧必须重新给出建议。

        同时钉住两头：`hand_uncertain` 不是装饰（它真在拦建议），而建议也不是
        被别的无关环节算空的（门槛一改就回来了）。
        """
        name = "shushan_play_01.jpg"
        e = next(x for x in load_gt() if x["file"] == name)
        base = run_frame(read_frame(name), e["platform"], e["mode"])
        self.assertTrue(base.get("hand_uncertain"), "基线帧本身没被标，本对照不成立")
        self.assertFalse(base.get("advice"), "基线帧已在给建议：④ 的前提不对")
        orig = E.HAND_UNREADABLE_CONF
        E.HAND_UNREADABLE_CONF = 0.0
        try:
            d = run_frame(read_frame(name), e["platform"], e["mode"])
        finally:
            E.HAND_UNREADABLE_CONF = orig
        self.assertFalse(d.get("hand_uncertain"),
                         "门槛改成 0 后仍被标：这个字段不是从逐张分数来的")
        self.assertTrue(d.get("advice"),
                        "不标了却仍不给建议：那「本帧不给建议」不是这条门槛拦的，"
                        "④ 把功劳记错了人")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：换窄牌集必须丢 7z、写死生效玩法必须让声明对不上、"
              "逐张门槛改 0 必须重新给建议")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
