# -*- coding: utf-8 -*-
"""把 Qoder 图片缓存里的实战截图按内容哈希去重，导入 localtest/shots_calib/。

缓存目录里同一张图会被多次落盘（文件名后缀不同、哈希前缀相同），
所以以「首个下划线段」作为内容指纹去重，并按首次出现时间排序编号。

用法: py -3.10 localtest/import_calib_shots.py
"""
import hashlib
import os
import shutil
import sys

SRC = r"C:\Users\ing\AppData\Roaming\Qoder\SharedClientCache\cache\images\task-33f"
HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, "shots_calib")


def main():
    if not os.path.isdir(SRC):
        print("缓存目录不存在:", SRC)
        return 1
    files = [f for f in os.listdir(SRC) if f.lower().endswith((".jpg", ".png"))]
    files.sort(key=lambda f: (os.path.getmtime(os.path.join(SRC, f)), f))

    os.makedirs(DST, exist_ok=True)
    seen = {}
    rows = []
    for f in files:
        p = os.path.join(SRC, f)
        # 内容指纹：直接读文件做 md5，避免只信文件名前缀
        with open(p, "rb") as fh:
            digest = hashlib.md5(fh.read()).hexdigest()[:12]
        if digest in seen:
            continue
        seen[digest] = f
        i = len(rows) + 1
        ext = os.path.splitext(f)[1].lower()
        out = f"calib_{i:02d}_{digest}{ext}"
        shutil.copy2(p, os.path.join(DST, out))
        rows.append((out, f, os.path.getsize(p)))

    print(f"唯一图像 {len(rows)} 张 -> {DST}")
    for out, src, size in rows:
        print(f"  {out}  <- {src}  {size}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
