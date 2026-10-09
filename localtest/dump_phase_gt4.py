# -*- coding: utf-8 -*-
"""真机四帧当前读数：逐帧喂 4 次，打印 status/count/hand/phase/dingque。"""
import contextlib, io, json, os, sys
import cv2
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))
from engine.engine import Engine  # noqa

SHOTS = os.path.join(HERE, "shots_tuyou_select")
NAMES = ["s1_select_cd17.jpg", "s2_select_cd07.jpg",
         "s3_select_cd01.jpg", "s4_after_swap_cd04.jpg"]

out = []
eng = Engine()
eng.set_platform("tencent")
eng.set_mode("sc_hz")
for n in NAMES:
    p = os.path.join(SHOTS, n)
    img = cv2.imread(p)
    if img is None:
        out.append(f"{n}: MISSING")
        continue
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    d = None
    for _ in range(4):
        with contextlib.redirect_stdout(io.StringIO()):
            r = eng.process(img)
        d = json.loads(r.result) if r is not None else None
    mp = (d or {}).get("match_phase") or {}
    out.append(
        f"{n}  status={d.get('status')} count={d.get('count')} "
        f"hand={d.get('hand')!r}\n    phase={mp.get('phase')} "
        f"dq_suit={d.get('dingque_suit')} dq_name={d.get('dingque_name')!r} "
        f"swap={d.get('swap_phase')} dq_phase={d.get('dingque_phase')} "
        f"pick={d.get('pick_phase')}\n    turn_basis={mp.get('turn_basis')!r} "
        f"hint={mp.get('hint')!r}")

txt = "\n".join(out)
os.makedirs(os.path.join(REPO, "build"), exist_ok=True)
with open(os.path.join(REPO, "build", "gt4_now.txt"), "w", encoding="utf-8") as fp:
    fp.write(txt + "\n")
print("written")
