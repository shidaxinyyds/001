import math
from typing import List, Tuple
import numpy as np
import cv2

from .stage import Stage
from utils.colors import MONO_WHITE, RGB_RED
from utils.stubs import CVImage, Contour, Contours, RRects, Rect, Rects



class EdgeDetector:
    def detect(self, image: CVImage) -> Stage[Rects]:
        stage = Stage.first_image(image)
        # show(stage.display)
        stage = self.find_all_contours(stage)
        # show(stage.display)
        stage = self.filter_contours_by_shape(stage)
        # show(stage.display)
        stage = self.filter_contours_by_placement(stage)
        # show(stage.display)

        rects: List[Rect] = []
        contours = stage.result
        for contour in contours:
            rect: Rect = cv2.boundingRect(contour)
            x,y,w,h = rect
            # expand?
            # rect = (x-10, y-10, w+20, h+20)
            rects.append(rect)

        stage.result = rects
        return stage


    def find_all_contours(self, stage: Stage[CVImage]):
        image = stage.image
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 150, 400)

        contours: Tuple[Contour, ...]
        contours, _ = cv2.findContours(edges.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        def display():
            canvas = image.copy()
            return cv2.drawContours(canvas, contours, -1, RGB_RED, thickness=2)
        return stage.next(contours, display)

    def filter_contours_by_shape(self, stage: Stage[Contours]) -> Stage[Tuple[Contours, RRects]]:
        contours = stage.result
        filtered = []
        for contour in contours:
            area: int = cv2.contourArea(contour)
            if area < 2000:
                continue
            filtered.append(contour)
        contours = filtered

        rrects = []
        filtered = []
        for contour in contours:
            rrect = cv2.minAreaRect(contour)
            _, __, angle = rrect
            if not (80 <= angle <= 100 or -10 <= angle <= 10):
                continue
            filtered.append(contour)
            rrects.append(rrect)
        contours = filtered

        assert len(contours) == len(rrects)
        def display():
            canvas = stage.image.copy()
            return cv2.drawContours(canvas, contours, -1, RGB_RED, thickness=2)

        return stage.next((contours, rrects), display_callback=display)


    def filter_contours_by_placement(self, stage: Stage[Tuple[Contours, RRects]]) -> Stage[Contours]:
        contours, rrects = stage.result

        w, h, *_ = stage.image.shape
        detection_canvas = np.zeros((w,h), np.uint8)
        # canvas = image
        tile_points = []

        def distanceCalculate(p1, p2):
            """p1 and p2 in format (x1,y1) and (x2,y2) tuples"""
            dis = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
            return dis

        if len(contours) == 0:
            return stage.next([], None)


        for contour, rrect in zip(contours, rrects):
            rrect: RRects = cv2.minAreaRect(contour)

            A, B, C, D = cv2.boxPoints(rrect)
            if distanceCalculate(A, B) > distanceCalculate(B, C):
                points = [
                    np.floor_divide(A+B, 2).astype(int),
                    np.floor_divide(C+D, 2).astype(int)
                ]
            else:
                points = [
                    np.floor_divide(B+C, 2).astype(int),
                    np.floor_divide(A+D, 2).astype(int)
                ]
            points = [tuple(p) for p in points]
            tile_points.append(points)

            for point in points:
                cv2.circle(detection_canvas, point, 5, MONO_WHITE, thickness=cv2.FILLED)

        linesP = cv2.HoughLinesP(detection_canvas, rho=10, theta=np.pi / 2, threshold=50, lines=None, minLineLength=1000, maxLineGap=200)
        if linesP is None:
            return stage.next([], None)

        l = max(linesP, key=lambda l: distanceCalculate((l[0][0], l[0][1]), (l[0][2], l[0][3])))

        x1, y1, x2, y2 = l[0].astype(int)

        a = y2 - y1
        b = x1 - x2
        c = x2 * y1 - x1 * y2


        def d(x: int, y: int) -> float:
            return abs(a * x + b* y + c) / math.sqrt(a**2 + b**2)

        filtered = []

        centers = []
        for (p1, p2) in tile_points:
            center = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
            centers.append(center)

        MARGIN = 10
        for contour, center in zip(contours, centers):
            if d(*center) > MARGIN:
                continue
            filtered.append(contour)

        contours = filtered


        def display():
            canvas = stage.image.copy()
            for center in centers:
                cv2.circle(canvas, center, 5, RGB_RED, thickness=cv2.FILLED)
            canvas = cv2.drawContours(canvas, contours, -1, RGB_RED, thickness=2)
            return canvas
        return stage.next(contours, display)

