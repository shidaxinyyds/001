from .shanten import calculate_shanten
from ..objects.tile_collection import TileCollection
from ..objects.tile import Tile
from ..utils.convert import tiles34_index_to_mpsz

from typing import Dict, Iterable, List, Set


def calculate_ukeire(hand: TileCollection) -> int:
    """原始进张（不区分玩法 / 不扣绝张），保留以兼容旧调用。"""
    output = 0
    base_shanten = calculate_shanten(hand)

    # Check adding every tile to see if it improves the shanten
    for tile in Tile.all_tiles:
        new_shanten = calculate_shanten(hand.add_tile(tile))
        if new_shanten >= base_shanten:
            continue
        output += 4 - hand.count_tile(tile)

    return output


def calculate_ukeire_ex(
    hand: TileCollection,
    available: Iterable[int],
    disc_counts: List[int],
    meld_counts: List[int],
) -> Dict[Tile, int]:
    """绝张感知进张（推荐打法核心）。

    与原始 calculate_ukeire 的区别：
      - 只考虑该玩法「可用牌集」available（二/三麻去掉的牌不计入）；
      - 每张可摸进的牌 d 贡献 = 墙内剩余张数，而墙内剩余
        = 4 - (自己手牌中 d 的剩余数 + 牌河中 d 的数量 + 副露中 d 的数量)；
        若 d 已出尽（绝张，剩余 ≤ 0）则贡献为 0 —— 这就是"绝张扣减"，
        避免把永远摸不到的牌算进进张，从而让推荐更贴近真实胜率。

    disc_counts / meld_counts：长度 34 的数组，表示「已可见但不在自己手牌里」的牌
    （牌河 = 所有玩家打出的牌；副露 = 自己/别家已亮出的吃碰杠）。

    返回 {弃牌 Tile: 进张数}，仅含「打出后向听不恶化」的牌。
    """
    available_set: Set[int] = set(available)
    base_shanten = calculate_shanten(hand)
    own = hand.tiles34
    out: Dict[Tile, int] = {}

    for idx in range(34):
        c = own[idx]
        if c == 0:
            continue  # 手里没有这张，不能打
        # 候选弃牌 = idx；打出后手牌 new_own
        new_own = own[:]
        new_own[idx] -= 1
        new_hand = TileCollection(new_own)
        new_shanten = calculate_shanten(new_hand)
        if new_shanten > base_shanten:
            continue  # 打出后向听恶化，不是候选
        u = 0
        for d in available_set:
            dc = new_own[d]
            if dc >= 4:
                continue
            # 墙内剩余 = 4 - (自己剩余手牌 + 牌河可见 + 副露可见)
            wall = 4 - (dc + disc_counts[d] + meld_counts[d])
            if wall <= 0:
                continue  # 该牌已出尽或不可见，摸不到
            drawn = new_own[:]
            drawn[d] += 1
            s2 = calculate_shanten(TileCollection(drawn))
            if s2 < new_shanten:
                u += wall
        out[Tile(tiles34_index_to_mpsz(idx))] = u

    return out
