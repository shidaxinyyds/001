# -*- coding: utf-8 -*-
"""
Train MobileNetV3-Small tile classifier on the 34-class face-pretrain set,
export int8-quantized TFLite model ready to be dropped into the Android APK.

Input:  localtest/train_data/processed/face_pretrain/<class>/*.png
Output: localtest/train_data/model/tile_classifier.tflite (+ .keras + labels.txt)
"""
import os
import sys
import json
import numpy as np
import tensorflow as tf
from tensorflow.keras.applications import MobileNetV3Small
from tensorflow.keras import layers, Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(SCRIPT_DIR, "train_data", "processed", "face_pretrain")
MODEL_DIR = os.path.join(SCRIPT_DIR, "train_data", "model")
os.makedirs(MODEL_DIR, exist_ok=True)

IMG_SIZE = 48
BATCH = 32
EPOCHS = 60
NUM_CLASSES = 34
SEED = 42
tf.random.set_seed(SEED)
np.random.seed(SEED)


def build_data_generators():
    train_gen = ImageDataGenerator(
        rescale=1.0 / 255,
        rotation_range=10,
        width_shift_range=0.10,
        height_shift_range=0.10,
        zoom_range=0.10,
        brightness_range=[0.7, 1.3],
        shear_range=0.05,
        fill_mode="nearest",
        validation_split=0.15,
    )
    val_gen = ImageDataGenerator(
        rescale=1.0 / 255,
        validation_split=0.15,
    )
    common = dict(
        directory=PROCESSED_DIR,
        target_size=(IMG_SIZE, IMG_SIZE),
        batch_size=BATCH,
        class_mode="categorical",
        color_mode="grayscale",  # 1 通道：避开 cv2 BGR / Keras RGB 通道顺序问题，
                                 # 模型体积更小（~1/3），麻将牌面灰度已够
        seed=SEED,
    )
    train = train_gen.flow_from_directory(subset="training", shuffle=True, **common)
    val = val_gen.flow_from_directory(subset="validation", shuffle=False, **common)
    return train, val


def build_model():
    # alpha=0.5 让模型更小（< 1MB int8），34 类闭域任务够用。
    # weights=None 避免 SSL 下载 ImageNet（34 类与 ImageNet 差异大，
    # 139 张 + 强增广从零学也能收敛到 95%+）。
    base = MobileNetV3Small(
        input_shape=(IMG_SIZE, IMG_SIZE, 1),
        alpha=0.5,
        include_top=False,
        weights=None,
    )
    x = base.output
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.2)(x)
    out = layers.Dense(NUM_CLASSES, activation="softmax")(x)
    model = Model(base.input, out)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def representative_dataset(gen, n=200):
    """为 int8 全量化提供校准样本（取前 n 批）。"""
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
    out = os.path.join(MODEL_DIR, "tile_classifier.tflite")
    with open(out, "wb") as f:
        f.write(tflite_model)
    return out


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

    print("构建模型...")
    model = build_model()
    model.summary(print_fn=lambda s: print("  " + s))

    callbacks = [
        tf.keras.callbacks.ReduceLROnPlateau(factor=0.5, patience=5, min_lr=1e-6),
        tf.keras.callbacks.EarlyStopping(patience=12, restore_best_weights=True,
                                          monitor="val_accuracy"),
    ]
    print(f"开始训练 {EPOCHS} epoch...")
    model.fit(train_gen, validation_data=val_gen,
              epochs=EPOCHS, callbacks=callbacks, verbose=1)

    val_loss, val_acc = model.evaluate(val_gen, verbose=0)
    print(f"最终验证准确率: {val_acc:.4f}")

    keras_path = os.path.join(MODEL_DIR, "tile_classifier.keras")
    model.save(keras_path)
    print(f"Keras 模型: {keras_path}")

    train_gen.reset()
    tflite_path = export_tflite_int8(model, train_gen)
    size_kb = os.path.getsize(tflite_path) / 1024
    print(f"TFLite 模型: {tflite_path} ({size_kb:.1f} KB)")

    interp = tf.lite.Interpreter(model_path=tflite_path)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    print(f"  输入: {inp['shape']} dtype={inp['dtype']}")
    print(f"  输出: {out['shape']} dtype={out['dtype']}")

    val_gen.reset()
    correct, total = 0, 0
    for x_batch, y_batch in val_gen:
        for j in range(x_batch.shape[0]):
            if total >= val_gen.samples:
                break
            x1 = x_batch[j:j + 1]  # (1, 48, 48, 3) — TFLite 期望 batch 1
            if inp["dtype"] == np.uint8:
                x_int = (x1 * 255).astype(np.uint8)
            else:
                x_int = x1.astype(np.float32)
            interp.set_tensor(inp["index"], x_int)
            interp.invoke()
            pred = interp.get_tensor(out["index"])[0]
            pred_lab = int(np.argmax(pred))
            true_lab = int(np.argmax(y_batch[j]))
            if pred_lab == true_lab:
                correct += 1
            total += 1
        if total >= val_gen.samples:
            break
    tflite_acc = correct / max(1, total)
    print(f"TFLite 验证准确率: {tflite_acc:.4f} ({correct}/{total})")

    summary = {
        "keras_val_acc": float(val_acc),
        "tflite_val_acc": float(tflite_acc),
        "tflite_size_kb": float(size_kb),
        "input_shape": [int(x) for x in inp["shape"]],
        "input_dtype": str(inp["dtype"]),
        "output_dtype": str(out["dtype"]),
        "num_classes": NUM_CLASSES,
    }
    with open(os.path.join(MODEL_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
