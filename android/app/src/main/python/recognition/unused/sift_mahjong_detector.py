import cv2
from .detector import Detector


class SiftMahjongDetector(Detector):
    def __init__(self):
        self.sift = cv2.SIFT_create()
        self.features: Dict[str, Tuple[Any, Any]] = {}
        self._predetect_features()
        if len(self.features) == 0:
            raise Exception("No features processed")


    def _predetect_features(self):
        for basename in os.listdir(LABELLED_DIR):
            filepath = f"{LABELLED_DIR}/{basename}"
            label = basename.split('.')[0]


            img = cv2.imread(filepath)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            keypoints, descriptors = self.sift.detectAndCompute(img, None)
            self.features[label] = (keypoints, descriptors)


    def process(self, image: CVImage) -> MahjongDectectionResult:
        edge_detector = EdgeDetector(image)

        edge_result = edge_detector.detect()

        rects: List[Rect] = edge_result.results



        result = MahjongDectectionResult(image)

        for rect in rects:
            x,y,w,h = rect
            tile_img = image[y:y+h, x:x+w]
            k, d = self.sift.detectAndCompute(tile_img, None)

            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm = FLANN_INDEX_KDTREE, trees = 5)
            search_params = dict(checks = 50)
            flann = cv2.FlannBasedMatcher(index_params, search_params)

            # all_draw = []
            best_score = 0
            best_label = None
            for label in self.features.keys():

                matches: List = flann.knnMatch(d,self.features[label][1],k=2)

                # store all the good matches as per Lowe's ratio test.
                good = []
                for m, n in matches:
                    if m.distance < 0.7*n.distance:
                        good.append(m)





                # print(label, len(matches), len(good))
                # score = sum((m.distance for m in matches))
                # actual = cv2.imread('./images/labelled/' + label + '.png')
                # draw = cv2.drawMatches(tile_img, k, actual, self.features[label][0], matches, actual, flags=2)
                # all_draw.append(draw)
                score = len(good)





                if score > best_score:
                    best_score = score
                    best_label = label


            assert best_label is not None
            result.add(rect, best_label)

        # result.image = debug
            # show(tile_img)

        result.get_debug_image = lambda: edge_result.get_debug_image()



        return result
