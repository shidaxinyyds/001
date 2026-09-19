# -*- coding: utf-8 -*-
"""Synthetic Realistic Mahjong Hand & River Dataset Generator."""
import os
import glob
import random
import numpy as np
import cv2

CLASSES = [
    '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m',
    '1p', '2p', '3p', '4p', '5p', '6p', '7p', '8p', '9p',
    '1s', '2s', '3s', '4s', '5s', '6s', '7s', '8s', '9s',
    '1z', '2z', '3z', '4z', '5z', '6z', '7z'
]
CLASS_TO_IDX = {c: i for i, c in enumerate(CLASSES)}
NUM_CLASSES = len(CLASSES)

STRIP_W = 640
STRIP_H = 160


def load_all_tile_banks(base_dir: str):
    """Load authentic tile face images from all available banks."""
    bank = {c: [] for c in CLASSES}
    
    # 1. localtest/real_tiles
    rt_dir = os.path.join(base_dir, "localtest", "real_tiles")
    for f in glob.glob(os.path.join(rt_dir, "*.png")):
        name = os.path.splitext(os.path.basename(f))[0]
        if name in bank:
            im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if im is not None:
                bank[name].append(im)

    # 2. localtest/tiles/duma520
    duma_dir = os.path.join(base_dir, "localtest", "tiles", "duma520")
    for f in glob.glob(os.path.join(duma_dir, "*.png")):
        name = os.path.splitext(os.path.basename(f))[0]
        if name == '5z': name = '7z'
        elif name == '7z': name = '5z'
        if name in bank:
            im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if im is not None:
                bank[name].append(im)

    # 3. localtest/tiles/tencent_happy
    th_dir = os.path.join(base_dir, "localtest", "tiles", "tencent_happy")
    for f in glob.glob(os.path.join(th_dir, "*.png")):
        name = os.path.splitext(os.path.basename(f))[0].split('_')[0]
        if name in bank:
            im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if im is not None:
                bank[name].append(im)

    # 4. localtest/tiles/screenshot
    sc_dir = os.path.join(base_dir, "localtest", "tiles", "screenshot")
    for f in glob.glob(os.path.join(sc_dir, "*.png")):
        parts = os.path.splitext(os.path.basename(f))[0].split('_')
        name = parts[-1]
        if name in bank:
            im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if im is not None:
                bank[name].append(im)

    print(f"Loaded tile bank: {sum(len(v) for v in bank.values())} source tiles across {len(bank)} classes.")
    return bank


def _render_tile(tile_raw: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Render a clean 3D Mahjong tile with bevel and realistic body."""
    tile_bgr = np.ones((target_h, target_w, 3), dtype=np.uint8) * 242
    grad = np.linspace(250, 235, target_h)[:, None, None]
    tile_bgr = (tile_bgr.astype(np.float32) * (grad / 242.0)).astype(np.uint8)

    # Bevel highlight on top and left edge
    tile_bgr[0:2, :, :] = 255
    tile_bgr[:, 0:2, :] = 255
    # Bevel shadow on bottom and right edge
    tile_bgr[-2:, :, :] = 190
    tile_bgr[:, -2:, :] = 200

    # Inset for inner face
    inset_x = max(2, int(target_w * 0.08))
    inset_y = max(2, int(target_h * 0.08))
    fw = target_w - inset_x * 2
    fh = target_h - inset_y * 2

    if tile_raw.ndim == 3 and tile_raw.shape[2] == 4:
        face_res = cv2.resize(tile_raw, (fw, fh), interpolation=cv2.INTER_AREA)
        alpha = (face_res[:, :, 3].astype(np.float32) / 255.0)[:, :, None]
        fg = face_res[:, :, :3].astype(np.float32)
        bg = tile_bgr[inset_y:inset_y + fh, inset_x:inset_x + fw].astype(np.float32)
        blended = (fg * alpha + bg * (1.0 - alpha)).astype(np.uint8)
        tile_bgr[inset_y:inset_y + fh, inset_x:inset_x + fw] = blended
    else:
        face_bgr = tile_raw if tile_raw.ndim == 3 else cv2.cvtColor(tile_raw, cv2.COLOR_GRAY2BGR)
        face_res = cv2.resize(face_bgr, (fw, fh), interpolation=cv2.INTER_AREA)
        tile_bgr[inset_y:inset_y + fh, inset_x:inset_x + fw] = face_res

    return tile_bgr


def generate_mahjong_strip(tile_bank, strip_w=STRIP_W, strip_h=STRIP_H):
    """Generate a realistic synthetic Mahjong hand/river strip with bounding boxes.
    
    Returns:
        strip_bgr: np.ndarray (strip_h, strip_w, 3)
        boxes: list of [class_id, x1, y1, x2, y2]
    """
    bg_choice = random.random()
    if bg_choice < 0.65:
        base_color = [random.randint(25, 45), random.randint(70, 110), random.randint(25, 45)]
    elif bg_choice < 0.85:
        base_color = [random.randint(90, 140), random.randint(55, 85), random.randint(20, 45)]
    else:
        base_color = [random.randint(30, 55), random.randint(35, 60), random.randint(45, 75)]

    strip = np.zeros((strip_h, strip_w, 3), dtype=np.uint8)
    strip[:, :] = base_color

    noise = np.random.normal(0, 4.0, (strip_h, strip_w, 3))
    strip = np.clip(strip.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    r = random.random()
    if r < 0.65:
        num_tiles = random.choice([13, 14])
    elif r < 0.85:
        num_tiles = random.choice([7, 8, 10, 11])
    else:
        num_tiles = random.choice([4, 5, 6])

    chosen_classes = random.choices(CLASSES, k=num_tiles)
    if num_tiles in (14, 11, 8, 5) and random.random() < 0.7:
        main_part = sorted(chosen_classes[:-1])
        chosen_classes = main_part + [chosen_classes[-1]]
    elif random.random() < 0.8:
        chosen_classes = sorted(chosen_classes)

    tile_h = random.randint(int(strip_h * 0.55), int(strip_h * 0.76))
    aspect = random.uniform(0.70, 0.76)
    tile_w = int(tile_h * aspect)

    gap = random.randint(-1, 3)
    total_w = num_tiles * tile_w + (num_tiles - 1) * gap

    if total_w > strip_w - 20:
        scale = (strip_w - 30) / float(total_w)
        tile_w = max(18, int(tile_w * scale))
        tile_h = max(24, int(tile_h * scale))
        gap = max(-1, int(gap * scale))
        total_w = num_tiles * tile_w + (num_tiles - 1) * gap

    max_start_x = max(10, strip_w - total_w - 10)
    start_x = random.randint(10, max_start_x)
    base_y = random.randint(int(strip_h * 0.18), max(int(strip_h * 0.18) + 1, strip_h - tile_h - 10))

    boxes = []
    lifted_idx = random.randint(0, num_tiles - 1) if random.random() < 0.35 else -1

    cur_x = start_x
    for i, cls_name in enumerate(chosen_classes):
        sample_raw = random.choice(tile_bank[cls_name])
        tile_img = _render_tile(sample_raw, tile_w, tile_h)

        cur_y = base_y
        if i == lifted_idx:
            cur_y = max(4, base_y - random.randint(8, 22))
        elif random.random() < 0.15:
            cur_y = base_y + random.randint(-2, 2)

        if cur_x + tile_w >= strip_w:
            break
        y2 = min(strip_h, cur_y + tile_h)
        x2 = min(strip_w, cur_x + tile_w)
        actual_h = y2 - cur_y
        actual_w = x2 - cur_x

        if actual_w >= 14 and actual_h >= 18:
            shadow_h = min(6, strip_h - y2)
            if shadow_h > 0:
                shadow_area = strip[y2:y2 + shadow_h, cur_x:x2]
                strip[y2:y2 + shadow_h, cur_x:x2] = (shadow_area.astype(np.float32) * 0.65).astype(np.uint8)

            strip[cur_y:y2, cur_x:x2] = tile_img[:actual_h, :actual_w]
            boxes.append([CLASS_TO_IDX[cls_name], cur_x, cur_y, x2, y2])

        cur_x += tile_w + gap

    if random.random() < 0.6:
        grad_alpha = np.linspace(random.uniform(0.85, 1.15), random.uniform(0.85, 1.15), strip_w)[None, :, None]
        strip = np.clip(strip.astype(np.float32) * grad_alpha, 0, 255).astype(np.uint8)

    if random.random() < 0.5:
        alpha = random.uniform(0.88, 1.12)
        beta = random.randint(-15, 15)
        strip = np.clip(strip.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)

    if random.random() < 0.4:
        ksize = random.choice([3, 5])
        strip = cv2.GaussianBlur(strip, (ksize, ksize), random.uniform(0.5, 1.2))

    return strip, boxes


def generate_river_row(tile_bank, strip_w=STRIP_W, strip_h=STRIP_H):
    """生成牌河行合成图：牌桌上某一行的弃牌。

    与手牌行的关键差异（正是现任模型学不到的部分）：
      - 牌更小：tile_h 约占行高 0.30~0.50（手牌是 0.55~0.76）
      - 每行更多张：8~18 张（含换行前的一整排弃牌）
      - 背景恒为深色绒布（牌桌），且牌间距更紧、常带轻微错位
    返回 (strip_bgr, boxes)，boxes=[[cls_id,x1,y1,x2,y2], ...]，与手牌行同格式。
    """
    # 深色绒布桌面（腾讯血流红中牌桌为墨绿），带少量色相/亮度抖动
    base_color = [random.randint(20, 42), random.randint(58, 96), random.randint(22, 44)]
    strip = np.zeros((strip_h, strip_w, 3), dtype=np.uint8)
    strip[:, :] = base_color
    noise = np.random.normal(0, 5.0, (strip_h, strip_w, 3))
    strip = np.clip(strip.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    num_tiles = random.choice([8, 9, 10, 11, 12, 13, 14, 15, 16, 18])
    chosen_classes = random.choices(CLASSES, k=num_tiles)

    tile_h = random.randint(int(strip_h * 0.30), int(strip_h * 0.50))
    aspect = random.uniform(0.68, 0.78)
    tile_w = int(tile_h * aspect)
    gap = random.randint(0, 3)
    total_w = num_tiles * tile_w + (num_tiles - 1) * gap

    if total_w > strip_w - 12:
        scale = (strip_w - 16) / float(total_w)
        tile_w = max(14, int(tile_w * scale))
        tile_h = max(18, int(tile_h * scale))
        gap = max(0, int(gap * scale))
        total_w = num_tiles * tile_w + (num_tiles - 1) * gap

    max_start_x = max(6, strip_w - total_w - 6)
    start_x = random.randint(6, max_start_x)
    base_y = random.randint(int(strip_h * 0.28), max(int(strip_h * 0.28) + 1, strip_h - tile_h - 6))

    boxes = []
    cur_x = start_x
    for cls_name in chosen_classes:
        sample_raw = random.choice(tile_bank[cls_name])
        tile_img = _render_tile(sample_raw, tile_w, tile_h)
        cur_y = base_y + (random.randint(-2, 2) if random.random() < 0.25 else 0)
        cur_y = max(2, min(strip_h - tile_h - 2, cur_y))
        if cur_x + tile_w >= strip_w:
            break
        y2 = min(strip_h, cur_y + tile_h)
        x2 = min(strip_w, cur_x + tile_w)
        if (x2 - cur_x) >= 12 and (y2 - cur_y) >= 14:
            strip[cur_y:y2, cur_x:x2] = tile_img[:y2 - cur_y, :x2 - cur_x]
            boxes.append([CLASS_TO_IDX[cls_name], cur_x, cur_y, x2, y2])
        cur_x += tile_w + gap

    if random.random() < 0.5:
        alpha = random.uniform(0.85, 1.12)
        beta = random.randint(-14, 12)
        strip = np.clip(strip.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
    if random.random() < 0.4:
        strip = cv2.GaussianBlur(strip, (3, 3), random.uniform(0.4, 1.0))

    return strip, boxes


def generate_training_strip(tile_bank, strip_w=STRIP_W, strip_h=STRIP_H, river_frac=0.5):
    """训练样本调度器：按 river_frac 概率生成牌河行，否则生成手牌行。

    返回 (strip_bgr, boxes, kind)，kind in {'hand','river'}。
    """
    if random.random() < river_frac:
        s, b = generate_river_row(tile_bank, strip_w, strip_h)
        return s, b, 'river'
    s, b = generate_mahjong_strip(tile_bank, strip_w, strip_h)
    return s, b, 'hand'
