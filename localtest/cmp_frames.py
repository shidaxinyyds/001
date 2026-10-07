# -*- coding: utf-8 -*-
"""同进程对照：ab_pitch_classify 的取数能出 14 张，montage_for_gt.hand_tiles 出 0 张。

两段代码逐行同构（同目录、同 JPEG50、同 4 帧、同 t[5]=="hand" 过滤），却结果相反。
在同一进程里按「先 hand_tiles 后 ab」与「先 ab 后 hand_tiles」两种顺序各跑一次，
用来区分到底是代码差异还是进程内状态差异（例如平台/模式文件轮询缓存）。

用法: py -3.10 localtest\cmp_frames.py 14
"""
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from engine.engine import Engine  # noqa: E402
from montage_for_gt import PLATFORM_OF, SHOTS, hand_tiles  # noqa: E402


def ab_style(idx):
    """完全照抄 ab_pitch_classify.run 的生产分支。"""
    f = next((x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
    img = cv2.imread(os.path.join(SHOTS, f))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    eng = Engine()
    eng.set_platform(PLATFORM_OF.get(idx, "generic"))
    d = None
    for _ in range(4):
        d = json.loads(eng.process(img).result)
        if (d.get("count") or 0) > 0 and d.get("status") not in ("waiting", "no_tiles"):
            break
    tiles = sorted([t for t in (d.get("tiles") or []) if len(t) > 5 and t[5] == "hand"],
                   key=lambda t: t[0])
    return d, tiles


def report(tag, d, tiles):
    print(f"  {tag}: status={d.get('status')} count={d.get('count')} "
          f"nHand={len(tiles)} platform={d.get('platform')} mode={d.get('mode')}")


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    print(f"[顺序 A] 先 hand_tiles，再 ab 复刻")
    img, ts = hand_tiles(idx)
    print(f"  hand_tiles: n={len(ts)}")
    d, tiles = ab_style(idx)
    report("ab 复刻", d, tiles)

    print(f"[顺序 B] 再来一次 ab 复刻")
    d2, tiles2 = ab_style(idx)
    report("ab 复刻#2", d2, tiles2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
