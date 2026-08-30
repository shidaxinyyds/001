from ..utils.convert import tiles34_index_to_mpsz


class classproperty:
    def __init__(self, func):
        self.fget = func
    def __get__(self, instance, owner):
        return self.fget(owner)

class Tile:
    def __init__(self, name: str):
        self.name = name

    @classmethod
    def from_tile34_index(cls, index: int):
        label = tiles34_index_to_mpsz(index)
        return Tile(label)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"Tile({self.name})"

    def __eq__(self, other: object):
        return isinstance(other, Tile) and self.name == other.name

    def __hash__(self):
        return hash(self.name)

    @classproperty
    def all_tiles(cls):
        for i in range(34):
            yield Tile(tiles34_index_to_mpsz(i))

