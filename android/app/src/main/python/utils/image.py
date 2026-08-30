from datetime import datetime
from typing import TypeVar, Union, List
import subprocess
import cv2
import numpy as np
from PIL import Image
from utils.stubs import CVImage, PILImage, Rect

T = TypeVar("T")
def crop_image(img: CVImage, rect: Rect):
    assert isinstance(img, np.ndarray)
    x,y,w,h = rect
    img = img[y:y+h, x:x+w].copy()
    return img

def scale(img: T, factor: float) -> T:
    if isinstance(img, Image.Image):
        assert 0.1 < factor < 100
        width, height = img.size
        new_size = (int(factor * width), int(factor * height))
        return img.resize(new_size, Image.LANCZOS)

    if isinstance(img, np.ndarray):
        w, h, *_ = img.shape
        target_size = (int(h*factor), int(w*factor))
        return cv2.resize(img, target_size)

    assert False

def try_convert_cv_to_pil(img: CVImage) -> PILImage:
    if not isinstance(img, Image.Image):
        img = convert_cv_to_pil(img)
    return img

def convert_cv_to_pil(cv_image: CVImage) -> PILImage:
    return Image.fromarray(cv_image)

def convert_pil_to_cv(pil_image: PILImage) -> CVImage:
    open_cv_image = np.array(pil_image)
    if len(open_cv_image.shape) == 2:
        return open_cv_image
    # Convert RGB to BGR
    open_cv_image = open_cv_image[:, :, ::-1].copy()

    return open_cv_image

def show(image: CVImage, block: bool = True):
    img: Union[CVImage, PILImage] = image
    if not isinstance(img, Image.Image):
        img = convert_cv_to_pil(img)
    try:
        img.save("/tmp/mahjong.png")
    except OSError:
        return

    try:
        if subprocess.call(['sh', '-c', 'pgrep qimgv &>/dev/null']) != 0:
            subprocess.call(['sh', '-c', 'nohup qimgv /tmp/mahjong.png &>/dev/null &'])
        if block:
            input()
    except KeyboardInterrupt:
        raise Exception("Exited")

def join_horizontal(images: List[Image.Image]):
    images = [try_convert_cv_to_pil(im) for im in images]

    MARGIN = 1

    width = sum((im.size[0] for im in images)) + (len(images) - 1) * MARGIN
    height = max((im.size[1] for im in images))

    pos = 0
    dst = Image.new('RGB', (width, height), (0,0,0))
    for i in range(len(images)):
        dst.paste(images[i], (pos, 0))
        pos += images[i].size[0] + MARGIN

    return dst

def join_vertical(images: List[Image.Image]):
    images = [try_convert_cv_to_pil(im) for im in images]

    MARGIN = 1

    width = max((im.size[0] for im in images)) + (len(images) - 1) * MARGIN
    height = sum((im.size[1] for im in images))

    pos = 0
    dst = Image.new('RGB', (width, height), (0,0,0))
    for i in range(len(images)):
        dst.paste(images[i], (0, pos))
        pos += images[i].size[1] + MARGIN

    return dst

def save_image(image: CVImage, tag: str):
    filepath = f"/storage/emulated/0/Android/data/com.example.realtime_mahjong_trainer/files/{datetime.now()}_{tag}.png"
    try:
        image_bytes: bytes = cv2.imencode('.png', image)[1].tobytes()
        with open(filepath, 'wb') as f:
            f.write(image_bytes)
    except Exception as e:
        print(e)
