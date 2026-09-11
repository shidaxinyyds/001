# -*- coding: utf-8 -*-
import os, sys, glob, random
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, cv2, torch, torch.nn as nn, torch.optim as optim
from torch.utils.data import Dataset, DataLoader

CLASSES = [
    '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m',
    '1p', '2p', '3p', '4p', '5p', '6p', '7p', '8p', '9p',
    '1s', '2s', '3s', '4s', '5s', '6s', '7s', '8s', '9s',
    '1z', '2z', '3z', '4z', '5z', '6z', '7z'
]
CLASS_TO_IDX = {c: i for i, c in enumerate(CLASSES)}
TARGET_H, TARGET_W = 48, 36

def to_bgr(img, lab=''):
    if img is None: return None
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3].astype(np.float32) / 255.0
        white = np.ones((img.shape[0], img.shape[1], 3), np.float32) * 255.0
        f = img[:, :, :3].astype(np.float32)
        return (f * a[:, :, None] + white * (1.0 - a)[:, :, None]).astype(np.uint8)
    if img.ndim == 3 and img.shape[2] == 3:
        return img
    if img.ndim == 2:
        ink = (img > 100).astype(np.uint8)
        tile = np.ones((img.shape[0], img.shape[1], 3), np.uint8) * 245
        if lab.endswith('s'):
            tile[ink > 0] = [35, 140, 35]
        elif lab.endswith('m'):
            h = img.shape[0]
            tile[:int(h*0.45), :][ink[:int(h*0.45), :] > 0] = [30, 30, 30]
            tile[int(h*0.45):, :][ink[int(h*0.45):, :] > 0] = [20, 20, 180]
        elif lab.endswith('p'):
            tile[ink > 0] = [170, 50, 40]
        elif lab == '7z':
            tile[ink > 0] = [20, 20, 180]
        elif lab == '6z':
            tile[ink > 0] = [35, 140, 35]
        else:
            tile[ink > 0] = [30, 30, 30]
        return tile
    return img

def load_dataset():
    data = []
    # 1. localtest/real_tiles
    for f in glob.glob('localtest/real_tiles/*.png'):
        lab = os.path.basename(f)[:-4]
        if lab in CLASS_TO_IDX:
            img = to_bgr(cv2.imread(f, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'real_tiles'))
            
    # 2. localtest/tiles/duma520
    for f in glob.glob('localtest/tiles/duma520/*.png'):
        lab = os.path.basename(f)[:-4]
        if lab == '5z': lab = '7z'
        elif lab == '7z': lab = '5z'
        if lab in CLASS_TO_IDX:
            img = to_bgr(cv2.imread(f, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'duma520'))
            
    # 3. android/app/src/main/python/recognition/images/labelled
    for f in glob.glob('android/app/src/main/python/recognition/images/labelled/*.png'):
        lab = os.path.basename(f)[:-4]
        if lab in CLASS_TO_IDX:
            img = to_bgr(cv2.imread(f, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'labelled'))
            
    # 4. localtest/tiles/tencent_happy
    for f in glob.glob('localtest/tiles/tencent_happy/*.png'):
        lab = os.path.basename(f)[:-4]
        if lab in CLASS_TO_IDX:
            img = to_bgr(cv2.imread(f, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'tencent_happy'))

    # 5. android/app/src/main/python/recognition/images/styles/tencent_happy
    for f in glob.glob('android/app/src/main/python/recognition/images/styles/tencent_happy/*.png'):
        lab = os.path.basename(f)[:-4].split('_')[0]
        if lab in CLASS_TO_IDX:
            img = to_bgr(cv2.imread(f, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'style_th'))

    # 6. android/app/src/main/python/recognition/images/styles/wikipedia
    for f in glob.glob('android/app/src/main/python/recognition/images/styles/wikipedia/*.png'):
        lab = os.path.basename(f)[:-4].split('_')[0]
        if lab in CLASS_TO_IDX:
            img = to_bgr(cv2.imread(f, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'style_wiki'))
            
    # 7. localtest/tiles/screenshot
    for f in glob.glob('localtest/tiles/screenshot/*.png'):
        base = os.path.basename(f)[:-4]
        lab = base.split('_')[1] if '_' in base else base
        if lab in CLASS_TO_IDX:
            img = to_bgr(cv2.imread(f, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'screenshot'))
            
    # 8. D:/mj/sucai/crops/tiles_28e
    c_28e = ['2m', '3m', '4m', '7m', '7m', '8m', '2p', '3p', '3p', '4p', '5p', '6p', '8p']
    for idx, lab in enumerate(c_28e):
        p = f'D:/mj/sucai/crops/tiles_28e/tile_{idx+1}.jpg'
        if os.path.isfile(p):
            img = to_bgr(cv2.imread(p, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'sucai_28e'))
            
    # 9. D:/mj/sucai/crops/tiles_9d11
    # 1s, 2s, 3s, 3s, 3s, 4s, 6s, 6s, 7s, 7s, 2p, 8p, 3m
    c_9d11 = ['1s', '2s', '3s', '3s', '3s', '4s', '6s', '6s', '7s', '7s', '2p', '8p', '3m']
    for idx, lab in enumerate(c_9d11):
        p = f'D:/mj/sucai/crops/tiles_9d11/tile_{idx+1}.jpg'
        if os.path.isfile(p):
            img = to_bgr(cv2.imread(p, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'sucai_9d11'))
            
    # 10. D:/mj/sucai/crops/tiles_a36b
    c_a36b = ['1m', '2m', '3m', '4m', '7m', '8m', '8m', '8m', '9m', '9p', '9p', '2s', '4s', '1m']
    for idx, lab in enumerate(c_a36b):
        p = f'D:/mj/sucai/crops/tiles_a36b/tile_{idx+1}.jpg'
        if os.path.isfile(p):
            img = to_bgr(cv2.imread(p, cv2.IMREAD_UNCHANGED), lab)
            if img is not None: data.append((img, CLASS_TO_IDX[lab], 'sucai_a36b'))
            
    return data

def augment_tile(img_bgr):
    h, w = img_bgr.shape[:2]
    scale_x = random.uniform(0.93, 1.07)
    scale_y = random.uniform(0.93, 1.07)
    angle = random.uniform(-4.0, 4.0)
    tx = random.uniform(-0.03, 0.03) * w
    ty = random.uniform(-0.03, 0.03) * h
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    M[0, 0] *= scale_x
    M[1, 1] *= scale_y
    M[0, 2] += tx
    M[1, 2] += ty
    border_val = [int(x) for x in img_bgr[0:3, 0:3].mean(axis=(0, 1))]
    warped = cv2.warpAffine(img_bgr, M, (w, h), borderValue=border_val)
    resized = cv2.resize(warped, (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
    res = resized.astype(np.float32)
    res *= random.uniform(0.88, 1.12)
    mean_val = res.mean()
    res = (res - mean_val) * random.uniform(0.88, 1.12) + mean_val
    if random.random() < 0.25:
        res += np.random.normal(0, random.uniform(1.0, 3.0), res.shape).astype(np.float32)
    res = np.clip(res, 0.0, 255.0).astype(np.uint8)
    return res

class MahjongDataset(Dataset):
    def __init__(self, items, epoch_size=3200, augment=True):
        self.items = items
        self.epoch_size = epoch_size
        self.augment = augment
    def __len__(self): return self.epoch_size
    def __getitem__(self, idx):
        img_bgr, label, _ = random.choice(self.items)
        img = augment_tile(img_bgr) if self.augment else cv2.resize(img_bgr, (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return torch.from_numpy(img_rgb.transpose(2, 0, 1)), label

class SpatialTileCNN(nn.Module):
    def __init__(self, num_classes=34):
        super().__init__()
        self.features = nn.Sequential(
            # 48x36 -> 24x18
            nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.Conv2d(32, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.MaxPool2d(2, 2),
            # 24x18 -> 12x9
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
            nn.Conv2d(64, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
            nn.MaxPool2d(2, 2),
            # 12x9 -> 4x3 spatial grid
            nn.Conv2d(64, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d((4, 3))
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(0.20),
            nn.Linear(64 * 4 * 3, 128),
            nn.ReLU(inplace=True),
            nn.Linear(128, num_classes)
        )
    def forward(self, x): return self.classifier(self.features(x))

class ONNXExportModel(nn.Module):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.softmax = nn.Softmax(dim=1)
    def forward(self, x):
        return self.softmax(self.base(x))

def train():
    data = load_dataset()
    print(f'Loaded {len(data)} authentic tile samples across all styles.')
    dataset = MahjongDataset(data, epoch_size=3500, augment=True)
    loader = DataLoader(dataset, batch_size=32, shuffle=True)
    model = SpatialTileCNN(num_classes=len(CLASSES))
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.AdamW(model.parameters(), lr=1.8e-3, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=25)
    
    print('Training SpatialTileCNN...')
    model.train()
    for epoch in range(1, 26):
        total_loss, correct, total = 0.0, 0, 0
        for x, y in loader:
            optimizer.zero_grad()
            logits = model(x)
            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(y)
            pred = logits.argmax(dim=1)
            correct += (pred == y).sum().item()
            total += len(y)
        scheduler.step()
        acc = correct / total * 100
        print(f'Epoch {epoch:2d}/25 | Loss: {total_loss/total:.4f} | Acc: {acc:.2f}%')

    model.eval()
    export_model = ONNXExportModel(model)
    export_model.eval()
    
    onnx_path = 'android/app/src/main/python/recognition/models/tile_classifier.onnx'
    os.makedirs(os.path.dirname(onnx_path), exist_ok=True)
    dummy_input = torch.randn(1, 3, TARGET_H, TARGET_W, dtype=torch.float32)
    torch.onnx.export(
        export_model,
        dummy_input,
        onnx_path,
        input_names=['input'],
        output_names=['output'],
        dynamic_axes={'input': {0: 'batch'}, 'output': {0: 'batch'}}
    )
    print(f'Exported ONNX model to {onnx_path} (size: {os.path.getsize(onnx_path)/1024:.1f} KB)')
    
    net = cv2.dnn.readNetFromONNX(onnx_path)
    print('Testing OpenCV DNN inference on all authentic dataset items...')
    by_src = {}
    total_corr = 0
    for img_bgr, gt_idx, src in data:
        resized = cv2.resize(img_bgr, (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
        blob = cv2.dnn.blobFromImage(resized, scalefactor=1.0/255.0, size=(TARGET_W, TARGET_H), swapRB=True)
        net.setInput(blob)
        probs = net.forward()[0]
        pred_idx = int(np.argmax(probs))
        ok = (pred_idx == gt_idx)
        if ok: total_corr += 1
        if src not in by_src: by_src[src] = [0, 0]
        by_src[src][1] += 1
        if ok: by_src[src][0] += 1
        else:
            print(f'  [FAIL] {src}: exp {CLASSES[gt_idx]} != pred {CLASSES[pred_idx]} (conf: {probs[pred_idx]:.3f})')
            
    print('\n=== Accuracy By Dataset Source ===')
    for src, (c, t) in sorted(by_src.items()):
        print(f'{src:15s}: {c}/{t} ({c/t*100:.1f}%)')
    print(f'OVERALL TOTAL   : {total_corr}/{len(data)} ({total_corr/len(data)*100:.2f}%)\n')

if __name__ == '__main__':
    train()
