# -*- coding: utf-8 -*-
"""实时推理保障实测：连续 1000 帧的耗时/内存/精度衰减，以及逐帧耗时拆解。

口径先说清楚（避免拿 PC 数字冒充手机数字）：
  本机是 Windows PC + OpenCV CPU 版；生产跑在 Android/Chaquopy + 同一套 Python 识别码。
  所以这里给出的是「这条 Python 识别路径在参考硬件上的开销」，不是手机端实测值；
  手机端必须另测（CPU 弱一个量级，且面板还有截图/序列化链路）。
  判据按商用规约：<=50ms/帧。达不到就直说达不到——把 140ms 写成"满足"才是事故。

两种模式：
  （默认）      1000 帧连续处理：p50/p95/p99、RSS 曲线、逐帧标签多重集一致性
  --engine N    面板真实链路（`Engine.process`）连续 N 帧（默认 1000）：除耗时/内存/精度
                衰减外，还拆开“手牌通道占多少 + 跳帧多少帧”。上面两种模式都只量
                检测器，量不到面板每帧实付的钱；换通道的代价必须在这里算。
  --per-frame   每个夹具帧的耗时中位数 + 该帧做了几次 classify_tile、其中几次跨全部 bank
                （p95 远高于 p50 时用它找元凶：兜底重扫与探针未路由是乘数，不是噪声）
  --channels    同一帧分别量两个手牌通道（网格模板 NCC vs 生产在用的 YOLO），
                拿“换通道值多少精度、付多少延时”的对比数字（精度侧见 layer_cost.py）

运行：py -3.10 -X utf8 localtest/bench_latency.py [--per-frame|--channels|--engine [N]] [--fix DIR] [--platform KEY] [--with-probe]
      `--with-probe` 仅对 `--engine` 有意义：拨回「声明平台仍扫全库探针」的旧行为，
      在同一条流上量「跳探针」到底值多少毫秒。
输出：build/latency.txt / build/per_frame.txt / build/channels.txt / build/engine_stream.txt（派生物，不入库）
"""
import ctypes
import json
import os
import statistics
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import recognition.tencent_grid_detector as T  # noqa: E402  (读 PARALLEL_WORKERS 等模块常量)
from recognition.tencent_grid_detector import TencentGridDetector  # noqa: E402
from platforms import PLATFORMS  # noqa: E402

FIX = os.path.join(HERE, "shots_tuyou")
N_ITER = 1000
# `--with-probe` 对照口径：只在 `--engine` 模式有意义（拨回「声明平台仍扫全库探针」
# 的旧行为，同一条流上比 p50）。默认 False = 量现行生产路径。
WITH_PROBE = "--with-probe" in sys.argv
# `--serial` 对照口径：把逐枚打分退回串行（`parallel_classify=False`），在同一条流上
# 量「并行到底值多少毫秒」。报告另写一份文件，避免把现行结论顶掉。
SERIAL = "--serial" in sys.argv
# `--workers N`：改外部并行的 worker 数（在池子创建前写模块常量才有效）。
# 为什么要有这个口子：4 worker 下 p50 从 712.7 -> 496.6ms，但 p95/p99 反而涨到
# 1340/3334ms（build/engine_stream_120_parallel.txt）。尾部才是「实时」的命门，
# 必须能在同一批帧上扫 worker 数找拐点，而不是拿一个数当结论。
WORKERS = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 0


def platform_from_fix():
    """夹具目录名 -> 平台 key（`shots_tuyou` -> tuyou）。

    为什么必须有：拿 A 家的帧在面板上声明 B 家平台，平台白名单会把候选风格集收窄
    到别家牌风，于是耗时和精度都不知在量谁。b9 那轮就是 `shots_tuyou` 的 10 帧配
    `platform=tencent`（默认值），数字本身能重复、但口径不成立，这轮起按夹具改名。
    认不出来的目录名不猜：回退 tencent，并把夹具目录名一起写进报告行（`平台声明=…
    （夹具 …）`），不符就肉眼可见。`shots`（腾讯底座 37 帧）也走这条回退，恰好对。
    """
    name = os.path.basename(os.path.normpath(FIX))
    tail = name.split("shots_", 1)[1] if name.startswith("shots_") else ""
    if tail in PLATFORMS:
        return tail
    cand = [k for k in PLATFORMS if k.endswith("_" + tail)] if tail else []
    return cand[0] if len(cand) == 1 else "tencent"



def rss_mb():
    """当前进程工作集（MB）：Windows 用 Psapi，失败则退回 None（不假装测到）。"""
    try:
        psutil = __import__("psutil")
        return psutil.Process().memory_info().rss / 1048576.0
    except Exception:
        pass
    try:
        h = ctypes.windll.kernel32.GetCurrentProcess()
        fn = getattr(ctypes.windll, "psapi", None) or ctypes.windll.kernel32
        if hasattr(fn, "GetProcessMemoryInfo"):
            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_size_t), ("PageFaultCount", ctypes.c_uint),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("NonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            p = PMC()
            p.cb = ctypes.sizeof(PMC)
            if fn.GetProcessMemoryInfo(h, ctypes.byref(p), p.cb):
                return p.WorkingSetSize / 1048576.0
    except Exception:
        return None
    return None


def load_frames():
    files = sorted(f for f in os.listdir(FIX) if f.endswith(".jpg"))
    frames = [(f, cv2.imread(os.path.join(FIX, f))) for f in files]
    return [(f, im) for f, im in frames if im is not None]


def bench_stream(det, frames, out):
    imgs = [im for _f, im in frames]
    for img in imgs:                       # 预热：首轮含 lazy 初始化与页缓存
        det.detect_hand_strip(img)
    base = [sorted(l for _r, l, _s in (det.detect_hand_strip(img) or [])) for img in imgs]
    rss0 = rss_mb()
    times, rss_at, mismatch = [], [], 0
    t_all = time.perf_counter()
    for it in range(N_ITER):
        img = imgs[it % len(imgs)]
        t0 = time.perf_counter()
        dets = det.detect_hand_strip(img) or []
        times.append((time.perf_counter() - t0) * 1000.0)
        if it % 100 == 0:
            rss_at.append((it, rss_mb()))
        if it < len(imgs) * 60:            # 前 60 轮逐帧比对，之后只抽查
            if sorted(l for _r, l, _s in dets) != base[it % len(imgs)]:
                mismatch += 1
    wall = time.perf_counter() - t_all
    times.sort()
    p = lambda q: times[min(len(times) - 1, int(len(times) * q))]
    lines = [
        "识别路径：TencentGridDetector.detect_hand_strip（NCC 模板匹配，含风格探针）",
        f"硬件/环境：Windows PC，OpenCV {cv2.__version__} CPU 版；结论不代表手机端实测",
        f"夹具：{len(imgs)} 帧（2712x1220 为主），连续处理 {N_ITER} 帧，用时 {wall:.1f}s",
        "",
        f"单帧耗时 ms：p50={p(0.5):.1f}  p95={p(0.95):.1f}  p99={p(0.99):.1f}  max={times[-1]:.1f}",
        f"预算 50ms/帧 -> {'满足' if p(0.95) <= 50 else '不满足（实测超预算 ' + format(p(0.95) / 50, '.1f') + ' 倍）'}",
        f"整轮吞吐：{N_ITER / wall:.1f} 帧/s（含取帧与循环开销）",
        f"精度衰减：前 {len(imgs) * 60} 帧逐帧比对标签多重集，不一致 {mismatch} 次",
    ]
    if rss0 is not None and rss_at:
        r_end = rss_mb()
        lines.append(f"内存：起始 {rss0:.0f}MB -> 结束 {r_end:.0f}MB（Δ {r_end - rss0:+.0f}MB）")
        lines.append("采样点(帧,RSS MB)：" + ", ".join(f"({i},{r:.0f})" for i, r in rss_at if r))
    else:
        lines.append("内存：本机取不到 RSS（psutil 缺失且 Psapi 调用失败），未测，不猜")

    # YOLO 路径（牌河在用）单独量一次前向：输入尺寸必须用生产常量（牌河是一条横带，
    # 不是正方形），拿 640x640 去喂会在 reshape 层就报错。
    onnx = os.path.join(PYROOT, "recognition", "models", "yolo_mahjong.onnx")
    if os.path.exists(onnx):
        try:
            from recognition.yolo_detector import MODEL_W, MODEL_H
            net = cv2.dnn.readNetFromONNX(onnx)
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            blob = cv2.dnn.blobFromImage(cv2.resize(imgs[0], (MODEL_W, MODEL_H)),
                                         1 / 255.0, (MODEL_W, MODEL_H), swapRB=True)
            ts = []
            for _ in range(20):
                t0 = time.perf_counter()
                net.setInput(blob)
                net.forward()
                ts.append((time.perf_counter() - t0) * 1000.0)
            lines.append(f"YOLO onnx 前向（{MODEL_W}x{MODEL_H}, CPU）：p50={statistics.median(ts):.1f}ms "
                         f"max={max(ts):.1f}ms  模型 {os.path.getsize(onnx) // 1024 // 1024}MB")
        except Exception as e:
            lines.append(f"YOLO onnx 前向：未能测通（{type(e).__name__}: {e}）")
    text = "\n".join(lines) + "\n"
    out.write(text)
    print(text)


def bench_per_frame(det, frames, out, n=5):
    """逐帧耗时 + classify_tile 调用次数与 styles 参数（跨 bank 全扫 = 耗时乘数）。"""
    calls = {"n": 0}
    full = {"n": 0}
    orig = det.classify_tile

    def counted(crop, **kw):
        """只计数与记录 styles，不改行为。

        styles=None 意味着这一次在**全部 bank** 上扫（探针未路由或均分不达标兜底重扫），
        它就是 p95 的来源；比去猜一个不存在的 last_probe_style 属性直接。"""
        calls["n"] += 1
        if kw.get("styles", "__pos__") in (None, "__pos__"):
            full["n"] += 1
        return orig(crop, **kw)

    det.classify_tile = counted
    lines = [f"模板条目 {len(det._cores)}，bank 风格：{sorted({c[1] for c in det._cores})}",
             "",
             f"{'帧':22s} {'宽x高':11s} {'张数':>4s} {'ms(中位)':>9s} {'ms(最大)':>9s} "
             f"{'classify/帧':>10s}  跨全部 bank 的次数"]
    for f, img in frames:
        h, w = img.shape[:2]
        det.detect_hand_strip(img)                     # 预热
        ts, nc, nf = [], 0, 0
        for _ in range(n):
            calls["n"] = full["n"] = 0
            t0 = time.perf_counter()
            dets = det.detect_hand_strip(img) or []
            ts.append((time.perf_counter() - t0) * 1000.0)
            nc, nf = calls["n"], full["n"]
        lines.append(f"{f[:22]:22s} {w}x{h:<7} {len(dets):>4d} "
                     f"{statistics.median(ts):>9.1f} {max(ts):>9.1f} {nc:>10d}  {nf}")
    text = "\n".join(lines) + "\n"
    out.write(text)
    print(text)


def bench_channels(frames, platform, out, n=5):
    """两个手牌通道的同帧耗时对比（均按生产口径声明平台）。

    只量耗时不量精度：精度对拍是 `localtest/layer_cost.py --set base|new` 的职责；
    两边分开才不会出现“同一个脚本里自己调参数拟合自己”。
    """
    grid = TencentGridDetector()
    if hasattr(grid, "set_platform_styles"):
        grid.set_platform_styles(platform)
    from recognition.yolo_detector import YOLODetector
    yolo = YOLODetector()
    if not yolo.is_available:
        out.write("YOLO 不可用（onnx 缺失或 OpenCV 版本不够），未测\n")
        return
    if hasattr(yolo, "set_platform_styles"):
        yolo.set_platform_styles(platform)

    lines = [f"夹具 {len(frames)} 帧（--fix），平台声明={platform}（两通道同口径）",
             "手牌行取 `detect_all_rows(...)[0]`（引擎就是拿第一行当手牌）",
             "",
             f"{'帧':24s} {'宽x高':11s} {'网格ms':>8s} {'YOLOms':>8s}  {'网格张':>5s} {'YOLO张':>5s}"]
    gt = []
    for f, img in frames:
        h, w = img.shape[:2]
        grid.detect_all_rows(img, allow_rotation=False)          # 预热
        yolo.detect_all_rows(img, allow_rotation=False)
        tg, ty = [], []
        for _ in range(n):
            t0 = time.perf_counter()
            rg = grid.detect_all_rows(img, allow_rotation=False) or []
            tg.append((time.perf_counter() - t0) * 1000.0)
            t0 = time.perf_counter()
            ry = yolo.detect_all_rows(img, allow_rotation=False) or []
            ty.append((time.perf_counter() - t0) * 1000.0)
        ng = len(rg[0]) if rg else 0
        ny = len(ry[0]) if ry else 0
        gt.append((statistics.median(tg), statistics.median(ty), ng, ny))
        lines.append(f"{f[:24]:24s} {w}x{h:<7} {statistics.median(tg):>8.1f} "
                     f"{statistics.median(ty):>8.1f}  {ng:>5d} {ny:>5d}")
    g_ms = sorted(x[0] for x in gt)
    y_ms = sorted(x[1] for x in gt)
    med = lambda a: a[len(a) // 2]
    lines.append("")
    lines.append(f"中位耗时：网格 {med(g_ms):.1f}ms   YOLO {med(y_ms):.1f}ms   "
                 f"最慢帧：网格 {g_ms[-1]:.1f}ms / YOLO {y_ms[-1]:.1f}ms")
    lines.append(f"预算 50ms/帧 -> 网格{'满足' if med(g_ms) <= 50 else '不满足'}、"
                 f"YOLO{'满足' if med(y_ms) <= 50 else '不满足'}（PC 实测，手机端另算）")
    text = "\n".join(lines) + "\n"
    out.write(text)
    print(text)


def bench_engine(frames, platform, out, n_iter=N_ITER):
    """面板实付：连续把夹具帧喂 `Engine.process`，量耗时/内存/精度衰减与手牌通道占比。

    为什么不能拿 `bench_stream`（只量 `detect_hand_strip`）当“每帧开销”：那一层不含
    ROI 切片、方向快检、牌河/副露扫描、策略计算与 payload 序列化，而那些在面板里
    是每帧都要付的。手牌通道切换的代价与收益只有在这里才能一起看见。

    诚实声明：夹具只有几帧、循环喂会频繁命中“智能跳帧”（画面未变且已稳定→直接
    复用上帧结果）。跳帧帧在生产里也存在（静默期降频），但循环夹具会把它放大，
    所以这里把跳帧帧单独计数并从耗时百分位里剔除，两套数字都给出。
    """
    import engine.engine as E
    from engine.engine import Engine

    E.load_platform = lambda *a, **kw: platform      # 生产口径：声明平台
    # 玩法也必须显式声明，理由与 `localtest/test_hand_channel.run_engine` 同源：
    # `load_mode()` 的候选路径最后一条是 cwd 下的相对文件名，PC 上今天没有那个文件
    # 所以会静默落到 DEFAULT_MODE；哪天落到了别家，这份耗时/精度数字就对不上历史了。
    # 取该平台 default_mode = 用户刚选到这个平台时真正拿到的玩法（也是 D1 修复后
    # `Engine.__init__` 生效的那条口径）。tencent 的默认玩法就是 sc_hz，与 b9 基线
    # 建表时写死的常量相同，所以这轮的耗时数字仍可与上一轮直接比。
    E.load_mode = lambda *a, **kw: PLATFORMS[platform]["default_mode"]
    eng = Engine()
    hand = eng.get_hand_detector()
    # 对照口径：`--with-probe` 把「声明平台跳全库探针」拨回旧行为，同一条流上直接
    # 量这个改动值多少毫秒。只靠 91 帧 A/B 的那个百分比不够 —— 那条不含面板每帧
    # 实付的 ROI 切片/牌河扫描/策略计算，而预算是按面板口径追的。
    hand.probe_when_declared = WITH_PROBE
    # 并行对照：`--serial` 关掉逐枚打分的线程池，同一条流上直接量它值多少毫秒。
    hand.parallel_classify = not SERIAL
    if WORKERS:
        T.PARALLEL_WORKERS = WORKERS      # 必须在池子创建前改（池子是懒创建的）
    primary = eng.get_detector()
    lines = ["识别路径：Engine.process（面板真正调的那个入口）",
             f"手牌通道 = {hand.__class__.__name__}，主检测器 = {primary.__class__.__name__}，"
             f"平台声明={platform} 玩法声明={E.load_mode()}（夹具 {os.path.basename(os.path.normpath(FIX))}）",
             "手牌排布分支：" + ("旧行为（声明平台仍扫全库探针）—— 对照" if WITH_PROBE
                              else "现行（声明平台时排布查表、跳全库探针）"),
             "手牌打分调度：" + ("串行（--serial 对照）" if SERIAL
                              else f"并行（{hand.__class__.__name__} 线程池，上限 "
                                   f"{T.PARALLEL_WORKERS}）"),
             f"硬件/环境：Windows PC，OpenCV {cv2.__version__} CPU 版；结论不代表手机端实测"]

    imgs = [im for _f, im in frames]
    names = [f for f, _im in frames]
    for img in imgs:                                  # 预热（lazy 初始化/页缓存）
        eng.process(img)

    # 手牌通道耗时归因：包一层只计时，不改行为
    cost = {"ms": 0.0, "n": 0}
    if hasattr(hand, "detect_all_rows"):
        orig = hand.detect_all_rows

        def timed(image, *a, **kw):
            t0 = time.perf_counter()
            r = orig(image, *a, **kw)
            cost["ms"] += (time.perf_counter() - t0) * 1000.0
            cost["n"] += 1
            return r

        hand.detect_all_rows = timed

    base = {}                                         # 每帧基线手牌多重集
    rss0 = rss_mb()
    ts, skipped, drift, rss_at = [], 0, [], []
    # 通道耗时必须**按帧归集**再分组：全局累计值除以「非跳帧总耗时」会得出 >100%
    # 的占比（分子含跳帧、分母不含），既不能用也不能读。更要紧的是分组之后能直接
    # 看见一件本来看不见的事：跳帧帧到底有没有把最贵的手牌通道跳掉。
    kept_ms = 0.0
    kept_calls = 0
    skip_ms = 0.0
    skip_calls = 0
    t_all = time.perf_counter()
    for it in range(n_iter):
        idx = it % len(imgs)
        c0, k0 = cost["ms"], cost["n"]
        t0 = time.perf_counter()
        res = eng.process(imgs[idx])
        ms = (time.perf_counter() - t0) * 1000.0
        c_cost, c_calls = cost["ms"] - c0, cost["n"] - k0
        data = json.loads(res.result) if res is not None else {}
        if data.get("frame_skipped"):
            skipped += 1
            skip_ms += c_cost
            skip_calls += c_calls
        else:
            ts.append(ms)
            kept_ms += c_cost
            kept_calls += c_calls
        mpsz = data.get("hand", "") or ""
        hand_mpsz = sorted(mpsz[i:i + 2] for i in range(0, len(mpsz), 2))
        if it < len(imgs):
            base[idx] = hand_mpsz
        elif hand_mpsz != base.get(idx):
            drift.append((it, names[idx], len(base.get(idx, [])), len(hand_mpsz)))
        if it % 200 == 0:
            rss_at.append((it, rss_mb()))
    wall = time.perf_counter() - t_all
    ts.sort()
    p = lambda q: ts[min(len(ts) - 1, int(len(ts) * q))] if ts else 0.0
    lines += [
        f"夹具 {len(imgs)} 帧，连续处理 {n_iter} 帧，用时 {wall:.1f}s（均 {wall / n_iter * 1000:.0f}ms/帧）",
        "",
        f"非跳帧帧数 {len(ts)}（跳帧 {skipped} 帧 = {100.0 * skipped / n_iter:.1f}%）",
        f"单帧耗时 ms（仅非跳帧）：p50={p(0.5):.1f}  p95={p(0.95):.1f}  "
        f"p99={p(0.99):.1f}  max={ts[-1] if ts else 0:.1f}",
        f"预算 50ms/帧 -> {'满足' if p(0.95) <= 50 else '不满足'}"
        + ("" if not ts else
           f"（p50 超 {p(0.5) / 50:.1f} 倍，p95 超 {p(0.95) / 50:.1f} 倍）"),
        f"手牌通道：非跳帧 {len(ts)} 帧内 {kept_calls} 次调用共 {kept_ms / 1000:.1f}s"
        f"（均 {kept_ms / max(1, len(ts)):.0f}ms/帧，占该组总耗时 "
        f"{100.0 * kept_ms / max(1.0, sum(ts)):.1f}%）",
        f"跳帧 {skipped} 帧：通道仍被调用 {skip_calls} 次、付 {skip_ms / 1000:.1f}s（均 "
        f"{skip_ms / max(1, skipped):.0f}ms/帧）"
        + ("  ← 跳帧**没**跳过识别，只省下决策与序列化：静默期的降频并不省钱"
           if skip_calls and skip_ms / max(1, skipped) >= 0.5 * kept_ms / max(1, len(ts))
           else ("  ← 跳帧时通道调用变便宜，降频部分生效" if skip_calls
                 else "  ← 跳帧完全跳过通道，不付识别的钱")),
        f"精度衰减：第 {len(imgs) + 1} 帧起与首轮基线比较，手牌多重集不一致 "
        f"{len(drift)} 帧" + (f"（前 5 例：{drift[:5]}）" if drift else ""),
    ]
    if rss0 is not None and rss_at:
        r_end = rss_mb()
        lines.append(f"内存：起始 {rss0:.0f}MB -> 结束 {r_end:.0f}MB（Δ {r_end - rss0:+.0f}MB）")
        lines.append("采样点(帧,RSS MB)：" + ", ".join(f"({i},{r:.0f})" for i, r in rss_at if r))
    else:
        lines.append("内存：本机取不到 RSS，未测，不猜")
    text = "\n".join(lines) + "\n"
    out.write(text)
    print(text)


def main():
    global FIX
    if "--fix" in sys.argv:
        FIX = os.path.abspath(sys.argv[sys.argv.index("--fix") + 1])
    # 平台跟着夹具走（见 `platform_from_fix`）；显式 --platform 仍然最大，
    # 便于做「同批帧换平台声明」的对照。顺序不能倒过来：--fix 晚于平台解析
    # 就会拿旧目录的名字去查平台，量出来的口径没人认得。
    platform = platform_from_fix()
    if "--platform" in sys.argv:
        platform = sys.argv[sys.argv.index("--platform") + 1]
    frames = load_frames()
    if not frames:
        print(f"夹具目录空：{FIX}")
        return 2
    det = TencentGridDetector()
    per_frame = "--per-frame" in sys.argv
    channels = "--channels" in sys.argv
    engine_mode = "--engine" in sys.argv
    n_eng = N_ITER
    if engine_mode:
        i = sys.argv.index("--engine") + 1
        if i < len(sys.argv) and sys.argv[i].isdigit():
            n_eng = int(sys.argv[i])
    eng_tag = ("_serial" if SERIAL else "") + (f"_w{WORKERS}" if WORKERS else "")
    out = os.path.join(REPO, "build",
                       "channels.txt" if channels else
                       ("per_frame.txt" if per_frame else
                        (f"engine_stream{eng_tag}.txt" if engine_mode else "latency.txt")))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        if channels:
            bench_channels(frames, platform, fp)
        elif engine_mode:
            bench_engine(frames, platform, fp, n_iter=n_eng)
        elif per_frame:
            bench_per_frame(det, frames, fp)
        else:
            bench_stream(det, frames, fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
