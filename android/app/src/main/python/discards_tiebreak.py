# -*- coding: utf-8 -*-
"""同分牌理裁决（Tie-Breaking）：主键相等时按**确定性优先级链**决定出牌先后。

为什么需要它
------------
两个 analyzer 的主排序键是 EV（川麻甚至只有 `ev` 单键，std 是 `(-ev, -ukeire)`）。
EV 落库前统一 `round(x, 1)`，所以「同分」不是理论边界而是**高频事件**：两张牌的
EV 与进张都相等时，谁排第一过去完全由候选枚举顺序（牌的索引先后：万→筒→条→字）
决定。那等于让「牌面是第几类」决定打哪张，与牌理无关；PVN 摘权之后策略头不再
参与，这个偏差就成了唯一的隐性随机源（用户会感觉「同分永远先打万」）。

决胜链（越小越优；元组逐层比较，任何一层分出名次即停止）
--------------------------------------------------------
  0  期望值      -ev              主键本身，保留原语义，不重新定义价值模型。**它必须
                                  是第一层**：本模块的职责是「同分时裁决」，任何写在
                                  EV 之前的层都会改掉非同分的主推牌，那是改牌理模型
                                  而不是裁决（实测 373/400 帧含缺门候选且旧主键不变，
                                  说明两者同向；把 EV 放第一层是让这一点由构造保证）。
  1  定缺门优先  is_dingque       同 EV 时缺门牌先处理（川麻必断门才能胡）。std 恒 False
                                  故该层恒平。知识库的【定缺绝杀】boost 已从 EV 表达
                                  同一意图，两者同向，不会互相抵消。
  2  进张数      -ukeire          同 EV 下选进张更宽的（更稳的听牌路径）
  3  听口面数    -wait_count      同进张总数下选叫口**种类**更多的（一门 3 张 vs
                                  三门各 1 张：口多者更难被一次弃牌打掉）
  4  牌墙可摸    -wall_lo_total   账本给出的「至少这么多张确实还在牌墙」，越大越
                                  容易自摸；无账本时恒 0，该层自动不起作用
  5  防守安全    danger           进攻收益持平时打点炮风险更低的（升序，越小越优）
  6  牌面弹性    flex_rank        仍全等时优先打改良价值低的牌：字牌(0) < 幺九(1)
                                  < 中张(2)。这是纯牌面属性，不引入任何新估算量
  7  索引兜底    idx34            前 7 层全等时的最终确定性来源。它必须存在：没有
                                  它，"全等"就退化成依赖列表构造顺序（改一处枚举
                                  实现就可能变结果）。兜底后同一输入永远同一输出

诚实边界（不要当成已实现）
--------------------------
* std 家族目前没有危险度模型，第 5 层在 std 下恒平（不是猜一个 0 来制造差异）。
* 第 3/4 层只在听牌（有叫口/账本）时提供信息；未听牌时两者皆 0，由第 2 层决定。
* 本模块**不**参与 EV 计算，只决定同分先后，因此不影响胜率显示与账本守恒校验。

所有排序点必须走这里：`sichuan/sichuan_analyzer.py`（14 张与 13 张两条路径）、
`std/std_analyzer.py`、`knowledge_base.py`（战术加权后重排）。漏掉任何一处，就会
出现「analyzer 裁决完、上层又按另一套键重排」的两处口径打架。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

# 档位措辞只允许有一个源头（probability_bands 是叶子模块，不依赖任何层，
# 顶层 import 不会把循环依赖引进决策链）。
from probability_bands import danger_band

__all__ = ["tile_flex_rank", "tie_tail", "order_key", "sort_discards",
           "first_diff_layer", "advantage_note"]

# 牌面弹性：数牌 1/9 与字牌难以参与顺子，做搭子的改良空间最小。
_FLEX_HONOR = 0
_FLEX_TERMINAL = 1
_FLEX_SIMPLE = 2

_SUIT_CHARS = ("m", "p", "s")


def _mpsz_to_idx34(mpsz: str) -> int:
    """mpsz → 34 型索引（与 tile_ledger.idx_to_mpsz 互逆）。解析失败返回 -1。

    自带一份解析而不是 import trainer/engine：本模块被两个 analyzer 与知识库共用，
    依赖任何一层都会把循环 import 引进决策链。
    """
    t = str(mpsz or "").strip()
    if len(t) != 2 or not t[0].isdigit() or t[1] not in _SUIT_CHARS + ("z",):
        return -1
    n = int(t[0])
    if t[1] == "z":
        return 27 + (n - 1) if 1 <= n <= 7 else -1
    if not 1 <= n <= 9:
        return -1
    return _SUIT_CHARS.index(t[1]) * 9 + (n - 1)


def tile_flex_rank(mpsz: str) -> int:
    """牌面改良弹性等级：字牌 0 < 幺九 1 < 中张 2（越小越该先打）。"""
    idx = _mpsz_to_idx34(mpsz)
    if idx < 0:
        return _FLEX_SIMPLE          # 认不出的一律当中张，不因解析失败抢先后
    if idx >= 27:
        return _FLEX_HONOR
    n = idx % 9 + 1
    return _FLEX_TERMINAL if n in (1, 9) else _FLEX_SIMPLE


def _num(item: Dict, key: str, flow_key: str = "", default: float = 0.0) -> float:
    """安全取数：缺失/None/非数一律 default，绝不让这些值进元组比较。

    为什么不让它报：排序一旦抛异常，外层 `except Exception: pass` 会把整条
    EV/危险度链静默降级回通用路径（用户看到的是“建议变笨了”而不是报错），
    是本项目最难查的那类错。脏数据只能当“该层无信息”，不能炸。
    """
    v = item.get(key)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if flow_key:
        flow = item.get("danger_flow")
        if isinstance(flow, dict):
            fv = flow.get(flow_key)
            if isinstance(fv, (int, float)) and not isinstance(fv, bool):
                return float(fv)
    return default


def tie_tail(item: Dict) -> Tuple:
    """决胜链第 2 层往后的元组后缀（越小越优）。

    单独暴露它，是为了让「双策略路线」那种**主键不同**的排序（极速流按向听/进张、
    大番流按 EV）也能复用同一套 tie 裁决，而不用把整条 order_key 塞进去打架。
    """
    chance = item.get("ting_chance")
    wall_lo = 0.0
    if isinstance(chance, dict):
        wv = chance.get("wall_lo_total")
        if isinstance(wv, (int, float)) and not isinstance(wv, bool):
            wall_lo = float(wv)
    waits = item.get("ting_tiles") or []
    # 进张取数走 _num：历史上写过一版 int(item.get(...) or 0)，遇到字符串直接 ValueError，
    # 而异常会被上层吞掉并静默降级——必须把脏数据归为“该层无信息”而不是抛。
    ukeire = int(_num(item, "ukeire"))
    return (
        -ukeire,                              # 2 进张多
        -len(waits) if isinstance(waits, (list, tuple)) else 0,   # 3 叫口种类多
        -wall_lo,                               # 4 牌墙真能摸到
        _num(item, "danger_penalty", "deal_in_prob"),             # 5 打出去更安全
        tile_flex_rank(str(item.get("tile") or "")),              # 6 先打改良价值低的
        _mpsz_to_idx34(str(item.get("tile") or "")),              # 7 确定性兜底
    )


def order_key(item: Dict) -> Tuple:
    """完整排序键（越小越优）。所有出牌候选排序点都应 `sorted(xs, key=order_key)`。

    EV 在前、定缺在后：只在同分时裁决，绝不越权改掉非同分的主推牌。
    """
    return (
        -_num(item, "ev"),                         # 0 主键：期望值
        0.0 if item.get("is_dingque") else 1.0,    # 1 缺门牌先处理
    ) + tie_tail(item)


def sort_discards(results: Sequence[Dict]) -> List[Dict]:
    """按决胜链排序并返回新列表（不改原列表，调用方可能还在按原顺序做诊断）。"""
    return sorted(results, key=order_key)


# 供诊断/测试用：名字与层一一对应，"哪一层把它们分开了"必须能被解释出来。
_LAYERS = ("EV", "定缺门", "进张数", "听口面数", "牌墙可摸", "防守安全", "牌面弹性", "索引兜底")


def first_diff_layer(a: Dict, b: Dict) -> str:
    """返回第一个能分出 a、b 先后的层名（全等则返回「完全同序」）。

    存在意义：决胜层最容易坏在「写了但永远不生效」（上一层已分出名次）。测试与端上
    诊断都靠它确认指定层**确实**是决定性的一层，而不是空转的装饰。
    """
    ka, kb = order_key(a), order_key(b)
    for i, (x, y) in enumerate(zip(ka, kb)):
        if x != y:
            return _LAYERS[i] if i < len(_LAYERS) else f"layer{i}"
    return "完全同序"


_FLEX_NAME = {_FLEX_HONOR: "字牌", _FLEX_TERMINAL: "幺九张", _FLEX_SIMPLE: "中张"}


def _wall_lo(item: Dict) -> float:
    """账本「至少在牌墙」张数（听口与进张两条路径都可能带）。"""
    for key in ("ting_chance", "ukeire_chance"):
        blk = item.get(key)
        if isinstance(blk, dict):
            v = blk.get("wall_lo_total")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return 0.0


def _danger_word(item: Dict) -> str:
    flow = item.get("danger_flow")
    if isinstance(flow, dict):
        band = flow.get("danger_band")
        if band:
            return str(band)
        level = flow.get("danger_level")
        if level:
            return danger_band(str(level))
    return "未定档"


def advantage_note(a: Dict, b: Dict) -> Dict:
    """说清「打 {a.tile} 为什么排在打 {b.tile} 前面」。

    为什么不报 EV 差值（B-P4 空白 A 的设计约束）
    -----------------------------------------
    `ev` 是 `-1000*向听 + 进张 + 番数权重` 一类的**合成评分**（实测同帧候选差常见
    1000/20/9 这种量级），单位既不是番也不是张。把它写成「优于对方 1.2 分」就是
    B-P3 刚消除的伪量纲：看着精确，实际不可核对。本函数只说**能从账本/牌面
    逐张核对的差额**（向听、未现上界、叫口门数、牌墙可摸、点炮档位、牌面弹性），
    事实说不出时就诚实承认「模型评分更高（未标定）」；而主键与所有层都全等时，
    结论就是「两张等价」——这比硬造一个不存在的优势对用户有用得多。

    返回：`{layer, note, kind, equivalent}`；kind ∈ {fact, model}。
    """
    layer = first_diff_layer(a, b)
    ta = str(a.get("tile") or "")
    tb = str(b.get("tile") or "")
    ea = _num(a, "ev")
    eb = _num(b, "ev")
    sa = int(_num(a, "shanten"))
    sb = int(_num(b, "shanten"))
    ua = int(_num(a, "ukeire"))
    ub = int(_num(b, "ukeire"))
    wa = len(a.get("ting_tiles") or [])
    wb = len(b.get("ting_tiles") or [])

    def _eq(note: str) -> Dict:
        return {"layer": layer, "note": note, "kind": "fact", "equivalent": True}

    # 两句等价文案都不再自己点名被比较的那张牌：调用方（engine 的
    # `advantage_reason` / 双策略卡的 `main_compare`）已经用「与次选 X 等价：」这种
    # 前缀把牌名说过一遍，note 里再说“与打X”就是冗余（面板只有一行宽）。
    if layer == "完全同序":
        return _eq("牌理、进张、安全全同，打哪张都不亏")
    if layer == "索引兜底":
        return _eq("牌理与安全全等，仅按固定顺序列前（无牌理依据）")

    # 主键（EV）先分出名次的层：EV 本身不可解释，必须先去找它背后的事实。
    if layer == "EV" and ea < eb:
        # 排在前面却评分更低：名次不是评分给的，而是上层规则（主推取首/阶段固定
        # 次序）给的。这时候写「评分更高」就是假的，必须直接说出它俩的评分关系。
        return {"layer": layer, "kind": "model", "equivalent": False,
                "note": f"列在前面不是因为有评分优势（本牌评分低于{tb}）"}
    if sa < sb:
        return {"layer": layer, "kind": "fact", "equivalent": False,
                "note": f"比打{tb}少 {sb - sa} 向听（{sa} vs {sb}）"}
    if sa == sb and ua > ub:
        # ua/ub 都是「未现张数」上界（对手按住的也算），差值仍是上界之差，
        # 所以带「上界」二字，不能写成「多 16 张机会」。
        return {"layer": layer, "kind": "fact", "equivalent": False,
                "note": f"同 {sa} 向听，未现进张上界多 {ua - ub} 张（{ua} vs {ub}）"}
    if wa > wb:
        return {"layer": layer, "kind": "fact", "equivalent": False,
                "note": f"叫口多 {wa - wb} 门（{wa} vs {wb}），更难被一次弃牌打掉"}
    if layer == "定缺门":
        return {"layer": layer, "kind": "fact", "equivalent": False,
                "note": "先断缺门：川麻不断门不能胡，缺门牌早晚都要打"}
    if layer == "牌墙可摸":
        va, vb = _wall_lo(a), _wall_lo(b)
        if va > vb:
            return {"layer": layer, "kind": "fact", "equivalent": False,
                    "note": f"牌墙确实还能摸到 {int(va)} 张（比打{tb}多 {int(va - vb)} 张）"}
    if layer == "防守安全":
        ba, bb = _danger_word(a), _danger_word(b)
        if ba == "未定档" or bb == "未定档":
            # 候选没带 danger_flow（std 家族就是这样）：只说方向，不给人编一个档位名
            return {"layer": layer, "kind": "model", "equivalent": False,
                    "note": "点炮风险更低（未标定模型值，只用于同分裁决）"}
        if ba != bb:
            return {"layer": layer, "kind": "model", "equivalent": False,
                    "note": f"点炮档位更低（{ba} vs {bb}，未标定模型值）"}
        # 同一个档位里仍有高低（微危 0.012 vs 0.038）：这层确实分了名次，
        # 但不得披上百分比。说“同档内更低”即可，数值留在 payload 里可查。
        return {"layer": layer, "kind": "model", "equivalent": False,
                "note": f"点炮同属{ba}档，本牌在档内风险更低（未标定，只用于同分裁决）"}
    if layer == "牌面弹性":
        fa = _FLEX_NAME.get(tile_flex_rank(ta), "中张")
        return {"layer": layer, "kind": "fact", "equivalent": False,
                "note": f"{fa}改良空间小，留在手上难成搭，先打它"}

    # 知识库的战术加权是 EV 分层最常见的实际成因（实测：【现物防守】+25 把同
    # 向听同进张的牌拉到首位）。规则名是可查的，加权的强度不是，所以 kind 给
    # model，但文案必须把规则名说出来——只说“模型评分更高”等于什么都没解释。
    tip_a = str(a.get("tactical_tip") or "")
    tip_b = str(b.get("tactical_tip") or "")
    if tip_a and tip_a != tip_b and _num(a, "tactical_ev_boost") > _num(b, "tactical_ev_boost"):
        return {"layer": layer, "kind": "model", "equivalent": False,
                "note": f"{tip_a}（知识库规则加权后评分更高；规则可查，评分未标定）"}

    # 剩下的就是「EV 分了名次，但上面那些事实层都没分出来」：
    # 可能是番数/副露/知识库加权的结果。说不出可核对的事实就直接承认，
    # 比编一个似是而非的理由诚实。
    return {"layer": layer, "kind": "model", "equivalent": False,
            "note": "模型综合评分更高（内部评分，未经实战标定，只用于相对排序）"}
