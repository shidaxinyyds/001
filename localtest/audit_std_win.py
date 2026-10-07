# -*- coding: utf-8 -*-
"""对拍审计 StdAnalyzer 的胡牌判定与听口计算。

为什么要先审计再加玩法：新增玩法全部复用同一套 can_win/find_waits，现在 6 个
玩法在用它，扩到 16 个就是把这个函数里的任何缺陷乘以 16。

ground truth 刻意采用**不同算法**以降低"同源同错"的概率：
- StdAnalyzer._complete_melds 按「最左非零张必须被消费」递归（剪枝正确性依赖推理）
- 本脚本 brute 穷举所有面子组合（刻子+顺子全集），不做任何最左假设
- 赖子：本脚本把 w 张赖子**显式枚举分配到 33 个非赖子牌位**（stars and bars），
  每种分配单独走一次无赖子纯判定；这与 StdAnalyzer 在递归里"顺带补赖子"是不同路径

赖子数 0~3 用全枚举（双向严格对拍）；4 张时全枚举是 63 万级 × 每手，改为随机采样
分配，只能单向定论：brute 说能胡而 analyzer 说不能 → 确定是 analyzer 漏判。

样本不是随机发牌，而是按胡型骨架构造后再扰动（见 gen_structured）：随机 14 张几
乎永不胡，两实现只会在「都不能胡」上虚假一致，对拍退化成空转。
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

from modes import MODES, LAIZI_CONFIG, get_mode
from std.std_analyzer import StdAnalyzer, index_to_mpsz, KOKUSHI_IDX, TERMINAL_HONOR_IDX


def fmt(counts):
    """按 MPSZ 展开成可读手牌（重复张出现重复，便于人工复算）。"""
    return "".join(index_to_mpsz(i) for i in range(34) for _ in range(counts[i]))

# ---------- brute：全面子组合穷举 ----------

PUNGS = tuple((i, i, i) for i in range(34))
SEQS = tuple((i, i + 1, i + 2) for i in range(27) if i % 9 <= 6)


@lru_cache(maxsize=None)
def decomp(c, need, seq_ok):
    """c 恰好拆成 need 个面子且无剩余。穷举全部面子候选，不假设消费顺序。"""
    if need == 0:
        return sum(c) == 0
    if sum(c) < need * 3:
        return False
    cands = PUNGS + (SEQS if seq_ok else ())
    for m in cands:
        if c[m[0]] >= 1 and c[m[1]] >= (2 if m[0] == m[1] else 1) and c[m[2]] >= (3 if m[0] == m[2] else 1):
            lst = list(c)
            for t in m:
                lst[t] -= 1
            if decomp(tuple(lst), need - 1, seq_ok):
                return True
    return False


def pure_standard(c, seq_ok):
    """无赖子标准胡：4 面子 + 1 将。"""
    if sum(c) != 14:
        return False
    for p in range(34):
        if c[p] >= 2:
            lst = list(c)
            lst[p] -= 2
            if decomp(tuple(lst), 4, seq_ok):
                return True
    return False


def pure_seven(c):
    return sum(c) == 14 and sum(x // 2 for x in c) >= 7


def pure_kokushi(c):
    if sum(c) != 14:
        return False
    if any(c[i] for i in range(34) if i not in KOKUSHI_IDX):
        return False
    distinct = sum(1 for i in KOKUSHI_IDX if c[i] >= 1)
    return distinct == 13 and any(c[i] >= 2 for i in KOKUSHI_IDX)


def brute_win(counts, fixed_melds, rules, rng=None):
    """独立判胡。所有胡型均在「赖子分配完毕的纯 14 张」上判定，
    因此不需要任何针对赖子的专用分支（七对/国士的赖子用法被分配枚举自然覆盖）。
    赖子>3 时全枚举过大，改随机抽 300 个分配方案，结论只能单向：
    brute 说能胡而 analyzer 说不能 → analyzer 漏判；反之不定论。"""
    lz = rules.get("laizi")
    lz_list = [] if lz is None or lz == LAIZI_CONFIG else (
        list(lz) if isinstance(lz, (list, tuple, set, frozenset)) else [lz])
    c = list(counts)
    wild = 0
    for i in lz_list:
        wild += c[i]
        c[i] = 0
    needed = 4 - fixed_melds
    if sum(c) + wild != needed * 3 + 2:
        return False
    seq_ok = bool(rules.get("sequences", True))
    # 刻意与 analyzer 对齐：用**含赖子**的原始 counts 判“带幺九”。
    # 红中既是赖子又是字牌，它能否单独满足带幺九要求属规则语义选择，不在此断言。
    if rules.get("need_terminals") and not any(counts[i] for i in TERMINAL_HONOR_IDX):
        return False

    slots = [i for i in range(34) if i not in lz_list]
    if wild <= 3:
        allocs = combinations_with_replacement(slots, wild)
        exact = True
    else:
        allocs = {tuple(sorted(rng.choices(slots, k=wild))) for _ in range(300)}
        exact = False

    for alloc in allocs:
        t = list(c)
        # 不设“同牌有效张数 ≤ 4”上限：鬼牌是独立的物理牌，手上 4 张 3m 再用一张鬼做
        # 3m 成将完全合法（早期版本加了 t[k]>4 就丢弃分配的限制，会把这类真胡误报成
        # “analyzer 诈胡”）。实物张数 ≤ 4 的约束由造牌器保证，不靠这里。
        for k in alloc:
            t[k] += 1
        if pure_standard(t, seq_ok):
            return True
        if rules.get("seven_pairs") and pure_seven(t):
            return True
        if rules.get("kokushi") and pure_kokushi(t):
            return True
    return False if exact else "unsat"


# ---------- 造手牌：按胡型骨架构造，再扰动 ----------
#
# 为什么不能用纯随机（这是本脚本早期版本的致命缺陷）：实测随机 14 张里约 1080 手
# 才命中 1 手胡牌，两个实现几乎只在「一起说不能胡」上达成一致，对拍等于空转，
# 报出来的「0 漏判 0 诈胡」不构成证据。而且 sequences=False / need_all_pungs /
# kokushi=False 这类**规则开关**，只有构造出「含顺子的胡牌」「国士牌面」才可能被
# 触发，随机样本永远碰不到。
#
# 因此先按目标胡型拼出结构，再注入鬼牌，最后随机替换 0~2 张，使样本同时覆盖
# 正例（骨架完整、应当能胡）与紧邻反例（差一张、通常不能胡）。
# 造牌器**不下任何胡牌结论**，期望值一律由 brute_win 判，避免 ground truth 被污染。

def _real_tiles(avail, lz_list):
    lz = set(lz_list)
    return [i for i in avail if i not in lz]


def _seq_candidates(real):
    s = set(real)
    return [(i, i + 1, i + 2) for i in range(27)
            if i % 9 <= 6 and i in s and (i + 1) in s and (i + 2) in s]


def _groups_of(shape, real, rules, rng):
    """返回 14 张牌的分组（面子/对子/单张）；牌数不够或结构不适用返回 None。"""
    if shape == "kokushi":
        k = [i for i in real if i in KOKUSHI_IDX]
        if len(k) < 13:
            return None
        g = [[i] for i in k]
        pr = rng.choice(k)
        g.append([pr, pr])
        return g
    if shape == "pairs":
        if len(real) < 7:
            return None
        return [[x, x] for x in rng.sample(real, 7)]

    seqs = _seq_candidates(real)
    allow_seq = bool(rules.get("sequences", True))
    usable_seqs = seqs if (seqs and allow_seq) else []
    if shape == "seq" and not usable_seqs:
        return None          # 牌集里拼不出顺子，该形态无意义

    g = []
    for _ in range(4):
        if shape == "seq":
            g.append(list(rng.choice(usable_seqs)))
        elif shape == "pung" or not usable_seqs or rng.random() < 0.5:
            x = rng.choice(real)
            g.append([x, x, x])
        else:
            g.append(list(rng.choice(usable_seqs)))
    pr = rng.choice(real)
    g.append([pr, pr])
    cnt = {}
    for m in g:
        for t in m:
            cnt[t] = cnt.get(t, 0) + 1
    if any(v > 4 for v in cnt.values()):
        return None          # 同一张牌超 4 份，物理不成立，弃样
    return g


def gen_structured(avail, lz_list, rules, rng, shape, wild=0, break_n=0):
    counts = [0] * 34
    groups = _groups_of(shape, _real_tiles(avail, lz_list), rules, rng)
    if groups is None:
        return None
    for m in groups:
        for t in m:
            counts[t] += 1

    # 注鬼牌：每个分组最多替换到「只剩一张实牌」，避免造出纯鬼组这种无信息样本
    if wild and lz_list:
        budget = [len(m) - 1 for m in groups]
        order = [i for i in range(len(groups)) if budget[i] > 0]
        rng.shuffle(order)
        placed = 0
        for gi in order:
            take = min(budget[gi], wild - placed)
            for _ in range(take):
                counts[groups[gi][-1]] -= 1
                groups[gi].pop()
                placed += 1
            if placed >= wild:
                break
        if placed < wild:
            return None
        left, guard = placed, 0
        while left > 0:
            guard += 1
            if guard > 400:
                return None
            g = lz_list[rng.randrange(len(lz_list))]
            if counts[g] < 4:
                counts[g] += 1
                left -= 1

    # 扰动：随机换位，把样本从「必胡骨架」推向决策边界
    real = _real_tiles(avail, lz_list)
    for _ in range(break_n):
        src = [i for i in range(34) if i not in lz_list and counts[i] > 0]
        dst = [i for i in real if counts[i] < 4]
        if not src or not dst:
            break
        s = rng.choice(src)
        d = rng.choice(dst)
        counts[s] -= 1
        counts[d] += 1

    if sum(counts) != 14:
        return None
    return counts


def _laizi_list(rules):
    lz = rules.get("laizi")
    if lz is None or lz == LAIZI_CONFIG:
        return []
    return list(lz) if isinstance(lz, (list, tuple, set, frozenset)) else [lz]


def main():
    rng = random.Random(20261003)
    lines = []
    bad = []
    thin = []
    std_keys = [k for k, v in MODES.items() if v.get("analyzer") == "std"]
    lines.append("对拍 analyzer=std 的玩法（%d 个）：%s" % (len(std_keys), std_keys))
    lines.append("采样：胡型骨架构造 + 鬼牌 0~3 + 扰动 0~2（正例与紧邻反例均入样本）")
    lines.append("")

    shapes = ["mix", "pung", "seq", "pairs", "kokushi"]
    for key in std_keys:
        rules = get_mode(key)
        avail = rules["available"]
        lz_list = _laizi_list(rules)
        diffs_pos, diffs_neg, unsat = [], [], 0
        n_pos = n_neg = n_tried = 0
        for shape in shapes:
            for wild in ([0, 1, 2, 3] if lz_list else [0]):
                for break_n in (0, 1, 2):
                    for _ in range(10):
                        counts = gen_structured(avail, lz_list, rules, rng, shape, wild, break_n)
                        if counts is None:
                            continue
                        n_tried += 1
                        mine = brute_win(counts, 0, rules, rng=rng)
                        theirs = StdAnalyzer.can_win(list(counts), 0, rules)
                        if mine == "unsat":
                            # 采样未定（鬼牌>3）不能当成诈胡：brute 未抽到不代表 analyzer 错
                            unsat += 1
                            continue
                        if mine:
                            n_pos += 1
                        else:
                            n_neg += 1
                        if mine != theirs:
                            (diffs_pos if theirs else diffs_neg).append(
                                (fmt(counts), shape, wild, theirs, mine))
        line = (f"  {key:<9} wild={'Y' if lz_list else 'N':<1} 样本={n_tried:<4} "
                f"正例={n_pos:<4} 反例={n_neg:<4} "
                f"漏判={len(diffs_neg)} 诈胡={len(diffs_pos)} 未定={unsat}")
        lines.append(line)
        # 正例覆盖不足 = 对拍退化回空转，必须报警而不是当成“通过”
        if n_pos < 10:
            thin.append(key)
        if diffs_neg or diffs_pos:
            bad.append(key)
            for d in (diffs_neg[:4] + diffs_pos[:4]):
                lines.append(f"      {key} 形态={d[1]} 鬼{d[2]} 手={d[0]} analyzer={d[3]} brute={d[4]}")

    lines.append("")
    if bad:
        lines.append("结论：存在需查证的差异 → " + ", ".join(bad))
    elif thin:
        lines.append("结论：无差异，但正例覆盖不足（不可视为已验证）→ " + ", ".join(thin))
    else:
        lines.append("结论：未发现差异，且每个玩法均有足量正例覆盖 ✓")
    out = os.path.join(HERE, "audit_std_win.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if (bad or thin) else 0


if __name__ == "__main__":
    sys.exit(main())
