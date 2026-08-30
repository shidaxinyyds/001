import abc
from typing import Dict
from .stage import DetectionResult, Stage

from utils.stubs import CVImage

class Detector(abc.ABC):
    def __init__(self, targets: Dict[str, CVImage]) -> None:
        self.targets = targets

    @abc.abstractmethod
    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        pass

