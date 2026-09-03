"""TFLite-based tile classifier (replaces recognition/structural.py geometric classifier).

When the trained .tflite model is missing or fails to load, `is_available()` returns
False and the caller falls back to the handwritten geometric classifier
(`structural.py`). This lets the APK ship even before the model is trained — the
legacy path keeps the app functional while we iterate on the CNN.

Public API (mirrors structural.py's per-tile classifier contract):
    is_available() -> bool
    predict(image) -> Optional[dict]   # {'tile': '5m', 'confidence': 0.97, 'probs': [...]}
    predict_batch(images) -> list     # batch version, much faster for full hand

Class index → mpsz mapping (34 classes):
    alphabetical 顺序（与 train.py flow_from_directory class_indices 一致，**也是 labels.txt
    实际写入顺序**）：
        0=1m  1=1p  2=1s  3=1z
        4=2m  5=2p  6=2s  7=2z
        8=3m  9=3p 10=3s 11=3z
       12=4m 13=4p 14=4s 15=4z
       16=5m 17=5p 18=5s 19=5z
       20=6m 21=6p 22=6s 23=6z
       24=7m 25=7p 26=7s 27=7z
       28=8m 29=8p 30=8s
       31=9m 32=9p 33=9s
"""
import os
import threading
from typing import List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Class index ↔ mpsz mapping
#
# **CRITICAL**: LABEL_LIST 顺序必须与 train.py 的 flow_from_directory 输出一致
# (alphabetical: 1m,1p,1s,1z,2m,2p,2s,2z,...,9s)。否则模型输出 idx 会映射到
# 错误的牌面，引擎整套识别错位。labels.txt 在 train_data/model/ 与
# tflite_assets/ 副本均按此序写入；不要手动改其中之一。
# ---------------------------------------------------------------------------
LABEL_LIST: List[str] = [
    "1m", "1p", "1s", "1z",
    "2m", "2p", "2s", "2z",
    "3m", "3p", "3s", "3z",
    "4m", "4p", "4s", "4z",
    "5m", "5p", "5s", "5z",
    "6m", "6p", "6s", "6z",
    "7m", "7p", "7s", "7z",
    "8m", "8p", "8s",
    "9m", "9p", "9s",
]
NUM_CLASSES: int = len(LABEL_LIST)  # 34

# Model input shape (frozen — MUST match train.py TFLite export)
INPUT_SIZE: int = 48   # tile image resized to 48x48
INPUT_CHANNELS: int = 1  # grayscale (mahjong tiles are essentially monochrome)

# Chaquopy asset path resolution — model is packaged at src/main/python/tflite_assets/tile_classifier.tflite
_MODEL_FILENAME: str = "tile_classifier.tflite"
# Confidence threshold below which the caller is encouraged to fall back to structural.py
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.55

# Lazy-loaded interpreter state (initialized on first call to is_available() / predict())
_interpreter = None
_input_details = None
_output_details = None
_load_lock = threading.Lock()      # protects lazy init
_inference_lock = threading.Lock()  # serializes interpreter.invoke() — TFLite is not reentrant


def _find_model_path() -> Optional[str]:
    """Locate the .tflite model file.

    Search order:
      1. Chaquopy asset dir (production path — model packaged in APK)
      2. /data/data/<pkg>/files/ (Android internal files dir — sideload location)
      3. CWD (development convenience — running on dev machine)
    """
    candidates = [
        # Chaquopy: assets from src/main/python/tflite_assets/ land in a known location.
        # Chaquopy exposes CHAQUOPY_ASSET_DIR env var at runtime.
        os.path.join(os.environ.get("CHAQUOPY_ASSET_DIR", ""), _MODEL_FILENAME),
        # Android internal files dir (written by sideload scripts)
        f"/data/data/com.example.auto_vision/files/{_MODEL_FILENAME}",
        # Dev / sideload convenience
        os.path.join(os.path.dirname(__file__), "..", "tflite_assets", _MODEL_FILENAME),
        _MODEL_FILENAME,  # CWD
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _load() -> bool:
    """Idempotent loader. Returns True iff interpreter is ready."""
    global _interpreter, _input_details, _output_details
    if _interpreter is not None:
        return True
    with _load_lock:
        if _interpreter is not None:
            return True
        path = _find_model_path()
        if path is None:
            return False
        try:
            # Import lazily — if tflite-runtime isn't installed (e.g. local dev
            # without the Chaquopy image), we want a clean False, not an import crash.
            from tflite_runtime.interpreter import Interpreter  # noqa: E402

            _interpreter = Interpreter(model_path=path, num_threads=2)
            _interpreter.allocate_tensors()
            _input_details = _interpreter.get_input_details()
            _output_details = _interpreter.get_output_details()
            print(f"[tflite_classifier] loaded model from {path}")
            return True
        except Exception as e:
            # Stay quiet on first failure to avoid log churn; caller decides fallback.
            print(f"[tflite_classifier] load failed ({e!r}); structural.py will be used.")
            _interpreter = None
            return False


def is_available() -> bool:
    """Returns True if a TFLite model is loaded and ready for inference.

    Use this from engine.py to choose between this classifier (preferred, when
    available) and the legacy structural.py fallback.
    """
    return _load()


def _preprocess_one(image: np.ndarray) -> Optional[np.ndarray]:
    """Convert a single tile image (any channel layout) to a 1xHxWxC float32 tensor.

    Returns None if the image is degenerate (empty, NaN, inf) — caller should
    skip inference rather than feed garbage to the model.
    """
    if image is None or image.size == 0:
        return None
    arr = image.astype(np.float32)
    if not np.isfinite(arr).all():
        return None
    # Convert to grayscale (mean over channels) — mahjong tiles are essentially monochrome.
    if arr.ndim == 3:
        gray = arr.mean(axis=2)
    else:
        gray = arr
    # Resize to model input size.
    h, w = gray.shape
    if h != INPUT_SIZE or w != INPUT_SIZE:
        # Lazy import cv2 — only needed if input isn't already the target size.
        # (Most callers from the geometric locator will pre-crop to roughly square.)
        try:
            import cv2  # noqa: E402
            gray = cv2.resize(gray, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_AREA)
        except Exception:
            return None
    # Normalize to [0, 1] (model is trained on (x/255), no mean subtraction — see train.py).
    gray = gray / 255.0
    return gray.reshape(1, INPUT_SIZE, INPUT_SIZE, INPUT_CHANNELS).astype(np.float32)


def predict(image: np.ndarray) -> Optional[dict]:
    """Run inference on a single tile image.

    Returns:
        dict with keys 'tile' (mpsz, e.g. '5m'), 'confidence' (float, 0..1),
        'probs' (length-34 list), and 'idx' (int class index); or
        None if the model is unavailable / image is invalid / inference failed.

    Callers should treat low 'confidence' as a hint to fall back to
    structural.py for that specific tile.
    """
    if not _load():
        return None
    tensor = _preprocess_one(image)
    if tensor is None:
        return None
    try:
        with _inference_lock:
            _interpreter.set_tensor(_input_details[0]["index"], tensor)
            _interpreter.invoke()
            probs = _interpreter.get_tensor(_output_details[0]["index"])[0]
        idx = int(np.argmax(probs))
        return {
            "tile": LABEL_LIST[idx],
            "idx": idx,
            "confidence": float(probs[idx]),
            "probs": probs.tolist(),
        }
    except Exception as e:
        print(f"[tflite_classifier] predict failed: {e!r}")
        return None


def predict_batch(images: List[np.ndarray]) -> List[Optional[dict]]:
    """Run inference on a batch of tile images (one interpreter.invoke per call).

    Returns a list aligned with `images`: each entry is the same dict as predict(),
    or None for entries that were invalid / failed.
    """
    if not images:
        return []
    if not _load():
        return [None] * len(images)
    tensors = []
    valid_idx = []
    for i, img in enumerate(images):
        t = _preprocess_one(img)
        if t is not None:
            tensors.append(t[0])  # strip batch dim
            valid_idx.append(i)
    if not tensors:
        return [None] * len(images)
    try:
        batched = np.stack(tensors, axis=0)
        with _inference_lock:
            _interpreter.set_tensor(_input_details[0]["index"], batched)
            _interpreter.invoke()
            probs = _interpreter.get_tensor(_output_details[0]["index"])
        results: List[Optional[dict]] = [None] * len(images)
        for k, i in enumerate(valid_idx):
            p = probs[k]
            idx = int(np.argmax(p))
            results[i] = {
                "tile": LABEL_LIST[idx],
                "idx": idx,
                "confidence": float(p[idx]),
                "probs": p.tolist(),
            }
        return results
    except Exception as e:
        print(f"[tflite_classifier] predict_batch failed: {e!r}")
        return [None] * len(images)


def warmup() -> bool:
    """Optional: trigger interpreter init + run a dummy inference so the first
    real call doesn't pay the latency cost. Returns True on success."""
    if not _load():
        return False
    try:
        dummy = np.zeros((INPUT_SIZE, INPUT_SIZE), dtype=np.uint8)
        return predict(dummy) is not None
    except Exception:
        return False