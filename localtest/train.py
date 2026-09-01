# -*- coding: utf-8 -*-
"""
v3 (this round): 34-CLASS UPGRADE.

数据构成（v2 → v3）：
  - 27 数值类：各 8 张 = 7 张教学图(v2 harvest 抽取，标签肉眼核验) + 1 张 real_tiles 锚点
  - 7 字牌类：各 8 张 = 1 张 real_tiles × 8 复制（保证 validation_split 不切空）
    ⚠️ 字牌本质是"每类 1 张真样本 + 7 复制"，模型会显著过拟合，仅作为
    structural.py 兜底之外的"模型端覆盖"，字牌识别主力仍交给 structural.py。

honor 复制策略权衡：
  - 不复制 → validation_split=0.25 会把每类 1 张全切到 train 或 val 一侧，val 永远 0
  - 复制 8 份 → 每类都有 train/val 样本，validation 至少可计算（虽只是同一张的 2 个噪声副本）
  - 选后者。

接入策略（与 v2 一致）：
  - 部署件仍为 float16 TFLite（Keras val 1:1 保留 + 与 tflite_classifier float32 契约一致）
  - engine.py 优先 tflite_classifier，conf < 阈值时 fallback 到 structural.py
  - 7 字牌类靠 structural.py 字形匹配兜底（已实测稳健，glyph 库多字体覆盖）
"""
import os
import sys
import json
import shutil
import numpy as np
import tensorflow as tf
from tensorflow.keras.applications import MobileNetV3Small
from tensorflow.keras import layers, Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(SCRIPT_DIR, "train_data", "processed", "face_pretrain")
MODEL_DIR = os.path.join(SCRIPT_DIR, "train_data", "model")
# Best-effort deploy target (Chaquopy asset dir). Copy failure is non-fatal.
DEPLOY_DIR = os.path.join(
    SCRIPT_DIR, "..", "android", "app", "src", "main", "python", "tflite_assets"
)
os.makedirs(MODEL_DIR, exist_ok=True)

IMG_SIZE = 48
BATCH = 16  # 272 张 / 34 类 数据极小，缩 batch 让每 epoch 步数多、val_loss 噪声小
# 本轮基线：34 类（1m-9s 数值 27 + 1z-7z 字牌 7）。
# 字牌类每类 8 张全为同一张原图复制（见脚本顶部注释），所以模型在字牌上的 val
# 准确率上限取决于「复制间增广变体能否与原图分入同类」——若不能，val 会显示
# 字牌极差，属预期。production 字牌识别仍交给 structural.py 字形匹配兜底。
NUM_CLASSES = 34
SEED = 42
tf.random.set_seed(SEED)
np.random.seed(SEED)


def build_data_generators():
    train_gen = ImageDataGenerator(
        rescale=1.0 / 255,
        rotation_range=12,
        width_shift_range=0.10,
        height_shift_range=0.10,
        zoom_range=0.12,
        brightness_range=[0.65, 1.35],
        shear_range=0.06,
        fill_mode="nearest",
        validation_split=0.25,
    )
    val_gen = ImageDataGenerator(
        rescale=1.0 / 255,
        validation_split=0.25,
    )
    common = dict(
        directory=PROCESSED_DIR,
        target_size=(IMG_SIZE, IMG_SIZE),
        batch_size=BATCH,
        class_mode="categorical",
        color_mode="grayscale",  # 1 通道：与 tflite_classifier 契约一致
        seed=SEED,
    )
    train = train_gen.flow_from_directory(subset="training", shuffle=True, **common)
    val = val_gen.flow_from_directory(subset="validation", shuffle=False, **common)
    return train, val


def build_model():
    """ImageNet-pretrained MobileNetV3Small with a grayscale->3ch stem.

    Input: (48,48,1) float in [0,1].
    The stem (fixed 1x1 Conv2D, all-ones kernel) copies the single channel to 3
    identical channels; Rescaling(2,-1) maps [0,1]->[-1,1] for the pretrained BN.
    """
    inputs = layers.Input(shape=(IMG_SIZE, IMG_SIZE, 1), name="gray_input")
    # Fixed channel-copy stem (TFLite Conv2D builtin, quantizable).
    stem = layers.Conv2D(3, 1, padding="same", use_bias=False,
                         trainable=False, name="gray_to_rgb")
    x = stem(inputs)
    stem.set_weights([np.ones((1, 1, 1, 3), dtype=np.float32)])
    # Match ImageNet preprocessing distribution: [0,1] -> [-1,1].
    x = layers.Rescaling(scale=2.0, offset=-1.0, name="scale_to_imagenet")(x)
    # ImageNet 预训练权重只提供 alpha=0.75/1.0（无 0.5）。选 0.75 兼顾
    # 精度与体积：int8 后约 2.5MB，对 173MB APK 可忽略，换来对小样本的强迁移。
    base = MobileNetV3Small(
        input_shape=(IMG_SIZE, IMG_SIZE, 3),
        alpha=0.75,
        include_top=False,
        weights="imagenet",
    )
    x = base(x)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.30)(x)
    out = layers.Dense(NUM_CLASSES, activation="softmax", name="tile_head")(x)
    model = Model(inputs, out, name="tile_classifier_v1")
    return model


def representative_dataset(gen, n=200):
    """int8 全量化校准样本。注意：必须提供与推理一致的 float [0,1] 输入，
    配合 inference_input_type=uint8（tflite_classifier 喂入 uint8[0,255]）。"""
    count = 0
    for x, _ in gen:
        yield [x.astype(np.float32)]
        count += len(x)
        if count >= n:
            break
    gen.reset()


def export_tflite_int8(model, train_gen):
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = lambda: representative_dataset(train_gen, n=200)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.uint8
    converter.inference_output_type = tf.float32  # 输出 float 便于取概率
    tflite_model = converter.convert()
    out = os.path.join(MODEL_DIR, "tile_classifier_int8.tflite")  # 仅对比，不部署
    with open(out, "wb") as f:
        f.write(tflite_model)
    return out


def export_tflite_float16(model):
    """float16：权重以 16-bit 存储，CPU 上以 float32 执行（输入/输出对外是 float32）。
    相比 int8：小数据下不崩精度，且 float32 I/O 契约与 tflite_classifier.py 一致。"""
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.target_spec.supported_types = [tf.float16]
    tflite_model = converter.convert()
    out = os.path.join(MODEL_DIR, "tile_classifier.tflite")  # 部署件
    with open(out, "wb") as f:
        f.write(tflite_model)
    return out


def evaluate_tflite(tflite_path, val_gen):
    interp = tf.lite.Interpreter(model_path=tflite_path)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    correct = total = 0
    for x_batch, y_batch in val_gen:
        for j in range(x_batch.shape[0]):
            if total >= val_gen.samples:
                break
            x1 = x_batch[j:j + 1]
            if inp["dtype"] == np.uint8:
                x_int = (x1 * 255).astype(np.uint8)
            else:
                x_int = x1.astype(np.float32)
            interp.set_tensor(inp["index"], x_int)
            interp.invoke()
            pred = interp.get_tensor(out["index"])[0]
            if int(np.argmax(pred)) == int(np.argmax(y_batch[j])):
                correct += 1
            total += 1
        if total >= val_gen.samples:
            break
    return correct / max(1, total), correct, total


def main():
    if not os.path.isdir(PROCESSED_DIR):
        print(f"错误：{PROCESSED_DIR} 不存在，请先跑 harvest_teaching.py")
        sys.exit(1)
    classes = sorted(d for d in os.listdir(PROCESSED_DIR)
                     if os.path.isdir(os.path.join(PROCESSED_DIR, d)))
    if len(classes) != NUM_CLASSES:
        print(f"错误：找到 {len(classes)} 类，期望 {NUM_CLASSES}")
        sys.exit(1)
    print(f"类别（{len(classes)}）: {classes}")
    with open(os.path.join(MODEL_DIR, "labels.txt"), "w", encoding="utf-8") as f:
        for c in classes:
            f.write(c + "\n")

    print("构建数据生成器...")
    train_gen, val_gen = build_data_generators()
    print(f"训练样本: {train_gen.samples}, 验证样本: {val_gen.samples}")

    print("构建模型（ImageNet 预训练骨干）...")
    model = build_model()
    model.summary(print_fn=lambda s: print("  " + s))

    # ---- Stage 1: 冻结骨干，只训头 ----
    model.get_layer("gray_to_rgb").trainable = False
    model.layers[-1].trainable = True  # Dense head
    # base is the MobileNetV3Small; freeze it entirely for stage 1
    for layer in model.layers:
        if isinstance(layer, tf.keras.Model):  # the functional base
            layer.trainable = False
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    cb = [
        tf.keras.callbacks.ReduceLROnPlateau(factor=0.5, patience=5, min_lr=1e-6),
        tf.keras.callbacks.EarlyStopping(patience=12, restore_best_weights=True,
                                         monitor="val_accuracy"),
    ]
    print("=== Stage 1: 冻结骨干训头 (25 epoch) ===")
    model.fit(train_gen, validation_data=val_gen, epochs=25,
              callbacks=cb, verbose=1)

    # ---- Stage 2: 解冻末端，微调整网 ----
    for layer in model.layers:
        if isinstance(layer, tf.keras.Model):
            layer.trainable = True
            # 数据极小（272 张）只微调末端 ~15 层，避免灾难性遗忘
            for ln in layer.layers[:-15]:
                ln.trainable = False
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-5),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    print("=== Stage 2: 解冻末端微调 (12 epoch, lr=1e-5) ===")
    model.fit(train_gen, validation_data=val_gen, epochs=12,
              callbacks=cb, verbose=1)

    val_loss, val_acc = model.evaluate(val_gen, verbose=0)
    print(f"Keras 最终验证准确率: {val_acc:.4f}")

    keras_path = os.path.join(MODEL_DIR, "tile_classifier.keras")
    model.save(keras_path)
    print(f"Keras 模型: {keras_path}")

    # ---- 双量化导出对比 + 部署 float16 ----
    # 干净校准集（无增强）用于 int8 参考对比。
    calib_gen = ImageDataGenerator(rescale=1.0 / 255, validation_split=0.25).flow_from_directory(
        subset="training", shuffle=False, directory=PROCESSED_DIR,
        target_size=(IMG_SIZE, IMG_SIZE), batch_size=BATCH,
        class_mode="categorical", color_mode="grayscale", seed=SEED)
    int8_path = export_tflite_int8(model, calib_gen)
    int8_size = os.path.getsize(int8_path) / 1024
    f16_path = export_tflite_float16(model)
    f16_size = os.path.getsize(f16_path) / 1024
    print(f"int8    模型(仅对比): {int8_path} ({int8_size:.1f} KB)")
    print(f"float16 模型(部署)  : {f16_path} ({f16_size:.1f} KB)")

    val_gen.reset()
    int8_val, c1, t1 = evaluate_tflite(int8_path, val_gen)
    val_gen.reset()
    f16_val, c2, t2 = evaluate_tflite(f16_path, val_gen)

    # 全量评估（参考上限，避免验证集过小导致指标噪声）：用全部 189 张、
    # 不分折、顺序读取，给出模型在已见数据上的拟合能力。
    full_gen = ImageDataGenerator(rescale=1.0 / 255).flow_from_directory(
        directory=PROCESSED_DIR, target_size=(IMG_SIZE, IMG_SIZE),
        batch_size=BATCH, class_mode="categorical", color_mode="grayscale",
        shuffle=False, seed=SEED)
    full_gen.reset()
    int8_full, fc1, ft1 = evaluate_tflite(int8_path, full_gen)
    full_gen.reset()
    f16_full, fc2, ft2 = evaluate_tflite(f16_path, full_gen)
    print(f"int8    val={int8_val:.4f} ({c1}/{t1})  full={int8_full:.4f} ({fc1}/{ft1})")
    print(f"float16 val={f16_val:.4f} ({c2}/{t2})  full={f16_full:.4f} ({fc2}/{ft2})")
    print("假设验证: float16 保留精度=" + str(f16_val >= val_acc - 0.05) +
          " | int8 崩溃=" + str(int8_val < 0.10))

    # ---- 部署拷贝 float16（best-effort，input float32 与 tflite_classifier.py 一致）----
    deploy_path = os.path.join(DEPLOY_DIR, "tile_classifier.tflite")
    try:
        os.makedirs(DEPLOY_DIR, exist_ok=True)
        shutil.copyfile(f16_path, deploy_path)
        label_src = os.path.join(MODEL_DIR, "labels.txt")
        label_dst = os.path.join(DEPLOY_DIR, "labels.txt")
        shutil.copyfile(label_src, label_dst)
        print(f"已部署 float16 到: {deploy_path}")
    except Exception as e:
        print(f"部署拷贝跳过（非致命）: {e!r}")

    summary = {
        "version": "v3-34classes-float16",
        "keras_val_acc": float(val_acc),
        "int8_val_acc": float(int8_val),
        "int8_full_acc": float(int8_full),
        "float16_val_acc": float(f16_val),
        "float16_full_acc": float(f16_full),
        "deployed_variant": "float16",
        "int8_size_kb": float(int8_size),
        "float16_size_kb": float(f16_size),
        "backbone": "MobileNetV3Small(alpha=0.75, imagenet)",
        "input_shape": [1, IMG_SIZE, IMG_SIZE, 1],
        "input_dtype": "float32 (匹配 tflite_classifier.py，修掉 int8 的 uint8 错配)",
        "output_dtype": "float32",
        "num_classes": NUM_CLASSES,
    }
    with open(os.path.join(MODEL_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
