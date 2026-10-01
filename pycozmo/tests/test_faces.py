"""

Tests for finding and keeping track of faces.

The models are OpenCV's and are not part of the tests: what finds a face in an image is replaced by a detector that
says what it is told. A face needs a person's photograph to find, and nobody's is in the repository; a test that does
find one takes its image from the environment: PYCOZMO_TEST_FACE_IMAGE.

"""

import json
import math
import os
import pathlib
import tempfile
import threading
import time
import unittest
from typing import Any, List, Optional, Tuple
from unittest import mock

import numpy as np
from PIL import Image

import pycozmo
from pycozmo import camera, event, face_detection, faces
from pycozmo.face_detection import DetectedFace

from .test_brain import cozmo_assets_available


CALIBRATION = camera.DEFAULT_CALIBRATION
IMAGE = Image.new("L", (320, 240), 90)


def seen_face(depth: float = 600.0, left: float = 0.0, up: float = 0.0, score: float = 0.95,
              head_angle: float = 0.0) -> DetectedFace:
    """
    The face a camera would find: a pair of eyes, EYE_DISTANCE apart, at a depth along the optical axis and so far to
    the right of it and below it, in mm, projected through the lens.
    """
    half = face_detection.EYE_DISTANCE / 2
    points = np.array([[left - half, up, depth], [left + half, up, depth], [left, up + 30.0, depth]])
    u, v = CALIBRATION.distort(points[:, 0] / points[:, 2], points[:, 1] / points[:, 2])
    landmarks = tuple((float(x), float(y)) for x, y in zip(u, v))
    # The image's left is the face's right.
    eyes = (landmarks[0], landmarks[1])
    box = (eyes[0][0] - 20.0, eyes[0][1] - 30.0, eyes[1][0] - eyes[0][0] + 40.0, 80.0)
    return DetectedFace(box, eyes + (landmarks[2], landmarks[2], landmarks[2]), score, np.zeros(15, dtype=np.float32))


class FakeDetector:
    """ Finds what it is given, and tells each face by the features it is given with it. """

    def __init__(self) -> None:
        self.found: List[Tuple[DetectedFace, np.ndarray]] = []
        self.embedded = 0

    def show(self, *found: Tuple[DetectedFace, np.ndarray]) -> None:
        self.found = list(found)

    def detect(self, image: Any) -> List[DetectedFace]:
        return [face for face, _ in self.found]

    def embed(self, image: Any, face: DetectedFace) -> np.ndarray:
        self.embedded += 1
        return next(features for found, features in self.found if found is face)


def person(index: int) -> np.ndarray:
    """ The features of somebody: each is alike itself, and not alike another. """
    features = np.zeros(128, dtype=np.float32)
    features[index] = 1.0
    features[(index + 1) % 128] = 0.1
    return features


class TestSimilarity(unittest.TestCase):

    def test_a_cosine(self):
        self.assertAlmostEqual(face_detection.similarity(person(0), person(0)), 1.0, places=5)
        self.assertLess(face_detection.similarity(person(0), person(1)), face_detection.SAME_PERSON)
        self.assertEqual(face_detection.similarity(np.zeros(128), person(0)), 0.0)


class TestPosition(unittest.TestCase):

    def check(self, depth: float, left: float, up: float, head_angle: float, pitch: float) -> None:
        face = seen_face(depth, left, up)
        position, distance = face_detection.face_position(face, CALIBRATION, head_angle, pitch)
        # Where the middle of the eyes is, in the camera's frame and then the robot's.
        centre = camera.camera_to_robot(np.array([left, up, depth]), head_angle, pitch)
        self.assertAlmostEqual(distance, math.sqrt(depth ** 2 + left ** 2 + up ** 2), delta=distance * 0.01)
        np.testing.assert_allclose(position, centre, atol=0.01 * distance + 1.0)

    def test_where_a_face_is(self):
        for depth, left, up, head_angle, pitch in ((600.0, 0.0, 0.0, 0.0, 0.0), (900.0, 120.0, -60.0, 0.0, 0.0),
                                                   (400.0, -100.0, 40.0, 0.3, 0.02), (1200.0, 200.0, 0.0, -0.1, 0.0)):
            with self.subTest(depth=depth, left=left, up=up, head_angle=head_angle):
                self.check(depth, left, up, head_angle, pitch)

    def test_a_face_twice_as_far_is_half_as_wide(self):
        near = face_detection.face_position(seen_face(500.0), CALIBRATION, 0.0)[1]
        far = face_detection.face_position(seen_face(1000.0), CALIBRATION, 0.0)[1]
        self.assertAlmostEqual(far / near, 2.0, places=2)

    def test_eyes_in_one_place_have_no_distance(self):
        face = seen_face()._replace(landmarks=((100.0, 100.0), (100.0, 100.0), (100.0, 120.0)))
        with self.assertRaises(ValueError):
            face_detection.face_position(face, CALIBRATION, 0.0)


class TestImages(unittest.TestCase):

    def test_what_the_models_take(self):
        grey = face_detection._to_bgr(Image.new("L", (4, 2), 7))
        self.assertEqual((grey.shape, grey.dtype), ((2, 4, 3), np.uint8))
        self.assertTrue((grey == 7).all())
        # RGB comes out as BGR.
        colour = face_detection._to_bgr(Image.new("RGB", (4, 2), (1, 2, 3)))
        self.assertEqual(tuple(colour[0, 0]), (3, 2, 1))
        self.assertEqual(tuple(face_detection._to_bgr(np.full((2, 4, 4), 5, dtype=np.uint8))[0, 0]), (5, 5, 5))


class TestModels(unittest.TestCase):

    def test_found_where_they_are(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            self.assertFalse(face_detection.models_present(path))
            for name, _, _ in face_detection.MODELS.values():
                (path / name).write_bytes(b"x")
            self.assertTrue(face_detection.models_present(path))

    def download(self, directory: pathlib.Path, payload: bytes, sha256: str) -> None:
        models = {role: (name, url, sha256) for role, (name, url, _) in face_detection.MODELS.items()}

        class Response:
            headers = {"Content-Length": str(len(payload))}

            def __init__(self) -> None:
                self.rest = payload

            def read(self, count: int) -> bytes:
                data, self.rest = self.rest[:count], self.rest[count:]
                return data

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        with mock.patch.object(face_detection, "MODELS", models), \
                mock.patch.object(face_detection.urllib.request, "urlopen", side_effect=lambda url: Response()):
            face_detection.download_models(directory)

    def test_downloaded_and_checked(self):
        import hashlib
        payload = b"a model"
        with tempfile.TemporaryDirectory() as directory:
            self.download(pathlib.Path(directory), payload, hashlib.sha256(payload).hexdigest())
            names = sorted(name for name, _, _ in face_detection.MODELS.values())
            self.assertEqual(sorted(os.listdir(directory)), names)
            self.assertEqual((pathlib.Path(directory) / face_detection.MODELS["detector"][0]).read_bytes(), payload)

    def test_one_that_is_not_what_it_should_be_is_not_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(pycozmo.exception.InvalidOperation):
                self.download(pathlib.Path(directory), b"a model", "0" * 64)
            self.assertEqual(os.listdir(directory), [])

    def test_a_sound_one_is_not_fetched_again(self):
        import hashlib
        payload = b"a model"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            self.download(path, payload, hashlib.sha256(payload).hexdigest())
            with mock.patch.object(face_detection.urllib.request, "urlopen", side_effect=AssertionError):
                models = {role: (name, url, hashlib.sha256(payload).hexdigest())
                          for role, (name, url, _) in face_detection.MODELS.items()}
                with mock.patch.object(face_detection, "MODELS", models):
                    face_detection.download_models(path)


class FacesTestCase(unittest.TestCase):

    def setUp(self):
        self.cli = pycozmo.client.Client()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.detector = FakeDetector()
        self.faces = faces.Faces(self.cli, self.detector,
                                 faces.FaceGallery(pathlib.Path(self.directory.name) / "faces" / "gallery.json"))
        self.events: List[Tuple[str, int]] = []
        for evt in (event.EvtFaceAppeared, event.EvtFaceObserved, event.EvtFaceIdentified, event.EvtFaceDisappeared):
            self.cli.add_handler(evt, lambda cli, face, name=evt.__name__: self.events.append((name[7:], face.face_id)))

    def look(self, now: float, *found: Tuple[DetectedFace, np.ndarray], head_angle: float = 0.0) -> List[faces.Face]:
        self.detector.show(*found)
        return self.faces.process(IMAGE, CALIBRATION, head_angle, 0.0, now)

    def happened(self) -> List[Tuple[str, int]]:
        events, self.events = self.events, []
        return events


class TestTracking(FacesTestCase):

    def test_a_face_appears_and_is_seen_again(self):
        seen, = self.look(10.0, (seen_face(600.0), person(0)))
        self.assertEqual(self.happened(), [("Appeared", 1), ("Observed", 1)])
        # Ahead of the robot and a face's height: where it is in the world, which is the robot's frame as the robot
        # has not moved.
        assert seen.pose is not None
        self.assertAlmostEqual(seen.pose.x, 600.0, delta=15.0)
        self.assertAlmostEqual(seen.distance, 600.0, delta=10.0)
        again, = self.look(10.2, (seen_face(610.0, left=10.0), person(0)))
        self.assertIs(again, seen)
        self.assertEqual(self.happened(), [("Observed", 1)])
        self.assertEqual(seen.observations, 2)
        self.assertEqual(self.faces.visible(10.2), [seen])

    def test_a_face_is_not_told_again_every_time(self):
        self.look(10.0, (seen_face(), person(0)))
        self.look(10.2, (seen_face(), person(0)))
        self.look(10.4, (seen_face(), person(0)))
        self.assertEqual(self.detector.embedded, 1)
        self.look(11.2, (seen_face(), person(0)))
        self.assertEqual(self.detector.embedded, 2)

    def test_a_face_leaves(self):
        seen, = self.look(10.0, (seen_face(), person(0)))
        self.happened()
        self.look(11.0)
        self.assertEqual(self.happened(), [])
        self.assertTrue(seen.present)
        self.look(10.0 + self.faces.LOST_TIME)
        self.assertEqual(self.happened(), [("Disappeared", 1)])
        self.assertFalse(seen.present)
        self.assertEqual(self.faces.visible(10.0 + self.faces.LOST_TIME), [])
        # Only once.
        self.look(20.0)
        self.assertEqual(self.happened(), [])

    def test_and_comes_back_somewhere_else_to_be_known_by_its_features(self):
        seen, = self.look(10.0, (seen_face(600.0), person(0)))
        self.look(14.0)
        self.happened()
        again, = self.look(15.0, (seen_face(900.0, left=300.0), person(0)))
        self.assertIs(again, seen)
        self.assertEqual(self.happened(), [("Appeared", 1), ("Observed", 1)])

    def test_somebody_else_is_another_face(self):
        first, = self.look(10.0, (seen_face(), person(0)))
        self.look(14.0)
        other, = self.look(15.0, (seen_face(), person(5)))
        self.assertIsNot(other, first)
        self.assertEqual(len(self.faces), 2)

    def test_somebody_who_takes_the_place_of_a_face_is_another(self):
        first, = self.look(10.0, (seen_face(), person(0)))
        other, = self.look(10.0 + self.faces.RECOGNIZE_INTERVAL + 0.1, (seen_face(), person(5)))
        self.assertIsNot(other, first)
        self.assertEqual(len(self.faces), 2)

    def test_two_faces_keep_their_own_tracks(self):
        a, b = self.look(10.0, (seen_face(600.0, left=-150.0), person(0)), (seen_face(700.0, left=150.0), person(1)))
        # They come in the other order, and a little moved.
        again = self.look(10.2, (seen_face(710.0, left=155.0), person(1)), (seen_face(605.0, left=-145.0), person(0)))
        self.assertEqual({face.face_id for face in again}, {a.face_id, b.face_id})
        self.assertEqual(sorted(self.faces.visible(10.2), key=lambda face: face.face_id), [a, b])

    def test_a_far_face_may_seem_to_move_further(self):
        # Its distance is only told to some 15%: at two metres it may jump 300 mm and be the same face.
        first, = self.look(10.0, (seen_face(2000.0), person(0)))
        again, = self.look(10.2, (seen_face(2300.0), person(0)))
        self.assertIs(again, first)

    def test_a_face_pose_is_in_the_world(self):
        # The robot has moved on, and turned to face where it was left: a quarter turn to the left.
        self.cli.pose = pycozmo.util.Pose(1000.0, 200.0, 0.0, angle_z=pycozmo.util.Angle(degrees=90.0))
        seen, = self.look(10.0, (seen_face(600.0), person(0)))
        assert seen.pose is not None
        self.assertAlmostEqual(seen.pose.x, 1000.0, delta=15.0)
        self.assertAlmostEqual(seen.pose.y, 200.0 + 600.0, delta=15.0)

    def test_the_head_is_where_the_image_was_taken(self):
        up, = self.look(10.0, (seen_face(600.0), person(0)), head_angle=0.4)
        level, = self.look(15.0, (seen_face(600.0), person(1)), head_angle=0.0)
        assert up.pose is not None and level.pose is not None
        self.assertIsNot(up, level)
        self.assertGreater(up.pose.z, level.pose.z + 100.0)

    def test_what_is_not_found_is_not_a_face(self):
        self.assertEqual(self.look(10.0), [])
        self.assertEqual(len(self.faces), 0)

    def test_an_unnamed_face_is_forgotten_in_time(self):
        self.look(10.0, (seen_face(), person(0)))
        self.look(10.0 + self.faces.REMEMBER_TIME + 1.0)
        self.assertEqual(len(self.faces), 0)


class TestNames(FacesTestCase):

    def test_a_person_known_is_told(self):
        self.faces.gallery.add("Eileen", [person(0)])
        self.happened()
        seen, = self.look(10.0, (seen_face(), person(0)))
        self.assertEqual(seen.name, "Eileen")
        self.assertGreater(seen.similarity, 0.99)
        self.assertEqual(self.happened(), [("Appeared", 1), ("Observed", 1), ("Identified", 1)])
        # Told once.
        self.look(10.2, (seen_face(), person(0)))
        self.look(12.0, (seen_face(), person(0)))
        self.assertEqual([name for name, _ in self.happened()].count("Identified"), 0)

    def test_a_person_not_known_is_not(self):
        self.faces.gallery.add("Eileen", [person(0)])
        seen, = self.look(10.0, (seen_face(), person(7)))
        self.assertIsNone(seen.name)

    def test_the_closest_of_the_people_known(self):
        self.faces.gallery.add("Eileen", [person(0)])
        self.faces.gallery.add("Bob", [person(1)])
        view = person(1) + 0.05 * person(0)
        seen, = self.look(10.0, (seen_face(), view))
        self.assertEqual(seen.name, "Bob")

    def test_by_name(self):
        self.faces.gallery.add("Eileen", [person(0)])
        self.assertIsNone(self.faces.by_name("Eileen"))
        seen, = self.look(10.0, (seen_face(), person(0)))
        self.assertIs(self.faces.by_name("Eileen"), seen)

    def test_forgotten(self):
        self.faces.gallery.add("Eileen", [person(0)])
        seen, = self.look(10.0, (seen_face(), person(0)))
        self.assertTrue(self.faces.forget("Eileen"))
        self.assertIsNone(seen.name)
        self.assertEqual(self.faces.gallery.names(), [])
        self.assertFalse(self.faces.forget("Eileen"))

    def test_the_gallery_is_kept(self):
        gallery = self.faces.gallery
        gallery.add("Eileen", [person(0), person(0) * 0.9])
        gallery.add("Bob", [person(1)])
        again = faces.FaceGallery(gallery.path)
        self.assertEqual(again.names(), ["Bob", "Eileen"])
        self.assertEqual(again.identify(person(0))[0], "Eileen")
        # Features, not pictures: 128 numbers a view, in a file of the user's own.
        with open(gallery.path) as f:
            data = json.load(f)
        self.assertEqual(sorted(len(view) for views in data.values() for view in views), [128, 128, 128])

    def test_a_rename(self):
        gallery = self.faces.gallery
        gallery.add("Eileen", [person(0)])
        self.assertTrue(gallery.rename("Eileen", "Collins"))
        self.assertEqual(gallery.names(), ["Collins"])
        self.assertFalse(gallery.rename("Eileen", "Nobody"))

    def test_only_so_many_views_are_kept(self):
        gallery = self.faces.gallery
        for _ in range(gallery.MAX_FEATURES + 5):
            gallery.add("Eileen", [person(0)])
        self.assertEqual(len(gallery.people["Eileen"]), gallery.MAX_FEATURES)

    def test_a_gallery_that_does_not_read_is_empty(self):
        path = self.faces.gallery.path
        os.makedirs(os.path.dirname(str(path)))
        with open(path, "w") as f:
            f.write("{not json")
        self.assertEqual(faces.FaceGallery(path).names(), [])


class TestEnrollment(FacesTestCase):

    def enroll(self, **kwargs: Any) -> List[bool]:
        result: List[bool] = []
        thread = threading.Thread(target=lambda: result.append(self.faces.enroll("Eileen", **kwargs)))
        thread.start()
        self.addCleanup(thread.join, 5.0)
        while self.faces._enrollment is None and thread.is_alive():
            time.sleep(0.01)
        self.thread = thread
        return result

    def test_a_few_views_of_the_nearest_face(self):
        result = self.enroll(views=3, timeout=10.0)
        now = 10.0
        while not result and now < 20.0:
            # A person at 600 mm, and somebody behind them: the nearest is who the robot gets to know.
            self.look(now, (seen_face(600.0), person(0)), (seen_face(1500.0, left=300.0), person(4)))
            now += self.faces.ENROLL_INTERVAL
            time.sleep(0.01)
        self.thread.join(5.0)
        self.assertEqual(result, [True])
        self.assertEqual(self.faces.gallery.names(), ["Eileen"])
        self.assertEqual(len(self.faces.gallery.people["Eileen"]), 3)
        self.assertEqual(self.faces.visible(now)[0].name, "Eileen")
        self.assertIn(("Identified", 1), self.events)
        # And the robot knows them.
        self.assertEqual(self.faces.gallery.identify(person(0))[0], "Eileen")
        self.assertIsNone(self.faces.gallery.identify(person(4))[0])

    def test_views_are_a_little_apart(self):
        result = self.enroll(views=3, timeout=10.0)
        for i in range(5):
            self.look(10.0 + i * 0.1, (seen_face(), person(0)))
        self.assertEqual(result, [])
        enrollment = self.faces._enrollment
        assert enrollment is not None
        self.assertEqual(len(enrollment.views), 2)
        self.look(11.0, (seen_face(), person(0)))
        self.thread.join(5.0)
        self.assertEqual(result, [True])

    def test_no_face_no_enrollment(self):
        self.assertFalse(self.faces.enroll("Eileen", timeout=0.2))
        self.assertEqual(self.faces.gallery.names(), [])
        self.assertIsNone(self.faces._enrollment)

    def test_cancelled(self):
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(self.faces.enroll("Eileen", cancel=cancel))


class TestWithoutOpenCV(unittest.TestCase):

    def test_nothing_is_found_and_nothing_fails(self):
        cli = pycozmo.client.Client()
        found = faces.Faces(cli)
        with mock.patch.object(face_detection, "opencv_available", return_value=False):
            self.assertFalse(found.available())
            self.assertEqual(found.process(IMAGE, CALIBRATION, 0.0), [])
            # And does not try again.
            with mock.patch.object(face_detection, "opencv_available", side_effect=AssertionError):
                self.assertEqual(found.process(IMAGE, CALIBRATION, 0.0), [])

    def test_a_detector_that_needs_it_says_so(self):
        with mock.patch.object(face_detection, "opencv_available", return_value=False):
            with self.assertRaises(pycozmo.exception.InvalidOperation):
                face_detection.FaceDetector()

    def test_nor_are_the_models_there(self):
        with tempfile.TemporaryDirectory() as directory:
            found = faces.Faces(pycozmo.client.Client())
            with mock.patch.object(pycozmo.util, "get_face_model_dir", return_value=pathlib.Path(directory)):
                self.assertFalse(found.available())
                self.assertEqual(found.process(IMAGE, CALIBRATION, 0.0), [])


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestBrain(unittest.TestCase):
    """ A face that appears is acknowledged, as Anki's engine did. """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def setUp(self):
        patcher = mock.patch.object(self.brain, "post_reaction")
        self.posted = patcher.start()
        self.addCleanup(patcher.stop)
        self.face = faces.Face(1, 0.0)

    def test_acknowledged_when_it_appears(self):
        self.brain.cli.dispatch(event.EvtFaceAppeared, self.brain.cli, self.face)
        self.posted.assert_called_once_with("FacePositionUpdated")

    def test_only_when_it_appears(self):
        self.brain.cli.dispatch(event.EvtFaceObserved, self.brain.cli, self.face)
        self.brain.cli.dispatch(event.EvtFaceDisappeared, self.brain.cli, self.face)
        self.posted.assert_not_called()

    def test_not_at_a_game_or_handling_a_cube(self):
        script = pycozmo.cube_behaviors.BehaviorStackBlocks(self.brain.cli, {"behaviorID": "StackBlocks"})
        with mock.patch.object(self.brain, "behavior", script):
            self.brain.cli.dispatch(event.EvtFaceAppeared, self.brain.cli, self.face)
        self.posted.assert_not_called()

    def test_faces_are_looked_for_while_the_robot_keeps_still(self):
        with mock.patch.object(self.brain.cli.faces, "process") as process:
            self.brain.faces_time = 0.0
            self.brain.on_camera_image(self.brain.cli, IMAGE)
            self.brain.on_camera_image(self.brain.cli, IMAGE)
            self.assertEqual(process.call_count, 1)
            self.brain.faces_time = 0.0
            still = self.brain.cli.robot_status
            self.brain.cli.robot_status = still | pycozmo.robot.RobotStatusFlag.IS_MOVING
            try:
                self.brain.on_camera_image(self.brain.cli, IMAGE)
            finally:
                self.brain.cli.robot_status = still
            self.assertEqual(process.call_count, 1)


FACE_IMAGE: Optional[str] = os.environ.get("PYCOZMO_TEST_FACE_IMAGE")


@unittest.skipUnless(FACE_IMAGE and face_detection.opencv_available() and face_detection.models_present(),
                     "PYCOZMO_TEST_FACE_IMAGE names no image, or OpenCV and the models are not there.")
class TestWithTheModels(unittest.TestCase):
    """ OpenCV's own models, on a photograph of somebody's face the environment names: nobody's is kept here. """

    def test_a_face_is_found_told_and_placed(self):
        assert FACE_IMAGE is not None
        photograph = Image.open(FACE_IMAGE).convert("RGB")
        detector = face_detection.FaceDetector()
        found = detector.detect(photograph)
        self.assertEqual(len(found), 1)
        self.assertGreaterEqual(found[0].score, face_detection.SCORE_THRESHOLD)
        features = detector.embed(photograph, found[0])
        self.assertEqual(features.shape, (128,))
        # Itself, in the camera's own quality: grey, small and blurred a little.
        small = photograph.convert("L").resize((photograph.width // 2, photograph.height // 2))
        again = detector.detect(small)
        if again:
            self.assertGreater(face_detection.similarity(features, detector.embed(small, again[0])),
                               face_detection.SAME_PERSON)
        # Nothing in a blank image.
        self.assertEqual(detector.detect(Image.new("L", (320, 240), 90)), [])
