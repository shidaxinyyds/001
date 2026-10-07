# -*- coding: utf-8 -*-
"""分层归因：同一帧，从"检测器看得见"到"面板真显示"之间，牌是在哪一步丢的。

背景（实测）：`localtest/ab_bank_impact.py` 的 C 口径（声明平台、生产可达）在腾讯
底座上是 494/496=99.6% 逐张；而 `localtest/eval_base.py`（走 `Engine.process`，面板
真正用的那一层）同批帧却有 31 帧手牌不匹配。差值必须逐段拆开才知道该修哪一层。

本脚本把一帧拆成 5 个可观测面（手牌行口径，全部按平台声明的生产设置）：

  S1 网格检测器·原图   `TencentGridDetector.detect_hand_strip`（模板 NCC 通道的上限）
  S2 生产检测器·原图   `Engine.get_detector()` 实际用的那个检测器，喂整屏
                       ——实测它是 YOLODetector（`engine.py::get_detector` 第 1 优先，
                       onnx 随 python 源码打进 APK），模板网格只是第 2 优先的兜底。
                       S1/S2 的差 = "换检测器"的差，与 Engine 后处理无关。
  S3 生产检测器·Engine图 同一次 process 内 detector 真正拿到的图（方向归一/降采样/
                       ROI 切片之后）——S2→S3 的差 = 输入变换的代价（ROI 切掉牌在这露形）
  S4 门槛后            `_apply_conf`（网格 0.38 / 严格 0.50）+ 牌形 aspect + 牌面亮度
                       S3→S4 的差 = "宁可不识别也不臆测"的代价，逐张给拒绝原因
  S5 面板输出          稳定器 / 同字互斥整帧否决 / 阶段门控（换三张·选牌·waiting 清空）

为什么要这么拆：98% 卡在哪一层，决定下一步是补样本、换检测器、改 ROI 还是松门槛——
四者的修法互不兼容，靠猜会浪费时间。这里也能看出"单帧喂 Engine"这个评测口径是否
公平（S5 里 waiting/换牌阶段的清空在连续帧生产环境并不会发生）。

用法：py -3.10 -X utf8 localtest/layer_cost.py [帧数] [--set base|new]
      base=腾讯底座人工 GT（localtest/gt/shots.json，37 帧）
      new =新素材人工 GT（localtest/gt/new_shots.json，public/0 里打开的几帧，
            逐帧按 GT 声明的平台跑 = 生产口径；这批帧是模板未收割过的样本）
输出：build/layer_cost_base.txt / build/layer_cost_new.txt
"""
import argparse
import collections
import contextlib
import io
import json
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

from engine.engine import Engine, MIN_FACE_BRIGHTNESS  # noqa: E402
from modes import DEFAULT_MODE, available_set  # noqa: E402
from platforms import PLATFORMS  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from recognition.structural import MAX_TILE_ASPECT, MIN_TILE_ASPECT  # noqa: E402
from trainer.utils.convert import tiles34_index_to_mpsz  # noqa: E402
from eval_base import canon_mpsz, tile_accuracy, GT_PATH, SHOT_DIR  # noqa: E402

GRID_MIN_CONF = 0.38      # `_apply_conf` 里网格整排切片的专用门槛
NEW_GT = os.path.join(HERE, "gt", "new_shots.json")
NEW_DIR = os.path.join(REPO, "public", "0")
# 风格名 -> 平台 key（platforms.py 注册名），与 eval_new_material 同一张表。
STYLE_OF = {"tencent": "tencent", "shushan": "shushan", "queshen": "gd_queshen",
            "tuyou": "tuyou", "jj": "jj", "weile": "weile"}


def mode_labels(key):
    """玩法 key -> 该玩法允许出现的牌的 mpsz 标签集。"""
    return {tiles34_index_to_mpsz(i) for i in available_set(key)}


def mode_for(platform, hand_tiles):
    """为「平台 + 这批 GT 手牌」选一个**装得下这些牌**的玩法，返回 (mode_key, missing)。

    为什么评测必须显式声明玩法（本轮实测，`localtest/_probe_panel_styles_blame.py`）：
    分类器打分前有一道牌集闸门 `resolve_candidate_tiles`，玩法里没有的牌**根本不进候选**
    —— 广东雀神帧 45329b3e 画面上是 東/發，在川麻系玩法（字牌只放开 7z）下被强行判成
    1p(0.43)/1s(0.41)，而同帧按能装下字牌的玩法喂就逐位回到 GT(1.00)。于是
    「面板 84 / 网格 91」这个缺口从来不是识别退化，是**评测拿错了玩法**。
    自然实验也印证：这批帧里只有 7z 的（jj/tuyou）全对，需要 1z/3z/6z 的（queshen）全错。

    选择顺序（要确定性，也要尽量贴合用户真实选择）：
      1) 平台 default_mode 能装下就用它 —— 那正是用户进这个平台会拿到的默认玩法；
      2) 否则在该平台 supported_modes 里取**能装下的最小牌集**（最保守、最接近真实）；
      3) 都装不下 -> 返回 default_mode 并把 missing 如实交出去（**绝不静默放宽**，
         这条差额就是「平台玩法表与素材冲突」的台账，由守卫 test_mode_covers_gt 盯）。
    """
    pf = PLATFORMS.get(platform) or {}
    supported = list(pf.get("supported_modes") or [])
    default = pf.get("default_mode")
    want = set(hand_tiles or [])
    if default and want <= mode_labels(default):
        return default, []
    fitting = [k for k in supported if want <= mode_labels(k)]
    if fitting:
        fitting.sort(key=lambda k: (len(mode_labels(k)), k))
        return fitting[0], []
    miss = sorted(want - (mode_labels(default) if default else set()))
    return (default or (supported[0] if supported else DEFAULT_MODE)), miss


def load_entries(which):
    """统一两套 GT 形状：都返回 [{path, platform, hand, mode, mode_missing}]。

    `mode` 是**评测口径的一部分**（见 `mode_for`）：玩法决定分类器的牌集闸门，
    不声明就等于让引擎用建表时的默认玩法去认这批帧，字牌会被整批挤成数牌。
    """
    if which == "new":
        with open(NEW_GT, encoding="utf-8") as fp:
            shots = json.load(fp)["shots"]
        out = []
        for e in shots:
            if not e.get("verified"):
                continue
            platform = STYLE_OF.get(e["style"], "tencent")
            hand = sorted(e["hand"])
            mode, miss = mode_for(platform, hand)
            out.append({"path": os.path.join(NEW_DIR, e["file"]),
                        "platform": platform, "hand": hand,
                        "mode": mode, "mode_missing": miss})
        return out
    with open(GT_PATH, encoding="utf-8") as fp:
        shots = json.load(fp)["shots"]
    out = []
    for e in shots:
        if not e.get("verified"):
            continue
        hand = canon_mpsz(e.get("hand", ""))
        if not hand:
            continue
        mode, miss = mode_for("tencent", hand)
        out.append({"path": os.path.join(SHOT_DIR, e["file"]), "platform": "tencent",
                    "hand": hand, "mode": mode, "mode_missing": miss})
    return out


def _hand_row(rows):
    """detector 返回的第一行 = 引擎认定的手牌行（YOLO/网格两侧均如此）。"""
    return list(rows[0]) if rows else []


def _labels(dets):
    """(rect,label,conf) 序列 -> 按左→右排序的标签序列。"""
    return [l for (_r, l, _c) in sorted([d for d in dets if d[1]], key=lambda d: int(d[0][0]))]


def _diff_missing(got, want):
    """多重集差：want 里有、got 里没的那些张。"""
    c = collections.Counter(want)
    for x in got:
        c[x] -= 1
    return sorted(c.elements())


def run_one_frame(path, want, platform, grid_det, prod_det, lines):
    """同一帧跑遍 5 个面，返回各面逐张命中；拒绝原因写进 lines。"""
    img = cv2.imread(path)
    if img is None:
        return None

    for d in (grid_det, prod_det):
        if hasattr(d, "set_platform_styles"):
            d.set_platform_styles(platform)

    t0 = time.time()
    s1 = _labels(grid_det.detect_hand_strip(img))
    t1 = time.time()
    s2 = _labels(_hand_row(prod_det.detect_all_rows(img)))

    # 生产口径：用户在面板里选了平台。Engine 每帧 `load_platform()` 读平台，这里把
    # 这个入口换成固定值（否则会读到 cwd 里可能残留的 mahjong_platform.json，结果不可复现）。
    import engine.engine as E
    E.load_platform = lambda *a, **kw: platform
    eng = Engine()
    # 包的是「手牌通道」而不是主检测器：引擎的手牌行可以走网格 NCC（见引擎
    # get_hand_detector），包错对象就会看不到 detector 被调用（只记成“未走到”）。
    det = eng.get_hand_detector()
    rec = {}
    orig = det.detect_all_rows

    def wrapped(image, *a, **kw):
        rows = orig(image, *a, **kw)
        rec["image"] = image
        rec["rows"] = rows
        return rows

    det.detect_all_rows = wrapped
    rejects = []
    orig_conf = eng._apply_conf

    def spy_conf(rect, label, conf, bootstrap=False, is_grid=False):
        lab = orig_conf(rect, label, conf, bootstrap=bootstrap, is_grid=is_grid)
        if label is not None and lab is None:
            aspect = rect[2] / float(rect[3]) if rect[3] else -1.0
            if aspect < MIN_TILE_ASPECT or aspect > MAX_TILE_ASPECT:
                why = "牌形aspect%.2f" % aspect
            elif conf < GRID_MIN_CONF:
                why = "低分conf%.2f" % conf
            else:
                why = "亮度/其它conf%.2f" % conf
            rejects.append((label, why))
        return lab

    eng._apply_conf = spy_conf
    dup_hit = []
    orig_dup = eng._check_dup_explosion
    eng._check_dup_explosion = lambda mpsz: (dup_hit.append(orig_dup(mpsz)), dup_hit[-1])[1]

    with contextlib.redirect_stdout(io.StringIO()):
        res = eng.process(img)
    t2 = time.time()
    d = json.loads(res.result) if res is not None else {}

    hand = _hand_row(rec.get("rows"))
    s3 = _labels(hand)
    # S4 = S3 扣掉 `_apply_conf` 判死的张（spy 只记录 label 非 None 却被拒的张）
    s4 = list(s3)
    for lab, _why in rejects:
        if lab in s4:
            s4.remove(lab)
    s5 = canon_mpsz(d.get("hand", ""))
    status = str(d.get("status"))

    hits = [tile_accuracy(want, x)[0] for x in (s1, s2, s3, s4, s5)]
    note = []
    if "rows" not in rec:
        note.append("Engine 未走到 detector（牌桌校验/方向探测提前返回）")
    elif len(s2) != len(s3):
        note.append(f"输入变换{len(s2)}→{len(s3)}张(ROI/方向)")
    if rejects:
        note.append("门槛拒%d张:%s" % (len(rejects), ",".join(sorted({w for _l, w in rejects}))))
    if dup_hit and dup_hit[-1]:
        note.append("同字互斥整帧否决")
    if status in ("waiting", "no_tiles", "decode_error") and s4:
        note.append(f"出口清空({status})")
    extra = ""
    if s5 and len(s5) > len(s4):
        extra = f" 引擎补槽{len(s5) - len(s4)}张"
    lines.append(f"{os.path.basename(path)[:20]:20} GT{len(want):2d} "
                 + " ".join(f"S{i + 1}{h:2d}" for i, h in enumerate(hits))
                 + f" ({len(s1)}/{len(s2)}/{len(s3)}/{len(s4)}/{len(s5)}) "
                 + f"{status:8} {(t1 - t0) * 1000:5.0f}/{(t2 - t1) * 1000:6.0f}ms  "
                 + "  ".join(note) + extra)
    if s5 and hits[0] != hits[4]:
        lines.append(f"     面板: {' '.join(s5)}   相对GT缺{_diff_missing(s5, want)} 多{_diff_missing(want, s5)}")
    if hits[0] != hits[1]:
        # 网格与生产器不同帧时把多重集差铺出来：看差异是“没切出牌”还是“切对了读错”
        # ——前者是定位/切牌问题，后者才是分类器问题，两者修法完全不同。
        # （用多重集而非位序：两边都是按 x 排序的空间序，但漏检一张就会整行错位）
        lines.append(f"     GT  : {' '.join(want)}")
        lines.append(f"     网格: {' '.join(sorted(s1))}   相对GT缺{_diff_missing(s1, want)} 多{_diff_missing(want, s1)}")
        lines.append(f"     生产: {' '.join(sorted(s2))}   相对GT缺{_diff_missing(s2, want)} 多{_diff_missing(want, s2)}")
    return hits, len(want)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default=8)
    ap.add_argument("--set", dest="which", choices=("base", "new"), default="base")
    a = ap.parse_args()

    gt = load_entries(a.which)[:a.n]
    grid_det = TencentGridDetector()
    # 生产检测器：与 Engine 同一优先级顺序（YOLO 在位则 YOLO，否则网格）
    prod_det = Engine().get_detector()

    lines = [f"待拆帧 {len(gt)} 张（GT set={a.which} 前 {a.n} 条 verified）",
             f"生产检测器 = {prod_det.__class__.__name__}（Engine 主检测器）；"
             "手牌通道另算（见引擎 get_hand_detector），对比的网格通道 = TencentGridDetector",
             "S1 网格·原图  S2 生产器·原图  S3 生产器·Engine图  S4 门槛后  S5 面板输出"
             "   (括号为张数)", ""]
    tot = [0] * 5
    n_gt = 0
    per_platform = {}
    for e in gt:
        r = run_one_frame(e["path"], e["hand"], e["platform"], grid_det, prod_det, lines)
        if r is None:
            lines.append(f"{os.path.basename(e['path'])[:22]:22} 读不到图，跳过")
            continue
        hits, ng = r
        for i, v in enumerate(hits):
            tot[i] += v
        n_gt += ng
        k = per_platform.setdefault(e["platform"], [0, 0, 0, 0])
        k[0] += ng
        k[1] += hits[0]
        k[2] += hits[1]
        k[3] += hits[4]
    lines.append("")
    lines.append(f"合计逐张命中（GT 共 {n_gt} 张）："
                 + "  ".join(f"S{i + 1}={v}/{n_gt}={100.0 * v / max(1, n_gt):.1f}%"
                             for i, v in enumerate(tot)))
    lines.append("段间净损失："
                 f"S1→S2 {tot[0] - tot[1]} 张（换检测器）   "
                 f"S2→S3 {tot[1] - tot[2]} 张（输入变换）   "
                 f"S3→S4 {tot[2] - tot[3]} 张（置信/牌形/亮度门槛）   "
                 f"S4→S5 {tot[3] - tot[4]} 张（稳定器/互斥/状态门）")
    lines.append(f"参照：`MIN_FACE_BRIGHTNESS={MIN_FACE_BRIGHTNESS}` "
                 f"牌形[{MIN_TILE_ASPECT},{MAX_TILE_ASPECT}] 网格门槛{GRID_MIN_CONF}")
    if a.which == "new":
        lines.append("逐平台（网格 S1 / 生产器 S2 / 面板 S5）：" + "  ".join(
            f"{p} {h1}/{n}={100.0 * h1 / max(1, n):.0f}% | {h2}/{n}={100.0 * h2 / max(1, n):.0f}%"
            f" | {h5}/{n}={100.0 * h5 / max(1, n):.0f}%"
            for p, (n, h1, h2, h5) in sorted(per_platform.items())))
    lines.append("口径提醒：S5 是“单帧冷喂 Engine”（每帧全新 Engine，warmup 从未走完、"
                 "稳定器无历史），连续帧生产环境下换牌/waiting 类的清空不会发生；"
                 "拿 S5 当生产已实现数字会低估。S1/S2 才是同帧可比的检测器上限。")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.join(REPO, "build"), exist_ok=True)
    with open(os.path.join(REPO, "build", f"layer_cost_{a.which}.txt"), "w",
              encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
