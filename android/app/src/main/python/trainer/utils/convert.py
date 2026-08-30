from typing import List

def mpsz_to_tile34_index(tile: str) -> int:
    assert (
        len(tile) == 2
        and tile[0].isdigit()
        and 1 <= int(tile[0]) <= 9
        and tile[1] in 'mpsz'), tile

    if tile[1] == 'm':
        return int(tile[0]) - 1
    elif tile[1] == 'p':
        return int(tile[0]) + 9 - 1
    elif tile[1] == 's':
        return int(tile[0]) + 18 - 1
    elif tile[1] == 'z':
        assert int(tile[0]) <= 7
        return int(tile[0]) + 27 - 1
    else:
        assert False

def tiles34_index_to_mpsz(index: int) -> str:
    if 0 <= index <= 8:
        return f"{index % 9 + 1}m"
    if 9 <= index <= 17:
        return f"{index % 9 + 1}p"
    if 18 <= index <= 26:
        return f"{index % 9 + 1}s"
    if 27 <= index <= 34:
        return f"{index % 9 + 1}z"
    assert False



def expand_mpsz(s: str) -> List[str]:
    output = []
    temp = []

    for ch in s:
        if ch.isdigit():
            temp.append(ch)
        elif ch in "mpsz":
            for ch2 in temp:
                output.append(ch2 + ch)
            temp = []
        else:
            raise ValueError(f"The queried hand contains an unrecognised character: {ch} ({s})")

    return output

