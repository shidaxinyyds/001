from enum import Enum, auto
import base64
import io
import socket
from typing import Callable
from engine import EngineResult
from utils.image import show
from PIL import Image

HOST = '0.0.0.0'
PORT = 55555

def read_base64_data(data: str):
    base64_string = data
    image_data = base64.b64decode(base64_string)

    image = Image.open(io.BytesIO(image_data))

    image.save("image.jpg")



class ServerState(Enum):
    empty = auto()
    partial = auto()


class Server:
    def __init__(self, callback, host: str, port: int):
        self.callback: Callable = callback
        self.host = host
        self.port = port
        self.buffer = bytearray()
        self.state = ServerState.empty
        self.remaining = 0

    def start(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.listen()

            while True:
                conn, addr = sock.accept()
                print(f"Connected by {addr}")
                buf = bytearray()
                while True:
                    data = conn.recv(1024)
                    if data == b'':
                        break
                    buf.extend(data)
                print(len(buf))
                self.callback(bytes(buf)[8:])

def callback(bytes):
    print(len(bytes))
    try:
        result = EngineResult.loads(bytes)
        stage = result.stage
        stage = stage.prev.prev


        show(stage.display, block=False)
        print("Showing")

    except Exception as e:
        print(e)



def main():
    server = Server(callback, HOST, PORT)
    server.start()


if __name__ == '__main__':
    main()
