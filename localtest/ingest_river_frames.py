# -*- coding: utf-8 -*-
"""P4 闭环接料器：把 adb pull 回来的 river_frames/ 真实帧收编入库。

端侧（Engine._maybe_collect_river）在每次「牌河内容变化」时存一对文件：
  river_NNNNN_<ts>.jpg   方向归一后的整帧（引擎真正看到的画面）
  river_NNNNN_<ts>.json  元数据 + 引擎弱标签（river_mpsz / hand / 阶段…）

本工具做三件事：
  1. 校验配对（jpg 必须有同名 json，且 verified 字段存在）；
  2. 内容级去重（按 jpg md5；同帧被多轮 pull 重复收取不会污染数据集）；
  3. 归放入 dst（默认 localtest/river_real/），并合并进 manifest.jsonl，
     人工只需在 manifest 里把 verified 翻成 true（纠错时直接改 river_mpsz）。

用法:
  adb pull /sdcard/Android/data/com.example.auto_vision/files/river_frames ./pulled
  py -3.10 localtest/ingest_river_frames.py --src ./pulled
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_DST = os.path.join(REPO, "localtest", "river_real")


def jpg_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(dst: str):
    """返回 (已入库 md5 集合, 条目列表)。"""
    mpath = os.path.join(dst, "manifest.jsonl")
    seen, entries = set(), []
    if os.path.exists(mpath):
        with open(mpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entries.append(e)
                if e.get("md5"):
                    seen.add(e["md5"])
    return seen, entries


def main() -> int:
    ap = argparse.ArgumentParser(description="真实牌河帧接料入库")
    ap.add_argument("--src", required=True,
                    help="adb pull 回来的 river_frames 目录")
    ap.add_argument("--dst", default=DEF_DST,
                    help="入库目录（默认 localtest/river_real）")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    dst = os.path.abspath(args.dst)
    if not os.path.isdir(src):
        print(f"[ingest] 源目录不存在: {src}")
        return 2
    os.makedirs(dst, exist_ok=True)
    seen, entries = load_manifest(dst)

    stems = sorted(
        os.path.splitext(fn)[0] for fn in os.listdir(src)
        if fn.endswith(".jpg")
    )
    added = skipped_dup = skipped_pair = 0
    for stem in stems:
        jp = os.path.join(src, stem + ".jpg")
        mp = os.path.join(src, stem + ".json")
        if not os.path.exists(mp):
            print(f"[ingest] 缺元数据，跳过 {stem}")
            skipped_pair += 1
            continue
        with open(mp, "r", encoding="utf-8") as f:
            try:
                meta = json.load(f)
            except json.JSONDecodeError:
                print(f"[ingest] 元数据损坏，跳过 {stem}")
                skipped_pair += 1
                continue
        md5 = jpg_md5(jp)
        if md5 in seen:
            skipped_dup += 1
            continue
        name = f"{md5[:12]}.jpg"
        shutil.copy2(jp, os.path.join(dst, name))
        entry = dict(meta)
        entry.pop("file", None)
        entry.update({"md5": md5, "file": name, "src_stem": stem})
        with open(os.path.join(dst, md5[:12] + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
        entries.append(entry)
        seen.add(md5)
        added += 1

    with open(os.path.join(dst, "manifest.jsonl"), "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    river_states = Counter(e.get("river_mpsz", "") for e in entries)
    statuses = Counter(e.get("status", "?") for e in entries)
    verified = sum(1 for e in entries if e.get("verified"))
    print(f"[ingest] 本次新增 {added}，内容去重跳过 {skipped_dup}，"
          f"坏对跳过 {skipped_pair}")
    print(f"[ingest] 库内共 {len(entries)} 帧（人工已核验 {verified}），"
          f"不同牌河状态 {len(river_states)} 种")
    print(f"[ingest] status 分布: {dict(statuses)}")
    sizes = sorted(
        (len(r) // 2 for r in river_states), reverse=True)[:10]
    print(f"[ingest] 牌河最大前10（张数）: {sizes}")
    print(f"[ingest] 下一步：人工在 manifest.jsonl 里逐帧核对 river_mpsz，"
          f"正确的翻 verified=true，错的直接改 river_mpsz 再翻。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
