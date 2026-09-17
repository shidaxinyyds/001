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
from typing import Dict, List, Optional, Set, Tuple


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
    return f"未知({idx})"


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
                counts[pair_tile] += 2

        return False

    @classmethod
    def can_win(cls, counts: List[int], num_fixed_melds: int = 0) -> bool:
        """川麻胡牌核心判定（支持红中赖子万能百搭，必须缺一门）。"""
        suits = cls.get_suits_in_hand(counts)
        if len(suits) > 2:
            return False

        num_wild = counts[27] if len(counts) > 27 else 0
        if num_wild == 0:
            return cls._can_win_no_wild(counts[:27], num_fixed_melds)

        # 带有红中赖子的七对判定
        if num_fixed_melds == 0:
            num_pairs = sum(counts[i] // 2 for i in range(27))
            num_singles = sum(counts[i] % 2 for i in range(27))
            if num_wild >= num_singles and (num_pairs + num_singles + (num_wild - num_singles) // 2) >= 7:
                return True

        # 赖子替张枚举
        cand = set()
        for t in range(27):
            if counts[t] > 0:
                cand.add(t)
                num = t % 9
                if num > 0: cand.add(t - 1)
                if num < 8: cand.add(t + 1)
                if num > 1: cand.add(t - 2)
                if num < 7: cand.add(t + 2)
        if not cand:
            return True

        return cls._can_win_wild_recurse(list(counts[:27]), num_wild, sorted(cand), num_fixed_melds)

    @classmethod
    def _can_win_wild_recurse(
        cls, c27: List[int], wilds_left: int, cand: List[int], num_fixed_melds: int
    ) -> bool:
        if wilds_left == 0:
            return cls._can_win_no_wild(c27, num_fixed_melds)
        for t in cand:
            c27[t] += 1
            if cls._can_win_wild_recurse(c27, wilds_left - 1, cand, num_fixed_melds):
                c27[t] -= 1
                return True
            c27[t] -= 1
        return False

    @staticmethod
    def get_meld_protected_tiles(counts_27: List[int]) -> Set[int]:
        """返回必须保护的已成顺子/刻子牌集合（弃打会破坏已成型牌面）。"""
        protected = set()
        for s in range(3):
            sub = counts_27[s * 9 : (s + 1) * 9]
            if sum(sub) < 3:
                continue
            best_melds = -1
            best_leftover_lists = []

            def dfs(c, melds, pairs, leftovers):
                nonlocal best_melds, best_leftover_lists
                idx = -1
                for i in range(9):
                    if c[i] > 0:
                        idx = i
                        break
                if idx == -1:
                    score = melds * 10 + pairs * 3
                    if score > best_melds:
                        best_melds = score
                        best_leftover_lists = [set(leftovers)]
                    elif score == best_melds:
                        best_leftover_lists.append(set(leftovers))
                    return

                if c[idx] >= 3:
                    c[idx] -= 3
                    dfs(c, melds + 1, pairs, leftovers)
                    c[idx] += 3

                if idx <= 6 and c[idx + 1] > 0 and c[idx + 2] > 0:
                    c[idx] -= 1
                    c[idx + 1] -= 1
                    c[idx + 2] -= 1
                    dfs(c, melds + 1, pairs, leftovers)
                    c[idx] += 1
                    c[idx + 1] += 1
                    c[idx + 2] += 1

                if c[idx] >= 2:
                    c[idx] -= 2
                    dfs(c, melds, pairs + 1, leftovers)
                    c[idx] += 2

                c[idx] -= 1
                dfs(c, melds, pairs, leftovers + [idx])
                c[idx] += 1

            dfs(list(sub), 0, 0, [])
            if best_melds >= 10:
                all_leftovers = set().union(*best_leftover_lists) if best_leftover_lists else set()
                for num in range(9):
                    if sub[num] > 0 and num not in all_leftovers:
                        protected.add(s * 9 + num)
        return protected

    @classmethod
    def find_waiting_tiles(
        cls, counts: List[int], num_fixed_melds: int = 0, pool_remaining: Optional[List[int]] = None
    ) -> Dict[int, int]:
        """找出当前手牌能够胡的牌（叫口）及其真实剩余存活数。"""
        waiting = {}
        suits = cls.get_suits_in_hand(counts)
        if len(suits) > 2:
            return waiting

        for test_tile in range(27):
            test_suit = tile_to_suit(test_tile)
            new_suits = suits | {test_suit}
            if len(new_suits) > 2:
                continue

            counts[test_tile] += 1
            if cls.can_win(counts, num_fixed_melds):
                if pool_remaining is not None and 0 <= test_tile < len(pool_remaining):
                    rem = pool_remaining[test_tile]
                else:
                    rem = max(0, 4 - counts[test_tile])
                waiting[test_tile] = rem
            counts[test_tile] -= 1

        return waiting

    @classmethod
    def calculate_shanten(
        cls, counts: List[int], num_fixed_melds: int = 0
    ) -> int:
        """计算四川麻将向听数（0=听牌/胡牌, 1=1向听, 2=2向听...）。支持红中赖子。"""
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

        num_wild = counts[27] if len(counts) > 27 else 0
        total_tiles = sum(counts[:27]) + num_wild
        if total_tiles % 3 == 2:
            if cls.can_win(counts, num_fixed_melds):
                return 0
        elif total_tiles % 3 == 1:
            waits = cls.find_waiting_tiles(counts, num_fixed_melds)
            if waits:
                return 0

        if extra_dingque_penalty > 0:
            return max(1, extra_dingque_penalty)

        # 1 向听检测
        if total_tiles % 3 == 2:
            for d in range(27):
                if counts[d] > 0:
                    counts[d] -= 1
                    w = cls.find_waiting_tiles(counts, num_fixed_melds)
                    counts[d] += 1
                    if w:
                        return 1
        elif total_tiles % 3 == 1:
            for t in range(27):
                counts[t] += 1
                for d in range(27):
                    if counts[d] > 0:
                        counts[d] -= 1
                        w = cls.find_waiting_tiles(counts, num_fixed_melds)
                        counts[d] += 1
                        if w:
                            counts[t] -= 1
                            return 1
                counts[t] -= 1

        base_shanten = 2
        if num_wild > 0:
            base_shanten = max(1, base_shanten - num_wild)
        return base_shanten

    @classmethod
    def calculate_fan(cls, counts: List[int], win_tile: int, num_fixed_melds: int = 0) -> int:
        """估算川麻胡牌番数（清一色、七对、根数）。"""
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
                    w = cls.find_waiting_tiles(counts, num_fixed_melds, pool_remaining)
                    counts[d] += 1
                    if w:
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
    ) -> List[Dict]:
        """核心选叫与出牌分析器（EV 期望价值排序模型）。"""
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
            waiting_dict = cls.find_waiting_tiles(counts, num_fixed_melds, pool_remaining)
            real_ukeire = sum(waiting_dict.values()) if waiting_dict else 0
            ting_mpsz_list = [index27_to_mpsz(w) for w in waiting_dict.keys()]
            ting_cn_list = [index27_to_chinese(w) for w in waiting_dict.keys()]

            is_dingque_discard = (dingque_suit is not None and tile_to_suit(discard) == dingque_suit)
            ev_score = 0.0

            if waiting_dict:
                shanten = 0
                fan_sum = 0
                for w_tile, rem in waiting_dict.items():
                    w_fan = cls.calculate_fan(counts, w_tile, num_fixed_melds)
                    fan_sum += rem * (2 ** w_fan)
                ev_score = 50000.0 + fan_sum * 10.0 + real_ukeire * 5.0

                is_qing = len(cls.get_suits_in_hand(counts)) == 1
                suffix = " (清一色)" if is_qing else ""
                if real_ukeire > 0:
                    reason = f"听 {'/'.join(ting_cn_list[:3])}，余 {real_ukeire} 张{suffix}"
                else:
                    reason = f"听 {'/'.join(ting_cn_list[:3])}（绝张）{suffix}"
            else:
                shanten_raw = cls.calculate_shanten(counts, num_fixed_melds)
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

            if is_dingque_discard:
                ev_score += 100000.0

            results.append({
                "tile": index27_to_mpsz(discard),
                "tile_idx": discard,
                "ukeire": real_ukeire,
                "shanten": shanten,
                "ev": round(ev_score, 1),
                "ting_tiles": ting_mpsz_list,
                "reason": reason,
                "is_dingque": is_dingque_discard,
            })

            counts[discard] += 1

        results.sort(key=lambda item: item["ev"], reverse=True)
        return results

    @classmethod
    def evaluate_defense_radar(
        cls,
        counts: List[int],
        pool_remaining: Optional[List[int]] = None,
        opponents_dingque: Optional[List[int]] = None,
    ) -> List[Dict]:
        """防点炮雷达评级 (SAFE / SUSPICIOUS / DANGER)"""
        ratings = []
        if opponents_dingque is None:
            opponents_dingque = []
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
                reason = f"防点炮天条：场上有对手定缺【{SUIT_NAMES[s]}】，该门对其为绝对安全牌"
            elif rem == 0 or seen >= 3:
                lvl = "SAFE"
                reason = f"绝张现物：场上已见 {seen} 张，无人能以此牌胡牌"
            elif is_edge and seen >= 1:
                lvl = "SAFE"
                reason = f"边张安全：1/9 偏张且场上已见 {seen} 张，点炮率极低"
            elif is_middle and seen == 0:
                lvl = "DANGER"
                reason = f"极度高危：中心张 {index27_to_chinese(t)} 为纯生张(未见)，点炮率极高，切勿轻易打出！"
            elif seen == 0:
                lvl = "SUSPICIOUS"
                reason = f"疑牌生张：{index27_to_chinese(t)} 场上尚未出现，存在叫口风险"
            else:
                lvl = "SUSPICIOUS"
                reason = f"一般疑牌：场上已见 {seen} 张，注意防守"

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
        """开局换三张推荐：换顺不换对，拆孤张，防送清一色"""
        suit_tiles = {SUIT_M: [], SUIT_P: [], SUIT_S: []}
        for t in range(27):
            for _ in range(counts[t]):
                suit_tiles[tile_to_suit(t)].append(t)

        suits_with_at_least_3 = [s for s, tiles in suit_tiles.items() if len(tiles) >= 3]
        if not suits_with_at_least_3:
            return {"viable": False, "reason": "无满3张的花色可换"}

        def suit_cost(s):
            tiles = suit_tiles[s]
            total = len(tiles)
            sub_c = [0] * 9
            for t in tiles:
                sub_c[t % 9] += 1
            isolated = sum(1 for i in range(9) if sub_c[i] == 1 and (i == 0 or sub_c[i - 1] == 0) and (i == 8 or sub_c[i + 1] == 0))
            return (total, -isolated)

        best_suit = min(suits_with_at_least_3, key=suit_cost)
        cand_tiles = suit_tiles[best_suit]

        def tile_priority(t):
            num = (t % 9) + 1
            c = counts[t]
            is_edge = (num in (1, 9))
            is_near_edge = (num in (2, 8))
            is_mid = (num in (4, 5, 6))
            p = 0
            if c == 1: p += 10
            elif c == 2: p += 50
            else: p += 100
            if is_edge: p += 1
            elif is_near_edge: p += 3
            elif is_mid: p += 8
            return p

        cand_tiles.sort(key=tile_priority)
        chosen = cand_tiles[:3]
        return {
            "viable": True,
            "suit": SUIT_NAMES[best_suit],
            "tiles": [index27_to_mpsz(t) for t in chosen],
            "tiles_cn": [index27_to_chinese(t) for t in chosen],
            "reason": f"【{SUIT_NAMES[best_suit]}】仅持 {len(cand_tiles)} 张，拆换成本最低，优先换出孤张偏张"
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
        """生成 9x3 记牌器存活矩阵"""
        matrix = {}
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
                row.append({
                    "tile": index27_to_mpsz(t),
                    "name": f"{num}{sname}",
                    "remaining": rem,
                    "status": "绝张" if rem == 0 else f"{rem}张",
                    "is_zero": (rem == 0)
                })
            matrix[sname] = row
        return matrix
