# -*- coding: utf-8 -*-
"""全场实时胡牌胜率与期望收益雷达 (Win Equity & EV Gauge).

核心量化模型：
1. 实时胡牌胜率 (Win Equity W ∈ [0.0, 1.0])
   - 听牌态 (向听数 0): 基于场上存活胡牌张数 K、剩余牌山未见总数 U、牌局剩余巡目 T，
     推导泊松竞争击中概率并结合对手听牌先制压制，得出可信真实胜率。
   - 一向听 (向听数 1): 结合有效进张面 (Ukeire) 与两巡下叫转化率推导。
   - 二向听及以上: 基于结构步数折减。
2. 期望收益仪表盘 (EV Gauge)
   - 净期望收益 Net EV = P(胡牌) * 期望番数分 - P(点炮) * 点炮失分
   - 综合仪表盘级别 (Extreme / High / Neutral / Risk) 与实战军师洞察。
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional


class WinEquityGauge:
    """胜率与期望收益综合评估雷达。"""

    @classmethod
    def calculate_win_equity(
        cls,
        shanten: int,
        waiting_dict: Optional[Dict[int, int]],
        incoming_ukeire: int,
        pool_remaining: List[int],
        opponents_tenpai_probs: Optional[List[float]] = None,
    ) -> float:
        """计算我方当前手牌真实胡牌胜率 Win Equity ∈ [0.0, 1.0]。"""
        total_unseen = sum(pool_remaining[:27]) if len(pool_remaining) >= 27 else 50
        # 预估全场剩余摸牌巡数 (每人摸一张，牌山约占 total_unseen / 4)
        turns_left = max(1, min(18, (total_unseen - 8) // 4))

        # 对手总听牌压制因子
        p_opp_competing = 0.0
        if opponents_tenpai_probs:
            p_no_opp_win = 1.0
            for p_t in opponents_tenpai_probs:
                # 对手若听牌，在剩余巡目内自摸或胡牌的概率
                p_opp_win = p_t * (1.0 - math.pow(max(0.0, 1.0 - 4.0 / max(10, total_unseen)), turns_left))
                p_no_opp_win *= (1.0 - min(0.9, p_opp_win))
            p_opp_competing = 1.0 - p_no_opp_win

        if shanten == 0:
            # ===== 听牌态 (向听数 0) =====
            total_outs = sum(waiting_dict.values()) if waiting_dict else 0
            if total_outs <= 0:
                return 0.0  # 绝张死叫，真实胜率为 0

            # 击中存活牌的自然概率 (结合自摸与对手打出)
            # 自摸率 ≈ 1 - (1 - K/U)^T; 加上对手点炮乘数 (约 1.6 倍)
            hit_factor = min(0.98, float(total_outs) * 1.6 / max(1.0, float(total_unseen)))
            p_self_win = 1.0 - math.pow(max(0.0, 1.0 - hit_factor), turns_left)

            # 受到对手竞争击中扣减
            equity = p_self_win * (1.0 - 0.45 * p_opp_competing)
            return max(0.05, min(0.98, equity))

        elif shanten == 1:
            # ===== 一向听 (向听数 1) =====
            # 先进张下叫，再下叫胡牌的两阶段复合概率
            u_in = max(1, incoming_ukeire)
            p_to_tenpai = 1.0 - math.pow(max(0.0, 1.0 - float(u_in) / max(1.0, float(total_unseen))), max(1, turns_left // 2))
            # 下叫后平均进张约 4 张
            p_after_tenpai = 1.0 - math.pow(max(0.0, 1.0 - 4.0 * 1.5 / max(1.0, float(total_unseen))), max(1, turns_left // 2))

            equity = p_to_tenpai * p_after_tenpai * (1.0 - 0.60 * p_opp_competing)
            return max(0.02, min(0.65, equity))

        else:
            # ===== 两向听及以上 =====
            base_eq = 0.25 / float(max(2, shanten))
            equity = base_eq * (1.0 - 0.70 * p_opp_competing)
            return max(0.01, min(0.30, equity))

    @classmethod
    def evaluate_gauge(
        cls,
        win_equity: float,
        expected_fan: int = 1,
        max_deal_in_prob: float = 0.05,
    ) -> Dict:
        """根据胜率、期望番数与点炮风险，生成雷达仪表盘量化结果。"""
        # 收益分 = 胜率 * (2^番数 * 基础倍数 4)
        gain_points = win_equity * (math.pow(2.0, min(5, expected_fan)) * 4.0)
        # 点炮失分风险 = (1 - 胜率) * 点炮概率 * 惩罚倍数 8
        risk_loss = (1.0 - win_equity) * max_deal_in_prob * 8.0
        net_ev = round(gain_points - risk_loss, 2)

        win_rate_percent = int(round(win_equity * 100))

        if win_equity >= 0.70 and net_ev >= 10.0:
            level = "extreme"
            badge = "🔥 绝对胜势 · 全力锁定"
            insight = f"胜率高达 {win_rate_percent}%，期望收益 +{net_ev}，牌势处于绝对顶峰，全力冲刺胡牌！"
        elif win_equity >= 0.45 or net_ev >= 5.0:
            level = "high"
            badge = "⚡ 优势主导 · 积极进攻"
            insight = f"胜率 {win_rate_percent}%，期望收益 +{net_ev}，牌面宽广，维持攻势稳步推进。"
        elif win_equity >= 0.20 and net_ev >= 0.0:
            level = "neutral"
            badge = "⚖️ 均势博弈 · 见机而动"
            insight = f"胜率 {win_rate_percent}%，局势处于胶着期，兼顾进张与防守。"
        else:
            level = "risk"
            badge = "🛡️ 逆风承压 · 防守优先"
            insight = f"胜率仅 {win_rate_percent}% 且点炮风险高，建议扣下生张，退避自保防点炮。"

        return {
            "win_equity": round(win_equity, 3),
            "win_rate": win_rate_percent,
            "net_ev": net_ev,
            "level": level,
            "badge": badge,
            "insight": insight,
            "expected_fan": expected_fan,
            "risk_loss": round(risk_loss, 2),
        }
