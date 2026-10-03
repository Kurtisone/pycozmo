"""

Face detection and recognition in the camera images.

The robot does not look for faces; the application does, as Anki's engine did on the phone. This finds them with
OpenCV's two small face models: YuNet, a detector of 0.2 MB that gives a face's box, its score and five landmarks -
the eyes, the nose and the corners of the mouth - and SFace, which turns an aligned face into 128 numbers that are
close for the same person and far for another. Neither is Anki's: its engine used another detector, native code that
is no resource, so what is seen and what is told apart differs from what Cozmo's app did.

OpenCV is not a dependency of PyCozmo: install it with `pip install opencv-python-headless`, or with the faces extra of
a source install, `pip install ".[faces]"`, and fetch the models with tools/pycozmo_faces.py download. Without either,
nothing here finds a face, and the rest of PyCozmo goes on as before.


On the robot's camera, 320 by 240 in grayscale, YuNet found a face of 40 pixels across, a metre or so away, and not
one of 28; it takes 4.6 ms an image on a Steam Deck, and SFace 14.6 ms a face. Over 4092 images of cubes and rooms it
found nothing at its default threshold of 0.9, and four blurred cubes at 0.6. Where a face is follows from the
distance between its eyes, 63 mm on average: good to some 15%, which is as far as a face is turned.

"""

import contextlib
import hashlib
import math
import os
import pathlib
import urllib.request
from typing import Any, Callable, Iterator, List, NamedTuple, Optional, Tuple

import numpy as np

from . import camera
from . import exception
from . import util


__all__ = [
    "SCORE_THRESHOLD",
    "FOLLOW_THRESHOLD",
    "SAME_PERSON",
    "EYE_DISTANCE",
    "MODELS",

    "DetectedFace",
    "FaceDetector",

    "opencv_available",
    "models_present",
    "download_models",
    "face_position",
    "similarity",
]


#: How sure YuNet has to be of a face to take it for one, as a score from 0 to 1. Blurred cubes score up to 0.8
#: (4154 camera images, none with a face); OpenCV's default.
SCORE_THRESHOLD = 0.9
#: How sure it has to be of one it is already following. In a dim room a face scores 0.8 to 0.9, and is lost at every
#: frame under SCORE_THRESHOLD, which it is not.
FOLLOW_THRESHOLD = 0.7
#: How much two detections of the same face overlap before one is dropped, as intersection over union.
NMS_THRESHOLD = 0.3
#: How many candidates YuNet keeps before dropping those that overlap.
TOP_K = 5000
#: How alike two faces' features have to be, as a cosine, to be the same person: SFace's own threshold.
SAME_PERSON = 0.363
#: The distance between the eyes of a face, in mm: an adult's, on average.
EYE_DISTANCE = 63.0

#: The models, by role: file name, where they come from, and the SHA-256 of the file. YuNet is MIT licensed, SFace
#: Apache 2.0, both from OpenCV's model zoo; PyCozmo does not ship them.
_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
MODELS = {
    "detector": (
        "face_detection_yunet_2023mar.onnx",
        _ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "recognizer": (
        "face_recognition_sface_2021dec.onnx",
        _ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


class DetectedFace(NamedTuple):
    """ A face found in an image. """

    #: Its box in pixels: left, top, width, height.
    box: Tuple[float, float, float, float]
    #: Where YuNet puts the face's right eye, its left eye, the nose and the right and left corners of the mouth, in
    #: pixels. The face's right is on the left of the image.
    landmarks: Tuple[Tuple[float, float], ...]
    #: How sure the detector is of it, from 0 to 1.
    score: float
    #: What the detector said, as the recognizer wants it to align the face.
    row: np.ndarray


def opencv_available() -> bool:
    """ Whether OpenCV can be imported. """
    try:
        import cv2     # noqa: F401
    except ImportError:
        return False
    return True


def models_present(directory: Optional[pathlib.Path] = None) -> bool:
    """ Whether both models are where PyCozmo looks for them. """
    directory = directory or util.get_face_model_dir()
    return all((directory / name).is_file() for name, _, _ in MODELS.values())


def download_models(directory: Optional[pathlib.Path] = None,
                    progress: Optional[Callable[[str, int, int], None]] = None) -> None:
    """
    Fetch the models into a directory, the PyCozmo one by default, and check them against their hashes. A model
    already there and sound is left. progress is called with a file's name and the bytes read and expected.
    """
    directory = directory or util.get_face_model_dir()
    os.makedirs(str(directory), exist_ok=True)
    for name, url, sha256 in MODELS.values():
        path = directory / name
        if path.is_file() and _sha256(path) == sha256:
            continue
        partial = directory / (name + ".part")
        digest = hashlib.sha256()
        with urllib.request.urlopen(url) as response, open(partial, "wb") as f:
            total = int(response.headers.get("Content-Length") or 0)
            read = 0
            while True:
                data = response.read(65536)
                if not data:
                    break
                f.write(data)
                digest.update(data)
                read += len(data)
                if progress is not None:
                    progress(name, read, total)
        if digest.hexdigest() != sha256:
            os.remove(str(partial))
            raise exception.InvalidOperation("{} does not match its hash: it was not downloaded intact.".format(name))
        os.replace(str(partial), str(path))


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """ How alike two faces' features are, as a cosine: SAME_PERSON and above is the same person. """
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a.ravel(), b.ravel()) / norm) if norm else 0.0


class FaceDetector:
    """ OpenCV's YuNet and SFace, loaded when first used. """

    def __init__(self, directory: Optional[pathlib.Path] = None, score_threshold: float = SCORE_THRESHOLD) -> None:
        if not opencv_available():
            raise exception.InvalidOperation("Faces need OpenCV: pip install opencv-python-headless.")
        directory = directory or util.get_face_model_dir()
        if not models_present(directory):
            raise exception.InvalidOperation(
                "The face models are not in {} . Try running 'pycozmo_faces.py download'.".format(directory))
        import cv2
        self._cv2 = cv2
        self._directory = directory
        self.score_threshold = score_threshold
        with self._quiet():
            self._detector: Any = cv2.FaceDetectorYN.create(
                str(directory / MODELS["detector"][0]), "", (320, 240), score_threshold, NMS_THRESHOLD, TOP_K)
        self._recognizer: Any = None

    @contextlib.contextmanager
    def _quiet(self) -> Iterator[None]:
        """
        Without OpenCV's warnings while a model loads: OpenCV 5 says on loading either that the graph engine does
        not take the targets it was asked for, which it is not asked for, and which changes nothing.
        """
        logging = getattr(getattr(self._cv2, "utils", None), "logging", None)
        if logging is None:
            yield
            return
        previous = logging.getLogLevel()
        logging.setLogLevel(logging.LOG_LEVEL_ERROR)
        try:
            yield
        finally:
            logging.setLogLevel(previous)

    def detect(self, image: Any) -> List[DetectedFace]:
        """ The faces in an image, a PIL one or an array, in grayscale or colour: the most sure of them first. """
        bgr = _to_bgr(image)
        self._detector.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, rows = self._detector.detect(bgr)
        if rows is None:
            return []
        faces = [DetectedFace(
            box=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
            landmarks=tuple((float(row[4 + 2 * i]), float(row[5 + 2 * i])) for i in range(5)),
            score=float(row[14]), row=np.array(row, dtype=np.float32)) for row in rows]
        return sorted(faces, key=lambda face: -face.score)

    def embed(self, image: Any, face: DetectedFace) -> np.ndarray:
        """ A face's 128 features, for similarity(). """
        if self._recognizer is None:
            with self._quiet():
                self._recognizer = self._cv2.FaceRecognizerSF.create(
                    str(self._directory / MODELS["recognizer"][0]), "")
        bgr = _to_bgr(image)
        features: np.ndarray = self._recognizer.feature(self._recognizer.alignCrop(bgr, face.row))
        return np.array(features, dtype=np.float32).ravel()


def _to_bgr(image: Any) -> np.ndarray:
    """ An image as the 8 bit, three channel array OpenCV's models take. """
    pixels = np.asarray(image)
    if pixels.ndim == 2:
        pixels = np.stack([pixels] * 3, axis=-1)
    elif pixels.shape[2] == 4:
        pixels = pixels[..., :3][..., ::-1]
    else:
        pixels = pixels[..., ::-1]
    return np.ascontiguousarray(pixels, dtype=np.uint8)


def face_position(face: DetectedFace, calibration: camera.CameraCalibration, head_angle: float,
                  pitch: float = 0.0) -> Tuple[Tuple[float, float, float], float]:
    """
    Where a face is, in the robot's frame - ahead of the origin, to its left, up from the ground, in mm - and how far
    from the camera, from the distance between its eyes in the image, taken to be EYE_DISTANCE: a face is not seen
    squarely, and one turned away looks further than it is.

    head_angle and pitch are in radians, as for camera.camera_to_robot(). The calibration is the image's own: see
    CameraCalibration.scaled().
    """
    (eye_x1, eye_y1), (eye_x2, eye_y2) = face.landmarks[0], face.landmarks[1]
    x, y = calibration.undistort(np.array([eye_x1, eye_x2]), np.array([eye_y1, eye_y2]))
    apart = math.hypot(float(x[0] - x[1]), float(y[0] - y[1]))
    if apart <= 1e-6:
        raise ValueError("A face with its eyes in one place has no distance.")
    # The eyes' separation in normalized coordinates is the tangent of the angle between them, which is all
    # EYE_DISTANCE over the depth is, to a face held squarely to the camera.
    depth = EYE_DISTANCE / apart
    centre = np.array([float(x.mean()) * depth, float(y.mean()) * depth, depth])
    in_robot = camera.camera_to_robot(centre, head_angle, pitch)
    return (float(in_robot[0]), float(in_robot[1]), float(in_robot[2])), float(np.linalg.norm(centre))
