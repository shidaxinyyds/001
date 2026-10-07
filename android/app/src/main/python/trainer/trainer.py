from typing import Dict, List, Optional

from .objects.tile import Tile
from .objects.tile_collection import TileCollection
from .utils.shanten import calculate_shanten
from .utils.ukeire import calculate_ukeire_ex, calculate_discards_info
from .utils.convert import tile_to_chinese
from modes import (DEFAULT_MODE, available_set, get_analyzer, get_laizi_set,
                   get_mode, hand_sizes, is_sichuan_family)
from tile_ledger import build_ledger, ledger_for_sichuan

class Trainer:
    def __init__(self, hand: TileCollection, mode: str = DEFAULT_MODE):
        self.hand = hand
        self.mode = mode
        # 该玩法可用牌的 34 型索引集合（二/三麻去掉的牌不计入）
        self.available: set = available_set(mode)
        # 「已可见但不在自己手牌里」的牌计数（长度 34）。
        # disc_counts = 牌河（所有玩家打出的牌）；meld_counts = 副露（吃碰杠）。
        # 由引擎每帧根据识别结果刷新，用于绝张扣减。
        self.disc_counts: List[int] = [0] * 34
        self.meld_counts: List[int] = [0] * 34
        self.dingque_suit: Optional[int] = None
        self.opponent_dingque_suits: List[int] = []
        self.opponents: List = []

        self.sichuan_results: List[Dict] = []
        self.general_results: List[Dict] = []
        self.std_results: List[Dict] = []
        # 牌局账本：每类牌的「已现 / 未现 / 牌墙可摸 / 对手手上(上界) / 鬼牌剩余」。
        # 由 calculate_discards() 顺手生成，engine 读它做脏帧硬门与听口结论下发。
        self.ledger: Optional[Dict] = None
        # 账本生成失败的原因（None = 成功）。降级可以发生，但绝不允许静默发生：
        # 否则“升级被吞掉”与“本来就没账本”两种状态在下游看起来一模一样。
        self.ledger_error: Optional[str] = None
        # 玩法引擎路由："sichuan" / "std" / ""（历史回退）
        self.analyzer: str = get_analyzer(mode)
        self.rules: Dict = get_mode(mode)

    def set_dingque(self, suit: Optional[int]) -> None:
        """设置四川麻将定缺门（0=万, 1=筒, 2=条）。"""
        self.dingque_suit = suit

    def set_opponent_dingque(self, suits: List[int]) -> None:
        """设置对手定缺门列表，供防点炮雷达扣减危险与安全加分。"""
        self.opponent_dingque_suits = list(suits) if suits else []

    def set_opponents(self, opponents: List) -> None:
        """设置三位对手的实时状态模型（含弃牌、副露、定缺及听牌率），用于贝叶斯透视与收益雷达。"""
        self.opponents = list(opponents) if opponents else []

    def set_visible(self, disc_counts: List[int], meld_counts: List[int]) -> None:
        """引擎在每帧识别后调用：传入当前牌河 / 副露计数，供进张计算扣减绝张。"""
        if disc_counts is not None:
            self.disc_counts = list(disc_counts)
        if meld_counts is not None:
            self.meld_counts = list(meld_counts)

    def get_shanten(self):
        if self.analyzer == "std":
            try:
                from std import StdAnalyzer
                return StdAnalyzer.calculate_shanten(
                    list(self.hand.tiles34), 0, self.rules)
            except Exception:
                pass
        if is_sichuan_family(self.mode):
            try:
                from sichuan import SichuanAnalyzer
                hand_mpsz = "".join(str(t) for t in self.hand.all)
                hand_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz)
                counts = SichuanAnalyzer.counts_from_tiles(hand_indices)
                return SichuanAnalyzer.calculate_shanten(counts)
            except Exception:
                pass
        return calculate_shanten(self.hand)

    def _ledger_opponents(self):
        """把对手信息折算成账本入参：opponents=[(副露数, 定缺门)] + standings=[站立张数]。

        引擎侧只对川麻家族构造 OpponentState；其余玩法没有逐家记账，但牌局人数是玩法
        常量，于是按「每家满手、无定缺」合成：副露亮牌已计入 seen，拿满手当上界只会
        高估别人手上的牌、低估牌墙——方向上是“少报还能摸到”，不会反过来谎报机会。
        返回 None 表示连人数都不可用（账本会因此不判“只能自摸”）。
        """
        if self.opponents:
            opps, standings = [], []
            for o in self.opponents:
                # standings 优先：OpponentState.melds 是平铺的亮牌张数而不是副露个数，
                # 拿 len(melds) 去走 13-3m 公式会把副露多扣两倍。
                opps.append((0, getattr(o, "dingque_suit", None)))
                standings.append(int(getattr(o, "standing_count", 13)))
            return opps, standings
        players = int(self.rules.get("players") or 0)
        if players > 1:
            return [(0, None)] * (players - 1), None
        return None

    def _make_ledger(self, counts: List[int], is_sichuan: bool) -> Optional[Dict]:
        """生成本帧牌局账本。失败不阻断建议，但必须**留痕**（self.ledger_error）。

        这里原先是 `except Exception: return None`：川麻分支因形参名写错抛 TypeError，
        被静默吞掉后表现为“账本永远为空”，而建议照旧、测试照绿——建议文案停留在旧
        口径却没人知道。留痕后引擎与离线测试都能直接断言“降级没有发生过”。
        """
        common = dict(
            opponents=None,
            standings=None,
            expected_hand_sizes=hand_sizes(self.mode),
        )
        # 鬼牌集统一是 34 型口径，但两个入口的形参名不同，只能分支传。
        lz = sorted(get_laizi_set(self.mode))
        opp = self._ledger_opponents()
        if opp:
            common["opponents"], common["standings"] = opp[0], opp[1]
        try:
            if is_sichuan:
                # 牌河/副露已是 34 型口径，手牌是 28 型：换位集中在 ledger_for_sichuan。
                # 它的形参名是 available34/laizi34（强调“必须给 34 型口径”），与
                # build_ledger 的 available/laizi 不同名，拼错就是拼错，不做兼容别名。
                led = ledger_for_sichuan(list(counts), self.disc_counts, self.meld_counts,
                                         available34=sorted(self.available),
                                         laizi34=lz, **common)
            else:
                led = build_ledger(list(counts), self.disc_counts, self.meld_counts,
                                   available=sorted(self.available), laizi=lz, **common)
            # 签名：引擎拿它判定账本是不是本帧的。上一帧的违例不能拿来门控本帧，
            # 否则“偶尔少识一张”会被放大成连续几秒不给建议。用 34 型口径的可见量
            # + 手牌张数（28/34 两种口径下张数相同），不用原始 counts 数组。
            if isinstance(led, dict):
                led["sig"] = (tuple(self.disc_counts), tuple(self.meld_counts),
                              sum(int(c) for c in counts))
            self.ledger_error = None
            return led
        except Exception as exc:  # noqa: BLE001 - 记账失败只能降级，但必须可观测
            self.ledger_error = f"{type(exc).__name__}: {exc}"
            return None

    def calculate_discards(self) -> Dict[Tile, int]:
        """返回 {候选弃牌: 进张数}，进张已按绝张扣减（见 calculate_ukeire_ex）。"""
        if self.analyzer == "std":
            try:
                from std import StdAnalyzer
                counts = list(self.hand.tiles34)
                # 牌池物理守恒（34 型）：扣除手牌 + 牌河 + 副露可见量
                pool_remaining = [0] * 34
                for i in range(34):
                    vis = counts[i]
                    if i < len(self.disc_counts):
                        vis += self.disc_counts[i]
                    if i < len(self.meld_counts):
                        vis += self.meld_counts[i]
                    pool_remaining[i] = max(0, 4 - vis)
                self.ledger = self._make_ledger(counts, is_sichuan=False)
                self.std_results = StdAnalyzer.analyze_discards(
                    counts, self.rules, sorted(self.available),
                    pool_remaining=pool_remaining, fixed_melds=0,
                    ledger=self.ledger,
                )
                res: Dict[Tile, int] = {}
                for item in self.std_results:
                    res[Tile(item["tile"])] = item["ukeire"]
                return res
            except Exception:
                pass
        if is_sichuan_family(self.mode):
            try:
                from sichuan import SichuanAnalyzer
                from sichuan.sichuan_analyzer import pool_remaining_from_visible
                hand_mpsz = "".join(str(t) for t in self.hand.all)
                hand_indices = SichuanAnalyzer.parse_hand_mpsz(hand_mpsz)
                counts = SichuanAnalyzer.counts_from_tiles(hand_indices)
                # 牌池物理守恒：真实扣减全场公开可见牌（支持 28 型，含 7z 红中）；
                # 与 engine 兜底路径共用同一守恒函数，口径统一。
                pool_remaining = pool_remaining_from_visible(
                    counts, self.disc_counts, self.meld_counts)

                self.ledger = self._make_ledger(counts, is_sichuan=True)
                self.sichuan_results = SichuanAnalyzer.analyze_discards(
                    counts,
                    pool_remaining=pool_remaining,
                    dingque_suit=self.dingque_suit,
                    opponent_dingque_suits=self.opponent_dingque_suits,
                    opponents=self.opponents,
                    ledger=self.ledger,
                )
                res = {}
                for item in self.sichuan_results:
                    res[Tile(item["tile"])] = item["ukeire"]
                return res
            except Exception:
                pass


        self.general_results = calculate_discards_info(
            self.hand, self.available, self.disc_counts, self.meld_counts
        )
        return {item["tile"]: item["ukeire"] for item in self.general_results}


    def discard(self, tile: Tile) -> str:
        valid_discards = self.calculate_discards()

        best_discards = []
        best_ukeire = 0
        for discard, ukeire in valid_discards.items():
            if ukeire > best_ukeire:
                best_discards = [discard]
                best_ukeire = ukeire
            elif ukeire == best_ukeire:
                best_discards.append(discard)

        tile_cn = tile_to_chinese(str(tile))
        best_cn = '、'.join(tile_to_chinese(str(e)) for e in best_discards)

        # “可进张 N 张”里的 N 是**未现牌计数**（牌墙 + 对手手上），不是真能摸到的张数。
        # 比较两个打法仍同口径（所以“谁更优”的结论不变），但写给用户就得到处：
        # 标「至多」才能说清它是个上界（B-P3 区间 vs 点估计规则）。
        if tile not in valid_discards:
            message = (
                f"你打出了{tile_cn}，这会导致向听数增加！" + '\n'
                "你离听牌更远了。")
        elif valid_discards[tile] != best_ukeire:
            message = (
                f"你打出了{tile_cn}，此时进张至多 {valid_discards[tile]} 张（未现数，含对手手上）。" + '\n'
                f"最高效的打法是 {best_cn}，进张至多 {best_ukeire} 张。")
        else:
            alternatives = best_discards.copy()
            alternatives.remove(tile)
            message = (
                f"你打出了{tile_cn}，此时进张至多 {valid_discards[tile]} 张（未现数，含对手手上）。" + '\n'
                "这是当前最优的选择！")

            if len(best_discards) > 1:
                message += '\n' + f"其他同等高效的打法：{('、'.join(tile_to_chinese(str(e)) for e in alternatives))}"

        self.hand = self.hand.remove_tile(tile)
        return message

    def draw(self, tile: Tile) -> str:
        self.hand = self.hand.add_tile(tile)
        return f"你摸到了 {tile_to_chinese(str(tile))}。"
