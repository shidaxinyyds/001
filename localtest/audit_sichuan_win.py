# -*- coding: utf-8 -*-
"""对拍审计 SichuanAnalyzer.can_win（川麻家族=最大流量分支）。

可疑点：_min_wild_melds 第 146 行 `if idx <= 7 and c[idx+1] >= 1` 允许
8m9m+赖子 组成「顺子」，而那需要一张不存在的 10m。不过合法的 7m8m9m（赖子当
最小张）同样只花 1 张赖子，两者在"最少赖子数"口径下数值相同，可能恰好抵消——
所以必须实测，不能靠读代码定罪。

ground truth 用不同算法：把赖子显式分配到 27 个牌位，再对纯 14 张穷举全部面子
组合（不依赖任何最左/分门假设）。花色<=2 的约束刻意与 analyzer 对齐（用**不含
赖子**的 real 计数判定），因为"赖子不能替你把第三门变成合法门"属规则语义选择。
"""
import io
import os
import random
import sys
from functools import lru_cache
from itertools import combinations_with_replacement

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "android", "app", "src", "main", "python")))

from sichuan.sichuan_analyzer import SichuanAnalyzer

PUNGS27 = tuple((i, i, i) for i in range(27))
SEQS27 = tuple((i, i + 1, i + 2) for i in range(27) if i % 9 <= 6)


@lru_cache(maxsize=None)
def decomp(c, need):
    """c 恰好拆成 need 个面子且无剩余：穷举所有面子候选。"""
    if need == 0:
        return sum(c) == 0
    if sum(c) < need * 3:
        return False
    for m in PUNGS27 + SEQS27:
        if c[m[0]] >= 1 and c[m[1]] >= (2 if m[0] == m[1] else 1) and c[m[2]] >= (3 if m[0] == m[2] else 1):
            lst = list(c)
            for t in m:
                lst[t] -= 1
            if decomp(tuple(lst), need - 1):
                return True
    return False


def pure_win(t):
    """无赖子 14 张：4 面子 + 1 将。"""
    if sum(t) != 14:
        return False
    for p in range(27):
        if t[p] >= 2:
            lst = list(t)
            lst[p] -= 2
            if decomp(tuple(lst), 4):
                return True
    return False


def pure_seven(t):
    return sum(t) == 14 and sum(x // 2 for x in t) >= 7


def brute_win(counts, rng, exact_max_wild=4):
    """独立判胡。川麻只有 27 个非鬼槽位，鬼牌 4 张的全枚举是 C(30,4)=27405（且
    decomp 带 lru_cache），完全跑得起，因此默认全分层均为**双向严格对拍**。
    早期把阈值定为 3、>3 改抽样，既丢了结论强度，又会把“采样没抽到”误报成
    analyzer 诈胡。"""
    wild = counts[27]
    c = list(counts[:27])
    if sum(c) + wild != 14:
        return False
    if len({i // 9 for i in range(27) if c[i] > 0}) > 2:
        return False
    if wild <= exact_max_wild:
        allocs = combinations_with_replacement(range(27), wild)
        exact = True
    else:
        allocs = {tuple(sorted(rng.choices(range(27), k=wild))) for _ in range(300)}
        exact = False
    for alloc in allocs:
        t = list(c)
        # 同 audit_std_win：不限制“同牌有效张数≤4”，鬼牌是独立物理牌，4 张实物+
        # 一张鬼成第五张是合法结构；这里加限制会把真胡误判成 analyzer 诈胡。
        for k in alloc:
            t[k] += 1
        if pure_win(t) or pure_seven(t):
            return True
    return False if exact else "unsat"


# ---------- 构造式采样 ----------
#
# 随机发牌在这里同样会空转：14 张随机牌几乎永不胡，两实现只会在「都不能胡」
# 上虚假一致。川麻占最大流量，必须把「全刻子」「含顺子」「七对」「三门花猪」
# 这些真实边界都造出来。

def sc_groups(shape, suits, rng):
    """在选定的 ≤2 门里拼 4 面子 + 1 将。"""
    pool = [i for s in suits for i in range(s * 9, s * 9 + 9)]
    if shape == "pairs":
        if len(pool) < 7:
            return None
        return [[x, x] for x in rng.sample(pool, 7)]
    g = []
    for _ in range(4):
        if shape == "pung":
            x = rng.choice(pool)
            g.append([x, x, x])
        else:
            seq_starts = [i for i in pool if i % 9 <= 6]
            if rng.random() < 0.5 and seq_starts:
                i = rng.choice(seq_starts)
                g.append([i, i + 1, i + 2])
            else:
                x = rng.choice(pool)
                g.append([x, x, x])
    pr = rng.choice(pool)
    g.append([pr, pr])
    cnt = {}
    for m in g:
        for t in m:
            cnt[t] = cnt.get(t, 0) + 1
    if any(v > 4 for v in cnt.values()):
        return None
    return g


def gen_structured(rng, wild_n, suit_cnt, shape, break_n):
    """suit_cnt==3 时故意造跨三门的牌面（花猪，应判不胡）；==2 时拼可胡骨架。"""
    counts = [0] * 28
    suits = rng.sample(range(3), suit_cnt)
    groups = sc_groups(shape, suits[:2] if suit_cnt > 1 else suits, rng)
    if groups is None:
        return None
    for m in groups:
        for t in m:
            counts[t] += 1
    if wild_n:
        placed = 0
        for gi in rng.sample(range(len(groups)), len(groups)):
            take = min(len(groups[gi]) - 1, wild_n - placed)
            for _ in range(take):
                counts[groups[gi][-1]] -= 1
                groups[gi].pop()
                placed += 1
            if placed >= wild_n:
                break
        if placed < wild_n:
            return None
        counts[27] = placed
    # 注鬼完成后再处理第三门：groups 与 counts 此时仍逐张对应，不会出现
    # “先挪牌再取 groups[-1]”导致的负计数。
    if suit_cnt == 3:
        # 把骨架里的 2 张**挪**进第三门（不是追加：追加会造出 16 张，再被
        # 下面的 sum!=14 全部弃样，三门层一条样本也不会产生）。
        third = suits[2]
        for _ in range(2):
            src = [i for i in range(27) if counts[i] > 0 and i // 9 != third]
            dst = [i for i in range(third * 9, third * 9 + 9) if counts[i] < 4]
            if not src or not dst:
                break
            s, d = rng.choice(src), rng.choice(dst)
            counts[s] -= 1
            counts[d] += 1
    for _ in range(break_n):
        src = [i for i in range(27) if counts[i] > 0]
        dst = [i for i in range(27) if counts[i] < 4]
        if not src or not dst:
            break
        s, d = rng.choice(src), rng.choice(dst)
        counts[s] -= 1
        counts[d] += 1
    if sum(counts) != 14 or any(v < 0 for v in counts):
        return None
    return counts


def idx_repr(counts):
    out = []
    for i in range(28):
        for _ in range(counts[i]):
            out.append(f"{i % 9 + 1}{'mps红'[i // 9 if i < 27 else 3]}" if i < 27 else "红")
    return " ".join(out)


def main():
    rng = random.Random(20261003)
    lines = []
    miss, fraud, unsat, checked = [], [], 0, 0
    n_pos = n_neg = 0
    empty_layers = []
    lines.append("采样：胡型骨架构造（全刻/含顺/七对）× 鬼牌 0~4 × 扰动 0~2 × 门数 2/3")
    # 按「赖子数 × 门数」分层：赖子越多越容易暴露分门 DP 的边界，三门用于验花猪判定
    for wild_n in range(0, 5):
        for suit_cnt in (2, 3):
            m = f = 0
            layer_n = layer_pos = 0
            for shape in ("mix", "pung", "pairs"):
                for break_n in (0, 1, 2):
                    for _ in range(16):
                        counts = gen_structured(rng, wild_n, suit_cnt, shape, break_n)
                        if counts is None:
                            continue
                        mine = brute_win(counts, rng)
                        theirs = SichuanAnalyzer.can_win(list(counts), 0)
                        checked += 1
                        layer_n += 1
                        if mine == "unsat":
                            # 未定不能计成诈胡：brute 没抽到不代表 analyzer 错
                            unsat += 1
                            continue
                        if mine:
                            n_pos += 1
                            layer_pos += 1
                        else:
                            n_neg += 1
                        if mine and not theirs:
                            m += 1
                            if len(miss) < 4:
                                miss.append((wild_n, suit_cnt, idx_repr(counts)))
                        elif theirs and not mine:
                            f += 1
                            if len(fraud) < 4:
                                fraud.append((wild_n, suit_cnt, idx_repr(counts)))
            lines.append(f"  赖子{wild_n} 门{suit_cnt}: 样本={layer_n:<4} 正例={layer_pos:<4} 漏判={m} 诈胡={f}")
            # 某层零样本 = 造牌器在那一层废了，不能当成“该层已验证”
            if layer_n == 0:
                empty_layers.append((wild_n, suit_cnt))

    lines.append(f"\n共比对 {checked} 手（正例={n_pos} 反例={n_neg} 采样未定={unsat}）")
    for tag, cases in (("漏判(analyzer 判不胡，实际能胡)", miss), ("诈胡(analyzer 判胡，实际不能胡)", fraud)):
        if cases:
            lines.append(f"  {tag}:")
            for c in cases:
                lines.append(f"      赖子{c[0]} 门{c[1]} 手={c[2]}")
    bad = bool(miss or fraud)
    thin = n_pos < 200 or empty_layers
    lines.append("结论：" + ("存在真实缺陷 → 必须修" if bad else
                             ("无差异，但覆盖不足（正例过少或存零样本层 {}），不可视为已验证".format(empty_layers) if thin else
                              "未发现差异，且正/反例均有足量覆盖 ✓")))
    out = os.path.join(HERE, "audit_sichuan_win.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if (bad or thin) else 0


if __name__ == "__main__":
    sys.exit(main())
