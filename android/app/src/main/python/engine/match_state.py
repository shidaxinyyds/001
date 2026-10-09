# -*- coding: utf-8 -*-
"""牌局阶段与事件状态机（纯逻辑层：不碰 cv2 / numpy / 网络，可离线逐帧单测）。

## 为什么要有这个模块（它解决的正是「看不清牌局在干嘛」）

端上原本只有一组 `phase_label / tactical_badge / tactical_intent`，那是**战术建议层**
（"该打哪张"），不是**局况事实层**（"现在谁在动、刚刚发生了什么"）。三个硬伤：

1. `_build_tactical_perception` 在 `shanten >= 2` 时 `return "", "", ""`，而悬浮窗
   `_buildTacticalPerceptionWidget` 见空串就 `SizedBox.shrink()`。于是开局到中盘
   ——恰恰是最需要对齐认知的一段——面板上**一个字都不显示**，用户自然"不知道在干嘛"。
2. 词表全是修辞（"摸牌决断 · 进听冲刺"），没有"轮到谁"和"谁刚做了什么"。
3. 碰/杠其实**早就识别到了**（`detect_player_melds` 回 (分区, 牌, 张数)），但它只进了
   牌池计数账本（`_meld_counts_34`），从未以"对家 碰 5万"的形式露出；本家副露（bottom
   分区）更是在 `zone_to_seat` 映射里被直接丢掉。

还有一处**真实缺陷**顺带在这里修掉：判"是否轮到你"用的是 `count % 3 == 2`
（14/11/8/5/2 视为待打）。这个同余式只在"没有杠"时成立——杠从手里拿走 4 张，
4 ≠ 0 (mod 3)，所以**杠过一次之后**立牌基数变成 9（13-4），摸进一张是 10，
`10 % 3 == 1` 永远不等于 2 ⇒ 明明轮到你出牌，面板却一直报"候牌中"，而且再也不会
纠正回来。本模块把基数按本家副露实际扣掉的张数校正后再判同余，这个盲区才闭合。

## 口径纪律（这个模块存在的意义就是"不编"）

* 只把**跨帧确认**后的观测当事件：单帧抖动（牌河漏读一张、副露区被动画压住）不允许
  变成播报。确认门一律用**时间**而不是帧数——真机采帧间隔在 15ms（活跃）与 80~120ms
  （防抖）之间切换，按帧数去抖会让同一件事在两种节奏下灵敏度不同。
* 牌河与副露在同局内**单调不减**：读数变小必然是误检，只忽略并计数，绝不反向播
  "某家收回了一张牌"这种物理上不存在的事件。
* 定位不到就明说：牌河差分能唯一确定新牌面才报"下家 打出 3条"，否则报
  "下家 打出一张牌（牌面待确认）"。
* **不宣称"现在轮到下家"**。能确定的是"本家手牌比基数多一张 ⇒ 轮到我"（物理事实）和
  "某家牌河刚多一张 ⇒ 那家刚打了牌"（观测事实）。"此刻轮到谁"要看三家手牌，识别器
  看不到，写出来就是猜。所以局况条只说"等他家行牌 · 最近：下家 打出 3条"。
* 副露视觉只能给"同字 3 张=碰、4 张=杠"，以及同局内 3→4=加杠。除此之外一律只报"杠"，
  不编造明杠/暗杠类别。
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

try:  # 牌面中文；离线单测没有 trainer 包时退化成机读码（只影响文案，不影响事件）
    from trainer.utils.convert import tile_to_chinese, tiles34_index_to_mpsz
except Exception:  # pragma: no cover
    tile_to_chinese = None  # type: ignore[assignment]
    tiles34_index_to_mpsz = None  # type: ignore[assignment]

# 座位：0=本家(bottom) 1=下家(right) 2=对家(top) 3=上家(left)，与 engine 的
# zone_to_seat 约定一致。接线时必须把 bottom 也送进来，否则"你碰了/你杠了"永远播不出。
SEAT_NAMES: Dict[int, str] = {0: "本家", 1: "下家", 2: "对家", 3: "上家"}
SEATS: Tuple[int, ...] = (0, 1, 2, 3)

# 阶段键（机读）→ 中文（面板直接展示；文案只在这里拼一份，Dart 不再重算一套）
PHASE_LABEL: Dict[str, str] = {
    "idle": "等待开局",
    "swap": "换三张",
    "dingque": "定缺选门",
    "pick": "开局选牌",
    "turn": "轮到你出牌",
    "wait": "候牌中",
    "irregular": "手牌待对齐",
}

# 事件类型：端上据此着色/加图标。新增类型若端上没配色只会用默认色，不会崩，
# 但守卫（test_match_state_guard）会断言两端清单一致，避免"播了却长得不认识"。
KINDS: Tuple[str, ...] = (
    "start", "draw", "discard", "discard_other", "pong", "kong", "added_kong",
    "swap_in", "swap_done", "dingque_in", "dingque", "tenpai", "irregular",
    "over",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class _TimeGate:
    """同一个候选值必须**持续 ≥ min_ms** 才提交。返回 True 表示"本次刚刚提交"。

    为什么不用帧数：采帧节奏会被防抖开关改变（15ms ↔ 80~120ms），按帧数去抖等于让
    确认时长跟着节奏漂移；按时长才与画面真实变化对齐。
    """

    __slots__ = ("value", "since_ms", "committed")

    def __init__(self, committed=None) -> None:
        self.value = None
        self.since_ms = 0
        self.committed = committed

    def observe(self, value, now_ms: int, min_ms: float) -> bool:
        if value == self.committed:
            self.value = None          # 与已提交一致 ⇒ 撤回候选，不重复提交
            self.since_ms = 0
            return False
        if value != self.value:
            self.value = value
            self.since_ms = now_ms
            return False
        if now_ms - self.since_ms >= min_ms:
            self.committed = value
            self.value = None
            self.since_ms = 0
            return True
        return False

    def force(self, value) -> None:
        """静默设基准（特殊阶段/清账：吸收观测但不产事件）。"""
        self.committed = value
        self.value = None
        self.since_ms = 0

    def drop(self) -> None:
        self.value = None
        self.since_ms = 0


def _snap_counter(values) -> Tuple[Tuple[int, int], ...]:
    """把牌河牌面读数折成 (型→张数) 的**可哈希快照**。用 Counter 而不是集合：
    同一型出现两张以上时，集合会把它们糊成一张，差分就再也对不上张数。"""
    c: Counter = Counter()
    for v in (values or []):
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        c[iv] += 1
    return tuple(sorted(c.items()))


def empty_phase_view(hint: str = "", base_hand: int = 13) -> Dict:
    """与 `update()` 返回值同 schema 的空视图（错误帧/启动前用）。

    为什么非要一份函数而不是各处手拼一份 dict：悬浮窗按固定键取值，任何一条出口
    少一个键，那条路径上整个局况条就不显示（而不是“少一个字段”）—— schema 只能
    由一处定义才能守住。"""
    return {
        "phase": "idle", "label": PHASE_LABEL["idle"],
        "hint": hint or PHASE_LABEL["idle"], "turn_seat": None, "turn_basis": "",
        "melds": [], "feed": [],
        "hand": {"count": 0, "base": int(base_hand), "meld_removed": 0},
        "evidence": {"river": {}, "rejected": {}, "special": "",
                     "confirm_ms": int(MatchPhaseMachine.CONFIRM_MS),
                     "over_ms": int(MatchPhaseMachine.OVER_MS)},
        "updated_at_ms": 0, "seq": 0,
    }


class MatchPhaseMachine:
    """把逐帧可观测事实折算成「阶段 + 回合 + 事件实录 + 副露一览」。

    生命周期：整局一个实例；engine 的 `_reset_game_state()` 里调 `reset()`；
    每个真识别帧调一次 `update(obs)`，返回值塞进 payload 的 `match_phase`。
    跳帧（frame_skipped）走缓存 payload，天然冻结在这一刻，不会重播事件。

    所有可调项都只是"确认时长/容量"，没有任何精度旋钮：它不改变识别结果，只做折算。
    """

    #: 数值差分的确认时长。60ms ≈ 活跃节奏下 4 帧、防抖节奏下 1 帧：既吃得住单帧
    #: 抖动，又不会让播报明显变钝（牌河/副露读数在两次扫描之间会被重复读到，
    #: 所以这里等的是"同一个值再看到一次"，几乎不额外增加端到端时延）。
    CONFIRM_MS = 60.0
    #: 「离开牌桌/结算」要持续这么久才播"本局结束"。与悬浮窗清空帧去抖(450ms)同量级，
    #: 免得数据层播了结束、显示层还挂着上一局。
    OVER_MS = 400.0
    #: 后台牌河扫描偶尔迟到很久，一帧能攒出"某家多了 3 张"。允许补齐，但每帧最多
    #: 产这么多条实录，防止一瞬间灌满把真正重要的动作（碰/杠/听牌）挤出去。
    MAX_EVENTS_PER_FRAME = 3
    #: 单家牌河上界：真实牌局不可能超过，超过说明读数脏，整帧拒收该家。
    RIVER_CAP = 40
    #: 单家副露组数上界（4 组已是刻子/杠满）。
    MELD_GROUP_CAP = 4
    #: 本家副露落定后，给立牌张数留多久“按旧基数判”的窗口：副露区与手牌行是同一帧
    #: 采的，但两边的确认门各自提交，中间会有一两帧“碰已计、立牌还没减”的间隙。
    #: 窗口外还按旧基数对上的读数一律当成误读（宁可报“待对齐”，不能说“轮到你”）。
    SETTLE_MS = 1200.0

    def __init__(self, base_hand: int = 13, feed_cap: int = 8, meld_cap: int = 12,
                 label_fn=None) -> None:
        self.base_hand = int(base_hand)
        self.feed_cap = int(feed_cap)
        self.meld_cap = int(meld_cap)
        # mpsz → 中文。由 engine 注入真 tile_to_chinese；没注入时用模块自己那份
        # （同源同一函数），再退化才用机读码。写成三层兜底是因为**文案是端上直接
        # 展示的**：一旦接线漏注入就显示「本家 杠 1s」这种机读码，用户看到的是
        # 「这软件在说黑话」。注入失败不影响事件，所以不做启动期断言。
        self._label_fn = label_fn or tile_to_chinese
        self.reset()

    # ------------------------------------------------------------------ 生命周期

    def reset(self) -> None:
        self._river: Dict[int, _TimeGate] = {s: _TimeGate(0) for s in SEATS}
        self._river_snap: Dict[int, _TimeGate] = {s: _TimeGate(()) for s in SEATS}
        # 牌面基线与牌面确认门分开：门只负责“这个读数稳不稳定”，基线只在
        # **张数确认提交**的那一帧前进。两者合成一个门就会出现：牌面比张数早一帧
        # 稳定，等张数确认时基线已被推到新值，差分永远是空 ⇒ 弃牌明明看得见牌面却
        # 只能报“待确认”。
        self._river_base: Dict[int, Tuple[Tuple[int, int], ...]] = {s: () for s in SEATS}
        self._melds: Dict[int, Dict[str, int]] = {s: {} for s in SEATS}
        self._meld_gates: Dict[Tuple[int, str], _TimeGate] = {}
        self._hand = _TimeGate(0)
        self._shanten = _TimeGate(None)
        self._dingque = _TimeGate(None)
        self._special = _TimeGate(None)
        self._last_now_ms = 0
        self._self_meld_removed = 0
        self._meld_removed_prev = 0          # 上一次本家副露提交前的扣牌数（结算窗口用）
        self._meld_settle_ms = 0             # 此时刻前允许按旧基数判回合
        self._suppress_hand_event = False     # 本家副露刚落定 ⇒ 下一次张数跳变不播报
        self._idle_since: Optional[int] = None
        self._feed: List[Dict] = []
        self._feed_max = 8
        self._started = False
        self._over_emitted = True
        self._last_actor: Optional[Dict] = None
        self._phase = "idle"
        self._evt_this_frame = 0
        self.seq = 0
        # 折算自检计数：全部进 payload.evidence。出问题时端上能直接看到
        # "是脏读被挡了 N 次"，而不是"神秘地没播报"。
        self._rejected = {"river_over_cap": 0, "meld_group_cap": 0,
                          "non_monotonic": 0, "event_cap": 0, "clock_back": 0,
                          "dingque_conflict": 0}

    # ------------------------------------------------------------------ 主入口

    def update(self, obs: Dict) -> Dict:
        """喂一帧观测，返回可直接 json 序列化的 match_phase 视图。

        obs（缺项一律按"没看到"处理，绝不臆造）：
          now_ms:int, status:str, count:int, drawing_tile:str|None,
          swap_phase/dq_phase/pick_phase:bool, self_discard:str|None,
          seat_river:{seat:张数}, seat_river_tiles:{seat:[34型]},
          seat_melds:{seat:[(mpsz, 3|4)]},
          dingque_suit:int|None, dingque_name:str, dingque_recommend:str,
          shanten:int|None, ting_tiles:[mpsz]
        """
        self.seq += 1
        self._evt_this_frame = 0
        now = int(obs.get("now_ms") or _now_ms())
        if now < self._last_now_ms:
            self._rebase_clock(now)
        self._last_now_ms = now
        confirm = float(obs.get("confirm_ms") or self.CONFIRM_MS)

        special = self._update_special(obs, now, confirm)
        if special:
            # 换三张/定缺/选牌期间牌河与副露本就不该有增量（引擎此时不扫牌河），
            # 任何残留读数只配当基准，不配当事件。
            self._absorb(obs, absorb_dingque=False)
            # 缺门恰恰是在定缺这一屏上读出来并敲定的：这个分支提前 return，早先没调
            # _update_dingque，于是“进入定缺选门”播完之后“定缺敲定：条”永远轮不到——
            # 最关心的那个结果反而从不播报。牌河/副露在这里确实无事可做，缺门不是；
            # 所以 _absorb 也得放过缺门闸门，不能把它一起 force 成基准把事件吞掉。
            self._update_dingque(obs, now, confirm)
            self._phase = special
            return self._view(now, obs, special, None,
                              f"处于「{PHASE_LABEL[special]}」交互阶段",
                              hint=self._special_hint(special, obs))

        status = str(obs.get("status") or "")
        count = int(obs.get("count") or 0)
        in_table = count > 0 and status not in (
            "waiting", "no_tiles", "py_error", "decode_error", "animation")
        if not in_table:
            self._phase = self._off_table(now)
            return self._view(now, obs, "idle", None,
                              "画面无牌桌读数（未开局、结算或瞬态遮挡）")

        if not self._started:
            # 本局第一个有牌帧：一切基准从实测起算，旧局读数不得跨局生效
            self._started = True
            self._over_emitted = False
            self._idle_since = None
            self._hand.force(count)
            for s in SEATS:
                _snap = _snap_counter((obs.get("seat_river_tiles") or {}).get(s))
                self._river[s].force(self._river_n(s, obs))
                self._river_snap[s].force(_snap)
                self._river_base[s] = _snap
            self._push("start", 0, None, now, note=str(count))

        # 副露先折算：本家扣牌数决定手牌基数，必须早于回合判定提交
        self._update_melds(obs, now, confirm)
        self._update_rivers(obs, now, confirm)
        own_turn, basis = self._update_hand(obs, now, confirm)
        self._update_shanten(obs, now, confirm)
        self._update_dingque(obs, now, confirm)

        if own_turn is None:
            phase = "irregular"
            # 只在"刚刚发现不对"时播一次；持续异常不重复刷屏（否则实录全是同一句废话）
            if not self._feed or self._feed[-1].get("kind") != "irregular":
                self._push("irregular", 0, None, now, note=basis)
        else:
            phase = "turn" if own_turn else "wait"
        self._phase = phase
        self._idle_since = None
        hint = self._hint(phase, obs, basis)
        return self._view(now, obs, phase, (0 if own_turn else None), basis, hint=hint)

    # ------------------------------------------------------------------ 各路折算

    @staticmethod
    def _river_n(seat: int, obs: Dict) -> int:
        try:
            return max(0, int((obs.get("seat_river") or {}).get(seat, 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _push(self, kind: str, seat: Optional[int], tile: Optional[str],
              at_ms: int, note: str = "") -> Optional[Dict]:
        """追加一条实录（超每帧限额则只计数不追加）。文本在这里拼好——
        Python 与 Dart 各拼一套必然漂移。"""
        if self._evt_this_frame >= self.MAX_EVENTS_PER_FRAME:
            self._rejected["event_cap"] += 1
            return None
        self._evt_this_frame += 1
        cn = self._tile_cn(tile)
        who = SEAT_NAMES.get(seat if seat is not None else -1, "")
        if kind == "start":
            text = f"本局开始，起手 {note} 张"
        elif kind == "draw":
            text = f"本家 摸到 {cn}" if cn else "本家 摸牌"
        elif kind == "discard":
            text = f"本家 打出 {cn}" if cn else "本家 打出 一张牌（牌面待确认）"
        elif kind == "discard_other":
            text = f"{who} 打出 {cn}" if cn else f"{who} 打出一张牌（牌面待确认）"
        elif kind == "pong":
            text = f"{who} 碰 {cn}"
        elif kind == "kong":
            text = f"{who} 杠 {cn}"
        elif kind == "added_kong":
            text = f"{who} 加杠 {cn}"
        elif kind == "swap_in":
            text = "进入换三张"
        elif kind == "swap_done":
            text = "换牌完成，开始行牌"
        elif kind == "dingque_in":
            # 只是“进了定缺这一屏”，缺门还没读出来。与 swap_in 对称：早前一版这里
            # 复用了 "dingque"，播出来是“定缺已敲定”——把还没做的决定说成已经做了。
            text = "进入定缺选门"
        elif kind == "dingque":
            text = f"定缺敲定：{note}" if note else "定缺已敲定"
        elif kind == "tenpai":
            text = f"本家听牌：{note}" if note else "本家听牌"
        elif kind == "irregular":
            text = note or "手牌张数与副露不符，阶段待对齐"
        elif kind == "over":
            text = "本局结束（画面已离开牌桌或进入结算）"
        else:
            text = note or kind
        item = {"kind": kind, "seat": seat, "tile": tile or "", "tile_cn": cn,
                "at_ms": int(at_ms), "text": text}
        self._feed.append(item)
        if len(self._feed) > self._feed_max:
            del self._feed[:-self._feed_max]
        if kind not in ("over", "irregular"):
            self._last_actor = item
        return item

    def _tile_cn(self, mpsz: Optional[str]) -> str:
        if not mpsz:
            return ""
        fn = self._label_fn
        if fn is None:
            return str(mpsz)
        try:
            return str(fn(mpsz)) or str(mpsz)
        except Exception:
            return str(mpsz)

    def _update_special(self, obs: Dict, now: int, confirm: float) -> Optional[str]:
        """换三张/定缺/选牌：返回当前特殊阶段键，普通行牌返回 None。"""
        flag = ("swap" if obs.get("swap_phase")
                else "dingque" if obs.get("dq_phase")
                else "pick" if obs.get("pick_phase") else None)
        # `observe` 只回答“刚刚是否提交”，新提交的是什么值得在提交**前**把旧值抢下来：
        # 离开换牌阶段时提交的是 None，拿返回值去比 "swap" 永远不相等，
        # “换牌完成”就再也不会播（换牌结束是玩家最关心的时刻之一）。
        prev_committed = self._special.committed
        just_committed = self._special.observe(flag, now, confirm)
        if flag:
            if just_committed:
                if flag == "swap":
                    self._push("swap_in", 0, None, now)
                elif flag == "dingque":
                    # 进入定缺交互时门还没敲，这里只报“进入”（dingque_in），具体缺门由
                    # _update_dingque 在读到缺门徽章/用户 override 后补报，不提前替玩家决定。
                    self._push("dingque_in", 0, None, now)
            return flag
        if just_committed and prev_committed == "swap":
            self._push("swap_done", 0, None, now)
        return None

    def _absorb(self, obs: Dict, absorb_dingque: bool = True) -> None:
        """把当前所有读数写成基准：吸收，不产事件，也不留悬空候选。

        `absorb_dingque=False`：定缺那一屏上的缺门读数不是噪声而是待播报的结果，
        把它写成基准就等于把“敲定了什么”消灭在萌芽里（由调用方接着去走确认门）。
        """
        for s in SEATS:
            snap = _snap_counter((obs.get("seat_river_tiles") or {}).get(s))
            self._river[s].force(self._river_n(s, obs))
            self._river[s].drop()
            self._river_snap[s].force(snap)
            self._river_snap[s].drop()
            self._river_base[s] = snap
        seen: Dict[Tuple[int, str], int] = {}
        for seat, tile, n in self._iter_meld_obs(obs):
            seen[(seat, tile)] = max(n, seen.get((seat, tile), 0))
        for key, gate in self._meld_gates.items():
            gate.force(seen.get(key, gate.committed))
            gate.drop()
        self._hand.force(int(obs.get("count") or 0))
        self._shanten.force(obs.get("shanten"))
        if absorb_dingque:
            self._dingque.force(obs.get("dingque_suit"))

    @staticmethod
    def _iter_meld_obs(obs: Dict):
        raw = obs.get("seat_melds") or {}
        for seat_key, entries in raw.items():
            try:
                seat = int(seat_key)
            except (TypeError, ValueError):
                continue
            if seat not in SEATS:
                continue
            for ent in (entries or []):
                try:
                    tile, n = str(ent[0]), int(ent[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if not tile or n not in (3, 4):
                    # detect_player_melds 按长宽比猜张数，只会给 3 或 4；出现别的值
                    # 就是接线错了。宁可不播，也不能把"2 张"当副露记账。
                    continue
                yield seat, tile, n

    def _update_melds(self, obs: Dict, now: int, confirm: float) -> List[Dict]:
        """副露账本：每座每型同局内单调不减（碰→杠允许 3→4），时间门确认。

        注意必须**先遍历完所有观测再返回**：中途 return 会让同帧其它副露这一次
        根本没被 observe，确认时长被无谓拉长（表现为"碰了两句才播"）。
        """
        out: List[Dict] = []
        seen: Dict[Tuple[int, str], int] = {}
        for seat, tile, n in self._iter_meld_obs(obs):
            seen[(seat, tile)] = max(n, seen.get((seat, tile), 0))
        for key, n in seen.items():
            seat, tile = key
            gate = self._meld_gates.get(key)
            if gate is None:
                gate = self._meld_gates[key] = _TimeGate(0)
            prev = self._melds[seat].get(tile, 0)
            if not gate.observe(n, now, confirm):
                continue
            if n <= prev:
                if n < prev:
                    self._rejected["non_monotonic"] += 1
                continue
            if prev == 0 and len(self._melds[seat]) >= self.MELD_GROUP_CAP:
                self._rejected["meld_group_cap"] += 1
                continue
            self._melds[seat][tile] = n
            _removed_before = self._self_meld_removed
            self._self_meld_removed += (n - prev)
            if seat == 0:
                # 碰/杠会让立牌张数一次掉 2~3 张，那是副露不是摸打；
                # 关掉紧接着的那一次张数跳变播报，免得播成"本家 打出 未知牌"。
                self._suppress_hand_event = True
                # 记下旧扣牌数与窗口：下面几帧立牌可能还按旧基数摆着，那不是误读
                self._meld_removed_prev = _removed_before
                self._meld_settle_ms = now + self.SETTLE_MS
            if prev == 3 and n == 4:
                out.append(self._push("added_kong", seat, tile, now))
            elif n == 4:
                out.append(self._push("kong", seat, tile, now))
            else:
                out.append(self._push("pong", seat, tile, now))
        # 已提交但本帧没再看到的副露：副露不会消失，只丢弃悬空候选，基准保留。
        for key, gate in self._meld_gates.items():
            if key not in seen:
                gate.drop()
        return [e for e in out if e]

    def _update_rivers(self, obs: Dict, now: int, confirm: float) -> List[Dict]:
        """各家牌河张数 + 牌面快照的差分 → "X家 打出 Y"。

        张数与牌面各自一个时间门：牌面读不出（区里漏一张）时仍要能播"打了牌"这件事，
        只是牌面写成"待确认"；反之牌面多出一张而张数没变，说明是分类抖动，不播。
        """
        out: List[Dict] = []
        tiles_raw = obs.get("seat_river_tiles") or {}
        for seat in SEATS:
            gate = self._river[seat]
            prev_n = int(gate.committed or 0)
            n = self._river_n(seat, obs)
            if n > self.RIVER_CAP:
                self._rejected["river_over_cap"] += 1
                gate.drop()
                continue
            if n < prev_n:
                # 牌河不会变短：脏读，丢候选保基准
                self._rejected["non_monotonic"] += 1
                gate.drop()
                continue
            sgate = self._river_snap[seat]
            sgate.observe(_snap_counter(tiles_raw.get(seat)), now, confirm)
            if not gate.observe(n, now, confirm):
                continue
            added = n - prev_n
            if added <= 0:
                continue
            # 只在“张数增量恰好等于牌面快照增量、且新增只涉一种牌型”时才报牌面
            prev_cnt = dict(self._river_base[seat])
            cur_cnt = dict(sgate.committed or ())
            uniq = [t for t, c in cur_cnt.items() if c > prev_cnt.get(t, 0)]
            snap_added = sum(cur_cnt[t] - prev_cnt.get(t, 0) for t in uniq)
            tile = self._t34_to_mpsz(uniq[0]) if (added == snap_added and len(uniq) == 1) else None
            for _ in range(min(added, self.MAX_EVENTS_PER_FRAME)):
                evt = self._push("discard_other" if seat else "discard", seat, tile, now)
                if evt is None:
                    break
                tile = None          # 补多张时只有第一张可能有牌面，其余如实待确认
                out.append(evt)
            # 基线推到本次用来差分的读数：不推则下一次会把这两张一起算、凭空多报；
            # 推了之后若牌面一直追不上张数，也只是持续报“待确认”，不会报成别的牌。
            self._river_base[seat] = tuple(sorted(cur_cnt.items()))
        return out

    @staticmethod
    def _t34_to_mpsz(idx: int) -> Optional[str]:
        if tiles34_index_to_mpsz is None:
            return None
        try:
            return str(tiles34_index_to_mpsz(int(idx)))
        except Exception:
            return None

    def _update_hand(self, obs: Dict, now: int, confirm: float):
        count = int(obs.get("count") or 0)
        prev = int(self._hand.committed or 0)
        if not self._hand.observe(count, now, confirm):
            # 候选没确认：沿用上一次提交值给答案，阶段条不会在两个值之间来回闪
            return self._turn_of(prev, now)
        turn, basis = self._turn_of(count, now)
        base = self.base_hand - self._self_meld_removed
        if turn is True and prev == base and not self._suppress_hand_event:
            self._push("draw", 0, obs.get("drawing_tile"), now)
        elif turn is False and prev == base + 1 and not self._suppress_hand_event:
            self._push("discard", 0, obs.get("self_discard"), now)
        if self._suppress_hand_event and count != prev:
            # 副露刚落定造成的张数跳变：吃掉一次，之后恢复播报
            self._suppress_hand_event = False
        return turn, basis

    def _turn_of(self, count: int, now: int):
        """本家立牌张数 → 轮到我(True) / 候牌(False) / 对不上(None)。

        基数 = 13 - 本家副露扣掉的张数（碰/吃 3、杠 4），只认两个合法读数：
          张数 = 基数   ⇒ 手上没有多余的牌，本轮不到你出牌；
          张数 = 基数+1 ⇒ 已摸进一张，轮到你出牌；
          其余          ⇒ 与副露对不上（漏读/多读），明确报"待对齐"，不硬猜。
        「等于基数」只能推出于「轮不到你」，推不出「你刚打完一张」：开局庄家还未
        摸牌时本家也是 13 张，此时并没有发生过「出完一张」这件事。旧口径写成
        「已出完等下一轮」，是在把牌局里没有发生过的事当成事实报给用户。
        旧口径 `count % 3 == 2` 在有杠时会把"轮到你"永久误判成"候牌中"；而光把同余
        式里的 13 换成校正后的基数又会把“比基数多 3 张”这种明显不对的读数糊成
        “候牌中”（多出来的牌不会凭空出现：要么漏读、要么副露还没跟上）。唯一的
        例外是本家副露刚落定的结算窗口：那时立牌还按旧基数摆着，就按旧基数判。
        """
        if count <= 0:
            return None, "手牌张数尚未确认"
        base = self.base_hand - self._self_meld_removed
        if count == base:
            return False, f"立牌 {count} 张：等于基数 {base}，手上没有多余的牌可出"
        if count == base + 1:
            return True, f"立牌 {count} 张：基数 {base} + 已摸 1 张"
        if now < self._meld_settle_ms:
            prev_base = self.base_hand - self._meld_removed_prev
            if count in (prev_base, prev_base + 1) and count > base:
                return True, (f"立牌 {count} 张：本家副露刚落定（新基数 {base}），"
                              f"等你把多出的牌打出去")
        return None, (f"立牌 {count} 张与副露扣牌数（本家副露 {self._self_meld_removed} 张，"
                      f"基数 {base}）对不上，按实际读数呈现")

    def _update_shanten(self, obs: Dict, now: int, confirm: float) -> None:
        prev = self._shanten.committed
        if not self._shanten.observe(obs.get("shanten"), now, confirm):
            return
        cur = self._shanten.committed
        if cur == 0 and (prev is None or prev > 0):
            tiles = obs.get("ting_tiles") or []
            self._push("tenpai", 0, None, now,
                       note="/".join(self._tile_cn(t) for t in tiles[:4]))

    def _update_dingque(self, obs: Dict, now: int, confirm: float) -> None:
        suit = obs.get("dingque_suit")
        if suit is None:
            self._dingque.observe(None, now, confirm)
            return
        if self._dingque.committed is not None:
            # 缺门一局至多变一次。锁存后再读到不同值，只计不播：同一屏里先说
            # 「缺筒」再说「缺条」比不播更糟，而把这个冲突留在 evidence.rejected
            # 里才能事后查清是徽章误读还是真的跨了局。
            if suit != self._dingque.committed:
                self._rejected["dingque_conflict"] += 1
            return
        if self._dingque.observe(suit, now, confirm):
            # 事件里的缺门只能来自 dingque_suit 本身（牌桌徽章或用户手动指定），
            # 不得回落到 `dingque_recommend`：那是引擎按张数算出来的建议，玩家还没选。
            # 把建议播成「定缺敲定：X」是在牌局里凭空造一个事实。
            name = str(obs.get("dingque_name") or "")
            if not name:
                name = {0: "万", 1: "筒", 2: "条"}.get(int(suit), "")
            self._push("dingque", 0, None, now, note=name)

    def _off_table(self, now: int) -> str:
        """离开牌桌/结算：持续 OVER_MS 才播"本局结束"，然后回到 idle 并清账。"""
        if self._idle_since is None:
            self._idle_since = now
        if (self._started and not self._over_emitted
                and now - self._idle_since >= self.OVER_MS):
            self._push("over", None, None, now)
            self._over_emitted = True
            self._started = False
            # 下一局所有基准重新起算：副露账本与牌河基准必须一起清空。只清牌河不清
            # 副露，“本局结束”后的副露一览会把上一局的碰/杠带进新一局画面里。
            self._clear_field_state()
        return "idle"

    def _clear_field_state(self) -> None:
        """清掉所有跨帧事实基准（局末/新局/特殊阶段退出时的同一动作）。"""
        for s in SEATS:
            self._river[s].force(0)
            self._river_snap[s].force(())
            self._river_base[s] = ()
            self._melds[s] = {}
        self._meld_gates = {}
        self._self_meld_removed = 0
        self._meld_removed_prev = 0
        self._meld_settle_ms = 0
        self._suppress_hand_event = False
        self._hand.force(0)
        self._shanten.force(None)
        self._dingque.force(None)
        self._last_actor = None

    def _all_gates(self):
        """所有持有计时点的确认门（回拨重定基时要一个不漏地扫到）。"""
        yield from self._river.values()
        yield from self._river_snap.values()
        yield from self._meld_gates.values()
        yield self._hand
        yield self._shanten
        yield self._dingque
        yield self._special

    def _rebase_clock(self, now: int) -> None:
        """墙钟回拨（用户手改系统时间 / NTP 校时）时，把所有等待中的计时点一起平移。

        不处理会怎样：确认门判的是 `now - since_ms >= min_ms`，时间倒流后 now 恒小于
        since ⇒ 所有门再也不提交，局况条**永久冻结**在回拨前那一刻的读数上，而且一点
        报错都没有（用户看到的是“牌局明明在走，面板一动不动”）。这里保住“已经等了多
        久”：since 同步减去回拨量。回拨这件事计入 evidence.rejected.clock_back，事后能查。
        """
        delta = self._last_now_ms - int(now)
        if delta <= 0:
            return
        for gate in self._all_gates():
            if gate.value is not None:
                gate.since_ms -= delta
        if self._idle_since is not None:
            self._idle_since -= delta
        self._rejected["clock_back"] += 1

    # ------------------------------------------------------------------ 文案与视图

    def _special_hint(self, phase: str, obs: Dict) -> str:
        if phase == "swap":
            tiles = ((obs.get("swap_tiles") or [])[:3])
            if tiles:
                return "准备换出 " + "、".join(self._tile_cn(t) for t in tiles)
            return "正在评估起手，挑三张同门换出"
        if phase == "dingque":
            name = str(obs.get("dingque_recommend") or "")
            return f"建议定缺【{name}】门" if name else "正在估各门厚度，缺张最少的一门"
        return "识别候选牌张，请在弹窗里确认选牌"

    def _hint(self, phase: str, obs: Dict, basis: str) -> str:
        if phase == "turn":
            cn = self._tile_cn(obs.get("drawing_tile"))
            return f"已摸进 {cn}，等你出牌" if cn else "轮到你，请出牌"
        if phase == "wait":
            last = self._last_actor
            if last and last.get("kind") in ("draw", "discard", "discard_other",
                                             "pong", "kong", "added_kong"):
                return f"等他家行牌 · 最近：{last['text']}"
            # 没有可引荐的上一次行动时，只说能确定的那半句：现在轮不到本家。
            # 不能说「已出完这一张」（开局等庄家摸牌时本家一张还没打过）。
            return "还没轮到你，等他家行动"
        if phase == "irregular":
            return basis
        return PHASE_LABEL.get(phase, "")

    def _view(self, now: int, obs: Dict, phase: str, turn_seat: Optional[int],
              basis: str, hint: str = "") -> Dict:
        melds: List[Dict] = []
        for seat in SEATS:
            for tile, n in sorted(self._melds[seat].items()):
                melds.append({"seat": seat, "seat_name": SEAT_NAMES[seat],
                              "tile": tile, "tile_cn": self._tile_cn(tile), "n": n,
                              "kind": ("kong" if n == 4 else
                                       "pong" if n == 3 else "unknown")})
                if len(melds) >= self.meld_cap:
                    break
            if len(melds) >= self.meld_cap:
                break
        base = self.base_hand - self._self_meld_removed
        return {
            "phase": phase,
            "label": PHASE_LABEL.get(phase, ""),
            "hint": hint or PHASE_LABEL.get(phase, ""),
            "turn_seat": turn_seat,
            "turn_basis": basis or "",
            "melds": melds,
            "feed": [dict(x) for x in self._feed],
            "hand": {"count": int(obs.get("count") or 0), "base": base,
                     "meld_removed": self._self_meld_removed},
            "evidence": {
                "river": {SEAT_NAMES[s]: int(self._river[s].committed or 0)
                          for s in SEATS},
                "rejected": dict(self._rejected),
                "special": str(self._special.committed or ""),
                "confirm_ms": int(self.CONFIRM_MS),
                "over_ms": int(self.OVER_MS),
            },
            "updated_at_ms": int(now),
            "seq": self.seq,
        }

    # 供守卫/调试页读取的内部快照（不参与端上渲染）
    def own_removed(self) -> int:
        """本家副露已确认扣掉的牌数（碰/吃 3、杠 4）。

        engine 侧的 `is_drawing` 判定要用它校正立牌基数。这里给的是**已过双时确认**
        的值，不是后台扫描的原始快照：副露区一帧误读就会把基数抹改 3 张，当场把
        “轮到你”误报成“候牌中”。"""
        return int(self._self_meld_removed)

    def debug_state(self) -> Dict:
        return {"phase": self._phase, "started": self._started,
                "hand": self._hand.committed, "melds": {SEAT_NAMES[s]: dict(self._melds[s])
                                                        for s in SEATS},
                "river": {SEAT_NAMES[s]: int(self._river[s].committed or 0) for s in SEATS},
                "feed_kinds": [x["kind"] for x in self._feed],
                "seq": self.seq}
