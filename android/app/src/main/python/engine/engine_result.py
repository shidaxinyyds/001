from __future__ import annotations

from dataclasses import dataclass
import pickle
from typing import Any

import cv2
from recognition.stage import Stage

from utils.stubs import CVImage


@dataclass
class EngineResult:
    image: CVImage
    result: str
    stage: Stage[Any]

    def to_bytes(self) -> bytes:
        image_bytes: bytes = cv2.imencode('.png', self.image)[1].tobytes()
        return (self.result.encode() +
            ("\n").encode() +
        image_bytes)

    def dumps(self) -> bytes:
        stage = self.stage
        while stage is not None:
            if stage.display_callback is not None:
                stage.display = stage.display_callback()
                stage.display_callback = None
            stage = stage.prev
        return pickle.dumps(self)

    @classmethod
    def loads(cls, b: bytes) -> EngineResult:
        return pickle.loads(b)
