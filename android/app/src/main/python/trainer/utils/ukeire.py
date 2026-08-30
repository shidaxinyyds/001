from .shanten import calculate_shanten
from ..objects.tile_collection import TileCollection
from ..objects.tile import Tile


def calculate_ukeire(hand: TileCollection) -> int:
    output = 0
    base_shanten = calculate_shanten(hand)

    # Check adding every tile to see if it improves the shanten
    for tile in Tile.all_tiles:
        new_shanten = calculate_shanten(hand.add_tile(tile))
        if new_shanten >= base_shanten:
            continue
        output += 4 - hand.count_tile(tile)

    return output
