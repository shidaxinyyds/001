"""Build a labeling montage for Mahjong-Soul tiles from the user's D:\\a\\1 footage.

We cannot auto-label majsoul tiles reliably (geometry is wrong on 3D art,
and cross-style matching is too weak). So we cluster the harvested real
tiles into visually-similar groups and render a montage for the USER to
label once. No new screenshots required -- we reuse the footage provided.

Output:
  localtest/majsoul_label_montage.png   (one row per cluster, 3 samples + id)
  localtest/majsoul_clusters.json       (cluster_id -> sample paths, size)
"""
import os, sys, glob, json
from collections import defaultdict
import cv2
import numpy as np

FOLDER = r"D:\a\1"
REPO = r"D:\a\realtime-mahjong-trainer-main"
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)
from recognition.structural import _GlyphBank, StructuralDetector  # noqa: E402

OUT_DIR = os.path.join(REPO, "localtest", "majsoul_clusters")
MONTAGE = os.path.join(REPO, "localtest", "majsoul_label_montage.png")
GREEN_LO = (30, 40, 60)
GREEN_HI = (95, 240, 240)
SIZE = 96
CLUSTER_THR = 0.60
MIN_CLUSTER = 5
MAX_PER_FRAME = 24

os.makedirs(OUT_DIR, exist_ok=True)


def strip_green(face):
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


def sharpness(gray):
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def main():
    det = StructuralDetector()
    files = sorted(glob.glob(os.path.join(FOLDER, "*")))[:96]
    tiles = []  # (norm_mask, face_img, sharp)
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
        for (x, y, bw, bh) in find_tiles(region)[:MAX_PER_FRAME]:
            px, py = int(bw * 0.08), int(bh * 0.06)
            f0 = region[max(0, y - py):min(region.shape[0], y + bh + py),
                        max(0, x - px):min(region.shape[1], x + bw + px)]
            if f0.size == 0:
                continue
            f0 = strip_green(f0)
            gray = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
            # must be a tile: bright face + some dark ink
            if gray.mean() < 120 or (gray < 90).mean() < 0.05:
                continue
            m = det._ink_mask(f0)          # ink = pips / character (distinctive)
            if not m.any():
                continue
            nm = _GlyphBank._norm(m, SIZE)
            tiles.append((nm, f0, sharpness(gray)))

    print(f"[harvest] raw tiles={len(tiles)}")

    # greedy clustering on normalized masks
    reps = []  # (mask, [members])
    for nm, f0, sh in tiles:
        best_i, best_s = -1, 0.0
        for i, (rm, _) in enumerate(reps):
            s = _GlyphBank._match_tolerant(nm, rm)
            if s > best_s:
                best_i, best_s = i, s
        if best_i >= 0 and best_s >= CLUSTER_THR:
            reps[best_i][1].append((nm, f0, sh))
        else:
            reps.append((nm, [(nm, f0, sh)]))

    # keep sizable clusters, sort by size desc
    reps = [r for r in reps if len(r[1]) >= MIN_CLUSTER]
    reps.sort(key=lambda r: len(r[1]), reverse=True)
    print(f"[cluster] kept clusters={len(reps)} (min_size={MIN_CLUSTER})")
    for i, (rm, members) in enumerate(reps):
        print(f"  C{i:02d}: {len(members)} tiles")

    # save samples + render montage
    json_out = {}
    cell_w, cell_h = 76, 100
    pad = 10
    row_h = cell_h + 26
    rows = len(reps)
    mont = np.full((rows * row_h + 16, cell_w * 4 + 30, 3), 255, np.uint8)
    # title
    cv2.putText(mont, "Mahjong-Soul tile clusters -- label each row (C00..)",
                (6, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    for i, (rm, members) in enumerate(reps):
        members.sort(key=lambda t: t[2], reverse=True)
        samples = members[:3]
        # save samples to disk
        cdir = os.path.join(OUT_DIR, f"C{i:02d}")
        os.makedirs(cdir, exist_ok=True)
        spaths = []
        for j, (nm, f0, sh) in enumerate(samples):
            sp = os.path.join(cdir, f"sample_{j}.png")
            cv2.imwrite(sp, f0)
            spaths.append(sp)
        json_out[f"C{i:02d}"] = {"size": len(members), "samples": spaths}
        # draw on montage
        y = 18 + i * row_h
        cv2.putText(mont, f"C{i:02d}", (4, y + cell_h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        for j, (nm, f0, sh) in enumerate(samples):
            x = 30 + j * (cell_w + pad)
            t = cv2.resize(f0, (cell_w, cell_h))
            mont[y:y + cell_h, x:x + cell_w] = t
    cv2.imwrite(MONTAGE, mont)
    with open(os.path.join(REPO, "localtest", "majsoul_clusters.json"), "w") as fh:
        json.dump(json_out, fh, indent=2)
    print(f"[out] montage={MONTAGE}")
    print(f"[out] json  =localtest/majsoul_clusters.json")


if __name__ == "__main__":
    main()
