# -*- coding: utf-8 -*-
"""验证「放开字牌候选集」的收益：模板本来就在库里，只是被默认候选集挡住。

recognition/templates_data.py 有 34 类（含 1z-7z），但
TencentGridDetector.classify_tile 的默认 valid_tiles 是
{1-9m,1-9p,1-9s,7z} —— 川麻只认红中的历史假设。于是微乐这类带字牌玩法里，
风牌/發白**结构上不可能**被覆盖层判出，只能放行 YOLO 的标签
（实测微乐帧 07 的南/南/發 被读成 7s/7s/2p，三张全错）。

本脚本不改生产代码：直接对同一批 patch 跑两次分类，比较
  A) 默认候选集（现状）
  B) 放开到 34 类
看字牌能不能被判回、以及放开后数牌会不会被字牌抢走（回退风险）。

用法: py -3.10 localtest\probe_honors.py 7
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS, PLATFORM_OF  # noqa: E402
from recognition.yolo_detector import YOLODetector  # noqa: E402
from audit_bank_fit import locate_strip  # noqa: E402

ALL34 = {f"{n}{s}" for s in "mps" for n in range(1, 10)} | {f"{n}z" for n in range(1, 8)}


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    plat = PLATFORM_OF.get(idx, "generic")
    f = next(x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_"))
    img = cv2.imread(os.path.join(SHOTS, f))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    det = YOLODetector()
    det.set_platform_styles(plat)
    helper = det._phase_helper
    y_top, y_bot, x_left, x_right = locate_strip(img)
    raw = det.detect_strip(img[y_top:y_bot, x_left:x_right],
                           offset_x=x_left, offset_y=y_top)
    print(f"帧 {idx} 平台 {plat}：A=默认候选集  B=放开 34 类")
    n_honor = 0
    for col, (rect, ylbl, _c) in enumerate(sorted(raw, key=lambda d: d[0][0])):
        rx, ry, rw, rh = rect
        patch = img[ry:ry + rh, rx:rx + rw]
        if patch.size == 0:
            continue
        la, sa = helper.classify_tile(patch)
        lb, sb = helper.classify_tile(patch, avail=ALL34)
        mark = ""
        if la != lb:
            mark = "  <== 字牌候选改变结果"
            n_honor += 1
        print(f"  [{col:02d}] A={la}({sa:.2f})  B={lb}({sb:.2f})  yolo={ylbl}{mark}")
    print(f"\n放开后改判的张数: {n_honor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
