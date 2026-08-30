from .objects.tile import Tile
from .objects.tile_collection import TileCollection
from .trainer import Trainer
from .utils.convert import tiles34_index_to_mpsz

def main():
    hand = TileCollection.from_mpsz("3568889m238p3678s")
    trainer = Trainer(hand)

    # hack
    selected = hand.randomly_select(1)
    for i in range(34):
        if selected.tiles34[i] == 1:
            tile = Tile(tiles34_index_to_mpsz(i))

    # print(f"You discarded {tile}")
    message = trainer.discard(tile)
    return message
    # print("flush")
