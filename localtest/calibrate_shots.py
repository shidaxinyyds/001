# -*- coding: utf-8 -*-
"""对 shots_calib 全量实战截图跑一次完整识别链路，产出逐图诊断清单。

每张图：模拟设备端 JPEG50 压缩 -> Engine.set_platform -> Engine.process，
记录 hand/count/status/top_score/tiles 与手牌带几何，写出 JSONL + 控制台汇总，
并把各平台预设 hand_roi 框出的区域裁成长条图存到 shots_calib/roi/ 供人工核对。

平台归属按画幅 + 界面特征推断（见 PLATFORM_OF），后续人工核验后再修正。

用法: py -3.10 localtest/calibrate_shots.py [--only 01,09,34] [--jpg 50] [--repeat 4]

注意 --repeat：引擎里换牌/定缺确认、手牌稳定器 2 帧共识、投票窗口都是多帧门控，
真机是静止机位连续取帧，所以单帧单次跑会把「门控还没满足」误判成「识别不出来」。
默认把同一张图连续喂 4 帧，取末帧结果，并记录第几帧才锁定（lock_frame）。
"""
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PYROOT = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402
from platforms import get_hand_roi  # noqa: E402

SHOTS = os.path.join(HERE, "shots_calib")
ROI_DIR = os.path.join(SHOTS, "roi")
VIZ_DIR = os.path.join(SHOTS, "viz")
OUT_JSONL = os.path.join(HERE, "calib_report.jsonl")

# 画幅指纹 -> 平台。同一次导入按缓存落盘时间排序，批次边界见 scan 输出。
PLATFORM_OF = {}
for i in range(1, 13):
    PLATFORM_OF[i] = "weile"            # 01~12 微乐
PLATFORM_OF[13] = "shushan"             # 13 蜀山四川麻将(贵阳捉鸡)
for i in range(14, 22):
    PLATFORM_OF[i] = "gd_queshen"       # 14~21 雀神四川麻将
for i in list(range(22, 26)) + list(range(27, 33)):
    PLATFORM_OF[i] = "jj"               # 22~25,27~32 JJ麻将
for i in [26] + list(range(33, 42)):
    PLATFORM_OF[i] = "tuyou"            # 26,33~41 途游四川麻将


def parse_only(argv):
    if "--only" not in argv:
        return None
    seg = argv[argv.index("--only") + 1]
    return {int(x) for x in seg.replace(" ", "").split(",") if x.strip()}


def jpg_quality(argv):
    return int(argv[argv.index("--jpg") + 1]) if "--jpg" in argv else 50


def repeat_n(argv):
    return max(1, int(argv[argv.index("--repeat") + 1])) if "--repeat" in argv else 4


def out_jsonl(argv):
    """报告路径可改。默认路径会被每次跑覆写（"w"），所以做「加/不加 bank」
    对照时必须给基线跑单独指定 --out，否则基线会被后一次跑冲掉。"""
    return argv[argv.index("--out") + 1] if "--out" in argv else OUT_JSONL


def apply_bank_filter(argv):
    """--disable-bank queshen[,...]：按名字从加载清单里移除风格 bank。

    新增 bank 最大的风险是跨风格泄漏（雀神模板去抢微乐/途游的牌），
    必须能做只改这一个变量的 A/B 对照。
    """
    if "--disable-bank" not in argv:
        return
    names = {x for x in argv[argv.index("--disable-bank") + 1].split(",") if x}
    from recognition import tencent_grid_detector as TGD
    kept = [(m, s) for m, s in TGD.EXTRA_BANKS if s not in names]
    dropped = [s for _m, s in TGD.EXTRA_BANKS if s in names]
    TGD.EXTRA_BANKS = tuple(kept)
    print(f"[harness] 已禁用 bank: {dropped}")


def band_from_tiles(tiles):
    """由检测框反推实际手牌带的行区间（图像坐标 y0,y1）。"""
    ys = [t[1] for t in tiles if t[5] == "hand"] + [t[1] + t[3] for t in tiles if t[5] == "hand"]
    return (int(min(ys)), int(max(ys))) if ys else None


def save_roi_strip(img, platform_key, idx, q):
    """把该平台 hand_roi 框出的区域裁出来存图，用于肉眼核对 ROI 是否框住手牌。"""
    h, w = img.shape[:2]
    top, bottom, left, right = get_hand_roi(platform_key)
    y0, y1 = int(h * top), int(h * bottom)
    x0, x1 = int(w * left), int(w * right)
    crop = img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    if crop.size == 0:
        return None
    scale = min(1.0, 1400 / max(1, crop.shape[1]))
    if scale < 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    p = os.path.join(ROI_DIR, f"{idx:02d}_{platform_key}.jpg")
    cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 82])[1].tofile(p)
    return p


def save_viz(img, rec, tiles):
    """把检测到的手牌框 + label 叠回原图，用于人工核对 GT 与漏检。"""
    h, w = img.shape[:2]
    canvas = img.copy()
    for t in tiles:
        x, y, tw, th, label, kind = t[0], t[1], t[2], t[3], t[4], t[5]
        color = (0, 220, 0) if kind == "hand" else (0, 160, 255)
        cv2.rectangle(canvas, (x, y), (x + tw, y + th), color, 2)
        if kind == "hand":
            cv2.putText(canvas, label or "?", (x + 2, max(16, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)
    top, bottom = rec["hand_roi"][0], rec["hand_roi"][1]
    for frac, col in ((top, (255, 0, 255)), (bottom, (255, 0, 255))):
        yy = int(h * frac)
        cv2.line(canvas, (0, yy), (w, yy), col, 2)
    scale = min(1.0, 1500 / max(1, w))
    if scale < 1.0:
        canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    p = os.path.join(VIZ_DIR, f"{rec['idx']:02d}_{rec['platform']}.jpg")
    cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 84])[1].tofile(p)
    return p


def main():
    argv = sys.argv[1:]
    apply_bank_filter(argv)
    only = parse_only(argv)
    q = jpg_quality(argv)
    n_rep = repeat_n(argv)
    os.makedirs(ROI_DIR, exist_ok=True)
    os.makedirs(VIZ_DIR, exist_ok=True)

    files = sorted(f for f in os.listdir(SHOTS) if f.lower().endswith(".jpg"))
    rows = []
    for f in files:
        idx = int(f.split("_")[1][:2])
        if only is not None and idx not in only:
            continue
        platform = PLATFORM_OF.get(idx, "generic")
        img = cv2.imread(os.path.join(SHOTS, f))
        if img is None:
            continue
        h, w = img.shape[:2]
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        assert ok
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)

        eng = Engine()
        eng.set_platform(platform)
        d, lock_frame, err = {}, None, ""
        for k in range(n_rep):
            try:
                res = eng.process(frame)
                d = json.loads(res.result) if res else {}
            except Exception as e:  # 单张图崩了不能阻断全量盘点
                d = {"status": f"EXC:{type(e).__name__}", "message": str(e)[:160]}
                err = f"{type(e).__name__}: {e}"
                break
            if (d.get("count") or 0) > 0 and d.get("status") not in ("waiting", "no_tiles"):
                lock_frame = k + 1
                break
        if lock_frame is None:
            lock_frame = -1  # 喂满 n_rep 帧仍未锁定

        tiles = d.get("tiles") or []
        hand_tiles = [t for t in tiles if len(t) > 5 and t[5] == "hand"]
        rec = {
            "idx": idx,
            "file": f,
            "platform": platform,
            "size": [w, h],
            "ar": round(w / h, 3),
            "hand_roi": list(get_hand_roi(platform)),
            "status": d.get("status"),
            "message": (d.get("message") or err or "")[:120],
            "lock_frame": lock_frame,
            "hand": d.get("hand") or "",
            "count": d.get("count"),
            "n_hand_tiles": len(hand_tiles),
            "top_score": d.get("top_score"),
            "styles": d.get("styles"),
            "glyphs": d.get("glyphs"),
            "screen": d.get("screen"),
            "elapsed": d.get("elapsed"),
            "detected_band": band_from_tiles(tiles),
            "labels": [t[4] for t in hand_tiles],
        }
        rows.append(rec)
        roi = save_roi_strip(img, platform, idx, q)
        rec["roi_img"] = os.path.relpath(roi, HERE) if roi else None
        v = save_viz(img, rec, tiles)
        rec["viz_img"] = os.path.relpath(v, HERE)
        print(f"[{idx:02d}] {platform:11} {w}x{h} ar={rec['ar']:.2f} "
              f"status={str(rec['status']):9} count={rec['count']} "
              f"lock={rec['lock_frame']} top={rec['top_score']} "
              f"hand={rec['hand'][:40]}")

    with open(out_jsonl(sys.argv[1:]), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ===== 汇总 =====
    print("\n=== 汇总 ===")
    bad_status = [r for r in rows if r["status"] not in ("ok", "no_hand", "pending")]
    zero = [r for r in rows if (r["count"] or 0) == 0]
    low = [r for r in rows if (r["top_score"] or 0) < 0.45]
    slow = [r for r in rows if r["lock_frame"] > 1]
    print(f"总计 {len(rows)} 张 | 非正常status {len(bad_status)} | 0张手牌 {len(zero)} "
          f"| top<0.45 {len(low)} | 需>1帧才锁定 {len(slow)}")
    for r in rows:
        flags = []
        if r["status"] not in ("ok", "no_hand", "pending"):
            flags.append(f"STATUS:{r['status']}")
        if (r["count"] or 0) == 0:
            flags.append("EMPTY")
        if r["lock_frame"] < 0:
            flags.append(f"NOLOCK:{n_rep}帧")
        elif r["lock_frame"] > 1:
            flags.append(f"LOCK@{r['lock_frame']}")
        if (r["top_score"] or 0) < 0.45:
            flags.append(f"LOWTOP:{r['top_score']}")
        if (r["count"] or 0) not in (13, 14) and (r["count"] or 0) > 0:
            flags.append(f"COUNT:{r['count']}")
        if flags:
            print(f"  [{r['idx']:02d}] {r['platform']:11} {' '.join(flags)}  {r['message']}")
    print(f"\nJSONL -> {out_jsonl(sys.argv[1:])}")
    print(f"ROI裁图 -> {ROI_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
