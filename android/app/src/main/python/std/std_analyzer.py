# -*- coding: utf-8 -*-
"""通用地方玩法规则引擎（modes 中 analyzer="std" 的玩法：
std_tdh / wh_kk / db_qh / hz_bd / gd_hz / cs_zz）。

与 SichuanAnalyzer（28 型、定缺）互补：本分析器处理带字牌的 34 型牌集，
行为完全由 modes.MODES 字段数据驱动：

- laizi            赖子/百搭/鬼牌：胡牌判定枚举其充当任意牌（含作将）；
                   赖子永不做弃牌候选（鬼牌不许打出）。
                   可写单个 34 型索引，也可写列表（两精/中发白全鬼类多赖子玩法）。
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

# 同分牌理裁决：与川麻共用同一份决胜链，保证六种 std 玩法与川麻行为一致。
# 必须放在顶层 import：排序点无降级分支，延迟 import 一旦失败就会在建议链里静默炸。
from discards_tiebreak import order_key as _tie_order_key

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
    # 分支2：顺子。first 可以落在顺子的最小/中间/最大任一张上，另两张缺则用赖子补。
    # 早期实现只把 first 当**最小张**（仅枚举 first+1/first+2），会漏判“赖子当
    # 顺子最小张”的结构：手上 8m9m + 一张赖子胡 7m8m9m，此时 first=8m 既凑不出
    # 8m9m10m，又因 8m%9>6 连顺子分支都进不去 → 整手被判不能胡（对拍实测漏判）。
    if seq_ok and first < 27:
        for off in (0, 1, 2):
            s = first - off                      # 顺子起始 index
            if s < 0 or s % 9 > 6:
                continue                         # 越界或跨花色
            p_a, p_b = [p for p in (s, s + 1, s + 2) if p != first]
            for a in (0, 1):
                for b in (0, 1):
                    if a > lst[p_a] or b > lst[p_b]:
                        continue
                    w = 2 - a - b
                    if w > wild:
                        continue
                    lst[first] -= 1
                    lst[p_a] -= a
                    lst[p_b] -= b
                    if _complete_melds(tuple(lst), melds - 1, seq_ok, wild - w):
                        return True
                    lst[first] += 1
                    lst[p_a] += a
                    lst[p_b] += b
    return False  # first 无处可去 → 该分配失败


def _has_terminal_honor(counts: List[int]) -> bool:
    return any(counts[i] > 0 for i in TERMINAL_HONOR_IDX)


def _suits_present(counts: List[int]) -> Tuple[List[int], bool]:
    num = [i for i in range(27) if counts[i] > 0]
    return sorted({i // 9 for i in num}), any(counts[i] > 0 for i in range(27, 34))


def laizi_set(rules: Dict) -> frozenset:
    """把 modes 里的 laizi 字段归一为集合：支持 None / 单个索引 / 索引列表。

    历史玩法都写单个索引；两精（江西）、中发白全鬼类玩法需要多个，故此处
    统一成一个入口，避免各个调用点各自 if-else（漏一处就会出现“赖子被当弃牌”）。
    """
    lz = rules.get("laizi")
    if lz is None:
        return frozenset()
    items = lz if isinstance(lz, (list, tuple, set, frozenset)) else (lz,)
    return frozenset(int(i) for i in items if isinstance(i, int) and 0 <= int(i) < 34)


class StdAnalyzer:
    """数据驱动的地方玩法分析核心；rules 传 modes.get_mode(key) 字典。"""

    # ---------- 拆分与剥离 ----------

    @staticmethod
    def split_wild(counts: List[int], rules: Dict) -> Tuple[List[int], int]:
        """把所有赖子从计数里剥离（每张赖子都可充当任意牌）。"""
        lz = laizi_set(rules)
        c = list(counts)
        wild = 0
        for i in lz:
            wild += c[i]
            c[i] = 0
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
        lz = laizi_set(rules)
        for t in range(34):
            if t in lz:
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
        lz = laizi_set(rules)
        waits: Dict[int, int] = {}
        for t in available:
            if t in lz:
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
                         fixed_melds: int = 0,
                         ledger: Optional[Dict] = None) -> List[Dict]:
        """14(3k+2) 张出牌态逐张推演：向听/进张/听口/最大番 → EV 排序。
        赖子（鬼牌）永不做弃牌候选。

        ledger（tile_ledger.build_ledger 结果）传入后，听牌文案会把“余 N 张”拆成
        「牌墙可自摸 / 对手可能打出 / 牌墙还能撑几轮」的可追溯事实；不传保持旧口径。
        std 口径本身就是 34 型，听口索引直接可用，不需川麻那套 27↔33 换位。
        """
        lz = laizi_set(rules)
        if sum(counts) % 3 != 2:
            return []
        results: List[Dict] = []
        for d in range(34):
            if counts[d] <= 0 or d in lz:
                continue
            counts[d] -= 1
            sh = cls.calculate_shanten(counts, fixed_melds, rules)
            waits: Dict[int, int] = {}
            improving: List[int] = []
            ukeire = 0
            if sh <= 0:
                waits = cls.find_waits(counts, fixed_melds, rules, available, pool_remaining)
                ukeire = sum(waits.values())
            else:
                for t in available:
                    if t in lz or t >= len(counts):
                        continue
                    rem = pool_remaining[t] if pool_remaining is not None and t < len(pool_remaining) else max(0, 4 - counts[t])
                    if rem <= 0:
                        continue
                    counts[t] += 1
                    sh2 = cls.calculate_shanten(counts, fixed_melds, rules)
                    counts[t] -= 1
                    if sh2 < sh:
                        ukeire += rem
                        improving.append(t)
            ting_details: List[Dict] = []
            max_fan = 0
            chance = None
            ukeire_chance = None
            if sh <= 0 and waits:
                if ledger is not None:
                    from tile_ledger import ting_chance as _ting_chance
                    chance = _ting_chance(ledger, sorted(waits))
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
            elif sh > 0 and improving and ledger is not None:
                # 未听牌的「进张 N 张」同样得拆：N 是未现牌计数（牌墙 + 对手手上），
                # 直接当机会数展示会高估。账本能给的是同一个口径的下界。
                # 存进**单独**的 ukeire_chance 字段，不占用 ting_chance：决胜链
                # （discards_tiebreak）只读 ting_chance 作为第 4 层，写进同一个键会
                # 连带改掉 B-P2 已锁定的排序行为。
                from tile_ledger import ting_chance as _ting_chance
                ukeire_chance = _ting_chance(ledger, improving, verb="进")
            results.append({
                "tile": index_to_mpsz(d),
                "ukeire": int(ukeire),
                "shanten": max(0, sh),
                "ev": (10000 if sh <= 0 else 0) + float(ukeire) * (1.5 ** max(0, max_fan - 1)),
                "reason": cls._make_reason(sh, ting_details, ukeire, rules,
                                           chance=chance, ukeire_chance=ukeire_chance),
                "ting_tiles": [td["tile"] for td in ting_details],
                "ting_details": ting_details,
                "ting_chance": chance,
                "ukeire_chance": ukeire_chance,
                "max_fan": max_fan,
                "is_dingque": False,
            })
            counts[d] += 1
        # 旧口径 (-ev, -ukeire) 在两项都相等时依旧靠枚举序分先后（万永远先于条）。
        # 现走与川麻同一份决胜链：进张→叫口宽→牌墙可摸→安全→弹性→索引兜底。
        results.sort(key=_tie_order_key)
        return results

    @staticmethod
    def _make_reason(sh: int, ting_details: List[Dict], ukeire: int, rules: Dict,
                     chance: Optional[Dict] = None,
                     ukeire_chance: Optional[Dict] = None) -> str:
        open_hint = "（开口翻：须吃/碰开口后方可胡）" if rules.get("need_open") else ""
        if sh <= 0 and ting_details:
            fan = max((td["fan"] for td in ting_details), default=1)
            # 账本可用时走它的可追溯口径（拆得开“牌墙还有 / 只剩在人家手上”），
            # 否则退回旧口径；两条路径都带上番数与开口提示，升级不许丢信息。
            if chance and chance.get("text"):
                return f"{chance['text']} · 最高 {fan} 番{open_hint}"
            names = "/".join(td["name"] for td in ting_details[:3])
            # 无账本时不拆牌墙/对手，但必须标「未现至多」：那个数是上界，
            # 含了对手按住的牌（与 sichuan_analyzer 的同位分支同一口径）。
            return f"听 {names} · 未现至多 {ukeire} 张 · 最高 {fan} 番{open_hint}"
        if sh <= 0:
            # 叫口四张全见光时“等碰/自摸”是错的（那张牌已经不存在了），只能换口。
            return f"叫口已绝（四张全部见光），需换口{open_hint}"
        # 未听牌：有账本就把进张拆成可追溯事实（共余 N 张 / 只在牌墙 / 还能撑几轮）；
        # 没账本时必须标「至多」——那个数是未现牌上界，含了对手按住的牌。
        if ukeire_chance and ukeire_chance.get("text"):
            return f"{sh}向听 · {ukeire_chance['text']}"
        return f"{sh}向听 · 进张至多 {ukeire} 张（未现数，含对手手上）"
