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


from ..utils.convert import tiles34_index_to_mpsz, tile_to_chinese


def calculate_discards_info(
    hand: TileCollection,
    available: Iterable[int],
    disc_counts: List[int],
    meld_counts: List[int],
) -> List[Dict]:
    """全信息推荐打法推演：计算每张候选弃牌的进张数、向听数及听牌绝张雷达详情。"""
    available_set: Set[int] = set(available)
    base_shanten = calculate_shanten(hand)
    own = hand.tiles34
    results = []

    for idx in range(34):
        c = own[idx]
        if c == 0:
            continue  # 手里没有这张，不能打
        new_own = own[:]
        new_own[idx] -= 1
        new_hand = TileCollection(new_own)
        new_shanten = calculate_shanten(new_hand)
        if new_shanten > base_shanten:
            continue  # 打出后向听恶化，不是候选
        u = 0
        ting_details = []
        for d in available_set:
            dc = new_own[d]
            if dc >= 4:
                continue
            wall = max(0, 4 - (dc + disc_counts[d] + meld_counts[d]))
            drawn = new_own[:]
            drawn[d] += 1
            s2 = calculate_shanten(TileCollection(drawn))
            if s2 < new_shanten:
                u += wall
                if new_shanten == 0:
                    t_str = tiles34_index_to_mpsz(d)
                    ting_details.append({
                        "tile": t_str,
                        "name": tile_to_chinese(t_str),
                        "remaining": wall,
                        "is_dead": (wall == 0),
                    })
        results.append({
            "tile": Tile(tiles34_index_to_mpsz(idx)),
            "tile_str": tiles34_index_to_mpsz(idx),
            "ukeire": u,
            "shanten": new_shanten,
            "ting_details": ting_details,
            "ting_tiles": [td["tile"] for td in ting_details],
        })

    results.sort(key=lambda x: -x["ukeire"])
    return results


def calculate_ukeire_ex(
    hand: TileCollection,
    available: Iterable[int],
    disc_counts: List[int],
    meld_counts: List[int],
) -> Dict[Tile, int]:
    """绝张感知进张（推荐打法核心）。保留以兼容历史接口。"""
    info = calculate_discards_info(hand, available, disc_counts, meld_counts)
    return {item["tile"]: item["ukeire"] for item in info}

