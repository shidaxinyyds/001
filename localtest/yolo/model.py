# -*- coding: utf-8 -*-
"""Ultra-lightweight YOLO-Mahjong-Nano architecture.

Designed for high-speed mobile inference (< 15ms) and 100% OpenCV cv2.dnn compatibility.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F

NUM_CLASSES = 34


def autopad(k, p=None):
    if p is None:
        p = k // 2 if isinstance(k, int) else [x // 2 for x in k]
    return p


class Conv(nn.Module):
    """Standard Conv2d + BatchNorm2d + SiLU."""
    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, act=True):
        super().__init__()
        self.conv = nn.Conv2d(c1, c2, k, s, autopad(k, p), groups=g, bias=False)
        self.bn = nn.BatchNorm2d(c2)
        self.act = nn.SiLU() if act else nn.Identity()

    def forward(self, x):
        return self.act(self.bn(self.conv(x)))


class Bottleneck(nn.Module):
    """Standard bottleneck."""
    def __init__(self, c1, c2, shortcut=True, g=1, k=(3, 3), e=0.5):
        super().__init__()
        c_ = int(c2 * e)
        self.cv1 = Conv(c1, c_, k[0], 1)
        self.cv2 = Conv(c_, c2, k[1], 1, g=g)
        self.add = shortcut and c1 == c2

    def forward(self, x):
        return x + self.cv2(self.cv1(x)) if self.add else self.cv2(self.cv1(x))


class C2f(nn.Module):
    """CSP Bottleneck with 2 convolutions."""
    def __init__(self, c1, c2, n=1, shortcut=False, g=1, e=0.5):
        super().__init__()
        self.c = int(c2 * e)
        self.cv1 = Conv(c1, 2 * self.c, 1, 1)
        self.cv2 = Conv((2 + n) * self.c, c2, 1)
        self.m = nn.ModuleList(Bottleneck(self.c, self.c, shortcut, g, k=((3, 3), (3, 3)), e=1.0) for _ in range(n))

    def forward(self, x):
        y = list(self.cv1(x).chunk(2, 1))
        y.extend(m(y[-1]) for m in self.m)
        return self.cv2(torch.cat(y, 1))


class MahjongYOLONano(nn.Module):
    """Dedicated lightweight Mahjong target detector."""
    def __init__(self, num_classes=NUM_CLASSES):
        super().__init__()
        self.num_classes = num_classes
        self.strides = [8, 16, 32]

        # Backbone (channels: 16 -> 32 -> 64 -> 128 -> 192)
        self.b_stem = Conv(3, 16, 3, 2)            # /2  (80, 320)
        self.b_stage1 = nn.Sequential(
            Conv(16, 32, 3, 2),                     # /4  (40, 160)
            C2f(32, 32, n=1, shortcut=True)
        )
        self.b_stage2 = nn.Sequential(
            Conv(32, 64, 3, 2),                     # /8  (20, 80) -> P3
            C2f(64, 64, n=2, shortcut=True)
        )
        self.b_stage3 = nn.Sequential(
            Conv(64, 128, 3, 2),                    # /16 (10, 40) -> P4
            C2f(128, 128, n=2, shortcut=True)
        )
        self.b_stage4 = nn.Sequential(
            Conv(128, 192, 3, 2),                   # /32 (5, 20) -> P5
            C2f(192, 192, n=1, shortcut=True)
        )

        # Neck (FPN + PAN)
        self.neck_up1 = nn.Upsample(scale_factor=2, mode='nearest')
        self.neck_c2f1 = C2f(192 + 128, 128, n=1, shortcut=False)

        self.neck_up2 = nn.Upsample(scale_factor=2, mode='nearest')
        self.neck_c2f2 = C2f(128 + 64, 64, n=1, shortcut=False)

        self.neck_down1 = Conv(64, 64, 3, 2)
        self.neck_c2f3 = C2f(64 + 128, 128, n=1, shortcut=False)

        self.neck_down2 = Conv(128, 128, 3, 2)
        self.neck_c2f4 = C2f(128 + 192, 192, n=1, shortcut=False)

        # Decoupled Detection Heads (P3, P4, P5)
        # Reg: 4 (cx, cy, w, h), Cls: 34 classes
        self.head_reg_p3 = nn.Sequential(Conv(64, 64, 3), nn.Conv2d(64, 4, 1))
        self.head_cls_p3 = nn.Sequential(Conv(64, 64, 3), nn.Conv2d(64, num_classes, 1))

        self.head_reg_p4 = nn.Sequential(Conv(128, 64, 3), nn.Conv2d(64, 4, 1))
        self.head_cls_p4 = nn.Sequential(Conv(128, 64, 3), nn.Conv2d(64, num_classes, 1))

        self.head_reg_p5 = nn.Sequential(Conv(192, 64, 3), nn.Conv2d(64, 4, 1))
        self.head_cls_p5 = nn.Sequential(Conv(192, 64, 3), nn.Conv2d(64, num_classes, 1))

    def forward(self, x):
        # Backbone
        x2 = self.b_stem(x)
        x4 = self.b_stage1(x2)
        p3 = self.b_stage2(x4)
        p4 = self.b_stage3(p3)
        p5 = self.b_stage4(p4)

        # Neck Top-down
        p4_up = self.neck_c2f1(torch.cat([self.neck_up1(p5), p4], dim=1))
        p3_out = self.neck_c2f2(torch.cat([self.neck_up2(p4_up), p3], dim=1))

        # Neck Bottom-up
        p4_out = self.neck_c2f3(torch.cat([self.neck_down1(p3_out), p4_up], dim=1))
        p5_out = self.neck_c2f4(torch.cat([self.neck_down2(p4_out), p5], dim=1))

        # Heads
        reg3 = self.head_reg_p3(p3_out)
        cls3 = self.head_cls_p3(p3_out)

        reg4 = self.head_reg_p4(p4_out)
        cls4 = self.head_cls_p4(p4_out)

        reg5 = self.head_reg_p5(p5_out)
        cls5 = self.head_cls_p5(p5_out)

        return (reg3, cls3), (reg4, cls4), (reg5, cls5)


class MahjongYOLOExport(nn.Module):
    """Export wrapper that decodes anchor boxes directly into standard YOLO output.
    
    Output shape: (batch, 4 + num_classes, total_anchors)
    Compatible with OpenCV cv2.dnn.readNetFromONNX.
    """
    def __init__(self, model: MahjongYOLONano, img_size=(160, 640)):
        super().__init__()
        self.model = model
        self.num_classes = model.num_classes
        h, w = img_size
        
        # Precompute anchor centers and strides
        grids = []
        strides = []
        for s in [8, 16, 32]:
            gh, gw = h // s, w // s
            y, x = torch.meshgrid(torch.arange(gh), torch.arange(gw), indexing='ij')
            grid = torch.stack([x, y], dim=-1).float() + 0.5  # center
            grids.append(grid.view(-1, 2) * s)
            strides.append(torch.full((gh * gw, 1), s, dtype=torch.float32))

        self.register_buffer("anchor_centers", torch.cat(grids, dim=0))  # (2100, 2)
        self.register_buffer("anchor_strides", torch.cat(strides, dim=0))  # (2100, 1)

    def forward(self, x):
        (reg3, cls3), (reg4, cls4), (reg5, cls5) = self.model(x)

        # Reshape and concatenate across feature levels
        # reg: (B, 4, H, W) -> (B, H*W, 4)
        b = x.shape[0]
        reg_all = torch.cat([
            reg3.permute(0, 2, 3, 1).reshape(b, -1, 4),
            reg4.permute(0, 2, 3, 1).reshape(b, -1, 4),
            reg5.permute(0, 2, 3, 1).reshape(b, -1, 4),
        ], dim=1)

        cls_all = torch.cat([
            cls3.permute(0, 2, 3, 1).reshape(b, -1, self.num_classes),
            cls4.permute(0, 2, 3, 1).reshape(b, -1, self.num_classes),
            cls5.permute(0, 2, 3, 1).reshape(b, -1, self.num_classes),
        ], dim=1).sigmoid()

        # Decode box (cx, cy, w, h)
        # reg_all[..., :2] is delta_xy relative to stride
        # reg_all[..., 2:] is exp(scale_wh) * stride
        cxcy = self.anchor_centers + reg_all[..., :2] * self.anchor_strides
        wh = torch.exp(torch.clamp(reg_all[..., 2:], -3.0, 4.0)) * self.anchor_strides * 2.0

        boxes = torch.cat([cxcy, wh], dim=-1)  # (B, 2100, 4)
        preds = torch.cat([boxes, cls_all], dim=-1)  # (B, 2100, 38)
        # Transpose to standard YOLOv8 ONNX layout: (B, 38, 2100)
        return preds.transpose(1, 2)
