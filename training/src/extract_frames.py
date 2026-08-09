#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

from training_config import load_config
from pipeline_common import ensure_workspace_structure

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


def _list_videos(videos_dir: Path) -> list[Path]:
    if not videos_dir.exists():
        return []
    return [p for p in videos_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS]


def _preprocess_frame(frame, target_imgsz: int, capture_height: int = 720):
    """Mirror the runtime preprocessing pipeline so training distribution
    matches deployment distribution.

    Runtime path (esp_jni.cpp + yolo_detector.cpp::preprocess, fullFrame=true):
      1. ImageReader hands us a `capture_height`p RGBA buffer
         (CAPTURE_WIDTH x CAPTURE_HEIGHT = 1280 x 720).
      2. Letterbox-resize the FULL frame to MODEL_INPUT_SIZE x MODEL_INPUT_SIZE
         (scale = min(modelW/srcW, modelH/srcH), resize, pad to square with 114).
      3. Convert to RGB, scale to [0, 1].

    The training-time frames produced here use the SAME full-frame letterbox:
    we scale the source to 720p (matching the runtime capture resolution),
    then letterbox-resize to `target_imgsz` (default 640) so the trainer sees
    more pixel detail than the runtime model will, while the FOV the network
    learns is identical — 100% of the game screen, because games run fullscreen.

    Color stays in BGR on disk (cv2 default, Ultralytics swaps to RGB
    internally when loading), matching the auto-label pipeline.
    """
    h, w = frame.shape[:2]
    # Step 1: scale source to runtime capture height so the frame dimensions
    # match the runtime path (1280x720).
    scale = float(capture_height) / max(1, h)
    scaled_w = int(w * scale)
    scaled_h = capture_height
    resized = cv2.resize(frame, (scaled_w, scaled_h))

    # Step 2: letterbox-resize the full frame to a target_imgsz x target_imgsz
    # square. This preserves aspect ratio (no stretch distortion) and pads the
    # remaining area with 114 (the standard YOLO padding value).
    # This matches the runtime letterbox in yolo_detector.cpp::preprocess()
    # with fullFrame=true: the entire 1280x720 frame is scaled into the model
    # input square, giving 100% screen coverage.
    lb_scale = min(float(target_imgsz) / scaled_w, float(target_imgsz) / scaled_h)
    new_w = max(1, int(scaled_w * lb_scale))
    new_h = max(1, int(scaled_h * lb_scale))
    resized_frame = cv2.resize(resized, (new_w, new_h),
                               interpolation=cv2.INTER_AREA if target_imgsz < min(scaled_w, scaled_h) else cv2.INTER_LINEAR)

    # Pad to square with value 114 (YOLO standard padding).
    canvas = np.full((target_imgsz, target_imgsz, 3), 114, dtype=np.uint8)
    pad_x = (target_imgsz - new_w) // 2
    pad_y = (target_imgsz - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized_frame

    return canvas


def _process_video(video_path: Path, output_root: Path, target_imgsz: int, fps_extract: float, capture_height: int) -> tuple[int, bool]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"Skipping unreadable video: {video_path.name}")
        return 0, True

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    interval = max(1, int(video_fps / max(0.1, fps_extract)))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    out_dir = output_root / video_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_idx = 0
    saved = 0

    with tqdm(total=total if total > 0 else None, desc=video_path.name, unit="frame") as bar:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if frame_idx % interval == 0:
                final = _preprocess_frame(frame, target_imgsz, capture_height)
                if final is not None:
                    out_file = out_dir / f"{video_path.stem}_frame_{saved:05d}.jpg"
                    cv2.imwrite(str(out_file), final)
                    saved += 1

            frame_idx += 1
            bar.update(1)

    cap.release()
    print(f"Extracted {saved} frames -> {out_dir}")
    return saved, False


def extract(
    config_path: Path | None = None,
    fps_extract: float = 1.0,
    capture_height: int = 720,
    target_imgsz: int | None = None,
) -> int:
    cfg = load_config(config_path)
    ensure_workspace_structure(cfg)
    videos_dir = cfg.paths.videos_dir
    output_root = cfg.paths.raw_frames_dir
    output_root.mkdir(parents=True, exist_ok=True)

    videos = _list_videos(videos_dir)
    if not videos:
        print(f"No videos found in {videos_dir}")
        return 1

    imgsz = target_imgsz if target_imgsz is not None else cfg.training.imgsz
    print(f"Extracting frames at imgsz={imgsz}, full-frame letterbox, capture_height={capture_height} (runtime CAPTURE_HEIGHT=720)")

    had_error = False
    total_saved = 0

    for video_path in videos:
        saved, error = _process_video(video_path, output_root, imgsz, fps_extract, capture_height)
        total_saved += saved
        had_error = had_error or error

    print(f"Total extracted frames: {total_saved}")

    return 2 if had_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract training frames using runtime-aligned full-frame letterbox preprocessing")
    parser.add_argument("--config", type=Path, default=None, help="Path to config/config.ini")
    parser.add_argument("--fps", type=float, default=1.0, help="Frames to extract per second")
    parser.add_argument("--capture-height", type=int, default=720, help="Capture height to scale source to (default 720 = matches runtime CAPTURE_HEIGHT)")
    parser.add_argument("--imgsz", type=int, default=None, help="Override training image size (default = [training] imgsz from config)")
    args = parser.parse_args()
    return extract(args.config, args.fps, args.capture_height, args.imgsz)


if __name__ == "__main__":
    raise SystemExit(main())
