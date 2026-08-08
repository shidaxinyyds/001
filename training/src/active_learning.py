#!/usr/bin/env python3
"""
Active learning: after a first training pass, run the student model
on unlabeled frames and surface the ones MOST worth labeling next.

"Worth labeling" = frames the model is uncertain about, where a
human label adds the most information. Three signals:

  1. Low-confidence detections (0.25 < conf < 0.55)  -  the model "kind
     of" sees something but isn't sure.
  2. Teacher-student disagreement (optional)  -  teacher fires N boxes,
     student fires M boxes, |N-M| > threshold.
  3. Zero detections on frames the teacher labels (false negatives)  - 
     the student is missing what the teacher catches.

Outputs:
    review_dir/
        uncertain/     -  frames flagged for human review
        report.json    -  per-frame scoring breakdown

Workflow:
    1. Train v1 with a small auto-labelled set.
    2. Run this on a large unlabelled raw_frames pool.
    3. Hand-correct labels for the surfaced frames only (~10% of pool).
    4. Add to dataset, retrain.

Usage:
    python src/active_learning.py \\
        --student outputs/runs/detect/train/weights/best.pt \\
        --teacher yolov8x.pt \\
        --frames raw_frames \\
        --out outputs/review
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

try:
    from ultralytics import YOLO
except ImportError:
    print("ERROR: ultralytics not installed. Run scripts/01_setup_environment.bat first.")
    raise SystemExit(2)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _count_detections(result, lo: float, hi: float) -> tuple[int, int, int]:
    """Return (total, in_uncertain_band, high_confidence)."""
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return 0, 0, 0
    confs = boxes.conf.cpu().numpy()
    in_band = int(((confs >= lo) & (confs < hi)).sum())
    high = int((confs >= hi).sum())
    total = int(len(confs))
    return total, in_band, high


def active_learning(
    student_weights: Path,
    teacher_weights: Path | None,
    frames_dir: Path,
    out_dir: Path,
    student_imgsz: int,
    teacher_imgsz: int,
    student_conf: float,
    teacher_conf: float,
    uncertain_lo: float,
    uncertain_hi: float,
    disagreement_threshold: int,
    max_review: int,
    device: str,
) -> int:
    if not student_weights.exists():
        print(f"ERROR: student weights not found: {student_weights}")
        return 2
    if not frames_dir.exists():
        print(f"ERROR: frames dir not found: {frames_dir}")
        return 2

    uncertain_dir = out_dir / "uncertain"
    uncertain_dir.mkdir(parents=True, exist_ok=True)

    student = YOLO(str(student_weights))
    teacher = YOLO(str(teacher_weights)) if teacher_weights is not None else None

    scored: list[dict] = []

    for img_path in sorted(frames_dir.rglob("*")):
        if not (img_path.is_file() and img_path.suffix.lower() in IMAGE_EXTS):
            continue

        s_result = student.predict(
            source=str(img_path),
            imgsz=student_imgsz,
            conf=max(0.05, student_conf - 0.20),  # lower than runtime to surface borderline cases
            verbose=False,
            device=device,
        )[0]
        s_total, s_band, s_high = _count_detections(s_result, uncertain_lo, uncertain_hi)

        t_total = 0
        if teacher is not None:
            t_result = teacher.predict(
                source=str(img_path),
                imgsz=teacher_imgsz,
                conf=teacher_conf,
                verbose=False,
                device=device,
                classes=[0],
            )[0]
            t_total, _, _ = _count_detections(t_result, uncertain_lo, uncertain_hi)

        disagreement = abs(t_total - s_total) if teacher is not None else 0
        # Score: higher = more worth labelling. Uncertain-band detections dominate;
        # teacher-student disagreement is a strong secondary signal.
        score = s_band * 3 + disagreement * 2 + (1 if (s_total == 0 and t_total > 0) else 0) * 5

        if score > 0:
            scored.append({
                "image": str(img_path),
                "score": score,
                "student_total": s_total,
                "student_uncertain": s_band,
                "student_high_conf": s_high,
                "teacher_total": t_total,
                "disagreement": disagreement,
            })

    scored.sort(key=lambda r: r["score"], reverse=True)
    top = scored[:max_review]

    for entry in top:
        src = Path(entry["image"])
        dst = uncertain_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps({
        "student_weights": str(student_weights),
        "teacher_weights": str(teacher_weights) if teacher_weights else None,
        "frames_scanned": len(scored),
        "frames_surfaced": len(top),
        "top": top,
    }, indent=2), encoding="utf-8")

    print(f"Surfaced {len(top)} frames for review -> {uncertain_dir}")
    print(f"Full report -> {report_path}")
    print()
    print("Next steps:")
    print("  1. Open uncertain/ in your labelling tool (labelImg, Roboflow, CVAT).")
    print("  2. Correct/add boxes for these frames only.")
    print("  3. Move them into dataset/train/ and retrain.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Surface unlabeled frames most worth labelling next")
    parser.add_argument("--student", type=Path, required=True, help="Trained student .pt weights")
    parser.add_argument("--teacher", type=Path, default=None, help="Optional teacher .pt weights (e.g. yolov8x.pt) for disagreement signal")
    parser.add_argument("--frames", type=Path, required=True, help="Pool of unlabelled frames to scan")
    parser.add_argument("--out", type=Path, required=True, help="Output review directory")
    parser.add_argument("--student-imgsz", type=int, default=256, help="Student inference imgsz (default: 256  -  matches runtime)")
    parser.add_argument("--teacher-imgsz", type=int, default=1280)
    parser.add_argument("--student-conf", type=float, default=0.50, help="Student runtime conf threshold")
    parser.add_argument("--teacher-conf", type=float, default=0.30)
    parser.add_argument("--uncertain-lo", type=float, default=0.25)
    parser.add_argument("--uncertain-hi", type=float, default=0.55)
    parser.add_argument("--disagreement-threshold", type=int, default=1)
    parser.add_argument("--max-review", type=int, default=300, help="Cap on frames surfaced (sorted by score)")
    parser.add_argument("--device", type=str, default="auto")
    args = parser.parse_args()

    device = args.device
    if device == "auto":
        try:
            import torch  # type: ignore
            device = 0 if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    return active_learning(
        student_weights=args.student,
        teacher_weights=args.teacher,
        frames_dir=args.frames,
        out_dir=args.out,
        student_imgsz=args.student_imgsz,
        teacher_imgsz=args.teacher_imgsz,
        student_conf=args.student_conf,
        teacher_conf=args.teacher_conf,
        uncertain_lo=args.uncertain_lo,
        uncertain_hi=args.uncertain_hi,
        disagreement_threshold=args.disagreement_threshold,
        max_review=args.max_review,
        device=device,
    )


if __name__ == "__main__":
    raise SystemExit(main())
