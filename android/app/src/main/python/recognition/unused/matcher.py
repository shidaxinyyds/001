import math
import numpy as np
import numpy.typing as npt
import cv2
from typing import Tuple

from .utils import convert_cv_to_pil, convert_pil_to_cv, scale
from utils.stubs import CVImage



class Matcher:
    def __init__(self) -> None:
        self.sift = cv2.SIFT_create()

        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm = FLANN_INDEX_KDTREE, trees = 5)
        search_params = dict(checks = 50)
        self.flann = cv2.FlannBasedMatcher(index_params, search_params)




    @staticmethod
    def preprocess_image(img: CVImage):
        MARGIN_FRAC = 0.05
        h, w, _ = img.shape

        bounds = (slice(int(h * MARGIN_FRAC), int(h * (1-MARGIN_FRAC))),
            slice(int(w * MARGIN_FRAC), int(w * (1-MARGIN_FRAC))))
        cropped = img[bounds].copy()

        gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        threshold = cv2.adaptiveThreshold(blurred, 255,
                                          cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY,
                                          11, 2)
        return threshold

    def extract_features(self, img: CVImage) -> Tuple[Tuple[cv2.KeyPoint, ...], Tuple[npt.NDArray[np.float32]] ]:
        keypoints, descriptors = self.sift.detectAndCompute(img, None)
        return keypoints, descriptors

    def match_features(self, ft1, ft2):
        kp1, des1 = ft1
        kp2, des2 = ft2



        matches = self.flann.knnMatch(des1, des2, k=2)

        # store all the good matches as per Lowe's ratio test.
        is_good = [True] * len(matches)
        for i, (m, n) in enumerate(matches):
            if m.distance < 0.7 * n.distance:
                pass
            else:
                is_good[i] = False

        return matches, is_good


    def compare(self, image1: CVImage, image2: CVImage) -> bool:
        SCORE_THRESHOLD = 0.85
        result = self.compare_and_score(image1, image2)
        return result.score > SCORE_THRESHOLD

    def compare_and_score(self, image1: CVImage, image2: CVImage) -> MatchResult:
        stats = {}

        image1 = Matcher.preprocess_image(image1)
        image2 = Matcher.preprocess_image(image2)
        kp1, des1 = self.extract_features(image1)
        kp2, des2 = self.extract_features(image2)
        stats['image1_features'] = len(kp1)
        stats['image2_features'] = len(kp2)



        matches, is_good = self.match_features((kp1, des1), (kp2, des2))
        stats['stage1_matches'] = len(matches)
        good_matches = [match for (i, match) in enumerate(matches) if is_good[i]]
        n_good_matches = sum(is_good)
        stats['stage1_good_matches'] = n_good_matches
        pts1 = np.float32([ kp1[m[0].queryIdx].pt for m in good_matches ]).reshape(-1,1,2)
        pts2 = np.float32([ kp2[m[0].trainIdx].pt for m in good_matches ]).reshape(-1,1,2)


        def _():
            return self.draw_matches(matches, None, image1, kp1, image2, kp2)

        result = MatchResult(score=0, display_callback=_)

        if n_good_matches < 4:
            return result

        M, mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, ransacReprojThreshold=5.0)
        print(mask)
        stats['stage1_inlier_matches'] = mask.sum()
        print(stats)


        if M is None:
            return result

        det: float = np.linalg.det(M)
        assert isinstance(det, float)

        print('det', det)
        if det < 1:
            scale_factor = math.sqrt(abs(det))
        else:
            scale_factor = math.sqrt(abs(det))
        print("scalefactor", scale_factor)


        if not 0.5 < scale_factor < 2:
            return result

        image3 = convert_pil_to_cv(scale(convert_cv_to_pil(image2), 1/scale_factor))
        kp3, des3 = self.extract_features(image3)
        stats['image3_features'] = len(kp3)



        res = cv2.matchTemplate(image1,image3,cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)






        matches, is_good = self.match_features((kp1, des1), (kp3, des3))
        stats['stage2_matches'] = len(matches)

        good_matches = [match for (i, match) in enumerate(matches) if is_good[i]]
        n_good_matches = sum(is_good)
        stats['stage2_good_matches'] = n_good_matches

        print(stats)

        n_good = len(list(filter(lambda x: x, mask)))
        score = n_good / len(matches)

        def _():
            cvmask = [([1, 0] if x else [0, 0]) for x in is_good]
            return self.draw_matches(matches, cvmask, image1, kp1, image3, kp3)

        score = max_val

        return MatchResult(score=score, display_callback=_)


    def draw_matches(self, matches, mask, img1, kp1, img2, kp2):
        draw_params = dict(matchColor = (0,255,0),
                   singlePointColor = (255,0,0),
                   matchesMask = mask,
                   flags = 0)
        return cv2.drawMatchesKnn(img1, kp1, img2, kp2, matches, img2, **draw_params)



