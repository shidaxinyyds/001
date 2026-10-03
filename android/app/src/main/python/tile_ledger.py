# -*- coding: utf-8 -*-
"""牌局账本 (Tile Ledger)：把「没见过的牌」严格拆成 还在牌墙里 / 在别人手上。

为什么必须拆
------------
只看 `4 − 已现`（engine 的 remaining_matrix、analyzer 的 pool_remaining）把两堆混成了
一堆：某张牌还剩 3 张未现，可能 3 张都在牌墙（我摸得到），也可能 2 张正攥在两家手里
（永远不会有第三家把它打出来）。对「这搭子还留不留」「我这口还有没有机会」「这张牌打
出去会不会点炮」，两种情况的正确答案完全不同，混着算就会系统性地高估机会。

能算到多确定
------------
* 未现总数 `U = Σ(每型 4 张 − 已现)`：**精确**。已现 = 我的手牌 + 全场牌河 + 全场副露，
  三者都是公开信息。
* 对手站立手牌总数 `S = Σ(起手张数 − 3 × 副露数)`：**精确**（副露公开）。杠按 3 张/副露
  记账（实际手上少 4 张、但补摸 1 张再打 1 张），故 S 取**偏大**口径 → 牌墙下界保守，
  不会虚报「还能摸到」。
* 牌墙剩余 `W = U − S`：**精确值**。牌墙是唯一没被看见的那堆牌。
* **逐型**的牌墙量只能是区间（无法知道某张 5 万具体在墙里还是在人家手上）：
      wall ∈ [max(0, 未现 − 对手容量), min(未现, W)]
  但当「所有对手都定缺该门」时对手容量为 0，区间塌缩成确定值 —— 这正是
  「这张只能自摸」的可验证依据，也是川麻定缺信息独有的价值。

口径
----
全 34 型索引（0-8 万 / 9-17 筒 / 18-26 条 / 27-33 东南西北白发中），与 engine 的
`_monotonic_discards`、`_meld_counts_34` 一致；川麻 28 型口径由 sichuan_analyzer 在
调用处换算（红中 27 型 ↔ 33 索引）。

两个建模假设（别当成绝对真理）
------------------------------
1. 「定缺门上界 = 0」把定缺当硬约束。真实情形是对手刚摸到一张该门牌、还没来得及打
   出去的那一瞬间，故 `wall_lo` 可能偏乐观一张。方向上保守于“点炮机会”（低估别人
   会打给你），所以只会把“只能自摸”说得偏绝对一点，不会误导你往危险牌上撞。
2. 对手站立张数用 13−3×副露 估计，杠按 3 张计 → 高估手上的牌、低估牌墙，宁可少报
   “还能摸到”。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

NUM_TYPES = 34
TILES_PER_TYPE = 4
DEFAULT_START_HAND = 13
_HONOR_NAMES = "东南西北白发中"


def idx_to_mpsz(idx: int) -> str:
    """34 型索引 → mpsz。与 std.std_analyzer.index_to_mpsz 同口径（有测试锁定）。"""
    if 27 <= idx <= 33:
        return f"{idx - 26}z"
    return f"{idx % 9 + 1}" + "mps"[idx // 9]


def idx_to_chinese(idx: int) -> str:
    """34 型索引 → 中文。字牌单字，数牌带花色。"""
    if 27 <= idx <= 33:
        return _HONOR_NAMES[idx - 27]
    return f"{idx % 9 + 1}" + "万筒条"[idx // 9]


def tile_suit(idx: int) -> Optional[int]:
    """数牌花色 0/1/2；字牌返回 None（不参与定缺判定）。"""
    return idx // 9 if 0 <= idx < 27 else None


def _norm(counts: Optional[Sequence[int]]) -> List[int]:
    """把任意长度的计数数组归一成 34 长（不足补 0，多余截断）。"""
    out = [0] * NUM_TYPES
    if not counts:
        return out
    for i in range(min(NUM_TYPES, len(counts))):
        try:
            out[i] = max(0, int(counts[i]))
        except (TypeError, ValueError):
            out[i] = 0
    return out


def standing_hand_count(start_hand_size: int, melds: int) -> int:
    """某家副露后的站立手牌数（上界口径：杠按 3 张/副露扣）。"""
    return max(0, int(start_hand_size) - 3 * int(melds))


def _bounds(unseen: int, capacity: int, wall_remaining: int) -> Tuple[int, int, int, int]:
    """(wall_lo, wall_hi, opp_lo, opp_hi)——唯一一份区间公式。

    build_ledger 与 ledger_after_draw 必须共用它，否则「假想摸一张」后的区间与账面
    区间会因公式漂移而不一致，那种不一致正是「同一个数字两处显示不一样」的病根。
    """
    opp_hi = min(unseen, capacity)
    wall_hi = min(unseen, max(0, wall_remaining))
    return max(0, unseen - opp_hi), wall_hi, max(0, unseen - wall_hi), opp_hi


def build_ledger(
    hand_counts: Sequence[int],
    disc_counts: Optional[Sequence[int]] = None,
    meld_counts: Optional[Sequence[int]] = None,
    *,
    available: Iterable[int],
    laizi: Iterable[int] = (),
    opponents: Optional[Sequence[Tuple[int, Optional[int]]]] = None,
    standings: Optional[Sequence[int]] = None,
    expected_hand_sizes: Optional[Sequence[int]] = None,
    start_hand_size: int = DEFAULT_START_HAND,
) -> Dict:
    """生成牌局账本。

    参数
      hand_counts   我的手牌 34 型计数
      disc_counts   全场牌河 34 型计数（所有人打出的牌）
      meld_counts   全场副露 34 型计数（含我自己的碰杠亮牌，公开可见）
      available     本玩法实际使用的牌型索引集合（不在集合内的型 total=0，
                    绝不能算成「还剩 4 张」——风牌玩法之外的字牌就是这种情况）
      laizi         鬼牌（赖子/百搭/中）索引集合，单独记账
      opponents     每个对手 `(副露数, 定缺门 or None)`；定缺门取 0/1/2，无定缺玩法传 None。
                    不传视为对手无副露且信息未知（容量按满手牌算，保守）
      standings     直接给出每个对手的站立手牌张数（观测量）。引擎侧从副露亮牌数直接
                    得到的是张数而不是「副露个数」，优先用这个，避免重复换算出错；
                    长度必须与 opponents 一致，否则回退到副露数换算
      expected_hand_sizes 本玩法合法的我方站立手牌张数集合（modes.hand_sizes）。
                    传了才会校验「手牌张数与副露是否自洽」；不传就不猜——用
                    13/14 去卡一个已经碰过两家的局面会天天误报脏帧

    返回（全部为 int，除 `ok/violations`）：
      by_type[idx] = {"tile","name","total","seen","unseen",
                      "wall_lo","wall_hi","opp_lo","opp_hi","opp_capacity","is_wild","wall_only"}
      seen_total / unseen_total / standing_total / wall_remaining / rounds_left
      wild = {"seen","unseen","tiles"}
      opp_known = 是否给了对手信息（没有则一律不判「只能自摸」）
      violations = [{"kind","tile","detail"}]，ok = not violations
    """
    hand = _norm(hand_counts)
    disc = _norm(disc_counts)
    meld = _norm(meld_counts)
    avail = {i for i in available if 0 <= i < NUM_TYPES}
    wild = {i for i in laizi if 0 <= i < NUM_TYPES}
    opps: List[Tuple[int, Optional[int]]] = list(opponents) if opponents else []
    # 没有任何对手信息时必须当「未知」，绝不能当「全定缺」：len(opps)==0 若直接
    # 让容量=0，每一型都会算成「一张都不可能在对手手上」，于是全局刷
    # 「只能自摸」——这句话在牌桌上是错的，还会反向把点炮危险牌算成安全牌。
    opp_known = bool(opps)

    by_type: Dict[int, Dict] = {}
    seen_total = unseen_total = 0
    for i in range(NUM_TYPES):
        total = TILES_PER_TYPE if i in avail else 0
        seen = hand[i] + disc[i] + meld[i]
        unseen = max(0, total - seen)
        seen_total += seen
        unseen_total += unseen
        by_type[i] = {
            "tile": idx_to_mpsz(i),
            "name": idx_to_chinese(i),
            "total": total,
            "seen": seen,
            "unseen": unseen,
            "is_wild": i in wild,
        }

    # 对手站立手牌：优先用引擎直接观测到的张数（副露亮牌数是可数死的公开信息）
    if standings is not None and len(standings) == len(opps):
        opp_standing = [max(0, int(s)) for s in standings]
    else:
        opp_standing = [standing_hand_count(start_hand_size, m) for m, _ in opps]
    standing_total = sum(opp_standing)
    # 牌墙剩余是唯一没被看见的堆：未现 − 在别人手上。为负说明账本已经被脏帧污染。
    wall_remaining = unseen_total - standing_total
    players = len(opps) + 1
    rounds_left = max(0, wall_remaining // players) if players > 0 else 0

    violations: List[Dict] = []
    # 对**全 34 型**都算区间（不只看牌集内的）：下游按索引取值时不能遇到 KeyError；
    # 而且「牌集里没有的型却看到了牌」正是需要被抓出来的越界（如川麻识别出风牌）。
    for i in range(NUM_TYPES):
        rec = by_type[i]
        # 只有「没定缺这一门」的对手才可能握着它；全定缺 → 容量 0，区间塌缩。
        # 两个容易写错的边界：① 对手无定缺（dq=None，非川麻玩法）——什么都收，
        # 必须计入容量；② 字牌不属于任何定缺门（suit=None）——定缺万的人也照收字牌。
        # 写成 `dq != suit` 会让 (None, None) 这对判定为假，把无定缺玩法的字牌算成
        # 「一张都不可能在对手手上」，直接错到安全判定与自摸判定上。
        suit_i = tile_suit(i)
        if opp_known:
            capacity = sum(s for s, (_, dq) in zip(opp_standing, opps)
                           if dq is None or suit_i is None or dq != suit_i)
        else:
            capacity = rec["unseen"]          # 未知 = 不排斥任何可能，保守上界
        wall_lo, wall_hi, opp_lo, opp_hi = _bounds(rec["unseen"], capacity, wall_remaining)
        rec.update({
            "wall_lo": wall_lo,
            "wall_hi": wall_hi,
            "opp_lo": opp_lo,
            "opp_hi": opp_hi,
            "opp_capacity": capacity,
            # 只能自摸：对手一张都握不住（或牌墙上界为 0 时根本不成立），
            # 此时这口牌的唯一来源就是牌墙。无对手信息时绝不为真。
            "wall_only": opp_known and opp_hi == 0 and wall_hi > 0,
        })
        if rec["seen"] > rec["total"]:
            if rec["total"] == 0:
                detail = (f"本玩法牌集里没有{rec['name']}（{rec['tile']}），"
                          f"却看到 {rec['seen']} 张——识别或牌池账本越界")
            else:
                detail = (f"{rec['name']} 已见 {rec['seen']} 张，超过该玩法的 "
                          f"{rec['total']} 张实物上限")
            violations.append({
                "kind": "over_four",
                "tile": rec["tile"],
                "detail": detail,
            })

    # 全局可行性：每型都有一部分「对手根本握不住、只能塞进牌墙」，这些需求加起来
    # 不得超过牌墙剩余。超了就不是估算误差，而是可见牌账本 自相矛盾（某处把同一次
    # 弃牌双计、或把不存在的牌读进了牌河）——必须当脏帧标出来。
    min_wall_need = sum(max(0, by_type[i]["unseen"] - by_type[i]["opp_capacity"])
                        for i in sorted(avail))
    if min_wall_need > max(0, wall_remaining):
        violations.append({
            "kind": "wall_overflow",
            "tile": "",
            "detail": (f"至少 {min_wall_need} 张只能待在牌墙，但牌墙只剩 "
                       f"{max(0, wall_remaining)} 张，可见牌账本自相矛盾"),
        })

    if wall_remaining < 0:
        violations.append({
            "kind": "negative_wall",
            "tile": "",
            "detail": (f"未现 {unseen_total} 张 < 对手站立手牌 {standing_total} 张，"
                        f"牌墙为负，可见牌账本已被脏数据污染"),
        })
    # 我的手牌张数必须是本玩法的合法站立张数（由调用方按玩法声明，不在这里猜）
    my_hand = sum(hand)
    if expected_hand_sizes is not None and my_hand and my_hand not in set(expected_hand_sizes):
        violations.append({
            "kind": "hand_size",
            "tile": "",
            "detail": f"手牌 {my_hand} 张不在本玩法合法张数 {sorted(set(expected_hand_sizes))} 内，"
                      f"手牌识别或副露记账不一致",
        })

    wild_seen = sum(by_type[i]["seen"] for i in wild)
    wild_unseen = sum(by_type[i]["unseen"] for i in wild)

    if violations:
        wall_remaining = max(0, wall_remaining)

    return {
        "by_type": by_type,
        "available": sorted(avail),
        "seen_total": seen_total,
        "unseen_total": unseen_total,
        "standing_total": standing_total,
        "wall_remaining": wall_remaining,
        "players": players,
        "rounds_left": rounds_left,
        "my_hand": my_hand,
        "wild": {
            "tiles": [by_type[i]["tile"] for i in sorted(wild)],
            "seen": wild_seen,
            "unseen": wild_unseen,
        },
        "violations": violations,
        "ok": not violations,
        "opp_known": opp_known,
        "expected_hand_sizes": sorted(set(expected_hand_sizes)) if expected_hand_sizes else [],
    }


def ledger_for_sichuan(
    counts28: Sequence[int],
    disc_counts: Optional[Sequence[int]] = None,
    meld_counts: Optional[Sequence[int]] = None,
    *,
    available34: Iterable[int],
    laizi34: Iterable[int] = (),
    opponents: Optional[Sequence[Tuple[int, Optional[int]]]] = None,
    standings: Optional[Sequence[int]] = None,
    expected_hand_sizes: Optional[Sequence[int]] = None,
    start_hand_size: int = DEFAULT_START_HAND,
) -> Dict:
    """川麻 28 型口径 → 账本。

    counts28 的索引 27 是红中，在 34 型体系里是 33；牌河/副露已是 34 型口径，这里只做
    手牌换位，避免调用方各写一份映射（历史上 7z 被丢弃正是这类手工映射出错）。

    available34 是**必填**的：必须由玩法牌集声明本局到底有哪些牌。不传（或猜一个默认值）
    正是本轮反复清理的那类跨层猜测——纯川麻 sc_xz 根本没有红中，若默认把 33 算成 4 张，
    账本会凭空多出 4 张不存在的活牌并抬高牌墙；反过来若默认不含 33，血流红中的红中会被
    误判越界。两种默认都错，所以只能由调用方给出。

    给了错误的牌集也不会静默：counts28[27] > 0 而 33 不在牌集时，build_ledger 会报
    over_four 脏帧（「牌集里没有的牌却看到了」），而不是把红中当不存在。
    """
    hand = [0] * NUM_TYPES
    for i in range(min(27, len(counts28))):
        hand[i] = int(counts28[i])
    if len(counts28) > 27:
        hand[33] = int(counts28[27])          # 红中 7z
    avail = {i for i in available34 if 0 <= i < NUM_TYPES}
    return build_ledger(
        hand, disc_counts, meld_counts,
        available=avail, laizi=laizi34, opponents=opponents, standings=standings,
        expected_hand_sizes=expected_hand_sizes, start_hand_size=start_hand_size)


def ledger_after_draw(ledger: Dict, idx34: int, n: int = 1) -> Dict:
    """返回「从牌墙摸进 n 张该型牌之后」的账本副本（用于预摸牌期望路径）。

    摸牌是把一张牌从牌墙搬进可见域：unseen −n、wall −n、seen +n。不单独走这一步，
    预摸牌场景的文案就会把刚摸进的那一张也当成“还能摸到”，系统性多报一张机会。
    牌不在牌墙（unseen=0）时原样返回，不产生负数。
    """
    rec = ledger["by_type"].get(idx34)
    if rec is None or n <= 0:
        return ledger
    took = min(int(n), rec["unseen"])
    if took <= 0:
        return ledger
    unseen = rec["unseen"] - took
    wall_remaining = max(0, int(ledger["wall_remaining"]) - took)
    wall_lo, wall_hi, opp_lo, opp_hi = _bounds(unseen, rec["opp_capacity"], wall_remaining)
    new_rec = dict(rec)
    new_rec.update({
        "seen": rec["seen"] + took,
        "unseen": unseen,
        "wall_lo": wall_lo, "wall_hi": wall_hi,
        "opp_lo": opp_lo, "opp_hi": opp_hi,
        "wall_only": bool(ledger.get("opp_known", True)) and opp_hi == 0 and wall_hi > 0,
    })
    by_type = dict(ledger["by_type"])
    by_type[idx34] = new_rec
    players = int(ledger.get("players") or 0)
    wild = dict(ledger["wild"])
    if rec["is_wild"]:
        wild["seen"] = wild["seen"] + took
        wild["unseen"] = wild["unseen"] - took
    return {
        **ledger,
        "by_type": by_type,
        "unseen_total": ledger["unseen_total"] - took,
        "wall_remaining": wall_remaining,
        "rounds_left": wall_remaining // players if players > 0 else 0,
        "my_hand": ledger.get("my_hand", 0) + took,
        "wild": wild,
        "drawn_tile": idx_to_mpsz(idx34),
        "drawn_n": took,
    }


def ting_chance(ledger: Dict, wait_idxs: Iterable[int], verb: str = "听") -> Dict:
    """把听口牌汇总成一条**可逐数字追溯**的结论（供 UI 与建议文案共用）。

    total_unseen : 叫口上共余多少张未现（= 旧口径的“进张 N 张”）
    wall_lo_total: 至少多少张确实存在于牌墙（把每型下界加起来，只会保守）
    wall_only    : 多少张**不可能**从对手手上得到（对手已定缺该门）→ 只能自摸
    discardable  : 多少张还可能在对手手上（total_unseen − wall_only）
    rounds_left  : 牌墙还能支撑几轮（每轮每人摸一张）
    dead         : 是否已叫死（四张全部见光）
    """
    by = ledger["by_type"]
    idxs = [i for i in wait_idxs if i in by]
    total = sum(by[i]["unseen"] for i in idxs)
    wall_only = sum(by[i]["unseen"] for i in idxs if by[i]["wall_only"])
    wall_lo_total = sum(by[i]["wall_lo"] for i in idxs)
    return {
        "wait_tiles": [by[i]["tile"] for i in idxs],
        "wait_names": [by[i]["name"] for i in idxs],
        "total_unseen": total,
        "wall_lo_total": wall_lo_total,
        "wall_only": wall_only,
        "discardable": total - wall_only,
        "rounds_left": int(ledger.get("rounds_left", 0)),
        "wall_remaining": int(ledger.get("wall_remaining", 0)),
        "opp_known": bool(ledger.get("opp_known", False)),
        "dead": total == 0,
        "text": describe_opportunity(ledger, idxs, verb=verb),
    }


def describe_opportunity(ledger: Dict, wait_idxs: Iterable[int], verb: str = "听") -> str:
    """把叫口牌按账本拆成人话，只报**可验证事实**，不含任何猜测概率。

    例：「听 3条/5筒 共余 7 张；其中 4 张只在牌墙（该门对手已定缺，只能自摸）；牌墙还能撑 6 轮」
    verb 只改主语（听口用「听」、一向听进张用「进」），数字口径完全相同。
    字段名 wait_* 保留：UI 直接取用，改名会连带改 Dart 侧。
    """
    by = ledger["by_type"]
    idxs = [i for i in wait_idxs if i in by]
    if not idxs:
        return ""
    unseen = sum(by[i]["unseen"] for i in idxs)
    wall_only = sum(by[i]["unseen"] for i in idxs if by[i]["wall_only"])
    maybe_discard = unseen - wall_only
    names = "/".join(by[i]["name"] for i in idxs[:3]) + ("…" if len(idxs) > 3 else "")
    parts = [f"{verb} {names} 共余 {unseen} 张"]
    if unseen == 0:
        parts.append("叫口已绝（四张全部见光），牌墙与对手都摸不到")
        return "；".join(parts)
    if not ledger.get("opp_known", True):
        parts.append("对手信息未知，不拆自摸/点炮来源")
    elif wall_only and maybe_discard == 0:
        parts.append(f"{unseen} 张全在牌墙（对手已定缺该门，只能自摸）")
    elif wall_only:
        parts.append(f"其中 {wall_only} 张只在牌墙（该门对手已定缺，只能自摸）")
    else:
        parts.append(f"{maybe_discard} 张仍可能在对手手上")
    parts.append(f"牌墙还能撑 {ledger.get('rounds_left', 0)} 轮")
    return "；".join(parts)
