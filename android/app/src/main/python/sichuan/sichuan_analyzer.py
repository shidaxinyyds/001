"""
四川麻将（血战到底 / 血流成河）规则引擎与智能选叫分析器。
108 张牌物理守恒记牌器与强制定缺门控系统。

编码约定（标准 mpsz，与 001-main 全局一致）：
- 0-8  : 1m..9m (万子，共 9 种，36 张)
- 9-17 : 1p..9p (筒子，共 9 种，36 张)
- 18-26: 1s..9s (条子，共 9 种，36 张)
合计 27 种牌型，每种 4 张，共 108 张。无字牌（东/南/西/北/中/发/白）。
"""
from __future__ import annotations
import itertools
from typing import Dict, List, Optional, Set, Tuple
from functools import lru_cache


# 花色索引常量
SUIT_M = 0  # 万 (0-8)
SUIT_P = 1  # 筒 (9-17)
SUIT_S = 2  # 条 (18-26)

SUIT_NAMES = {SUIT_M: "万", SUIT_P: "筒", SUIT_S: "条"}
SUIT_CHARS = {SUIT_M: "m", SUIT_P: "p", SUIT_S: "s"}


INDEX_HONGZHONG = 27


def tile_to_suit(idx: int) -> int:
    """返回牌所在的套系：0=万(m), 1=筒(p), 2=条(s), -1=红中(z)。"""
    if 0 <= idx <= 8:
        return SUIT_M
    elif 9 <= idx <= 17:
        return SUIT_P
    elif 18 <= idx <= 26:
        return SUIT_S
    elif idx == 27:
        return -1
    raise ValueError(f"四川麻将非法牌索引: {idx}（超出 0-27 范围）")


def mpsz_to_index27(tile_str: str) -> Optional[int]:
    """将 '1m', '5p', '9s', '7z' 转换为 0-27 索引。"""
    if not tile_str or len(tile_str) < 2:
        return None
    if tile_str == "7z":
        return 27
    num_ch, suit_ch = tile_str[0], tile_str[1]
    if not num_ch.isdigit():
        return None
    num = int(num_ch)
    if not (1 <= num <= 9):
        return None
    if suit_ch == 'm':
        return num - 1
    elif suit_ch == 'p':
        return 9 + num - 1
    elif suit_ch == 's':
        return 18 + num - 1
    return None


def index27_to_mpsz(idx: int) -> str:
    """将 0-27 索引转换为 mpsz 字符串。"""
    if 0 <= idx <= 8:
        return f"{idx + 1}m"
    elif 9 <= idx <= 17:
        return f"{idx - 9 + 1}p"
    elif 18 <= idx <= 26:
        return f"{idx - 18 + 1}s"
    elif idx == 27:
        return "7z"
    raise ValueError(f"非法牌索引: {idx}")


def index27_to_chinese(idx: int) -> str:
    """将 0-27 索引转换为中文名称，如 '5万', '8筒', '2条', '中'。"""
    if 0 <= idx <= 8:
        return f"{idx + 1}万"
    elif 9 <= idx <= 17:
        return f"{idx - 9 + 1}筒"
    elif 18 <= idx <= 26:
        return f"{idx - 18 + 1}条"
    elif idx == 27:
        return "中"


def pool_remaining_from_visible(
    counts: List[int],
    disc_counts: Optional[List[int]] = None,
    meld_counts: Optional[List[int]] = None,
) -> List[int]:
    """108 张物理守恒：28 型牌池剩余 = 4 − 己手 − 牌河 − 副露可见量。

    disc_counts / meld_counts 采用 34 型索引（红中 7z 位于索引 33），
    与引擎/Trainer 的可见牌账本口径一致。trainer 与 engine 兜底路径共用。
    """
    pool = [0] * 28
    for i in range(28):
        vis = counts[i] if i < len(counts) else 0
        if i < 27:
            if disc_counts is not None and i < len(disc_counts):
                vis += disc_counts[i]
            if meld_counts is not None and i < len(meld_counts):
                vis += meld_counts[i]
        else:
            # 7z (红中) 在 34 索引系统下是 33
            if disc_counts is not None and 33 < len(disc_counts):
                vis += disc_counts[33]
            if meld_counts is not None and 33 < len(meld_counts):
                vis += meld_counts[33]
        pool[i] = max(0, 4 - vis)
    return pool

@lru_cache(maxsize=16384)
def _min_wild_melds(c: Tuple[int, ...]) -> int:
    """计算将单门牌 c (9元组) 全部组合为顺子/刻子所需的最少红中赖子数。"""
    idx = -1
    for i in range(9):
        if c[i] > 0:
            idx = i
            break
    if idx == -1:
        return 0

    # 1. 纯赖子补齐当前牌
    ans = 2 + _min_wild_melds(c[:idx] + (c[idx] - 1,) + c[idx + 1:])

    # 2. 刻子
    if c[idx] >= 3:
        ans = min(ans, _min_wild_melds(c[:idx] + (c[idx] - 3,) + c[idx + 1:]))
    if c[idx] >= 2:
        ans = min(ans, 1 + _min_wild_melds(c[:idx] + (c[idx] - 2,) + c[idx + 1:]))

    # 3. 顺子
    if idx <= 6 and c[idx + 1] >= 1 and c[idx + 2] >= 1:
        cl = list(c)
        cl[idx] -= 1
        cl[idx + 1] -= 1
        cl[idx + 2] -= 1
        ans = min(ans, _min_wild_melds(tuple(cl)))
    if idx <= 7 and c[idx + 1] >= 1:
        cl = list(c)
        cl[idx] -= 1
        cl[idx + 1] -= 1
        ans = min(ans, 1 + _min_wild_melds(tuple(cl)))
    if idx <= 6 and c[idx + 2] >= 1:
        cl = list(c)
        cl[idx] -= 1
        cl[idx + 2] -= 1
        ans = min(ans, 1 + _min_wild_melds(tuple(cl)))

    return ans


@lru_cache(maxsize=16384)
def _min_wild_pair_melds(c: Tuple[int, ...]) -> int:
    """计算将单门牌 c (9元组) 组合为一个雀头(对子) + 任意面子所需的最少红中赖子数。"""
    # 雀头全由赖子充当 (需2张赖子)
    ans = 2 + _min_wild_melds(c)
    for p in range(9):
        if c[p] >= 2:
            cl = list(c)
            cl[p] -= 2
            ans = min(ans, _min_wild_melds(tuple(cl)))
        if c[p] >= 1:
            cl = list(c)
            cl[p] -= 1
            ans = min(ans, 1 + _min_wild_melds(tuple(cl)))
    return ans


@lru_cache(maxsize=32768)
def _is_tenpai_cached(
    counts_key: Tuple[int, ...], num_fixed_melds: int, dingque_key: int
) -> bool:
    """听牌判定记忆层：13 张预摸牌分析中大量 (摸牌场景 × 候选打牌) 组合会
    产生相同的 13 张中间态，缓存后重复子问题只算一次。"""
    return SichuanAnalyzer._is_tenpai_impl(
        list(counts_key), num_fixed_melds,
        None if dingque_key < 0 else dingque_key)


class SichuanAnalyzer:
    """四川麻将（血战到底 / 红中血流）分析核心。"""

    @staticmethod
    def parse_hand_mpsz(mpsz: str) -> List[int]:
        """解析手牌字符串（例如 '123m456p789s11m7z'）为 0-27 索引列表（27 为红中赖子）。"""
        res = []
        cur_nums = []
        for ch in mpsz:
            if ch.isdigit():
                cur_nums.append(int(ch))
            elif ch in "mps":
                for n in cur_nums:
                    if 1 <= n <= 9:
                        if ch == 'm':
                            res.append(n - 1)
                        elif ch == 'p':
                            res.append(9 + n - 1)
                        elif ch == 's':
                            res.append(18 + n - 1)
                cur_nums = []
            elif ch == 'z':
                for n in cur_nums:
                    if n == 7:
                        res.append(27)
                cur_nums = []
        return sorted(res)

    @staticmethod
    def counts_from_tiles(tiles: List[int]) -> List[int]:
        """将手牌列表转换为 28 长度的计数数组（0-26 为数牌，27 为红中赖子）。"""
        c = [0] * 28
        for t in tiles:
            if 0 <= t < 28:
                c[t] += 1
        return c

    @staticmethod
    def get_suits_in_hand(counts: List[int]) -> Set[int]:
        """返回当前手牌包含的花色集合 {SUIT_M, SUIT_P, SUIT_S}。"""
        suits = set()
        for i in range(27):
            if counts[i] > 0:
                suits.add(tile_to_suit(i))
        return suits

    @staticmethod
    def can_form_sequence(counts: List[int], tile: int) -> bool:
        """检查以 tile 为起点的顺子是否可能（同一花色连续 3 张）。"""
        suit = tile_to_suit(tile)
        if suit == SUIT_M and tile > 6:
            return False
        if suit == SUIT_P and tile > 15:
            return False
        if suit == SUIT_S and tile > 24:
            return False
        return counts[tile] >= 1 and counts[tile + 1] >= 1 and counts[tile + 2] >= 1

    @classmethod
    def _can_form_melds(cls, counts: List[int], num_melds: int) -> bool:
        """递归判断 counts 中能否拆分成指定数量的顺子或刻子。"""
        if num_melds == 0:
            return all(c == 0 for c in counts)

        first = -1
        for i in range(27):
            if counts[i] > 0:
                first = i
                break
        if first == -1:
            return False

        # 尝试刻子
        if counts[first] >= 3:
            counts[first] -= 3
            if cls._can_form_melds(counts, num_melds - 1):
                counts[first] += 3
                return True
            counts[first] += 3

        # 尝试顺子
        if cls.can_form_sequence(counts, first):
            counts[first] -= 1
            counts[first + 1] -= 1
            counts[first + 2] -= 1
            if cls._can_form_melds(counts, num_melds - 1):
                counts[first] += 1
                counts[first + 1] += 1
                counts[first + 2] += 1
                return True
            counts[first] += 1
            counts[first + 1] += 1
            counts[first + 2] += 1

        return False

    @classmethod
    def is_seven_pairs(cls, counts: List[int], num_fixed_melds: int = 0) -> bool:
        """七对判定（血战暗七对）：必须门清（0副露），恰好 7 个对子（含龙七对）。"""
        if num_fixed_melds > 0:
            return False
        if sum(counts) != 14:
            return False
        pairs = 0
        for c in counts:
            if c == 2:
                pairs += 1
            elif c == 4:
                pairs += 2  # 根 / 龙七对
            elif c != 0:
                return False
        return pairs == 7

    @classmethod
    def _can_win_no_wild(cls, counts: List[int], num_fixed_melds: int = 0) -> bool:
        """无赖子纯手牌胡牌判定。"""
        suits = cls.get_suits_in_hand(counts)
        if len(suits) > 2:
            return False

        # 七对判定
        if cls.is_seven_pairs(counts, num_fixed_melds):
            return True

        num_needed = 4 - num_fixed_melds
        hand_tiles_count = sum(counts[:27])
        expected_count = 2 + num_needed * 3
        if hand_tiles_count != expected_count:
            return False

        # 枚举将对（对子）
        for pair_tile in range(27):
            if counts[pair_tile] >= 2:
                counts[pair_tile] -= 2
                if cls._can_form_melds(counts, num_needed):
                    counts[pair_tile] += 2
                    return True
        return False

    @classmethod
    def can_win(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        dingque_suit: Optional[int] = None,
    ) -> bool:
        """川麻胡牌核心判定（分门 DP 极速算法，支持红中赖子万能百搭，必须缺一门）。

        dingque_suit：给定时，手牌仍含定缺花色牌一律判不胜（花猪不算胡）。
        """
        if dingque_suit is not None:
            ds = dingque_suit * 9
            if any(counts[ds + i] > 0 for i in range(9)):
                return False
        suits = cls.get_suits_in_hand(counts)
        if len(suits) > 2:
            return False

        num_wild = counts[27] if len(counts) > 27 else 0
        total_len = sum(counts[:27]) + num_wild + num_fixed_melds * 3
        if total_len != 14:
            return False

        # 七对判定
        if num_fixed_melds == 0:
            num_pairs = sum(counts[i] // 2 for i in range(27))
            num_singles = sum(counts[i] % 2 for i in range(27))
            if num_wild >= num_singles and (num_pairs + num_singles + (num_wild - num_singles) // 2) >= 7:
                return True

        m_c = tuple(counts[0:9])
        p_c = tuple(counts[9:18])
        s_c = tuple(counts[18:27])

        m_m = _min_wild_melds(m_c)
        p_m = _min_wild_melds(p_c)
        s_m = _min_wild_melds(s_c)

        # 赖子全做雀头
        if m_m + p_m + s_m + 2 <= num_wild:
            return True

        # 雀头在万 / 筒 / 条
        if _min_wild_pair_melds(m_c) + p_m + s_m <= num_wild:
            return True
        if m_m + _min_wild_pair_melds(p_c) + s_m <= num_wild:
            return True
        if m_m + p_m + _min_wild_pair_melds(s_c) <= num_wild:
            return True

        return False

    @staticmethod
    def get_meld_protected_tiles(counts_27: List[int]) -> Set[int]:
        """返回必须保护的已成顺子/刻子牌集合（弃打会破坏已成型牌面）。"""
        protected = set()
        for s in range(3):
            sub = counts_27[s * 9 : (s + 1) * 9]
            # 刻子
            for num in range(9):
                if sub[num] >= 3:
                    protected.add(s * 9 + num)
            # 顺子完整性保护
            for start in range(7):
                if sub[start] >= 1 and sub[start + 1] >= 1 and sub[start + 2] >= 1:
                    protected.add(s * 9 + start)
                    protected.add(s * 9 + start + 1)
                    protected.add(s * 9 + start + 2)
        return protected

    @classmethod
    def is_tenpai(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        dingque_suit: Optional[int] = None,
    ) -> bool:
        """极速听牌判断（带记忆层）：只要存在任意一张能胡的牌，立即返回 True。

        dingque_suit：给定时，试胡牌与手牌均受定缺约束（含定缺牌的手牌不可能听牌）。
        """
        return _is_tenpai_cached(
            tuple(counts), num_fixed_melds,
            -1 if dingque_suit is None else dingque_suit)

    @classmethod
    def _is_tenpai_impl(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        dingque_suit: Optional[int] = None,
    ) -> bool:
        suits = cls.get_suits_in_hand(counts)
        if len(suits) > 2:
            return False
        num_wild = counts[27] if len(counts) > 27 else 0
        total_len = sum(counts[:27]) + num_wild + num_fixed_melds * 3
        if total_len % 3 != 1:
            return False
        for test_tile in range(27):
            if dingque_suit is not None and tile_to_suit(test_tile) == dingque_suit:
                continue
            test_suit = tile_to_suit(test_tile)
            if len(suits | {test_suit}) > 2:
                continue
            counts[test_tile] += 1
            win = cls.can_win(counts, num_fixed_melds, dingque_suit)
            counts[test_tile] -= 1
            if win:
                return True
        return False

    @classmethod
    def find_waiting_tiles(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        pool_remaining: Optional[List[int]] = None,
        dingque_suit: Optional[int] = None,
    ) -> Dict[int, int]:
        """找出当前手牌能够胡的牌（叫口）及其真实剩余存活数。

        dingque_suit：给定时，定缺花色的牌既不作试胡牌，手牌含定缺牌也无叫口。
        """
        waiting = {}
        suits = cls.get_suits_in_hand(counts)
        if len(suits) > 2:
            return waiting

        num_wild = counts[27] if len(counts) > 27 else 0
        total_len = sum(counts[:27]) + num_wild + num_fixed_melds * 3
        if total_len % 3 != 1:
            return waiting

        for test_tile in range(27):
            if dingque_suit is not None and tile_to_suit(test_tile) == dingque_suit:
                continue
            test_suit = tile_to_suit(test_tile)
            if len(suits | {test_suit}) > 2:
                continue

            counts[test_tile] += 1
            if cls.can_win(counts, num_fixed_melds, dingque_suit):
                if pool_remaining is not None and 0 <= test_tile < len(pool_remaining):
                    rem = pool_remaining[test_tile]
                else:
                    rem = max(0, 4 - counts[test_tile])
                waiting[test_tile] = rem
            counts[test_tile] -= 1

        return waiting

    @classmethod
    def calculate_shanten(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        dingque_suit: Optional[int] = None,
    ) -> int:
        """计算四川麻将向听数（0=听牌/胡牌, 1=1向听, 2=2向听...）。支持红中赖子与定缺约束。"""
        num_wild = counts[27] if len(counts) > 27 else 0
        total_tiles = sum(counts[:27]) + num_wild

        # 定缺硬约束：仍持有定缺花色牌时，向听 = 需打光该门的张数（断门 Prior）
        if dingque_suit is not None:
            ds = dingque_suit * 9
            dq_left = sum(counts[ds + i] for i in range(9))
            if dq_left > 0:
                return max(1, dq_left)

        suits = cls.get_suits_in_hand(counts)
        extra_dingque_penalty = 0
        if len(suits) > 2:
            suit_counts = {
                SUIT_M: sum(counts[0:9]),
                SUIT_P: sum(counts[9:18]),
                SUIT_S: sum(counts[18:27]),
            }
            min_suit = min(suit_counts, key=suit_counts.get)
            extra_dingque_penalty = suit_counts[min_suit]

        if total_tiles % 3 == 2:
            if cls.can_win(counts, num_fixed_melds, dingque_suit):
                return 0
        elif total_tiles % 3 == 1:
            if cls.is_tenpai(counts, num_fixed_melds, dingque_suit):
                return 0
        else:
            # 3n 张型（如 13 张打掉一张后的 12 张中间态）：以「补一张即听」为 0 向听
            # 的正确定义计算，不再落入常量 2 兜底。
            if cls._predraw_tenpai(counts, num_fixed_melds, dingque_suit):
                return 0

        if extra_dingque_penalty > 0:
            return max(1, extra_dingque_penalty)

        # 1 向听极速检测
        if total_tiles % 3 == 2:
            for d in range(27):
                if counts[d] > 0:
                    counts[d] -= 1
                    ten = cls.is_tenpai(counts, num_fixed_melds, dingque_suit)
                    counts[d] += 1
                    if ten:
                        return 1
        elif total_tiles % 3 == 1:
            for t in range(27):
                if dingque_suit is not None and tile_to_suit(t) == dingque_suit:
                    continue
                if len(suits | {tile_to_suit(t)}) > 2:
                    continue
                counts[t] += 1
                for d in range(27):
                    if counts[d] > 0:
                        counts[d] -= 1
                        ten = cls.is_tenpai(counts, num_fixed_melds, dingque_suit)
                        counts[d] += 1
                        if ten:
                            counts[t] -= 1
                            return 1
                counts[t] -= 1
        else:
            # 3n 的 1 向听：一个「摸+打」循环后达到「补一张即听」
            for t in range(27):
                if dingque_suit is not None and tile_to_suit(t) == dingque_suit:
                    continue
                counts[t] += 1
                reached = False
                for d in range(27):
                    if counts[d] > 0:
                        counts[d] -= 1
                        if cls._predraw_tenpai(counts, num_fixed_melds, dingque_suit):
                            reached = True
                        counts[d] += 1
                        if reached:
                            break
                counts[t] -= 1
                if reached:
                    return 1

        base_shanten = 2
        if num_wild > 0:
            base_shanten = max(1, base_shanten - num_wild)
        return base_shanten

    @classmethod
    def _predraw_tenpai(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        dingque_suit: Optional[int] = None,
    ) -> bool:
        """3n 张型专用：是否存在一张摸入牌使手牌（+1 后 3n+1）直接听牌。"""
        for t in range(27):
            if dingque_suit is not None and tile_to_suit(t) == dingque_suit:
                continue
            counts[t] += 1
            ten = cls.is_tenpai(counts, num_fixed_melds, dingque_suit)
            counts[t] -= 1
            if ten:
                return True
        return False

    @classmethod
    def calculate_fan(
        cls,
        counts: List[int],
        win_tile: int,
        num_fixed_melds: int = 0,
        dingque_suit: Optional[int] = None,
    ) -> int:
        """估算川麻胡牌番数（清一色、七对、根数）。含定缺牌时为非法胡，返 0。"""
        if dingque_suit is not None:
            if tile_to_suit(win_tile) == dingque_suit:
                return 0
            ds = dingque_suit * 9
            if any(counts[ds + i] > 0 for i in range(9)):
                return 0
        fan = 1  # 基础 1 番（平胡）
        c = list(counts)
        c[win_tile] += 1

        # 清一色判定（只有1种花色）
        suits = cls.get_suits_in_hand(c)
        if len(suits) == 1:
            fan += 4  # 清一色加 4 番（16倍）

        # 七对判定
        if cls.is_seven_pairs(c, num_fixed_melds):
            fan += 2  # 暗七对加 2 番

        # 根判定（4张相同的牌，不论暗杠或手牌）
        gen_count = sum(1 for x in c if x >= 4)
        fan += gen_count

        return fan

    @classmethod
    def find_1shanten_ukeire(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        pool_remaining: Optional[List[int]] = None,
        dingque_suit: Optional[int] = None,
    ) -> Tuple[Dict[int, int], int]:
        """计算 1 向听时的有效进张牌型及其真实存活总张数。"""
        valid_incoming = {}
        suits = cls.get_suits_in_hand(counts)
        for test_tile in range(27):
            if dingque_suit is not None and tile_to_suit(test_tile) == dingque_suit:
                continue
            test_suit = tile_to_suit(test_tile)
            if len(suits | {test_suit}) > 2:
                continue
            if pool_remaining is not None and pool_remaining[test_tile] <= 0:
                continue

            counts[test_tile] += 1
            can_reach_ting = False
            for d in range(27):
                if counts[d] > 0:
                    counts[d] -= 1
                    ten = cls.is_tenpai(counts, num_fixed_melds)
                    counts[d] += 1
                    if ten:
                        can_reach_ting = True
                        break
            counts[test_tile] -= 1

            if can_reach_ting:
                rem = pool_remaining[test_tile] if pool_remaining is not None else max(0, 4 - counts[test_tile])
                valid_incoming[test_tile] = rem

        total_ukeire = sum(valid_incoming.values())
        return valid_incoming, total_ukeire

    @classmethod
    def analyze_discards(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        pool_remaining: Optional[List[int]] = None,
        dingque_suit: Optional[int] = None,
        opponent_dingque_suits: Optional[List[int]] = None,
    ) -> List[Dict]:
        """核心选叫与出牌分析器入口（EV 期望价值排序模型，含防点炮风险惩罚）。

        引擎常态喂 13 张（3n+1，刚打完牌等摸牌）：此时直接打一张会剩 12 张（3n），
        听牌/叫口搜索全部短路、进张恒 0。对 3n+1 输入自动改走「预摸牌期望」路径，
        14 张（3n+2）及其他输入沿用完整 EV 流程。
        """
        num_wild = counts[27] if len(counts) > 27 else 0
        total_len = sum(counts[:27]) + num_wild + num_fixed_melds * 3
        if total_len % 3 == 1:
            return cls._analyze_discards_predraw(
                counts, num_fixed_melds, pool_remaining, dingque_suit, opponent_dingque_suits)
        return cls._analyze_discards_3n2(
            counts, num_fixed_melds, pool_remaining, dingque_suit, opponent_dingque_suits)

    @classmethod
    def _analyze_discards_3n2(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        pool_remaining: Optional[List[int]] = None,
        dingque_suit: Optional[int] = None,
        opponent_dingque_suits: Optional[List[int]] = None,
    ) -> List[Dict]:
        """14 张（3n+2）完整出牌 EV 分析：打每张后剩 13 张（3n+1），叫口/向听可算。"""
        results = []
        suits = cls.get_suits_in_hand(counts)

        if dingque_suit is None and len(suits) > 2:
            suit_counts = {
                SUIT_M: sum(counts[0:9]),
                SUIT_P: sum(counts[9:18]),
                SUIT_S: sum(counts[18:27]),
            }
            dingque_suit = min(suit_counts, key=suit_counts.get)

        has_dingque_tiles = False
        if dingque_suit is not None:
            suit_start = dingque_suit * 9
            has_dingque_tiles = any(counts[suit_start + i] > 0 for i in range(9))

        # 面子保护：精准识别所有不可或缺的已成顺子与刻子
        protected_tiles = cls.get_meld_protected_tiles(counts[:27])

        candidate_discards = []
        for t in range(27):
            if counts[t] > 0:
                if has_dingque_tiles:
                    if tile_to_suit(t) == dingque_suit:
                        candidate_discards.append(t)
                else:
                    candidate_discards.append(t)

        for discard in candidate_discards:
            original_count = counts[discard]
            counts[discard] -= 1
            waiting_dict = cls.find_waiting_tiles(counts, num_fixed_melds, pool_remaining, dingque_suit)
            real_ukeire = sum(waiting_dict.values()) if waiting_dict else 0
            ting_mpsz_list = [index27_to_mpsz(w) for w in waiting_dict.keys()]
            ting_cn_list = [index27_to_chinese(w) for w in waiting_dict.keys()]

            is_dingque_discard = (dingque_suit is not None and tile_to_suit(discard) == dingque_suit)
            ev_score = 0.0

            if waiting_dict:
                shanten = 0
                fan_sum = 0
                for w_tile, rem in waiting_dict.items():
                    w_fan = cls.calculate_fan(counts, w_tile, num_fixed_melds, dingque_suit)
                    fan_sum += rem * (2 ** w_fan)
                ev_score = 50000.0 + fan_sum * 10.0 + real_ukeire * 5.0

                is_qing = len(cls.get_suits_in_hand(counts)) == 1
                suffix = " (清一色)" if is_qing else ""
                if real_ukeire > 0:
                    reason = f"听 {'/'.join(ting_cn_list[:3])}，余 {real_ukeire} 张{suffix}"
                else:
                    reason = f"听 {'/'.join(ting_cn_list[:3])}（绝张）{suffix}"
            else:
                shanten_raw = cls.calculate_shanten(counts, num_fixed_melds, dingque_suit)
                shanten = shanten_raw
                if shanten == 1:
                    incoming_dict, incoming_ukeire = cls.find_1shanten_ukeire(
                        counts, num_fixed_melds, pool_remaining, dingque_suit
                    )
                    real_ukeire = incoming_ukeire
                    incoming_cn = [index27_to_chinese(t) for t in incoming_dict.keys()]
                    ev_score = 20000.0 + incoming_ukeire * 10.0

                    # 顺子/刻子面子保护：绝对禁止无故拆散完整面子
                    if discard in protected_tiles and not is_dingque_discard:
                        ev_score -= 50000.0

                    # 清一色诱导因子
                    non_dq_suits = [s for s in range(3) if s != dingque_suit]
                    suit_counts = {s: sum(counts[s*9:(s+1)*9]) for s in non_dq_suits}
                    if suit_counts:
                        max_suit = max(suit_counts, key=suit_counts.get)
                        max_count = suit_counts[max_suit]
                        discard_suit = tile_to_suit(discard)
                        if max_count >= 8:
                            if discard_suit != max_suit:
                                ev_score += (max_count - 7) * 2000.0
                            else:
                                ev_score -= 3000.0

                    # 七对偏好：若对子数 >= 4，打单张牌加分，拆对子扣分
                    pair_count = sum(1 for c in counts if c >= 2)
                    if pair_count >= 4:
                        if original_count == 1:
                            ev_score += (pair_count - 3) * 1200.0
                        elif original_count >= 2:
                            ev_score -= 2500.0

                    # 刻子/根保护：避免轻易拆刻子或4张根
                    if original_count >= 3 and not is_dingque_discard:
                        ev_score -= 4000.0

                    if is_dingque_discard:
                        if incoming_cn:
                            reason = f"定缺打{SUIT_NAMES[dingque_suit]}，进 {'/'.join(incoming_cn[:3])} 共 {incoming_ukeire} 张"
                        else:
                            reason = f"定缺打{SUIT_NAMES[dingque_suit]}"
                    elif discard in protected_tiles:
                        reason = "破坏顺子/刻子"
                    else:
                        if incoming_cn:
                            reason = f"进 {'/'.join(incoming_cn[:3])} 共 {incoming_ukeire} 张"
                        else:
                            reason = "改善牌型"
                else:
                    real_ukeire = 0
                    ev_score = -1000.0 * shanten
                    if is_dingque_discard:
                        reason = f"定缺打{SUIT_NAMES[dingque_suit]}"
                    elif discard in protected_tiles:
                        ev_score -= 50000.0
                        reason = "破坏顺子/刻子"
                    else:
                        # 2向听及以上：结构性冗余张与全孤张优选模型
                        suit = tile_to_suit(discard)
                        has_adj1 = any(0 <= discard + d < 27 and tile_to_suit(discard + d) == suit and counts[discard + d] > 0 for d in (-1, 1))
                        has_adj2 = any(0 <= discard + d < 27 and tile_to_suit(discard + d) == suit and counts[discard + d] > 0 for d in (-2, 2))
                        if original_count >= 2:
                            ev_score += 500.0
                            reason = "多余对子"
                        elif not has_adj1 and not has_adj2:
                            ev_score += 800.0
                            reason = "全孤单张"
                        elif not has_adj1 and has_adj2:
                            ev_score += 300.0
                            reason = "间搭孤张"
                        else:
                            ev_score += 100.0
                            reason = "多余邻张"

            # 防点炮与危险度综合扣分（EV 引擎升级）
            danger_penalty = 0.0
            discard_suit = tile_to_suit(discard)
            discard_num = (discard % 9) + 1
            rem_discard = pool_remaining[discard] if pool_remaining is not None else max(0, 4 - counts[discard])
            seen_discard = 4 - rem_discard - counts[discard]

            if not is_dingque_discard:
                if opponent_dingque_suits and discard_suit in opponent_dingque_suits:
                    # 对手定缺的花色，点炮率为 0，打出具有绝对安全性，给予安全奖励加分
                    ev_score += 1500.0
                elif seen_discard == 0 and discard_num in (4, 5, 6):
                    # 中张纯生张（4/5/6 未见），后期点炮率极高，扣除危险减分
                    danger_penalty = 2500.0
                    ev_score -= danger_penalty
                elif seen_discard == 0:
                    # 一般生张
                    danger_penalty = 1000.0
                    ev_score -= danger_penalty

            ting_details = []
            if waiting_dict:
                for w, rem in waiting_dict.items():
                    ting_details.append({
                        "tile": index27_to_mpsz(w),
                        "name": index27_to_chinese(w),
                        "remaining": rem,
                        "is_dead": (rem == 0),
                        "fan": cls.calculate_fan(counts, w, num_fixed_melds, dingque_suit),
                    })

            results.append({
                "tile": index27_to_mpsz(discard),
                "tile_idx": discard,
                "ukeire": real_ukeire,
                "shanten": shanten,
                "ev": round(ev_score, 1),
                "ting_tiles": ting_mpsz_list,
                "ting_details": ting_details,
                "reason": reason,
                "is_dingque": is_dingque_discard,
                "danger_penalty": danger_penalty,
            })

            counts[discard] += 1

        results.sort(key=lambda item: item["ev"], reverse=True)
        return results

    @classmethod
    def _analyze_discards_predraw(
        cls,
        counts: List[int],
        num_fixed_melds: int = 0,
        pool_remaining: Optional[List[int]] = None,
        dingque_suit: Optional[int] = None,
        opponent_dingque_suits: Optional[List[int]] = None,
    ) -> List[Dict]:
        """13 张（3n+1）预摸牌期望分析。

        对每种牌池中剩余 > 0 的摸入牌 t 组成 14 张手牌，复用完整 14 张 EV 流程
        算各候选打点的进张/向听，再按摸入概率（剩余张数占比）加权聚合成该
        13 张局面下每张牌的期望值。仅在手牌/牌河变化时由上层缓存控制重算。
        """
        draw_opts = []
        for t in range(28):
            # 摸入定缺花色的牌只能立即打掉、回到同一 13 张局面，期望自洽，
            # 不必成场景（剪枝降低算力）；红中赖子（27）保留。
            if dingque_suit is not None and t < 27 and tile_to_suit(t) == dingque_suit:
                continue
            if pool_remaining is not None and t < len(pool_remaining):
                rem = pool_remaining[t]
            else:
                rem = max(0, 4 - (counts[t] if t < len(counts) else 0))
            if rem > 0:
                draw_opts.append((t, rem))
        if not draw_opts:
            return []
        total_w = float(sum(w for _, w in draw_opts))

        agg: Dict[int, Dict] = {}
        for t, w in draw_opts:
            p = w / total_w
            counts[t] += 1
            try:
                scen = cls._analyze_discards_3n2(
                    counts, num_fixed_melds, pool_remaining,
                    dingque_suit, opponent_dingque_suits)
            finally:
                counts[t] -= 1
            for r in scen:
                idx = r["tile_idx"]
                a = agg.get(idx)
                if a is None:
                    a = {
                        "tile": r["tile"], "weight": 0.0, "ev": 0.0,
                        "ukeire": 0.0, "shanten": 0.0, "danger": 0.0,
                        "is_dingque": False,
                        "best_p": -1.0, "best_reason": "",
                        "ting_p": -1.0, "ting_tiles": [], "ting_details": [],
                    }
                    agg[idx] = a
                a["weight"] += p
                a["ev"] += p * r["ev"]
                a["ukeire"] += p * r["ukeire"]
                a["shanten"] += p * r["shanten"]
                a["danger"] += p * r.get("danger_penalty", 0.0)
                a["is_dingque"] = a["is_dingque"] or r["is_dingque"]
                if p > a["best_p"]:
                    a["best_p"] = p
                    a["best_reason"] = r.get("reason", "")
                if r["ting_details"] and p > a["ting_p"]:
                    a["ting_p"] = p
                    a["ting_tiles"] = r["ting_tiles"]
                    a["ting_details"] = r["ting_details"]

        results = []
        for idx, a in agg.items():
            # 仅推荐当前 13 张里真实持有的牌（某摸牌场景下刚摸进的牌不作为候选）
            if counts[idx] <= 0:
                continue
            wsum = a["weight"]
            if wsum <= 0:
                continue
            avg_ev = a["ev"] / wsum
            avg_uke = a["ukeire"] / wsum
            avg_shanten = int(round(a["shanten"] / wsum))
            reason = a["best_reason"] or ""
            if a["ting_details"]:
                cn_names = [d["name"] for d in a["ting_details"][:3]]
                reason = f"摸牌后听 {'/'.join(cn_names)}，期望进张 {avg_uke:.1f} 张"
            elif reason and "期望" not in reason:
                reason = f"{reason}｜摸牌期望进张 {avg_uke:.1f} 张"
            elif not reason:
                reason = f"摸牌期望进张 {avg_uke:.1f} 张"
            results.append({
                "tile": a["tile"],
                "tile_idx": idx,
                "ukeire": int(round(avg_uke)),
                "shanten": avg_shanten,
                "ev": round(avg_ev, 1),
                "ting_tiles": a["ting_tiles"],
                "ting_details": a["ting_details"],
                "reason": reason,
                "is_dingque": a["is_dingque"],
                "danger_penalty": round(a["danger"] / wsum, 1),
            })
        results.sort(key=lambda item: item["ev"], reverse=True)
        return results

    @classmethod
    def check_tenpai_alert(
        cls,
        counts: List[int],
        tiles_remaining_in_wall: int = 108,
        dingque_suit: Optional[int] = None,
        num_fixed_melds: int = 0,
        pool_remaining: Optional[List[int]] = None,
    ) -> Optional[Dict]:
        """功能A：查大叫 / 查花猪 避坑雷达。
        当牌墙剩余牌数 <= 16 时启动生死预警：
        1. 查花猪预警：手牌若仍持有定缺花色，标红最高优先级必打牌。
        2. 查大叫预警：手牌未听牌时，强提醒必须打出何牌以实现最快叫听。
        """
        if tiles_remaining_in_wall > 20:
            return None

        # 1. 查花猪生死警报检测
        if dingque_suit is not None:
            suit_start = dingque_suit * 9
            dq_tiles_in_hand = [
                suit_start + i for i in range(9) if counts[suit_start + i] > 0
            ]
            if dq_tiles_in_hand:
                dq_names = [index27_to_chinese(t) for t in dq_tiles_in_hand]
                return {
                    "alert": True,
                    "level": "CRITICAL",
                    "type": "huazhu",
                    "title": "🚨 查花猪生死警报",
                    "message": f"牌墙仅剩 {tiles_remaining_in_wall} 张！手牌仍有定缺【{SUIT_NAMES[dingque_suit]}】({','.join(dq_names)})，必在流局前打光，否则全场顶格包赔！",
                    "must_discard": [index27_to_mpsz(t) for t in dq_tiles_in_hand],
                    "must_discard_cn": dq_names,
                }

        # 2. 查大叫叫听预警检测
        waiting = cls.find_waiting_tiles(counts, num_fixed_melds, pool_remaining, dingque_suit)
        if waiting:
            # 已经听牌，安全无忧
            return {
                "alert": False,
                "level": "SAFE",
                "type": "tenpai",
                "title": "✅ 已叫听",
                "message": f"当前已下叫听牌（余 {tiles_remaining_in_wall} 张），安心防守胡牌即可！",
                "must_discard": [],
                "must_discard_cn": [],
            }

        # 未听牌，必须找出能最快叫听的打法
        shanten = cls.calculate_shanten(counts, num_fixed_melds, dingque_suit)
        best_discards = []
        best_ukeire = -1
        # 寻找打哪张能进入 1-向听或叫听
        for d in range(27):
            if counts[d] > 0:
                counts[d] -= 1
                w = cls.find_waiting_tiles(counts, num_fixed_melds, pool_remaining, dingque_suit)
                counts[d] += 1
                if w:
                    u = sum(w.values())
                    if u > best_ukeire:
                        best_ukeire = u
                        best_discards = [d]
                    elif u == best_ukeire:
                        best_discards.append(d)

        rec_mpsz = [index27_to_mpsz(d) for d in best_discards[:2]]
        rec_cn = [index27_to_chinese(d) for d in best_discards[:2]]
        rec_str = " 或 ".join(rec_cn) if rec_cn else "任意多余孤张"

        return {
            "alert": True,
            "level": "WARNING" if tiles_remaining_in_wall > 10 else "CRITICAL",
            "type": "daxiao",
            "title": "⚠️ 查大叫避坑雷达",
            "message": f"牌墙仅剩 {tiles_remaining_in_wall} 张且尚未叫听！打【{rec_str}】可最速叫听，避免流局巨额赔叫！",
            "must_discard": rec_mpsz,
            "must_discard_cn": rec_cn,
        }

    @classmethod
    def evaluate_defense_radar(
        cls,
        counts: List[int],
        pool_remaining: Optional[List[int]] = None,
        opponents_dingque: Optional[List[int]] = None,
        opponents_danger_suits: Optional[List[int]] = None,
        pool_evidence: int = 0,
    ) -> List[Dict]:
        """防点炮雷达评级系统 (SAFE / SUSPICIOUS / DANGER)
        全息追踪对手打牌习惯与弃牌特征：
        - 绝对安全 (SAFE)：对手定缺门、绝张现物（已出4张或场上见3张）、或已被验证的现物。
        - 疑牌 (SUSPICIOUS)：场上见 1~2 张的邻张或偏张。
        - 极度危险 (DANGER)：对手清一色主攻门、中心中张(4/5/6)纯生张(0见)。

        pool_evidence：本局已可靠观测到的弃牌总数（牌河视觉 + 自家打出差分累计）。
        当证据不足（牌河尚未稳定读入）时，「尚未出现 / 纯生张」这类基于 seen==0 的
        危险判定毫无依据——没读到牌河当然什么都「未出现」。此时仅保留有实据的 SAFE
        评级（对手定缺门、现物绝张），抑制一切凭空捏造的 SUSPICIOUS/DANGER，
        从根源消除「无中生有、乱显示防守警告」。随着牌局推进证据累积，评级自动恢复。
        """
        ratings = []
        if opponents_dingque is None:
            opponents_dingque = []
        if opponents_danger_suits is None:
            opponents_danger_suits = []
        # 证据门控阈值：少于 3 张已观测弃牌时，牌河不可信，禁止基于「未见」的危险推断。
        evidence_ok = pool_evidence >= 3

        for t in range(27):
            if counts[t] <= 0:
                continue
            s = tile_to_suit(t)
            rem = pool_remaining[t] if pool_remaining is not None else max(0, 4 - counts[t])
            seen = 4 - rem - counts[t]
            num = (t % 9) + 1
            is_edge = (num in (1, 9))
            is_middle = (num in (4, 5, 6))

            if s in opponents_dingque:
                lvl = "SAFE"
                reason = f"绝对安全：场上有对手定缺【{SUIT_NAMES[s]}】，对其绝不点炮"
            elif rem == 0 or seen >= 3:
                lvl = "SAFE"
                reason = f"现物绝张：场上已见 {seen} 张，无人能以此牌胡牌"
            elif is_edge and seen >= 1:
                lvl = "SAFE"
                reason = f"边张相对安全：1/9 偏张且已见 {seen} 张"
            elif not evidence_ok:
                # 牌河证据不足：不得凭「未见」捏造危险/疑牌，直接跳过该张，交由 UI 显示中性状态。
                continue
            elif s in opponents_danger_suits and seen == 0:
                lvl = "DANGER"
                reason = f"极度高危：对手主攻【{SUIT_NAMES[s]}】门，此牌为未见生张，点炮率极高！"
            elif is_middle and seen == 0:
                lvl = "DANGER"
                reason = f"高危生张：中心张 {index27_to_chinese(t)} 纯生张，切勿在深牌墙轻易打出"
            elif seen == 0:
                lvl = "SUSPICIOUS"
                reason = f"疑牌生张：{index27_to_chinese(t)} 尚未出现，存在暗叫风险"
            else:
                lvl = "SUSPICIOUS"
                reason = f"一般张：场上已见 {seen} 张，常规防守"

            ratings.append({
                "tile": index27_to_mpsz(t),
                "tile_cn": index27_to_chinese(t),
                "level": lvl,
                "reason": reason,
                "seen": seen,
                "remaining": rem
            })
        return ratings

    @classmethod
    def recommend_huan_san_zhang(cls, counts: List[int]) -> Dict:
        """博弈级换三张算法（穷举 C(N, 3) 组合博弈最优解）。
        核心博弈准则：
        1. 留大做大：若手牌某一门特别长（>=7张），坚决保护该门做清一色。
        2. 换顺不换对：优先换全孤单张，绝对严禁拆散已成刻子、顺子或将对。
        3. 弃子防喂：尽量换出偏张/孤张（1/9/偏杂张），减少喂饱下家的概率。
        """
        suit_tiles = {SUIT_M: [], SUIT_P: [], SUIT_S: []}
        for t in range(27):
            for _ in range(counts[t]):
                suit_tiles[tile_to_suit(t)].append(t)

        suits_with_at_least_3 = [s for s, tiles in suit_tiles.items() if len(tiles) >= 3]
        if not suits_with_at_least_3:
            return {"viable": False, "reason": "手牌中无任意单门达到3张以上"}

        # 评估各门花色手牌长度，判断是否有清一色潜在优势
        suit_lengths = {s: len(suit_tiles[s]) for s in (SUIT_M, SUIT_P, SUIT_S)}
        max_suit = max(suit_lengths, key=suit_lengths.get)
        max_len = suit_lengths[max_suit]

        best_overall = None
        best_score = -999999.0

        for suit in suits_with_at_least_3:
            tiles_in_suit = suit_tiles[suit]
            total_in_suit = len(tiles_in_suit)

            # 若另一门达到 8+ 张，选择换出此门的意愿极高（推动清一色）
            is_sacrificial_suit = (suit != max_suit and max_len >= 8)

            # 枚举该花色下所有 3 张牌的组合 (C(N, 3))
            unique_combos = set(itertools.combinations(tiles_in_suit, 3))

            for combo in unique_combos:
                score = 0.0
                # 换出这 3 张牌后，评估本门剩余牌型损失
                sub_c = [0] * 9
                for t in tiles_in_suit:
                    sub_c[t % 9] += 1

                combo_c = [0] * 9
                for t in combo:
                    combo_c[t % 9] += 1

                # 惩罚项：破坏对子 / 刻子
                for i in range(9):
                    if combo_c[i] > 0:
                        orig = sub_c[i]
                        if orig >= 3 and combo_c[i] >= 1:
                            score -= 300.0 * combo_c[i]  # 拆刻子严重扣分
                        elif orig == 2 and combo_c[i] == 1:
                            score -= 150.0  # 拆对子扣分
                        elif orig == 1:
                            score += 80.0   # 成功甩掉孤张加分

                # 惩罚项：破坏已成顺子
                rem_c = [sub_c[i] - combo_c[i] for i in range(9)]
                for i in range(7):
                    if sub_c[i] >= 1 and sub_c[i+1] >= 1 and sub_c[i+2] >= 1:
                        if not (rem_c[i] >= 1 and rem_c[i+1] >= 1 and rem_c[i+2] >= 1):
                            score -= 120.0  # 拆顺子扣分

                # 奖励项：换出偏张/孤张（减少给下家喂好搭子的风险）
                for t in combo:
                    num = (t % 9) + 1
                    if num in (1, 9):
                        score += 30.0
                    elif num in (2, 8):
                        score += 15.0
                    elif num in (4, 5, 6):
                        score -= 10.0  # 换中张容易送给下家成顺子

                # 策略加分：若此花色张数本就最少，断门成本最低，换出全加分
                score += (14 - total_in_suit) * 25.0
                if is_sacrificial_suit:
                    score += 250.0

                if score > best_score:
                    best_score = score
                    best_overall = {
                        "suit": suit,
                        "combo": list(combo),
                    }

        if not best_overall:
            return {"viable": False, "reason": "未找到可行换三张方案"}

        chosen_suit = best_overall["suit"]
        chosen_tiles = sorted(best_overall["combo"])
        is_qing = (max_len >= 8 and chosen_suit != max_suit)
        target_name = SUIT_NAMES[max_suit] if is_qing else None

        reason_parts = [f"推荐换出【{SUIT_NAMES[chosen_suit]}】"]
        if is_qing:
            reason_parts.append(f"（留大做大：全力冲刺【{target_name}】清一色）")
        else:
            reason_parts.append(f"（仅持{suit_lengths[chosen_suit]}张，拆换成本最低，优先断门）")

        return {
            "viable": True,
            "suit": SUIT_NAMES[chosen_suit],
            "tiles": [index27_to_mpsz(t) for t in chosen_tiles],
            "tiles_cn": [index27_to_chinese(t) for t in chosen_tiles],
            "reason": "".join(reason_parts),
        }

    @classmethod
    def recommend_dingque(cls, counts: List[int]) -> Dict:
        """智能定缺决策评估"""
        def eval_suit(s):
            sub_c = [counts[s * 9 + i] for i in range(9)]
            total = sum(sub_c)
            isolated = sum(1 for i in range(9) if sub_c[i] == 1 and (i == 0 or sub_c[i - 1] == 0) and (i == 8 or sub_c[i + 1] == 0))
            return (total, isolated)

        scores = {s: eval_suit(s) for s in (SUIT_M, SUIT_P, SUIT_S)}
        best_suit = min(scores, key=lambda s: (scores[s][0], -scores[s][1]))
        return {
            "suit": SUIT_NAMES[best_suit],
            "suit_id": best_suit,
            "suit_char": SUIT_CHARS[best_suit],
            "count": scores[best_suit][0],
            "reason": f"【{SUIT_NAMES[best_suit]}】手牌最少(仅{scores[best_suit][0]}张)，断门代价最小，能以最快速度进入听牌"
        }

    @classmethod
    def generate_tile_matrix(
        cls,
        counts: List[int],
        disc_counts: Optional[List[int]] = None,
        meld_counts: Optional[List[int]] = None,
    ) -> Dict:
        """功能B：全场活牌厚度透视表（含 9x3 存活矩阵 + 活跃大张/绝张汇总透视）"""
        matrix = {}
        all_rem_tiles = []
        for s in (SUIT_M, SUIT_P, SUIT_S):
            sname = SUIT_NAMES[s]
            row = []
            for num in range(1, 10):
                t = s * 9 + num - 1
                vis = counts[t]
                if disc_counts and t < len(disc_counts):
                    vis += disc_counts[t]
                if meld_counts and t < len(meld_counts):
                    vis += meld_counts[t]
                rem = max(0, 4 - vis)
                tile_info = {
                    "tile": index27_to_mpsz(t),
                    "name": f"{num}{sname}",
                    "remaining": rem,
                    "status": "绝张" if rem == 0 else f"{rem}张",
                    "is_zero": (rem == 0),
                    "is_hot": (rem >= 3),
                }
                row.append(tile_info)
                all_rem_tiles.append(tile_info)
            matrix[sname] = row

        # 全场活跃大张透视（剩余 3~4 张的未见/多存活牌，按存活张数降序）
        hot_tiles = [t for t in all_rem_tiles if t["remaining"] >= 3]
        hot_tiles.sort(key=lambda x: x["remaining"], reverse=True)

        # 绝张死牌汇总
        dead_tiles = [t for t in all_rem_tiles if t["remaining"] == 0]

        return {
            "matrix": matrix,
            "hot_tiles": hot_tiles[:6],
            "dead_tiles": dead_tiles,
        }

