#!/usr/bin/env python3
"""
Hard-negative mining: take frames you KNOW contain no enemies
(menus, vehicles you mistakenly labelled, friendly NPCs, scoreboards)
and emit empty YOLO labels for them.

Why: a detector trained only on positives learns "every human-shaped
thing is an enemy" and produces over-confident false positives on
HUD elements, billboards, posters, teammate models, etc. Mixing in
labelled negatives teaches it what is NOT a target.

Recommended ratio: 10-25% negatives in your final training set.

Usage:
    python src/mine_negatives.py --in raw_frames/menus --out dataset/train
    python src/mine_negatives.py --in raw_frames/vehicles --out dataset/train

The output mirrors YOLO dataset layout:
    out/images/<name>.jpg      (copied from input)
    out/labels/<name>.txt      (empty file)
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def mine_negatives(frames_dir: Path, out_dir: Path) -> int:
    if not frames_dir.exists():
        print(f"ERROR: input directory missing: {frames_dir}")
        return 2

    images_out = out_dir / "images"
    labels_out = out_dir / "labels"
    images_out.mkdir(parents=True, exist_ok=True)
    labels_out.mkdir(parents=True, exist_ok=True)

    copied = 0
    for img_path in sorted(frames_dir.rglob("*")):
        if not (img_path.is_file() and img_path.suffix.lower() in IMAGE_EXTS):
            continue

        out_image = images_out / img_path.name
        if not out_image.exists():
            shutil.copy2(img_path, out_image)

        # Empty label file == frame contains zero enemies.
        label_path = labels_out / (img_path.stem + ".txt")
        label_path.write_text("", encoding="utf-8")
        copied += 1

    print(f"Wrote {copied} negative samples to {out_dir}")
    if copied == 0:
        print("Warning: no images found. Negatives have no effect on training.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Add labelled negative samples to a dataset split")
    parser.add_argument("--in", dest="frames_dir", type=Path, required=True, help="Directory of no-enemy frames")
    parser.add_argument("--out", dest="out_dir", type=Path, required=True, help="Dataset split directory (e.g. dataset/train)")
    args = parser.parse_args()
    return mine_negatives(args.frames_dir, args.out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
