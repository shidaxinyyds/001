"""Neural Tile Classifier using OpenCV DNN and lightweight ONNX model.
Runs in native C++ via cv2.dnn without requiring PyTorch or TensorFlow runtimes on Android.
"""
import os
import cv2
import numpy as np
from typing import Optional, Tuple

CLASSES = [
    '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m',
    '1p', '2p', '3p', '4p', '5p', '6p', '7p', '8p', '9p',
    '1s', '2s', '3s', '4s', '5s', '6s', '7s', '8s', '9s',
    '1z', '2z', '3z', '4z', '5z', '6z', '7z'
]

class NeuralTileClassifier:
    def __init__(self, model_path: Optional[str] = None):
        self.net = None
        self.classes = CLASSES
        if model_path is None:
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            cand_paths = [
                os.path.join(cur_dir, "models", "tile_classifier.onnx"),
                os.path.join(cur_dir, "tile_classifier.onnx"),
            ]
            for cp in cand_paths:
                if os.path.isfile(cp):
                    model_path = cp
                    break
        
        if model_path and os.path.isfile(model_path):
            try:
                self.net = cv2.dnn.readNetFromONNX(model_path)
                # Set preferable backend and target to CPU for max compatibility
                self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            except Exception as e:
                print(f"Failed to load ONNX model via cv2.dnn: {e}")
                self.net = None

    @property
    def is_available(self) -> bool:
        return self.net is not None

    def classify(self, face_bgr: np.ndarray) -> Tuple[Optional[str], float]:
        """Classify a cropped Mahjong tile face.
        Returns: (label, confidence)
        """
        if self.net is None or face_bgr is None or face_bgr.size == 0:
            return None, 0.0
        
        fh, fw = face_bgr.shape[:2]
        if fw < 12 or fh < 12:
            return None, 0.0

        try:
            # Resize to target input 36x48 (W=36, H=48)
            resized = cv2.resize(face_bgr, (36, 48), interpolation=cv2.INTER_AREA)
            # Normalize to 0..1 range with RGB conversion (swapRB=True)
            blob = cv2.dnn.blobFromImage(
                resized,
                scalefactor=1.0 / 255.0,
                size=(36, 48),
                mean=(0, 0, 0),
                swapRB=True,
                crop=False
            )
            self.net.setInput(blob)
            probs = self.net.forward()[0]
            top_idx = int(np.argmax(probs))
            top_conf = float(probs[top_idx])
            
            # If top confidence is reasonable, return top class
            if 0 <= top_idx < len(self.classes):
                return self.classes[top_idx], top_conf
            return None, 0.0
        except Exception as e:
            return None, 0.0

_classifier_instance: Optional[NeuralTileClassifier] = None

def get_neural_classifier() -> NeuralTileClassifier:
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = NeuralTileClassifier()
    return _classifier_instance
