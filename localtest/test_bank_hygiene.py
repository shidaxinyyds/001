# -*- coding: utf-8 -*-
"""模板库卫生门禁（库脏会让"分类失误"这个诊断本身失真）。

缺陷原型（本轮在 shushan 上实测坐实）：`localtest/tiles/shushan/legacy_7s.png`
与 `legacy_7s#s4.png` 的牌面其实是**五索**（四角绿 + 中心红），`legacy_5s.png`
其实是三索。它们与 5s 类真模板的核 NCC 高达 0.994，于是 LOFO 一剔掉本帧的 5s
模板，假 7s 就以 0.99 抢走判决 —— 看上去是"分类器分不清 5s/7s"，实际是库脏。
按错的方向去改分类器，会把一个没病的模块改出病。

本文件锁八件事：
1. **跨标签近重复 = 0 对**：同平台、不同标签的两张模板核 NCC >= 0.95 只可能是贴错
   名字。依据是实测分布：同一张牌的不同帧变体 >= 0.985（`pick_variants` 就用这个
   阈值归族），而两个真不同的牌面最高只有 0.79（shushan 4p/5p）。0.95 落在这两者
   之间，不会误报也不会漏报。
2. **不落选（LOFO 剔不到）的模板张数不得增长**（棘轮）：文件名拿不到源帧归属的样本
   进不了任何一折的剔帧集 —— 它们在每一折评测里都白拿“见过的素材”，样本外估计因此
   偏乐观。这类样本必须被显式记在案（张数只许降不许升），否则“LOFO 口径”这个词就是
   假的。归属只拿 `build_platform_bank.frame_of` 一把尺算（以前本文件用
   `re.search(r"f(\\d+)_")`、评测端用 `re.match(r"\\w+_f(\\d+)_")`，同一批样本量出
   17 / 145 两个数）。手绘主库（34 张腾讯模板）不计入这条：它本来就不是从 GT 帧
   割的，“每一折都在场”对它是真样本外知识；但它跟 bank 共用同一个键名空间
   （实测 33 张撞名），所以剔帧必须校 `_core_harvested`，否则手绘模板会被同名的
   收割键连带剔掉，而那条错法不红反涨（被误剔的类会报成“bank 缺类”而豁免）。
3. **provenance 轨不得错位**：`_cores` 与 `_core_keys` 长度不一致时，LOFO 剔的是
   错位的另一个模板，比不剔更坑。这条断言在生产侧已有（`_build_cores` 抛异常），
   这里从库的角度再钉一次，保证 bank 重建后仍然成立。
4. **报样本外数字的 GT 不得混进不落选模板自己的批次**（自确认红线）：2026-10 实测
   库里 145 张模板无帧归属，它们全部来自 public/0 批次。当前报门禁的 GT
   （`gt/shots_b1.json`，91 帧）逐条显式写 `src: public/1`，所以两边不重叠；但
   `eval_new_material` 里 `e.get("src") or public/0` 是个**默认值**：一旦新帧漏写 src，
   那些帧就会静默地按 public/0 去读，而那正是 145 张模板的收割来源 —— LOFO 剔不到
   它们，于是“样本外精度”会变成拿本帧养大的模板自评本帧，报告里一个痕迹都不会留。
5. **收割不得白做（接线不变量）**：本轮实测坐实的缺陷形状 ——
   `build_platform_bank.py` 已经为 `tiles/tencent/` 编译出 `templates_tencent.py`
   （83 张、1.4 MB），但 `EXTRA_BANKS` 里没有登记它：全仓库零引用、文件连 git 都
   没跟踪，腾讯帧一直只拿 34 张手绘主库读数（接上后同分母逐位 1028 -> 1055、
   结构性缺类豁免面 103 -> 61）。「编译了但没接线」和「加载时被 try/except 静默
   吞掉」都不会让任何现有测试变红，只能靠这条门拦。
6. **所有模板必须在同一画布上**：`_build_cores` 那两刀是按 120 高的画布写的，而
   numpy 对越界切片**静默少切**不报错。实测手绘主库 6 张字牌是 80x56 / 77x56，
   切出 64x44 / 61x44 的核，与 88x56 的核在 `matchTemplate` 下不可比：tencent#12
   六枚字牌全在 0.33~0.48 而唯一合规的 7z 拿 0.88。生产已改为先归一到统一画布
   （`canonical_canvas`，与 `extract_face` 同源），本条把「核齐尺」与「尺寸不齐的
   原始素材必须登记在案」钉成门（见 OFF_SPEC_ALLOWED）。
7. **GT 声明的平台必须等于人工水印定名的平台真值**：2026-10 坐实的缺陷 —— 无监督
   分组把 4 帧 JJ 截图归成独立的一组 G33，但**组名**被人工读水印时写成了 tuyou。
   后果不是“某个标签错”而是“这些帧在评测里拿的是别家候选库，而别家的字模躺在本家
   库里”；逐位指标只把它表现为一格 0.50 分的真失误（见 build/tuyou_band.txt、
   build/who_0991.txt）。现在拿 `build/frame_platforms_<tag>.json`（assign_platforms.py
   的产物）逐帧对账，并校“同一 cluster 组不得在 GT 里被拆到两个 style 名下”。
8. **字牌标签必须与牌面颜色一致**：本项目的字牌编号是日麻序 1z東 2z南 3z西 4z北
   **5z白 6z發 7z中**（见 recognition/structural.py 的色判据、sichuan_analyzer.py
   「红中 7z 位于索引 33」），而 MCR/国标口数是「中發白 = 5z6z7z」—— 两者把 中↔白
   整个对调。2026-10 我就按后者“纠”过一次 tencent#12 的标签：图没错、模板没错，
   错的是名字，而它在逐位指标上表示成两格 0.88 / 0.67 的**高分互换**（错标签在拿
   对的模板比谁更像），真失误 2 -> 4。颜色是字牌上最不怕看错的特征（红中必有大片
   红墨、發必有大片绿墨、白板与風牌两者都没有），所以它能当一条不需要看图、也不
   依赖任何打分口径的常驻门（阈值实测分布见 build/honor_color_core.txt）。

变异检验（改完本测试必须手动跑一次，确认它会变红）：
    py -3.10 -X utf8 localtest/test_bank_hygiene.py --mutate
它跑七个变异探针，都必须被对应的门报出：
  - 脏样本：把某平台一张模板的标签改成别类，第 1 条必须报出这一对；
  - 断接线：从 EXTRA_BANKS 里摸掉一个已登记的 bank，第 5 条必须报出它；
  - 匿名样本：凭空多造一张拿不到帧归属的模板，第 2 条棘轮必须报出超限；
  - 漏写 src：把 GT 里若干条的 src 抹成默认值，自确认红线必须报出；
  - 小模板：往某个 bank 塞一张被缩到 80x56 的模板，第 6 条必须报出它。
  - 错身份：把一条 GT 条目改挂到另一个平台名下，第 7 条必须报出它。
  - 错字牌名：把一张红中模板改标成白板（再把白板改标成红中），第 8 条必须两个方向
    都报出它。

运行：py -3.10 -X utf8 localtest/test_bank_hygiene.py
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

import recognition.tencent_grid_detector as tgd  # noqa: E402
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from eval_new_material import (STYLE_OF, load_provenance,
                               lofo_keep_indices)  # noqa: E402
from build_platform_bank import SKIP_DIRS, TILES, frame_of  # noqa: E402

REC_DIR = os.path.join(REPO, "android", "app", "src", "main", "python",
                       "recognition")
# 手绘主库：由 `__init__` 直接按模块名加载，本来就不该出现在 EXTRA_BANKS 里。
MAIN_BANK_MODULE = "templates_data"
ALL_KINDS = {f"{n}{s}" for s in "mps" for n in range(1, 10)} | \
            {f"{n}z" for n in range(1, 8)}

DUP_THR = 0.95          # 跨标签近重复判定线（见模块 docstring 的实测分布依据）
# 不落选（LOFO 剔不到）的模板张数上限。2026-10 把口径收拢到 frame_of 一把尺后实测
# 145 张（jj 38 / weile 39 / tuyou 34 / queshen 17 / shushan 17，tencent 与 zj 为 0），
# 全是 public/0 批次遗留的无前缀 / legacy_* 样本。旧上限写的 20 是用
# `re.search(r"f(\\d+)_")` 数的，只数到 17 张 —— 那是“文件名里根本没有 f<NN>_”，
# 而不是“LOFO 剔不到”，两者差 128 张（无前缀的 `f30_00_1m.png` 有帧号，但那个帧号
# 属于 public/0 批次，与本批帧号撞号，按帧号剔会误删别家模板）。
# 只许降不许升：新增样本一律走 GT -> harvest_from_gt（文件名自带批次前缀 + 帧号），
# 降了就把这个数字一起改小，否则棘轮会松。
# 145 -> 150（2026-10，改判那 4 帧之后）：撤掉途游名下的 44 张 JJ 裁片后，同名牌面
# 的变体槽由 public/0 遗留样本**补位**（tuyou 34 -> 39，其余平台一张未变）。这不是新
# 引入的污染，而是原先被假样本掩盖的素材债现了形：途游真帧从来没有 5s/8s（bank 因此
# 从 26 类掉到 24 类，见 build/tuyou_lost_classes.log）。补回这 5 张的唯一正当途径是
# 给途游钉更多真帧 GT，而不是把这 5 张记成“可接受”。
ORPHAN_CEILING = 150
GT_DIR = os.path.join(HERE, "gt")
# 帧的平台真值表（`localtest/assign_platforms.py` 的产物，tag = 素材目录名）。
# 为什么守卫要依赖它：2026-10 实测坐实，4 帧 JJ 麻将的截图被整组命名成了途游
# （`build/frame_clusters_1/labels.txt` 里 G33 那组写错组名），于是**评测在按错的
# 候选库给它们打分**，途游库里还躺着一张 JJ 的字模（`8m#3 <- b1_f13_12_8m.png`）。
# 当时逐位指标看起来只是“tuyou 有 1 格真失误”，按分类缺陷去查会一路查错方向。
# 这张表是身份链上唯一的人工真值（读原分辨率水印带定名），所以GT 必须对它对齐。
PLATFORM_TRUTH_DIR = os.path.join(REPO, "build")
# 报 97% 门禁样本外数字的 GT：逐条必须有显式 src，且不得指向 public/0。
LOFO_GT_FILES = ["shots_b1.json"]
# 已知属于 public/0 旧批次的 GT：它们评的是样本内读数（那批帧正是不落选模板的收割
# 来源），不得被拿去当 LOFO 样本外数字。名单里的文件必须还在 —— 它被删了却没从
# 这里销掉，等于守卫在为一个不存在的口径写理由。
LEGACY_GT = {"shots.json": "腾讯底座 37 帧，手绘主库同源",
             "new_shots.json": "public/0 新素材帧，不落选模板的收割来源",
             # 用户真机 20 帧：P2-A/P2-B 的修法（gd_hz 牌集、错配纠正）就是按这批
             # 帧的错读形状定的，拿它报“样本外准确率”是自夸。它能钉的是**不回退**
             # （见 localtest/test_multi_hand_guard.py），不是泛化能力。
             "shots_multi.json": "用户真机 20 帧（六平台），修法同源=样本内，只当回归钉"}
DEFAULT_SRC = os.path.relpath(os.path.join(REPO, "public", "0"), REPO).replace("\\", "/")
# 手绘主库模板没有收割文件名：它们不是从任何 GT 帧割的，拿键名去 provenance 里
# 查只会“借”到同名的收割样本（见 bank_instances 的 docstring），所以单独标一个值。
MAIN_BANK_SRC = "(手绘主库)"
# 统一画布与核尺寸：直接引生产常量，不在这里重抄一遍 —— 重抄的那份改了而生产
# 没改，守卫就是在为一个不存在的几何写绿灯。
FACE_HW = (tgd.FACE_H, tgd.FACE_W)
CORE_HW = (tgd.CORE_H, tgd.CORE_W)
# 盘上已知尺寸不齐的**原始素材**（实测手绘主库的 6 张字牌是 80x56 / 77x56）。
# 它们现在靠 `canonical_canvas` 归一进统一画布，所以本名单不是“打分不可比”的
# 报警线，而是新素材的登记门：静默归一一张本该重割的模板，等于把配准误差
# 当成牌面特征学进库里。重割完必须把名单清空（下面有断言拦过期的名单）。
OFF_SPEC_ALLOWED = frozenset(("tencent", k) for k in ("1z", "2z", "3z", "4z", "5z", "6z"))
# 字牌色判据（第 8 条）：在**生产真正用于匹配的核**上量的面积占比（高饱和像素按色相
# 归红/绿，见 build/honor_color_core.txt 的逐模板数据）：
#   该“有”的一律 > 本阈值：7z 红墨实测最低 0.087（weile f03_01），6z 绿墨最低 0.224；
#   该“无”的一律 < 本阈值：1z~4z 与真 5z 红墨全部 0.000，绿墨最大 0.004。
# 0.04 落在两者之间，紧的一边留 2.2 倍余量，松的一边留 10 倍 —— 不是拍出来的数。
HONOR_INK_THR = 0.04
# 必须有大片红墨 / 绿墨 / 两者都必须没有的类（日麻序：5z=白板、6z=發、7z=红中）
HONOR_NEED_RED = {"7z"}
HONOR_NEED_GREEN = {"6z"}
HONOR_NEED_PLAIN = {"1z", "2z", "3z", "4z", "5z"}


def bank_instances(det):
    """[(style, label, key, source_file, core, harvested)]：只取已接线的平台模板。

    `harvested` = 这张实例是不是从 GT 帧收割进 bank 的（`det._core_harvested`）。
    手绘主库固定 False。它必须单独拿出来，不能拿“键名在不在 provenance 里”代
    替：实测 34 张手绘腾讯模板里有 33 张的键名与 b1 收割 bank 的键名撞车，只要
    拿键名去查溯源，手绘模板就会“借”到一张不属于它的收割文件名，于是“这张有不
    有帧归属”这个诊断从根上失真。
    """
    prov = load_provenance()
    rows = []
    src = {}
    for style in prov:
        p = os.path.join(HERE, "tiles", style, "provenance.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fp:
                src[style] = json.load(fp)
    for (lbl, st, _btn, plain, _gb, _gp), key, harv in zip(
            det._cores, det._core_keys, det._core_harvested):
        if st not in prov:
            continue                # 内置 tencent 主库不参与 bank 卫生断言
        rows.append((st, lbl.split("#")[0], key,
                     src.get(st, {}).get(key, "?") if harv else MAIN_BANK_SRC, plain,
                     bool(harv)))
    return rows


def cross_label_dups(rows):
    """[(ncc, style, labA, keyA, fileA, labB, keyB, fileB)]：同平台不同标签的近重复对。"""
    bad = []
    for i, (s1, l1, k1, f1, c1, _h1) in enumerate(rows):
        for s2, l2, k2, f2, c2, _h2 in rows[i + 1:]:
            if s1 != s2 or l1 == l2:
                continue
            v = float(cv2.matchTemplate(c1, c2, cv2.TM_CCOEFF_NORMED).max())
            if v >= DUP_THR:
                bad.append((v, s1, l1, k1, f1, l2, k2, f2))
    return bad


def honor_ink(core):
    """(红墨面积占比, 绿墨面积占比)：只在高饱和像素里按色相分，再除以核面积。

    用“占整张核的面积”而不是“占彩色像素的份额”：后者在彩色像素极少时是噪声
    （風牌的牌面偏黄、JPEG 压缩会在白底上造出几百个高饱和像素，它们归红还是
    归绿完全随机，份额能刷到 0.9 而红中反而只占 0.38）。面积没有这个毛病：
    没墨就是 0.000，有字就是 0.1 量级（实测见 build/honor_color_core.txt）。
    """
    hsv = cv2.cvtColor(np.asarray(core, np.uint8), cv2.COLOR_BGR2HSV)
    h, s = hsv[..., 0], hsv[..., 1]
    col = s > 90
    area = max(1, int(h.size))
    red = float((((h < 12) | (h > 168)) & col).sum()) / area
    grn = float(((h >= 35) & (h <= 85) & col).sum()) / area
    return red, grn


def honor_color_offenders(rows):
    """[(style, label, key, src, red, grn, 违反了哪条)]：标签与牌面颜色对不上的模板。

    只管字牌（数牌的红/绿是牌面图案的一部分，一筒就是大红，拿颜色判不了）。
    拿 rows 作参是为了能让变异探针喂伪造的标签；阈值与分类面都在常量区，
    不在这里重抄。抽成函数而不是内联断言，是因为“错字牌名”与“错平台名”
    同样需要能在不弄脏盘上数据的前提下验证门会响。
    """
    bad = []
    for st, lbl, key, fn, core, _harv in rows:
        if not lbl.endswith("z"):
            continue
        red, grn = honor_ink(core)
        if lbl in HONOR_NEED_RED and red < HONOR_INK_THR:
            bad.append((st, lbl, key, fn, red, grn, "7z=红中，核上却没有红墨"))
        elif lbl in HONOR_NEED_GREEN and grn < HONOR_INK_THR:
            bad.append((st, lbl, key, fn, red, grn, "6z=發，核上却没有绿墨"))
        elif lbl in HONOR_NEED_PLAIN and (red >= HONOR_INK_THR or
                                         grn >= HONOR_INK_THR):
            bad.append((st, lbl, key, fn, red, grn,
                        f"{lbl} 应是黑墨/空面（風牌或白板），却带了成片的"
                        f"{'红' if red >= HONOR_INK_THR else '绿'}墨"))
    return bad


def wired(extra_banks):
    """EXTRA_BANKS -> {模块名: 风格}。抽成函数是为了能喂变异后的清单。"""
    return {m.rsplit(".", 1)[-1]: s for m, s in extra_banks}


def unattributed(rows):
    """[(style, key, file)]：收割来但拿不到源帧归属的模板（= 不落选）。

    尺子只有一把（`build_platform_bank.frame_of`）：在这里算出多少，`load_provenance`
    就剔不到多少，评测报告头上印的“不落选模板”也是同一个数。手绘主库不参与：
    它本来就不是从 GT 帧割的，“每一折都在场”对它是真样本外知识，不是口径漏洞。
    抽成函数是为了能让变异探针喂伪造的 rows。
    """
    return [(st, key, fn) for st, _l, key, fn, _c, harv in rows
            if harv and frame_of(fn) is None]


def gt_entries(name):
    with open(os.path.join(GT_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)["shots"]


def self_confirm_offenders(entries, where):
    """[(where, style, 帧, src)]：会被当成 public/0 帧去读的 GT 条目。

    `src` 缺省不是“没写”而是“默认 public/0”（`eval_new_material` 里
    `e.get("src") or public/0`）：那批帧是无帧归属模板的收割来源，它们出现在报
    样本外数字的 GT 里就是自确认。写显式 `public/0` 同样拦：要评旧批次可以，但
    得先把它归到 LEGACY_GT 名单里说清楚，不能混在报 97% 的那里。
    """
    bad = []
    for e in entries:
        src = e.get("src")
        got = DEFAULT_SRC if src is None else str(src).replace("\\", "/").strip("./")
        if got.rstrip("/") == DEFAULT_SRC:
            bad.append((where, e["style"], e["frame"],
                        "缺省(静默回到 public/0)" if src is None else src))
    return bad


def platform_truth(src_dir, cache={}):
    """素材目录 -> {文件名: {platform, group}}（人工水印定名的平台真值表）。

    tag 用素材目录名（`public/1` -> `1`），与 `assign_platforms.py` 的命名同一把尺；
    取不到时返回 None = **调用方必须报错而不是跳过**：“真值表不在”与“真值表里
    没有这一帧”是两种不同的失效，后者把帧评到错的候选库里时不会自己浮现。
    """
    tag = os.path.basename(str(src_dir or "").replace("\\", "/").rstrip("/")) or "root"
    if tag not in cache:
        p = os.path.join(PLATFORM_TRUTH_DIR, f"frame_platforms_{tag}.json")
        cache[tag] = (json.load(open(p, encoding="utf-8"))
                      if os.path.exists(p) and tag else None)
    return cache[tag]


def identity_offenders(entries, where):
    """[(where, style, 帧, 文件, 真值平台, 分组)]：GT 声明的平台与真值表不一致的帧。

    只校两件事，不把探针当期值：
      1) GT 的 `style` 必须等于该帧在真值表里的 `platform`（经 STYLE_OF 归一）；
      2) 同一个 cluster 组（看起来就是同一处来源的截图）不得在 GT 里被拆到两个
         style 名下 —— 拆了必有一个错。
    探针风格（probe_style）**不参与判定**：它从原理上认不出没挂 bank 的平台，本批
    与人工真值的一致率只有 65.4%（见 build/frame_platforms_1.txt），拿它当门就是把
    “自证式身份认定”写进守卫。
    """
    bad, by_group = [], {}
    for e in entries:
        truth = platform_truth(e.get("src"))
        if truth is None:
            bad.append((where, e["style"], e["frame"], e.get("file", "?"),
                        "真值表缺失（先跑 assign_platforms.py）", ""))
            continue
        t = truth.get(os.path.basename(e.get("file", "")))
        if t is None:
            bad.append((where, e["style"], e["frame"], e.get("file", "?"),
                        "不在分组真值表里", ""))
            continue
        want = STYLE_OF.get(e["style"])
        if want is None:
            bad.append((where, e["style"], e["frame"], e.get("file", "?"),
                        "style 未登记在 eval_new_material.STYLE_OF", ""))
            continue
        if t["platform"] != want:
            bad.append((where, e["style"], e["frame"], e.get("file", "?"),
                        t["platform"], t.get("group", "")))
        by_group.setdefault(t.get("group", ""), set()).add(e["style"])
    for grp, styles in sorted(by_group.items()):
        if len(styles) > 1:
            bad.append((where, "/".join(sorted(styles)), 0, f"组 {grp}",
                        "同组被拆到多个 style", grp))
    return bad


def raw_off_spec(banks):
    """[(style, key, 'WxH')]：原始模板里不在统一画布上的那些。

    只看库的**本体**（主库 `templates_bgr` + 各 bank 模块的 `TEMPLATES_BGR`），不看
    `_cores`：核已经被生产归一过了，从核上量永远是齐的，量不出素材债。
    抽成函数并允许喂参数，是为了让变异探针能造一张“新塞进来的小模板”。
    """
    out = []
    for style, tpls in banks:
        for key, tpl in tpls.items():
            shape = tuple(np.asarray(tpl).shape[:2])
            if shape != FACE_HW:
                out.append((style, key, f"{shape[1]}x{shape[0]}"))
    return out


class BankHygiene(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.det = TencentGridDetector()
        cls.rows = bank_instances(cls.det)
        cls.banks = [("tencent", cls.det.templates_bgr)] + [
            (style, importlib.import_module(mod).TEMPLATES_BGR)
            for mod, style in tgd.EXTRA_BANKS]

    def test_core_tracks_aligned(self):
        self.assertEqual(len(self.det._cores), len(self.det._core_keys),
                         "_cores 与 _core_keys 长度错位，LOFO 会剔错模板")
        self.assertEqual(len(self.det._cores), len(self.det._core_harvested),
                         "_cores 与 _core_harvested 长度错位，LOFO 会把不该剔的剔掉")
        self.assertTrue(self.rows, "一个 bank 模板都没扫到（provenance 或加载链断了）")

    def test_no_cross_label_near_duplicate(self):
        bad = cross_label_dups(self.rows)
        self.assertFalse(
            bad, "跨标签近重复模板（至少一张贴错了名字）：\n  " + "\n  ".join(
                f"{s} {la}/{ka}({fa}) <-> {lb}/{kb}({fb})  NCC={v:.3f}"
                for v, s, la, ka, fa, lb, kb, fb in sorted(bad, reverse=True)))

    def test_honor_labels_match_tile_colors(self):
        """第 8 条：字牌标签必须与牌面颜色一致（日麻序 5z白 / 6z發 / 7z中）。

        坐实过的缺陷：把 tencent#12 的 中/白 按 MCR 序（中發白=5z6z7z）“纠正”了一遍，
        于是收割出 `tencent/b1_f12_10_5z.png`（其实是红中）与 `b1_f12_12_7z.png`（其实是
        白板）两张真机模板。它们不会触发第 1 条（跨标签近重复）—— 因为库里本来就有
        正确的 5z/7z 手绘模板，错的是新增的那两张；而它们会在**别的帧**的每一折里以错
        身份抢判（手绘模板 harvested=False，LOFO 永远剔不到，错名字就一直生效）。
        """
        bad = honor_color_offenders(self.rows)
        self.assertFalse(
            bad,
            f"{len(bad)} 张字牌模板的标签与牌面颜色矛盾（本项目口径：1z東 2z南 3z西 "
            f"4z北 5z白 6z發 7z中）：\n  " + "\n  ".join(
                f"{s}/{k} key={key} <- {fn}  红墨={red:.3f} 绿墨={grn:.3f}  {why}"
                for s, k, key, fn, red, grn, why in sorted(bad)) +
            "\n  先定案“是牌面看错了还是编号口径用错了”，再改 GT 重收割；"
            "直接改标签而不重跑 harvest/bank，盘上样本与库仍会分属两个名字。")

    def test_orphan_templates_do_not_grow(self):
        """第 2 条：不落选（LOFO 剔不到）张数只许降不许升，否则评测口径会静默变松。"""
        orphans = unattributed(self.rows)
        per_style = {}
        for st, _k, _f in orphans:
            per_style[st] = per_style.get(st, 0) + 1
        self.assertLessEqual(
            len(orphans), ORPHAN_CEILING,
            f"LOFO 剔不到的模板从 {ORPHAN_CEILING} 涨到 {len(orphans)} 张"
            f"（按平台：{per_style}）：这些样本在每一折评测里都算见过的素材，"
            f"“样本外估计”会因此偏乐观而报告不会红。新增样本必须经 GT 收割"
            f"（文件名自带批次前缀 + 帧号），或把确实复核过的补进 ORPHAN_CEILING "
            f"并说明理由。降了也要把上限改小，否则棘轮就松了。\n"
            + "\n".join(f"  {s}/{k} <- {f}" for s, k, f in sorted(orphans)))

    def test_templates_share_one_canvas(self):
        """第 6 条：所有核必须在同一画布上，且尺寸不齐的原始素材必须登记在案。

        坐实过的缺陷：手绘主库 6 张字牌是 80x56 / 77x56，`_build_cores` 那两刀对它们
        **静默少切**成 64x44 / 61x44，与其余 88x56 的核在 `matchTemplate` 下不可比；
        同一帧六枚字牌全在 0.33~0.48 且正确类进不了前 5，而唯一尺寸合规的 7z 拿 0.88
        （见 build/main_bank_sizes.txt、build/audit_tc12.txt）。归一后逐位 8/13 -> 11/13
        （见 build/main_bank_geom.txt、build/prod_geom_check.txt）。
        """
        bad_core = [(st, lbl, f"{p.shape[1]}x{p.shape[0]}")
                    for (lbl, st, _b, p, _gb, _gp) in self.det._cores
                    if p.shape[:2] != CORE_HW]
        self.assertFalse(
            bad_core, f"有 {len(bad_core)} 张模板的 plain 核不在 "
                      f"{tgd.CORE_W}x{tgd.CORE_H} 上：`matchTemplate` 的分数在不同类之间"
                      f"不可比，小核还会因为滑窗更多而虚高（等于尺寸不齐的模板在抢判）。\n  "
                      + "\n  ".join(f"{s}/{k} 核={v}" for s, k, v in bad_core[:10]))
        off = raw_off_spec(self.banks)
        stray = [r for r in off if (r[0], r[1]) not in OFF_SPEC_ALLOWED]
        self.assertFalse(
            stray, f"新出现 {len(stray)} 张不在统一画布（{tgd.FACE_W}x{tgd.FACE_H}）上、"
                   f"也没登记在 OFF_SPEC_ALLOWED 里的模板：\n  "
                   + "\n  ".join(f"{s}/{k} 实际={v}" for s, k, v in stray[:10])
                   + "\n  修法：重割成统一尺寸（走 GT -> harvest_from_gt），"
                     "或确认它就是旧素材并写理由登记进 OFF_SPEC_ALLOWED。")
        stale = OFF_SPEC_ALLOWED - {(s, k) for s, k, _v in off}
        self.assertFalse(
            stale,
            f"OFF_SPEC_ALLOWED 里有 {len(stale)} 张已不再尺寸不齐（已被重割）：名单过期了，"
            f"把它们从名单销掉（否则守卫在为并不存在的素材债开绿灯）。\n  "
            + "\n  ".join(f"{s}/{k}" for s, k in sorted(stale)))
        print(f"\n  [几何] 核实例 {len(self.det._cores)} 张全部 "
              f"{tgd.CORE_W}x{tgd.CORE_H}；原始素材尺寸不齐 {len(off)} 张（均已登记）")

    def test_lofo_drop_leaves_the_hand_written_main_bank_alone(self):
        """第 3 条的延伸：把评测那条剔除规则在盘上全部溯源上过一遍，手绘主库不得受伤。

        为什么要拿“撞名会不会发生”当断言而不是只写注释：误剔的那一类会被报成
        “bank 缺类”而从分母里豁免，所以它不红反涨 —— 不能说“剔多了只是保守”。
        """
        prov = load_provenance()
        styles = [r[0] for r in self.rows]
        keys = [r[2] for r in self.rows]
        harv = [r[5] for r in self.rows]
        fake = [("", s) for s in styles]          # lofo_keep_indices 只用得到 c[1]
        n = len(self.rows)
        idx = set(range(n))
        main_rows = [r for r in self.rows if not r[5]]      # 带 numpy 核，不能入集
        self.assertFalse([r for r in main_rows if r[3] != MAIN_BANK_SRC],
                         "手绘模板被借用了 bank 的收割文件名：归属诊断从根上失真")
        drop_main = drop_bank = naive_main = 0
        for style, per_frame in prov.items():
            for fr, drop in sorted(per_frame.items()):
                gone = idx - set(lofo_keep_indices(fake, keys, harv, style, drop))
                drop_main += sum(1 for i in gone if not harv[i])
                drop_bank += sum(1 for i in gone if harv[i])
                # 旧规则（只按键名剔，不校收割位）会伤到多少张手绘模板：
                # 这一量就是 `_core_harvested` 这道校验的实际作用面。
                for k in drop:
                    naive_main += sum(1 for r in main_rows if r[2] == k and r[0] == style)
        self.assertEqual(drop_main, 0,
                         f"现行剔除规则仍会剔掉 {drop_main} 张手绘主库模板："
                         f"那些帧的“结构性缺类”豁免全部不可信")
        self.assertGreater(drop_bank, 0,
                           "LOFO 一折都没剔到模板：溯源链断了（或根本没开 --lofo）")
        self.assertGreater(naive_main, 0,
                           "手绘主库与 bank 的键名已不再重叠：`_core_harvested` 这道校验以"
                           "及本断言都可以删了（删前要把两个数字重新量一遍）")
        print(f"\n  [归属] 每折平均剔 bank {drop_bank // max(1, sum(len(v) for v in prov.values()))}"
              f" 张、剔手绘 0 张；若无 harvested 校验会连带误剔 {naive_main} 张手绘模板")


class GtBatchAttribution(unittest.TestCase):
    """第 4 条：报样本外数字的 GT 不得与不落选模板的收割批次重叠。"""

    def test_lofo_gt_frames_are_attributed_to_a_distinct_batch(self):
        offenders = []
        for name in LOFO_GT_FILES:
            p = os.path.join(GT_DIR, name)
            self.assertTrue(os.path.exists(p),
                            f"{name} 是指定用来报 97% 门禁的 GT，它不在了："
                            f"要换批次就同时改 LOFO_GT_FILES，不要默默拿旧数字当真")
            offenders += self_confirm_offenders(gt_entries(name), name)
        self.assertFalse(
            offenders,
            f"报 LOFO 样本外数字的 GT 里有 {len(offenders)} 帧会被按 public/0 去读："
            f"库里 {ORPHAN_CEILING} 张无帧归属模板全部收割于 public/0，它们在每一折"
            f"都不会被剔，于是这些帧上的“样本外精度”实际是模板自评本帧。"
            f"\n修法：给这些 GT 条目显式写 src: public/1（或新批次目录）。\n  "
            + "\n  ".join(f"{w} {s}#{f} src={d}" for w, s, f, d in offenders[:15]))

    def test_gt_platform_matches_manual_watermark_truth(self):
        """第 7 条：GT 帧声明的平台必须等于分组真值表里的人工定名。

        坐实的缺陷形状：4 帧 JJ 截图被整组命名成途游（G33 组名错），于是它们拿着
        途游的候选库被打分、而途游 bank 里躺着一张 JJ 字模。它在逐位指标上只表示成
        “tuyou 1 格真失误”，完全看不出错在身份；而把帧从一个平台换成另一个平台时，
        重编译/重收割/重建 bank 三步里任何一步漏跑，GT 与 bank 就会分属两个平台。
        """
        offenders = []
        for name in LOFO_GT_FILES:
            offenders += identity_offenders(gt_entries(name), name)
        self.assertFalse(
            offenders,
            f"报 97% 门禁的 GT 里有 {len(offenders)} 处与人工平台真值不一致：这些帧的"
            f"候选库是选错的，它们身上的“失误”不是分类缺陷而是身份缺陷（修方向会错一路）。\n"
            f"  修法：看原分辨率水印带定身份 -> 改 build/frame_clusters_*/labels.txt 的"
            f"组名 -> 重跑 assign_platforms.py -> 改 gt/labels_*.txt 的平台名/表号 ->"
            f" 重编译 GT + 重收割 + 重建 bank。探针不能当身份真值。\n  "
            + "\n  ".join(f"{w} 声明={s}#{f} 真值={p} 组={g} file={v}"
                          for w, s, f, v, p, g in offenders[:15]))

    def test_gt_register_is_neither_stale_nor_incomplete(self):
        """归属名单要跟上盘上的 GT：过期或漏登记都是拿名单当口误。"""
        files = {f for f in os.listdir(GT_DIR)
                 if f.endswith(".json") and os.path.isfile(os.path.join(GT_DIR, f))}
        gone = sorted(n for n in set(LOFO_GT_FILES) | set(LEGACY_GT) if n not in files)
        self.assertFalse(
            gone, f"归属名单里挂着已不存在的 GT（名单过期就是在骗人）：{' '.join(gone)}")
        stray = sorted(files - set(LOFO_GT_FILES) - set(LEGACY_GT))
        self.assertFalse(
            stray, f"新 GT 既没进 LOFO_GT_FILES 也没进 LEGACY_GT：{' '.join(stray)}\n"
                   "  拿它报样本外数字就登记到 LOFO_GT_FILES（必须逐条写 src）；"
                   "它是旧批次读数就登记到 LEGACY_GT 并写明它评的是样本内。")
        overlap = sorted(set(LOFO_GT_FILES) & set(LEGACY_GT))
        self.assertFalse(overlap, f"一份 GT 同时被当样本外门禁和旧批次读数：{overlap}")


class BankWiring(unittest.TestCase):
    """第 5 条：收割 -> 编译 -> 登记 -> 真的加载上了，四步每一步都可能断。"""

    @classmethod
    def setUpClass(cls):
        cls.det = TencentGridDetector()

    def test_generated_bank_modules_are_registered(self):
        w = wired(tgd.EXTRA_BANKS)
        gen = {f[:-3] for f in os.listdir(REC_DIR)
               if f.startswith("templates_") and f.endswith(".py")} - {MAIN_BANK_MODULE}
        un = sorted(gen - set(w))
        self.assertFalse(
            un, "编译出来但 EXTRA_BANKS 没登记的 bank 模块（这些模板永远不会被加载，"
                f"等于白收割）：{' '.join(un)}\n"
                "  修法：在 recognition/tencent_grid_detector.py 的 EXTRA_BANKS 里加一行，"
                "并跑一次 LOFO 评测确认不回退。")
        ghost = sorted(set(w) - gen)
        self.assertFalse(
            ghost, f"EXTRA_BANKS 登记了不存在的模块（加载端只会 print，不会报错）："
                   f"{' '.join(ghost)}")

    def test_registered_style_matches_module_name(self):
        """(模块名, 风格) 这个二元组会漂移：风格名写错 = 平台白名单与探针路由全部对不上，
        而模板确实加载了，所以评测只会看起来“莫名地读不准”。"""
        for mod_name, style in tgd.EXTRA_BANKS:
            self.assertEqual(style, mod_name.rsplit(".", 1)[-1][len("templates_"):],
                             f"{mod_name} 的风格名与模块名不匹配")

    def test_registered_banks_all_actually_load(self):
        """加载端是 `try: ... except Exception: print(...)`：一个 bank 坏了不会崩，
        只是静默少一批模板。所以下面不能只信“登记了”，要逐模块 import 后对张数。"""
        expect, bad = 0, []
        for mod_name, _style in tgd.EXTRA_BANKS:
            mod = importlib.import_module(mod_name)   # 这里报错 = 模块真坏了
            tpls = getattr(mod, "TEMPLATES_BGR", None)
            if not tpls:
                bad.append(f"{mod_name}: 没有非空的 TEMPLATES_BGR")
                continue
            expect += len(tpls)
            for k in tpls:
                if k.split("#")[0] not in ALL_KINDS:
                    bad.append(f"{mod_name}: 模板键 {k!r} 不是合法牌类")
        self.assertFalse(bad, "登记的 bank 模块内容不合法：\n  " + "\n  ".join(bad[:10]))
        self.assertEqual(
            len(self.det._cores), len(self.det.templates_bgr) + expect,
            f"检测器实际加载 {len(self.det._cores)} 核 != 主库 "
            f"{len(self.det.templates_bgr)} + 各登记 bank {expect}："
            f"某个 bank 在 __init__ 的 try/except 里被静默吞掉了")

    def test_harvest_dirs_are_either_wired_or_justified(self):
        """每个 `localtest/tiles/<d>/` 要么已接线，要么在 SKIP_DIRS 里写了理由。"""
        dirs = {d for d in os.listdir(TILES) if os.path.isdir(os.path.join(TILES, d))}
        w = set(wired(tgd.EXTRA_BANKS).values())
        stray = sorted(dirs - w - set(SKIP_DIRS))
        self.assertFalse(
            stray, f"收割目录既没接线也没写弃用理由：{' '.join(stray)}\n"
                   "  要么补进 EXTRA_BANKS，要么在 build_platform_bank.SKIP_DIRS 里"
                   "给出具体理由（不能只写“旧数据”）。")
        # provenance.json 是 LOFO 的唯一依据：它有而 bank 没接线，说明评测在算
        # 一套根本不存在的模板，剔帧剔了个寂寞。
        prov = {d for d in dirs
                if os.path.exists(os.path.join(TILES, d, "provenance.json"))}
        self.assertFalse(sorted(prov - w),
                         f"有 provenance 却没接线的风格：{' '.join(sorted(prov - w))}")
        self.assertFalse(sorted(set(SKIP_DIRS) - dirs),
                         "SKIP_DIRS 里挂着已不存在的目录（名单过期就是在骗人）："
                         f"{' '.join(sorted(set(SKIP_DIRS) - dirs))}")


def mutate_check():
    """变异检验：克隆一张模板、把副本贴成别类的标签，第 1 条断言必须报出它。

    这就是 `legacy_7s.png`（其实是五索）当初造出来的缺陷形状：同一张牌面以两个
    不同标签躺在库里。必须确认门能拦住它，否则“0 对”这个绿灯只是没测到而已。
    """
    det = TencentGridDetector()
    rows = bank_instances(det)
    tgt = next(r for r in rows if r[0] == "shushan" and r[1] == "5s")
    clone = (tgt[0], "7s", tgt[2] + "#mutant", tgt[3] + "#mutant", tgt[4], tgt[5])
    poisoned = rows + [clone]
    hits = []
    for i, (s1, l1, k1, _f1, c1, _h1) in enumerate(poisoned):
        for s2, l2, k2, _f2, c2, _h2 in poisoned[i + 1:]:
            if s1 != s2 or l1 == l2:
                continue
            if float(cv2.matchTemplate(c1, c2, cv2.TM_CCOEFF_NORMED).max()) >= DUP_THR:
                hits.append((s1, l1, k1, l2, k2))
    ok = any(clone[2] in (h[2], h[4]) for h in hits)
    print(f"[mutate] 克隆 5s 冒充 7s 后报出 {len(hits)} 对，命中被污染副本：{ok}")
    return 0 if ok else 1


def mutate_wiring():
    """变异检验（接线门）：把已登记的一个 bank 从 EXTRA_BANKS 里摸掉。

    这正是 `templates_tencent.py` 历史上的状态：文件在、模板在、主库也能跑，
    只是没人引用。必须确认接线门能报出它，否则“现在全绿”只是没测到而已。
    """
    victim = next(m for m, _s in tgd.EXTRA_BANKS if m.endswith("templates_tencent"))
    saved = tgd.EXTRA_BANKS
    tgd.EXTRA_BANKS = tuple(x for x in saved if x[0] != victim)
    try:
        w = wired(tgd.EXTRA_BANKS)
        gen = {f[:-3] for f in os.listdir(REC_DIR)
               if f.startswith("templates_") and f.endswith(".py")} - {MAIN_BANK_MODULE}
        un = sorted(gen - set(w))
    finally:
        tgd.EXTRA_BANKS = saved
    ok = victim.rsplit(".", 1)[-1] in un
    print(f"[mutate] 摸掉 {victim} 后门报出：{un}（命中被断开的那个：{ok}）")
    return 0 if ok else 1


def mutate_unattributed():
    """变异检验（不落选棘轮）：往库里多造一张拿不到帧归属的模板。

    这就是“绕过 GT 直接往 tiles/<style>/ 扔图”的缺陷形状：图能加载、能匹配、
    评测也不会报错，只是它永远不会被任何一折剔掉。必须确认棘轮能报超限，
    否则“145 张”这个绿灯只是当前快照而已。探针同时校基准值与加料后的值：
    若盘上已经超上限（基准 > 上限），这一支也会报红 —— 那是真该红，不能当成
    “探针坏了”忽略。
    """
    det = TencentGridDetector()
    rows = bank_instances(det)
    base = len(unattributed(rows))
    attributed = [r for r in rows if r[5] and frame_of(r[3]) is not None]
    if not attributed:
        print("[mutate] 库里一张带帧归属的收割样本都没有，无法造变异体")
        return 1
    tgt = attributed[0]
    poisoned = rows + [(tgt[0], tgt[1], tgt[2] + "#anon", "anon_00_1m.png", tgt[4], True)]
    now = len(unattributed(poisoned))
    ok = base <= ORPHAN_CEILING < now
    print(f"[mutate] 造一张匿名样本后不落选张数 {base} -> {now}（上限 {ORPHAN_CEILING}），"
          f"棘轮报超限：{ok}")
    return 0 if ok else 1


def mutate_self_confirm():
    """变异检验（自确认红线）：把报门禁的 GT 抹掉几条 src（静默回到 public/0）。

    这是新增帧时最可能的笔误：复制条目改了文件名却没改 src。图在 public/0 里
    名字不撞时会被“读到错帧”拦下（报告显示读不到），名字撞时就会安安静静地
    拿旧帧当新帧评，而那批帧又正好有不落选模板在场。"""
    name = LOFO_GT_FILES[0]
    entries = [dict(e) for e in gt_entries(name)]
    clean = self_confirm_offenders(entries, name)
    for e in entries[:5]:
        e.pop("src", None)
    bad = self_confirm_offenders(entries, name)
    ok = not clean and len(bad) == 5
    print(f"[mutate] 抹掉 {name} 前 5 条的 src 后红线报出 {len(bad)} 帧（改动前 {len(clean)}），"
          f"拦下：{ok}")
    return 0 if ok else 1


def mutate_canvas():
    """变异检验（第 6 条）：往一个已接线的 bank 里塞一张被缩到 80x56 的模板。

    这就是手绘主库那 6 张字牌历史上的形状：图能加载、能匹配、生产不报错，只是
    它切出来的核比别人小一号，在 `matchTemplate` 下与别人的分数不可比（而且小核
    因为滑窗更多会虚高，是在抢判）。必须确认门能报出它，否则“核全部齐尺”这个
    绿灯只是当前快照而已。
    """
    det = TencentGridDetector()
    banks = [("tencent", det.templates_bgr)] + [
        (style, importlib.import_module(mod).TEMPLATES_BGR)
        for mod, style in tgd.EXTRA_BANKS]
    base = [r for r in raw_off_spec(banks) if (r[0], r[1]) not in OFF_SPEC_ALLOWED]
    style, tpls = next((s, t) for s, t in banks if s != "tencent")
    key = next(iter(tpls))
    shrunk = dict(tpls)
    shrunk[key] = cv2.resize(np.asarray(tpls[key], np.uint8),
                             (tgd.FACE_W, 56), interpolation=cv2.INTER_AREA)
    now = [r for r in raw_off_spec([(style, shrunk)] +
                                   [b for b in banks if b[1] is not tpls])
           if (r[0], r[1]) not in OFF_SPEC_ALLOWED]
    hit = any(s == style and k == key for s, k, _v in now)
    ok = not base and hit and len(now) == len(base) + 1
    print(f"[mutate] 把 {style}/{key} 缩到 {tgd.FACE_W}x56 后，未登记的尺寸不齐模板 "
          f"{len(base)} -> {len(now)}（命中被缩的那张：{hit}，拦下：{ok}）")
    return 0 if ok else 1


def mutate_identity():
    """变异检验（第 7 条）：把一条 GT 条目改挂到另一个平台名下。

    这就是 G33 那 4 帧的缺陷形状：截图本身没错、标签也没错，错的是“它是哪个
    平台的”。必须确认门能报出它，否则“GT 与真值对齐”这个绿灯只是人工看图才发现
    一次而已。探针会同时跑“原始”与“改后”两个口径：原始口径已经脏的话这一支也会报
    红 —— 那是真该红，不能当成探针坏了忽略。
    """
    name = LOFO_GT_FILES[0]
    entries = [dict(e) for e in gt_entries(name)]
    clean = identity_offenders(entries, name)
    tgt = next(i for i, e in enumerate(entries)
               if e["style"] in STYLE_OF and STYLE_OF[e["style"]])
    alt = next(v for k, v in STYLE_OF.items() if k != entries[tgt]["style"])
    entries[tgt]["style"] = alt
    bad = identity_offenders(entries, name)
    hit = any(s == alt and f == entries[tgt]["frame"] for _w, s, f, _v, _p, _g in bad)
    ok = not clean and hit
    print(f"[mutate] 把 {name} 的 #{entries[tgt]['frame']:02d} 从 "
          f"{gt_entries(name)[tgt]['style']} 改挂成 {alt} 后报出 {len(bad)} 处"
          f"（改动前 {len(clean)}，命中被改帧：{hit}，拦下：{ok}）")
    return 0 if ok else 1


def mutate_honor_name():
    """变异检验（第 8 条）：把字牌模板在 5z（白板）与 7z（红中）之间对调名字。

    这就是 2026-10 那次“按 MCR 序纠正 tencent#12”的缺陷形状：图没动、检测没动、
    打分也没动，只是牌被贴错了名。两个方向都要试：只试一个的话，“白板模板被当成
    红中”（不会报缺红墨，只会多一个抢判源）这一支就没人守。
    """
    det = TencentGridDetector()
    rows = bank_instances(det)
    clean = honor_color_offenders(rows)
    zhong = next(i for i, r in enumerate(rows) if r[1] == "7z")
    bai = next(i for i, r in enumerate(rows) if r[1] == "5z" and
               honor_ink(r[4])[0] < HONOR_INK_THR)
    a = [r[:1] + ("5z",) + r[2:] if i == zhong else r
         for i, r in enumerate(rows)]
    hit_a = any(r[1] == "5z" and r[2] == rows[zhong][2]
                for r in honor_color_offenders(a))
    b = [r[:1] + ("7z",) + r[2:] if i == bai else r for i, r in enumerate(rows)]
    hit_b = any(r[1] == "7z" and r[2] == rows[bai][2]
                for r in honor_color_offenders(b))
    ok = not clean and hit_a and hit_b
    print(f"[mutate] 把红中改标成 5z（白板）：{hit_a}；把白板改标成 7z（红中）："
          f"{hit_b}；改动前违规 {len(clean)} 张，拦下：{ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--mutate" in sys.argv:
        rc = mutate_check()
        rc |= mutate_wiring()
        rc |= mutate_unattributed()
        rc |= mutate_self_confirm()
        rc |= mutate_canvas()
        rc |= mutate_identity()
        rc |= mutate_honor_name()
        print(f"[mutate] 七个变异探针{'全部被拦下' if rc == 0 else '有未被拦下的'}")
        sys.exit(rc)
    unittest.main(verbosity=2)
