# -*- coding: utf-8 -*-
"""多平台真机帧取证：逐帧跑当前引擎，把「它以为牌局在干什么」全部打出来。

素材：用户上传的 20 张真机截图（localtest/shots_multi/，按 平台_阶段_序号 命名）。
命名里的「阶段」是**人眼读屏**得到的事实（屏幕上有「选择3张同花色手牌」「请选择定缺
的花色」「选牌中」这类原文），不是引擎的推断 —— 本脚本的目的就是把引擎的自述和它对齐。

每帧输出：
  声明(platform/mode) | 分辨率 | status | phase_label | 三个阶段 raw+final | 张数 | hand
  | dingque | 引擎自己写的遮挡/降级提示
并把底部手牌行裁片另存到 shots_multi/crops/，供人工核对「到底有几张、各是什么」。

用法: py -3.10 localtest/diag_multiplatform.py [--crop] [--only 前缀]
"""
import argparse
import contextlib
import io
import json
import os
import sys

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from engine.engine import Engine  # noqa: E402
import platforms as PL  # noqa: E402

SHOT_DIR = os.path.join(REPO, "localtest", "shots_multi")
CROP_DIR = os.path.join(SHOT_DIR, "crops")

# (文件, 平台 key, 玩法 key)  —— 平台/玩法都按截图里面板与桌面上的文字人工确认：
#   面板标题「血流红中」= sc_hz；「广东红中王」= gd_hz；桌面水印给出平台。
FRAMES = [
    ("jj_play_01.jpg",       "jj",          "sc_hz"),
    ("jj_play_02.jpg",       "jj",          "sc_hz"),
    ("jj_play_03.jpg",       "jj",          "sc_hz"),
    ("jj_dingque_01.jpg",    "jj",          "sc_hz"),
    ("jj_swap_01.jpg",       "jj",          "sc_hz"),
    ("zj_play_01.jpg",       "zj_sichuan",  "sc_hz"),
    ("zj_play_02.jpg",       "zj_sichuan",  "sc_hz"),
    ("shushan_play_01.jpg",  "shushan",     "sc_hz"),
    ("shushan_play_02.jpg",  "shushan",     "sc_hz"),
    ("shushan_play_03.jpg",  "shushan",     "sc_hz"),
    ("shushan_dingque_01.jpg", "shushan",   "sc_hz"),
    ("queshen_play_01.jpg",  "gd_queshen",  "gd_hz"),
    ("queshen_play_02.jpg",  "gd_queshen",  "sc_hz"),
    ("tuyou_swap_01.jpg",    "tuyou",       "sc_hz"),
    ("tuyou_dingque_01.jpg", "tuyou",       "sc_hz"),
    ("tuyou_pick_01.jpg",    "tuyou",       "sc_hz"),
    ("tencent_swap_01.jpg",  "tencent",     "sc_hz"),
    ("tencent_pick_01.jpg",  "tencent",     "sc_hz"),
    ("tencent_pick_02.jpg",  "tencent",     "sc_hz"),
    ("tencent_pick_03.jpg",  "tencent",     "sc_hz"),
]

# 人眼读屏得到的阶段事实（屏幕原文），用于对齐引擎自述。play = 已在摸打。
TRUTH_PHASE = {
    "jj_swap_01.jpg": "swap",          # 「选择3张同花色手牌」+ 换牌按钮
    "jj_dingque_01.jpg": "dingque",    # 「请选择定缺的花色（无此花色才能胡牌）」
    "shushan_dingque_01.jpg": "dingque",
    "tuyou_swap_01.jpg": "swap",
    "tuyou_pick_01.jpg": "swap",       # 「选择以下任意3张手牌 确定(2)」
    "tuyou_dingque_01.jpg": "dingque",  # 「请选择一种不要的花色」
    "tencent_swap_01.jpg": "swap",
    "tencent_pick_01.jpg": "swap",
    "tencent_pick_02.jpg": "swap",
    "tencent_pick_03.jpg": "swap",
}


def run_one(img, platform, mode):
    eng = Engine()
    eng.set_platform(platform)
    eng.set_mode(mode)
    with contextlib.redirect_stdout(io.StringIO()):
        res = eng.process(img)
    return json.loads(res.result), eng


def crop_hand(img, platform, name):
    """按平台 hand_roi 裁底部手牌行另存，供人工核对真值。"""
    h, w = img.shape[:2]
    top, bottom, left, right = PL.get_hand_roi(platform)
    y0, y1 = int(h * top), int(h * min(bottom, 1.0))
    x0, x1 = int(w * left), int(w * right)
    os.makedirs(CROP_DIR, exist_ok=True)
    out = os.path.join(CROP_DIR, name.replace(".jpg", "_hand.jpg"))
    cv2.imwrite(out, img[y0:y1, x0:x1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", action="store_true", help="同时导出底部手牌行裁片")
    ap.add_argument("--only", default="", help="只跑文件名以该前缀开头的帧")
    ap.add_argument("--force-platform", default="",
                    help="无视表里的平台，强制按该平台声明跑（验证「声明错平台」假设）")
    args = ap.parse_args()

    print(f"{'帧':26s} {'平台/玩法':22s} {'分辨率':11s} {'status':9s} "
          f"{'张':3s} {'期望阶段':9s} 引擎阶段(raw→final)")
    print("-" * 150)
    rows = []
    for name, platform, mode in FRAMES:
        if args.only and not name.startswith(args.only):
            continue
        if args.force_platform:
            platform = args.force_platform
        path = os.path.join(SHOT_DIR, name)
        img = cv2.imread(path)
        if img is None:
            print(f"{name:26s} !! 读不到 {path}")
            continue
        h, w = img.shape[:2]
        d, eng = run_one(img, platform, mode)
        if args.crop:
            crop_hand(img, platform, name)
        want = TRUTH_PHASE.get(name, "play")
        flags = (f"swap={bool(d.get('swap_phase'))} "
                 f"dq={bool(d.get('dingque_phase'))} "
                 f"pick={bool(d.get('pick_phase'))}")
        print(f"{name:26s} {platform+'/'+mode:22s} {str(w)+'x'+str(h):11s} "
              f"{str(d.get('status')):9s} {str(d.get('count')):3s} "
              f"{want:9s} {flags}")
        print(f"{'':26s} phase={d.get('phase_label')} badge={d.get('tactical_badge')} "
              f"dingque={d.get('dingque_suit')}/{d.get('dingque_name')} "
              f"msg={d.get('message')!r}")
        print(f"{'':26s} hand={d.get('hand')!r}")
        rows.append({"file": name, "platform": platform, "mode": mode,
                     "want_phase": want, "payload": d})
    out = os.path.join(REPO, "build", "multi_diag.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n留档：{out}（{len(rows)} 帧）")


if __name__ == "__main__":
    main()
