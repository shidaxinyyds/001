# -*- coding: utf-8 -*-
"""A/B：平台=tencent 时，蜀山(shushan)风格核是否只是白付的耗时。

口径：对 37 帧 verified GT 逐帧 fresh Engine，比较
  A 现状（active_styles 里的全部风格）
  B 裁掉 shushan 风格核
逐帧比对 hand / status / dingque 是否有任何变化，并给出单帧耗时。
裁法：在 _build_cores 之后过滤 _cores（同步过滤 _core_keys/_core_harvested，
      保持三轨同序），不改任何判据代码。
"""
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

from recognition import tencent_grid_detector as TGD  # noqa

DROP = set()
if len(sys.argv) > 1 and sys.argv[1] not in ("", "none"):
    DROP = set(sys.argv[1].split(","))
_orig_build = TGD.TencentGridDetector._build_cores


def _patched(self):
    _orig_build(self)
    keep = [i for i, c in enumerate(self._cores) if c[1] not in DROP]
    if len(keep) != len(self._cores):
        self._cores = [self._cores[i] for i in keep]
        if getattr(self, "_core_keys", None) is not None:
            self._core_keys = [self._core_keys[i] for i in keep]
        if getattr(self, "_core_harvested", None) is not None:
            self._core_harvested = [self._core_harvested[i] for i in keep]


if DROP:
    TGD.TencentGridDetector._build_cores = _patched

from engine.engine import Engine  # noqa

SHOT_DIR = os.path.join(REPO, "localtest", "shots")
GT_PATH = os.path.join(REPO, "localtest", "gt", "shots.json")


def one(img):
    with contextlib.redirect_stdout(io.StringIO()):
        r = Engine()
        t = time.perf_counter()
        d = json.loads(r.process(img).result)
        return d, (time.perf_counter() - t) * 1000.0


def main():
    gt = json.load(open(GT_PATH, encoding="utf-8"))["shots"]
    rows = []
    ts = []
    for e in gt:
        if not e.get("verified"):
            continue
        p = os.path.join(SHOT_DIR, e["file"])
        img = cv2.imread(p)
        if img is None:
            continue
        d, t = one(img)
        ts.append(t)
        rows.append((e["file"], e, d))
    print(f"帧数 {len(rows)}  裁掉风格 {sorted(DROP) or '无'}  "
          f"单帧均值 {sum(ts) / max(1, len(ts)):.1f}ms")
    bad = 0
    for f, e, d in rows:
        gh = sorted([e["hand"][i:i + 2] for i in range(0, len(e["hand"]), 2)])
        ah = sorted([d.get("hand", "")[i:i + 2] for i in range(0, len(d.get("hand", "")), 2)])
        okh = (gh == ah)
        oks = d.get("status") == e.get("status")
        if not okh or not oks:
            bad += 1
        print(f"{'OK ' if (okh and oks) else 'X  '} {f:24s} "
              f"status={d.get('status')}/{e.get('status')} "
              f"dq={d.get('dingque_suit')}/{e.get('dingque')} "
              f"hand{'=' if okh else '≠'}")
    print(f"不匹配帧数：{bad}")


if __name__ == "__main__":
    main()
