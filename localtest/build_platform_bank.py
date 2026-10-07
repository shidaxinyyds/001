# -*- coding: utf-8 -*-
"""把 harvest_style_tiles.py 割出的真机牌面编译成 TencentGridDetector 的风格 bank。

bank 的契约（必须与 recognition/templates_shushan.py 一致）：
  TEMPLATES_BGR = {label 或 "label#变体": 120x80x3 数组}
加载端 _build_cores 会按 `tmpl[16:104, 12:68]`（普通）与 `tmpl[44:104, 12:68]`
（带黄色顶标）切匹配核，所以尺寸错位会让整库静默失效——这里统一用生产同一个
`TencentGridDetector.extract_face` 生成，天然对齐，不自己另写一套归一化。

为什么用 base64 PNG 而不是像蜀山那样存嵌套列表：
  蜀山 35 张 = 7.0 MB 纯文本（每个像素都写成十进制字面量），
  再加 4 个平台就是 ~28 MB 打进 APK。PNG 无损且这些图本身就是 120x80 小图，
  单张 ~2-4 KB。解码用 np.frombuffer + cv2.imdecode，正是 _load_templates
  里为绕开 Chaquopy zip 文件系统而用的同一套读法，不引入新依赖。

用法: py -3.10 localtest\build_platform_bank.py [queshen ...]
输出: android/app/src/main/python/recognition/templates_<style>.py
"""
import base64
import json
import os
import re
import sys
from collections import defaultdict

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402

TILES = os.path.join(HERE, "tiles")
MAX_VARIANTS = 3  # 同牌面最多存几张变体（定缺压暗帧 vs 正常帧亮度差大）
# 与本族已选模板的最高相似度超过这个线 = 同一外观簇的重复样本（不是新信息）。
# 0.985 沿用历史实测：同一张牌的不同帧变体通常 >= 0.985，而真不同的牌面最高
# 只到 0.79（见 test_bank_hygiene.py 对跨标签近重复的实测分布）。现在拿它当
# “这个变体带不带新外观”的分界，而不是拿它当入库门槛。
NOVEL_THR = 0.985
ALL_KINDS = {f"{n}{s}" for s in "mps" for n in range(1, 10)} | \
            {f"{n}z" for n in range(1, 8)}
# 源帧归属：只认带批次前缀的裁片（`b1_f02_00_1m.png` -> 帧 2）。
# **必须与 `eval_new_material.load_provenance` 用的是同一把尺**：择优的目标是
# “LOFO 剔掉本帧后还剩不剩能打赢的模板”，把归属算错一格，择优选出来的就是一套
# 评测里根本不存在的支撑关系。无前缀（`f34_03_3m.png`）与 `legacy_*` 都返回 None
# = 该模板在任何一折里都不会被剔（它是别批次的真样本外素材）。
FRAME_RE = re.compile(r"\w+_f(\d+)_")


def frame_of(fn):
    m = FRAME_RE.match(fn or "")
    return int(m.group(1)) if m else None


# 不参与平台 bank 编译的 tiles/ 子目录。**每条都必须写理由**，因为「一个收割目录
# 悄悄躺在跳过名单里」与「收割了但没接线」是同一个缺陷的两种写法（见
# localtest/test_bank_hygiene.py 的接线不变量）。这个名单被那个测试引用，
# 新增目录不进来登记就会让测试变红，而不是默默少一个平台。
SKIP_DIRS = {
    # 外部素材站下载的牌面图（非任何游戏的实机截图），已由 build_style_bank.py
    # 走另一条链使用。接进 EXTRA_BANKS 等于把「素材站画的样子」当成
    # 「某个平台屏幕上渲染出来的样子」，正是风格串味的源头。
    "duma520": "外部素材站牌面图，不是任何平台的实机牌风；由 build_style_bank 用",
    # 单张旧 App 截图的 13 枚，同样由 build_style_bank 使用。
    "screenshot": "旧截图 13 枚，张数撑不起一个平台 bank；由 build_style_bank 用",
    # 2026-09 旧批 589 张：标签是按「手牌等宽分布」推算位置得到的（见
    # harvest_tencent_happy.py 顶部注释），没有逐枚看图复核。而本批已经用
    # GT 逐枚钉过的方式重收了 `tiles/tencent/`（184 张 / 33 类），旧批要么
    # 逐枚复核后并入 tencent，要么废弃，不许默默进库。
    "tencent_happy": "旧批标签靠等宽推算未复核；已由 tiles/tencent 的 GT 逐枚钉版取代",
}


def read_png(p):
    with open(p, "rb") as f:
        return cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)


def core_sig(face):
    """匹配核的归一化灰度指纹：择优选样本要用到的相似度尺子。"""
    g = cv2.cvtColor(face[16:104, 12:68], cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = (g - g.mean()) / (g.std() + 1e-6)
    return g.ravel()  # 必须拉平：否则 np.dot 会当成矩阵乘法而非内积


def gram(sigs):
    """两两归一化 NCC 矩阵（一次算完，择优时每步只是查表）。"""
    A = np.asarray(sigs, dtype=np.float32)
    A /= (np.linalg.norm(A, axis=1, keepdims=True) + 1e-6)
    return A @ A.T


def rival_bars(labels, frames, G):
    """每个样本要跨过的对手线：它与**别类**素材（剔掉同帧的）的最高相似度。

    用别类**全部**样本而不只是最终入库的那些：线取得比实际更严，择优会偏向
    能扛住最坏对手的样本；反过来（只算已入库的）会把“本来就会被剔掉的对手”
    当成不算，选出一批只能赢弱对手的模板。
    """
    bars = []
    for i in range(len(labels)):
        oth = [j for j in range(len(labels))
               if labels[j] != labels[i]
               and not (frames[i] is not None and frames[j] == frames[i])]
        bars.append(float(G[i, oth].max()) if oth else -1.0)
    return bars


def pick_variants(faces, rival):
    """按「LOFO 剔掉本帧后，本类查询还能不能赢」择优，而不是按清晰度。

    返回 [(face, 源文件名, 与本族代表的最劣相似度)]：带文件名是为了写 provenance，
    LOFO 评测（eval_new_material.py --lofo）需要知道“这张模板来自哪一帧”才能
    把该帧贡献的模板临时抽掉。不记源文件就无法做样本外估计，只能拿样本内
    的 100% 自欺（15 帧的 bank 本来就是从这 15 帧割的）。

    rival: {源文件名: 对手线}，由 `rival_bars` 用全库相似度算好传进来。

    目标函数就是评测口径在库内的直接投影：把本类每个样本 q 当查询，候选集里
    **剔除与 q 同帧的那几张**（那一折真会被抽掉），剩下的对 q 的最高相似度要
    > rival[q] 才算赢。多级排序（前一级同分才看下一级）：
      ① 赢下的查询格数
      ② 是否带来**新外观簇**（与本族已选的最高相似度 < NOVEL_THR）。
         不插这一级时，纯赢格目标会系统性把孤立外观帧的素材顶出库：实测 zj#08
         是全 GT 里唯一一帧 1440x664，改成纯赢格择优后本帧一张没入库，那一帧从
         “剔本帧 8 张仍 10/10”退成 6/10、均分 0.62 -> 0.36——检出层看的是**绝对
         分数**，分数不够就不出框，输掉的是整帧的框而不是一格判决（量法见
         build/_frame_support.py）。它只插在赢格之后：外观覆盖不得换来换掉判别力。
      ③ Σ_q (best(q) - rival[q])：主力群 0.99 vs 孤儿亚群 0.90 靠这一级分开
      ④ 是否引入新源帧
      ⑤ 与已选集合越不像越优先

    为什么不按 Laplacian 清晰度（旧规则），也不按跨类 margin：见下面两段
    【试过并且失败】。清晰度那次实测选进了微乐 8p 的 `f03_10_8p.png`（own 0.326，
    几乎不像自己类）；margin 那次实测奖励腾讯 9s 的 `b1_f12_*` 两张孤儿亚群。

    不再拿 0.985 当**入库门槛**（旧规则拿它拦同外观重复）：不同帧的同外观重复在
    LOFO 下严格有用（本帧被剔时它就是那个一直在场的兜底，也是结构性缺类从
    62/1087 降到 19/1087 的来源），所以它现在只在②之后参与排序，不再一票否决。
    跨标签近重复（贴错名字）仍由 `test_bank_hygiene.py` 的 0.95 门把；本函数自己
    的三条存在理由（孤儿亚群 / 唯一外观帧 / 名额不得全押一帧）由
    `localtest/test_bank_selection.py` 用人工夹具 + 真素材钉住，带 --mutate 反向检验。
    """
    n = len(faces)
    if n <= 1:
        return [(f, fn, 1.0) for f, fn in faces]
    sigs = [core_sig(f) for f, _fn in faces]
    G = gram(sigs)
    fr = [frame_of(fn) for _f, fn in faces]
    bar = [rival.get(fn, -1.0) for _f, fn in faces]
    chosen, seen_frames, left = [], set(), set(range(n))
    for _ in range(min(MAX_VARIANTS, n)):
        best, key = None, None
        for t in left:
            sub = sorted(chosen + [t])
            wins, tot = 0, 0.0
            for k in range(n):
                # 允许的模板：不是自己，且不与自己在同一帧（同帧的那几张会被 LOFO 抽掉）
                allow = [x for x in sub if x != k
                         and not (fr[k] is not None and fr[x] == fr[k])]
                if not allow:
                    continue         # 本帧之外无人可撑：是素材债，不记在本规则头上
                gap = float(max(G[k, x] for x in allow)) - bar[k]
                tot += gap
                if gap > 0:
                    wins += 1
            # ②新外观：与本族已选的最高相似度低于 NOVEL_THR 才算带了新信息；
            # ⑤（最后才看）与已选越不像越优先，保住旧规则“变体之间要有区别”的
            # 初衷，但不得反过来压过赢格数与外观覆盖。
            novel = 1 if (not chosen or
                          float(G[t, chosen].max()) < NOVEL_THR) else 0
            k2 = (wins, novel, round(tot / n, 4), 0 if fr[t] in seen_frames else 1,
                  round(-float(max(G[t, chosen], default=-1.0)), 4))
            if key is None or k2 > key:
                key, best = k2, t
        if best is None:
            break
        chosen.append(best)
        seen_frames.add(fr[best])
        left.discard(best)
    # 第三项返回值仍是“与本族已选代表的最劣相似度”，只作诊断用（旧规则拿它当门槛，
    # 现在它不再参与决策）。
    return [(faces[t][0], faces[t][1],
             1.0 if i == 0 else float(min(G[t, c] for c in chosen[:i])))
            for i, t in enumerate(chosen)]


# 【试过并且失败，勿再接回“按源帧轮转择优”】变体只按清晰度+互异性挑时，一个类的
# 三张变体可能全落在同一帧（实测微乐 5p/8p 各有两张来自帧 13），LOFO 剔该帧就连同
# 该类一起失去——看起来像“素材不够”，实际是“择优把素材押在同一帧”。于是改成先按
# 源帧分桶、每帧先交一张（帧间按清晰度排）再轮转追加。实测两点都不成立：
#   ① 结构性缺类一张没消掉（29/536 保持不变）——那 29 张确实是“该类只住在一帧里”
#      的素材债（gap_plan.py 独立统计的 14 类/19 张 + 剔空后连框都拿不到的 7 张）；
#   ② 97% 工作点反而变差：覆盖率从 97.6%（门限 0.50）掉到 95.1%（门限 0.70）。
#      原因是蜀山 4p 换了一张“更清晰但判别力更差”的变体后，帧 05 出现两例
#      4p→5p 的**高分**错（0.75/0.78），低门限拦不住。
# 结论：清晰度不是判别力。真要把择优做对，得按“与最近邻别类的间隔”挑变体，
# 而不是按 Laplacian 方差——那是另一个工程，且必须先有跨类间隔的度量与验证。


# 【试过并且失败，勿再接回】曾想按“与同类其余样本的相似度”自动剔除残缺裁片
# （drop_outliers）。度量不可用：同一张牌在不同帧（定缺阶段整行压暗）的滑窗
# NCC 只有 0.4~0.5，与“真残缺样本”完全重叠（同类对实测 0.05~0.98），任何阈值
# 都会在误杀好样本与放过坏样本之间选一边。而误杀的代价不是“识别错”而是
# **整手消失**：把雀神 8p 的模板误杀后，三张 8p 退化成 6p，凑成「6p x 5」这种
# 物理上不可能的组合，被引擎的同牌≤4 守卫直接拒掉整手（帧 14 从 count=14
# 变成 status=waiting/count=0）。残缺样本改由上游人工把关：
# harvest_style_tiles.py 的 GT 里读不准的格写 `?`，占位但不落盘。


def build(style):
    src = os.path.join(TILES, style)
    if not os.path.isdir(src):
        print(f"{style}: 无素材目录 {src}")
        return None
    by_label = defaultdict(list)
    junk = []
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".png"):
            continue
        # 文件名末段是 `label` 或 `label#变体`（见本文件顶部对 bank 契约的说明：
        # `TEMPLATES_BGR = {label 或 "label#变体": ...}`，加载端剥离 `#` 后后缀）。
        # 必须先把变体后缀剥掉再判合法，否则 `legacy_7s#b.png`（带蓝色「缺」角标
        # 的七索，人工逐枚看过）会被判成非法牌类，把整个 shushan 库连合法样本一起
        # 拒掉——下面的撞键保护本来就承认目录里有 `#2`/`#b` 手工样本，两处不能打架。
        lab = fn[:-4].split("_")[-1].split("#")[0]
        if lab not in ALL_KINDS:
            # 不是牌类的标签必须当场停下来，不能默默跳过：它说明上游收割把
            # **非牌面元素**当成了手牌（实测雀神 `f16_00_qs.png`/`f17_03_qs.png`
            # 是屏幕上的「雀神」台标：红圈里的雀字 + 绿环，根本不是一张牌）。
            # 打分端 `if lbl not in valid_tiles: continue` 会把它挡住，所以它在
            # 库里是永不生效的死重量；但拿它去“识别”只会给出一个下游解不出的
            # 标签。更关键的是：未知标签 = GT/收割错位，不报错就会继续往别家牌
            # 上烧错名字（见本文件顶部对 drop_outliers 的失败记录）。
            junk.append(fn)
            continue
        img = read_png(os.path.join(src, fn))
        if img is None or img.size == 0:
            continue
        by_label[lab].append((TencentGridDetector.extract_face(img), fn))
    if junk:
        raise SystemExit(
            f"{style}: {len(junk)} 张样本的标签不是合法牌类，已拒绝编译（不写任何产物）：\n  "
            + "\n  ".join(junk)
            + "\n  先看图定它到底是什么：非牌面元素（台标/按钮/副露指示）请移到 "
              "localtest/tiles_quarantine/<style>/（必须在 tiles/ 之外，否则会被当成"
              "一个风格扫进去）；牌面但标签写错请改 GT 后重收割。")
    tpls = {}
    prov = {}
    # 跨类对手线必须拿全库一起算：择优的目标是“本帧被 LOFO 抽掉后能不能赢过**别类**”，
    # 逐类单独算根本看不到对手是谁（下面 `pick_variants` 只拿到本类样本）。
    flat = [(lab, fn, f) for lab, faces in sorted(by_label.items()) for f, fn in faces]
    G_all = gram([core_sig(f) for _lab, _fn, f in flat])
    bars = rival_bars([lab for lab, _fn, _f in flat],
                      [frame_of(fn) for _lab, fn, _f in flat], G_all)
    rival = dict(zip((fn for _lab, fn, _f in flat), bars))
    for lab, faces in sorted(by_label.items()):
        # 不做自动离群剔除（见上方注释），变体按「LOFO 后本类查询能不能赢」择优
        for i, (f, fn, _dissim) in enumerate(pick_variants(faces, rival)):
            n = 1 if i == 0 else i + 1
            key = lab if n == 1 else f"{lab}#{n}"
            # 撞键保护：目录里可能躺着手工样本（文件名本身就带 #2、#b
            # 后缀，如 build_shushan_bank 的 7z#2 / 7s#b），它与本函数自动
            # 生成的 `lab#2` 同名。字典赋值会静默覆盖前一张——模板“消失”
            # 不报任何错，只表现为那张牌偶尔认错，极难查。宁可多几十字节。
            while key in tpls:
                n += 1
                key = lab if n == 1 else f"{lab}#{n}"
            ok, buf = cv2.imencode(".png", f, [cv2.IMWRITE_PNG_COMPRESSION, 6])
            if not ok:
                continue
            tpls[key] = base64.b64encode(buf.tobytes()).decode("ascii")
            prov[key] = fn
    out = os.path.join(PYROOT, "recognition", f"templates_{style}.py")
    lines = [
        "# -*- coding: utf-8 -*-",
        f'"""{style} 平台牌面风格 bank（localtest/build_platform_bank.py 自动生成，勿手改）。',
        "",
        "素材来源：localtest/tiles/%s/，由人工核对过的手牌 GT 从真机截图割出。" % style,
        '尺寸 120x80x3，与 recognition/templates_data.py 同一归一化空间。"""',
        "import base64",
        "import numpy as np",
        "import cv2",
        "",
        "_PNG_B64 = {",
    ]
    for k in sorted(tpls):
        lines.append(f"    {k!r}: {tpls[k]!r},")
    lines += [
        "}",
        "",
        "_dec = cv2.imdecode",
        "TEMPLATES_BGR = {k: _dec(np.frombuffer(base64.b64decode(v), np.uint8), cv2.IMREAD_COLOR)",
        "                 for k, v in _PNG_B64.items()}",
        "TEMPLATES_GRAY = {k: cv2.cvtColor(v, cv2.COLOR_BGR2GRAY) for k, v in TEMPLATES_BGR.items()}",
        "",
    ]
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    # provenance 落在 tiles/（派生目录、不进 APK）：它是 LOFO 评测的唯一依赖，
    # 少了它就只能拿样本内数字交差。
    with open(os.path.join(src, "provenance.json"), "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=1)
    kb = os.path.getsize(out) / 1024.0
    print(f"[ok] {style}: {len(by_label)} 类 / {len(tpls)} 张模板 -> templates_{style}.py ({kb:.0f} KB)")
    return out


def main():
    styles = sys.argv[1:] or sorted(d for d in os.listdir(TILES)
                                    if os.path.isdir(os.path.join(TILES, d))
                                    and d not in SKIP_DIRS)
    for s in styles:
        build(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
