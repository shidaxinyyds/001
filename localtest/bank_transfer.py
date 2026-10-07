# -*- coding: utf-8 -*-
"""跨平台模板复用与「全库开放」的定量对照：不补一张图，把三个悬着的问题一次问清。

三个问题：
  1) **generic 到底什么水平**。它不在 STYLE_PLATFORM_WHITELIST 的任何 value 里，按
     tencent_grid_detector 的规则「没在白名单出现的风格 = 全平台可用」，于是六个库
     **全部自由抢分**。这是可量化的生产行为，不该用「兼容所有平台」这句文案来评价。
  2) **蜀山未限平台的代价**。templates_shushan 有 35 条模板、体积是其他 bank 的 8 倍，
     它会参与所有未登记平台的抢分。雀神做过同型实验，结论是净修正 9 / 净泄漏 8；
     蜀山从没测过。
  3) **bank 能否跨平台复用**。同厂/同引擎的麻将 App 经常共用牌面美术资源，若能复用
     就等于省掉一整批补拍——这直接决定前面给你的缺口清单要不要打折。

口径与 localtest/cv_eval.py 完全一致（extract_face 80x120 -> 灰度 NORM_MINMAX ->
CORE_BOX 88x56 -> TM_CCOEFF_NORMED 滑动取 max），所以两张表的数字可以互相比较。
**不含**生产的 has_btn 分支与 2m/3m 笔画特判——那些是叠加增益，所以本文件的绝对值
会比线上略低，但配置之间的**差**仍然有效。

用法: py -3.10 localtest\bank_transfer.py
输出: localtest/bank_transfer.txt
"""
import collections
import importlib
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from cv_eval import FACE, core_of, load, ncc_matrix  # noqa: E402

# 有人工核对 GT 的裁片目录。shushan 不在 tiles/ 下（它的 bank 来自 harvest_shushan），
# 所以蜀山在这里只能当**施污染的一方**被测，不能被评测。
SAMPLE_PLATFORMS = ("weile", "tuyou", "queshen", "jj")
BANK_MODULES = {
    "base": "recognition.templates_data",       # 腾讯底座：34 类，每类 1 模板
    "weile": "recognition.templates_weile",
    "tuyou": "recognition.templates_tuyou",
    "queshen": "recognition.templates_queshen",
    "jj": "recognition.templates_jj",
    "shushan": "recognition.templates_shushan",
}
GATE = 0.40        # yolo_detector 覆盖层的放行线：低于它模板就没资格抢 YOLO 的标签

_stats = collections.Counter()


def to_face(t):
    """模板统一成 (120,80,3) uint8。库里混着异形尺寸（底座 1z 是 77x56）和
    int64（蜀山整库），不归一就没法在同一把尺子下比——顺手把这些数据质量问题
    计数报出来，别让它藏在平均分里。"""
    a = np.asarray(t)
    if a.dtype != np.uint8:
        _stats["dtype_fixed"] += 1
        a = a.astype(np.uint8)
    if a.ndim == 2:
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    if a.shape[0] == 120 and a.shape[1] == 80:
        return a
    _stats["reshaped"] += 1
    if a.shape[0] < 60 or a.shape[1] < 40:
        _stats["suspiciously_tiny"] += 1
    return cv2.resize(a, (FACE[0], FACE[1]), interpolation=cv2.INTER_AREA)


def bank_core(bgr):
    g = cv2.cvtColor(to_face(bgr), cv2.COLOR_BGR2GRAY)
    return core_of(cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX))


def load_bank(modname):
    """-> [(label, core)]。键里的 "#2"/"#s4" 是同类多模板变体后缀，剥掉还原基础标签。"""
    mod = importlib.import_module(modname)
    d = dict(getattr(mod, "TEMPLATES_BGR", None) or {})
    out = []
    for k, v in d.items():
        try:
            out.append((str(k).split("#")[0], bank_core(v)))
        except Exception as e:                       # 单条坏模板不该拖垮整个实验
            _stats["broken_template:%s" % k] += 1
            print("[skip] %s %s: %s" % (modname, k, e))
    return out


def classify(q, cores):
    """-> (top_label, top_score)：同标签多模板取 max，再跨标签取 max（生产口径）。"""
    if not cores:
        return None, 0.0
    sc = ncc_matrix(q, cores)
    best_lab, best_s = None, -9.0
    for (lab, _c), s in zip(cores, sc):
        if s > best_s:
            best_lab, best_s = lab, float(s)
    return best_lab, best_s


def build_configs():
    banks = {k: load_bank(m) for k, m in BANK_MODULES.items()}
    for k, v in banks.items():
        print("bank %-8s 条目=%3d 覆盖类=%2d" % (k, len(v), len(set(l for l, _ in v))))
    cfg = {}
    cfg["base"] = banks["base"]
    cfg["base+shushan"] = banks["base"] + banks["shushan"]
    cfg["ALL"] = [c for k in banks for c in banks[k]]          # generic 的真实行为
    for p in SAMPLE_PLATFORMS:
        cfg["own:%s" % p] = banks[p]
        cfg["base+own:%s" % p] = banks[p] + banks["base"]
    return banks, cfg


def main():
    lines = []

    def w(s=""):
        lines.append(s)
        print(s)

    samples = {}
    for p in SAMPLE_PLATFORMS:
        samples[p] = load(p)
        w("素材 %-8s 裁片=%3d 帧=%2d 类=%2d" % (
            p, len(samples[p]), len({f for f, _l, _g in samples[p]}),
            len({l for _f, l, _g in samples[p]})))
    w()

    banks, cfg = build_configs()
    w("\n模板库规模与数据质量问题：")
    for k, n in sorted(_stats.items()):
        w("  %-24s %d" % (k, n))
    w("\n列名：base=仅腾讯底座  base+shushan=底座+蜀山  ALL=六库全开(generic现状) "
      "base+own=底座+本平台库")

    # 明细表就是主表。曾想过先加一张紧凑的 top1 矩阵，但那要么丢掉放行线信息、
    # 要么把全量 matchTemplate 重复跑一遍（六库全开时这是几分钟级开销），不划算。
    names = ["base", "base+shushan", "ALL"] + ["base+own:%s" % p for p in SAMPLE_PLATFORMS]
    w("\n=== 1) 各平台样本在不同库配置下的表现 ===")
    for p in SAMPLE_PLATFORMS:
        w("-- %s（裁片 %d）--" % (p, len(samples[p])))
        for n in names:
            ok, tot, scs = 0, 0, []
            for _f, lab, g in samples[p]:
                pred, sc = classify(g, cfg[n])
                if pred is None:
                    continue
                tot += 1
                ok += (pred == lab)
                scs.append(sc)
            gate = 100.0 * sum(1 for s in scs if s >= GATE) / max(len(scs), 1)
            w("   %-16s acc=%5.1f%% (%3d/%3d)  mean=%.3f  >=0.40: %5.1f%%" % (
                n, 100.0 * ok / max(tot, 1), ok, tot,
                float(np.mean(scs)) if scs else 0.0, gate))
        w("   （ALL 与 base 的差 = 六库全开的净效果；base+shushan 与 base 的差 = 蜀山未限平台的净效果）")

    # 跨平台直接复用：只拿别家单一库来认，这才是"能不能不拍图"的答案
    w("\n=== 2) bank 跨平台直接复用（只用另一家库，不含底座）===")
    w("%-8s" % "样本" + "".join("%12s" % b for b in SAMPLE_PLATFORMS))
    for p in SAMPLE_PLATFORMS:
        row = "%-8s" % p
        for b in SAMPLE_PLATFORMS:
            ok = tot = 0
            for _f, lab, g in samples[p]:
                pred, _sc = classify(g, cfg["own:%s" % b])
                if pred is None:
                    continue
                tot += 1
                ok += (pred == lab)
            row += "%11.1f%%" % (100.0 * ok / max(tot, 1))
        w(row)
    w("  对角线=自家库复用（应最高）；非对角线越高说明两平台牌风越接近，越可省拍。")

    with open(os.path.join(HERE, "bank_transfer.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
