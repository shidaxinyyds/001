from __future__ import annotations
import random
from typing import Dict, List

from ..objects.tile import Tile
from ..utils.convert import expand_mpsz, mpsz_to_tile34_index, tiles34_index_to_mpsz

class TileCollection:
    def __init__(self, arr: List[int]):
        assert len(arr) == 34
        self.tiles34 = arr

    @property
    def all(self):
        for i, count in enumerate(self.tiles34):
            for _ in range(count):
                yield Tile(tiles34_index_to_mpsz(i))

    @property
    def unique(self):
        for i in range(len(self.tiles34)):
            if self.tiles34[i] == 0:
                continue
            yield Tile(tiles34_index_to_mpsz(i))


    @classmethod
    def get_full(cls):
        return TileCollection([4] * 34)

    @classmethod
    def get_empty(cls):
        return TileCollection([0] * 34)

    @classmethod
    def from_mpsz(cls, s: str):
        tiles = [0] * 34
        for tile in expand_mpsz(s):
            index = mpsz_to_tile34_index(tile)
            tiles[index] += 1
        return TileCollection(tiles)


    def add_tile(self, tile: Tile):
        index = mpsz_to_tile34_index(tile.name)
        new_tiles = self.tiles34.copy()
        new_tiles[index] += 1
        return TileCollection(new_tiles)

    def remove_tile(self, tile: Tile):
        index = mpsz_to_tile34_index(tile.name)
        if self.tiles34[index] == 0:
            raise Exception(f"No such tile: {tile}")
        new_tiles = self.tiles34.copy()
        new_tiles[index] -= 1
        return TileCollection(new_tiles)

    def count_tile(self, tile: Tile) -> int:
        index = mpsz_to_tile34_index(tile.name)
        return self.tiles34[index]



    def count_tiles(self) -> int:
        return sum(self.tiles34)

    def randomly_select(self, n: int) -> TileCollection:
        """
        Generates a random n tiles.

        Returns:
            : A dictionary containing the generated hand, available tiles, and tile pool.
        """

        if self.count_tiles() < n:
            return None

        hand = self.get_empty()

        for _ in range(n):
            index = random.choices(range(34), weights=self.tiles34)[0]

            hand.tiles34[index] += 1
            self.tiles34[index] -= 1

        return hand

    def __eq__(self, other: object):
        return isinstance(other, TileCollection) and self.tiles34 == other.tiles34

    def get_difference(self, other: TileCollection):
        output: Dict[Tile, int] = {}
        for i in range(34):
            d = self.tiles34[i] - other.tiles34[i]
            if d == 0:
                continue
            tile = Tile.from_tile34_index(i)
            output[tile] = d
        return output

    def __len__(self):
        return sum(self.tiles34)

    def __repr__(self):
        mpsz = ""
        for tile in self.all:
            mpsz += str(tile)
        return f"TileCollection({mpsz})"

