# -*- coding: utf-8 -*-
"""手牌覆盖层静默失效时，把被吞掉的异常挖出来。

现象：帧 14 的生产路径返回 0 张手牌，而把 classify_tile 换成 stub 的
no_template 分支能返回 14 张 —— 说明异常发生在「等距节距重切 + 模板分类」
这段，且被上层的 except 吞掉了。这里绕过引擎，直接按生产同样的方式
构造 patch 并调 classify_tile，让 traceback 打到 stdout。

用法: py -3.10 localtest\probe_classify.py 14
"""
import os
import sys
import traceback

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "python"))
sys.path.insert(0, HERE)

from montage_for_gt import SHOTS, PLATFORM_OF  # noqa: E402
from recognition import tencent_grid_detector as TGD  # noqa: E402


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    plat = PLATFORM_OF.get(idx, "generic")
    f = next(x for x in os.listdir(SHOTS) if x.startswith(f"calib_{idx:02d}_"))
    img = cv2.imread(os.path.join(SHOTS, f))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    ih, iw = img.shape[:2]
    print(f"帧 {idx} 平台 {plat} 尺寸 {iw}x{ih}")

    det = TGD.TencentGridDetector()
    det.set_platform_styles(plat)
    print(f"active_styles={det.active_styles} cores={len(det._cores)}")

    # 手牌带：取底部 30%，与 gd_queshen 的 hand_roi 一致
    top = int(ih * 0.70)
    band = img[top:ih, :]
    print(f"手牌带 {band.shape[1]}x{band.shape[0]}")
    n = 13
    pw = band.shape[1] // n
    for i in range(n):
        patch = band[:, i * pw:(i + 1) * pw]
        try:
            lbl, sc = det.classify_tile(patch)
            if i < 3:
                print(f"  [{i}] patch={patch.shape} -> {lbl} {sc:.3f}")
        except Exception:
            print(f"  [{i}] classify_tile 抛异常 patch={patch.shape}:")
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
