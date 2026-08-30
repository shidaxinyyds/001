from __future__ import annotations
import traceback
from typing import Dict, Optional

import json
import os
import cv2
import time

import numpy as np

from utils.image import save_image, show
from dirs import LABELLED_DIR

from .engine_result import EngineResult
from recognition.stage import DetectionResult
from recognition.template_detector import TemplateDetector
from utils.stubs import CVImage
from trainer.trainer import Trainer
from trainer.objects.tile_collection import TileCollection

def get_mpsz(detection: DetectionResult):
    tiles = sorted(detection, key=lambda x: x[0][0])
    # Ignore tiles that we are unable to detect
    return ''.join(tile[1] for tile in tiles if tile[1] is not None)

class Engine:
    def __init__(self):
        self.trainer = None

    def start(self):
        pass

    def load_target_images(self):
        labels = [basename.split('.')[0] for basename in os.listdir(LABELLED_DIR)]

        output: Dict[str, CVImage] = {}
        for label in labels:
            path = os.path.join(LABELLED_DIR, f"{label}.png")
            image: CVImage = cv2.imread(path)
            image: CVImage = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            output[label] = image

        return output


    def process_bytes(self, image_data: bytes) -> EngineResult:
        arr = np.array(image_data)
        image: CVImage = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return self.process(image)

    def update_trainer(self, hand: TileCollection) -> Optional[str]:
        if len(hand) not in (13, 14):
            print(f"Odd hand length of {len(hand)}")
            return None

        if self.trainer is None:
            print(f"First new hand: {hand}")
            self.trainer = Trainer(hand)
            return None

        prev_hand = self.trainer.hand
        if hand == prev_hand:
            print(f"Hand did not change: {hand}")
            return None

        diff = hand.get_difference(prev_hand)
        delta = sum(abs(x) for x in diff.values())

        if delta > 1:
            print(f"Change is large, hand reloaded: {diff}")
            self.trainer = Trainer(hand)
            return None

        if delta != 1:
            assert False

        tile, change = list(diff.items())[0]
        if len(hand) == 13 and len(prev_hand) == 14:
            assert change == -1
            print(f"Discard detected: {tile}")
            msg = self.trainer.discard(tile)
            assert(len(self.trainer.hand)) == 13
            return msg

        if len(hand) == 14 and len(prev_hand) == 13:
            assert change == 1
            print(f"Draw detected: {tile}")
            msg = self.trainer.draw(tile)
            assert(len(self.trainer.hand)) == 14
            return msg

        print(f"Unknown action {len(hand)} -> {len(prev_hand)} change:{change}")
        return None


    def process(self, image: CVImage) -> Optional[EngineResult]:
        try:
            start_time = time.time()

            target_set = self.load_target_images()

            detector = TemplateDetector(target_set)
            stage = detector.detect(image)
            mpsz = get_mpsz(stage.result)

            if mpsz == "":
                print("No tiles detected")
                return

            hand = TileCollection.from_mpsz(mpsz)

            commentary = self.update_trainer(hand)

            shanten = None

            analysis = None
            if commentary is not None:
                assert self.trainer is not None
                shanten = self.trainer.get_shanten()
                analysis = {
                    "shanten": shanten,
                    "commentary": commentary,
                }


            result = {
                "hand": mpsz,
                "tiles": stage.result,
                "analysis": analysis,
            }

            image = stage.display

            json_result = json.dumps(result)
            res = EngineResult(image=image, result=json_result, stage=stage)
            print(result)
            print(f"Processed in {time.time() - start_time}")
            return res
        except Exception as e:
            traceback.print_exc()
