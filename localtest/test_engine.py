"""引擎集成测试：验证 StructuralDetector 接入 Engine 后全流程可用。

用法: python localtest/test_engine.py [截图路径]
"""
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from engine.engine import Engine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SHOT = os.path.join(HERE, "screenshot.jpg")

# 期望手牌（与 run_structural 的 GT 一致，去掉花色分隔）
EXPECTED = "3m9m1s6s7s9s9s1p3p3p4p4p5p"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SHOT
    image = cv2.imread(path)
    assert image is not None, f"cannot read {path}"

    # 模拟设备端 JPEG50
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 50])
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    eng = Engine()
    res = eng.process(image)
    assert res is not None, "Engine.process returned None"
    data = json.loads(res.result)

    print("hand    :", data["hand"])
    print("count   :", data["count"])
    print("status  :", data["status"])
    print("tiles   :", len(data["tiles"]))
    print("top_score:", data["top_score"])
    print("screen  :", data["screen"])

    ok_hand = data["hand"] == EXPECTED
    ok_count = data["count"] == 13
    ok_status = data["status"] == "ok"
    ok_tiles = len(data["tiles"]) == 13

    print("\n=== 校验 ===")
    print(f"hand=={EXPECTED}: {ok_hand}")
    print(f"count==13     : {ok_count}")
    print(f"status==ok    : {ok_status}")
    print(f"tiles==13     : {ok_tiles}")

    # 校验 tiles 里低置信位是否被过滤（本图全高置信，应全部保留 label）
    labelled = sum(1 for t in data["tiles"] if t[4] != "")
    print(f"labelled tiles: {labelled}/13")

    assert ok_hand and ok_count and ok_status and ok_tiles, "ENGINE INTEGRATION FAILED"
    print("\nENGINE INTEGRATION OK")


if __name__ == "__main__":
    main()
