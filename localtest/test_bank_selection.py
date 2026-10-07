# -*- coding: utf-8 -*-
"""bank 变体择优规则（build_platform_bank.pick_variants）的接线守卫 + 变异检验。

被钉住的是三条**存在理由**，每条都对应一次实测过的退化，不是想象中的病：

① 孤儿亚群不得压过主力群（腾讯 9s 实测形状）
   同类 13 张里有两张 `b1_f12_*`：互相像 0.913，跟所有别类都不像（最高 0.337）。
   任何“同类支持 - 别类支持”的 margin 排序都会把它们排到最前（+0.576 > 主力群的
   +0.27），可它们对主力群查询只有 0.913 以下的支持，LOFO 一剔帧 3 就撑不住判决
   （实测 bar 0.715，赢格 11/13）。清晰度优先的旧规则因为这两张更“锐”也把它们收了
   进来。夹具要的是这个形状：主力群 own 高、对手线也高；孤儿 own 尚可、对手线极低。

② 唯一外观帧必须保住一个名额（zj#08 实测形状）
   纯“LOFO 赢格数”目标在这里是瞎的：异外观帧的样本对**别人的**赢格毫无贡献（别的
   外观互相已经撑满了），于是它和一张同外观冗余样本打平，被后面的 Σ余量 级顶出去。
   实测后果不是错一格而是掉整帧的框：zj#08 是全 GT 里唯一一帧 1440x664，改纯赢格
   后本帧一张没入库，那一帧从 10/10 出框退成 6/10、均分 0.62 -> 0.36（量法见
   build/_frame_support.py）——检出层看的是绝对分数，分数不够就不出框。

③ 名额不得全押在同一帧（微乐 8p / 腾讯 9s 旧病）
   旧规则实测把微乐 8p 的三张里的两张押在帧 13，LOFO 剔帧 13 就连同该类一起失去，
   看起来像“素材不够”，实际是“择优把素材押在同一帧”。

为什么既用合成夹具又用真素材：合成夹具能把“形状”钉死，并且可以暴力枚举全部子集当
独立裁判（择优是贪心，必须确认它没有输给某个更优的三元组）；真素材那边确认这条规则
在**生产尺子**（core_sig + 真实对手线）上确实优于旧规则，且盘上的 provenance 与规则
重算结果一致（防“改了规则忘了重建库”）。

变异检验（改完本文件必须手跑一次，确认它会变红）：
    py -3.10 -X utf8 localtest/test_bank_selection.py --mutate
  - 把 pick_variants 换成旧规则（Laplacian 清晰度 + 0.985 互异门）：①③ 与真素材三条
    必须全部报出退化；
  - 把 NOVEL_THR 设成 -1.0（等效关掉②级）：② 必须报出唯一外观被顶出库。

运行：py -3.10 -X utf8 localtest/test_bank_selection.py [tencent weile zj ...]
诊断：py -3.10 -X utf8 localtest/test_bank_selection.py --report
"""
from __future__ import annotations

import itertools
import json
import math
import os
import sys
import unittest
from collections import defaultdict

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import build_platform_bank as bpb  # noqa: E402  必须走模块属性：变异检验要换它的函数
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

CORE_HW = (88, 56)                 # core_sig 读的 face[16:104, 12:68]
DIM = CORE_HW[0] * CORE_HW[1]
STYLE_CACHE = {}


# ---------------------------------------------------------------- 合成牌面

def _atoms(k_extra, n_looks, seed):
    """标准正交基：前 n_looks 个是**低频**图案（外观原型），后面是白噪声（帧间差异）。

    频率分开是有原因的：旧规则拿 Laplacian 方差当“清晰度”排序，如果原型也是白噪声，
    所有样本的锐度就分不开（实测只有 1% 的抖动），变异体选谁全看骰子。分开后
    帧间差异幅度 d 越大 = 白噪声占比越高 = 越锐，旧规则的偏好可控制、可复现。
    """
    rng = np.random.default_rng(seed)

    def smooth():
        g = rng.standard_normal((7, 5))          # 粗网格上取值再放大：接近牌面图案尺度
        return cv2.resize(g, (CORE_HW[1], CORE_HW[0]), interpolation=cv2.INTER_CUBIC).ravel()

    X = np.column_stack([smooth() for _ in range(n_looks)] +
                        [rng.standard_normal(DIM) for _ in range(k_extra)])
    Q, _ = np.linalg.qr(X)
    return [Q[:, j] for j in range(n_looks + k_extra)]


def _cos_of(cos_tgt, a, b):
    """外观相似度查表：只写上半角就行（对称补齐，缺项当作 0 = 正交）。"""
    if a == b:
        return 1.0
    for x, y in ((a, b), (b, a)):
        if y in cos_tgt.get(x, {}):
            return float(cos_tgt[x][y])
    return 0.0


def _look_vectors(looks, cos_tgt, seed):
    """外观原型：两两余弦 == cos_tgt。Cholesky 不存在 = 声称的形状几何上不可能。"""
    names = sorted(looks)
    T = np.array([[_cos_of(cos_tgt, a, b) for b in names] for a in names], dtype=np.float64)
    try:
        L = np.linalg.cholesky(T)
    except np.linalg.LinAlgError:
        raise AssertionError(f"外观相似度矩阵不正定，形状本身不可能：{names} {T.tolist()}")
    at = _atoms(64, len(names), seed)
    return {nm: sum(L[i][j] * at[j] for j in range(len(names))) for i, nm in enumerate(names)}, at


def face_of_vec(vec):
    """把核窗口指纹铺成 120x80x3：core_sig 读的就是 [16:104, 12:68]，边界不参与。

    逐样本把灰度标准差定到 40 再落 uint8：core_sig 本身会去均值除标准差，缩放不改变
    相似度，但能避开截断（低频原子的能量集中在少量像素上，不归一化会直接削顶）。
    """
    v = vec.reshape(CORE_HW)
    g = (128.0 + 40.0 * v / (float(v.std()) + 1e-9)).clip(0, 255).astype(np.uint8)
    img = np.full((120, 80, 3), 200, np.uint8)
    img[16:104, 12:68] = g[:, :, None]
    return img


def mk(lab, fn, look, d):
    """一个样本：标签、源文件名（决定源帧归属）、所属外观、帧间差异幅度 d（同类余弦 ~ 1-d^2）。"""
    return (lab, fn, look, d)


def synth(spec, cos_tgt, seed=11):
    """-> dict(labels, fns, frames, faces, G, bars, by_label)；一条“外观 + 帧”的人工素材池。"""
    looks = {s[2] for s in spec}
    protos, at = _look_vectors(looks, cos_tgt, seed)
    fns, labs, faces, sig = [], [], [], {}
    for i, (lab, fn, look, d) in enumerate(spec):
        v = math.sqrt(max(0.0, 1.0 - d * d)) * protos[look] + d * at[len(looks) + i]
        f = face_of_vec(v)
        sig[fn] = bpb.core_sig(f)
        faces.append((f, fn))
        fns.append(fn)
        labs.append(lab)
    G = bpb.gram([sig[fn] for fn in fns])
    frames = [bpb.frame_of(fn) for fn in fns]
    bars = bpb.rival_bars(labs, frames, G)
    by_label = defaultdict(list)
    for idx, lab in enumerate(labs):
        by_label[lab].append(idx)
    return {"labels": labs, "fns": fns, "frames": frames, "G": G, "bars": bars,
            "by_label": by_label, "faces": faces, "sig": sig}


def faces_of(pool, li):
    """类内样本的 (face, fn) 列表 —— pick_variants 的入参形状。"""
    idx = {fn: i for i, fn in enumerate(pool["fns"])}
    return [pool["faces"][idx[pool["fns"][i]]] for i in li]


# ---------------------------------------------------------------- 打分口径（独立裁判）

def lofo_wins(G, frames, bars, sub, qs):
    """模板集 sub 对类内每个查询的 LOFO 赢格数（与 eval_new_material --lofo 同口径）。

    qs = 只拿本类样本当查询：把别类样本也算进来会变成“用 9s 的模板去支撑 7s 的
    查询”，那个数字没有意义。

    这里重写一遍而不是调生产里的内层循环：生产那份和择优是同一个函数写的，拿它当
    裁判就是自证。规则很简单：查询 q 能用的模板 = sub 里不是 q 自己、且不与 q 同帧
    的那些（那一折会被抽掉）；其对 q 的最高相似度必须 > 对手线 bars[q]。
    """
    w = 0
    for k in qs:
        allow = [t for t in sub if t != k
                 and not (frames[k] is not None and frames[t] == frames[k])]
        if not allow:
            continue                 # 本帧之外无人可撑：素材债，不记在择优规则头上
        if float(max(G[k, t] for t in allow)) > bars[k]:
            w += 1
    return w


def wins_of(pool, lab, sub):
    return lofo_wins(pool["G"], pool["frames"], pool["bars"], sub, pool["by_label"][lab])


def best_wins(pool, lab, slots):
    """暴力枚举本类全部 size<=slots 子集，取赢格上限（贪心必须打到它）。"""
    qs = pool["by_label"][lab]
    if math.comb(len(qs), min(slots, len(qs))) > 6000:
        return None                  # 组合数爆了就不当独立裁判
    return max(lofo_wins(pool["G"], pool["frames"], pool["bars"], list(c), qs)
               for r in range(1, min(slots, len(qs)) + 1)
               for c in itertools.combinations(qs, r))


def own_sim(G, li, frames, t):
    """t 与本类**其他帧**样本的最高相似度（孤立体在这项上低）。

    无帧归属（frames[t] is None，legacy 样本）时不排除任何同伴：它们不属于任何一折，
    在 LOFO 里一直在场。
    """
    o = [j for j in li if j != t
         and not (frames[t] is not None and frames[j] == frames[t])]
    return float(max(G[t, o])) if o else -1.0


def rival_sim(G, labs, li, t):
    """t 与别类样本的最高相似度（对手线，margin 的分母项）。"""
    o = [j for j in range(len(labs)) if labs[j] != labs[t]]
    return float(max(G[t, o])) if o else -1.0


def clarity(face):
    """旧规则的排序键：牌面 Laplacian 方差（越“锐”越优先）。"""
    return float(cv2.Laplacian(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var())


def legacy_pick(faces, rival):
    """旧规则（变异体，勿接回生产）：清晰度降序 + 0.985 互异门，最多 MAX_VARIANTS 张。"""
    sigs = [bpb.core_sig(f) for f, _fn in faces]
    G = bpb.gram(sigs)
    order = sorted(range(len(faces)),
                   key=lambda i: -clarity(faces[i][0]))
    got = []
    for t in order:
        if len(got) >= bpb.MAX_VARIANTS:
            break
        if got and float(G[t, got].max()) >= bpb.NOVEL_THR:
            continue                 # 同一外观簇的重复样本：旧规则一票否决
        got.append(t)
    return [(faces[t][0], faces[t][1], 0.0) for t in got]


def without_novel(faces, rival):
    """变异体：把②级（新外观优先）关掉，其余不变。勿接回生产。"""
    saved = bpb.NOVEL_THR
    bpb.NOVEL_THR = -1.0
    try:
        return bpb.pick_variants(faces, rival)
    finally:
        bpb.NOVEL_THR = saved


def run_pick(picker, pool, lab):
    """跑某个择优规则，返回它选中的样本索引（池内全局索引）。"""
    li = pool["by_label"][lab]
    faces = faces_of(pool, li)
    rival = dict(zip(pool["fns"], pool["bars"]))
    fn2i = {fn: i for i, fn in enumerate(pool["fns"])}
    return [fn2i[fn] for _f, fn, _d in picker(faces, rival)]


# ---------------------------------------------------------------- 人工夹具

def pool_orphan():
    """① 主力群(帧 1/2/3, own 0.99, 对手线 0.73) + 孤儿亚群(帧 12 x2, 互相 0.98,
    与主力群只像 0.60、与别类只像 0.20 -> margin 反而更高、也更“锐”)。"""
    d_m, d_o = 0.10, 0.14
    sp = [mk("9s", "b1_f01_00_9s.png", "main", d_m),
          mk("9s", "b1_f02_00_9s.png", "main", d_m),
          mk("9s", "b1_f03_00_9s.png", "main", d_m),
          mk("9s", "b1_f12_00_9s.png", "orph", d_o),
          mk("9s", "b1_f12_01_9s.png", "orph", d_o),
          mk("7s", "b1_f05_00_7s.png", "riv", 0.10),
          mk("7s", "b1_f06_00_7s.png", "riv", 0.10),
          mk("7s", "b1_f07_00_7s.png", "riv", 0.10)]
    return synth(sp, {"main": {"main": 1.0, "orph": 0.60, "riv": 0.73},
                      "orph": {"orph": 1.0, "riv": 0.20},
                      "riv": {"riv": 1.0}})


def pool_novel():
    """② 同外观冗余样本与唯一外观样本在赢格数上打平，唯一外观必须靠②级活下来。

    主力群(帧 1/2/3)、子群对(帧 4/5 互相 0.99、对主力群 0.91)、唯一外观(帧 6，谁都不像)。
    """
    sp = [mk("1p", "b1_f01_00_1p.png", "main", 0.10),
          mk("1p", "b1_f02_00_1p.png", "main", 0.10),
          mk("1p", "b1_f03_00_1p.png", "main", 0.10),
          mk("1p", "b1_f04_00_1p.png", "sub", 0.10),
          mk("1p", "b1_f05_00_1p.png", "sub", 0.10),
          mk("1p", "b1_f06_00_1p.png", "uniq", 0.10),
          mk("2p", "b1_f07_00_2p.png", "riv", 0.10),
          mk("2p", "b1_f08_00_2p.png", "riv", 0.10),
          mk("2p", "b1_f09_00_2p.png", "riv", 0.10)]
    return synth(sp, {"main": {"main": 1.0, "sub": 0.92, "uniq": 0.60, "riv": 0.86},
                      "sub": {"sub": 1.0, "uniq": 0.55, "riv": 0.80},
                      "uniq": {"uniq": 1.0, "riv": 0.90},
                      "riv": {"riv": 1.0}})


def pool_one_frame():
    """③ 三个名额、五个样本分布在四个帧上（同一外观）：不得把名额押在同一帧。"""
    sp = [mk("3m", f"b1_f{fr:02d}_{c:02d}_3m.png", "look", 0.10)
          for fr, c in ((1, 0), (1, 1), (2, 0), (3, 0), (4, 0))] + \
         [mk("4m", "b1_f09_00_4m.png", "riv", 0.10),
          mk("4m", "b1_f10_00_4m.png", "riv", 0.10)]
    return synth(sp, {"look": {"look": 1.0, "riv": 0.70}, "riv": {"riv": 1.0}})


class TestSelectionReasons(unittest.TestCase):
    """三条理由在合成形状上各有一条会红的断言。"""

    def test_fixture_shape_is_what_we_claimed(self):
        """覆盖守卫：夹具先确认它真的复现了记录里的形状，否则后面的绿灯没有意义。"""
        p = pool_orphan()
        G, fr, labs, li = p["G"], p["frames"], p["labels"], p["by_label"]["9s"]
        main = [i for i in li if fr[i] in (1, 2, 3)]
        orph = [i for i in li if fr[i] == 12]
        self.assertGreaterEqual(min(G[a, b] for a in main for b in main if a != b), 0.985,
                                "主力群内部不像‘同一外观’，夹具的 d 没起作用")
        self.assertLess(max(G[a, b] for a in orph for b in main), min(p["bars"][i] for i in main),
                        "孤儿亚群居然能撑住主力群的查询：夹具没有再现出病")
        mo = np.mean([G[a, b] for a in orph for b in orph if a != b]) - \
            np.mean([rival_sim(G, labs, li, t) for t in orph])
        mm = np.mean([G[a, b] for a in main for b in main if a != b]) - \
            np.mean([rival_sim(G, labs, li, t) for t in main])
        self.assertGreater(mo, mm, "margin 没有奖励孤立体：这条夹具没在钉记录里那个反例")
        # 旧规则靠“更锐”抢名额：夹具里孤儿亚群的帧间差异更大，Laplacian 方差必须更高
        c_orph = max(clarity(f) for f, fn in p["faces"] if "f12" in fn)
        c_main = min(clarity(f) for f, fn in p["faces"] if fn in [p["fns"][i] for i in main])
        self.assertGreater(c_orph, c_main,
                           "孤儿亚群不比主力群锐：旧规则的变异体不会选它们，①的检验是假的")

    def test_orphan_subgroup_must_not_take_two_slots(self):
        """①：孤儿亚群再锐、margin 再高，也不许占两个名额。"""
        p = pool_orphan()
        got = run_pick(bpb.pick_variants, p, "9s")
        orph = [i for i in p["by_label"]["9s"] if p["frames"][i] == 12]
        self.assertLessEqual(len(set(got) & set(orph)), 1,
                             f"孤儿亚群（帧 12 两张）又占了两个名额：{[p['fns'][i] for i in got]}")
        w = wins_of(p, "9s", got)
        bw = best_wins(p, "9s", bpb.MAX_VARIANTS)
        self.assertEqual(w, bw, f"贪心选出的集合赢格 {w} 低于可达上限 {bw}")

    def test_unique_appearance_keeps_a_slot(self):
        """②：与已选都不像的唯一外观帧，必须在一个名额都不少地留在库里。"""
        p = pool_novel()
        got = run_pick(bpb.pick_variants, p, "1p")
        self.assertIn(5, got, f"唯一外观帧（帧 6）被顶出库：{[p['fns'][i] for i in got]}")

    def test_slots_spread_over_source_frames(self):
        """③：素材分布在 4 个帧时，三个名额不得全落在同一个帧。"""
        p = pool_one_frame()
        got = run_pick(bpb.pick_variants, p, "3m")
        fr = {p["frames"][i] for i in got}
        self.assertEqual(len(fr), min(bpb.MAX_VARIANTS, 4),
                         f"名额没铺开：帧 {sorted(fr)}，选中 {[p['fns'][i] for i in got]}")

    def test_degenerate_inputs_are_not_eaten(self):
        """退化输入：单样本类全收；素材全在一帧时不得崩、也不得多收。"""
        p = synth([mk("5s", "b1_f01_00_5s.png", "look", 0.10),
                   mk("6s", "b1_f02_00_6s.png", "riv", 0.10)],
                  {"look": {"look": 1.0, "riv": 0.5}, "riv": {"riv": 1.0}})
        self.assertEqual(len(run_pick(bpb.pick_variants, p, "5s")), 1, "单样本类被择优吃掉了一张")
        q = synth([mk("5s", f"b1_f01_0{k}_5s.png", "look", 0.10) for k in (0, 1, 2, 3)],
                  {"look": {"look": 1.0}})
        got = run_pick(bpb.pick_variants, q, "5s")
        self.assertTrue(1 <= len(got) <= bpb.MAX_VARIANTS)
        self.assertEqual(lofo_wins(q["G"], q["frames"], q["bars"], got, q["by_label"]["5s"]), 0,
                         "全在一帧的素材不该被算成有 LOFO 支撑")

    def test_selection_does_not_depend_on_input_order(self):
        """建库遍历顺序变了，选择不得变“质”。

        分两档说：合成夹具里故意放了几何上完全对称的样本（同外观、同帧、同 d），
        五级排序键全部相等，选 A 还是选 B 在数学上等价 —— 所以这一档只要求
        （张数、源帧多重集、赢格数）不变；真实素材那边样本互不对称，直接要求文件名集合
        逐位不变（那才是会写进 provenance 的东西）。生产遍历是 sorted(os.listdir)，
        同码重建已实测逐字节相同，所以这里不是给一条实际会飘的规则开后门。
        """
        p = pool_orphan()
        li = p["by_label"]["9s"]
        sig = lambda got: (len(got), sorted(p["frames"][i] for i in got), wins_of(p, "9s", got))
        base = sig(run_pick(bpb.pick_variants, p, "9s"))
        for shift in range(1, len(li)):
            sub = dict(p, by_label={"9s": li[shift:] + li[:shift]})
            self.assertEqual(sig(run_pick(bpb.pick_variants, sub, "9s")), base,
                             f"把类内样本轮转 {shift} 位就换了选择质量：{base}")

    def test_real_material_selection_is_order_invariant(self):
        """真实素材（几何上无对称）：轮转输入顺序必须选到同一批文件。"""
        drift = []
        for style, lab, _banned, _need in WATCH:
            p = load_style(style)
            li = p["by_label"][lab]
            base = {p["fns"][i] for i in run_pick(bpb.pick_variants, p, lab)}
            for shift in range(1, len(li)):
                sub = dict(p, by_label={lab: li[shift:] + li[:shift]})
                got = {p["fns"][i] for i in run_pick(bpb.pick_variants, sub, lab)}
                if got != base:
                    drift.append((style, lab, shift, sorted(got ^ base)))
                    break
        self.assertFalse(drift, f"遍历顺序改变真实素材的择优结果（provenance 会飘）：{drift}")


# ---------------------------------------------------------------- 真素材

def load_style(style):
    """-> pool（与 synth 同形状），但 sig 来自生产尺子 core_sig(真实牌面)。"""
    if style in STYLE_CACHE:
        return STYLE_CACHE[style]
    src = os.path.join(bpb.TILES, style)
    det = TencentGridDetector()
    labs, fns, sigs, faces = [], [], [], []
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".png"):
            continue
        lab = fn[:-4].split("_")[-1].split("#")[0]
        if lab not in bpb.ALL_KINDS:
            continue                      # 非牌类标签由建库端报错拦，这里只跳过
        img = cv2.imread(os.path.join(src, fn))
        if img is None or not img.size:
            continue
        face = det.extract_face(img)
        labs.append(lab)
        fns.append(fn)
        sigs.append(bpb.core_sig(face))
        faces.append((face, fn))
    G = bpb.gram(sigs)
    frames = [bpb.frame_of(fn) for fn in fns]
    by_label = defaultdict(list)
    for i, lab in enumerate(labs):
        by_label[lab].append(i)
    pool = {"labels": labs, "fns": fns, "frames": frames, "G": G,
            "faces": faces, "sig": dict(zip(fns, sigs)),
            "by_label": by_label,
            "bars": bpb.rival_bars(labs, frames, G)}
    STYLE_CACHE[style] = pool
    return pool


# 历史病格（实测过的退化形状，不是猜的）：
#   banned     = 旧规则选中、不得再回来的那张
#   need_frame = 必须至少保住一张模板的源帧（唯一外观帧：掉了就不是错一格，是掉整帧的框）
WATCH = [("tencent", "9s", "b1_f12_03_9s.png", None),
         ("weile", "8p", "f03_10_8p.png", None),
         ("zj", "1p", None, 8)]


class TestRealMaterial(unittest.TestCase):
    """规则在生产尺子 + 真实素材上的三条断言：优于旧规则、与盘上库一致、可复现。"""

    @classmethod
    def setUpClass(cls):
        cls.pools = {s: load_style(s) for s, _l, _b, _f in WATCH}

    def test_current_rule_is_no_worse_than_legacy(self):
        worse = []
        for style, lab, _banned, _need in WATCH:
            p = self.pools[style]
            li = p["by_label"][lab]
            if len(li) < 2:
                continue
            new = run_pick(bpb.pick_variants, p, lab)
            old = run_pick(legacy_pick, p, lab)
            wn, wo = wins_of(p, lab, new), wins_of(p, lab, old)
            bw = best_wins(p, lab, bpb.MAX_VARIANTS)
            worse.append((style, lab, wo, wn, bw, len(old), len(new)))
        print("\n[择优] 类  旧赢格 -> 新赢格 (可达上限)  旧张数/新张数")
        for st, lab, wo, wn, bw, no, nn in worse:
            print(f"      {st}/{lab}: {wo} -> {wn} (上限 {bw})  {no}/{nn}")
        bad = [(st, lab, wo, wn) for st, lab, wo, wn, _b, _a, _c in worse if wn < wo]
        self.assertFalse(bad, f"新规则在这些类上比旧规则更差：{bad}")
        self.assertTrue(any(wn > wo or nn > no for _st, _lab, wo, wn, _b, _a, _c in worse),
                        "三个病格上新规则一次都没赢过旧规则 -> 这次改动没被这些类需要")

    def test_current_rule_hits_the_brute_force_bound(self):
        """贪心得打到全局最优：真素材上枚举本类全部三元组，没人在赢格数上胜过它。

        这条是“择优规则”与“评测口径”之间的桥：LOFO 评测里那两个病格能修好，先必须
        在库内口径上不输给任意一组变体。
        """
        for style, lab, _banned, _need in WATCH:
            p = self.pools[style]
            got = run_pick(bpb.pick_variants, p, lab)
            bw = best_wins(p, lab, bpb.MAX_VARIANTS)
            w = wins_of(p, lab, got)
            if bw is None:
                continue
            self.assertEqual(w, bw, f"{style}/{lab} 贪心赢格 {w} 低于暴力上限 {bw}："
                                    f"{[p['fns'][i] for i in got]}")

    def test_regression_cells_stay_fixed(self):
        """旧病的两个方向都钉住：弱样本不得回来，唯一外观帧不得被顶出去。"""
        for style, lab, banned, need in WATCH:
            p = self.pools[style]
            got = {p["fns"][i] for i in run_pick(bpb.pick_variants, p, lab)}
            if banned:
                if not os.path.exists(os.path.join(bpb.TILES, style, banned)):
                    self.skipTest(f"素材 {style}/{banned} 已不在盘上，本断言的参照件需重钉")
                self.assertNotIn(banned, got,
                                 f"{style}/{lab} 又把 {banned} 选进库了（旧病格）")
            if need is not None:
                fr = [bpb.frame_of(fn) for fn in got]
                self.assertIn(need, fr,
                              f"{style}/{lab} 把唯一外观帧 {need} 顶出库了：{sorted(got)}")

    def test_bank_on_disk_matches_the_rule(self):
        """接线不变量：盘上 provenance 必须等于当前规则的重算结果（改了规则没重建库就会红）。"""
        for style, lab, _banned, _need in WATCH:
            p = self.pools[style]
            got = {p["fns"][i] for i in run_pick(bpb.pick_variants, p, lab)}
            with open(os.path.join(bpb.TILES, style, "provenance.json"),
                      encoding="utf-8") as fp:
                prov = json.load(fp)
            on_disk = {v for k, v in prov.items() if k.split("#")[0] == lab}
            self.assertEqual(on_disk, got,
                             f"{style} {lab} 盘上库与择优规则的重算结果不一致：先跑 "
                             f"py -3.10 -X utf8 localtest/build_platform_bank.py {style}")


def mutate_check():
    """把规则换坏，确认上面每条断言真的会变红（绿灯不是因为没测到）。

    返回 0 = 四个变异探针全部被拦下。这里不用 unittest 跑，直接重算事实：变异体
    要验的是“断言敏感”，而不是“框架能报错”。
    """
    rc = 0

    def report(name, detected, detail):
        nonlocal rc
        rc |= 0 if detected else 1
        print(f"[mutate] {name} 被报出：{detected}   {detail}")

    # 探针 A：择优换回旧规则（清晰度 + 0.985 互异门）
    p = pool_orphan()
    orph = {i for i in p["by_label"]["9s"] if p["frames"][i] == 12}
    old = run_pick(legacy_pick, p, "9s")
    new = run_pick(bpb.pick_variants, p, "9s")
    wo, wn = wins_of(p, "9s", old), wins_of(p, "9s", new)
    report("① 旧规则把孤儿亚群占满两个名额", len(orph & set(old)) >= 2 and wo < wn,
           f"旧选 {[p['fns'][i] for i in old]} 赢格 {wo} vs 新选 {wn}")

    q = pool_one_frame()
    old2 = run_pick(legacy_pick, q, "3m")
    new2 = run_pick(bpb.pick_variants, q, "3m")
    sp = lambda got: len({q["frames"][i] for i in got})          # noqa: E731 只在这里用
    report("③ 旧规则的名额与帧覆盖会收缩", sp(old2) < sp(new2),
           f"旧覆盖 {sp(old2)} 帧({len(old2)} 张) vs 新 {sp(new2)} 帧({len(new2)} 张)")

    # 探针 B：关掉②级（它不在旧规则里，旧规则拿 0.985 当互异门反而保护新外观）
    n = pool_novel()
    no_novel = run_pick(without_novel, n, "1p")
    base = run_pick(bpb.pick_variants, n, "1p")
    report("② 关掉新外观级后唯一外观帧被顶出库",
           5 not in no_novel and 5 in base,
           f"无②：{[n['fns'][i] for i in no_novel]} / 有②：{[n['fns'][i] for i in base]}")

    # 探针 C：真素材上必须能回到历史病格（否则真素材那三条是空跑）
    #   banned     那个方向的病是旧规则留下的 -> 拿旧规则当变异体
    #   need_frame 那个方向的病是“纯赢格目标”新引入的 -> 拿关掉②级当变异体
    for style, lab, banned, need in WATCH:
        pool = load_style(style)
        new3 = run_pick(bpb.pick_variants, pool, lab)
        wn3 = wins_of(pool, lab, new3)
        mut = legacy_pick if banned else without_novel
        bad3 = run_pick(mut, pool, lab)
        wo3 = wins_of(pool, lab, bad3)
        fns_bad = {pool["fns"][i] for i in bad3}
        if banned:
            # 弱样本回来：赢格数必须不高于新规则（否则“钉住它”只是在看旧病）
            hit, why = banned in fns_bad and wo3 <= wn3, f"选回弱样本 {banned}"
        else:
            # 孤立外观被顶出：赢格数持平是必然的（本帧样本对别人的赢格没有贡献），
            # 所以这一支只能拿“帧还在不在”当判准——这恰好就是纯赢格目标的盲区。
            hit = need not in [bpb.frame_of(fn) for fn in fns_bad]
            why = f"把唯一外观帧 {need} 顶出库（赢格 {wo3} == 新 {wn3}，目标函数看不出来）"
        report(f"真素材 {style}/{lab}：变异体{why}", hit,
               f"变异体赢格 {wo3} vs 新 {wn3}；选 {sorted(fns_bad)}")

    print(f"[mutate] 探针{'全部被拦下' if rc == 0 else '有未被拦下的（灯是假的）'}")
    return rc


def report():
    """--report：把夹具形状与真素材上的选择/赢格打出来，用于定断言前先核数字。"""
    buf = ["# bank 择优规则夹具报告（localtest/test_bank_selection.py --report）"]
    for name, pool, lab in (("① 孤儿亚群", pool_orphan(), "9s"),
                            ("② 唯一外观", pool_novel(), "1p"),
                            ("③ 同外观多帧", pool_one_frame(), "3m")):
        li = pool["by_label"][lab]
        buf.append(f"\n=== {name} 夹具：{lab} 共 {len(li)} 张 ===")
        for t in li:
            buf.append(f"  {pool['fns'][t]:<26} 帧{str(pool['frames'][t]):>4}"
                       f"  own {own_sim(pool['G'], li, pool['frames'], t):.3f}"
                       f"  对手线 {pool['bars'][t]:.3f}"
                       f"  margin {own_sim(pool['G'], li, pool['frames'], t) - rival_sim(pool['G'], pool['labels'], li, t):+.3f}"
                       f"  锐度 {clarity(pool['faces'][t][0]):.1f}")
        for tag, picker in (("新", bpb.pick_variants), ("旧", legacy_pick)):
            got = run_pick(picker, pool, lab)
            buf.append(f"  {tag}规则选：{[pool['fns'][i] for i in got]} "
                       f"赢格 {wins_of(pool, lab, got)}"
                       f" 上限 {best_wins(pool, lab, bpb.MAX_VARIANTS)}")
    buf.append("\n=== 真素材（生产尺子 core_sig + 全库对手线）===")
    for style, lab, banned, need in WATCH:
        pool = load_style(style)
        li = pool["by_label"][lab]
        buf.append(f"\n[{style}/{lab}] {len(li)} 张样本，参照件 banned={banned} need_frame={need}")
        for t in li:
            buf.append(f"  {pool['fns'][t]:<26} 帧{str(pool['frames'][t]):>4}"
                       f"  own {own_sim(pool['G'], li, pool['frames'], t):.3f}"
                       f"  对手线 {pool['bars'][t]:.3f}")
        for tag, picker in (("新", bpb.pick_variants), ("旧", legacy_pick), ("无②", without_novel)):
            got = run_pick(picker, pool, lab)
            buf.append(f"  真素材 {tag}规则选：{[pool['fns'][i] for i in got]} "
                       f"赢格 {wins_of(pool, lab, got)} 上限 {best_wins(pool, lab, bpb.MAX_VARIANTS)}")
    out = os.path.join(REPO, "build", "bank_selection_report.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        fp.write("\n".join(buf) + "\n")
    print(f"[ok] -> {out}")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.argv.remove("--report")
        sys.exit(report())
    if "--mutate" in sys.argv:
        sys.argv.remove("--mutate")
        sys.exit(mutate_check())
    unittest.main(verbosity=2)
