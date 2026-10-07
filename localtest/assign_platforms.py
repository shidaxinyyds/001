# -*- coding: utf-8 -*-
"""把「无监督分组 + 人工水印标注」合成每帧的平台真值，并与探针归因对拍。

这一步是整条链的**事实基准**：后面收割 bank、钉 GT、算逐平台准确率，全都按这里的
平台归属来分组。之所以不信探针的归属而要多做一次人工标注，是因为探针从原理上认不出
没挂 bank 的平台——它只会给出“最像的已知风格”，于是新平台会被**自信地归错**（本批
15 帧指尖四川就是例子）。对拍表就是把这个错率量化出来，而不是留一句“探针不太准”。

同时做污染核查：新帧若与已用于收割 bank / 已钉 GT 的旧帧字节相同，则它对这些平台
**不是样本外**，拿它算出来的百分比是自欺。这里按 md5 逐帧比对并单列出来。

用法：
    py -3.10 -X utf8 localtest/assign_platforms.py --src public/1 \
        --clusters build/frame_clusters_1 --probe build/platform_probe_1.txt
输出：build/frame_platforms_<tag>.json  {文件名: {platform, group, probe_style, dup_of}}
      build/frame_platforms_<tag>.txt   对拍表 + 污染清单
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "android", "app", "src", "main", "python"))

import platforms  # noqa: E402

# 重复分三类，必须区分对待（弄混就会拿样本内数字当样本外）：
#   harvest = 图本身已被收割进某个 bank（该 bank 的模板就是从它裁的）→ 对该平台
#             **永久样本内**，不能用来报准确率；
#   gt      = 已在 localtest/gt/*.json 钉过人工 GT → 可用，但不能重复计入新样本量；
#   repeat  = 只是上一批素材里出现过（未收割、未钉 GT）→ 仍是有效样本外数据。
HARVEST_DIRS = ["localtest/shots_calib", "localtest/shots", "localtest/shots_shushan",
                "localtest/shots_tuyou", "localtest/shots_tencent", "localtest/shots_diag",
                "localtest/harvest_shushan", "localtest/shots_montage"]
# 上一批素材：与它重复只说明“不是新图”，并不构成样本内（它从未被收割进任何 bank、
# 也未钉过 GT），所以单独计数而不排除。
SEEN_DIRS = ["public/0"]
PROBE_LINE = re.compile(r"^\[(\d+)\].*?归属=(\S+)")
LABEL_LINE = re.compile(r"^(G\d+)\s+(\S+)")


def gt_files():
    """已钉 GT 的帧名（两个 GT 集都算）。"""
    out = set()
    for p in ("localtest/gt/shots.json", "localtest/gt/new_shots.json"):
        fp = os.path.join(REPO, p)
        if not os.path.exists(fp):
            continue
        with open(fp, encoding="utf-8") as fh:
            for s in json.load(fh).get("shots", []):
                if s.get("file"):
                    out.add(os.path.basename(s["file"]))
    return out


def md5(path, cache={}):
    if path not in cache:
        h = hashlib.md5()
        with open(path, "rb") as fp:
            for blk in iter(lambda: fp.read(1 << 20), b""):
                h.update(blk)
        cache[path] = h.hexdigest()
    return cache[path]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO, "public", "1"))
    ap.add_argument("--clusters", default=None, help="默认 build/frame_clusters_<tag>")
    ap.add_argument("--probe", default=None, help="默认 build/platform_probe_<tag>.txt")
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    tag = os.path.basename(src) or "root"
    cdir = a.clusters or os.path.join(REPO, "build", f"frame_clusters_{tag}")
    pfile = a.probe or os.path.join(REPO, "build", f"platform_probe_{tag}.txt")

    files = sorted(f for f in os.listdir(src)
                   if f.lower().endswith((".jpg", ".jpeg", ".png")))
    idx2file = {i + 1: f for i, f in enumerate(files)}

    # 1) 分组 + 人工标签
    members, gid2label = {}, {}
    cur = None
    with open(os.path.join(cdir, "groups.txt"), encoding="utf-8") as fp:
        for ln in fp:
            m = re.match(r"^(G\d+): ", ln)
            if m:
                cur = m.group(1)
                members[cur] = []
                continue
            if cur and ln.startswith("   #"):
                members[cur] += [int(x.lstrip("#")) for x in ln.split()]
    with open(os.path.join(cdir, "labels.txt"), encoding="utf-8") as fp:
        for ln in fp:
            m = LABEL_LINE.match(ln.strip())
            if m:
                gid2label[m.group(1)] = m.group(2)

    # 2) 探针归因（按报告里的 [NN] 序号）
    probe = {}
    if os.path.exists(pfile):
        with open(pfile, encoding="utf-8") as fp:
            for ln in fp:
                m = PROBE_LINE.match(ln.strip())
                if m:
                    probe[idx2file.get(int(m.group(1)), "?")] = m.group(2)

    # 3) 污染核查（按上面三类分别比 md5）
    known = {}
    for d in HARVEST_DIRS:
        dd = os.path.join(REPO, d)
        if not os.path.isdir(dd):
            continue
        for f in sorted(os.listdir(dd)):
            p = os.path.join(dd, f)
            if os.path.isfile(p) and f.lower().endswith((".jpg", ".jpeg", ".png")):
                known.setdefault(md5(p), ("harvest", f"{d}/{f}"))
    with_gt = gt_files()
    seen = {}
    for d in SEEN_DIRS:
        dd = os.path.join(REPO, d)
        if not os.path.isdir(dd):
            continue
        for f in sorted(os.listdir(dd)):
            p = os.path.join(dd, f)
            if os.path.isfile(p) and f.lower().endswith((".jpg", ".jpeg", ".png")):
                seen.setdefault(md5(p), f"{d}/{f}")

    rows, dup = {}, collections.Counter()
    for gid, ms in members.items():
        plat = gid2label.get(gid, "")
        for j in ms:
            f = idx2file[j]
            hit = known.get(md5(os.path.join(src, f)))
            if hit:
                kind, where = hit
            elif f in with_gt:
                kind, where = "gt", "已钉 GT"
            else:
                kind, where = "", ""
            if kind:
                dup[kind] += 1
                dup[f"{kind}:{plat}"] += 1
            rows[f] = {"platform": plat or "unlabeled", "group": gid,
                       "probe_style": probe.get(f, ""),
                       "dup_kind": kind, "dup_of": where or "",
                       "seen_in": seen.get(md5(os.path.join(src, f)), "")}
    by_plat = collections.Counter(r["platform"] for r in rows.values())
    # 4) 对拍：人工平台真值 vs 探针风格归属
    cross = collections.Counter((r["platform"], r["probe_style"]) for r in rows.values())
    valid = [(p, s) for (p, s), n in cross.items()
             if p not in ("unknown", "unlabeled") for _ in range(n)]
    n_valid = len(valid)
    agree = sum(1 for p, s in valid if p == s or (p == "gd_queshen" and s == "queshen"))

    lines = []
    lines.append(f"== 素材 {src}：{len(rows)} 帧 ==")
    lines.append("按人工水印标注的平台分布：")
    for p, n in by_plat.most_common():
        star = "" if p in platforms.PLATFORMS else "（非平台 key）"
        lines.append(f"  {p}: {n} 帧{star}")
    lines.append(f"\n人工真值 vs 探针归因一致率：{agree}/{n_valid} = "
                 f"{agree / max(1, n_valid):.1%}（只统计已定平台的帧）")
    lines.append("混淆明细（平台 → 探针说成谁）：")
    for (p, s), n in sorted(cross.items(), key=lambda kv: (-kv[1], kv[0])):
        flag = "" if p == s or (p == "gd_queshen" and s == "queshen") else "   ← 错"
        lines.append(f"  {p:>12} → {s:<10} {n:>3} 帧{flag}")
    lines.append(f"\n重复核查：已收割 bank 内 {dup['harvest']} 帧、已钉 GT {dup['gt']} 帧"
                 f"（其余为可用新样本）")
    lines.append("按平台拆（harvest 帧对该平台不能算准确率）：")
    for k in sorted([x for x in dup if ":" in x]):
        kind, plat = k.split(":", 1)
        lines.append(f"  {plat:>12} {kind}: {dup[k]} 帧")
    lines.append("可用于评测的帧（未收割、未钉 GT）：")
    usable = collections.Counter(r["platform"] for r in rows.values() if not r["dup_kind"])
    for p, n in usable.most_common():
        lines.append(f"  {p:>12}: {n} 帧 ≈ {n * 13} 张")
    n_seen = sum(1 for r in rows.values() if r["seen_in"] and not r["dup_kind"])
    lines.append(f"其中与上一批（public/0）同图：{n_seen} 帧——不是新素材，但未被收割，"
                 f"仍属样本外可用数据")

    out_json = os.path.join(REPO, "build", f"frame_platforms_{tag}.json")
    with open(out_json, "w", encoding="utf-8") as fp:
        json.dump(rows, fp, ensure_ascii=False, indent=1, sort_keys=True)
    out_txt = os.path.join(REPO, "build", f"frame_platforms_{tag}.txt")
    text = "\n".join(lines) + "\n"
    with open(out_txt, "w", encoding="utf-8") as fp:
        fp.write(text)
    print(text)
    print(f"-> {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
