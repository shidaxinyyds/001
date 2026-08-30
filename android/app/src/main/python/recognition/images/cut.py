import os
from typing import List

from PIL import Image

SCREENSHOTS_DIR = './screenshots'
CROPPED_DIR = './cropped'

W = 115
H = 150

X = 415
Y = 925

def crop_all():
    images = []

    y = Y

    for basename in os.listdir(SCREENSHOTS_DIR):
        path = f"{SCREENSHOTS_DIR}/{basename}"
        im = Image.open(path)

        x = X
        for _ in range(13):
            cropped = im.crop((x, y, x+W, y+H))
            images.append(cropped)
            x += W
            x += 4

    return images

def join_horizontal(images: List[Image.Image]):
    MARGIN = 10

    width = sum((im.size[0] for im in images))
    height = max((im.size[1] for im in images))

    pos = 0
    dst = Image.new('RGB', (width, height), (255,0,0))
    for i in range(len(images)):
        dst.paste(images[i], (pos, 0))
        pos += images[i].size[0] + MARGIN

    return dst

# new = join_horizontal(crop_all())
# new.save("file.png")

def ave_pixel_hash(img):
    img = img.convert("L")
    pixel_data = list(img.getdata())
    avg_pixel = sum(pixel_data)/len(pixel_data)
    return int(avg_pixel * 100)

for im in crop_all():
    basename = f"{ave_pixel_hash(im)}.png"
    im.save(f"{CROPPED_DIR}/{basename}")

