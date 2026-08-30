import glob
import os
import random
import cv2
from PIL import Image
from typing import Tuple, List

from dirs import LABELLED_DIR
from .detector import Detector
from .utils import convert_pil_to_cv, join_horizontal, join_vertical, scale, show


class MatcherTester:
    def __init__(self, matcher: Detector) -> None:
        self.matcher = matcher

    @staticmethod
    def get_random_image(pattern: str, scale_factor: int = 1):
        files = glob.glob(os.path.join(LABELLED_DIR, f'{pattern}.*'))
        path = random.choice(files)

        img = Image.open(path)

        # scramble = random.choice([True, False])
        img = scale(img, scale_factor)

        image = convert_pil_to_cv(img)
        return image

    def randomly_test(self, n):
        pass
            # if random.random() > P_SAME:
            #     image1 = Matcher.get_random_image()
            #     image2 = Matcher.get_random_image()
            # else:
            #     image1 = Matcher.get_random_image()
            #     image2 = image1.copy()


    def test_and_show(self, test_input: List[Tuple[str, str]], show_size: Tuple[int, int]):

        to_show = []
        for file_pattern1, file_pattern2 in test_input:
            print(file_pattern1, file_pattern2)
            image1 = MatcherTester.get_random_image(file_pattern1, scale_factor=1.2)
            image2 = MatcherTester.get_random_image(file_pattern2, scale_factor=1)

            result = self.matcher.compare_and_score(image1, image2)

            display = result.display

            cv2.putText(display, f"{result.score:.2f}", (80, 100), fontFace=1, fontScale=3, color=(0,0,0), thickness=5)
            to_show.append(display)

        rows, cols = show_size
        combined = join_vertical([join_horizontal(to_show[i*cols:(i+1)*cols]) for i in range(0, rows)])
        show(combined)

    def test_and_show_within_suit(self, suit: str):
        assert suit in 'mpsz', f"{suit} is not a valid suit"

        N = 81
        show_size = (9, 9)

        test_input = []
        for k in range(N):
            i = k // 9 + 1
            j = k % 9 + 1

            file_pattern1 = f"{i}{suit}"
            file_pattern2 = f"{j}{suit}"
            test_input.append((file_pattern1, file_pattern2))

        self.test_and_show(test_input, show_size)



    def test_random(self, n: int):
        test_input = []
        for _ in range(n):
            file_pattern1 = f"*"
            file_pattern2 = f"*"
            test_input.append((file_pattern1, file_pattern2))

        show_size = (n, 1)
        self.test_and_show(test_input, show_size)






    def test_all(self):
        paths = [os.path.join(LABELLED_DIR, file) for file in os.listdir(LABELLED_DIR)]

        for i in range(len(paths)):
            for j in range(len(paths)):
                # for scale in (0.8, 0.9, 1.1, 1.2):
                for scale_factor in (1.1,):
                    image1 = Image.open(paths[i])
                    image2 = Image.open(paths[j])

                    image1 = convert_pil_to_cv(scale(image1, 1))
                    image2 = convert_pil_to_cv(scale(image2, 1))




                    result = self.matcher.compare_and_score(image1, image2)
                    score = result.result

                    THRESHOLD = 0.85

                    print(i, j, score)

                    if i == j and score < THRESHOLD:
                        print(f"i eq j but {score}")
                    if i != j and score > THRESHOLD:
                        print(f"i neq j but {score}")


