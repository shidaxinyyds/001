# -*- coding: utf-8 -*-
"""四川麻将对手手牌概率透视与危险流向反推引擎 (Bayesian Hand Range Reading & Danger Flow Reverse Inference).

理论体系：
1. 贝叶斯后验概率分布 P(t ∈ Hand_p | Evidence)
   - 先验分布 P_0: 基于全场 108 张物理守恒账本未见剩余牌量与立牌张数
   - 似然度修正 L(Evidence):
     * 定缺门物理硬约束：P(t ∈ DQ_suit) = 0.0
     * 现物 (Genbutsu)：近期弃牌已出，手牌持有概率显著折减
     * 染手/清一色倾斜：若对手弃牌极少打某花色且狂打其他两门，该花色持有概率激增
     * 筋/壁 (Suji/Kabe)：无筋生张与壁牌阻断推断
2. 危险流向反推 (Danger Flow Inference)
   - 听牌概率 P(Tenpai_p) 动态估算：巡目进展 + 鸣牌副露加成 + 晚巡打中张生张突变
   - 各牌点炮风险 D_p(t) = P(Tenpai_p) * P(Wait_p == t | Tenpai)
   - 全场综合危险度 D(t) = 1 - Π (1 - D_p(t))
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

# 口径单一来源：本模块的 tenpai_prob / deal_in_prob / top_held.prob 全部是**手工先验**
# 算出的模型值（下面的 0.28/0.20/0.08/0.04~0.26/2.2 等常数都没拿实战标定过），
# 所以对外只交档位 + 字段类别，由 UI 决定能说到什么程度。
from probability_bands import danger_band, held_band, held_kind, tenpai_band, value_kind

SUIT_M = 0  # 万 0..8
SUIT_P = 1  # 筒 9..17
SUIT_S = 2  # 条 18..26


def tile_to_suit(idx: int) -> int:
    if 0 <= idx <= 8:
        return SUIT_M
    elif 9 <= idx <= 17:
        return SUIT_P
    elif 18 <= idx <= 26:
        return SUIT_S
    return -1


def tile_number(idx: int) -> int:
    return (idx % 9) + 1


def index27_to_chinese(idx: int) -> str:
    if 0 <= idx <= 8:
        return f"{idx + 1}万"
    elif 9 <= idx <= 17:
        return f"{idx - 9 + 1}筒"
    elif 18 <= idx <= 26:
        return f"{idx - 18 + 1}条"
    elif idx == 27:
        return "中"
    return "??"


def index27_to_mpsz(idx: int) -> str:
    if 0 <= idx <= 8:
        return f"{idx + 1}m"
    elif 9 <= idx <= 17:
        return f"{idx - 9 + 1}p"
    elif 18 <= idx <= 26:
        return f"{idx - 18 + 1}s"
    elif idx == 27:
        return "7z"
    return ""


class OpponentState:
    """对手感知状态模型（下家、对家、上家）。"""

    def __init__(
        self,
        seat: int,
        name: str = "",
        dingque_suit: Optional[int] = None,
        discards: Optional[List[int]] = None,
        melds: Optional[List[int]] = None,
        standing_count: int = 13,
    ) -> None:
        self.seat = seat  # 1: 下家, 2: 对家, 3: 上家
        self.name = name or {1: "下家", 2: "对家", 3: "上家"}.get(seat, f"对手{seat}")
        self.dingque_suit = dingque_suit
        self.discards = list(discards) if discards else []
        self.melds = list(melds) if melds else []
        self.standing_count = max(1, min(14, standing_count))

    def estimate_tenpai_probability(self, total_turn: int = 10, turn: Optional[int] = None) -> float:
        """估算该对手的实时听牌倾向 P(Tenpai) ∈ [0.05, 0.95]。

        **这是模型打分，不是频率概率**：下面的 logistic 斜率 0.28、每次鸣牌 +0.20、
        晚巡中张生张 +0.08 都是手写常数，从未用真实对局标定。它可以用来分档
        （低/中/高/极高）与算危险度乘积，不可以当「X% 听牌」直接给用户看。
        """
        # 1. 巡目自然成长曲线 (S型 Logistic)
        effective_turn = turn if turn is not None else (len(self.discards) or total_turn)
        effective_turn = max(1, effective_turn)
        # 巡目节点：第 6 巡约 20%，第 10 巡约 50%，第 14 巡约 80%
        base_logit = 0.28 * (effective_turn - 10.0)
        p_base = 1.0 / (1.0 + math.exp(-base_logit))

        # 2. 副露鸣牌突变加成：每次碰/杠显著缩减手牌并加速成型
        num_melds = len(self.melds) // 3
        meld_boost = num_melds * 0.20

        # 3. 晚巡弃牌特征：后半程连续出中心生张（4/5/6），表明已成型听牌
        late_danger_discards = 0
        if len(self.discards) >= 6:
            for d in self.discards[-3:]:
                if tile_number(d) in (4, 5, 6):
                    late_danger_discards += 1
        discard_boost = late_danger_discards * 0.08

        prob = p_base + meld_boost + discard_boost
        return max(0.05, min(0.95, prob))


class BayesianHandRangeReader:
    """贝叶斯对手手牌透视与全场危险流向核心求解器。"""

    @classmethod
    def calculate_hand_distribution(
        cls,
        opp: OpponentState,
        pool_remaining: List[int],
    ) -> List[float]:
        """求解对手手牌在 27 种牌上的后验概率分布向量 P(t ∈ Hand_opp)。

        返回:
            长度 27 的浮点数数组，每个值为对手持有该牌的相对后验概率 [0.0, 1.0]。
        """
        distribution = [0.0] * 27
        total_unseen = sum(pool_remaining[:27])
        if total_unseen <= 0:
            return distribution

        k = float(opp.standing_count)

        # 统计该对手在三门花色上的弃牌特征
        suit_discards = {SUIT_M: 0, SUIT_P: 0, SUIT_S: 0}
        for d in opp.discards:
            s = tile_to_suit(d)
            if s in suit_discards:
                suit_discards[s] += 1
        total_opp_disc = sum(suit_discards.values())

        for t in range(27):
            rem = pool_remaining[t]
            if rem <= 0:
                distribution[t] = 0.0
                continue

            # 1. 物理先验：超几何分布无放回抽取概率
            p_prior = 1.0 - math.pow(max(0.0, 1.0 - (float(rem) / float(total_unseen))), k)

            # 2. 似然度修正因子 Likelihood
            likelihood = 1.0

            # 2.1 定缺门硬性约束：绝不可能持有定缺花色
            t_suit = tile_to_suit(t)
            if opp.dingque_suit is not None and t_suit == opp.dingque_suit:
                likelihood = 0.0

            # 2.2 现物约束：自己刚打过该牌，持有对子/暗刻的概率大降
            if t in opp.discards:
                likelihood *= 0.15

            # 2.3 花色偏好与清一色染手倾向
            if total_opp_disc >= 4:
                # 若对手狂打另两门而一门未打，极可能在该门做牌/蓄牌
                disc_ratio = float(suit_discards.get(t_suit, 0)) / float(total_opp_disc)
                if disc_ratio < 0.10:
                    likelihood *= 2.2  # 极少打此门 -> 大概率手牌深藏此门
                elif disc_ratio > 0.60:
                    likelihood *= 0.4  # 大量倾倒此门 -> 剩余此门很少

            # 2.4 筋牌关联修正：若对手打过 4，持有 1、7 的几率略有下降
            num = tile_number(t)
            base_idx = t_suit * 9
            if num in (1, 7) and (base_idx + 3) in opp.discards:  # 打过 4
                likelihood *= 0.65
            if num in (2, 8) and (base_idx + 4) in opp.discards:  # 打过 5
                likelihood *= 0.65
            if num in (3, 9) and (base_idx + 5) in opp.discards:  # 打过 6
                likelihood *= 0.65

            distribution[t] = max(0.0, min(1.0, p_prior * likelihood))

        return distribution

    @classmethod
    def evaluate_danger_flow(
        cls,
        candidate_tile: int,
        pool_remaining: List[int],
        opponents: List[OpponentState],
        total_turn: int = 10,
    ) -> Dict:
        """针对指定打牌候选，反推全场点炮风险与危险流向 (Danger Flow)。

        返回:
            {
                "tile": "5p",
                "deal_in_prob": 0.185,
                "danger_level": "medium",  # "safe", "low", "medium", "high", "critical"
                "danger_reason": "对家极度危险 (中张生张)",
                "safest_against": "下家",
                "max_threat_seat": 2,
                "threats": [
                    {"seat": 1, "name": "下家", "prob": 0.0, "reason": "定缺绝对安全"},
                    ...
                ]
            }
        """
        threats = []
        overall_safe = 1.0  # 都不点炮的联合概率
        max_threat_seat = -1
        max_threat_prob = -1.0
        max_threat_reason = "安全牌"

        cand_suit = tile_to_suit(candidate_tile)
        cand_num = tile_number(candidate_tile)
        rem_count = pool_remaining[candidate_tile] if candidate_tile < len(pool_remaining) else 0

        for opp in opponents:
            p_tenpai = opp.estimate_tenpai_probability(total_turn)
            p_deal_in = 0.0
            reason = "常规牌"

            # 1. 绝对安全情形 1：对手定缺此门
            if opp.dingque_suit is not None and cand_suit == opp.dingque_suit:
                p_deal_in = 0.0
                reason = "对手定缺 · 绝对安全"

            # 2. 绝对安全情形 2：对手现物（已弃该牌）
            elif candidate_tile in opp.discards:
                p_deal_in = 0.0
                reason = "对手现物 · 绝对安全"

            # 3. 绝张无牌
            elif rem_count == 0:
                p_deal_in = 0.0
                reason = "场上绝张 · 无点炮可能"

            # 4. 筋牌防护判定
            else:
                base_idx = cand_suit * 9
                is_suji = False
                if cand_num in (1, 7) and (base_idx + 3) in opp.discards:
                    is_suji = True
                elif cand_num in (2, 8) and (base_idx + 4) in opp.discards:
                    is_suji = True
                elif cand_num in (3, 9) and (base_idx + 5) in opp.discards:
                    is_suji = True

                # 4.1 危险度基准按张数和中张/幺九划分
                if is_suji:
                    p_wait_given_tenpai = 0.04
                    reason = "筋牌安全庇护"
                elif cand_num in (1, 9):
                    p_wait_given_tenpai = 0.08
                    reason = "幺九边张牌"
                elif cand_num in (2, 8):
                    p_wait_given_tenpai = 0.14
                    reason = "二八次生张"
                else:
                    # 4, 5, 6 中张生张
                    p_wait_given_tenpai = 0.26
                    reason = "中张中心生张"

                # 4.2 若对手鸣牌较多，点炮概率进一步膨胀
                if len(opp.melds) >= 6:
                    p_wait_given_tenpai *= 1.4

                p_deal_in = min(0.90, p_tenpai * p_wait_given_tenpai)

            overall_safe *= (1.0 - p_deal_in)
            if p_deal_in > max_threat_prob:
                max_threat_prob = p_deal_in
                max_threat_seat = opp.seat
                max_threat_reason = f"{opp.name} ({reason})"

            threats.append({
                "seat": opp.seat,
                "name": opp.name,
                "prob": round(p_deal_in, 3),
                "reason": reason,
            })

        total_deal_in = max(0.0, min(1.0, 1.0 - overall_safe))

        if total_deal_in <= 0.01:
            level = "safe"
            summary_reason = "全场安目 (定缺/现物)"
        elif total_deal_in <= 0.08:
            level = "low"
            summary_reason = f"安全偏低风险 ({max_threat_reason})"
        elif total_deal_in <= 0.20:
            level = "medium"
            summary_reason = f"注意防范 ({max_threat_reason})"
        elif total_deal_in <= 0.40:
            level = "high"
            summary_reason = f"高度警惕 ({max_threat_reason})"
        else:
            level = "critical"
            summary_reason = f"极度危险点炮警戒 ({max_threat_reason})"

        return {
            "tile": index27_to_mpsz(candidate_tile),
            "tile_idx": candidate_tile,
            "deal_in_prob": round(total_deal_in, 3),
            "danger_level": level,
            # 档位与字段类别：面板拿 `danger_band` 渲染，不再自己把 deal_in_prob
            # 乘 100 拼成「X% 危」（未标定数字披百分号就是 B-P3 要消除的伪精确）。
            "danger_band": danger_band(level),
            "prob_kind": value_kind(),
            "danger_reason": summary_reason,
            "max_threat_seat": max_threat_seat,
            "threats": threats,
        }

    @classmethod
    def get_hand_ranges_summary(
        cls,
        opponents: List[OpponentState],
        pool_remaining: List[int],
    ) -> List[Dict]:
        """导出全场各对手手牌透视高概率持牌摘要 (供 UI 浮窗与雷达透视直接消费)。"""
        summaries = []
        for opp in opponents:
            dist = cls.calculate_hand_distribution(opp, pool_remaining)
            tenpai_p = opp.estimate_tenpai_probability()

            # 找出该对手持有概率最高的 Top 3 牌
            ranked = sorted(enumerate(dist), key=lambda x: -x[1])
            top_held = []
            for t_idx, prob in ranked[:3]:
                if prob > 0.05:
                    top_held.append({
                        "tile": index27_to_mpsz(t_idx),
                        "chinese": index27_to_chinese(t_idx),
                        "prob": round(prob, 2),
                        # prob 是 `p_prior * likelihood`，likelihood 可被染手倾斜乘到 2.2，
                        # 整个分布**从未归一化**：它只能比大小，不是「有 68% 拿这张」。
                        # 所以随字段给出档位与含义，不给百分比。
                        "band": held_band(prob),
                    })

            dq_name = {SUIT_M: "万", SUIT_P: "筒", SUIT_S: "条"}.get(opp.dingque_suit, "未定")

            summaries.append({
                "seat": opp.seat,
                "name": opp.name,
                "dingque": opp.dingque_suit,
                "dingque_name": dq_name,
                "standing": opp.standing_count,
                "tenpai_prob": round(tenpai_p, 2),
                "tenpai_band": tenpai_band(tenpai_p),
                "top_held": top_held,
                "discards_count": len(opp.discards),
                # 字段含义随 payload 下发，避免前端把相对后验当频率概率印成百分号。
                "prob_kind": value_kind(),
                "held_prob_kind": held_kind(),
            })
        return summaries
