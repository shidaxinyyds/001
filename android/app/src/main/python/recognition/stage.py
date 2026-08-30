from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple, TypeVar, Generic, Any

from utils.stubs import CVImage, Rect

T = TypeVar("T")
U = TypeVar("U")

@dataclass
class Stage(Generic[T]):
    result: T
    image: CVImage
    prev: Optional[Stage[Any]] = None

    _display: Optional[CVImage] = None
    display_callback: Optional[Callable[..., CVImage]] = None

    @classmethod
    def first_image(cls, image: CVImage):
        return Stage(result=None, image=image)

    def next(self, result: U, display_callback: Optional[Callable[..., CVImage]], image: Optional[CVImage] = None):
        if image is None:
            image = self.image
        return Stage(result=result, prev=self, display_callback=display_callback, image=image)

    @property
    def display(self) -> CVImage:
        if self._display is not None:
            return self._display
        if self.display_callback is None:
            return self.image
        return self.display_callback()

    @display.setter
    def display(self, image: CVImage):
        self._display = image

DetectionResult = List[Tuple[Rect, Optional[str]]]
