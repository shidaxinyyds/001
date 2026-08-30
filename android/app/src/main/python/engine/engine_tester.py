import os
import socket

import cv2

from dirs import SCREENSHOTS_DIR
from utils.image import scale, show
from .engine_result import EngineResult
from .engine import Engine


HOST = '127.0.0.1'
PORT = 55555

class EngineTester:
    def test(self):
        path = os.path.join(SCREENSHOTS_DIR, os.listdir(SCREENSHOTS_DIR)[0])
        img = cv2.imread(path)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        img = scale(img, 0.99)

        engine = Engine()
        result = engine.process(img)
        b = result.dumps()

        b = str(len(b)).zfill(8).encode() + b

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect((HOST, PORT))
            s.sendall(b)

