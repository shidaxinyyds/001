"""Build a near-complete Mahjong-Soul style bank from the user's D:\\a\\1 footage.

No new screenshots required: the CENTRAL green playfield in those frames
contains opponents' discards / called tiles / side hands, which the
recognizer auto-labels cross-style (geometric man/honor detection + the
existing p/s templates). We harvest every frame, cluster by visual
self-consistency, and emit one template per class -- WITHOUT overwriting
the 10 already-verified majsoul templates (1p,4p,5p,6p,7p,8p,9p,3s,4s,6s).

Safety gates (reject mislabeled outliers):
  * cluster size >= 3 (>=2 tiles agree with the chosen representative)
  * chosen template self-matches its own cluster at >= 0.65
  * chosen template, matched against the FULL style bank, returns its own
    label with score >= 0.55 (not secretly closer to another class)

Output: android/.../images/styles/majsoul/<label>.png  (+ report to stdout)
"""
import os, sys, glob, argparse
from collections import defaultdict, Counter
import cv2
import numpy as np

FOLDER = r"D:\a\1"
REPO = r"D:\a\realtime-mahjong-trainer-main"
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from recognition.structural import StructuralDetector, _GlyphBank, _StyleBank  # noqa: E402

OUT = os.path.join(PYROOT, "recognition", "images", "styles", "majsoul")
GREEN_LO = (30, 40, 60)
GREEN_HI = (95, 240, 240)
SIZE = 96

ALL_LABELS = [f"{n}{k}" for k in ("m", "p", "s") for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)]
# the 10 already-verified templates -- never overwrite
KEEP = {"1p", "4p", "5p", "6p", "7p", "8p", "9p", "3s", "4s", "6s"}


def _strip_green(face):
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, GREEN_LO, GREEN_HI)
    out = face.copy()
    out[green > 0] = 0
    return out


def find_tiles(img):
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bright = (gray > 175).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, k)
    cnts, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tiles, ra = [], h * w
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        area = bw * bh
        if area < ra * 0.0025 or area > ra * 0.03:
            continue
        asp = bw / float(max(1, bh))
        if asp < 0.55 or asp > 0.95:
            continue
        if bh < 24 or bw < 18:
            continue
        tiles.append((x, y, bw, bh))
    return tiles


def to_tpl(det, face):
    fh, fw = face.shape[:2]
    ix, iy = int(fw * 0.06), int(fh * 0.06)
    face = face[iy:fh - iy, ix:fw - ix]
    face = _strip_green(face)
    m = det._ink_mask(face)
    ys, xs = np.where(m > 0)
    if len(ys) == 0:
        return None
    crop = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ch, cw = crop.shape
    sc = 96.0 / float(max(ch, cw))
    nh, nw = max(1, int(ch * sc)), max(1, int(cw * sc))
    tpl = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
    _, tpl = cv2.threshold(tpl, 100, 255, cv2.THRESH_BINARY)
    return tpl


def sharpness(gray):
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10**9)
    args = ap.parse_args()

    det = StructuralDetector()
    style = det._styles  # full bank incl. the 10 majsoul + others
    files = sorted(glob.glob(os.path.join(FOLDER, "*")))[: args.limit]

    # label -> list of (norm96 mask, sharpness)
    by_label = defaultdict(list)
    total = 0
    for fi, f in enumerate(files):
        im = cv2.imread(f)
        if im is None:
            continue
        h, w = im.shape[:2]
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, GREEN_LO, GREEN_HI)
        ys = np.where(green.sum(1) > w * 0.10)[0]
        if len(ys) == 0:
            continue
        y0, y1 = max(0, int(ys.min()) - 30), min(h, int(ys.max()) + 30)
        region = im[y0:y1, :]
        for (x, y, bw, bh) in find_tiles(region):
            px, py = int(bw * 0.08), int(bh * 0.06)
            f0 = region[max(0, y - py):min(region.shape[0], y + bh + py),
                        max(0, x - px):min(region.shape[1], x + bw + px)]
            if f0.size == 0:
                continue
            f0 = _strip_green(f0)
            label, conf = det._classify_face(f0)
            if label is None or label not in ALL_LABELS:
                continue
            m = det._ink_mask(f0)
            if not m.any():
                continue
            nm = _GlyphBank._norm(m, SIZE)
            by_label[label].append((nm, sharpness(cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)), f0))
            total += 1
    print(f"[harvest] frames={len(files)} tiles_classified={total}")

    # build templates
    os.makedirs(OUT, exist_ok=True)
    existing = {os.path.splitext(n)[0] for n in os.listdir(OUT) if n.endswith(".png")}
    added, skipped = [], []
    for label in ALL_LABELS:
        if label in existing:
            continue  # keep the 10 verified
        tiles = by_label.get(label, [])
        if len(tiles) < 3:
            skipped.append((label, len(tiles), "too_few"))
            continue
        # pick the sharpest as seed; require >=2 others match it >=0.65
        tiles.sort(key=lambda t: t[1], reverse=True)
        seed = tiles[0][0]
        agree = sum(1 for t in tiles[1:] if _GlyphBank._match_tolerant(seed, t[0]) >= 0.65)
        if agree < 2:
            skipped.append((label, len(tiles), f"low_agree({agree})"))
            continue
        # self-consistency vs full bank: must return own label
        bl, bs, bm = style.match(tiles[0][0])
        if bl != label or bs < 0.55:
            skipped.append((label, len(tiles), f"bank_mismatch({bl},{bs:.2f})"))
            continue
        tpl = to_tpl(det, tiles[0][2])
        if tpl is None:
            skipped.append((label, len(tiles), "empty_ink"))
            continue
        cv2.imwrite(os.path.join(OUT, f"{label}.png"), tpl)
        added.append(label)

    print(f"[build] added={len(added)} kept={len(existing)} skipped={len(skipped)}")
    print("  added:", " ".join(added))
    print("  skipped:")
    for s in skipped:
        print("    ", s)
    have = sorted(existing | set(added))
    print(f"[coverage] {len(have)}/34 classes present")
    miss = [l for l in ALL_LABELS if l not in have]
    if miss:
        print("  missing:", " ".join(miss))


if __name__ == "__main__":
    main()
