from __future__ import annotations
import traceback
from typing import Dict, List, Optional

import json
import os
import time

import cv2
import numpy as np

from .engine_result import EngineResult
from recognition.stage import DetectionResult
from recognition.structural import StructuralDetector, MIN_CONF
from utils.stubs import CVImage
from trainer.trainer import Trainer
from trainer.objects.tile_collection import TileCollection

# 一局中的合法手牌张数：13 = 待摸牌，14 = 刚摸到牌
VALID_HAND_SIZES = (13, 14)

# 预览图最大宽度。原实现每帧都把全屏截图做 PNG 编码（100~300ms），
# 而 Dart 端并没有使用这张图，纯属浪费，是识别卡顿的主因之一。
PREVIEW_MAX_WIDTH = 240


def get_mpsz(detection: DetectionResult) -> str:
    tiles = sorted(detection, key=lambda x: x[0][0])
    # Ignore tiles that we are unable to detect
    return ''.join(tile[1] for tile in tiles if tile[1] is not None)


def _to_uint8_buffer(image_data) -> np.ndarray:
    """把 Java 传来的图像字节转成 cv2.imdecode 需要的 uint8 一维数组。

    Chaquopy 可能给出 bytes / Java byte[] 序列 / numpy 数组，这里都兼容。
    原实现写的是 np.array(image_data)：当传入 bytes 时得到的是「0 维」数组，
    cv2.imdecode 会直接失败，是识别链路上的一处隐藏断点。
    """
    if isinstance(image_data, np.ndarray):
        return image_data.astype(np.uint8, copy=False)
    if isinstance(image_data, (bytes, bytearray, memoryview)):
        return np.frombuffer(bytes(image_data), dtype=np.uint8)
    return np.asarray(list(image_data), dtype=np.uint8)


def _make_preview(image: CVImage) -> CVImage:
    """生成一张很小的预览图（不再编码全屏截图）。"""
    try:
        h, w = image.shape[:2]
        if w > PREVIEW_MAX_WIDTH:
            new_h = max(1, int(h * PREVIEW_MAX_WIDTH / w))
            return cv2.resize(image, (PREVIEW_MAX_WIDTH, new_h), interpolation=cv2.INTER_AREA)
        return image
    except Exception:
        traceback.print_exc()
        return np.zeros((1, 1, 3), dtype=np.uint8)


class Engine:
    def __init__(self):
        self.trainer: Optional[Trainer] = None
        # 结构识别器自带字形库，构建一次即可（无需每帧读模板图）。
        self._detector: Optional[StructuralDetector] = None
        # 推荐打法的缓存（按手牌内容），避免每帧重算 34 次向听 + 进张
        self._advice_key: Optional[str] = None
        self._advice: List[Dict] = []

    def start(self):
        pass

    def get_detector(self) -> Optional[StructuralDetector]:
        if self._detector is not None:
            return self._detector
        # 结构识别器自带字形库，无需外部模板图片，构建一次即可。
        self._detector = StructuralDetector()
        return self._detector

    def update_trainer(self, hand: TileCollection) -> Optional[str]:
        """根据最新一手牌更新训练器，返回对上一手的中文点评。"""
        # 只有 13/14 张才是合法手牌。其它张数说明这一帧识别不完整，
        # 直接跳过，避免拿脏数据去点评（原实现在这里 assert，会中断整帧）。
        if len(hand) not in VALID_HAND_SIZES:
            print(f"Hand length {len(hand)} is not valid, skipping")
            return None

        if self.trainer is None:
            print(f"Initial hand: {hand}")
            self.trainer = Trainer(hand)
            return None

        prev_hand = self.trainer.hand
        if hand == prev_hand:
            return None

        diff = hand.get_difference(prev_hand)
        delta = sum(abs(x) for x in diff.values())

        if delta == 0:
            return None
        if delta > 1:
            print(f"Large change detected, reloading hand: {diff}")
            self.trainer = Trainer(hand)
            return None

        tile, change = list(diff.items())[0]

        if len(hand) == 13 and len(prev_hand) == 14 and change == -1:
            print(f"Discard detected: {tile}")
            return self.trainer.discard(tile)

        if len(hand) == 14 and len(prev_hand) == 13 and change == 1:
            print(f"Draw detected: {tile}")
            return self.trainer.draw(tile)

        # 张数变化不符合"摸牌/打牌"，多半是中途识别跳变，整手重建
        print(f"Unexpected transition {len(prev_hand)} -> {len(hand)}, reloading hand")
        self.trainer = Trainer(hand)
        return None

    def build_advice(self, hand: TileCollection):
        """返回 (向听数, 推荐打法列表)。

        向听数每帧都算（单次开销很小），保证界面上一直有反馈；
        推荐打法要跑 34 次向听 + 进张，改为按手牌内容缓存，只有手牌变了才重算。
        """
        if self.trainer is None:
            return None, []

        shanten = self.trainer.get_shanten()

        key = f"{hand}|{shanten}"
        if key == self._advice_key:
            return shanten, self._advice

        advice: List[Dict] = []
        try:
            if len(hand) == 14:
                raw = self.trainer.calculate_discards()
                advice = [
                    {"tile": str(t), "ukeire": int(u)}
                    for t, u in sorted(raw.items(), key=lambda kv: -kv[1])
                ][:6]
        except Exception:
            traceback.print_exc()

        self._advice_key = key
        self._advice = advice
        return shanten, advice

    def process_bytes(self, image_data) -> Optional[EngineResult]:
        arr = _to_uint8_buffer(image_data)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            print("Failed to decode image")
            return None
        return self.process(image)

    def process(self, image: CVImage) -> Optional[EngineResult]:
        try:
            start_time = time.time()

            detector = self.get_detector()
            if detector is None:
                print("No templates available")
                return None

            stage = detector.detect(image)
            # 低置信过滤：识别精度 > 速度，宁可漏检也不把错牌喂给向听/进张逻辑。
            # last_conf 与 stage.result 下标已按 x 排序一一对应（见 StructuralDetector.detect）。
            confs = getattr(detector, "last_conf", [])
            detected = [
                (rect, label if (i >= len(confs) or confs[i] >= MIN_CONF) else None)
                for i, (rect, label) in enumerate(stage.result)
            ]
            mpsz = get_mpsz(detected)

            status = "no_tiles"
            commentary: Optional[str] = None
            shanten: Optional[int] = None
            advice: List[Dict] = []
            tile_count = 0

            if mpsz:
                hand = TileCollection.from_mpsz(mpsz)
                tile_count = len(hand)
                if tile_count in VALID_HAND_SIZES:
                    status = "ok"
                    commentary = self.update_trainer(hand)
                    shanten, advice = self.build_advice(hand)
                else:
                    # 识别到的张数不对（被遮挡或漏检），不做点评，仅回传当前可见的牌
                    status = "incomplete"

            result = {
                "hand": mpsz,
                "count": tile_count,
                "status": status,
                "shanten": shanten,
                "advice": advice,
                "commentary": commentary,
                "tiles": [
                    [int(v) for v in rect] + [label if label is not None else ""]
                    for rect, label in detected
                ],
                # 最近一帧的最高模板匹配分（无论是否过阈）。
                # 用于诊断：分数长期 <0.2 说明屏幕里没牌；0.3~0.44 说明有牌但样式与模板差异大。
                "top_score": round(float(getattr(detector, "last_top_score", 0.0)), 3),
                # MediaProjection 截到的屏幕真实像素尺寸（宽, 高）——用于确认取到了整屏
                "screen": [
                    int(getattr(detector, "last_screen", (0, 0))[0]),
                    int(getattr(detector, "last_screen", (0, 0))[1]),
                ],
                "elapsed": round(time.time() - start_time, 3),
            }

            res = EngineResult(
                image=_make_preview(image),
                result=json.dumps(result),
                stage=stage,
            )
            print(result)
            print(f"Processed in {time.time() - start_time}")
            return res
        except Exception:
            traceback.print_exc()
            return None
