"""TileNN: data-driven, style-invariant tile classifier.

Approach
--------
A tile's identity is encoded by its shape topology, which is *universal* across
mahjong art styles:
  - 筒(pin):  the number of circles (1p..9p)
  - 条(sou):  the number of bamboo (1s..9s; 1s = bird)
  - 萬(man):  a 萬 block + a universal numeral (一..九)
  - 字(honor): 白=blank, 中=red, 發=green, 東南西北=universal chars

We capture this with a HOG (histogram-of-gradients) descriptor and a
nearest-centroid classifier trained on a *multi-style* database of real tile
images. Because HOG responds to local gradient/shape structure rather than raw
texture, tiles of the same label but different art cluster together, so a game
whose art resembles any training style is recognized.

The model is precomputed offline and shipped as a small JSON (centroids + labels),
so the device needs neither the raw images nor any ML framework — only numpy/cv2.

Device note: Python 3.8, numpy 1.x, OpenCV 4.5. Avoid 3.9+ syntax.
"""
import os
import json
import math
import numpy as np
import cv2

# Grid geometry (must match what training used).
CELL = 8
NX, NY = 6, 8
RES_W, RES_H = NX * CELL, NY * CELL
NBINS = 9
EPS = 1e-6

# Ink fraction below which a tile is treated as 白板 (blank).
BLANK_INK = 0.030


def _ink_mask(face: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    lo = float(np.percentile(v, 5))
    hi = float(np.percentile(v, 95))
    if hi - lo < 12.0:
        return np.zeros(v.shape, np.uint8)
    _, bv = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, bs = cv2.threshold(s, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = cv2.bitwise_or((bv == 0).astype(np.uint8), (bs > 0).astype(np.uint8)) * 255
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def hog(img_bgr: np.ndarray) -> np.ndarray:
    """HOG descriptor of a tile image (any size). Returns float32 vector.

    Computed on the INK MASK (foreground) rather than raw pixels, so the
    background (white card, green felt, table) is stripped out and the descriptor
    responds only to the tile's drawn shape — this is what makes it style-invariant.
    """
    m = _ink_mask(img_bgr)
    if m.sum() == 0:
        return np.zeros((NX - 1) * (NY - 1) * NBINS * 4, np.float32)
    g = cv2.resize(m, (RES_W, RES_H), interpolation=cv2.INTER_AREA)
    g = g.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=1)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=1)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = (np.arctan2(gy, gx) * 180.0 / math.pi) % 180.0
    hist = np.zeros((NY, NX, NBINS), np.float32)
    bx = RES_W / NX
    by = RES_H / NY
    for cy in range(NY):
        for cx in range(NX):
            for yy in range(int(cy * by), int((cy + 1) * by)):
                for xx in range(int(cx * bx), int((cx + 1) * bx)):
                    b = int(ang[yy, xx] / 20.0) % NBINS
                    hist[cy, cx, b] += mag[yy, xx]
    feat = []
    for cy in range(NY - 1):
        for cx in range(NX - 1):
            v = hist[cy:cy + 2, cx:cx + 2, :].flatten()
            n = np.linalg.norm(v)
            feat.append(v / (n + EPS))
    return np.concatenate(feat).astype(np.float32)


class TileNN:
    def __init__(self, model_path: str):
        with open(model_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        self.labels = d["labels"]
        self.centroids = np.array(d["centroids"], dtype=np.float32)  # (n, D)
        self.norm = np.linalg.norm(self.centroids, axis=1)  # (n,)
        self.blank_ink = d.get("blank_ink", BLANK_INK)

    def classify(self, face: np.ndarray):
        """face: BGR tile image. Returns (label_or_None, confidence)."""
        if face is None or face.size == 0:
            return None, 0.0
        fh, fw = face.shape[:2]
        if fw < 16 or fh < 16:
            return None, 0.0
        m = _ink_mask(face)
        ink = int((m > 0).sum()) / float(fw * fh)
        if ink < self.blank_ink:
            return "5z", 0.9
        f = hog(face)
        fn = np.linalg.norm(f) + EPS
        sim = self.centroids @ f / (self.norm * fn)  # cosine similarity
        order = np.argsort(-sim)
        best = int(order[0])
        second = int(order[1]) if len(sim) > 1 else -1
        # margin-based confidence: how much better the best is than the runner-up
        margin = sim[best] - (sim[second] if second >= 0 else 0.0)
        conf = float(0.5 * sim[best] + 0.5 * max(0.0, margin))
        return self.labels[best], conf

    @staticmethod
    def save(model_path: str, labels, centroids, blank_ink=BLANK_INK):
        d = {
            "labels": list(labels),
            "centroids": np.asarray(centroids, dtype=np.float32).tolist(),
            "blank_ink": blank_ink,
        }
        with open(model_path, "w", encoding="utf-8") as f:
            json.dump(d, f)
