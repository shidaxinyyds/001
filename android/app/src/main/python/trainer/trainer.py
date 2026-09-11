from typing import Dict, List, Optional

from .objects.tile import Tile
from .objects.tile_collection import TileCollection
from .utils.shanten import calculate_shanten
from .utils.ukeire import calculate_ukeire_ex
from .utils.convert import tile_to_chinese
from modes import DEFAULT_MODE, available_set

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

        self.sichuan_results: List[Dict] = []

    def set_dingque(self, suit: Optional[int]) -> None:
        """设置四川麻将定缺门（0=万, 1=筒, 2=条）。"""
        self.dingque_suit = suit

    def set_visible(self, disc_counts: List[int], meld_counts: List[int]) -> None:
        """引擎在每帧识别后调用：传入当前牌河 / 副露计数，供进张计算扣减绝张。"""
        if disc_counts is not None:
            self.disc_counts = list(disc_counts)
        if meld_counts is not None:
            self.meld_counts = list(meld_counts)

    def get_shanten(self):
        if self.mode == "sc":
            try:
                from sichuan import SichuanAnalyzer
                hand_indices = SichuanAnalyzer.parse_hand_mpsz(str(self.hand))
                counts = SichuanAnalyzer.counts_from_tiles(hand_indices)
                return SichuanAnalyzer.calculate_shanten(counts)
            except Exception:
                pass
        return calculate_shanten(self.hand)

    def calculate_discards(self) -> Dict[Tile, int]:
        """返回 {候选弃牌: 进张数}，进张已按绝张扣减（见 calculate_ukeire_ex）。"""
        if self.mode == "sc":
            try:
                from sichuan import SichuanAnalyzer
                hand_indices = SichuanAnalyzer.parse_hand_mpsz(str(self.hand))
                counts = SichuanAnalyzer.counts_from_tiles(hand_indices)
                # 108 牌物理守恒：真实扣减全场公开可见牌
                pool_remaining = [0] * 27
                for i in range(27):
                    vis = counts[i]
                    if i < len(self.disc_counts):
                        vis += self.disc_counts[i]
                    if i < len(self.meld_counts):
                        vis += self.meld_counts[i]
                    pool_remaining[i] = max(0, 4 - vis)

                self.sichuan_results = SichuanAnalyzer.analyze_discards(
                    counts, pool_remaining=pool_remaining, dingque_suit=self.dingque_suit
                )
                res = {}
                for item in self.sichuan_results:
                    res[Tile(item["tile"])] = item["ukeire"]
                return res
            except Exception:
                pass

        return calculate_ukeire_ex(
            self.hand, self.available, self.disc_counts, self.meld_counts
        )

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
