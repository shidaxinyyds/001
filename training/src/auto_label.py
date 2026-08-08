#!/usr/bin/env python3
"""
Auto-label gameplay frames with a large teacher detector.

Teacher: any Ultralytics-compatible weights with a "person" class
(default: yolov8x.pt, COCO id 0). The teacher runs once per frame at a
much higher input resolution than the runtime model so small / distant
targets get labeled. Detections are filtered, NMS'd, and written as
YOLO-format labels with our single-class id (0 = enemy).

Why use a teacher instead of labeling by hand:
- yolov8x at imgsz=1280 recovers distant / partially-occluded humans
  the runtime model would miss outright.
- 95% of frames are labeled without human review; you only need to
  spot-check and prune false positives (vehicles, posters, billboards).
- After the first training pass, run this again with `--teacher` set
  to your trained best.pt at imgsz=640 to harvest more in-game data.
  The student bootstraps the next-generation teacher (self-distillation).

Usage:
    python src/auto_label.py --in raw_frames --out dataset/train \
        --teacher yolov8x.pt --imgsz 1280 --conf 0.30

The output mirrors YOLO dataset layout:
    out/images/<name>.jpg
    out/labels/<name>.txt
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Iterable

try:
    from ultralytics import YOLO
except ImportError:
    print("ERROR: ultralytics not installed. Run scripts/01_setup_environment.bat first.")
    raise SystemExit(2)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# COCO id for "person". Most off-the-shelf YOLO weights ship with this
# class at index 0; we remap to our single-class enemy id (also 0).
COCO_PERSON_ID = 0


def _iter_images(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            yield path


def _normalize_box(xyxy, img_w: int, img_h: int) -> tuple[float, float, float, float] | None:
    """xyxy in pixel coords -> YOLO (cx, cy, w, h) normalized."""
    x1, y1, x2, y2 = xyxy
    x1 = max(0.0, float(x1))
    y1 = max(0.0, float(y1))
    x2 = min(float(img_w), float(x2))
    y2 = min(float(img_h), float(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    cx = ((x1 + x2) / 2.0) / img_w
    cy = ((y1 + y2) / 2.0) / img_h
    w = (x2 - x1) / img_w
    h = (y2 - y1) / img_h
    if w <= 0 or h <= 0:
        return None
    return cx, cy, w, h


def auto_label(
    frames_dir: Path,
    out_dir: Path,
    teacher_weights: Path,
    imgsz: int,
    conf_threshold: float,
    iou_threshold: float,
    keep_class_ids: list[int],
    device: str,
    write_negatives: bool,
    min_box_area_frac: float,
    max_box_area_frac: float,
) -> int:
    if not frames_dir.exists():
        print(f"ERROR: input frames directory missing: {frames_dir}")
        return 2
    if not teacher_weights.exists():
        # Let Ultralytics auto-download if it's a known model name.
        if teacher_weights.name in {
            "yolov8x.pt", "yolov8l.pt", "yolov8m.pt",
            "yolo11x.pt", "yolo11l.pt", "yolo11m.pt",
            "yolov10x.pt", "yolov10l.pt",
        }:
            print(f"Teacher not found locally; ultralytics will download {teacher_weights.name}")
        else:
            print(f"ERROR: teacher weights not found: {teacher_weights}")
            return 2

    images_out = out_dir / "images"
    labels_out = out_dir / "labels"
    images_out.mkdir(parents=True, exist_ok=True)
    labels_out.mkdir(parents=True, exist_ok=True)

    teacher = YOLO(str(teacher_weights))

    total_frames = 0
    total_labels = 0
    skipped_no_detection = 0

    for img_path in _iter_images(frames_dir):
        total_frames += 1

        results = teacher.predict(
            source=str(img_path),
            imgsz=imgsz,
            conf=conf_threshold,
            iou=iou_threshold,
            device=device,
            verbose=False,
        )
        if not results:
            continue

        result = results[0]
        boxes = result.boxes
        img_h, img_w = result.orig_shape[:2]

        keep_lines: list[str] = []
        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            cls = boxes.cls.cpu().numpy().astype(int)
            confs = boxes.conf.cpu().numpy()

            for i in range(len(xyxy)):
                if int(cls[i]) not in keep_class_ids:
                    continue
                norm = _normalize_box(xyxy[i], img_w, img_h)
                if norm is None:
                    continue
                cx, cy, w, h = norm
                area = w * h
                if area < min_box_area_frac:
                    # tiny detections at this scale are almost always noise on
                    # in-game UI (kill feed avatars, minimap icons, etc.)
                    continue
                if area > max_box_area_frac:
                    # huge boxes are almost always background players or HUD elements
                    continue
                # remap to our single-class enemy id (0)
                keep_lines.append(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

        if not keep_lines and not write_negatives:
            skipped_no_detection += 1
            continue

        out_image_path = images_out / img_path.name
        if not out_image_path.exists():
            shutil.copy2(img_path, out_image_path)

        label_path = labels_out / (img_path.stem + ".txt")
        label_path.write_text("\n".join(keep_lines), encoding="utf-8")
        total_labels += len(keep_lines)

    print("=" * 60)
    print(f"Auto-label summary")
    print(f"  Frames scanned          : {total_frames}")
    print(f"  Frames with detections  : {total_frames - skipped_no_detection}")
    print(f"  Frames skipped (no det) : {skipped_no_detection}")
    print(f"  Total enemy labels      : {total_labels}")
    print(f"  Output images           : {images_out}")
    print(f"  Output labels           : {labels_out}")
    print()
    print("Next steps:")
    print("  1. Spot-check labels in Roboflow / labelImg  -  the teacher will")
    print("     occasionally box vehicles, posters, NPCs. Delete those.")
    print("  2. Run scripts/03_validate_dataset.bat to verify format.")
    print("  3. Train.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-label gameplay frames with a teacher detector")
    parser.add_argument("--in", dest="frames_dir", type=Path, required=True, help="Input directory of frames")
    parser.add_argument("--out", dest="out_dir", type=Path, required=True, help="Output dataset split directory (e.g. dataset/train)")
    parser.add_argument("--teacher", type=Path, default=Path("yolov8x.pt"), help="Teacher model weights (default: yolov8x.pt  -  auto-downloads)")
    parser.add_argument("--imgsz", type=int, default=1280, help="Inference image size for the teacher (default: 1280 for small targets)")
    parser.add_argument("--conf", type=float, default=0.30, help="Confidence threshold (default: 0.30; lower = more recall)")
    parser.add_argument("--iou", type=float, default=0.55, help="NMS IoU threshold (default: 0.55)")
    parser.add_argument("--classes", type=int, nargs="+", default=[COCO_PERSON_ID], help="Class ids in the teacher to keep (default: [0] = person)")
    parser.add_argument("--device", type=str, default="auto", help="cuda device id, 'cpu', or 'auto'")
    parser.add_argument("--write-negatives", action="store_true", help="Also copy frames that produced zero detections (with empty labels)")
    parser.add_argument("--min-box-area-frac", type=float, default=0.0005, help="Drop boxes smaller than this fraction of image area")
    parser.add_argument("--max-box-area-frac", type=float, default=0.85, help="Drop boxes larger than this fraction of image area")
    args = parser.parse_args()

    device = args.device
    if device == "auto":
        try:
            import torch  # type: ignore
            device = 0 if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    return auto_label(
        frames_dir=args.frames_dir,
        out_dir=args.out_dir,
        teacher_weights=args.teacher,
        imgsz=args.imgsz,
        conf_threshold=args.conf,
        iou_threshold=args.iou,
        keep_class_ids=args.classes,
        device=device,
        write_negatives=args.write_negatives,
        min_box_area_frac=args.min_box_area_frac,
        max_box_area_frac=args.max_box_area_frac,
    )


if __name__ == "__main__":
    raise SystemExit(main())
