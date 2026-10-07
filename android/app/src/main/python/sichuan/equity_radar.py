# -*- coding: utf-8 -*-
"""全场实时胡牌胜率与期望收益雷达 (Win Equity & EV Gauge).

核心量化模型：
1. 实时胡牌胜率 (Win Equity W ∈ [0.0, 1.0])
   - 听牌态 (向听数 0): 基于场上存活胡牌张数 K、剩余牌山未见总数 U、牌局剩余巡目 T，
     推导泊松竞争击中概率并结合对手听牌先制压制，得出模型估值。
   - 一向听 (向听数 1): 结合有效进张面 (Ukeire) 与两巡下叫转化率推导。
   - 二向听及以上: 基于结构步数折减。
2. 期望收益仪表盘 (EV Gauge)
   - 净期望收益 Net EV = P(胡牌) * 期望番数分 - P(点炮) * 点炮失分
   - 综合仪表盘级别 (Extreme / High / Neutral / Risk) 与实战军师洞察。

口径声明（B-P3 概率诚实化，改动前务必先读）
------------------------------------------------
本模块产出的全部是**未标定的模型值**，不是频率意义上的胜率：
- `hit_factor` 的 1.6 倍点炮乘数、`0.45/0.60/0.70` 竞争压制、
  `max(0.05, ...)` / `min(0.65, ...)` 一类的地板与天花板，都是手写常数；
- 从未拿真实对局结果做过标定，所以 `0.81` 只意味着「在本模型里排在 0.81 的位置」，
  **不**意味着「100 局能胡 81 局」。

因此 `evaluate_gauge` 对外只承诺三件事：
1. `level` / `band` / `tier`：档位（相对序），可展示；`tier` 是 band 的三档粗分
   （偏优/中性/偏劣），专供面板做配色与结论位（B-P4 空白 B）；
2. `net_ev`：模型内部评分，单位是「分」不是「番」（`net_ev_unit` 随字段下发）；
3. `insight`：结论句，**只允许出现档位与账本事实，禁止出现任何 `N%`**。

绝对百分比的唯一复活前提是 `probability_bands.CALIBRATED` 被真实翻转（需要标定样本），
不是在这里把字符串改回去。
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Sequence

# 档位口径的单一来源：本模块不自写阈值，只负责「分档」，文案措辞交给
# probability_bands，避免同一个 level 在三处被译成三种说法。
from probability_bands import band_of, danger_band


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
        """计算我方当前手牌的胡牌估值 Win Equity ∈ [0.0, 1.0]。

        **返回值是模型评分，不是标定概率**：下面的地板/天花板（0.05/0.98/0.65/0.30）
        只是防止数值退化成 0 或 1，它们本身就是拍的。调用方可以拿它排序、
        分档，不可以直接当百分比印到面板上（见模块 docstring 的口径声明）。
        """
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
                return 0.0  # 绝张死叫：叫口一张都摸不到，估值归零（这个是账本事实，不是拍的）

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
        basis: str = "analytical",
        facts: Optional[Sequence[str]] = None,
        deal_in_level: Optional[str] = None,
    ) -> Dict:
        """根据胜率估值、期望番数与点炮风险，生成雷达仪表盘结果。

        Args:
            win_equity: `calculate_win_equity` 的输出，**未标定模型值**。
            expected_fan: 期望番数（解析式算出的整数，属事实侧）。
            max_deal_in_prob: 最大点炮概率（同为未标定模型值）。
            basis: 估值来源标记。`"analytical"` = 纯解析式（PVN 未参与）；
                `"analytical+pvn"` = 已叠加策略网络。面板据此判定能不能提「胜率」二字：
                PVN 未训练时两者本应相等，把它标出来主要是让「这个数字从哪来」可查。
            facts: 来自牌局账本的可逐张核对事实短句（如 ting_chance/describe_opportunity
                的 text）。insight 会引用它们，让结论句里有真东西可撑。
            deal_in_level: hand_range 已分好的 `danger_level`，只译成档位，不印百分比。

        旧版此处会写「胜率高达 81%，期望收益 +31.6」——一个未标定数字配一个无单位
        数字，看着精确，用户无从判断真伪。现在一律降为档位 + 事实。
        """
        # 收益分 = 胜率 * (2^番数 * 基础倍数 4)
        gain_points = win_equity * (math.pow(2.0, min(5, expected_fan)) * 4.0)
        # 点炮失分风险 = (1 - 胜率) * 点炮概率 * 惩罚倍数 8
        risk_loss = (1.0 - win_equity) * max_deal_in_prob * 8.0
        net_ev = round(gain_points - risk_loss, 2)

        if win_equity >= 0.70 and net_ev >= 10.0:
            level = "extreme"
            directive = "牌势压在对面头上，不必为防守扣牌。"
        elif win_equity >= 0.45 or net_ev >= 5.0:
            level = "high"
            directive = "维持攻势，优先护住进张面。"
        elif win_equity >= 0.20 and net_ev >= 0.0:
            level = "neutral"
            directive = "局势胶着，进张与防守并重。"
        else:
            level = "risk"
            directive = "先扣生张退避自保，再谈进攻。"

        # 档位、粗分档、标定状态、脚注四个字段同源于 band_of()：不在这里写死
        # calibrated，也不在这里另拟一套“偏优/中性/偏劣”——那是第二个阈值表。
        meta = band_of(level)
        band = meta["band"]
        # B-P4 空白 B：`badge` 从「🔥 极优机会 · 全力冲胡」这类长句收回到三档
        # 粗分词。面板不再渲染进度条，badge 就是那一行的结论位；把行动指令
        # 留在 insight 里（那里有账本依据支撑），不在此处堆修辞。长句里那个
        # “机会”二字会让用户把一个未标定打分当成可靠信号。
        badge = meta["tier"]
        # 可追溯依据：账本事实优先，没有事实就只说档位，绝不编一个百分比充数。
        basis_text = "；".join(str(f).strip() for f in (facts or []) if f and str(f).strip())
        insight = f"{band}（{meta['note']}）"
        if basis_text:
            insight += f"｜依据：{basis_text}"
        if deal_in_level:
            insight += f"｜点炮：{danger_band(deal_in_level)}"
        insight += f"｜{directive}"

        return {
            "win_equity": round(win_equity, 3),
            "net_ev": net_ev,
            # net_ev 是无量纲内部评分（gain_points 与 risk_loss 都被拍扁的常数乘过），
            # 早先面板把它标成「番」，用户会当成「这牌能收几番」来打，那是错的。
            "net_ev_unit": "分",
            "level": level,
            "band": band,
            # `tier` 与 `badge` 同值：前者给面板配色/图标，后者是旧 payload 的兼容位。
            "tier": meta["tier"],
            "badge": badge,
            "insight": insight,
            "expected_fan": expected_fan,
            "risk_loss": round(risk_loss, 2),
            "calibrated": meta["calibrated"],
            "note": meta["note"],
            "equity_basis": basis,
        }
