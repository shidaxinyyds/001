"""真机帧诊断台：把用户回传的截图逐张喂进冷启动引擎，dump 实际输出。

目的：用铁证定位「阶段误判 / 定缺错 / 换牌建议 / 出牌建议」到底是识别、
逻辑、还是集成/状态的问题。每张图独立冷启动（新 Engine），隔离单帧判定。

用法: py -3.10 localtest/diag_shots.py [图片目录]
"""
import contextlib
import glob
import io
import json
import os
import sys

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_DIR = (
    r"C:\Users\ing\AppData\Roaming\Qoder\SharedClientCache"
    r"\cache\images\task-c8a"
)


def run_one(path):
    img = cv2.imread(path)
    if img is None:
        return {"error": f"cannot read {path}"}
    # 模拟设备端 JPEG50 压缩
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    eng = Engine()  # 冷启动，隔离单帧
    with contextlib.redirect_stdout(io.StringIO()):  # 吞掉引擎 debug 打印
        res = eng.process(img)
    if res is None:
        return {"error": "process returned None"}
    d = json.loads(res.result)
    keep = {
        "status": d.get("status"),
        "count": d.get("count"),
        "hand": d.get("hand"),
        "dingque_phase": d.get("dingque_phase"),
        "swap_phase": d.get("swap_phase"),
        "pick_phase": d.get("pick_phase"),
        "dingque_suit": d.get("dingque_suit"),
        "dingque": d.get("dingque"),
        "message": d.get("message"),
        "best": d.get("best"),
        "shanten": d.get("shanten"),
        "advice": [
            {k: a.get(k) for k in ("tile", "reason", "ukeire") if k in a}
            for a in (d.get("advice") or [])[:3]
        ],
        "swap_advice": d.get("swap_advice"),
        "discards_n": len(d.get("discards") or "") // 2,
        "diag": {
            k: (d.get("diag") or {}).get(k)
            for k in ("raw", "yolo_river")
        },
    }
    return keep


def main():
    dirp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    shots = sorted(glob.glob(os.path.join(dirp, "*.jpg")))
    if not shots:
        print(f"no jpg under {dirp}")
        return
    outp = os.path.join(HERE, "diag_out.jsonl")
    with open(outp, "w", encoding="utf-8") as f:
        for i, p in enumerate(shots, 1):
            out = run_one(p)
            out["_i"] = i
            out["_name"] = os.path.basename(p)[:12]
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    print(f"wrote {len(shots)} rows -> {outp}")


if __name__ == "__main__":
    main()
