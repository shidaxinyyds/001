# -*- coding: utf-8 -*-
"""真机四帧阶段守卫：把用户回传的截图固化成「阶段/手牌/定缺」的可执行契约。

背景（用户反馈，附 4 张真机截图）：
  1. 识别与响应显示慢；
  2. 明明有手牌，却莫名其妙显示「等待牌局开始」；
  3. 牌局感知错乱——不在换牌阶段却显示换牌，反之亦然，各阶段都错乱；
  4. 牌面识别不精准不完整，明明 13 张却只显示 5 张。

四帧取自同一局（腾讯欢乐麻将小程序 · 血流红中 · 2000x899）：
  s1 换三张选牌中 倒计时17     s2 同 倒计时07     s3 同 倒计时01
  s4 换三张已结束、庄家已摸牌 倒计时04
改前实测（见 `localtest/_diag_select_frames.py` 与 build/diag_sel.txt）：识别层
13/13、14/14 全对，错的全在阶段层 —— s1~s3 报「候牌中 + 已出完这一张」（玩家一张
没打，纯臆断），s4 报「换三张」。

分四层，每层都对应一条**实测到证据**的真实故障，不是快照：
  A. 换牌按钮视觉判据（合成图，不依赖 bank）：只认「手牌行正上方低带里那个孤岛扁
     圆盘」。牌河中带的杠/碰金色、单独的青色「过」都不算；**加载界面那片铺满的沙滩
     金**也不算（占比 0.0673 越过 0.05 门槛那条老路已实测证伪，见 A 组）。
  B. 定缺三色盘几何（合成图）：色盘边长必须按取区宽度归一。绝对面积上限在
     2000x899 真机帧上把筒盘挡在门外（实测轮廓面积 12763 > 上限 10000），于是
     「定缺中」明明在屏幕中央却漏判 —— 用户说的「各个阶段都非常错乱」的其中一条。
  C. 局况文案诚实性（状态机合成观测）：张数只配推「轮不轮到你」，不配编造
     「已出完这一张」；定缺事件只认徽章/手动值，推荐值不得冒充敲定，锁存后
     改口只计不播。
  D. 真机四帧全链路 GT：逐帧断言 status / phase / count / hand / 阶段标志一致性，
     并钉住「读不到的徽章不得把阶段带进定缺」这条不变量。

已知边界（如实记录，不在本守卫里假装解决）：自家定缺徽章的取区在面板展开时会被
自家悬浮窗压住，此时 `detect_dingque` 读出的是面板 chip 的颜色，不是牌桌事实
（真机 s2 报筒、s4 报条）。像素启发式判「自噬」已被实测证伪并拆除（六条特征在
「真徽章」与「面板 chip」两组上全部重叠）。唯一诚实的补法是**面板矩形事实源**
（Java 已知窗口的绝对左上偏移与宽高，推出归一矩形推给引擎，相交即报「没读到」）；
它涉及 Dart→主引擎→Java→Python 四跳与旋转映射，**必须真机验证**，不能在没有设备
的情况下凭推理上线，故留待下一次带真机回归一起做。当前由三道次级防线兜住：
徽章读数不参与阶段判定（D 组断言）；match_state 只认已锁存的缺门、改口只计不播
（C 组断言）；读数必须**时间稳定**才可信（同一窗口内后一枪推翻前一枪就整体判
「没读到」，见 `test_dingque_stability_guard.py`）——它只挡抽搐，不挡持续遮挡。

运行：
  py -3.10 -X utf8 localtest/test_real_phase_frames_guard.py
  py -3.10 -X utf8 localtest/test_real_phase_frames_guard.py --mutate
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import textwrap
import types
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PY_DIR = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

MUTATE = "--mutate" in sys.argv

DET_PY = os.path.join(PY_DIR, "recognition", "tencent_grid_detector.py")
ENGINE_PY = os.path.join(PY_DIR, "engine", "engine.py")
STATE_PY = os.path.join(PY_DIR, "engine", "match_state.py")
OVERLAY_DART = os.path.join(REPO, "lib", "overlays", "mahjong_overlay.dart")
SHOTS = os.path.join(HERE, "shots_tuyou_select")


def _read(p):
    with open(p, encoding="utf-8") as fp:
        return fp.read()


DET_SRC = _read(DET_PY)
ENGINE_SRC = _read(ENGINE_PY)
STATE_SRC = _read(STATE_PY)
OVERLAY_SRC = _read(OVERLAY_DART)

from engine.engine import detect_dingque, _detect_badge_suit  # noqa: E402
import recognition.tencent_grid_detector as TGD  # noqa: E402  (变异检验要它的命名空间)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from engine.match_state import MatchPhaseMachine  # noqa: E402

# 真机帧尺寸（横屏 2000x899）；合成图沿用同一比例，让百分比取区在两种图源上一致。
H, W = 899, 2000
GOLD_BGR = (30, 200, 230)      # HSV≈(25, 222, 230)：落在金按钮窗口内
CYAN_BGR = (235, 210, 60)      # 局中「过」按钮的青色
RED_BGR = (40, 40, 220)        # 万盘
GREEN_BGR = (40, 210, 40)      # 条盘
ORANGE_BGR = (30, 140, 230)    # 筒盘（腾讯版是橙黄，蜀山版才是蓝）


def swap_probe(img):
    """直接跑视觉判据本体。方法体不触碰 self，所以不必为此加载模板库。"""
    return TencentGridDetector.is_swap_phase(None, img)


def dq_probe(img):
    return TencentGridDetector.is_dingque_phase(None, img)


def _strip_comments(body: str) -> str:
    """去掉整行注释（包括缩进注释），只留代码。

    为什么需要：生产源码的注释会**如实引用**被拆掉的坏写法（例如「旧写法
    400 <= area <= 10000 在真机帧上漏判」），拿全文做 assertNotIn 会被自己的
    注释打红——那种红只会教人把断言删掉，反而把守卫削弱了。"""
    return "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))


def _det_method_src(name: str) -> str:
    """从文件里切出一个方法的完整定义（含原有 4 格缩进）。

    守卫用它做源码断言，变异检验用它 exec 出一个只改坏一个方法体的副本，
    两边读的是同一段文本，不会一边绿一边默。方法体内若出现嵌套 `def`，
    它的缩进是 8 格，不会被 `"\\n    def "` 误判成下一个方法。"""
    head = f"    def {name}("
    start = DET_SRC.index(head)
    end = DET_SRC.index("\n    def ", start + len(head))
    return DET_SRC[start:end]


def blank(h=H, w=W):
    return np.zeros((h, w, 3), np.uint8)


def _hexagon_blob(img, x0, y0, sw, sh, color):
    """画一个「切掉四个角」的扁圆盘，extent=(1-0.28)=0.72，与真机换牌按钮同档。

    为什么不用 cv2.ellipse：椭圆 extent=π/4≈0.785，落不进实测窗口
    [0.55,0.75]；真机那个按钮是带切角/镂空的牌面，实测 extent 0.651~0.675。
    """
    pts = np.array([[x0 + int(0.28 * sw), y0],
                    [x0 + int(0.72 * sw), y0],
                    [x0 + sw, y0 + int(0.5 * sh)],
                    [x0 + int(0.72 * sw), y0 + sh],
                    [x0 + int(0.28 * sw), y0 + sh],
                    [x0, y0 + int(0.5 * sh)]], np.int32)
    cv2.fillPoly(img, [pts], color)
    return img


# 换牌按钮低带（与判据同源：y 0.74~0.88、x 0.64~0.78）在 2000x899 上的像素框。
BAND_X0, BAND_Y0 = int(W * 0.64), int(H * 0.74)
BAND_W, BAND_H = int(W * 0.78) - BAND_X0, int(H * 0.88) - BAND_Y0

# ---------------------------------------------------------------- 合成夹具
# 抽成模块级函数而不是写在测试方法里：下面的变异检验要用**同一批**图去跑改坏的
# 代码，两边各画一图迟早会漂移成「变异没人接住但守卫还是绿的」。


def fx_swap_button():
    """真机换牌按钮：低带里那个孤岛扁圆盘（实测 133x40，宽高比 3.3）。"""
    return _hexagon_blob(blank(), BAND_X0 + 30, BAND_Y0 + 25, 133, 40, GOLD_BGR)


def fx_action_band():
    """局中 杠/碰/胡 那一排，中心实测 y≈0.678，必须在低带窗外。"""
    return cv2.circle(blank(), (int(0.690 * W), int(0.678 * H)), 40, GOLD_BGR, -1)


def fx_cyan_pass():
    """局中「过」的青色按钮：旧判据把它当必要条件，换牌弹窗里实测 cyan=0.0。"""
    return cv2.circle(blank(), (int(0.745 * W), int(0.660 * H)), 40, CYAN_BGR, -1)


def fx_gold_speck():
    return cv2.circle(blank(), (BAND_X0 + 140, BAND_Y0 + 60), 4, GOLD_BGR, -1)


def fx_loading_sheet():
    """加载大厅那片铺满低带的沙滩金（真机误报原型，aspect/extent/高占比全出格）。"""
    img = blank()
    img[BAND_Y0:BAND_Y0 + int(BAND_H * 0.10), BAND_X0:BAND_X0 + BAND_W] = GOLD_BGR
    return img


def fx_tall_strip():
    return _hexagon_blob(blank(), BAND_X0 + 100, BAND_Y0 + 10, 22, 100, GOLD_BGR)


def fx_discs(w, h, r, gap_factor=1.0, cols=(RED_BGR, GREEN_BGR, ORANGE_BGR)):
    """在定缺取区（y 0.40~0.75、x 0.25~0.75）画三盘，默认 万(红)/条(绿)/筒(橙)。

    `cols` 可换顺序：各家平台把三盘摆成什么次序并不统一，而腾讯旧取区是「红在绿左、
    绿在橙左」的**顺序**判据 —— 反序帧只有跨平台几何通路认得出，所以它得有独立的
    变异对照（否则几何通路被删空也照样全绿）。"""
    img = np.full((h, w, 3), (60, 60, 60), np.uint8)
    cy = int(h * 0.575)
    sub_w = w * 0.50
    x0 = int(w * 0.25 + sub_w * 0.15)
    step = int((r * 2 + max(8, r * 0.9)) * gap_factor)
    for k, col in enumerate(cols):
        cv2.circle(img, (x0 + k * step, cy), r, col, -1)
    return img


def fx_mid_specks():
    """三个中等色块：轮廓面积已过 400 的绝对下限，但边长不足取区宽的 3.5%。

    这一组是「相对下限」的存在理由——只把它改成 0，桌布花纹/头像角标这种
    中小色块就会凑成一行被认成定缺三色盘。
    """
    img = np.full((H, W, 3), (60, 60, 60), np.uint8)
    for k, col in enumerate((RED_BGR, GREEN_BGR, ORANGE_BGR)):
        cv2.circle(img, (int(W * (0.30 + 0.06 * k)), int(H * 0.575)), 12, col, -1)
    return img


def fx_specks():
    """碎点：轮廓面积连 400 的地板都上不去。"""
    img = np.full((H, W, 3), (60, 60, 60), np.uint8)
    for k, col in enumerate((RED_BGR, GREEN_BGR, ORANGE_BGR)):
        cv2.circle(img, (int(W * (0.30 + 0.12 * k)), int(H * 0.575)), 6, col, -1)
    return img


# ------------------------------------------------- 断言（守卫与变异检验共用）
# 接一个 probe 而不是写死实现：默认套件传生产代码，变异检验传「只改坏一处」的
# 副本。同一个断言两边跑，才能问出「那段代码真的在生效吗」而不是「字还在吗」。


def _assert_swap_button_is_swap(probe):
    assert probe(fx_swap_button()), "换牌按钮位置的金色扁圆盘没被认成换牌阶段"


def _assert_action_band_is_not_swap(probe):
    assert not probe(fx_action_band()), "牌河中带的动作按钮又被打成了换三张"


def _assert_cyan_is_not_swap(probe):
    # 旧判据把青色「过」当必要条件：换牌弹窗里根本没有它（实测 cyan=0.0），
    # 于是 s1~s3 整屏漏判成「候牌中」。它现在必须什么都不是。
    assert not probe(fx_cyan_pass()), "青色「过」又被当成换牌证据"


def _assert_speck_is_not_swap(probe):
    assert not probe(fx_gold_speck()), "门槛低到几颗金点就算换牌阶段"


def _assert_loading_sheet_is_not_swap(probe):
    """真机故障原型：「正在加载资源」大厅的沙滩铺满低带，占比 0.0673 越过
    0.05 门槛，被报成换三张（build/pf.txt）。它形状上是整片贴底背景：
    实测 aspect 15.23 / extent 0.922 / 高度只占带高 0.103 —— 三项全出格。"""
    img = fx_loading_sheet()
    zone = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[
        BAND_Y0:BAND_Y0 + BAND_H, BAND_X0:BAND_X0 + BAND_W]
    gold = ((zone[:, :, 0] >= 14) & (zone[:, :, 0] <= 35)
            & (zone[:, :, 1] >= 120) & (zone[:, :, 2] >= 150))
    ratio = int(np.count_nonzero(gold)) / float(BAND_W * BAND_H)
    assert ratio >= 0.05, "夹具不再满足「只看占比会误判」这个前提，本测试的存在理由变了"
    assert not probe(img), "整片沙滩背景又被当成换牌按钮"


def _assert_strip_is_not_swap(probe):
    # 竖条（侧栏装饰/进度条）：aspect 与占比都可能在窗口外，但必须被形状判据挡掉
    assert not probe(fx_tall_strip()), "竖条金色被当成换牌按钮"


def _assert_big_discs_are_dingque(probe):
    # 2000x899 真机帧：筒盘实测外接框 159x162、轮廓面积 12763。
    # 旧写法用绝对面积上限把它拒了，is_dingque_phase 漏判。
    assert probe(fx_discs(W, H, r=80)), "大屏真机帧上的三色盘又被绝对面积上限挡在门外"


def _assert_reversed_discs_are_dingque(probe):
    """筒-条-万 反序三盘：腾讯旧取区的顺序判据（红<绿<橙）必然不成立，
    只剩跨平台几何通路认得出。这条是「几何通路在承重」的唯一证据。"""
    assert probe(fx_discs(W, H, r=80, cols=(ORANGE_BGR, GREEN_BGR, RED_BGR))), \
        "异序（筒-条-万）三色盘漏判：非腾讯布局的定缺阶段又瞎了"


def _assert_small_discs_are_dingque(probe):
    # 同一相对几何在 1000x450 上也必须成立：判据与分辨率无关才对。
    assert probe(fx_discs(1000, 450, r=40)), "小图上的三色盘漏判（归一窗没起作用）"


def _assert_specks_are_not_dingque(probe):
    assert not probe(fx_specks()), "碎点又被拼成了一行定缺色盘"


def _assert_mid_specks_are_not_dingque(probe):
    """面积过了 400 地板、但边长不到取区宽 3.5% 的中等色块：靠**相对**下限挡。

    没有这一条，「地板」就退化成一个与分辨率无关的绝对值，桌布花纹、
    头像角标这类中小色块凑成一行就会被当成定缺三色盘。"""
    assert not probe(fx_mid_specks()), "相对下限没有牙：中等色块又被排成了一行定缺盘"


class TestSwapVisual(unittest.TestCase):
    """A 组：只认「低带里那个孤岛扁圆盘」，不认「这一带有金色」。"""

    def test_swap_button_low_band_blob_is_swap(self):
        _assert_swap_button_is_swap(swap_probe)

    def test_action_band_gold_is_not_swap(self):
        _assert_action_band_is_not_swap(swap_probe)

    def test_cyan_pass_button_alone_is_not_swap(self):
        _assert_cyan_is_not_swap(swap_probe)

    def test_tiny_gold_speck_is_not_swap(self):
        _assert_speck_is_not_swap(swap_probe)

    def test_loading_screen_flat_gold_sheet_is_not_swap(self):
        _assert_loading_sheet_is_not_swap(swap_probe)

    def test_tall_narrow_gold_strip_is_not_swap(self):
        _assert_strip_is_not_swap(swap_probe)

    def test_shape_windows_are_the_measured_ones(self):
        """三窗口是 41 帧实测值，不许被随手放宽（放宽一次就等于回到假阳性）。"""
        body = _det_method_src("is_swap_phase")
        for frag in ("1.8 <= sw / float(sh) <= 4.5",
                     "0.55 <= area / float(sw * sh) <= 0.75",
                     "0.20 <= sh / float(bh) <= 0.60",
                     "cv2.connectedComponentsWithStats(gold, 8)"):
            self.assertIn(frag, body, f"换牌形状判据缺了这一块：{frag}")

    def test_detector_never_demands_cyan_anymore(self):
        body = _det_method_src("is_swap_phase")
        body = body[body.rindex('"""') + 3:]
        self.assertNotIn("cyan", body.lower(),
                         "is_swap_phase 的代码里又出现了青色证据要求")


class TestDingqueDiscGeometry(unittest.TestCase):
    """B 组：三色盘尺寸按取区宽度归一 —— 同一套几何在不同分辨率上必须同判。"""

    def test_real_device_scale_discs_are_dingque_phase(self):
        _assert_big_discs_are_dingque(dq_probe)

    def test_small_scale_discs_are_dingque_phase(self):
        _assert_small_discs_are_dingque(dq_probe)

    def test_specks_are_not_dingque_phase(self):
        _assert_specks_are_not_dingque(dq_probe)

    def test_mid_specks_are_not_dingque_phase(self):
        _assert_mid_specks_are_not_dingque(dq_probe)

    def test_source_has_no_absolute_area_ceiling(self):
        # 只对**代码**断言：这个文件里描述旧写法的注释会原样引用那句坏代码，
        # 拿全文去 assertNotIn 会被自己的注释打红（踩过）。
        body = _strip_comments(_det_method_src("is_dingque_phase"))
        self.assertNotIn("400 <= area <= 10000", body,
                         "绝对面积上限复活：它在 2000x899 帧上会漏判定缺阶段")
        self.assertIn("sub.shape[1] * 0.035", body)
        self.assertIn("sub.shape[1] * 0.22", body)


class TestBadgeReadIsRawPixels(unittest.TestCase):
    """B' 组：徽章读数就是「那块像素是什么」。它是原始观测，不是事实层。"""

    @staticmethod
    def _badge_img(tile_color):
        img = np.full((H, W, 3), (180, 190, 200), np.uint8)
        cv2.circle(img, (int(W * 0.112), int(H * 0.631)), 14, tile_color, -1)
        return img

    def test_real_badge_is_readable(self):
        self.assertEqual((0, "万"), detect_dingque(self._badge_img(RED_BGR)))
        self.assertEqual((2, "条"), detect_dingque(self._badge_img(GREEN_BGR)))
        self.assertEqual((1, "筒"), detect_dingque(self._badge_img((235, 200, 60))))

    def test_empty_region_reads_nothing(self):
        # 「没读到」是一个合法出口：不得把空区/暗底当成某个已敲定的缺门
        self.assertEqual((None, None), detect_dingque(blank()))
        self.assertIsNone(_detect_badge_suit(np.zeros((90, 110, 3), np.uint8)))

    def test_no_pixel_heuristic_guard_is_reintroduced(self):
        """自噬的像素启发式已被实测证伪并拆除，不许换个名字再塞回来。
        真正的解法是面板矩形事实源（见模块 docstring 的「已知边界」）。"""
        for frag in ("_badge_region_self_covered", "BADGE_DARK_COVER_MAX"):
            self.assertNotIn(frag, ENGINE_SRC,
                             f"{frag} 又回来了：那条像素启发式实测六项特征全部重叠，"
                             "把 dingque 从 11/11 打到 2/11")


def base_obs(**kw) -> dict:
    obs = {"status": "ok", "count": 13, "shanten": 2, "drawing_tile": None,
           "self_discard": None, "swap_phase": False, "dq_phase": False,
           "pick_phase": False, "seat_river": {}, "seat_river_tiles": {},
           "seat_melds": {}, "dingque_suit": None, "dingque_name": "",
           "dingque_recommend": "", "ting_tiles": [], "now_ms": 0}
    obs.update(kw)
    return obs


def uniq_events(views, kind):
    """feed 是累计的滑窗：同一个事件会出现在后几帧的视图里。按 (kind, at_ms) 去重，
    才能问「这一局这件事到底播了几次」。"""
    seen = {}
    for v in views:
        for x in v["feed"]:
            if x["kind"] == kind:
                seen[(x["kind"], x["at_ms"])] = x
    return list(seen.values())


def replay(Machine, frames, step=100, t0=1_700_000_000_000):
    """按固定时钟把一串观测喂给状态机，返回逐帧视图。

    时钟局部于本次回放（不拿共享计数器）：变异检验要把同一个局面在干净机器与
    变异机器上各跑一遍比差异，共享时钟会让两边看到不同的时间轴。"""
    m = Machine()
    out, now = [], t0
    for f in frames:
        o = dict(f)
        now += step
        o["now_ms"] = now
        out.append(m.update(o))
    return out


# C 组的三条纪律抽成函数（不是写在测试方法里）：变异检验要用**同一个断言**
# 去跑变异机器，否则两边口径会漂移成「变异改变了行为但守卫还是绿的」。
# 用裸 `assert` 而不是 self.assertEqual：这段代码要在 unittest 之外被调用。


def _assert_no_turn_lie(views):
    v = views[-1]
    assert v["phase"] == "wait", f'13 张立牌被报成 {v["phase"]}'
    joined = (v["turn_basis"] or "") + "|" + (v["hint"] or "")
    for lie in ("已出完", "刚打完", "已经打出"):
        assert lie not in joined, f"张数反推又在编造行牌事实：{joined}"


def _assert_dingque_announces_the_badge(views):
    evs = uniq_events(views, "dingque")
    assert len(evs) == 1, f"读到缺门徽章应播一次敲定，实际播了 {len(evs)} 次"
    assert "条" in evs[0]["text"], f"敲定的缺门不是徽章读数：{evs[0]['text']}"
    assert "筒" not in evs[0]["text"], (
        f"推荐值被播成了「定缺敲定」，牌局里没人做过这个决定：{evs[0]['text']}")


def _assert_dingque_latches_once(views):
    evs = uniq_events(views, "dingque")
    assert len(evs) == 1, "缺门一局至多变一次，改口不得再播"
    assert "条" in evs[0]["text"], evs[0]["text"]
    assert "筒" not in "|".join(x["text"] for x in evs), "锁存后又播了第二个缺门"
    # 冲突计数按帧累加（它回答的是「改口诉求持续了多久」），但事件只许播一次。
    assert views[-1]["evidence"]["rejected"]["dingque_conflict"] >= 1, \
        "改口被静悄悄吞掉了：事后查不清是徽章误读还是真的跨了局"


def _machine_assert_no_turn_lie(Machine):
    _assert_no_turn_lie(replay(Machine, frames_idle()))


def _machine_assert_dingque_badge(Machine):
    _assert_dingque_announces_the_badge(replay(Machine, frames_badge_with_recommend()))


def _machine_assert_dingque_latch(Machine):
    _assert_dingque_latches_once(replay(Machine, frames_badge_then_flip()))


def frames_idle():
    return [base_obs()] * 4


def frames_badge_with_recommend():
    return [base_obs(dingque_suit=2, dingque_name="", dingque_recommend="筒")] * 4


def frames_badge_then_flip():
    return ([base_obs(dingque_suit=2, dingque_name="条")] * 3 +
            [base_obs(dingque_suit=1, dingque_name="筒")] * 3)


class TestPhaseTruth(unittest.TestCase):
    """C 组：状态机只播可观测事实。"""

    def test_thirteen_tiles_never_claims_a_discard_happened(self):
        _machine_assert_no_turn_lie(MatchPhaseMachine)

    def test_dingque_event_refuses_the_recommendation(self):
        _machine_assert_dingque_badge(MatchPhaseMachine)

    def test_dingque_latches_and_counts_conflict(self):
        _machine_assert_dingque_latch(MatchPhaseMachine)


class TestRealDeviceFrames(unittest.TestCase):
    """D 组：真机四帧全链路 GT（同一引擎顺序推进，每个时刻停 4 帧）。"""

    GT = [
        # (帧, 引擎 status, 局况 phase, 张数, 逐张手牌)
        ("s1_select_cd17.jpg", "swap", "swap", 13, "7z4m6m9m1s4s5s1p2p4p7p7p8p"),
        ("s2_select_cd07.jpg", "swap", "swap", 13, "7z4m6m9m1s4s5s1p2p4p7p7p8p"),
        ("s3_select_cd01.jpg", "swap", "swap", 13, "7z4m6m9m1s4s5s1p2p4p7p7p8p"),
        ("s4_after_swap_cd04.jpg", "ok", "turn", 14,
         "7z1s4s5s1p2p4p5p7p7p8p8p8p8p"),
    ]

    @classmethod
    def setUpClass(cls):
        missing = [n for n, *_ in cls.GT
                   if not os.path.exists(os.path.join(SHOTS, n))]
        if missing:
            raise AssertionError(f"真机回归夹具缺失，守卫在测空气：{missing}")
        from engine.engine import Engine
        cls.eng = Engine()
        cls.eng.set_platform("tencent")
        cls.eng.set_mode("sc_hz")

    def _feed(self, img):
        with contextlib.redirect_stdout(io.StringIO()):
            res = self.eng.process(img)
        return json.loads(res.result) if res is not None else None

    def test_four_frames_phase_ledger(self):
        for name, want_status, want_phase, want_count, want_hand in self.GT:
            img = cv2.imread(os.path.join(SHOTS, name))
            self.assertIsNotNone(img, name)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            d = None
            for _ in range(4):                     # 与真机静止片段等价
                d = self._feed(img)
            self.assertIsNotNone(d, f"{name} 没有返回帧")
            mp = d.get("match_phase") or {}
            self.assertEqual(want_status, d.get("status"),
                             f"{name} status：{d.get('status')} ≠ {want_status}")
            self.assertEqual(want_count, d.get("count"),
                             f"{name} count={d.get('count')} 应 {want_count}")
            self.assertEqual(want_hand, d.get("hand"),
                             f"{name} 手牌逐张必须一致")
            self.assertEqual(want_phase, mp.get("phase"),
                             f"{name} 局况阶段={mp.get('phase')} 应 {want_phase}")
            # 阶段标志与 status 只能有一套说法
            self.assertEqual(want_status == "swap", bool(d.get("swap_phase")), name)
            # 徽章读数不参与阶段：s2/s4 的取区被自家面板压住（见模块 docstring 的
            # 已知边界），但那种读数**不许**把阶段带进「定缺中」。
            self.assertNotEqual("dingque", mp.get("phase"),
                                f"{name} 一个被面板污染的徽章读数把阶段改成了定缺")
            self.assertFalse(bool(d.get("dingque_phase")) and want_status != "dingque",
                             f"{name} 阶段标志与 status 打架")
            # 出口不变量：报「等待牌局开始」的帧不得同时带着牌
            if d.get("status") == "waiting":
                self.assertFalse(d.get("hand") or d.get("count"),
                                 f"{name} waiting 帧仍报出手牌")

    def test_recognition_is_the_baseline_not_the_suspect(self):
        # 用户第 4 条反馈指向「识别不全」。这四帧上识别层一直是对的，GT 必须钉住
        # 这条分工：以后再出现「少几张」，先查阶段/呈现层，不要再动阈值。
        for name, _, _, want_count, want_hand in self.GT:
            img = cv2.imread(os.path.join(SHOTS, name))
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            d = self._feed(img)
            self.assertEqual(len(want_hand) // 2, want_count, name)
            self.assertTrue(d.get("hand"), f"{name} 单帧冷启动就该报出手牌")

    def test_count_never_invents_a_phase(self):
        """张数不许反推阶段：GT 里存在「14 张立牌 + 换三张」同帧的真例（s_6d33、t2），
        所以「>=14 张 ⇒ 已摸牌 ⇒ 不在换牌」在本平台为假，不许再被写成铁律。"""
        self.assertNotIn("curr_raw_n >= 14", ENGINE_SRC,
                         "张数反推阶段的老路子又回来了（它会真误伤 GT 里的换牌帧）")


# --------------------------------------------------------------- 接线契约
# 这些判据活在 engine.py 与 Dart 里，两侧都没办法像上面那样跑「行为对照」：
# engine.py 是包内相对导入，整文件 exec 一份副本要先重建包结构；Dart 根本没有
# 解释器可跑。所以对它们采用双层口径：下面 `TestWiring` 在默认套件里**真的断言**
# 这些写法在场且只出现一次，变异检验则证明「把它改掉，那条断言立刻红」。
# 行为层的兜底不在这里，在 D 组真机四帧与 eval_base。
WIRING_CONTRACTS = [
    # (说明, 源, 好写法, 它替不成的坏写法)
    ("换牌迟滞只有 1 帧（调大就会在换完牌后还黏着换三张）", "engine",
     "                    self._swap_hold_frames = 1",
     "                    self._swap_hold_frames = 3"),
    ("牌局账本一旦进入摸打就否决换牌视觉判据", "engine",
     "                if in_play_by_ledger:\n                    is_swap_phase = False",
     "                if False:\n                    is_swap_phase = False"),
    ("瞬态阻尼只认「读到过手牌」，不绑 _match_started（否则换牌期欠读裸露成 5 张）",
     "engine",
     "in_active_match = bool(self._stable_hand_mpsz)",
     'in_active_match = getattr(self, "_match_started", False) and bool(self._stable_hand_mpsz)'),
    ("waiting 出口不变量：报等待开局的那一帧必定没报出牌", "engine",
     'if status == "waiting" and hand_mpsz and curr_raw_n > 0:',
     'if False and hand_mpsz and curr_raw_n > 0:'),
    ("端上手牌闸门只看「引擎报出牌」，不许再拿阶段标志二次否决",
     "dart",
     "if (hand.isNotEmpty && count > 0 && status != 'waiting') ...[",
     "if (hand.isNotEmpty && count > 0 && (inMatch || isDingquePhase || isSwapPhase || isPickPhase)) ...["),
]

WIRING_SRC = {"engine": ENGINE_SRC, "state": STATE_SRC, "dart": OVERLAY_SRC}


def _assert_wiring_holds(srcs):
    for name, key, good, bad in WIRING_CONTRACTS:
        assert good in srcs[key], f"{key} 里的接线断了/改了写法：{name}"
        assert srcs[key].count(good) == 1, f"{name}：契约定位不唯一，改错地方也没人知道"
        assert bad not in srcs[key], f"坏写法已经在场上了：{name}"


class TestWiring(unittest.TestCase):
    """E 组：接线契约。这些写法是用户那四条抱怨的承重墙。"""

    def test_contracts_are_wired(self):
        _assert_wiring_holds(WIRING_SRC)


# ------------------------------------------------------------- 变异检验夹具
def _guard_breaks(assertion) -> bool:
    """跑一段守卫断言，返回它是否**变红**。只用 AssertionError：其它异常意味着
    模板本身不合法（语法/路径错），那要当作测试错误抛出去，不能算「拦住」。"""
    try:
        assertion()
        return False
    except AssertionError:
        return True


def _mutated_det(method: str, old, new):
    """把识别器的一个方法体改坏（可以一次改好几处），返回可调用函数（不碰磁盘也不碰生产实例）。

    只 exec 这一个方法：det 模块导入了 cv2/numpy，拿它的命名空间当 globals 就够
    方法体用，而不必重建整个模板库（加载 bank 要好几秒）。

    为什么要支持一次改多处：`is_dingque_phase` 现在是**两条通路**，跨平台几何通路在前、
    命中就 return，腾讯旧取区在后。只改坏后面那段，夹具帧在第一段就返回了，被改坏的代码
    根本没执行到 —— 变异活下来既不代表「守卫接住」也不代表「判据失效」，纯属空转。
    要证明旧取区还在承重，就得把几何通路一并拆掉（见 BEHAVIOR_MUTANTS 里 `old` 是列表那条）。"""
    body = _det_method_src(method)
    pairs = list(zip(old, new)) if isinstance(old, list) else [(old, new)]
    if isinstance(old, list) and len(old) != len(new):
        raise AssertionError(f"变异模板 old/new 不成对（{method}）")
    mutated = body
    for o, n in pairs:
        if o not in mutated:
            raise AssertionError(f"变异模板过期（{method}）：{o[:70]}")
        mutated = mutated.replace(o, n, 1)
    if mutated == body:
        raise AssertionError(f"变异无效：{str(old)[:70]}")
    ns = dict(vars(TGD))
    exec(compile(textwrap.dedent(mutated), f"<mutant:{method}>", "exec"), ns)
    return ns[method]


def _mutated_state(old: str, new: str):
    """整模块 exec 一份改坏的 match_state，返回它的 MatchPhaseMachine 类。

    match_state 是纯逻辑层（只依赖 time/collections/typing），所以可以这样拿副本；
    这个做法照了 `test_match_state_guard.py` 里已经跑通的先例。"""
    if old not in STATE_SRC:
        raise AssertionError(f"变异模板过期（state）：{old[:70]}")
    mod = types.ModuleType("mutated_match_state")
    mod.__dict__["__name__"] = "mutated_match_state"
    exec(compile(STATE_SRC.replace(old, new, 1), "<mutant:match_state>", "exec"),
         mod.__dict__)
    return mod.MatchPhaseMachine


class TestMutationControls(unittest.TestCase):
    """--mutate：把坏写法塞回生产代码，本守卫必须立刻变红。

    分两档，并且**如实标注各自证到了什么**：
      行为对照（BEHAVIOR / STATE）：exec 一份只改坏一处的源码副本，用守卫自己的
        同一条断言跑同一批夹具——红了才算接住。这一档证的是「那段判据真的在生效」，
        而不是「那段字还在」。如果某个变异活了下来，意味着夹具根本没跑到那段代码。
      接线契约（WIRING）：见 `WIRING_CONTRACTS` 上方说明，只能证「这条写法是承重的」。
    """

    BEHAVIOR_MUTANTS = [
        ("换牌取区退回牌河中带（局中杠/碰那排金色一并被包住）",
         "is_swap_phase",
         "gold_zone = image_bgr[int(ih * 0.74):int(ih * 0.88), int(iw * 0.64):int(iw * 0.78)]",
         "gold_zone = image_bgr[int(ih * 0.56):int(ih * 0.76), int(iw * 0.61):int(iw * 0.73)]",
         _assert_swap_button_is_swap),
        ("换牌只看占比、不量形状（加载界面那片沙滩重新越线）",
         "is_swap_phase",
         "        return (1.8 <= sw / float(sh) <= 4.5\n"
         "                and 0.55 <= area / float(sw * sh) <= 0.75\n"
         "                and 0.20 <= sh / float(bh) <= 0.60)",
         "        return True",
         _assert_loading_sheet_is_not_swap),
        ("定缺色盘退回绝对面积上限（2000x899 帧漏判定缺阶段）；"
         "必须连几何通路一起拆 —— 它在前面命中即 return，留着它这段代码跑不到，"
         "变异活下来只说明守卫空转", "is_dingque_phase",
         ["        discs = _find_phase_discs(image_bgr)\n"
          "        if discs and _phase_disc_row_in_band(\n"
          "                _phase_disc_row(discs, _PHASE_DISC_TRIPLE_GAP, 0.035)):\n"
          "            return True",
          "                if not (side_lo <= bw <= side_hi and side_lo <= bh <= side_hi):\n"
          "                    continue"],
         ["        discs = []",
          "                if area > 10000:\n                    continue"],
         _assert_big_discs_are_dingque),
        ("跨平台几何通路被抽掉（只剩腾讯顺序判据）：异序三盘必须漏判",
         "is_dingque_phase",
         "        discs = _find_phase_discs(image_bgr)",
         "        discs = []",
         _assert_reversed_discs_are_dingque),
        ("定缺色盘相对下限改成 0（中等色块凑成一行就算选门）",
         "is_dingque_phase",
         "        side_lo = max(20.0, sub.shape[1] * 0.035)",
         "        side_lo = 0.0",
         _assert_mid_specks_are_not_dingque),
    ]

    STATE_MUTANTS = [
        ("状态机恢复「已出完」这句臆断",
         'return False, f"立牌 {count} 张：等于基数 {base}，手上没有多余的牌可出"',
         'return False, f"立牌 {count} 张：等于基数 {base}，已出完等下一轮"',
         _machine_assert_no_turn_lie),
        ("候牌提示恢复臆断文案",
         'return "还没轮到你，等他家行动"',
         'return "已出完这一张，等他家行牌"',
         _machine_assert_no_turn_lie),
        ("定缺事件回落到推荐值（把建议播成敲定）",
         'name = str(obs.get("dingque_name") or "")',
         'name = str(obs.get("dingque_name") or obs.get("dingque_recommend") or "")',
         _machine_assert_dingque_badge),
        ("缺门锁存后允许改口（同一局先缺筒再缺条）",
         '            if suit != self._dingque.committed:\n'
         '                self._rejected["dingque_conflict"] += 1\n'
         '            return',
         '            self._dingque.force(suit)',
         _machine_assert_dingque_latch),
    ]

    def test_behavior_mutants_are_caught(self):
        missed = []
        for name, method, old, new, assertion in self.BEHAVIOR_MUTANTS:
            fn = _mutated_det(method, old, new)
            if _guard_breaks(lambda: assertion(lambda img: bool(fn(None, img)))):
                print(f"[mutate] {name}：同一条断言立刻变红——已接住")
            else:
                missed.append(name)
        self.assertEqual([], missed,
                         "这些变异活了下来：夹具根本没跑到那段代码，守卫是空转")

    def test_state_mutants_are_caught(self):
        missed = []
        for name, old, new, assertion in self.STATE_MUTANTS:
            Machine = _mutated_state(old, new)
            if _guard_breaks(lambda: assertion(Machine)):
                print(f"[mutate] {name}：说谎的状态机被同一断言拦下——已接住")
            else:
                missed.append(name)
        self.assertEqual([], missed,
                         "这些状态机变异活了下来：局况诚实性其实没人守")

    def test_wiring_mutants_are_caught(self):
        missed = []
        for name, key, good, bad in WIRING_CONTRACTS:
            srcs = dict(WIRING_SRC)
            srcs[key] = srcs[key].replace(good, bad, 1)
            self.assertNotEqual(srcs[key], WIRING_SRC[key], f"变异模板过期：{name}")
            if _guard_breaks(lambda: _assert_wiring_holds(srcs)):
                print(f"[mutate] {name}：接线契约变红——已接住（行为层由 D 组兜底）")
            else:
                missed.append(name)
        self.assertEqual([], missed, "这些接线变异活了下来：契约字符串其实不承重")


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestSwapVisual, TestDingqueDiscGeometry, TestBadgeReadIsRawPixels,
                TestPhaseTruth, TestRealDeviceFrames, TestWiring):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
