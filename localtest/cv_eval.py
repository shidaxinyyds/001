# -*- coding: utf-8 -*-
"""留一帧交叉验证（LOFO）：在真实数据量下比较 NCC 与其替代方案的**泛化**能力。

为什么必须先跑这个而不是直接换模型：
  1) **防泄漏**。tiles/ 里的 309 张裁片来自我用来评分的那批帧。若拿全部裁片训练、
     再在同一批帧上评分，准确率是假的（上一轮的评分尺子已经出过一次可信度事故，
     不能再犯第二次）。所以按**帧**切分：验证帧的任何牌都不出现在训练集中。
     必须按帧而不是按牌切——同帧的牌共享桌布、光照、尺度和截图链路，按牌随机切
     会让验证集与训练集几乎同分布，指标虚高。
  2) **区分"方法好"与"参数多"**。309 张 / 32 类这个量级，几百上千参数的模型
     （CNN、全维逻辑回归）大概率记住训练集而不是学会泛化。而"每类分数校准"只有
     2×类数 个参数，同样能从 309 张里估出来。把它们放同一把尺子下，才知道收益
     到底来自哪。
  3) **可部署性**。Android/Chaquopy 侧不希望新增 sklearn/onnxruntime 依赖。
     numpy 可表达的方案（NCC、分数校准、线性模型矩阵乘）优先。

方法族：
  ncc       生产口径的简化版：每类多个 88x56 子窗口模板，matchTemplate 取 max，
            跨模板再取 max。**不含**生产的 has_btn 分支与 2m/3m 笔画特判——
            那些是叠加项，先比方法族本身。
  ncc_cal   ncc + 每类分数校准。TM_CCOEFF_NORMED 的 max 响应**跨类不可比**：
            模板内容密度不同（1s 一只鸟大片白 vs 9s 九根密排），噪声峰值的期望
            就不同，于是存在系统性类别偏置。校准把每类分数标准化
            (s - mu_c) / sigma_c，mu_c/sigma_c 由训练集**留一自身**估计。
  proto     最近类中心：整图皮尔逊相关。参数只有类数×图像大小，是"最朴素的可
            部署方案"的下限参照。
  logreg    纯 numpy 多类 softmax 回归（特征为降采样灰度），参数量远大于上面
            几种——用来验证"参数更多是否真的更好"。

用法: py -3.10 localtest\cv_eval.py [--styles weile,tuyou,queshen,jj]
输出: localtest/cv_eval.txt
"""
import argparse
import collections
import os
import re
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

TILE_RE = re.compile(r"[1-9][mps]|[1-7]z")
FACE = (80, 120)                 # 生产 extract_face 目标尺寸 (w, h)
CORE_BOX = (16, 104, 12, 68)     # 生产 _build_cores 的牌面子窗口 (y0,y1,x0,x1)


def load(style):
    """-> [(frame, label, gray_face)]，灰度已按生产口径归一化到 0..255。"""
    src = os.path.join(HERE, "tiles", style)
    recs = []
    if not os.path.isdir(src):
        return recs
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".png"):
            continue
        parts = fn[:-4].split("_")
        if len(parts) != 3 or not parts[0].startswith("f") or not parts[0][1:].isdigit():
            continue
        lab = parts[2]
        if not TILE_RE.fullmatch(lab):
            continue
        img = cv2.imread(os.path.join(src, fn))
        if img is None:
            continue
        face = TencentGridDetector.extract_face(img, FACE)
        g = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        recs.append((int(parts[0][1:]), lab,
                     cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX)))
    return recs


def core_of(g):
    y0, y1, x0, x1 = CORE_BOX
    return g[y0:y1, x0:x1]


# 模板增广：只扰动**模板侧**，query 保持真实。
#
# 为什么没有亮度缩放与平移（先试过）：TM_CCOEFF_NORMED 先减均值再除标准差，
# 对 g -> a*g+b 严格不变；matchTemplate 的滑动窗口又对平移不变。实测把
# b80/b125/sh 加进模板库，argmax 一位都没变（80.5% -> 80.5%）——它们是数学意义上
# 的恒等变换，只是白付 6 倍算力。这个负结果记下来，避免以后再走一遍。
#
# 这次选的是 NCC 真正管不住的东西：非线性响应（gamma）、重采样/失焦造成的
# 结构模糊（bl*）、尺度归一化不彻底留下的残余缩放（sc*）、加性噪声（n*）。
_RNG = np.random.RandomState(11)
# 噪声固定成常量矩阵：牌面尺寸恒为 (120, 80)，预生成一次可保证结果可复现，
# 不会因为调用顺序不同而往实验里掺入不可控随机性。
_NOISE8 = _RNG.normal(0, 8, (FACE[1], FACE[0])).astype(np.float32)


def _blur(g, k):
    return cv2.GaussianBlur(g, (k, k), 0)


def _rescale(g, k):
    """先把牌面缩 k 倍再回到原尺寸：缩小模拟失焦/远距离采集，
    放大后裁中心模拟牌在 face 内占比变化。"""
    h, w = g.shape
    if k < 1.0:
        small = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    big = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    y0 = (big.shape[0] - h) // 2
    x0 = (big.shape[1] - w) // 2
    return big[y0:y0 + h, x0:x0 + w]


AUGS = {
    "none": lambda g: g,
    "gm70": lambda g: (255 * (g / 255.0) ** 0.70).astype(np.uint8),
    "gm14": lambda g: (255 * (g / 255.0) ** 1.40).astype(np.uint8),
    "bl3": lambda g: _blur(g, 3),
    "sc90": lambda g: _rescale(g, 0.90),
    "sc112": lambda g: _rescale(g, 1.12),
    "n8": lambda g: np.clip(g.astype(np.float32) + _NOISE8, 0, 255).astype(np.uint8),
}


def ncc_matrix(q, cores):
    """-> np.array[len(cores)]：query 对每个模板的 matchTemplate 峰值。"""
    return np.array([cv2.matchTemplate(q, c, cv2.TM_CCOEFF_NORMED).max() for _l, c in cores],
                    dtype=np.float64)


def fit_ncc(train, aug_names=("none",)):
    return [(lab, core_of(AUGS[an](g))) for _f, lab, g in train for an in aug_names]


def predict_ncc(q, cores, labels):
    sc = ncc_matrix(q, cores)
    best = collections.defaultdict(float)
    for lab, s in zip(labels, sc):
        if s > best[lab]:
            best[lab] = float(s)
    return best


def fit_cal(train, cores, labels):
    """每类 self-score 的均值/标准差，估时排除样本自身模板（否则 mu 被高估）。"""
    per_cls = collections.defaultdict(list)
    for i, (_f, lab, g) in enumerate(train):
        sc = ncc_matrix(g, cores)
        others = [s for j, (l, s) in enumerate(zip(labels, sc)) if l == lab and j != i]
        if others:
            per_cls[lab].append(max(others))
    mu, sd = {}, {}
    for lab, vals in per_cls.items():
        mu[lab] = float(np.mean(vals))
        sd[lab] = float(np.std(vals)) + 1e-6
    return mu, sd


def predict_cal(scores, mu, sd):
    out = {}
    for lab, s in scores.items():
        if lab in mu:
            out[lab] = (s - mu[lab]) / sd[lab]
    return out


def fit_proto(train):
    cent = {}
    grp = collections.defaultdict(list)
    for _f, lab, g in train:
        grp[lab].append(g.astype(np.float32))
    for lab, arr in grp.items():
        cent[lab] = np.mean(arr, axis=0)
    return cent


def predict_proto(q, cent):
    v = q.astype(np.float32).ravel()
    v = v - v.mean()
    nv = np.linalg.norm(v) + 1e-6
    out = {}
    for lab, c in cent.items():
        w = c.ravel() - c.mean()
        out[lab] = float(np.dot(v, w) / (nv * (np.linalg.norm(w) + 1e-6)))
    return out


def _lg_x(g):
    return cv2.resize(g, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()


def fit_logreg(train, l2=1.0, iters=400, lr=0.5, trace=None, trace_every=0):
    """纯 numpy 的多类 softmax 回归（不依赖 sklearn）。

    为什么自己写而不是装 sklearn：目标形态必须是 Android/Chaquopy 能跑的，
    多类线性分类器推理就是一次矩阵乘 + argmax，numpy 足够，不需要新增依赖。
    l2 默认给得很强：训练集只有几十张牌、特征 768 维，弱正则只会记住训练集。

    trace/trace_every：每 trace_every 步把当时的权重交给回调（用于记录收敛曲线）。
    这里每步都是**全批**梯度更新，所以它是“优化步收敛曲线”，不是样本轮次 epoch；
    报告里必须说清这一点，否则会被读成“跑了 400 轮”。
    """
    X = np.array([_lg_x(g) for _f, _l, g in train])
    mu, sd = X.mean(0), X.std(0) + 1e-6
    X = (X - mu) / sd
    ys = [l for _f, l, _g in train]
    cls = sorted(set(ys))
    ci = {c: i for i, c in enumerate(cls)}
    Y = np.zeros((len(ys), len(cls)), np.float32)
    for i, y in enumerate(ys):
        Y[i, ci[y]] = 1.0
    W = np.zeros((X.shape[1], len(cls)), np.float32)
    b = np.zeros(len(cls), np.float32)
    n = max(len(ys), 1)
    for it in range(iters):
        z = X @ W + b
        z -= z.max(1, keepdims=True)
        p = np.exp(z)
        p /= p.sum(1, keepdims=True) + 1e-9
        err = p - Y
        W -= lr * ((X.T @ err) / n + l2 * W)
        b -= lr * err.mean(0)
        if trace is not None and (it + 1) % max(1, trace_every) == 0:
            trace(it + 1, {"W": W.copy(), "b": b.copy(), "mu": mu, "sd": sd, "cls": cls})
    return {"W": W, "b": b, "mu": mu, "sd": sd, "cls": cls}


def predict_logreg(q, m):
    x = (_lg_x(q) - m["mu"]) / m["sd"]
    return dict(zip(m["cls"], (x @ m["W"] + m["b"]).tolist()))


def lofo(recs, lines, style, l2s=(0.003, 0.03, 0.3, 1.0), augs=("none",), do_cal=True,
         tag="", curve=None, trace_l2=None, trace_every=50):
    frames = sorted({r[0] for r in recs})
    if len(frames) < 3:
        lines.append(f"  {style:8} 帧数 {len(frames)} < 3，跳过（LOFO 无意义）")
        return None
    hit = collections.defaultdict(int)
    tot = 0
    tr_tot = 0
    conf = collections.defaultdict(int)
    for vf in frames:
        train = [r for r in recs if r[0] != vf]
        val = [r for r in recs if r[0] == vf]
        if not train:
            continue
        cores = fit_ncc(train, augs)
        labels = [l for l, _c in cores]
        mu, sd = fit_cal(train, cores, labels) if do_cal else ({}, {})
        cent = fit_proto(train)
        # 为什么要扫 l2 而不是只测一个默认值：拿一个被自己超参压坏的方案去
        # 否定整条路线是不成立的。必须给线性分类器一个公平的最优工作点，
        # 它仍然输才能说"这个数据量下就是不行"。
        # 同时记训练集自评：用来区分"过拟合"（train 高 val 低）与"根本没学到"
        # （两边都低）。ncc 的 train 自评永远是 100%（自身模板就在训练集里），
        # 所以只给 logreg 记。
        lgs = {}
        for l2 in l2s:
            tr = None
            if curve is not None and l2 == trace_l2:
                def tr(step, m, _val=val, _train=train, _l2=l2, _curve=curve):
                    """只记录不改行为：把当时权重在 val/train 上各扫一遍累进曲线。"""
                    cell = _curve[(_l2, step)]
                    for i, rows in ((0, _val), (2, _train)):
                        ok = 0
                        for _f, lab, g in rows:
                            s = predict_logreg(g, m)
                            ok += int(max(s, key=s.get) == lab)
                        cell[i] += ok
                        cell[i + 1] += len(rows)
            lgs[l2] = fit_logreg(train, l2=l2, trace=tr, trace_every=trace_every)
        for l2, m in lgs.items():
            for _f, lab, g in train:
                s = predict_logreg(g, m)
                if max(s, key=s.get) == lab:
                    hit[f"tr@{l2}"] += 1
        tr_tot += len(train)
        for _f, lab, g in val:
            tot += 1
            s_ncc = predict_ncc(g, cores, labels)
            if max(s_ncc, key=s_ncc.get) == lab:
                hit["ncc"] += 1
            s_cal = predict_cal(s_ncc, mu, sd) if mu else {}
            if s_cal and max(s_cal, key=s_cal.get) == lab:
                hit["ncc_cal"] += 1
            s_pro = predict_proto(g, cent)
            if max(s_pro, key=s_pro.get) == lab:
                hit["proto"] += 1
            for l2, m in lgs.items():
                s_lg = predict_logreg(g, m)
                top = max(s_lg, key=s_lg.get)
                if top == lab:
                    hit[f"lg@{l2}"] += 1
                elif l2 == l2s[0]:
                    # 混淆只在最弱正则（模型自由度最充分）的那一档统计，
                    # 否则报的是欠拟合导致的噪声，而不是真正的易混对
                    conf[(lab, top)] += 1
    lines.append(f"  {style:8}{tag} 帧={len(frames)} 样本={tot}  "
                 + "  ".join(f"{k}={hit[k]}/{tot}={100.0 * hit[k] / max(tot, 1):.1f}%"
                             for k in (["ncc"] if not do_cal else
                                       ["ncc", "ncc_cal", "proto"])
                             + [f"lg@{l2}" for l2 in l2s]))
    if tr_tot and l2s:
        lines.append(f"           logreg 训练集自评（过拟合量）: "
                     + "  ".join(f"l2={l2}:{100.0 * hit[f'tr@{l2}'] / tr_tot:.0f}%"
                                 for l2 in l2s))
    if conf:
        tp = sorted(conf.items(), key=lambda x: -x[1])[:6]
        lines.append(f"           logreg 主要混淆: " + "  ".join(f"{a}->{b}x{n}" for (a, b), n in tp))
    return hit, tot


def lofo_prefilter(recs, lines, style, ks=(3, 5, 8)):
    """粗到细（cascaded）验证：先用最便宜的“最近类中心”筛掉大多数类，
    再只对留下的 k 个类做 matchTemplate。

    为什么单独测这个而不是直接说“换个分类器更快”：实测 identify 侧 76% 的时间
    花在 matchTemplate（每帧 ~1000 次），而分类器在泛化精度上输 NCC。粗到细
    是唯一能“既要 NCC 的精度、又要线性分类器的速度”的形式：分类器不当裁判，
    只当预筛。代价必须量化：预筛把真值漏出 top-k 就直接错。

    不需要额外的 matchTemplate：复用全量 NCC 分数向量，只限制 argmax 的候选集。
    同时报“预期模板比对次数”与全量的比值，那就是可拿到的加速比上限。
    """
    frames = sorted({r[0] for r in recs})
    if len(frames) < 3:
        lines.append(f"  {style:8} 帧数 {len(frames)} < 3，跳过（LOFO 无意义）")
        return None
    hit = collections.defaultdict(int)
    n_tm_full = n_tm_casc = tot = 0
    for vf in frames:
        train = [r for r in recs if r[0] != vf]
        val = [r for r in recs if r[0] == vf]
        if not train:
            continue
        cores = fit_ncc(train)
        labels = [l for l, _c in cores]
        per_cls_tm = collections.Counter(labels)
        cent = fit_proto(train)
        for _f, lab, g in val:
            tot += 1
            s_ncc = predict_ncc(g, cores, labels)
            s_pro = predict_proto(g, cent)
            order = sorted(s_pro, key=lambda l: -s_pro[l])
            if max(s_ncc, key=s_ncc.get) == lab:
                hit["ncc"] += 1
            for k in ks:
                keep = set(order[:k])
                sub = {l: v for l, v in s_ncc.items() if l in keep}
                if sub and max(sub, key=sub.get) == lab:
                    hit[f"ncc@top{k}"] += 1
            n_tm_full += len(labels)
            for k in ks:
                n_tm_casc += sum(per_cls_tm[l] for l in order[:k])
    lines.append(f"  {style:8} 帧={len(frames)} 样本={tot}  "
                 + "  ".join(f"{k}={hit[k]}/{tot}={100.0 * hit[k] / max(tot, 1):.1f}%"
                             for k in ["ncc"] + [f"ncc@top{k}" for k in ks]))
    ratio = n_tm_casc / max(n_tm_full * len(ks), 1)
    lines.append(f"           模板比对量：粗到细平均每样本 {n_tm_casc / len(ks) / max(tot, 1):.1f} 次"
                 f" vs 全量 {n_tm_full / max(tot, 1):.1f} 次 -> 上限加速 {1.0 / max(ratio, 1e-9):.1f}x")
    return hit, tot


L2S = (0.003, 0.03, 0.3, 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--styles", default="weile,tuyou,queshen,jj")
    ap.add_argument("--mode", default="full", choices=["full", "aug", "single", "prefilter"],
                    help="full=方法族对比；aug=全量增广 vs 无增广；"
                         "single=逐个增广项单独测（找性价比）；"
                         "prefilter=粗到细级联（类中心预筛 + NCC 精分）")
    a = ap.parse_args()
    styles = [s.strip() for s in a.styles.split(",") if s.strip()]
    data = {s: load(s) for s in styles}
    if a.mode == "prefilter":
        lines = ["=== 粗到细级联 LOFO（proto 预筛 top-k，只对 k 个类做 NCC）==="]
        gh = collections.defaultdict(int)
        gt = 0
        for style in styles:
            if not data[style]:
                lines.append(f"  {style:8} 无素材")
                continue
            res = lofo_prefilter(data[style], lines, style)
            if res:
                hit, tot = res
                gt += tot
                for kk, vv in hit.items():
                    gh[kk] += vv
        lines.append("\n  合计 样本=" + str(gt) + "  "
                     + "  ".join(f"{kk}={gh[kk]}/{gt}={100.0 * gh[kk] / max(gt, 1):.1f}%"
                                for kk in ["ncc", "ncc@top3", "ncc@top5", "ncc@top8"]))
        lines.append("\n注：ncc 是全量模板比对的基线；ncc@topk 是只保留类中心前 k 名后的精度。"
                     "\n    两者差距就是预筛的精度代价，加速比则是模板比对次数的比值。")
        text = "\n".join(lines) + "\n"
        with open(os.path.join(HERE, "cv_eval_prefilter.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        print(text)
        return 0
    if a.mode == "aug":
        configs = [("无增广", ("none",)), ("全增广", tuple(AUGS))]
        l2s, do_cal = (), False
    elif a.mode == "single":
        # 为什么还要拆单项：全量增广只多 +1.6pt，却把模板库（也就是每帧每牌的
        # matchTemplate 次数）乘 7。Android 上这个代价不可接受，必须找出真正
        # 起作用的那一两项，只带它们上线。
        configs = [("基线", ("none",))] + [(f"+{k}", ("none", k))
                                           for k in AUGS if k != "none"]
        l2s, do_cal = (), False
    else:
        configs = [("", ("none",))]
        l2s, do_cal = L2S, True
    lines = ["=== 留一帧交叉验证（训练集不含验证帧的任何牌）==="]
    if a.mode in ("aug", "single"):
        lines.append("aug/single 模式下 ncc_cal 未测（已证实有害），logreg 也未测，只看 ncc。")
    grand = {name: collections.defaultdict(int) for name, _c in configs}
    g_n = {name: 0 for name, _c in configs}
    # 收敛曲线：只追最弱正则那一档（模型自由度最充分，最容易“记住训练集”），
    # 拿它看 val 是否平台、train-val 差距是否拉开，比只报一个终值准确得多。
    curve = collections.defaultdict(lambda: [0, 0, 0, 0]) if l2s else None
    trace_l2 = l2s[0] if l2s else None
    for style in styles:
        recs = data[style]
        if not recs:
            lines.append(f"  {style:8} 无素材")
            continue
        for name, augs in configs:
            res = lofo(recs, lines, style, l2s=l2s, augs=augs, do_cal=do_cal,
                       tag=f" [{name}]" if name else "",
                       curve=curve, trace_l2=trace_l2)
            if res:
                hit, tot = res
                for k, v in hit.items():
                    grand[name][k] += v
                g_n[name] += tot
    keys = (["ncc"] if a.mode == "aug" else ["ncc", "ncc_cal", "proto"]) \
        + [f"lg@{l2}" for l2 in l2s]
    for name, _c in configs:
        if not g_n[name]:
            continue
        g = grand[name]
        lines.append(f"\n  {'合计'} [{name or 'full'}] 样本={g_n[name]}  "
                     + "  ".join(f"{k}={g[k]}/{g_n[name]}={100.0 * g[k] / g_n[name]:.1f}%"
                                 for k in keys if k in g))
    lines.append("\n注：本表是**泛化**准确率（未见过的帧），必然低于在训练帧上的自评；")
    lines.append("    两者差距就是过拟合程度，是判断'能不能替掉 NCC'的唯一依据。")
    if curve:
        lines.append("\n=== 训练过程曲线（softmax 回归，全批梯度步；这不是样本轮次 epoch）===")
        lines.append(f"    只追 l2={trace_l2}（最弱正则）：每档都在 LOFO 的每个折上重训一次，")
        lines.append("    下表是把所有折的 val/train 命中数累加后算百分比。")
        for step in sorted(k[1] for k in curve):
            vh = sum(c[0] for k, c in curve.items() if k[1] == step)
            vt = sum(c[1] for k, c in curve.items() if k[1] == step)
            th = sum(c[2] for k, c in curve.items() if k[1] == step)
            tt = sum(c[3] for k, c in curve.items() if k[1] == step)
            va, tra = 100.0 * vh / max(vt, 1), 100.0 * th / max(tt, 1)
            lines.append(f"    step={step:<5} val={va:5.1f}%  train={tra:5.1f}%  "
                         f"差距={tra - va:+5.1f}pt")
        lines.append("    读法：val 曲线提前走平而 train 继续上→过拟合；两条都低→这数据量下就是没学到。")
    text = "\n".join(lines) + "\n"
    out = os.path.join(HERE, {"aug": "cv_eval_aug.txt", "single": "cv_eval_single.txt",
                              "prefilter": "cv_eval_prefilter.txt"}
                       .get(a.mode, "cv_eval.txt"))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
