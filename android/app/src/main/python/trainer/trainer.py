from typing import Dict, List, Optional

from .objects.tile import Tile
from .objects.tile_collection import TileCollection
from .utils.shanten import calculate_shanten
from .utils.ukeire import calculate_ukeire_ex, calculate_discards_info
from .utils.convert import tile_to_chinese
from modes import DEFAULT_MODE, available_set, is_sichuan_family, get_laizi, get_mode, get_analyzer

class Trainer:
    def __init__(self, hand: TileCollection, mode: str = DEFAULT_MODE):
        self.hand = hand
        self.mode = mode
        # 该玩法可用牌的 34 型索引集合（二/三麻去掉的牌不计入）
        self.available: set = available_set(mode)
        # 「已可见但不在自己手牌里」的牌计数（长度 34）。
        # disc_counts = 牌河（所有玩家打出的牌）；meld_counts = 副露（吃碰杠）。
        # 由引擎每帧根据识别结果刷新，用于绝张扣减。
        self.disc_counts: List[int] = [0] * 34
        self.meld_counts: List[int] = [0] * 34
        self.dingque_suit: Optional[int] = None
        self.opponent_dingque_suits: List[int] = []

        self.sichuan_results: List[Dict] = []
        self.general_results: List[Dict] = []
        self.std_results: List[Dict] = []
        # 玩法引擎路由："sichuan" / "std" / ""（历史回退）
        self.analyzer: str = get_analyzer(mode)
        self.rules: Dict = get_mode(mode)

    def set_dingque(self, suit: Optional[int]) -> None:
        """设置四川麻将定缺门（0=万, 1=筒, 2=条）。"""
        self.dingque_suit = suit

    def set_opponent_dingque(self, suits: List[int]) -> None:
        """设置对手定缺门列表，供防点炮雷达扣减危险与安全加分。"""
        self.opponent_dingque_suits = list(suits) if suits else []

    def set_visible(self, disc_counts: List[int], meld_counts: List[int]) -> None:
        """引擎在每帧识别后调用：传入当前牌河 / 副露计数，供进张计算扣减绝张。"""
        if disc_counts is not None:
            self.disc_counts = list(disc_counts)
        if meld_counts is not None:
            self.meld_counts = list(meld_counts)

    def get_shanten(self):
        if self.analyzer == "std":
            try:
                from std import StdAnalyzer
                return StdAnalyzer.calculate_shanten(
                    list(self.hand.tiles34), 0, self.rules)
            except Exception:
                pass
        if is_sichuan_family(self.mode):
            try:
                from sichuan import SichuanAnalyzer
                hand_mpsz = "".join(str(t) for t in self.hand.all)
                hand_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz)
                counts = SichuanAnalyzer.counts_from_tiles(hand_indices)
                return SichuanAnalyzer.calculate_shanten(counts)
            except Exception:
                pass
        return calculate_shanten(self.hand)

    def calculate_discards(self) -> Dict[Tile, int]:
        """返回 {候选弃牌: 进张数}，进张已按绝张扣减（见 calculate_ukeire_ex）。"""
        if self.analyzer == "std":
            try:
                from std import StdAnalyzer
                counts = list(self.hand.tiles34)
                # 牌池物理守恒（34 型）：扣除手牌 + 牌河 + 副露可见量
                pool_remaining = [0] * 34
                for i in range(34):
                    vis = counts[i]
                    if i < len(self.disc_counts):
                        vis += self.disc_counts[i]
                    if i < len(self.meld_counts):
                        vis += self.meld_counts[i]
                    pool_remaining[i] = max(0, 4 - vis)
                self.std_results = StdAnalyzer.analyze_discards(
                    counts, self.rules, sorted(self.available),
                    pool_remaining=pool_remaining, fixed_melds=0,
                )
                res: Dict[Tile, int] = {}
                for item in self.std_results:
                    res[Tile(item["tile"])] = item["ukeire"]
                return res
            except Exception:
                pass
        if is_sichuan_family(self.mode):
            try:
                from sichuan import SichuanAnalyzer
                from sichuan.sichuan_analyzer import pool_remaining_from_visible
                hand_mpsz = "".join(str(t) for t in self.hand.all)
                hand_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz)
                counts = SichuanAnalyzer.counts_from_tiles(hand_indices)
                # 牌池物理守恒：真实扣减全场公开可见牌（支持 28 型，含 7z 红中）；
                # 与 engine 兜底路径共用同一守恒函数，口径统一。
                pool_remaining = pool_remaining_from_visible(
                    counts, self.disc_counts, self.meld_counts)

                self.sichuan_results = SichuanAnalyzer.analyze_discards(
                    counts,
                    pool_remaining=pool_remaining,
                    dingque_suit=self.dingque_suit,
                    opponent_dingque_suits=self.opponent_dingque_suits,
                )
                res = {}
                for item in self.sichuan_results:
                    res[Tile(item["tile"])] = item["ukeire"]
                return res
            except Exception:
                pass


        self.general_results = calculate_discards_info(
            self.hand, self.available, self.disc_counts, self.meld_counts
        )
        return {item["tile"]: item["ukeire"] for item in self.general_results}


    def discard(self, tile: Tile) -> str:
        valid_discards = self.calculate_discards()

        best_discards = []
        best_ukeire = 0
        for discard, ukeire in valid_discards.items():
            if ukeire > best_ukeire:
                best_discards = [discard]
                best_ukeire = ukeire
            elif ukeire == best_ukeire:
                best_discards.append(discard)

        tile_cn = tile_to_chinese(str(tile))
        best_cn = '、'.join(tile_to_chinese(str(e)) for e in best_discards)

        if tile not in valid_discards:
            message = (
                f"你打出了{tile_cn}，这会导致向听数增加！" + '\n'
                "你离听牌更远了。")
        elif valid_discards[tile] != best_ukeire:
            message = (
                f"你打出了{tile_cn}，此时可进张 {valid_discards[tile]} 张。" + '\n'
                f"最高效的打法是 {best_cn}，可进张 {best_ukeire} 张。")
        else:
            alternatives = best_discards.copy()
            alternatives.remove(tile)
            message = (
                f"你打出了{tile_cn}，此时可进张 {valid_discards[tile]} 张。" + '\n'
                "这是当前最优的选择！")

            if len(best_discards) > 1:
                message += '\n' + f"其他同等高效的打法：{('、'.join(tile_to_chinese(str(e)) for e in alternatives))}"

        self.hand = self.hand.remove_tile(tile)
        return message

    def draw(self, tile: Tile) -> str:
        self.hand = self.hand.add_tile(tile)
        return f"你摸到了 {tile_to_chinese(str(tile))}。"
