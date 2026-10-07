# -*- coding: utf-8 -*-
"""对全部有 GT 的帧跑生产链路，按平台汇总命中率并落盘。

为什么要单独一个脚本：ab_pitch_classify 的表格输出里混着引擎自身的大量
stdout 日志，还要在 PowerShell 里二次解析（末列带 ≠ 标记，按列切经常错位），
读错数字的成本比跑一遍还高。本脚本把结果直接写成纯文本，只留数字。

用法: py -3.10 localtest\score_gt.py [idx ...]
输出: localtest/gt_score.txt
"""
import os
import sys
from collections import Counter, defaultdict

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))

import ab_pitch_classify as AB  # noqa: E402

OUT = os.path.join(HERE, "gt_score.txt")


def main():
    args = [int(a) for a in sys.argv[1:] if a.isdigit()]
    idxs = sorted(args or AB.GT)
    rows, total_hit, total_n = [], 0, 0
    total_pos, total_miss, total_extra = 0, 0, 0
    by_plat = defaultdict(lambda: [0, 0, 0, 0, 0])

    for idx in idxs:
        f = next((x for x in os.listdir(AB.SHOTS) if x.startswith(f"calib_{idx:02d}_")), None)
        if not f:
            rows.append(f"[{idx:02d}] 缺帧")
            continue
        img = cv2.imread(os.path.join(AB.SHOTS, f))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        lbls, _nc, d = AB.run_img(img, idx, "prod")
        lbls = lbls or []
        det = AB.score_detail(lbls, AB.GT.get(idx))
        if det is None:
            rows.append(f"[{idx:02d}] 无法评分 status={(d or {}).get('status')}")
            continue
        g = (AB.GT[idx] or "").split()
        # 逐位口径（旧尺子）一并保留：换口径必须能对照，否则无法证明
        # 不是靠尺子刷分。它偏低是因为一次漏检会株连后面整行。
        pos_hit = sum(1 for i in range(min(len(lbls), len(g)))
                      if g[i] != "?" and lbls[i] == g[i])
        hit, n = det["hit"], det["n"]
        plat = AB.PLATFORM_OF.get(idx, "?")
        total_hit += hit
        total_n += n
        total_pos += pos_hit
        total_miss += det["missed"]
        total_extra += det["extra"]
        b = by_plat[plat]
        b[0] += hit
        b[1] += n
        b[2] += pos_hit
        b[3] += det["missed"]
        b[4] += det["extra"]
        flag = "" if hit == n else "   <-- 丢 {} 位".format(n - hit)
        rows.append(f"[{idx:02d}] {plat:11} {hit:2}/{n:2} = {hit / n * 100:5.1f}%"
                    f"  漏{det['missed']} 多{det['extra']}  (逐位 {pos_hit}){flag}")
        if hit != n:
            # 保序口径下“错在哪”不能再用下标说：漏检会让下标整体错位。
            # 改报“引擎输出了但不在真值多重集里”的牌，以及真值里没被匹配到的牌。
            gc = Counter(x for x in g if x != "?")
            pc = Counter(lbls)
            wrong = sorted((pc - gc).elements())
            lost = sorted((gc - pc).elements())
            rows.append(f"        多出: {' '.join(wrong) or '—'}   未认出: {' '.join(lost) or '—'}")

    lines = list(rows)
    lines.append("")
    lines.append("按平台（保序口径 | 逐位口径 | 漏检位 | 多检位）：")
    for plat in sorted(by_plat):
        hit, n, pos, miss, extra = by_plat[plat]
        lines.append(f"  {plat:12} {hit:3}/{n:3} = {hit / n * 100:5.1f}%   (逐位 {pos:3} = {pos / n * 100:5.1f}%)"
                     f"   漏{miss:2} 多{extra:2}")
    lines.append(f"  {'合计':12} {total_hit:3}/{total_n:3} = {total_hit / total_n * 100:5.1f}%"
                 f"   (逐位 {total_pos:3} = {total_pos / total_n * 100:5.1f}%)"
                 f"   漏{total_miss:2} 多{total_extra:2}")
    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"合计 {total_hit}/{total_n} = {total_hit / total_n * 100:.1f}%"
          f"（逐位 {total_pos} = {total_pos / total_n * 100:.1f}%） 详见 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
