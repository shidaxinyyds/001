from mahjong.shanten import Shanten

from ..objects.tile_collection import TileCollection

def calculate_shanten(hand: TileCollection) -> int:
    return Shanten().calculate_shanten(tiles_34=hand.tiles34)
