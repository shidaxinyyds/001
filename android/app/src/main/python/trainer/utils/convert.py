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


# 将 mpsz 记法（如 "1m"、"5p"、"1z"）转换为中文牌名（如 "1万"、"5筒"、"东"）
_SUIT_CN = {'m': '万', 'p': '筒', 's': '条'}
_HONOR_CN = {
    '1z': '东', '2z': '南', '3z': '西', '4z': '北',
    '5z': '白', '6z': '发', '7z': '中',
}


def tile_to_chinese(name: str) -> str:
    if name in _HONOR_CN:
        return _HONOR_CN[name]
    if len(name) == 2 and name[1] in _SUIT_CN:
        return f"{name[0]}{_SUIT_CN[name[1]]}"
    return name


def hand_to_chinese(mpsz: str) -> str:
    """将整手牌的 mpsz 字符串转换为中文，例如 '4788m12446s34p26z' -> '4万7万8万8万1万2万4万4万6万3筒4筒2字6字'。"""
    buffer = []
    temp = []
    for ch in mpsz:
        if ch.isdigit():
            temp.append(ch)
        elif ch in 'mpsz':
            for d in temp:
                buffer.append(tile_to_chinese(f"{d}{ch}"))
            temp = []
        # 其它字符忽略
    return ''.join(buffer)

