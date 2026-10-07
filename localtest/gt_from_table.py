# -*- coding: utf-8 -*-
"""把「人工读牌结果」的文本表编译成 GT json。

为什么要中间这层文本表：读图结果是一段一段产生的（一次读几帧、隔几天再补几帧），
直接手改 json 会遇到两个老问题——(1) 长 hash 文件名手抄必错；(2) 改一处缩进就把
整份 GT 弄坏，而 GT 坏了不会报错，只会静默把准确率算错。文本表一行一帧、只含
`平台 表号 标签`，文件名/分辨率一律从 build/ann_<平台>/rects_NN.json 取，
所以 GT 里的 file 与读图时用的裁片**必然同源**。

表语法（# 开头与空行忽略）：
    <平台key> <表号> <label> [<label> ...] [+<label> ...]
    <平台key> <表号> -            # 减号 = 这帧读不准/状态不标准，显式弃用
                                   （必须写出来，否则下一个人不知道这帧是漏了还是被否了）

`+label` 是**检测层 FN 的台账**：牌确实在立牌行里、人眼读得出，但检测器没框到。
为什么需要它而不是直接把标签补进 hand：harvest 的硬门是「标签数 == 框数」，补进去
会整帧拒收（拿 11 张真机模板换两格 FN 不划算）；而不写下来就等于静默吞掉——
逐位指标只比“检出的框”，漏框的牌既不进分子也不进分母（这就是 b7 要量的 TP/FP/FN
里那两个字母）。所以它单独存进 GT 的 `missed` 字段，由评测按检测层口径报出。
注意「行尾单独摆的摸牌」不算 FN（也不属于 hand 口径）：它走 last_drawn_tile 另一路。

style 取平台 key 在 eval_new_material.STYLE_OF 的反向名（GT 里沿用的风格短名）。

运行：py -3.10 -X utf8 localtest/gt_from_table.py localtest/gt/labels_b1.txt \
        --base localtest/gt/shots_b1.json --out localtest/gt/shots_b1.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from eval_new_material import STYLE_OF  # noqa: E402

# 平台 key -> 风格短名（与 eval_new_material.STYLE_OF 同一张表反过来用）：
# 文本表里写的是平台 key（与 assign_platforms.py 的平台真值一致），
# 而 GT json 里沿用的是风格短名（bank 的 style 名），两者不是一回事。
STYLE_OF_PLATFORM = {v: k for k, v in STYLE_OF.items()}


def parse_table(path):
    rows = []
    with open(path, encoding="utf-8") as fp:
        for ln, line in enumerate(fp, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) < 2:
                raise SystemExit(f"{path}:{ln} 至少要有「平台 表号」两列：{s}")
            plat, idx = parts[0], int(parts[1])
            toks = parts[2:]
            if toks == ["-"]:
                toks = []
            # `+x` = 漏框台账（FN），其余按序是 hand；混着写不允许（hand 必须连续在前）
            plus = [i for i, t in enumerate(toks) if t.startswith("+")]
            if plus and min(plus) < len([t for t in toks if not t.startswith("+")]):
                raise SystemExit(f"{path}:{ln}：`+label`（漏框台账）必须写在所有 hand 标签"
                                 f"之后，否则看不出哪一格没框到：{s}")
            hand = [t for t in toks if not t.startswith("+")]
            missed = [t[1:] for t in toks if t.startswith("+")]
            for t in missed:
                if t == "?":
                    raise SystemExit(f"{path}:{ln}：`+?` 无意义（漏框的牌必须读得出才能记账）")
            rows.append((plat, idx, hand, missed, ln))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--base", default=None, help="已有 GT json（保留其中未由本表管理的条目）")
    ap.add_argument("--out", default=None, help="默认写到 --base（就地更新）")
    ap.add_argument("--src", default="public/1")
    ap.add_argument("--style", default=None,
                    help="只编译该风格（平台 key）的帧，其余保留 base 里的原条目")
    a = ap.parse_args()

    base = {"_note": "", "shots": []}
    if a.base and os.path.exists(a.base):
        with open(a.base, encoding="utf-8") as fp:
            base = json.load(fp)

    shots = [e for e in base.get("shots", [])
             if a.style is None or e.get("style") != a.style]
    known = {(e.get("style"), e.get("frame")) for e in shots}
    n_new = 0
    n_upd = 0
    for plat, idx, hand, missed, ln in parse_table(a.table):
        st = STYLE_OF_PLATFORM.get(plat)
        if st is None:
            raise SystemExit(f"第 {ln} 行：平台 {plat} 不在 STYLE_OF 反查表里，"
                             f"先在 eval_new_material.py 登记（否则评测会按错的平台白名单跑）")
        if a.style and st != a.style:
            continue
        rp = os.path.join(REPO, "build", f"ann_{plat}", f"rects_{idx:02d}.json")
        if not os.path.exists(rp):
            raise SystemExit(f"第 {ln} 行：找不到 {rp}（先跑 make_tile_sheets.py "
                             f"--src {a.src} --platform {plat}）")
        with open(rp, encoding="utf-8") as fp:
            r = json.load(fp)
        if not hand:
            print(f"  跳过 {plat}#{idx:02d}（表里显式标为弃用）")
            continue
        if len(hand) != r["n_tiles"]:
            raise SystemExit(f"第 {ln} 行：标签 {len(hand)} 个 != 裁片 {r['n_tiles']} 枚。"
                             f"读图是按裁片读的，数量对不上说明漏读/多读了一格，"
                             f"错位会把整行标签平移（比少一帧严重得多）")
        key = (st, idx)
        cur = next((e for e in shots if (e.get("style"), e.get("frame")) == key), None)
        if cur is not None:
            # 文本表是标签的唯一真源（见下面写进 json 的 _anno_tool），所以这里必须
            # 覆盖而不是跳过：跳过会让“改了表、重编译、跑评测”三步都静默成功，而
            # 评测用的还是旧标签——纠错等于没做，且没有任何地方报错。
            if cur["hand"] != hand or cur.get("missed", []) != missed:
                print(f"  更新 {st}#{idx:02d}：\n    旧 " + " ".join(cur["hand"])
                      + ("   漏框 " + " ".join(cur.get("missed", []))
                         if cur.get("missed") else "")
                      + "\n    新 " + " ".join(hand)
                      + ("   漏框 " + " ".join(missed) if missed else ""))
                cur["hand"] = hand
                # 不写 `+` 时必须把旧台账抹掉：留着就是拿上一版的人工结论冒充
                # 本版（比如后来修好了漏框，帧不再少框，台账还在扣 recall）。
                if missed:
                    cur["missed"] = missed
                else:
                    cur.pop("missed", None)
                n_upd += 1
            continue
        e = {"frame": idx, "file": r["file"], "src": a.src, "size": r["size"],
             "style": st, "verified": True, "hand": hand}
        if missed:
            e["missed"] = missed
        shots.append(e)
        known.add(key)
        n_new += 1
    shots.sort(key=lambda e: (e["style"], e["frame"]))
    base["shots"] = shots
    base["_anno_tool"] = ("由 localtest/gt_from_table.py 从 localtest/gt/labels_*.txt 编译；"
                          "改标签请改文本表再重编译，不要直接改本 json。")
    out = a.out or a.base
    if not out:
        print(json.dumps(base, ensure_ascii=False, indent=2))
        return 0
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(json.dumps(base, ensure_ascii=False, indent=2) + "\n")
    tot = sum(len(e["hand"]) for e in shots)
    n_mis = sum(len(e.get("missed", [])) for e in shots)
    # 落盘后回读校验：表里每一行的标签都必须在 json 里逐字相等。这一条堵死所有
    # “写了但没生效”的路径（就地更新时抛异常、字段名写错、排序把条目弄丢）。
    with open(out, encoding="utf-8") as fp:
        back = {(e["style"], e["frame"]): e for e in json.load(fp)["shots"]}
    n_chk = 0
    for plat, idx, hand, missed, ln in parse_table(a.table):
        if not hand:
            continue          # 表里显式标为弃用的帧不进 json，无物可校
        st = STYLE_OF_PLATFORM.get(plat)
        if a.style and st != a.style:
            continue
        ent = back.get((st, idx))
        if ent is None or ent["hand"] != hand or ent.get("missed", []) != missed:
            raise SystemExit(f"{out}:{ln} 回读校验失败：{st}#{idx:02d} 表里是 "
                             f"{' '.join(hand)}" +
                             (f" / 漏框 {' '.join(missed)}" if missed else "") +
                             f"，json 里是 {None if ent is None else ent['hand']}")
        n_chk += 1
    print(f"[ok] 新增 {n_new} 帧 / 更新 {n_upd} 帧 -> {out}（共 {len(shots)} 帧 / {tot} 张，"
          f"另记检测层漏框 {n_mis} 张，回读校验 {n_chk} 帧一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
