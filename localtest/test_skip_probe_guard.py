# -*- coding: utf-8 -*-
"""「声明平台就不再扫全库探针」这条改动的接线守卫。

背景（实测 `localtest/ab_skip_probe.py` -> build/ab_skip_probe.txt，91 帧 LOFO 夹具）：
风格探针每帧要拿 2~3 枚代表牌去对 **594 张模板**打分（1273 次 matchTemplate，
214~284ms/帧，占总耗时 21~28%，见 build/cost_detail.txt），而它产出的可用信息只有
「这帧像哪家的牌风」。用户已经声明平台时这件事是**已知的**：实测 91 帧里探针每次都把
本家排在第一（jj 14/14、queshen 15/15、tencent 16/16……仅 2 帧路由到隔壁家，而那两家
同为相邻排布、行为一致）。于是生产改成：声明平台 -> 手牌排布方式查
`PLATFORM_OVERLAP_LAYOUT`，不再扫全库；未声明（陌生平台自动路由）-> 探针照旧。

锁四件事，每件都是「改回坏写法就必红」的断言：
1. **声明时探针一次都不许被调用**（替身直接抛异常，被调即红），而**未声明时必须被调用**
   —— 后半条同样要紧：把陌生平台的自动路由一起关掉，等于把 b8 那套泛化能力删了，
   而它不会让任何精度测试变红，只会让真机遇到没建 bank 的平台时静默认错牌。
2. 回退口子 `probe_when_declared` 必须真的能拨回旧行为（调用计数 > 0）。
   口子上写着"可回退"但实际无效，是比没有口子更坑的调试陷阱。
3. 等价性有两条，缺一不可：
   3a **档位一致**：探针自己做决定时选的那档（tec/adj），查表必须选同一档。
   3b **输出一致**：逐帧「跳探针」与「照旧探针」的 (框x, 标签) 序列相同。
      置信度允许不同（候选集从"探针收窄的那家"放回"平台白名单"，同类的别家变体核
      分数更高，实测 queshen#05 一枚 7z 0.796->0.815），但差值必须留在安全范围内。
   3c **样本外棘轮**：LOFO 剔掉本帧模板后，蜀山帧 01 第 12 枚仍须读对（5p）。
      3b 的"零代价"只在全库夹具上成立，样本外这里候选集差异反而白捡了一格。

为什么 3a 必须单独存在（这一条是踩出来的）：最初本版守卫只断言 3b，变异检验
「把腾讯从排布表里摘掉」在同一帧上**输出一点没变**，于是断言报红、说 3b 在测空气。
量完 91 帧才搞清因果（build/layout_table_sensitivity.txt）：
  - 查表档 vs 探针档：冲突 0/91（`tec->tec` 16 帧、`adj->adj` 73 帧、探针弃权 2 帧）；
  - 两套张数候选 `c_adj == c_tec` 只有 65/91 —— 表在 26 帧上确实改变了候选；
  - 但输出仍 0/91 不同：`_try_counts` 会在候选里取均分最优，真档两边都被试到。
=> 「翻面必红」在输出层结构性地测不到，只能直接断言它选了哪一档（靠生产的
   `last_layout_branch` 可观测位）。这也顺带说明 3b 的「零精度代价」不是运气。

夹具：`localtest/gt/shots_b1.json` 里已钉帧，每平台 2 帧（缺夹具判红，不静默跳过）。

运行：
  py -3.10 -X utf8 localtest/test_skip_probe_guard.py            # 主守卫
  py -3.10 -X utf8 localtest/test_skip_probe_guard.py --mutate    # 变异对照
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import cv2  # noqa: E402

import recognition.tencent_grid_detector as T  # noqa: E402
from modes import available_set  # noqa: E402
from eval_new_material import STYLE_OF, load_provenance, lofo_keep_indices  # noqa: E402
from layer_cost import mode_for  # noqa: E402

GT = os.path.join(HERE, "gt", "shots_b1.json")
PER_STYLE = 2               # 每平台 2 帧：够覆盖两条排布分支，又把守卫压在 1 分钟内
CONF_SLACK = 0.08           # 候选集放回白名单后允许的置信度漂移（实测最大 0.019）
MUTATE = "--mutate" in sys.argv


def family(branch: str) -> str:
    """`table-tec` / `probe-tec` -> `tec`；探针弃权 -> `both`；没跑过 -> `?`。"""
    return branch.split("-", 1)[1] if branch else "?"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = []
        cls.setup_error = ""
        if not os.path.exists(GT):
            cls.setup_error = f"夹具缺失：{GT}"
            return
        with open(GT, encoding="utf-8") as fp:
            shots = [e for e in json.load(fp)["shots"] if e.get("verified")]
        seen = {}
        for e in shots:
            if seen.get(e["style"], 0) >= PER_STYLE:
                continue
            path = os.path.join(REPO, e.get("src") or os.path.join("public", "1"), e["file"])
            img = cv2.imread(path)
            if img is None:
                continue
            seen[e["style"]] = seen.get(e["style"], 0) + 1
            platform = STYLE_OF.get(e["style"], "tencent")
            hand = sorted({x for x in e["hand"] if x != "?"})
            cls.frames.append({"style": e["style"], "frame": e["frame"],
                               "platform": platform, "mode": mode_for(platform, hand)[0],
                               "gt_hand": list(e["hand"]), "img": img})
        if not cls.frames:
            cls.setup_error = "夹具里一帧都读不出来"

    def setUp(self):
        self.det = T.TencentGridDetector()

    def tearDown(self):
        self.det.probe_when_declared = False

    def _run(self, f, use_probe=False, with_conf=False):
        """跑一帧，返回 ((x, label[, conf]) 序列, 档位)。

        `use_probe=True` 是把调试口子拨回旧行为（照扫全库探针），用来当对照。
        """
        self.det.probe_when_declared = use_probe
        self.det.set_platform_styles(f["platform"])
        self.det.set_mode_tiles(available_set(f["mode"]))
        with contextlib.redirect_stdout(io.StringIO()):
            rows = self.det.detect_all_rows(f["img"], classify=True, allow_rotation=False)
        # 只留能代表识别结果的两项：x 与标签（框宽由节距决定，也在 x 序列里体现）
        flat = [(int(r[0]), lbl, float(c)) for row in rows for (r, lbl, c) in row]
        if not with_conf:
            flat = [(x, lbl) for x, lbl, _c in flat]
        # 返回**原始**档名（`table-*` / `probe-*`）：前缀本身就是「走了哪条分支」的证据，
        # 比较时才用 `family()` 剥掉，别在这里提前丢信息。
        return flat, self.det.last_layout_branch


class TestSkipProbeWhenDeclared(Base):
    # ---------- 1 ----------
    def test_declared_platform_must_not_call_the_probe(self):
        """声明平台后探针被调用 = 白付每帧 1273 次全库打分的钱。"""
        self.assertTrue(self.frames, self.setup_error or "夹具为空")
        calls = []

        def boom(crop, extra=None):
            calls.append(1)
            raise AssertionError("声明了平台却仍在扫全库探针")

        for f in self.frames:
            self.det._probe_style = boom
            try:
                out, _br = self._run(f)
            finally:
                del self.det._probe_style
            self.assertGreater(len(out), 0, f"{f['style']}#{f['frame']} 没读出手牌")
        self.assertEqual(calls, [], f"探针仍被调用 {len(calls)} 次")

    def test_undeclared_platform_must_still_probe(self):
        """没声明平台时探针必须照旧跑：那是陌生平台唯一的自动路由。

        这条是上一条的对偶。只测「声明时不探」会让重构顺手把探针整个删掉，
        精度门禁（都在已声明平台的夹具上跑）一条都不会红，真机却会在
        「用户没说平台」时拿别家字模认这家的牌。
        """
        f = self.frames[0]
        calls = []
        orig = self.det._probe_style

        def spy(crop, extra=None):
            calls.append(1)
            return orig(crop, extra)

        self.det._probe_style = spy
        try:
            self.det.set_platform_styles(None)
            self.det.set_mode_tiles(available_set(f["mode"]))
            with contextlib.redirect_stdout(io.StringIO()):
                self.det.detect_all_rows(f["img"], classify=True, allow_rotation=False)
        finally:
            self.det._probe_style = orig
        self.assertGreater(len(calls), 0, "未声明平台时探针没跑：陌生平台自动路由被关掉了")

    # ---------- 2 ----------
    def test_debug_knob_restores_old_behaviour(self):
        """`probe_when_declared=True` 必须真的拨回旧行为（否则回退口子是假的）。"""
        f = self.frames[0]
        calls = []
        orig = self.det._probe_style

        def spy(crop, extra=None):
            calls.append(1)
            return orig(crop, extra)

        self.det._probe_style = spy
        try:
            self._run(f, use_probe=True)
        finally:
            self.det._probe_style = orig
        self.assertGreater(len(calls), 0, "回退口子没生效：探针仍被跳过")

    # ---------- 3a ----------
    def test_table_agrees_with_the_probe_on_every_frame(self):
        """查表选的档必须与探针自己选的档一致（探针决断时）。

        探针弃权（`probe-both`）的帧不参与：那本来就没有「正确答案」可比，硬要比
        会把「探针自己没定」当成「查表错了」。
        """
        clash, abstain = [], []
        for f in self.frames:
            _out, br_table = self._run(f)
            _out2, br_probe = self._run(f, use_probe=True)
            # 声明了平台却还走探针分支，说明口子或声明记录坏了（弃权帧也要查这条）
            self.assertTrue(str(br_table).startswith("table"),
                            f"{f['style']}#{f['frame']} 声明平台后档位仍是 {br_table}")
            ft, fp = family(br_table), family(br_probe)
            if fp == "both":
                abstain.append((f["style"], f["frame"]))
                continue
            if fp in ("tec", "adj") and ft != fp:
                clash.append((f["style"], f["frame"], br_table, br_probe))
        self.assertEqual(clash, [],
                         f"{len(clash)} 帧查表与探针不同档：排布表与实测结论不一致 {clash}")
        print(f"  档位对账：{len(self.frames)} 帧，探针弃权 {len(abstain)} 帧 "
              f"{[f'{s}#{n:02d}' for s, n in abstain] or '无'}")

    # ---------- 3b ----------
    def test_output_is_identical_to_the_probed_path(self):
        """省的钱必须是白捡的，不是拿识别结果换的。

        注意这条**不能**当排布表的接线判据：实测把表整个翻面，91 帧输出 0 帧改变
        （两种节距候选同形是常态，且 `_try_counts` 在候选里取均分最优）。表的接线
        由 3a 负责，这里只保证「跳探针」这件事没动识别结果。
        """
        bad, conf_max = [], 0.0
        for f in self.frames:
            new, _bt = self._run(f, with_conf=True)
            old, _bp = self._run(f, use_probe=True, with_conf=True)
            if [(x[0], x[1]) for x in new] != [(x[0], x[1]) for x in old]:
                bad.append((f["style"], f["frame"],
                            [x[1] for x in old], [x[1] for x in new]))
                continue
            for o, n in zip(old, new):
                conf_max = max(conf_max, abs(o[2] - n[2]))
        for s, fr, o, n in bad[:5]:
            print(f"  差异帧 {s}#{fr:02d}\n    探针版 {o}\n    查表版 {n}")
        self.assertEqual(bad, [], f"{len(bad)} 帧的输出因跳探针而改变（改动不成立）")
        self.assertLessEqual(conf_max, CONF_SLACK,
                             f"置信度漂移 {conf_max:.3f} 超出安全范围 "
                             f"{CONF_SLACK}（候选集变化影响过大了）")

    # ---------- 3c ----------
    def test_lofo_ablation_keeps_the_whitelist_win(self):
        """样本外那 1 格必须留在正确答案上（棘轮，不是回归测试）。

        实测两把尺的对照（`--with-probe` 拨回旧行为）：
          通道 LOFO 全库 = 蜀山帧 01 第 12 枚
            旧行为 候选库 `shushan`×27          -> 4p（错，12/13）
            现行   候选库 `shushan+tencent`×27  -> 5p（对，13/13）
          合计 1080 -> 1081/1087（build/lofo_channel_b1.txt vs …_probe.txt）
        机理：探针把分类候选收窄成「本家 + 第二名」，而本帧贡献的 8 张模板刚被 LOFO
        剔走，5p 在本家 bank 里没了模板，就被形近的 4p 抢走；查表版给的是**平台白
        名单**（还含主库），主库那枚 5p 把它救了回来。
        所以「跳探针零代价」只在**全库**夹具上成立（见 3b 的注释与 ab_skip_probe.txt），
        样本外这一格是白捡的**变好**。谁把候选集改回探针前两名，这条就红。
        """
        target = next((x for x in self.frames
                       if x["style"] == "shushan" and x["frame"] == 1), None)
        self.assertIsNotNone(target, "夹具里没有蜀山帧 01，这格棘轮无从下手")
        drop = load_provenance().get("shushan", {}).get(1)
        self.assertTrue(drop, "蜀山帧 01 没有溯源信息：LOFO 剔不了模板，这条会退化成空测")

        det = self.det
        saved = (det._cores, det._core_keys, det._core_harvested)
        keep = lofo_keep_indices(*saved, "shushan", drop)
        det._cores = [saved[0][i] for i in keep]
        det._core_keys = [saved[1][i] for i in keep]
        det._core_harvested = [saved[2][i] for i in keep]
        try:
            new, _bt = self._run(target)
            old, _bp = self._run(target, use_probe=True)
        finally:
            det._cores, det._core_keys, det._core_harvested = saved
        gt = target["gt_hand"]
        self.assertEqual(len(new), len(gt),
                         f"LOFO 后框数 {len(new)} 与 GT {len(gt)} 不符，槽位对不上，"
                         "棘轮断言会失去意义")
        self.assertEqual([lbl for _x, lbl in new], gt,
                         f"现行（平台白名单）在样本外读成 {[l for _x, l in new]}，"
                         f"不再逐位等于 GT {gt} —— 这一格是本改动的收益，不能丢")
        if [lbl for _x, lbl in old] != gt:
            print(f"  对照：旧行为（探针收窄）这帧 {[l for _x, l in old]}，"
                  f"第 12 枚被 4p 抢走 —— 现行已修正")


class TestDeclaredPlatformBookkeeping(unittest.TestCase):
    """`_declared_platform` 是这条改动的唯一开关依据，它自己也得被钉住。"""

    def setUp(self):
        self.det = T.TencentGridDetector()

    def test_declaration_is_recorded_and_cleared(self):
        self.assertIsNone(self.det._declared_platform)
        self.det.set_platform_styles("tuyou")
        self.assertEqual(self.det._declared_platform, "tuyou")
        self.det.set_platform_styles(None)
        self.assertIsNone(self.det._declared_platform,
                          "清空声明失败：换平台后还按上一个平台的排布走")
        self.det.set_platform_styles("")
        self.assertIsNone(self.det._declared_platform, "空串等于没声明，别当成腾讯")

    def test_only_tencent_is_overlap_layout(self):
        """排布表里现在只有腾讯，是实测结论不是猜测（见 build/ab_skip_probe.txt）。"""
        self.assertEqual(set(T.PLATFORM_OVERLAP_LAYOUT), {"tencent"},
                         "改这张表必须先在 91 帧夹具上重跑 ab_skip_probe.py")

    def test_branch_is_cleared_every_frame(self):
        """档位留档每帧重算：中途早退不能留下上一帧的档当本帧证据。"""
        self.assertIsNone(self.det.last_layout_branch)
        self.assertEqual(self.det.detect_hand_strip(None), [])
        self.assertIsNone(self.det.last_layout_branch)


class TestMutationControls(Base):
    """变异对照：把生产换成坏写法，断言它**确实坏成主守卫禁止的那个样子**。

    两条各对应主守卫的一条：M1 对应 3a（档位），M2 对应 1（声明时不许调探针）。
    若哪天有人把 3a / 1 削弱成空断言，这两条会与主守卫互相矛盾而报红。
    """

    def _tencent_frame(self):
        f = next((x for x in self.frames if x["style"] == "tencent"), None)
        self.assertIsNotNone(f, "夹具里没有腾讯帧，排布表的变异检验无从下手")
        return f

    def test_M1_wrong_layout_table_must_break_the_branch(self):
        """腾讯被误标成相邻排布 -> 3a 依据的档位必须变脏。

        这条同时解释了为什么 3a 不能只看输出：摘掉表后输出仍然一模一样
        （实测 91 帧 0 差异），只有档位会变。
        """
        f = self._tencent_frame()
        orig = T.PLATFORM_OVERLAP_LAYOUT
        T.PLATFORM_OVERLAP_LAYOUT = frozenset()          # 腾讯被误标成相邻排布
        try:
            out_bad, br_bad = self._run(f)
        finally:
            T.PLATFORM_OVERLAP_LAYOUT = orig
        out_ok, br_ok = self._run(f)
        self.assertEqual((family(br_bad), family(br_ok)), ("adj", "tec"),
                         f"表摘掉腾讯后档位没变（{br_bad} vs {br_ok}）："
                         "说明表根本没接管这个决定，3a 在测空气")
        self.assertEqual([x[:2] for x in out_bad], [x[:2] for x in out_ok],
                         "变异体输出反而变了：那 3b 的『零代价』结论已不成立，"
                         "必须重新量 91 帧再决定怎么写守卫")

    def test_M2_forcing_the_probe_must_break_the_no_probe_rule(self):
        """把口子拨成「声明了也照扫」-> 主守卫 1 所依赖的事实必须坏掉。"""
        f = self._tencent_frame()
        calls = []
        orig = self.det._probe_style

        def spy(crop, extra=None):
            calls.append(1)
            return orig(crop, extra)

        self.det._probe_style = spy
        try:
            self._run(f, use_probe=True)
        finally:
            self.det._probe_style = orig
        self.assertGreater(len(calls), 0,
                           "口子拨回旧行为后探针仍没跑：主守卫 1 就成了空断言")

    def test_M4_narrowed_candidates_must_lose_that_tile(self):
        """把候选集人为收窄成「只剩本家」= 3c 禁止的坏写法，那格必须如期读错。

        没有这条，3c 可能在测空气：万一 LOFO 剔完模板后本家 bank 里其实还够读出 5p，
        那 3c 对候选集怎么改都无感，就守不住任何东西。
        """
        target = next((x for x in self.frames
                       if x["style"] == "shushan" and x["frame"] == 1), None)
        self.assertIsNotNone(target, "夹具里没有蜀山帧 01")
        drop = load_provenance().get("shushan", {}).get(1)
        self.assertTrue(drop, "蜀山帧 01 没有溯源信息")
        det = self.det
        saved = (det._cores, det._core_keys, det._core_harvested)
        keep = lofo_keep_indices(*saved, "shushan", drop)
        det._cores = [saved[0][i] for i in keep]
        det._core_keys = [saved[1][i] for i in keep]
        det._core_harvested = [saved[2][i] for i in keep]
        det.probe_when_declared = False
        det.set_platform_styles(target["platform"])
        det.set_mode_tiles(available_set(target["mode"]))
        det.active_styles = {"shushan"}          # 坏写法：候选集只剩本家（探针收窄的极端）
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rows = det.detect_all_rows(target["img"], classify=True,
                                           allow_rotation=False)
            labs = [lbl for row in rows for (_r, lbl, _c) in row]
        finally:
            det._cores, det._core_keys, det._core_harvested = saved
            det.set_platform_styles(target["platform"])    # 复原 active_styles
        self.assertNotEqual(labs, target["gt_hand"],
                            "候选集只剩本家时这帧仍逐位等于 GT：说明 3c 测不到候选集这件事，"
                            "得换个更敏感的夹具帧")

    def test_M3_branch_observability_is_real(self):
        """可观测位本身必须反映真实分支：未声明平台时档位必须是 `probe-*`。

        没有这条，3a 的「档位」可以是被硬编码出来的字符串，守卫照样全绿。
        """
        f = self.frames[0]
        self.det.set_platform_styles(None)
        self.det.set_mode_tiles(available_set(f["mode"]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.det.detect_all_rows(f["img"], classify=True, allow_rotation=False)
        self.assertTrue(str(self.det.last_layout_branch).startswith("probe"),
                        f"未声明平台却报出档位 {self.det.last_layout_branch}："
                        "留档与真实分支脱钩")


if __name__ == "__main__":
    if MUTATE:
        print("[mutate] 只跑变异对照：四个坏写法必须「如期坏掉」")
        unittest.main(argv=[sys.argv[0], "TestMutationControls"], exit=False, verbosity=2)
    else:
        unittest.main()
