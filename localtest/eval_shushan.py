# -*- coding: utf-8 -*-
"""新平台回归门禁 · 蜀山四川麻将（红中血流）手牌"整帧集合精确"断言。

协议与 eval_base 一致：每张截图新建 Engine、单帧评测（多帧确认类逻辑会破坏
本协议，改动前先看 eval_base 的架构约束）。GT 为人工逐图核对的手牌多重集。

⚠ 这份 GT 就硬编码在本文件里、没有外部来源记录，所以**它自己也会错**：`s4~s7`
原记 `7s7s7s`，2026-10 看图改判为 `5s5s5s`（依据见 GT 上方注释）。改后仍红的
`s4` 两枚 `3m→2m` 是真失误 —— 别再把 GT 改回去压绿，那等于把缺陷改回盲区。
新平台照抄本文件时，GT 要么落 json 记来源，要么配一张肉眼对照图（见
`docs/new_platform_onboarding.md` 的"已知边界"）。

后续新增平台帧：把截图放进 localtest/shots_shushan/（或新建平台目录）、
在 GT 里补一行，先跑 harvest/build_*_bank 收模板再进门禁。

用法: py -3.10 localtest/eval_shushan.py
退出码：任一 verified 帧手牌不匹配 → 1；否则 0。
"""
import contextlib
import io
import json
import os
import sys
from collections import Counter

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

import engine.engine as ee  # noqa: E402
ee.load_mode = lambda: "sc_hz"  # 强制川麻血流红中模式（本地无 Java 模式文件）

# 平台也必须显式声明：本地没有 Java 推进来的 mahjong_platform.json，Engine 会退回
# platforms.DEFAULT_PLATFORM="tencent"，于是这份「蜀山门禁」一直在拿**腾讯的手牌
# ROI + 腾讯的牌风白名单**读蜀山帧——名字写着蜀山，跑的是腾讯口径。
# 实测两种口径都是 82/84、红的都是同一格 s4（GT 注释里已登记的那两枚 3m→2m 真
# 失误），所以这条声明不是救火，只是把这道门禁接到它自己声称的平台上：下次腾讯
# 侧再收窄牌风白名单（v1.7.2 就干过一次，见 recognition/tencent_grid_detector.py
# 的 STYLE_PLATFORM_DENYLIST），这份夹具不会再靠「侥幸挂在腾讯名单里」悄悄活下来。
# 这里只改内存态，绝不写 mahjong_platform.json：那份文件一旦留下「shushan」，
# eval_base（腾讯口径）会被静默污染成同一个平台，两条门禁就都在测空气了。
# 口径与上面的 load_mode 一致：process() 每帧走 `self.platform = load_platform()`
# （engine.py），所以要改的是 engine 命名空间里绑定的那个符号；改 platforms 里的
# 模块变量无效（实测：改了 _EXPLICIT_PLATFORM/_PLATFORM_CACHE 后逐帧读数一字不变）。
ee.load_platform = lambda: "shushan"  # noqa: E402
from engine.engine import Engine  # noqa: E402

SHOT_DIR = os.path.join(REPO, "localtest", "shots_shushan")

# 人工逐图核对 GT（mpsz：1-9m=1-9万, 1-9p=筒, 1-9s=条, 7z=红中）
#
# s4~s7 末三枚原记 `7s7s7s`，2026-10 看图改判为 `5s5s5s`：检测器把这三枚打成
# 5s 且分数 1.00/0.99/0.99，满分错认只剩"GT 读错"与"bank 贴错标签"两种可能，
# 逐条排掉后者（shushan bank 内 5s 与 7s 模板峰值 NCC 仅 0.77，非同形；把四帧
# 这 12 枚与 bank 5s/7s 全部变体拼一张图肉眼比对，牌面是四绿+中心红的五条）。
# 见 localtest/_diag_shushan_gate.py / _diag_bank_confusion.py / _diag_shushan_visual.py。
GT = {
    "s1.jpg": "7z8m8m8m9m7p7p7p9p9p",
    "s2.jpg": "7z8m8m8m9m7p7p9p9p7s7p",
    "s3.jpg": "7z8m8m8m9m7p7p9p9p7s7p",
    "s4.jpg": "7z2m2m3m3m4m4m4m5m5m5s5s5s",
    "s5.jpg": "7z2m2m3m3m4m4m4m5m5m5s5s5s",
    "s6.jpg": "7z2m2m3m3m4m4m4m5m5m5s5s5s",
    "s7.jpg": "7z2m2m3m3m4m4m4m5m5m5s5s5s",
}


def codes(s):
    return sorted(s[i:i + 2] for i in range(0, len(s), 2))


def run_engine(img):
    with contextlib.redirect_stdout(io.StringIO()):
        return json.loads(Engine().process(img).result)


def main():
    n_hand = n_tile = n_ok = 0
    fails = []
    for fname, gt in sorted(GT.items()):
        img = cv2.imread(os.path.join(SHOT_DIR, fname))
        assert img is not None, f"读不到 {fname}"
        data = run_engine(img)
        det = codes(data.get("hand") or "")
        g = codes(gt)
        inter = sum((Counter(g) & Counter(det)).values())
        n_hand += 1
        n_tile += max(len(g), len(det), 1)
        n_ok += inter
        exact = inter == len(g) == len(det)
        mark = "OK " if exact else "FAIL"
        print(f"[{mark}] {fname}: gt={len(g)}张 det={len(det)}张 交={inter} "
              f"status={data.get('status')}")
        if not exact:
            print(f"      gt : {''.join(g)}")
            print(f"      det: {''.join(det)}")
            fails.append(fname)
    print(f"\n手牌整帧: {n_hand - len(fails)}/{n_hand}  逐张: {n_ok}/{n_tile}")
    print("RESULT:", "PASS" if not fails else f"FAIL {fails}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
