from typing import Dict
from .objects.tile import Tile
from .objects.tile_collection import TileCollection
from .utils.shanten import calculate_shanten
from .utils.ukeire import calculate_ukeire

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

        if tile not in valid_discards:
            message = (
                f"You discarded {tile}, which increases your shanten!" + '\n'
                "You are now further from ready.")
        elif valid_discards[tile] != best_ukeire:
            message = (
                f"You discarded {tile} which results in {valid_discards[tile]} tiles that can improve your hand." + '\n'
                f"The most efficient tiles are {','.join(str(e) for e in best_discards)}, which results in {best_ukeire}.")
        else:
            alternatives = best_discards.copy()
            alternatives.remove(tile)
            message = (
                f"You discarded {tile} which results in {valid_discards[tile]} tiles that can improve your hand." + '\n'
                f"That was the best choice!")

            if len(best_discards) > 1:
                message += '\n' + f"Other discards include {','.join(str(e) for e in alternatives)}"

        self.hand = self.hand.remove_tile(tile)
        return message

    def draw(self, tile: Tile) -> str:
        self.hand = self.hand.add_tile(tile)
        return f"You drew a {tile}."






