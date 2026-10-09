# -*- coding: utf-8 -*-
"""打分 13.3ms/枚 到底是「多少核 × 每核多少钱」：把两个因子分开量，再决定砍哪个。

为什么要量（本段实测）：`build/cost_split_warm.txt`（常驻 Engine，37 帧）给出
  单帧均值 187.1ms，其中「手牌行检测+分类」94.1ms/帧（50.3%）；
  内层「单枚牌面打分(matchTemplate 循环)」800 次 × 13.34ms = 10675.9ms。
`_score_face` 的循环体只有一件事：对通过 `valid_tiles` + `styles` 过滤的每个核做
一次 `cv2.matchTemplate(...).max()`。所以单价 = **核数 × 每核单价**，两因子的
处置方式完全不同：
  - 核数多 → 收窄扫描集（风格路由 / 候选集剪枝），有精度风险，必须配守卫；
  - 每核贵 → 换匹配方式（缩图 / 只算 top-K 核的二次精算），精度无损。
不分开量就只会猜。

运行：py -3.10 -X utf8 localtest/probe_core_scan.py
输出：build/probe_core_scan.txt
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

OUT = os.path.join(REPO, "build", "probe_core_scan.txt")
SHOT_DIR = os.path.join(HERE, "shots")
GT_PATH = os.path.join(HERE, "gt", "shots.json")

from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
import recognition.tencent_grid_detector as TG  # noqa: E402


def main() -> int:
    # 取检测器一律走生产的工厂（`Engine.get_hand_detector`）：自己 new 一个实例
    # 拿不到平台白名单/玩法候选集（没有 `set_mode` 这个接口），测出来的扫描核数
    # 与线上不是一个东西。
    import engine.engine as EE
    eng = EE.Engine()
    eng.set_platform("tencent")
    eng.set_mode("sc_hz")
    det = eng.get_hand_detector() or eng.get_detector()
    if det is None:
        with open(OUT, "w", encoding="utf-8") as fp:
            fp.write("拿不到检测器（模板库为空？）\n")
        return 1

    cores = getattr(det, "_cores", []) or []
    styles = Counter()
    labels = set()
    for c in cores:
        # 元组形如 (lbl, style, core_btn, core_plain, gcore_btn, gcore_plain)
        styles[c[1]] += 1
        labels.add(c[0])

    # —— 每核单价：拿真实 face 尺寸做一次 matchTemplate 的耗时分布
    # 样本 face 走生产出口：`process()` 的 payload 里 `tiles` 就是本帧真正被打分
    # 的那些格（整屏坐标）。自己调 `detect_all_rows` 再拆元组格式是猜，不如下面
    # 直接用引擎已经付过钱的读数。
    sample_faces = []
    with open(GT_PATH, encoding="utf-8") as fp:
        gt = json.load(fp)["shots"]
    import contextlib
    import io
    for s in gt:
        if not s.get("verified"):
            continue
        img = cv2.imread(os.path.join(SHOT_DIR, s["file"]))
        if img is None:
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            res = eng.process(img)
        try:
            tiles = json.loads(res.result).get("tiles") or []
        except Exception:
            tiles = []
        for t in tiles:
            x, y, w, h = int(t[0]), int(t[1]), int(t[2]), int(t[3])
            crop = img[y:y + h, x:x + w]
            if crop.size == 0:
                continue
            sample_faces.append(det.extract_face(crop))
        if len(sample_faces) >= 80:
            break
    if not sample_faces:
        with open(OUT, "w", encoding="utf-8") as fp:
            fp.write("拿不到任何 face，样本无效\n")
        return 1

    face = sample_faces[0]
    h, w = face.shape[:2]
    mt_ms = []
    plain = [(c[3], c[0]) for c in cores]
    for src, _lbl in plain[:40]:
        t0 = time.perf_counter()
        cv2.matchTemplate(face, src, cv2.TM_CCOEFF_NORMED).max()
        mt_ms.append((time.perf_counter() - t0) * 1000.0)
    core_sizes = Counter()
    for c in cores:
        core_sizes[c[3].shape] += 1

    # —— 每枚实际扫描多少核：包一层 `_score_face` 计数（含 styles/候选过滤后的真实值）
    orig = TencentGridDetector._score_face
    scan = Counter()
    filt = Counter()

    def counted(self, f, avail=None, styles_in=None):
        st = self._resolve_styles(styles_in)
        valid = TG.resolve_candidate_tiles(avail, self._mode_tiles, self.full_honors)
        n_all = len(self._cores)
        n_scan = sum(1 for lbl, style, *_ in self._cores
                     if lbl in valid and (st is None or style in st))
        scan["n_scan"] += n_scan
        scan["n_all"] += n_all
        filt["valid_labels"] += len(valid)
        if st is not None:
            filt["styles_active"] += len(st)
        return orig(self, f, avail, styles_in)

    TencentGridDetector._score_face = counted
    for f in sample_faces:
        counted(det, f)
    TencentGridDetector._score_face = orig

    # 单价与计数必须分两遍量：上面那一遍多了「预扫一遍核列表数个数」的 Python 循环，
    # 拿它的墙钟当单价就是把探针自己的成本算给生产（本段已经因同类错扣过一次结论）。
    per_tile_ms = []
    for f in sample_faces:
        t0 = time.perf_counter()
        orig(det, f)
        per_tile_ms.append((time.perf_counter() - t0) * 1000.0)

    n_tiles = len(per_tile_ms)
    avg_scan = scan["n_scan"] / max(n_tiles, 1)
    avg_mt = sum(mt_ms) / len(mt_ms)
    lines = [
        "核库：总核 %d，标签 %d 个，风格分布 %s" % (len(cores), len(labels), dict(styles)),
        "核尺寸分布(plain)：%s" % {str(k): v for k, v in core_sizes.items()},
        "",
        "样本 face：%d 枚，尺寸 %dx%d" % (n_tiles, h, w),
        "单枚打分：均 %.2fms（本探针样本，非线上均值 13.34ms 的口径，见文末）" % (
            sum(per_tile_ms) / n_tiles),
        "每枚实际扫描核数：均 %.1f（库内总核 %.0f）→ 候选+风格过滤掉了 %.1f%%" % (
            avg_scan, scan["n_all"] / max(n_tiles, 1),
            (1 - avg_scan / max(scan["n_all"] / max(n_tiles, 1), 1)) * 100),
        "每核单价：matchTemplate 均 %.3fms（%d 次样本）" % (avg_mt, len(mt_ms)),
        "",
        "因子分解：单枚 ≈ 扫描核数 %.1f × 每核 %.3fms = %.2fms（与实测 %.2fms 对照，"
        "差值即 .max()/循环开销）" % (
            avg_scan, avg_mt, avg_scan * avg_mt, sum(per_tile_ms) / n_tiles),
        "",
        "有效候选标签数：均 %.1f/枚，活跃风格数：均 %.1f/枚" % (
            filt["valid_labels"] / max(n_tiles, 1),
            filt["styles_active"] / max(n_tiles, 1)),
        "→ 若「活跃风格数」≈ 全库风格数，说明风格路由没在省钱（可切第一刀）；"
        "若已经只有一两家，剩下的钱只能在每核单价上找（缩图/粗筛精算）。",
    ]
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
