"""

The faces the robot sees, and the ones it knows.

Faces come from pycozmo.face_detection, which finds them in an image; this keeps track of them. A face seen where one
was a moment ago is that face; one seen anew is told by its features from those the robot lost sight of, and from the
people it knows by name. A person is known by name once enrolled: Faces.enroll() takes a few views of the face the
robot is looking at, and the features - 128 numbers, not a picture - are kept in the user's own directory, in
util.get_face_gallery_path(). Nothing is sent anywhere.

A face has a pose in the robot's world frame, like a cube, and the camera sees it through the robot's own pose: so
faces are looked for while the robot is still, as markers are. The events are EvtFaceAppeared for a face that was not
there, EvtFaceObserved each time one is seen, EvtFaceIdentified for a name told, and EvtFaceDisappeared once a face has
not been seen for LOST_TIME.

"""

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np

from .logger import logger
from . import camera
from . import event
from . import face_detection
from . import util


__all__ = [
    "FacePose",
    "Face",
    "FaceGallery",
    "Faces",
]


@dataclass(frozen=True)
class FacePose:
    """ Where a face was seen, in the robot's world frame. """

    #: The middle of the eyes, in mm.
    x: float
    y: float
    z: float
    #: When it was seen, by time.perf_counter().
    time: float


class Face:
    """ A face the robot has seen. """

    __slots__ = [
        "face_id",
        "name",
        "pose",
        "distance",
        "similarity",
        "first_seen",
        "last_seen",
        "observations",
        "present",
        "features",
        "identified_at",
    ]

    def __init__(self, face_id: int, now: float) -> None:
        self.face_id = face_id
        #: Who it is, if the robot knows: see Faces.enroll().
        self.name: Optional[str] = None
        self.pose: Optional[FacePose] = None
        #: How far from the camera it was, in mm, when last seen.
        self.distance = 0.0
        #: How alike it was to the person it is named for, when last checked, as a cosine.
        self.similarity = 0.0
        self.first_seen = now
        self.last_seen = now
        self.observations = 0
        #: Whether it is in sight: seen within Faces.LOST_TIME.
        self.present = False
        #: Its features when last told apart: see face_detection.similarity().
        self.features: Optional[np.ndarray] = None
        self.identified_at = float("-inf")

    def seen_within(self, seconds: float, now: Optional[float] = None) -> bool:
        now = time.perf_counter() if now is None else now
        return now - self.last_seen < seconds

    def __repr__(self) -> str:
        return "Face({}{}, present={})".format(self.face_id, " " + self.name if self.name else "", self.present)


class FaceGallery:
    """ The people the robot knows by name: each with the features of a few views of their face. """

    #: How many views of a person are kept: the oldest go.
    MAX_FEATURES = 30

    def __init__(self, path: Optional[Any] = None) -> None:
        self.path = path or util.get_face_gallery_path()
        self.people: Dict[str, List[np.ndarray]] = {}
        self._loaded = False

    def load(self) -> None:
        self._loaded = True
        self.people = {}
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.people = {str(name): [np.array(features, dtype=np.float32) for features in views]
                           for name, views in data.items() if views}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as e:
            logger.warning("Failed to read the faces known by name in %s . %s", self.path, e)

    def save(self) -> None:
        directory = os.path.dirname(str(self.path))
        os.makedirs(directory, exist_ok=True)
        data = {name: [[round(float(v), 6) for v in features] for features in views]
                for name, views in self.people.items()}
        partial = str(self.path) + ".part"
        with open(partial, "w") as f:
            json.dump(data, f)
        os.replace(partial, str(self.path))

    def names(self) -> List[str]:
        self._ensure_loaded()
        return sorted(self.people)

    def add(self, name: str, views: List[np.ndarray]) -> None:
        """ Add views of a person's face, and keep them. """
        self._ensure_loaded()
        kept = self.people.setdefault(name, []) + [np.array(view, dtype=np.float32) for view in views]
        self.people[name] = kept[-self.MAX_FEATURES:]
        self.save()

    def forget(self, name: str) -> bool:
        """ Forget a person. Say whether the robot knew them. """
        self._ensure_loaded()
        if self.people.pop(name, None) is None:
            return False
        self.save()
        return True

    def rename(self, name: str, new_name: str) -> bool:
        """ Call a person by another name. Say whether the robot knew them. """
        self._ensure_loaded()
        if name not in self.people:
            return False
        self.people[new_name] = self.people.pop(name) + self.people.pop(new_name, [])
        self.save()
        return True

    def identify(self, features: np.ndarray) -> Tuple[Optional[str], float]:
        """ Who a face is, if it is alike enough to somebody known: the name, and how alike, as a cosine. """
        self._ensure_loaded()
        best_name: Optional[str] = None
        best = -1.0
        for name, views in self.people.items():
            score = max(face_detection.similarity(features, view) for view in views)
            if score > best:
                best_name, best = name, score
        if best_name is not None and best >= face_detection.SAME_PERSON:
            return best_name, best
        return None, max(best, 0.0)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()


class _Enrollment:
    """ A person being enrolled: the views taken so far, and when the next is due. """

    def __init__(self, name: str, views: int) -> None:
        self.name = name
        self.wanted = views
        self.views: List[np.ndarray] = []
        self.next_view = 0.0
        self.done = threading.Event()


class Faces:
    """ The faces the robot has seen, in cli.faces. """

    #: How long a face is taken for in sight after it was last seen, in seconds.
    LOST_TIME = 2.0
    #: How far, in mm, a face may have moved since last seen and still be taken for the same one, and for how long
    #: after it was seen. Its distance from the camera is only told to some 15%, so the further it is the more it may
    #: seem to move: the greater of the two figures is what counts.
    TRACK_DISTANCE = 250.0
    TRACK_FRACTION = 0.35
    TRACK_TIME = 2.0
    #: How long a face that left is remembered for, to be told again by its features, in seconds. A face with a name
    #: is not forgotten: the gallery tells it.
    REMEMBER_TIME = 300.0
    #: How often a face seen is told again from whom it is, at most, in seconds: it takes 15 ms.
    RECOGNIZE_INTERVAL = 1.0
    #: How far apart enrollment takes its views, in seconds.
    ENROLL_INTERVAL = 0.4

    def __init__(self, cli: Any, detector: Optional[Any] = None, gallery: Optional[FaceGallery] = None) -> None:
        # The client. Typed Any, since importing it here would be circular.
        self.cli = cli
        self.gallery = gallery or FaceGallery()
        self._detector = detector
        self._unavailable = False
        self._faces: Dict[int, Face] = {}
        self._next_id = 1
        self._enrollment: Optional[_Enrollment] = None
        self._lock = threading.Lock()

    def __iter__(self) -> Iterator[Face]:
        return iter(list(self._faces.values()))

    def __len__(self) -> int:
        return len(self._faces)

    def visible(self, now: Optional[float] = None) -> List[Face]:
        """ The faces in sight, the nearest first. """
        now = time.perf_counter() if now is None else now
        return sorted((face for face in self._faces.values() if face.present and face.seen_within(self.LOST_TIME, now)),
                      key=lambda face: face.distance)

    def by_name(self, name: str) -> Optional[Face]:
        """ The face of a person, if the robot has seen them: the one seen last. """
        known = [face for face in self._faces.values() if face.name == name]
        return max(known, key=lambda face: face.last_seen) if known else None

    def available(self) -> bool:
        """ Whether faces can be found: OpenCV and the models are there, or a detector was given. """
        if self._detector is not None:
            return True
        return not self._unavailable and face_detection.opencv_available() and face_detection.models_present()

    def process(self, image: Any, calibration: Optional[camera.CameraCalibration], head_angle: float,
                pitch: float = 0.0, now: Optional[float] = None) -> List[Face]:
        """
        Look for faces in a camera image, the head angle and pitch, in radians, those the image was taken at, and
        keep track of them. Say which were seen. Without the robot's own calibration, a typical one is used.
        """
        now = time.perf_counter() if now is None else now
        calibration = calibration or camera.DEFAULT_CALIBRATION
        self._expire(now)
        detector = self._get_detector()
        if detector is None:
            return []
        width, height = _size(image)
        calibration = calibration.scaled(width, height)
        seen = []
        taken: set = set()
        detections = detector.detect(image)
        placed = []
        for detection in detections:
            try:
                position, distance = face_detection.face_position(detection, calibration, head_angle, pitch)
            except ValueError:
                continue
            placed.append((detection, self._to_world(position), distance))
        # Faces seen lately, the nearest to each detection first.
        candidates = [(face, face.pose) for face in self._faces.values()
                      if face.pose is not None and now - face.last_seen < self.TRACK_TIME]
        pairs = sorted(((_apart(world, pose) / max(self.TRACK_DISTANCE, self.TRACK_FRACTION * distance),
                         index, face)
                        for index, (_, world, distance) in enumerate(placed) for face, pose in candidates),
                       key=lambda pair: pair[0])
        tracked: Dict[int, Face] = {}
        for apart, index, candidate in pairs:
            if apart > 1.0:
                break
            if index not in tracked and candidate.face_id not in taken:
                tracked[index] = candidate
                taken.add(candidate.face_id)
        for index, (detection, world, distance) in enumerate(placed):
            face: Optional[Face] = tracked.get(index)
            features: Optional[np.ndarray] = None
            if face is not None and (face.features is None or now - face.identified_at >= self.RECOGNIZE_INTERVAL or
                                     self._wants_view(now)):
                features = detector.embed(image, detection)
                if face.features is not None and \
                        face_detection.similarity(features, face.features) < face_detection.SAME_PERSON:
                    # Another person where the face was.
                    face = None
            if face is None:
                features = features if features is not None else detector.embed(image, detection)
                face = self._revive(features, now, taken) or self._new_face(now)
                taken.add(face.face_id)
            self._observe(face, world, distance, now, features)
            seen.append(face)
        return seen

    def enroll(self, name: str, views: int = 5, timeout: float = 30.0,
               cancel: Optional[threading.Event] = None) -> bool:
        """
        Get to know the face in front of the camera: take a few views of the nearest one, a little apart, and keep
        them under a name. Blocks until done, so not from the thread that dispatches the client's events. Say whether
        it was. Called again for a name known, it adds views.
        """
        enrollment = _Enrollment(name, views)
        with self._lock:
            self._enrollment = enrollment
        try:
            deadline = time.perf_counter() + timeout
            while not enrollment.done.wait(0.05):
                if time.perf_counter() > deadline or (cancel is not None and cancel.is_set()):
                    return False
            return True
        finally:
            with self._lock:
                self._enrollment = None

    def forget(self, name: str) -> bool:
        """ Forget a person by name, and the faces the robot had told for them. Say whether it knew them. """
        known = self.gallery.forget(name)
        for face in self._faces.values():
            if face.name == name:
                face.name = None
        return known

    def _get_detector(self) -> Optional[Any]:
        if self._detector is None and not self._unavailable:
            if face_detection.opencv_available() and face_detection.models_present():
                try:
                    self._detector = face_detection.FaceDetector()
                except Exception as e:
                    logger.warning("Failed to load the face models. %s", e)
                    self._unavailable = True
            else:
                logger.info("Faces are not looked for: they need OpenCV, pycozmo[faces], and the models, "
                            "tools/pycozmo_faces.py download.")
                self._unavailable = True
        return self._detector

    def _to_world(self, position: Tuple[float, float, float]) -> Tuple[float, float, float]:
        pose = self.cli.pose
        angle = pose.rotation.angle_z.radians
        c, s = math.cos(angle), math.sin(angle)
        return (pose.position.x + c * position[0] - s * position[1],
                pose.position.y + s * position[0] + c * position[1],
                pose.position.z + position[2])

    def _wants_view(self, now: float) -> bool:
        enrollment = self._enrollment
        return enrollment is not None and now >= enrollment.next_view

    def _new_face(self, now: float) -> Face:
        face = Face(self._next_id, now)
        self._next_id += 1
        self._faces[face.face_id] = face
        return face

    def _revive(self, features: np.ndarray, now: float, taken: set) -> Optional[Face]:
        """
        A face seen before, told by its features: one that left and is back, or one that seemed to jump too far to be
        followed by where it was.
        """
        best: Optional[Face] = None
        best_score = face_detection.SAME_PERSON
        for face in self._faces.values():
            if face.features is None or face.face_id in taken:
                continue
            score = face_detection.similarity(features, face.features)
            if score >= best_score:
                best, best_score = face, score
        return best

    def _observe(self, face: Face, world: Tuple[float, float, float], distance: float, now: float,
                 features: Optional[np.ndarray]) -> None:
        appeared = not face.present
        face.pose = FacePose(world[0], world[1], world[2], now)
        face.distance = distance
        face.last_seen = now
        face.observations += 1
        face.present = True
        identified = None
        if features is not None:
            face.features = features
            face.identified_at = now
            name, face.similarity = self.gallery.identify(features)
            if name is not None and name != face.name:
                face.name = name
                identified = face
        self._enroll_view(face, features, now)
        if appeared:
            self.cli.dispatch(event.EvtFaceAppeared, self.cli, face)
        self.cli.dispatch(event.EvtFaceObserved, self.cli, face)
        if identified is not None:
            self.cli.dispatch(event.EvtFaceIdentified, self.cli, face)

    def _enroll_view(self, face: Face, features: Optional[np.ndarray], now: float) -> None:
        """ Take a view of a face for the person being enrolled, if it is the nearest in sight and it is time. """
        with self._lock:
            enrollment = self._enrollment
            if enrollment is None or features is None or now < enrollment.next_view:
                return
            nearest = self.visible(now)
            if nearest and nearest[0] is not face and nearest[0].distance < face.distance:
                return
            enrollment.views.append(features)
            enrollment.next_view = now + self.ENROLL_INTERVAL
            if len(enrollment.views) < enrollment.wanted:
                return
            self.gallery.add(enrollment.name, enrollment.views)
        face.name = enrollment.name
        face.similarity = 1.0
        self.cli.dispatch(event.EvtFaceIdentified, self.cli, face)
        enrollment.done.set()

    def _expire(self, now: float) -> None:
        for face in list(self._faces.values()):
            if face.present and now - face.last_seen >= self.LOST_TIME:
                face.present = False
                self.cli.dispatch(event.EvtFaceDisappeared, self.cli, face)
            if not face.present and face.name is None and now - face.last_seen >= self.REMEMBER_TIME:
                del self._faces[face.face_id]


def _size(image: Any) -> Tuple[int, int]:
    """ An image's width and height: a PIL one's, or an array's. """
    if hasattr(image, "size") and not hasattr(image, "shape"):
        width, height = image.size
        return int(width), int(height)
    shape = np.asarray(image).shape
    return int(shape[1]), int(shape[0])


def _apart(world: Tuple[float, float, float], pose: FacePose) -> float:
    return math.dist(world, (pose.x, pose.y, pose.z))
