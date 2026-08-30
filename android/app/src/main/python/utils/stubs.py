import numpy as np
from typing import Tuple, Any, Iterable
from PIL import Image

Rect = Tuple[int, int, int, int]
Rects = Iterable[Rect]
PILImage = Image.Image


try:
    import numpy.typing as npt
    CVImage = npt.NDArray[np.uint8]
    Contour = npt.NDArray[np.int32]
    RRect = Tuple[Tuple[float, float], Tuple[float, float], float]
    Contours = Iterable[Contour]
    RRects = Iterable[RRect]
except ImportError:
    CVImage = Any
    Contour = Any
    RRect = Any
    Contours = Any
    RRects = Any
