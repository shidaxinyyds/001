import math
import cv2

from dirs import LABELLED_DIR
from utils.colors import RGB_GREEN
from utils.image import join_horizontal, scale
from .edge_detector import EdgeDetector
from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage




class TemplateDetector(Detector):

    def preprocess_image(self, img: CVImage) -> CVImage:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img


    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        edge_detector = EdgeDetector()
        stage = edge_detector.detect(image)
        rects = stage.result

        output: DetectionResult = []

        for rect in rects:
            x,y,w,h = rect
            tile_img = image[y:y+h, x:x+w]
            tile_img = self.preprocess_image(tile_img)
            # tile_img = self.crop_image(tile_img, self.detect_corners(tile_img))

            best_label = None
            best_score = 0
            for label, img in self.targets.items():
                target_img = self.preprocess_image(img)

                score = self.compare_and_score(tile_img, target_img)

                if score > best_score:
                    best_label = label
                    best_score = score
            output.append((rect, best_label))

        def display():
            canvas = image.copy()
            for rect, label in output:
                x,y,w,h = rect
                cv2.rectangle(canvas, (x,y), (x+w, y+h), RGB_GREEN)

                cv2.putText(canvas, label,
                    org=(int(x + 0.1 * w),int(y + 0.2 * h)),
                    fontFace=1,
                    fontScale=2,
                    color=RGB_GREEN,
                    thickness=2)
            return canvas
        return stage.next(output, display_callback=display)


    def compare_and_score(self, img1: CVImage, img2: CVImage) -> float:
        w1, h1, *_ = img1.shape
        w2, h2, *_ = img2.shape

        scale_factor = 1 / math.sqrt((w1 * h1) / (w2 * h2))
        img1 = scale(img1, scale_factor)

        try:
            res = cv2.matchTemplate(img1, img2, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            score = max_val
        except cv2.error as e:
            print(img1.shape, img2.shape, "error")
            score = 0

        return score

    # def detect_corners(self, stage):
    #     # img = stage.result
    #     img = stage
    #     display = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    #     canvas = np.zeros(img.shape, np.uint8)

    #     blur = cv2.GaussianBlur(img, (5, 5), 0)
    #     edges = cv2.Canny(blur, 150, 400)

    #     contours, hierarchy = cv2.findContours(edges.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    #     hierarchy = hierarchy[0]

    #     # for i, c in enumerate(contours):
    #     #     opened = hierarchy[i][2]<0 and hierarchy[i][3]<0
    #     #     print(opened)
    #     #     if hierarchy[i][2] < 0 and hierarchy[i][3] < 0:
    #     #         cv2.drawContours(display, contours, i, (0, 0, 255), 2)
    #     #     else:
    #     #         pass

    #     largest = max(contours, key=lambda c: cv2.contourArea(c))

    #     # cv2.drawContours(display, [largest], -1, (0, 255, 0), 2)
    #     cv2.drawContours(canvas, [largest], -1, BGR_WHITE, thickness=cv2.FILLED)


    #     # print(len(contours))
    #     # for i, c in enumerate(contours):
    #     #     r, g, b = colorsys.hsv_to_rgb((i+0.5)/len(contours), 1, 1)
    #     #     color = (b*255,g*255,r*255)
    #     #     display = cv2.drawContours(display, [c], -1, color, 2)
    #     # show(canvas)
    #     edges = cv2.Canny(canvas, 150, 400)

    #     # show(edges)


    #     # Probabilistic Line Transform
    #     linesP = cv2.HoughLinesP(edges, rho=10, theta=np.pi / 2, threshold=5, lines=None, minLineLength=30, maxLineGap=20)
    #     # Draw the lines
    #     if linesP is not None:
    #         print(len(linesP))
    #         for i in range(0, len(linesP)):
    #             l = linesP[i][0]
    #             print(l)
    #             cv2.line(display, (l[0], l[1]), (l[2], l[3]), (0,0,255), 1, cv2.LINE_AA)

    #     # Cheat here

    #     rect = cv2.boundingRect(largest)
    #     x,y,w,h = rect
    #     return rect

    #     def _():
    #         canvas = img.copy()
    #         cv2.line(canvas, (x,y), (x+w, y+h), (0,0,0))
    #         return canvas

    #     res = Stage(rect, stage, _)

    #     return res

