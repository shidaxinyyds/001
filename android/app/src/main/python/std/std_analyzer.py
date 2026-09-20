# -*- coding: utf-8 -*-
"""通用地方玩法规则引擎（modes 中 analyzer="std" 的玩法：
std_tdh / wh_kk / db_qh / hz_bd / gd_hz / cs_zz）。

与 SichuanAnalyzer（28 型、定缺）互补：本分析器处理带字牌的 34 型牌集，
行为完全由 modes.MODES 字段数据驱动：

- laizi            赖子/百搭/鬼牌：胡牌判定枚举其充当任意牌（含作将）；
                   赖子永不做弃牌候选（鬼牌不许打出）。
- sequences=False  只能碰杠不能吃（转转胡）：顺子分解路径整体关闭。
- need_all_pungs   胡牌结构必须全刻子+一将（转转/碰碰类）。
- need_terminals   胡牌手必须带幺九/字牌（东北穷胡严格判定）。
- need_open        必须开口才能胡（武汉开口翻）：视觉暂无"已开口"状态，
                   对门清听牌给软提示，不做硬拦截。
- kokushi          允许国士无双胡型。
- fan_wild_per_use 每用一张赖子加一番（杭州百搭爆头）。

输出条目与 SichuanAnalyzer.analyze_discards 同构（tile/ukeire/shanten/ev/
reason/ting_tiles/ting_details），并额外给 max_fan/fan_names 供算番展示。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional, Tuple

SUIT_NAMES = {0: "万", 1: "筒", 2: "条"}
_MP = "mps"
# 幺九/字牌（穷胡"带幺九"判定与国士共用）
TERMINAL_HONOR_IDX = (0, 8, 9, 17, 18, 26) + tuple(range(27, 34))
KOKUSHI_IDX = (0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33)


def index_to_mpsz(idx: int) -> str:
    if 27 <= idx <= 33:
        return f"{idx - 26}z"
    return f"{idx % 9 + 1}{_MP[idx // 9]}"


def index_to_chinese(idx: int) -> str:
    if 27 <= idx <= 33:
        return "东南西北白发中"[idx - 27]
    return f"{idx % 9 + 1}{SUIT_NAMES[idx // 9]}"


@lru_cache(maxsize=65536)
def _complete_melds(t: Tuple[int, ...], melds: int, seq_ok: bool, wild: int) -> bool:
    """t 中的 real 牌 + 至多 wild 张百搭，恰好拆成 melds 个面子（无剩余）。

    递归按"最左非零张必须被消费"组织，天然去重；面子含纯赖子刻。
    """
    if melds == 0:
        return sum(t) == 0
    if melds < 0:
        return False
    first = -1
    for i, c in enumerate(t):
        if c > 0:
            first = i
            break
    if first == -1:
        return wild >= melds * 3  # 全部用纯赖子刻
    avail_real = sum(t)
    if avail_real + wild < melds * 3:
        return False
    lst = list(t)
    # 分支1：刻子（first 处 real 1~3 张 + 赖子补到 3）
    for r in (1, 2, 3):
        if lst[first] < r:
            break
        w = 3 - r
        if w > wild:
            continue
        lst[first] -= r
        if _complete_melds(tuple(lst), melds - 1, seq_ok, wild - w):
            return True
        lst[first] += r
    # 分支2：顺子（first 必占 1 张；中/张可缺用赖子补；不跨花色边界）
    if seq_ok and first < 27 and first % 9 <= 6:
        for a in (0, 1):
            for b in (0, 1):
                if lst[first + 1] < a or lst[first + 2] < b:
                    continue
                w = 2 - a - b
                if w > wild:
                    continue
                lst[first] -= 1
                lst[first + 1] -= a
                lst[first + 2] -= b
                if _complete_melds(tuple(lst), melds - 1, seq_ok, wild - w):
                    return True
                lst[first] += 1
                lst[first + 1] += a
                lst[first + 2] += b
    return False  # first 无处可去 → 该分配失败


def _has_terminal_honor(counts: List[int]) -> bool:
    return any(counts[i] > 0 for i in TERMINAL_HONOR_IDX)


def _suits_present(counts: List[int]) -> Tuple[List[int], bool]:
    num = [i for i in range(27) if counts[i] > 0]
    return sorted({i // 9 for i in num}), any(counts[i] > 0 for i in range(27, 34))


class StdAnalyzer:
    """数据驱动的地方玩法分析核心；rules 传 modes.get_mode(key) 字典。"""

    # ---------- 拆分与剥离 ----------

    @staticmethod
    def split_wild(counts: List[int], rules: Dict) -> Tuple[List[int], int]:
        """把赖子从计数里剥离（赖子在 34 型中占自己的槽位）。"""
        laizi = rules.get("laizi")
        c = list(counts)
        wild = 0
        if laizi is not None and 0 <= laizi < 34:
            wild = c[laizi]
            c[laizi] = 0
        return c, wild

    # ---------- 特殊胡型 ----------

    @staticmethod
    def is_seven_pairs(c: List[int], wild: int, fixed_melds: int) -> bool:
        """七对：门清 14 张；赖子先给单张配对(每张产 1 对)，剩余两两自成对。

        贪心最优：赖子花在单张上 1 张产 1 对，花在纯对上 2 张才产 1 对，
        故多用单张不劣。4 张同牌算两对（龙七对）由 x//2 天然覆盖。"""
        if fixed_melds > 0 or sum(c) + wild != 14:
            return False
        pairs = sum(x // 2 for x in c)
        singles = sum(x % 2 for x in c)
        need = min(singles, wild)
        return pairs + need + (wild - need) // 2 >= 7

    @staticmethod
    def is_kokushi(c: List[int], wild: int, fixed_melds: int) -> bool:
        if fixed_melds > 0 or sum(c) + wild != 14:
            return False
        distinct = sum(1 for i in KOKUSHI_IDX if c[i] >= 1)
        if distinct + wild < 13:
            return False
        pair_done = any(c[i] >= 2 for i in KOKUSHI_IDX)
        if not pair_done and wild == 0 and sum(c) == 14:
            return False  # 14 张必有一张第 2 份；无赖子时第 2 份却在非幺九牌上 → 不成立
        need = (13 - distinct) + (0 if pair_done else 1)
        return need <= wild

    # ---------- 胡牌核心 ----------
    # 注：need_all_pungs（转转/碰碰胡）无需独立分支——把顺子路径关掉后，
    # 4 面子 + 1 将的标准分解就是全刻子结构，与规则字段 sequences=False 同源。

    @classmethod
    def can_win(cls, counts: List[int], fixed_melds: int, rules: Dict) -> bool:
        c, wild = cls.split_wild(list(counts), rules)
        needed = 4 - fixed_melds
        if sum(c) + wild != needed * 3 + 2:
            return False
        seq_ok = bool(rules.get("sequences", True))
        if rules.get("need_terminals") and not _has_terminal_honor(list(counts)):
            return False

        if rules.get("kokushi", False) and wild <= 4 and cls.is_kokushi(c, wild, fixed_melds):
            return True
        if rules.get("seven_pairs", False) and cls.is_seven_pairs(c, wild, fixed_melds):
            return True
        return cls._standard_win(c, wild, needed, seq_ok)

    @staticmethod
    def _standard_win(c: List[int], wild: int, needed: int, seq_ok: bool) -> bool:
        """4 面子 + 1 将；面子内赖子替代由 _complete_melds 精确枚举。

        将的枚举：同一牌位实扣 real ∈ {2,1,0} 张，其余 2-real 张用赖子补
        （4 张同牌=暗刻+单吊、纯赖子作将均覆盖；赖子本体已在 split_wild
        剥离，不会进入枚举报）。"""
        for p in range(34):
            for real in (2, 1, 0):
                if real > c[p] or 2 - real > wild:
                    continue
                t = list(c)
                t[p] -= real
                if _complete_melds(tuple(t), needed, seq_ok, wild - (2 - real)):
                    return True
        return False

    # ---------- 向听 / 听牌 ----------

    @classmethod
    def calculate_shanten(cls, counts: List[int], fixed_melds: int, rules: Dict) -> int:
        """-1=已胡(14张), 0=听牌, N=N向听。胡/听判定精确（逐张试胡），
        更高向听为保守估计（面子/搭子/对子计数，赖子逐张降档），风格与
        SichuanAnalyzer.calculate_shanten 一致。"""
        c, wild = cls.split_wild(list(counts), rules)
        total = sum(c) + wild + fixed_melds * 3
        if total % 3 == 2 and cls.can_win(list(counts), fixed_melds, rules):
            return -1
        # 注意：必须传**原始含赖子**的 counts——is_tenpai 内部自行 split_wild；
        # 传剥除后的 c 会同时破坏 (3k+1) 张数校验与赖子可用性（漏报听牌）。
        if total % 3 == 1 and cls.is_tenpai(list(counts), fixed_melds, rules):
            return 0
        needed = 4 - fixed_melds
        melds = 0
        t = list(c)
        for i in range(34):
            m = t[i] // 3
            melds += m
            t[i] -= m * 3
        if rules.get("sequences", True):
            for suit in range(3):
                for n in range(7):
                    base = suit * 9 + n
                    m = min(t[base], t[base + 1], t[base + 2])
                    if m:
                        for k in range(3):
                            t[base + k] -= m
                        melds += m
        pairs = sum(1 for x in t if x >= 2)
        partials = sum(1 for x in t if x == 1)
        useful = min(needed, melds)
        taatsu = min(needed - useful, pairs - 1 if pairs else 0)
        taatsu += min(needed - useful - taatsu, partials // 2)
        sh = 2 * (needed - useful) - taatsu - (1 if pairs else 0)
        sh = max(1 if total % 3 == 1 else 0, sh - wild)
        return int(sh)

    @classmethod
    def is_tenpai(cls, counts: List[int], fixed_melds: int, rules: Dict) -> bool:
        """13 张（3k+1）听牌判定：逐张非赖子试成和。"""
        c, wild = cls.split_wild(list(counts), rules)
        if (sum(c) + wild + fixed_melds * 3) % 3 != 1:
            return False
        laizi = rules.get("laizi")
        for t in range(34):
            if t == laizi:
                continue
            counts[t] += 1
            win = cls.can_win(counts, fixed_melds, rules)
            counts[t] -= 1
            if win:
                return True
        return False

    @classmethod
    def find_waits(cls, counts: List[int], fixed_melds: int, rules: Dict,
                   available: List[int], pool_remaining: Optional[List[int]] = None) -> Dict[int, int]:
        laizi = rules.get("laizi")
        waits: Dict[int, int] = {}
        for t in available:
            if t == laizi:
                continue  # 鬼牌不作为"等的牌"上报
            if t >= len(counts):
                continue
            counts[t] += 1
            if cls.can_win(counts, fixed_melds, rules):
                if pool_remaining is not None and 0 <= t < len(pool_remaining):
                    rem = pool_remaining[t]
                else:
                    rem = max(0, 4 - counts[t])
                if rem > 0:
                    waits[t] = rem
            counts[t] -= 1
        return waits

    # ---------- 算番 ----------

    @classmethod
    def calc_fan(cls, counts: List[int], fixed_melds: int, rules: Dict) -> Tuple[int, List[str]]:
        c, wild = cls.split_wild(list(counts), rules)
        names: List[str] = []
        if rules.get("kokushi", False) and cls.is_kokushi(c, wild, fixed_melds):
            return 16, ["国士无双"]
        if rules.get("seven_pairs", False) and cls.is_seven_pairs(c, wild, fixed_melds):
            names.append("七对")
            fan = 4
        else:
            fan = 1
        suits, has_honor = _suits_present(c)
        if len(suits) == 1 and not has_honor:
            names.append("清一色")
            fan += 4
        elif len(suits) == 1 and has_honor:
            names.append("混一色")
            fan += 2
        needed = 4 - fixed_melds
        all_pungs = cls._standard_win(list(c), wild, needed, seq_ok=False)
        if all_pungs and not rules.get("need_all_pungs", False):
            names.append("碰碰胡")
            fan += 2
        if wild > 0 and rules.get("fan_wild_per_use", False):
            names.append(f"百搭x{wild}")
            fan += wild
        return fan, names

    # ---------- 出牌分析（与 SichuanAnalyzer.analyze_discards 同构） ----------

    @classmethod
    def analyze_discards(cls, counts: List[int], rules: Dict, available: List[int],
                         pool_remaining: Optional[List[int]] = None,
                         fixed_melds: int = 0) -> List[Dict]:
        """14(3k+2) 张出牌态逐张推演：向听/进张/听口/最大番 → EV 排序。
        赖子（鬼牌）永不做弃牌候选。"""
        laizi = rules.get("laizi")
        if sum(counts) % 3 != 2:
            return []
        results: List[Dict] = []
        for d in range(34):
            if counts[d] <= 0 or d == laizi:
                continue
            counts[d] -= 1
            sh = cls.calculate_shanten(counts, fixed_melds, rules)
            waits: Dict[int, int] = {}
            ukeire = 0
            if sh <= 0:
                waits = cls.find_waits(counts, fixed_melds, rules, available, pool_remaining)
                ukeire = sum(waits.values())
            else:
                for t in available:
                    if t == laizi or t >= len(counts):
                        continue
                    rem = pool_remaining[t] if pool_remaining is not None and t < len(pool_remaining) else max(0, 4 - counts[t])
                    if rem <= 0:
                        continue
                    counts[t] += 1
                    sh2 = cls.calculate_shanten(counts, fixed_melds, rules)
                    counts[t] -= 1
                    if sh2 < sh:
                        ukeire += rem
            ting_details: List[Dict] = []
            max_fan = 0
            if sh <= 0 and waits:
                for widx in sorted(waits):
                    counts[widx] += 1
                    fan, fnames = cls.calc_fan(counts, fixed_melds, rules)
                    counts[widx] -= 1
                    max_fan = max(max_fan, fan)
                    ting_details.append({
                        "tile": index_to_mpsz(widx),
                        "name": index_to_chinese(widx),
                        "remaining": waits[widx],
                        "is_dead": waits[widx] == 0,
                        "fan": fan,
                        "fan_names": fnames,
                    })
            results.append({
                "tile": index_to_mpsz(d),
                "ukeire": int(ukeire),
                "shanten": max(0, sh),
                "ev": (10000 if sh <= 0 else 0) + float(ukeire) * (1.5 ** max(0, max_fan - 1)),
                "reason": cls._make_reason(sh, ting_details, ukeire, rules),
                "ting_tiles": [td["tile"] for td in ting_details],
                "ting_details": ting_details,
                "max_fan": max_fan,
                "is_dingque": False,
            })
            counts[d] += 1
        results.sort(key=lambda r: (-r["ev"], -r["ukeire"]))
        return results

    @staticmethod
    def _make_reason(sh: int, ting_details: List[Dict], ukeire: int, rules: Dict) -> str:
        if sh <= 0 and ting_details:
            names = "/".join(td["name"] for td in ting_details[:3])
            fan = max((td["fan"] for td in ting_details), default=1)
            base = f"听 {names} · 余 {ukeire} 张 · 最高 {fan} 番"
            if rules.get("need_open"):
                base += "（开口翻：须吃/碰开口后方可胡）"
            return base
        if sh <= 0:
            return "听牌（叫口已绝，等碰/自摸换口）"
        return f"{sh}向听 · 进张{ukeire}张"
