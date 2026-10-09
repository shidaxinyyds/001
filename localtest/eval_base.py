"""P0 评测底座 · 打分与回归门禁。

读取 localtest/gt/shots.json，对每条 verified=true 的截图跑引擎并与人工标注比对：
  - 手牌（多重集）：精确匹配率 + 逐张牌准确率
  - 阶段 status、定缺 dingque：精确匹配
  - best（最优决策）：信息性比对（逻辑变更时允许更新 GT，不计入失败）
verified=false 的条目跳过（尚未人工校验，避免"自己测自己"的假绿灯）。

**评测口径必须自己钉死**（见 `pin_config`）：引擎每帧从全局配置读平台/玩法，
本地没有 Java 推进来，就会读到上一支探针留在仓库外的那份文件——名字写着
腾讯、跑的是别家口径。退出码只反映「在这一份声称的口径下对不对」。

退出码：任一 verified 条目在手牌/阶段/定缺上不匹配 → 1（CI 回归门失败）；否则 0。

用法:
  py -3.10 localtest/eval_base.py            # 全量打分（腾讯 sc_hz 口径）
  py -3.10 localtest/eval_base.py --strict   # best 不匹配也算失败
  py -3.10 localtest/eval_base.py --platform tencent --mode sc_hz   # 显式换口径
"""
import argparse
import contextlib
import io
import json
import os
import sys

import cv2

# Windows 控制台默认 gbk，报告里的 ⚠/✗ 会把打印本身炸掉（炸在半路 = 只看到半份
# 结论，比没结论更糟）——先把输出切成 utf-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from engine.engine import Engine  # noqa: E402

SHOT_DIR = os.path.join(REPO, "localtest", "shots")
GT_PATH = os.path.join(REPO, "localtest", "gt", "shots.json")

# 本门禁声称的默认口径：腾讯欢乐 + 川麻血流红中（与 GT 里 ok/dingque/swap/pick
# 那批阶段标注、以及 platforms.DEFAULT_PLATFORM / modes.DEFAULT_MODE 一致）。
DEFAULT_PLATFORM = "tencent"
DEFAULT_MODE = "sc_hz"


def pin_config(platform: str, mode: str) -> None:
    """把引擎读到的平台/玩法钉死在这一对值上（只改内存符号，绝不写磁盘）。

    为什么不能像以前那样“什么都不写就是默认腾讯”：`platforms.load_platform()` 的
    候选路径第一条是 `/storage/emulated/0/...`，Windows 把它当成**当前盘根**，于是
    任何调过 `Engine.set_platform()`（它走 `set_platform_explicit`→`save_platform`）
    的本地探针都会在仓库外留下一份全局配置。实测（2026-10）那份写着
    `gd_queshen`：本该是腾讯口径的 37 帧基线其实在用广东雀神的手牌 ROI、牌风
    白名单与玩法跑，阶段从 dingque/pick 变成 ok（工作树 26 条不匹配 vs 基线 2 条）。
    约定与 `eval_shushan.py`/`layer_cost.py` 一致：`process()` 每帧走的是 engine
    命名空间里绑定的 `load_platform`/`load_mode`，所以改这两个符号就够，
    不改磁盘、也不影响真机行为。
    """
    import engine.engine as ee
    ee.load_platform = lambda *a, **kw: platform
    ee.load_mode = lambda *a, **kw: mode


def ghost_configs() -> list:
    """磁盘上现存的全局配置绝对路径（评测已不用它们，但必须让污染留在报告里）。"""
    from modes import _get_candidate_paths
    from platforms import _candidate_platform_paths
    out = []
    for p in list(_candidate_platform_paths()) + _get_candidate_paths("mahjong_mode.json"):
        try:
            if os.path.exists(p):
                out.append(os.path.abspath(p))
        except OSError:
            continue
    return out


def run_engine(img):
    """跑引擎并吞掉其调试 stdout（引擎每帧 print 大字典会污染报表）。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return json.loads(Engine().process(img).result)


def canon_mpsz(s):
    """把 '7z1m3m...' 规范化为排序后的代码列表（多重集）。"""
    if not s:
        return []
    codes = [s[i:i + 2] for i in range(0, len(s), 2)]
    return sorted(codes)


def tile_accuracy(gt_codes, det_codes):
    """逐张准确率：以多重集交集计。"""
    from collections import Counter
    g, d = Counter(gt_codes), Counter(det_codes)
    inter = sum((g & d).values())
    total = max(len(gt_codes), len(det_codes), 1)
    return inter, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="best 不匹配也判失败")
    ap.add_argument("--platform", default=DEFAULT_PLATFORM,
                    help="评测口径的平台（默认 tencent，不跟随磁盘全局配置）")
    ap.add_argument("--mode", default=DEFAULT_MODE,
                    help="评测口径的玩法（默认 sc_hz）")
    args = ap.parse_args()

    # 先钉口径再跑图：否则本门禁报的是“上一支探针留下的平台”的分数。
    pin_config(args.platform, args.mode)
    print(f"评测口径（内存钉死，不读磁盘）：platform={args.platform} mode={args.mode}")
    ghosts = ghost_configs()
    if ghosts:
        print("⚠ 磁盘上仍有全局配置（已被上面的钉死忽略，但它们是别的门禁的隐患）：")
        for g in ghosts:
            print(f"    {g}")

    if not os.path.exists(GT_PATH):
        print("GT 不存在，请先运行 gen_gt.py 并人工校验")
        sys.exit(2)
    with open(GT_PATH, encoding="utf-8") as f:
        gt = json.load(f)["shots"]

    n_verified = n_skipped = 0
    hand_exact = 0
    tile_hit = tile_tot = 0
    status_ok = dingque_ok = status_n = dingque_n = 0
    best_warn = 0
    failures = []

    for e in gt:
        if not e.get("verified"):
            n_skipped += 1
            continue
        n_verified += 1
        path = os.path.join(SHOT_DIR, e["file"])
        img = cv2.imread(path)
        if img is None:
            failures.append((e["file"], "无法读取图片"))
            continue
        # 每张截图独立对局帧：全新 Engine，防止上一帧投票窗口泄漏污染评分
        d = run_engine(img)

        gt_hand = canon_mpsz(e.get("hand", ""))
        det_hand = canon_mpsz(d.get("hand", ""))
        exact = (gt_hand == det_hand)
        # 逐张准确率仅在有可读牌面时计入（遮挡帧 GT 为空，正确输出"空"只算精确匹配）
        if gt_hand:
            inter, total = tile_accuracy(gt_hand, det_hand)
            tile_hit += inter
            tile_tot += total
        else:
            inter, total = (1, 1) if exact else (0, 1)
        if exact:
            hand_exact += 1

        s_match = (d.get("status", "") == e.get("status", ""))
        status_ok += int(s_match)
        status_n += 1

        dq_match = True
        if e.get("dingque") is not None:
            dingque_n += 1
            dq_match = (d.get("dingque") == e.get("dingque"))
            dingque_ok += int(dq_match)

        best_match = True
        if e.get("best"):
            best_match = (d.get("best", "") == e.get("best", ""))
            if not best_match:
                best_warn += 1

        if not exact or not s_match or not dq_match or (args.strict and not best_match):
            st = "OK" if s_match else "X(={})".format(d.get("status"))
            failures.append((
                e["file"],
                "hand{}({}/{}) status{} dq{} best{}".format(
                    "OK" if exact else "X", inter, total, st,
                    "OK" if dq_match else "X", "OK" if best_match else "X",
                ),
            ))

    print("=" * 60)
    print(f"已校验条目: {n_verified} | 跳过(未校验): {n_skipped}")
    if n_verified:
        print(f"手牌精确匹配 : {hand_exact}/{n_verified} = {100*hand_exact/n_verified:.1f}%")
        print(f"逐张牌准确率 : {tile_hit}/{tile_tot} = {100*tile_hit/max(tile_tot,1):.2f}%")
        print(f"阶段 status  : {status_ok}/{status_n} = {100*status_ok/max(status_n,1):.1f}%")
        print(f"定缺 dingque : {dingque_ok}/{dingque_n} = {100*dingque_ok/max(dingque_n,1):.1f}%")
        print(f"best 差异    : {best_warn} (信息性{'，strict计入失败' if args.strict else '，不计失败'})")
    if failures:
        print("\n--- 不匹配明细 ---")
        for fn, msg in failures:
            print(f"  {fn[:16]:16s} {msg}")
        print(f"\nRESULT: FAIL ({len(failures)} 条不匹配)")
        sys.exit(1)
    print("\nRESULT: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
