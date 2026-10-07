# -*- coding: utf-8 -*-
"""概率诚实化：未标定概率的统一降档口径（单一来源）。

背景：本项目有三类数字，长得一样，含义完全不同
------------------------------------------------
1. **事实（fact）**：牌局账本里的计数——已现 X 张、未现 Y 张、牌墙还能撑 N 轮。
   这些是可逐张核对的整数，可以直接显示，但**下界/上界必须写"至少/至多"**。
2. **模型估计（model）**：`win_equity`、`deal_in_prob`、`tenpai_prob`、`top_held.prob`。
   它们由解析式或手工先验算出（logistic 曲线 + `meld_boost=0.20`、
   `p_wait_given_tenpai=0.04/0.08/0.14/0.26`、`likelihood *= 2.2` 一类常数），
   **从未与真实对局结果做过标定**。于是 `0.81` 不等于"81% 会胡"：
   它只代表"在这个模型里这个局面排在 0.81 的位置"。把这种数当百分比印给用户，
   就是本项目最典型的"看似精确实则虚高"。
3. **已标定概率（learned）**：目前没有。任何属于此类的展示都还不存在。

本模块的职责
------------
把第 2 类**统一降为档位 + 相对序**，并把"这是未标定模型值"这件事作为字段随
payload 一起下发，让 Dart 侧不需要（也不允许）自己判断该不该显示百分号。

规则只有一条：**档位边界可以调整（那是产品取向），但绝对百分比不可以恢复，
直到 `CALIBRATED` 被真正翻转为止**——而翻转的前提是拿到与预测同分布的标定样本
（真实对局结果 + 当时的模型输出），不是改一个常量。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

__all__ = ["CALIBRATED", "KIND_FACT", "KIND_MODEL", "KIND_LEARNED", "value_kind",
           "equity_band", "danger_band", "tenpai_band",
           "held_band", "held_kind", "band_note", "band_of", "tier_of",
           "coarse_tier", "danger_advice", "danger_rank", "danger_is_safe",
           "DANGER_LEVEL_ORDER", "DANGER_SAFE_LEVELS", "band_tables"]

# 概率字段的认识论类别：UI 只能对 `fact` 直接印数，`model` 必须走档位。
KIND_FACT = "fact"
KIND_MODEL = "model"
KIND_LEARNED = "learned"

# 唯一的"是否已标定"开关。全项目只有这一处判据；Dart 侧读 payload 里的
# `calibrated` 字段（由本常量写入），不再自己写死 true/false。
CALIBRATED: bool = False

# 胜率档位（与 equity_radar 的 level 同源，不另起一套阈值）
_EQUITY_BAND = {"extreme": "极优", "high": "较优", "neutral": "均势", "risk": "承压"}

# 点炮危险档位：danger_level 已在 hand_range 里分好，这里只负责出中文
_DANGER_BAND = {"safe": "安", "low": "微危", "medium": "中危", "high": "高危",
                "critical": "极危"}

# 危险档位的**次序**（越靠前越安全）。上层要比较「谁更安全」时必须用这个序，
# 不得自己拼字符串大小（那会把「微危 < 高危」当成字典型顺序这种巧合当逻辑）。
DANGER_LEVEL_ORDER: Sequence[str] = ("safe", "low", "medium", "high", "critical")
# 被视为「可以放心打」的档位（B-P4 空白 C：替代方案只从这几档里挑）。
DANGER_SAFE_LEVELS: Sequence[str] = ("safe", "low")


def danger_rank(level: str) -> int:
    """档位→序号（越小越安全）。认不出的一律排到「比极危还差」，不当作安全。"""
    try:
        return DANGER_LEVEL_ORDER.index(str(level or ""))
    except ValueError:
        return len(DANGER_LEVEL_ORDER)


# 对手听牌倾向档位：阈值是取向问题，但表达必须是"档"而不是"%"
_TENPAI_BANDS: Sequence[Tuple[float, str]] = ((0.20, "低"), (0.45, "中"), (0.70, "高"))
_TENPAI_TOP = "极高"

# 对手持牌倾向档位（top_held 的 prob 是「相对后验」，非归一化概率，详见下注释）
_HELD_BANDS: Sequence[Tuple[float, str]] = ((0.35, "低"), (0.60, "中"))
_HELD_TOP = "高"

_HELD_KIND = "相对后验（未归一化，非频率概率）"

# 三档粗分（B-P4 空白 B）：面板需要「一个颜色 + 一句话」的行动倾向，但**不得**
# 为此另写一套阈值。这里只做 `_EQUITY_BAND` 的四档到三档的**投影**（多对一映射），
# 所以极优/较优在档位词上仍有区别（面板文本用 band），而在配色上同属「偏优」。
# 新增 level 时如果忘了登记到这里，coarse_tier 会返回「未定档」而不是猜一个。
_TIER_FROM_LEVEL: Dict[str, str] = {
    "extreme": "偏优",
    "high": "偏优",
    "neutral": "中性",
    "risk": "偏劣",
}

# 危险分级的**行动指令**（B-P4 空白 C）：只有档位词（微危/中危/高危）的话，
# 用户看完仍然不知道该怎么办，所以每个档位必须跟一句可执行的话。
# 措辞集中在本模块：Dart 侧只渲染，另写一份就会两处漂移。
_DANGER_ADVICE: Dict[str, str] = {
    "safe": "绝对安全 · 现物/定缺门，可放心打出",
    "low": "轻微风险 · 当前形势可接受",
    "medium": "中等风险 · 建议优先选低危出张",
    "high": "高危 · 除非已听牌，否则改打安全牌",
    "critical": "极危 · 生张，不是必胡就别打",
}


def _step(value: float, bands: Sequence[Tuple[float, str]], top: str) -> str:
    v = float(value or 0.0)
    for hi, label in bands:
        if v < hi:
            return label
    return top


def equity_band(level: str) -> str:
    """把雷达 level 翻成中文档位（未知 level 一律"未定档"，不猜一个档）。"""
    return _EQUITY_BAND.get(str(level or ""), "未定档")


def danger_band(level: str) -> str:
    """点炮危险档位；`safe` 之外一律用「X危」，避免与「安全」二字混淆。"""
    return _DANGER_BAND.get(str(level or ""), "未定档")


def danger_advice(level: str) -> str:
    """危险档位对应的行动指令（未知档位返回「未定档」，不编一句安慰话）。

    B-P3 把「X% 危」降成档位词后，面板只剩一个描述词，没有回答「那我该不该换牌」。
    本函数就是把那一半补回来：描述 + 动作成对下发，不单独出现。
    """
    return _DANGER_ADVICE.get(str(level or ""), "未定档")


def danger_is_safe(level: str) -> bool:
    """该档位是否属于「可以放心打」（安全/微危）。"""
    return str(level or "") in DANGER_SAFE_LEVELS


def coarse_tier(level: str) -> str:
    """把雷达 level 投影成三档（偏优/中性/偏劣）。

    存在意义：面板的配色/图标需要一个粗粒度分档，但粗分档不得变成第二个
    阈值表（那就有两套牌势口径）。它只读 level，不读 win_equity。
    """
    return _TIER_FROM_LEVEL.get(str(level or ""), "未定档")


def tenpai_band(prob: float) -> str:
    """对手听牌倾向档位（输入是未标定模型值，只用于分档，不可当百分比展示）。"""
    return _step(prob, _TENPAI_BANDS, _TENPAI_TOP)


def held_band(prob: float) -> str:
    """对手持有某张牌的倾向档位。"""
    return _step(prob, _HELD_BANDS, _HELD_TOP)


def held_kind() -> str:
    """top_held.prob 的真实含义，随字段下发，防止 UI 把它当概率印出来。"""
    return _HELD_KIND


def band_note() -> str:
    """给 UI 的一句话脚注：非标定时必须说明数字来历。"""
    if CALIBRATED:
        return "概率已由实战样本标定"
    return "模型推算值·未经实战标定，仅供相对参考"


def value_kind() -> str:
    """当前概率字段属于哪一类量。没有标定样本之前恒为 `model`。

    随 payload 下发是为了让 Dart 不需要（也不允许）自己判断该不该印百分号：
    口径在 Python 侧定死，前端只做渲染。
    """
    return KIND_LEARNED if CALIBRATED else KIND_MODEL


def band_of(level: str) -> Dict:
    """统一打包档位与元信息，供 payload 直接展开。

    `tier` 与 `band` 必须同源：面板拿 tier 配色、拿 band 配文，两个字段如果分叉，
    就会出现「文字说较优、颜色红着」那种口是心非的面板。
    """
    return {
        "band": equity_band(level),
        "tier": coarse_tier(level),
        "calibrated": bool(CALIBRATED),
        "note": band_note(),
    }


def band_tables() -> Dict:
    """档位表只读快照（给跨语言契约测试用）。

    Dart 面板带着一套同名的兜底档位词（旧 payload 没带 band 字段时兜底），两边的
    阈值/措辞必须逐字一致，否则「旧引擎 + 新面板」会静默分叉。公开这个快照，是让
    守卫不必去摸私有常量——摸私有的测试会在重构后悄悄变成空守卫。
    """
    return {
        "equity": dict(_EQUITY_BAND),
        "danger": dict(_DANGER_BAND),
        # 三档投影表与危险行动指令表：Dart 兼容旧 payload 时会自己兜底一套词，
        # 跨语言契约测试逐字比对这两张表，防止“面板一套、引擎一套”。
        "tier": dict(_TIER_FROM_LEVEL),
        "danger_advice": dict(_DANGER_ADVICE),
        # 危险档位次序与安全集：引擎与面板比「谁更安全」只能走这一份定义
        "danger_order": list(DANGER_LEVEL_ORDER),
        "danger_safe_levels": list(DANGER_SAFE_LEVELS),
        # 阶梯表：[阈值升序的 [[阈值, 档位词], ...], 超过最后一个阈值时的顶档]
        "tenpai": [list(_TENPAI_BANDS), _TENPAI_TOP],
        "held": [list(_HELD_BANDS), _HELD_TOP],
        "unknown": "未定档",
    }


def tier_of(value: float, thresholds: List[float], labels: List[str]) -> str:
    """通用阈值分档（供测试与后续玩法复用，顺序必须与 thresholds 对齐）。"""
    assert len(labels) == len(thresholds) + 1, "labels 必须比 thresholds 多一个"
    v = float(value or 0.0)
    for hi, label in zip(thresholds, labels):
        if v < hi:
            return label
    return labels[-1]
