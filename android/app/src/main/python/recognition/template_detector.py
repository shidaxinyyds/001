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

    # 最低匹配置信度。模板匹配总会对噪声给出一个"最高分"，
    # 原实现不加阈值，任何噪点都会被硬认成某张牌，导致手牌乱七八糟、建议全错。
    MIN_SCORE = 0.45

    def __init__(self, targets) -> None:
        super().__init__(targets)
        # 灰度模板只算一次。原实现对「每个牌位 × 每个模板」都做一次 cvtColor，
        # 14 张牌 × 34 个模板 = 476 次/帧，是主要的性能瓶颈。
        self._gray_targets = None

    def preprocess_image(self, img: CVImage) -> CVImage:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img

    def gray_targets(self):
        if self._gray_targets is None:
            self._gray_targets = {
                label: self.preprocess_image(img)
                for label, img in self.targets.items()
            }
        return self._gray_targets

    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        edge_detector = EdgeDetector()
        stage = edge_detector.detect(image)
        rects = stage.result

        output: DetectionResult = []

        for rect in rects:
            x,y,w,h = rect
            if w <= 0 or h <= 0:
                continue
            tile_img = image[y:y+h, x:x+w]
            tile_img = self.preprocess_image(tile_img)
            # tile_img = self.crop_image(tile_img, self.detect_corners(tile_img))

            best_label = None
            best_score = 0.0
            for label, target_img in self.gray_targets().items():
                score = self.compare_and_score(tile_img, target_img)

                if score > best_score:
                    best_label = label
                    best_score = score

            # 置信度太低就当作"认不出来"，宁可少认一张也不要认错
            if best_score < self.MIN_SCORE:
                best_label = None

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
        h1, w1 = img1.shape[:2]
        h2, w2 = img2.shape[:2]
        if w1 <= 0 or h1 <= 0 or w2 <= 0 or h2 <= 0:
            return 0.0

        # 把待匹配图缩放到与模板相同的面积，让匹配不受牌面大小影响
        area_ratio = (w1 * h1) / float(w2 * h2)
        if area_ratio <= 0:
            return 0.0
        img1 = scale(img1, 1 / math.sqrt(area_ratio))
        h1, w1 = img1.shape[:2]

        # 模板必须能完整放进待匹配图，否则 matchTemplate 会抛异常。
        # 原实现直接吞掉异常返回 0，等于静默丢弃这一帧的匹配结果。
        if w1 < w2 or h1 < h2:
            factor = min(w1 / float(w2), h1 / float(h2)) * 0.98
            img2 = cv2.resize(
                img2,
                (max(1, int(w2 * factor)), max(1, int(h2 * factor))),
                interpolation=cv2.INTER_AREA,
            )

        try:
            res = cv2.matchTemplate(img1, img2, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            return float(max_val)
        except cv2.error:
            return 0.0

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

