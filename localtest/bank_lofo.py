# -*- coding: utf-8 -*-
"""LOFO 版跨库对照：把「蜀山未限平台」和「bank 跨平台复用」两件事一次性量干净。

为什么还要第二版（bank_transfer.py 已经跑出数字了）：
  那张表里 `base+own:weile = 94.9%` 这类**对角线数字全部不可信**——own bank 的模板
  正是从这些裁片收割的，查询图和模板是同一张图，准确率是自拟合。上一轮的评分尺子
  已经因为"训练帧==评估帧"出过一次可信度事故（微乐自评 85.9% vs 按帧留一 65.8%），
  不能再犯第三次。所以这里把 own 换成**留一帧**版：评到某帧的牌时，该帧的模板不在库里。

  另外 bank_transfer.py 的 `base+own` 配置本身也不符合生产：set_platform_styles 里
  `owners is None or platform_key in owners` 让未列白名单的 shushan **对所有平台开放**，
  于是生产中那四个平台的扫描集一直是 底座+自家+蜀山。要知道"限掉蜀山"值多少钱，必须
  比较 base+own+shushan 与 base+own 这一对。

为什么用配对比率而不是比两个百分比：
  同一批样本上两种配置只差蜀山那 35 条模板，绝大多数牌的判定结果是一样的。比百分比
  会把"没变化"的巨大方差算进置信区间（微乐 79 张，1 个百分点≈0.8 张，噪声就能盖掉
  效应）。正确的做法只数**发生翻转的牌**：被改对几张、被改错几张，做精确二项检验。

用法: py -3.10 localtest\bank_lofo.py
输出: localtest/bank_lofo.txt
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bank_transfer import GATE, classify, load_bank  # noqa: E402
from cv_eval import core_of, load, ncc_matrix  # noqa: E402


def best_masked(sc, labs, idx):
    """在模板池的索引子集上取“同类取 max 再跨类取 max”。返回 (标签, 分数)。"""
    if not idx:
        return None, 0.0
    best_lab, best_s = None, -9.0
    for i in idx:
        s = float(sc[i])
        if s > best_s:
            best_lab, best_s = labs[i], s
    return best_lab, best_s


SAMPLE_PLATFORMS = ("weile", "tuyou", "queshen", "jj")
BANK_OF = {
    "weile": "recognition.templates_weile",
    "tuyou": "recognition.templates_tuyou",
    "queshen": "recognition.templates_queshen",
    "jj": "recognition.templates_jj",
}


def exact_binom_two_sided(k, n):
    """配对翻转的精确二项检验（H0: 改对与改错等概率）。n 很小，直接算组合数，
    不依赖 scipy——这台机器上没装，而且结论也不该被"装不装包"卡住。"""
    if n == 0:
        return 1.0
    tail_lo = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    tail_hi = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return min(1.0, 2.0 * min(tail_lo, tail_hi))


def main():
    lines = []

    def w(s=""):
        lines.append(s)
        print(s)

    base = load_bank("recognition.templates_data")
    shushan = load_bank("recognition.templates_shushan")
    w("底座=%d 条(类%d)  蜀山=%d 条(类%d)" % (
        len(base), len({l for l, _ in base}),
        len(shushan), len({l for l, _ in shushan})))

    samples = {p: load(p) for p in SAMPLE_PLATFORMS}

    w("\n=== A) generic / 腾讯 / 指尖四川 的真实水平：底座+蜀山，不含任何自家库 ===")
    w("（这组数字是干净的：模板来自腾讯底座与蜀山库，与这四个平台的裁片无关）")
    for p in SAMPLE_PLATFORMS:
        for name, cores in (("base", base), ("base+shushan", base + shushan)):
            ok, scs = 0, []
            for _f, lab, g in samples[p]:
                pred, sc = classify(g, cores)
                if pred is not None:
                    ok += (pred == lab)
                    scs.append(sc)
            n = len(scs)
            gate = 100.0 * sum(1 for s in scs if s >= GATE) / max(n, 1)
            w("   %-8s %-14s acc=%5.1f%% (%3d/%3d)  >=0.40放行线: %5.1f%%" % (
                p, name, 100.0 * ok / max(n, 1), ok, n, gate))
    w("   注：这里的样本是**已知牌风**的四个平台，对新平台是乐观上界——真实新")
    w("   平台的牌风可能跟六个库都不像，那时分数会整体掉下来，覆盖层放行但结论错。")

    w("\n=== B) 蜀山未限平台的净影响（LOFO，配对比率）===")
    w("现状=底座+自家(留一帧)+蜀山   改后=底座+自家(留一帧)")
    for p in SAMPLE_PLATFORMS:
        recs = samples[p]
        frames = sorted({f for f, _l, _g in recs})
        gain = loss = same = tot = 0
        wrong_both = 0
        for f in frames:
            own = [(lab, core_of(g)) for _fr, lab, g in recs if _fr != f]
            cur = base + own + shushan        # 生产现状
            fix = base + own                   # 把 shushan 限成只给蜀山自己用
            for _fr, lab, g in recs:
                if _fr != f:
                    continue
                pc, _sc1 = classify(g, cur)
                pf, _sc2 = classify(g, fix)
                if pc is None or pf is None:
                    continue
                tot += 1
                okc, okf = (pc == lab), (pf == lab)
                if okc and not okf:
                    loss += 1
                elif okf and not okc:
                    gain += 1
                else:
                    same += 1
                    if not okc:
                        wrong_both += 1
            # 每折只数自己帧的样本，避免同一张牌被重复计入
        disc = gain + loss
        # 现状正确数 = 两边都对 + 现状对改后错 = (same - wrong_both) + loss = tot - gain - wrong_both
        # 同理改后正确数 = tot - loss - wrong_both。gain/loss 在这里极易写反，
        # 写反的症状是“净收益的正负号刚好相反”，而且两边数字看着都很合理。
        w("   %-8s n=%3d  现状=%5.1f%%  限蜀山后=%5.1f%%  翻转: 改对%d 改错%d "
          "(净%+d)  p=%.3f  两配置都错%d" % (
              p, tot, 100.0 * (tot - gain - wrong_both) / max(tot, 1),
              100.0 * (tot - loss - wrong_both) / max(tot, 1),
              gain, loss, gain - loss, exact_binom_two_sided(gain, disc), wrong_both))
    w("   判读：净翻转数 <3 或 p>0.05 就是噪声，不值得为它改生产；两边都错的那些")
    w("   才是真正要补样本的牌——限平台救不了它们。")

    w("\n=== C) bank 跨平台复用（LOFO，不含底座，用别家现成库认自家牌）===")
    w("   %-8s" % "样本" + "".join("%12s" % b for b in SAMPLE_PLATFORMS))
    for p in SAMPLE_PLATFORMS:
        recs = samples[p]
        frames = sorted({f for f, _l, _g in recs})
        row = "   %-8s" % p
        for b in SAMPLE_PLATFORMS:
            bank = load_bank(BANK_OF[b])
            ok = tot = 0
            if b == p:
                # 自家：必须留一帧，否则又是自拟合
                for f in frames:
                    own = [(lab, core_of(g)) for _fr, lab, g in recs if _fr != f]
                    for _fr, lab, g in recs:
                        if _fr != f:
                            continue
                        pred, _s = classify(g, own)
                        if pred is not None:
                            tot += 1
                            ok += (pred == lab)
            else:
                for _f, lab, g in recs:
                    pred, _s = classify(g, bank)
                    if pred is not None:
                        tot += 1
                        ok += (pred == lab)
            row += "%11.1f%%" % (100.0 * ok / max(tot, 1))
        w(row)
    w("   读法：非对角线越高=两家牌风越接近。若某行出现「别家库 ≥ 自家 LOFO」，")
    w("   说明该平台可以**直接挂别人现成的 bank**，不必从零补拍。")

    # C 段只证明“单独拿别家库来认更准”，但生产是**多库并存**：多挂一家就会多一批
    # 模板参与抢分，可能把本来对的牌抢错。所以必须在生产口径下再测一次净效果。
    w("\n=== D) 生产口径下多挂一家现成 bank 的净效果 ===")
    w("   基准 = 底座+自家(留一帧)+蜀山；再叠加 X 后的翻转情况")
    banks = {b: load_bank(BANK_OF[b]) for b in SAMPLE_PLATFORMS}
    for p in SAMPLE_PLATFORMS:
        recs = samples[p]
        frames = sorted({f for f, _l, _g in recs})
        tally = {b: [0, 0] for b in SAMPLE_PLATFORMS}   # [改对, 改错]
        n_tot = 0
        for f in frames:
            own = [(lab, core_of(g)) for _fr, lab, g in recs if _fr != f]
            fixed = base + own + shushan
            # 把所有候选模板拼成一个池子，每个样本只跑一次 ncc_matrix，再按配置切片
            # 聚合。逐个配置重算会把算力乘以配置数，这里完全没必要。
            pool = list(fixed)
            seg = {}
            for b in SAMPLE_PLATFORMS:
                seg[b] = (len(pool), len(pool) + len(banks[b]))
                pool += banks[b]
            labs = [l for l, _c in pool]
            for _fr, lab, g in recs:
                if _fr != f:
                    continue
                # ncc_matrix 的入参是 [(label, core)]，它自己会解包丢掉标签；
                # 这里再剥一层 core 反而会让它解包失败。
                sc = ncc_matrix(g, pool)
                _pc, _sc0 = best_masked(sc, labs, list(range(len(fixed))))
                for b in SAMPLE_PLATFORMS:
                    s0, s1 = seg[b]
                    idx = list(range(len(fixed))) + list(range(s0, s1))
                    pb, _sb = best_masked(sc, labs, idx)
                    if pb is None or _pc is None:
                        continue
                    n_tot += 1
                    if pb == lab and _pc != lab:
                        tally[b][0] += 1
                    elif pb != lab and _pc == lab:
                        tally[b][1] += 1
        w("   -- %s（%d 张牌 × 4 个候选库 = %d 次配对）--" % (p, len(recs), n_tot))
        for b in SAMPLE_PLATFORMS:
            gain, loss = tally[b]
            if b == p:
                w("      +%-8s （自家模板，已在基准里）" % b)
                continue
            tag = "★ 可白捡" if gain - loss >= 3 else ("噪声" if gain == loss == 0 else "")
            w("      +%-8s 改对%d 改错%d 净%+d  p=%.3f %s" % (
                b, gain, loss, gain - loss,
                exact_binom_two_sided(gain, gain + loss), tag))

    w("\n=== E) 错误是不是“高分错”（决定新平台会不会主动帮倒忙）===")
    w("   如果错牌的分数也大量过 0.40，那 generic 不是“认不出”，而是“认错且很自信”，")
    w("   它会主动覆盖掉 YOLO 本来对的标签。")
    for p in SAMPLE_PLATFORMS:
        ok_pass = ok_n = bad_pass = bad_n = 0
        for _f, lab, g in samples[p]:
            pred, sc = classify(g, base + shushan)
            if pred is None:
                continue
            if pred == lab:
                ok_n += 1
                ok_pass += (sc >= GATE)
            else:
                bad_n += 1
                bad_pass += (sc >= GATE)
        w("   %-8s 对牌过线 %3d/%3d=%.0f%%   错牌过线 %3d/%3d=%.0f%%" % (
            p, ok_pass, ok_n, 100.0 * ok_pass / max(ok_n, 1),
            bad_pass, bad_n, 100.0 * bad_pass / max(bad_n, 1)))

    # E 段说明错牌也在 0.40 以上，但"该把线抬到哪"不能拍脑袋。这里把分数→准确率
    # 的真实曲线跑出来：它同于回答"新平台没 bank 时，多少分才值得覆盖 YOLO"。
    w("\n=== F) 底座+蜀山的分数→准确率曲线（给新平台选覆盖门槛）===")
    pooled = []
    for p in SAMPLE_PLATFORMS:
        for _f, lab, g in samples[p]:
            pred, sc = classify(g, base + shushan)
            if pred is None:
                continue
            pooled.append((p, sc, pred == lab))
    w("   分数区间        n   准确率")
    for lo, hi in ((0.0, 0.40), (0.40, 0.60), (0.60, 0.75), (0.75, 0.85),
                   (0.85, 0.93), (0.93, 1.01)):
        sel = [c for _p, s, c in pooled if lo <= s < hi]
        if not sel:
            w("   %.2f-%.2f      0     -" % (lo, hi))
            continue
        w("   %.2f-%.2f    %3d   %5.1f%%" % (lo, hi, len(sel), 100.0 * sum(sel) / len(sel)))
    w("\n   门槛效果（仅适用于**没有专属 bank** 的平台；达标则采纳模板，不达标不覆盖）")
    w("   %-6s %-8s %-12s %-12s %s" % ("门槛", "保留率", "保留内准确", "保留里仍错", "拦到的错牌"))
    n_all = len(pooled)
    bad_all = sum(1 for _p, _s, c in pooled if not c)
    for t in (0.40, 0.75, 0.85, 0.90, 0.93, 0.95):
        keep = [(s, c) for _p, s, c in pooled if s >= t]
        if not keep:
            continue
        kp = sum(1 for _s, c in keep if c)
        # 保留里仍然报错的 = 保留总数 - 保留中正确的。拦下来的才是“本来会错但被挡住”。
        # 上一版把这两者算反了（用 bad_all 减了正确数），症状是比例出现负数。
        bad_keep = len(keep) - kp
        w("   %-6.2f %-8.1f %-12.1f %-12s %.0f%%" % (
            t, 100.0 * len(keep) / n_all, 100.0 * kp / len(keep),
            "%d/%d" % (bad_keep, len(keep)),
            100.0 * (bad_all - bad_keep) / max(bad_all, 1)))
    w("   样本不均：高分段只有十几个样本，0.93 那行 92.3% 与 0.85 那行 100% 的差异")
    w("   在 1~2 张牌之间，不能当成“分数越高越差”的结论。")
    w("   读法：保留率是「敢开口报牌」的比例，拉高门槛会用少报换准确——对新平台")
    w("   宁可不报也别报错牌，因为错牌会直接进后续算分与推荐。")

    with open(os.path.join(HERE, "bank_lofo.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
