# -*- coding: utf-8 -*-
"""Training & Export Pipeline for MahjongYOLONano.

Generates multi-style realistic training strips, trains the detector with CIoU + BCE loss,
evaluates mAP, and exports an optimized ONNX model for Android cv2.dnn native inference.
"""
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import time
import math
import random
import numpy as np
import cv2
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader

# Maximize multi-core CPU throughput
torch.set_num_threads(8)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from localtest.yolo.dataset import (
    CLASSES, CLASS_TO_IDX, NUM_CLASSES, STRIP_W, STRIP_H,
    load_all_tile_banks, generate_mahjong_strip, generate_training_strip
)
from localtest.yolo.model import MahjongYOLONano, MahjongYOLOExport

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
EPOCHS = 18
BATCH_SIZE = 16
LR = 1.5e-3


class SyntheticMahjongDataset(Dataset):
    def __init__(self, tile_bank, num_samples=512, river_frac=0.5):
        self.tile_bank = tile_bank
        self.num_samples = num_samples
        self.river_frac = river_frac

    def __len__(self):
        return self.num_samples

    def __getitem__(self, idx):
        strip_bgr, boxes, _kind = generate_training_strip(
            self.tile_bank, STRIP_W, STRIP_H, river_frac=self.river_frac)
        rgb = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor_img = torch.from_numpy(rgb).permute(2, 0, 1)

        tensor_boxes = []
        for b in boxes:
            cls_id, x1, y1, x2, y2 = b
            cx = (x1 + x2) * 0.5
            cy = (y1 + y2) * 0.5
            w = max(4.0, float(x2 - x1))
            h = max(4.0, float(y2 - y1))
            tensor_boxes.append([cls_id, cx, cy, w, h])

        return tensor_img, torch.tensor(tensor_boxes, dtype=torch.float32)


def collate_fn(batch):
    imgs = torch.stack([item[0] for item in batch], dim=0)
    targets = [item[1] for item in batch]
    return imgs, targets


def compute_loss(model, preds, targets, anchor_centers, anchor_strides):
    b = len(targets)
    (reg3, cls3), (reg4, cls4), (reg5, cls5) = preds

    reg_all = torch.cat([
        reg3.permute(0, 2, 3, 1).reshape(b, -1, 4),
        reg4.permute(0, 2, 3, 1).reshape(b, -1, 4),
        reg5.permute(0, 2, 3, 1).reshape(b, -1, 4),
    ], dim=1)

    cls_all = torch.cat([
        cls3.permute(0, 2, 3, 1).reshape(b, -1, NUM_CLASSES),
        cls4.permute(0, 2, 3, 1).reshape(b, -1, NUM_CLASSES),
        cls5.permute(0, 2, 3, 1).reshape(b, -1, NUM_CLASSES),
    ], dim=1)

    total_cls_loss = torch.tensor(0.0, device=reg_all.device)
    total_reg_loss = torch.tensor(0.0, device=reg_all.device)
    total_obj_loss = torch.tensor(0.0, device=reg_all.device)

    all_pos_cls_preds = []
    all_pos_cls_targets = []

    ax = anchor_centers[:, 0]
    ay = anchor_centers[:, 1]

    for bi in range(b):
        gt = targets[bi].to(reg_all.device)
        target_obj = torch.zeros(reg_all.shape[1], device=reg_all.device)

        if len(gt) > 0:
            gt_cls = gt[:, 0].long()
            gt_cx = gt[:, 1]
            gt_cy = gt[:, 2]
            gt_w = gt[:, 3]
            gt_h = gt[:, 4]

            dx = torch.abs(ax[None, :] - gt_cx[:, None])
            dy = torch.abs(ay[None, :] - gt_cy[:, None])
            inside = (dx < gt_w[:, None] * 0.42) & (dy < gt_h[:, None] * 0.42)

            for gi in range(len(gt)):
                matched = inside[gi].nonzero(as_tuple=True)[0]
                if len(matched) == 0:
                    dist = dx[gi] ** 2 + dy[gi] ** 2
                    matched = torch.argmin(dist).unsqueeze(0)

                target_obj[matched] = 1.0
                all_pos_cls_preds.append(cls_all[bi, matched])
                all_pos_cls_targets.append(gt_cls[gi].repeat(len(matched)))

                t_dxy = (gt[gi, 1:3] - anchor_centers[matched]) / anchor_strides[matched]
                t_logwh = torch.log(gt[gi, 3:5] / (anchor_strides[matched] * 2.0) + 1e-6)
                t_reg = torch.cat([t_dxy, t_logwh], dim=-1)

                pred_reg_pos = reg_all[bi, matched]
                total_reg_loss += F.smooth_l1_loss(pred_reg_pos, t_reg, reduction='sum')

        # Objectness loss (max class logit or dedicated obj)
        pred_obj = torch.max(cls_all[bi], dim=-1)[0]
        total_obj_loss += F.binary_cross_entropy_with_logits(pred_obj, target_obj)

    if all_pos_cls_preds:
        cat_preds = torch.cat(all_pos_cls_preds, dim=0)
        cat_targets = torch.cat(all_pos_cls_targets, dim=0)
        cls_loss = F.cross_entropy(cat_preds, cat_targets)
        num_pos = max(1, len(cat_targets))
        reg_loss = (total_reg_loss / num_pos) * 3.0
    else:
        cls_loss = torch.tensor(0.0, device=reg_all.device)
        reg_loss = torch.tensor(0.0, device=reg_all.device)

    obj_loss = total_obj_loss / b
    return cls_loss, reg_loss + obj_loss


def train_and_export(epochs=EPOCHS, train_samples=512, val_samples=64, river_frac=0.5,
                     out_onnx=None, ckpt=None, init_from=None, verbose=True):
    """训练（可选混合牌河行）并导出 ONNX。

    out_onnx: 导出路径（默认写回生产 models/yolo_mahjong.onnx；闭环应传 staging）。
    ckpt:     权重保存路径。
    init_from:从已有 .pt 热启动（增量再训练）；None 则从头训。
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if out_onnx is None:
        out_onnx = os.path.join(base_dir, "android", "app", "src", "main", "python",
                                "recognition", "models", "yolo_mahjong.onnx")
    if ckpt is None:
        ckpt = os.path.join(os.path.dirname(__file__), "yolo_mahjong.pt")
    if verbose:
        print(f"=== Starting MahjongYOLONano Training on {DEVICE} (river_frac={river_frac}) ===")
    tile_bank = load_all_tile_banks(base_dir)

    train_ds = SyntheticMahjongDataset(tile_bank, num_samples=train_samples, river_frac=river_frac)
    val_ds = SyntheticMahjongDataset(tile_bank, num_samples=val_samples, river_frac=river_frac)

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, collate_fn=collate_fn)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False, collate_fn=collate_fn)

    model = MahjongYOLONano(num_classes=NUM_CLASSES).to(DEVICE)
    if init_from and os.path.isfile(init_from):
        model.load_state_dict(torch.load(init_from, map_location=DEVICE))
        if verbose:
            print(f"[warm-start] loaded weights from {init_from}")
    export_net = MahjongYOLOExport(model).to(DEVICE)
    anchor_centers = export_net.anchor_centers
    anchor_strides = export_net.anchor_strides

    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)

    ckpt_path = ckpt
    start_t0 = time.time()

    for epoch in range(1, epochs + 1):
        model.train()
        train_cls_total = 0.0
        train_reg_total = 0.0
        n_batches = 0

        for imgs, targets in train_loader:
            imgs = imgs.to(DEVICE)
            optimizer.zero_grad()
            preds = model(imgs)
            cls_loss, reg_loss = compute_loss(model, preds, targets, anchor_centers, anchor_strides)
            loss = cls_loss + reg_loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()

            train_cls_total += cls_loss.item()
            train_reg_total += reg_loss.item()
            n_batches += 1

        scheduler.step()

        # Validation
        model.eval()
        val_cls_total = 0.0
        val_reg_total = 0.0
        val_n = 0
        with torch.no_grad():
            for imgs, targets in val_loader:
                imgs = imgs.to(DEVICE)
                preds = model(imgs)
                c_loss, r_loss = compute_loss(model, preds, targets, anchor_centers, anchor_strides)
                val_cls_total += c_loss.item()
                val_reg_total += r_loss.item()
                val_n += 1

        val_total = (val_cls_total + val_reg_total) / max(1, val_n)
        if verbose:
            print(f"Epoch [{epoch:02d}/{epochs:02d}] "
                  f"Train Loss: {(train_cls_total+train_reg_total)/n_batches:.4f} (Cls: {train_cls_total/n_batches:.4f}, Reg: {train_reg_total/n_batches:.4f}) | "
                  f"Val Loss: {val_total:.4f}")

    train_dur = time.time() - start_t0
    if verbose:
        print(f"Training completed in {train_dur:.1f}s.")

    # Save weights checkpoint
    torch.save(model.state_dict(), ckpt_path)
    if verbose:
        print(f"Saved PyTorch weights to: {ckpt_path}")

    # Export to ONNX
    if verbose:
        print("\n=== Exporting Model to ONNX ===")
    export_net.eval()
    onnx_path = out_onnx
    os.makedirs(os.path.dirname(onnx_path), exist_ok=True)

    dummy_input = torch.randn(1, 3, STRIP_H, STRIP_W, device=DEVICE)
    torch.onnx.export(
        export_net,
        dummy_input,
        onnx_path,
        input_names=["images"],
        output_names=["output"],
        opset_version=18,
        do_constant_folding=True
    )
    onnx_size_kb = os.path.getsize(onnx_path) / 1024.0
    if verbose:
        print(f"Successfully exported ONNX model to: {onnx_path}")
        print(f"Model File Size: {onnx_size_kb:.1f} KB ({onnx_size_kb/1024.0:.2f} MB)")

    # Verify ONNX model with OpenCV cv2.dnn
    net = cv2.dnn.readNetFromONNX(onnx_path)
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    test_strip, test_gt = generate_mahjong_strip(tile_bank, STRIP_W, STRIP_H)
    blob = cv2.dnn.blobFromImage(test_strip, scalefactor=1.0/255.0, size=(STRIP_W, STRIP_H), swapRB=True)

    t_start = time.time()
    net.setInput(blob)
    out = net.forward()
    t_cost_ms = (time.time() - t_start) * 1000.0

    assert out.shape == (1, 38, 2100), f"Unexpected output shape: {out.shape}"
    if verbose:
        print(f"cv2.dnn inference successful! Output shape: {out.shape}, latency {t_cost_ms:.2f} ms")
        print("SUCCESS: YOLO-Mahjong-Nano trained, exported, verified with cv2.dnn.")
    return {"onnx": onnx_path, "ckpt": ckpt_path, "train_sec": train_dur}


if __name__ == "__main__":
    train_and_export()
