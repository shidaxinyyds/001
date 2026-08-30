from typing import Dict
from .objects.tile import Tile
from .objects.tile_collection import TileCollection
from .utils.shanten import calculate_shanten
from .utils.ukeire import calculate_ukeire
from .utils.convert import tile_to_chinese

class Trainer:
    def __init__(self, hand: TileCollection):
        self.hand = hand

    def get_shanten(self):
        return calculate_shanten(self.hand)

    def calculate_discards(self) -> Dict[Tile, int]:
        hand = self.hand
        output: Dict[Tile, int] = {}

        base_shanten = calculate_shanten(hand)
        for tile in hand.unique:
            new_hand = hand.remove_tile(tile)
            new_shanten = calculate_shanten(new_hand)
            if new_shanten > base_shanten:
                continue
            output[tile] = calculate_ukeire(hand.remove_tile(tile))

        return output

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






