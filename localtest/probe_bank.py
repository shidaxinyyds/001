# -*- coding: utf-8 -*-
"""单变量隔离：某个 extra bank 的开/关对指定帧的命中率影响。

为什么必须用子进程 + 环境变量：bank 是在 TencentGridDetector.__init__ 里
_build_cores 时展开成模板向量的，而 detector 是跨 Engine() 缓存的单例——
在同一进程里改 EXTRA_BANKS 再去跑，用的还是上一次已经建好的向量，
测出来的"没有影响"是假的。

用法（PowerShell 用 ; 串联，不要用 &&）:
  $env:DISABLE_BANKS=""; py -3.10 localtest\probe_bank.py 33 34 36 40
  $env:DISABLE_BANKS="weile"; py -3.10 localtest\probe_bank.py 33 34 36 40
输出: localtest/bank_probe.txt
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))

_drop = {s for s in (os.environ.get("DISABLE_BANKS") or "").split(",") if s.strip()}
if _drop:
    from recognition import tencent_grid_detector as _TGD  # noqa: E402
    _TGD.EXTRA_BANKS = tuple(m for m in _TGD.EXTRA_BANKS if m[1] not in _drop)

import cv2  # noqa: E402
import ab_pitch_classify as AB  # noqa: E402

OUT = os.path.join(HERE, "bank_probe.txt")


def main():
    idxs = sorted(int(a) for a in sys.argv[1:] if a.isdigit()) or sorted(AB.GT)
    tag = ",".join(sorted(_drop)) or "（全开）"
    lines = [f"禁用 bank = {tag}"]
    hit_all = n_all = 0
    by = {}
    for idx in idxs:
        f = next((x for x in os.listdir(AB.SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            continue
        img = cv2.imread(os.path.join(AB.SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        lbls, _nc, _d = AB.run_img(img, idx, "prod")
        s = AB.score(lbls or [], AB.GT.get(idx))
        if "/" not in s:
            lines.append(f"[{idx:02d}] 无法评分")
            continue
        hit, n = (int(x) for x in s.split("/"))
        plat = AB.PLATFORM_OF.get(idx, "?")
        hit_all += hit
        n_all += n
        b = by.setdefault(plat, [0, 0])
        b[0] += hit
        b[1] += n
        lines.append(f"[{idx:02d}] {plat:11} {hit:2}/{n:2}")
    for plat in sorted(by):
        h, n = by[plat]
        lines.append(f"  {plat:12} {h:3}/{n:3} = {h / n * 100:5.1f}%")
    lines.append(f"  合计         {hit_all:3}/{n_all:3} = {hit_all / max(n_all, 1) * 100:5.1f}%")
    text = "\n".join(lines) + "\n"
    # 追加而不是覆盖：这个脚本的价值就在于两次运行的差值，分开存会丢对照。
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
