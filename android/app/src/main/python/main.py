import inspect
import os
import random
import time
import argparse

import cv2
from engine import Engine
from utils.image import show


functions = []

def add_to_parser(func):
    functions.append(func)
    return func


@add_to_parser
def process_one(path: str):
    if not os.path.exists(path):
        raise  FileNotFoundError(f"{path} does not exist")
    if os.path.isdir(path):
        path = os.path.join(path, random.choice(os.listdir(path)))

    engine = Engine()
    img = cv2.imread(path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    result = engine.process(img)
    show(result.stage.display, block=False)


@add_to_parser
def process_video(path: str):
    if not os.path.exists(path):
        raise  FileNotFoundError(f"{path} does not exist")
    if os.path.isdir(path):
        path = os.path.join(path, random.choice(os.listdir(path)))

    print(path)

    engine = Engine()

    video = cv2.VideoCapture(path)

    success = True
    count = 0
    while success:
        print(count)
        for _ in range(24):
            success, image = video.read()
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        result = engine.process(image)
        try:
            show(result.stage.display, block=False)
        except Exception as e:
            show(image, block=False)
        time.sleep(0.3)
        count += 1


# def test_engine():
#     tester = EngineTester()
#     tester.test()


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    for func in functions:
        subparser = subparsers.add_parser(func.__name__)
        for argname, argtype in inspect.signature(func).parameters.items():
            subparser.add_argument(argname)

    args = parser.parse_args()

    for func in functions:
        if args.command == func.__name__:
            params = {}
            for argname, argtype in inspect.signature(func).parameters.items():
                params[argname] = getattr(args, argname)
            func(**params)


