# -*- coding: utf-8 -*-
"""用户 2026-10 第二批报障（10 帧真机）的逐帧守卫：读数、字牌、阶段，一次钉住。

为什么单独一份而不并进 `test_multi_hand_guard`：那 20 帧是「修完之后」的回归基线，
这一批是**带着新发现的缺陷**进来的。把两者混在一张表里，要么逼着现在就把缺陷修完
（做不到），要么把缺陷写成 ok（假绿灯）。所以缺陷单独进 `KNOWN_DEFECTS`，形状变了
或修好了都会红 —— 双向棘轮，证据不许溜。

真值怎么来的（顺序不能反）：
  1. 人眼读屏（10 帧逐帧）；
  2. 字牌那几张再用 `build/crop_view.py` 放大复核（`東/北/北/發` 四张就是这么定的，
     顺带确认了本项目字牌编号是日麻序：5z=白、6z=發、7z=中）；
  3. 最后才拿引擎读数对。

钉四件事：
① 手牌多重集逐张等于人眼真值（缺陷帧除外，由 ③ 钉）。
② **字牌不许因为玩法而消失**：广东雀神那三帧挂着川麻玩法（牌集只放开 7z），
   屏上的 東/北 必须被「放开闸门重打分」读回来，并且每一张都在 `hand_gate_conflict`
   里留痕（用户报障原话「无法正确识别 7 张字牌」的真根因）。
③ `KNOWN_DEFECTS` 双向棘轮：错读/漏判必须错得和登记时一模一样。
④ 阶段真值：定缺/换三张/局中三态必须逐帧等于人眼判定（漏判的帧走 ③ 的台账）。

运行：py -3.10 -X utf8 localtest/test_report_frames_guard.py
"""
from __future__ import annotations

import collections
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

import diag_report_frames as DR  # noqa: E402
import layer_cost as lc  # noqa: E402

SHOT_DIR = DR.SHOT_DIR

# 人眼真值：(文件, 平台, 玩法, 手牌多重集, 屏上阶段)
# 阶段取值：dingque=定缺选门、swap=换三张、play=局中（出牌/摸牌/已下叫/结算）
#
# 真值怎么定的（改错过一次，记在这）：上一版阶段是我**看整屏缩略图**填的，把两帧填错
# 了：`shushan_swap_02` 被标成 dingque（它的色盘带里根本没有盘，屏上写的是「选择三张同
# 花色手牌」+ 圆形换牌按钮），`zj_popup_01` 跟着**面板当时错误的显示**标成了 swap。
# 现在阶段一律看 `localtest/montage_phase_bands.py` 出的中央证据带（文字/按钮都在那），
# 而不是看面板——拿被检者的输出当真值，就是自证。
TRUTH = [
    ("shushan_dingque_01.jpg", "shushan", "sc_hz",
     ["1m", "2m", "2m", "3m", "3m", "3m", "4m", "4m", "5m", "5m", "5m", "7m", "7m"],
     "dingque"),
    ("shushan_swap_01.jpg", "shushan", "sc_hz",
     ["1m", "2m", "3m", "3m", "3m", "4m", "5m", "5m", "5m", "7m"], "swap"),
    ("shushan_swap_02.jpg", "shushan", "sc_hz",
     ["1m", "1p", "2m", "3m", "3m", "3m", "4m", "5m", "5m", "5m", "5p", "6p", "7m"],
     "swap"),
    ("jj_play_02.jpg", "jj", "sc_hz",
     ["2m", "2s", "3p", "4m", "4p", "5p", "5s", "6m", "7p", "8m", "8p", "8s", "9p"],
     "play"),
    # 雀神三帧：字牌 東(1z)/北(4z)/發(6z) 由 build/crop_view.py 放大复核过。
    ("queshen_play_03.jpg", "gd_queshen", "sc_hz",
     ["1p", "1z", "3s", "4z", "4z", "5s", "6p", "6s", "6z", "8p", "9p", "9p", "9p", "9s"],
     "play"),
    ("queshen_play_04.jpg", "gd_queshen", "sc_hz",
     ["1p", "1z", "3s", "4z", "4z", "5s", "6p", "6s", "8p", "9p", "9p", "9p", "9s"],
     "play"),
    ("queshen_play_05.jpg", "gd_queshen", "sc_hz",
     ["1p", "1z", "3s", "4z", "4z", "5s", "6p", "6s", "8p", "9p", "9p", "9p", "9s"],
     "play"),
    ("zj_popup_01.jpg", "zj_sichuan", "sc_hz",
     ["1m", "1m", "1s", "1s", "1s", "2m", "2m", "2m", "2s", "2s", "2s", "7z", "7z"],
     "play"),
    ("zj_anomaly_01.jpg", "zj_sichuan", "sc_hz",
     ["1m", "1m", "1s", "1s", "1s", "2m", "2m", "2m", "2s", "2s", "2s", "7z", "7z"],
     "play"),
    ("zj_play_02.jpg", "zj_sichuan", "sc_hz",
     ["1m", "1m", "1s", "1s", "1s", "2m", "2m", "2m", "2s", "2s", "2s", "7z", "7z"],
     "play"),
]

# 已知缺陷台账（双向棘轮）。key = 文件，value = 当前引擎的**错误**输出。
# 每一条都写着「错在哪、为什么还没修」——修好后必须删条目，否则本守卫报红。
KNOWN_HAND_DEFECTS = {
    # 检测层漏框（不是分类）：第 14 张「發」压根没进手牌行。
    # 真身已量到：不是 `drawn_box` 那条 `sc_d >= 0.48`（那条已能救），而是
    # `detect_hand_strip` 里的**张数候选打分**：`_try_counts` 比 13/14 哪个假设均分高，
    # 而第 14 张在川麻门内只有 0.37 → 把 14 档的均分拉下去 → 选了 13 档。
    # 实测：同一帧全牌玩法 14 框、川麻玩法 13 框（`probe_geometry_purity.py`）。
    # 要动的是全仓最承重、守卫最密的那个函数，不能顺手改；改完必须逐格对拍。
    "queshen_play_03.jpg": ["1p", "1z", "3s", "4z", "4z", "5s", "6p", "6s",
                            "8p", "9p", "9p", "9p", "9s"],
    # 全屏弹窗（“购买麻卡解锁记牌器”）压暗手牌带 → 一个框都没检出。
    # 本轮修的是“不说谎”：这一帧现在报「画面被弹窗或暗层压住，本帧读不到手牌」
    # 而不是「等待牌局开始」（见下面的 test_dim_frame_says_why_it_cannot_read）。
    # 真把暗层下的牌读出来需要自适应归一化，那会动牌面掩膜本身，未做。
    "zj_popup_01.jpg": [],
}
KNOWN_PHASE_DEFECTS = {
    # 屏上是换三张（「选择三张同花色手牌」+ 右侧圆形换牌按钮），引擎没认出来：
    # 现有 `is_swap_phase` 只认腾讯那块低带金色扁圆盘，蜀山的按钮在右侧中部。
    #
    # 为什么本轮没直接加一条“跨平台几何通路”：拿“桌面中带里的白色近圆盘”试过，
    # **被实测否证**（`localtest/sweep_swap_button.py`，67 帧）：同一形状在 10 帧
    # 非换牌帧上照样出现（蜀山定缺帧 3 个、蜀山局中帧 3 个、JJ 局中帧 1 个，
    # 位置与真那个换牌按钮几乎重叠，如 shushan_play_03 的 (0.691,0.641) vs
    # shushan_swap_02 的 (0.672,0.638)）。拿它上屏就是把“不在换牌却显示换牌”
    # 亲手造出来——那正是用户报的第 ② 条。剩下的可用差异只有按钮里那两个字，
    # 需要 OCR 或模板，不是调阈值能得到的。
    "shushan_swap_01.jpg": "play",
    # 同上：同一局的另一帧换三张（带圆形换牌按钮），一样漏判。
    "shushan_swap_02.jpg": "play",
    # 全屏弹窗（“购买麻卡解锁记牌器”）+ 结算画面：引擎读不出手牌。
    # 本轮修的是“不说谎”：阶段仍报 waiting，但消息从「等待牌局开始」改成
    # 「画面被弹窗或暗层压住，本帧读不到手牌」（见 ⑤ 与 engine 的 `HAND_BAND_DIM_V`）。
    # 把牌真读出来需要自适应归一化（会动牌面掩膜本身），未做。
    "zj_popup_01.jpg": "waiting",
}
# 上一轮台账里的「蜀山定缺圆盘召回」已删：那是一条**假缺陷**——我把一帧换三张误标成
# 定缺，然后去量“为什么定缺漏判”。重新按中央证据带定阶段后，那帧本来就是 swap，引擎
# 判 play 的错属于上面两条同一个根子（跨平台换牌判据缺失），不是定缺召回问题。


def read_frame(name):
    p = os.path.join(SHOT_DIR, name)
    img = cv2.imread(p) if os.path.exists(p) else None
    if img is None:
        raise AssertionError(f"真机夹具帧缺失/读不出来，守卫在测空气：{p}")
    return img


def panel_of(d):
    return lc.canon_mpsz(d.get("hand", ""))


class TestReportFrames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panels = {}
        for name, pf, md, hand, phase in TRUTH:
            cls.panels[name] = DR.run(read_frame(name), pf, md)

    def _d(self, name):
        return self.panels[name]

    def test_table_covers_every_reported_frame(self):
        on_disk = {f for f in os.listdir(SHOT_DIR) if f.endswith(".jpg")}
        in_table = {t[0] for t in TRUTH}
        # 目录里允许有**非牌局帧**（字牌素材图），但它必须在这里点名：
        # 默默把帧从表里删掉与默默往目录里塞图，都会让「覆盖完整」变成空话。
        non_game = {"material_bank_01.jpg"}
        self.assertEqual(on_disk - non_game, in_table,
                         f"真值与夹具目录不一致：只在表={sorted(in_table - on_disk)} "
                         f"只在盘={sorted(on_disk - in_table - non_game)}")
        for f in non_game:
            self.assertIn(f, on_disk, f"{f} 不在了：它要么该从本白名单删掉，要么素材丢了")

    def test_hand_matches_human_truth(self):
        bad = []
        for name, pf, md, hand, phase in TRUTH:
            if name in KNOWN_HAND_DEFECTS:
                continue
            got = panel_of(self._d(name))
            if got != sorted(hand):
                lost = sorted(collections.Counter(hand) - collections.Counter(got))
                extra = sorted(collections.Counter(got) - collections.Counter(hand))
                bad.append(f"{name} 缺{lost} 多{extra} 读到{got}")
        self.assertEqual(bad, [], "这些帧的面板读数不等于人眼真值：\n  " + "\n  ".join(bad))

    def test_honors_are_rescued_and_tracked(self):
        """② 字牌必须读回来，而且每张都要在 hand_gate_conflict 里查得到。"""
        for name, pf, md, hand, phase in TRUTH:
            honors = [t for t in hand if t.endswith("z") and t != "7z"]
            if not honors or md == "std_tdh":
                continue
            d = self._d(name)
            from modes import available_set
            gate = {t for t in panel_of(d) if t in
                    {_mp(i) for i in available_set(md)}}
            trail = {c[1] for c in (d.get("hand_gate_conflict") or [])}
            got = panel_of(d)
            for h in honors:
                if h in KNOWN_HAND_DEFECTS.get(name, []) or h not in got:
                    continue      # 漏框那一半由 ③ 的台账管，不在这里混
                self.assertIn(h, trail,
                              f"{name} 读出了闸门外的 {h} 却没留痕：面板凭空多出字牌")
            self.assertTrue(trail <= set(honors),
                            f"{name} 留痕里出现了屏上没有的牌：{trail} vs {honors}")

    def test_known_defects_are_exactly_the_registered_ones(self):
        """③ 双向棘轮：错读必须错得登记时一模一样。"""
        for name, wrong in KNOWN_HAND_DEFECTS.items():
            e = next((t for t in TRUTH if t[0] == name), None)
            self.assertIsNotNone(e, f"{name} 已从真值表消失，台账条目该一起删掉")
            got = panel_of(self._d(name))
            self.assertNotEqual(got, sorted(e[3]),
                                f"{name} 已经读对了：该从 KNOWN_HAND_DEFECTS 删掉")
            self.assertEqual(got, sorted(wrong),
                             f"{name} 的错读形状变了：登记={sorted(wrong)} 实为={got}")

    def test_phase_matches_screen(self):
        """④ 阶段必须等于人眼判定；漏判的帧走台账，不许悄悄放过。"""
        bad = []
        for name, pf, md, hand, phase in TRUTH:
            d = self._d(name)
            got = _phase_of(d)
            if got != phase:
                bad.append((name, phase, got))
        registered = {k: v for k, v in KNOWN_PHASE_DEFECTS.items()}
        for name, want, got in bad:
            self.assertIn(name, registered,
                          f"{name} 阶段判成 {got}、屏上是 {want}：新漏判必须显式裁决")
            self.assertEqual(registered[name], got,
                             f"{name} 的漏判形状变了（登记={registered[name]} 实为={got}）："
                             "动的是另一条链路，别把新问题塞进旧台账")
        self.assertEqual(set(bad and [b[0] for b in bad]) or set(), set(registered),
                         "台账里有已经修好的条目（或漏了新条目）")


    def test_dim_frame_says_why_it_cannot_read(self):
        """⑤ 读不到牌时，面板必须说对原因：「没开局」与「被暗层压住」是两回事。

        这条不修召回（那张帧现在仍读不出牌，台账已记），只钉“不许把未知说成事实”：
        旧行为报「等待牌局开始」，用户会以为还要去开局，而牌局正在跑。
        判据与阈值（正常帧手牌带 V 中位 173~230、该帧 62）见 engine 的 `HAND_BAND_DIM_V`。
        """
        d = self._d("zj_popup_01.jpg")
        self.assertTrue(d.get("hand_dim"),
                        "这帧手牌带 V 中位只有 62，却没报出「被压暗」：那条判据失效了")
        msg = str(d.get("message") or "")
        self.assertNotIn("等待牌局开始", msg,
                         f"把「看不清」报成「没开局」就是把未知说成事实：{msg!r}")
        self.assertTrue("压住" in msg or "读不到" in msg,
                        f"降级了却没说原因：{msg!r}")
        # 反向：正常帧不得被这条判据误伤（不然“看不清”会喊成狼来了）
        dimmed = [t[0] for t in TRUTH
                  if t[0] != "zj_popup_01.jpg" and self._d(t[0]).get("hand_dim")]
        self.assertEqual(dimmed, [], f"这些帧手牌带并不暗却被报成压暗：{dimmed}")


def _mp(i):
    from trainer.utils.convert import tiles34_index_to_mpsz
    return tiles34_index_to_mpsz(i)


def _phase_of(d):
    """payload → 本守卫的阶段口径（dingque / swap / play / waiting）。"""
    st = d.get("status")
    if st == "waiting":
        return "waiting"
    ph = (d.get("match_phase") or {}).get("key") or ""
    if st == "dingque" or "dingque" in ph:
        return "dingque"
    if "swap" in ph:
        return "swap"
    return "play"


if __name__ == "__main__":
    unittest.main()
