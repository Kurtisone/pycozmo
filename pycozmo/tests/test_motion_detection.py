"""

Tests for motion detection in the camera images.

The images are made up: a textured scene, the noise a camera sensor adds to every frame, and the
JPEG compression the robot applies before sending it. Without the noise two identical frames would
compare equal whatever the detector does, which would test nothing.

"""

import io
import math
import unittest
from typing import Any, List, Optional, Tuple

import numpy as np
from PIL import Image

import pycozmo
from pycozmo import motion_detection
from pycozmo.motion_detection import MotionDetector, MotionDetectorConfig

from .test_brain import cozmo_assets_available


WIDTH = 320
HEIGHT = 240
#: Time between two frames, the robot's 15 frames per second.
PERIOD = 1.0 / 15.0


class Scene:
    """ A still, textured scene that frames are shot of, with a square that can be moved around in it. """

    def __init__(self, seed: int = 1) -> None:
        self.random = np.random.default_rng(seed)
        # Blotches of 16 x 16 pixels, between mid-dark and bright: a table and what lies on it.
        blotches = self.random.uniform(60.0, 200.0, (HEIGHT // 16, WIDTH // 16))
        self.background = np.kron(blotches, np.ones((16, 16)))

    def shoot(self, square: Optional[Tuple[int, int]] = None, size: int = 30,
              gain: float = 1.0) -> Image.Image:
        """ One frame, with a dark square at (x, y) if asked, through the robot's JPEG compression. """
        pixels = self.background.copy()
        if square is not None:
            x, y = square
            pixels[y:y + size, x:x + size] = 25.0
        pixels = pixels * gain + self.random.normal(0.0, 3.0, pixels.shape)
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "L")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=50)
        buffer.seek(0)
        return Image.open(buffer)


class Film:
    """ Feeds frames to a detector at the camera's pace. """

    def __init__(self, detector: Optional[MotionDetector] = None) -> None:
        self.detector = detector or MotionDetector()
        self.now = 0.0

    def show(self, image: Image.Image, **kwargs: Any) -> Optional[motion_detection.ObservedMotion]:
        motion = self.detector.process(image, self.now, **kwargs)
        self.now += PERIOD
        return motion


class TestStillScene(unittest.TestCase):

    def test_the_first_image_cannot_be_compared(self):
        self.assertIsNone(Film().show(Scene().shoot()))

    def test_noise_and_compression_are_not_motion(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot())
        for _ in range(60):
            motion = film.show(scene.shoot())
            assert motion is not None
            self.assertFalse(motion.any, "a still scene reported {}".format(motion))
            self.assertLess(motion.area, 0.001)

    def test_a_change_of_exposure_is_not_motion(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot())
        motion = film.show(scene.shoot(gain=1.4))
        assert motion is not None
        self.assertFalse(motion.any)

    def test_a_dark_scene_is_not_motion(self):
        # Below the brightness floor, sensor noise is a large part of the signal.
        film = Film()
        film.show(Image.new("L", (WIDTH, HEIGHT), 4))
        motion = film.show(Image.new("L", (WIDTH, HEIGHT), 8))
        assert motion is not None
        self.assertFalse(motion.any)


class TestMovingObject(unittest.TestCase):

    def test_an_object_moving_is_seen_where_it_moves(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)))
        motion = film.show(scene.shoot(square=(150, 100)))
        assert motion is not None and motion.centroid is not None
        x, y = motion.centroid
        # The square covers 140-180 across its two positions, 100-130 down.
        self.assertTrue(140 <= x <= 180, x)
        self.assertTrue(100 <= y <= 130, y)
        self.assertGreater(motion.area, 0.005)

    def test_it_reports_the_image_size_and_timestamp(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot())
        motion = film.show(scene.shoot(square=(150, 100)), timestamp=1234)
        assert motion is not None
        self.assertEqual((motion.width, motion.height, motion.timestamp), (WIDTH, HEIGHT, 1234))

    def test_an_object_that_stops_is_no_longer_seen(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)))
        film.show(scene.shoot(square=(150, 100)))
        motion = film.show(scene.shoot(square=(150, 100)))
        assert motion is not None
        self.assertIsNone(motion.centroid)

    def test_a_tiny_movement_is_not_reported(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(150, 100), size=4))
        motion = film.show(scene.shoot(square=(154, 100), size=4))
        assert motion is not None
        self.assertIsNone(motion.centroid)

    def test_a_colour_image_is_read_too(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)).convert("RGB"))
        motion = film.show(scene.shoot(square=(150, 100)).convert("RGB"))
        assert motion is not None
        self.assertIsNotNone(motion.centroid)


class TestMovingCamera(unittest.TestCase):
    """ A camera that moves sees the whole scene move. """

    POSE = (0.0, 0.0, 0.0, 0.0)

    def test_nothing_is_compared_while_a_motor_moves(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)))
        self.assertIsNone(film.show(scene.shoot(square=(150, 100)), moving=True))

    def test_the_camera_is_left_to_settle(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(), moving=True)
        settle = film.detector.config.settle_time
        compared = []
        for i in range(10):
            compared.append(film.show(scene.shoot(square=(140 + 5 * i, 100))) is not None)
        # Frames are ignored until the settle time is up, and compared from the one after.
        first = compared.index(True)
        self.assertAlmostEqual(first * PERIOD, settle, delta=2 * PERIOD)
        self.assertTrue(all(compared[first:]))

    def test_a_turn_between_two_images_is_not_motion(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)), pose=self.POSE)
        self.assertIsNone(film.show(scene.shoot(square=(150, 100)), pose=(0.0, 0.0, 0.05, 0.0)))

    def test_nor_is_a_head_movement(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)), pose=self.POSE)
        self.assertIsNone(film.show(scene.shoot(square=(150, 100)), pose=(0.0, 0.0, 0.0, -0.05)))

    def test_nor_is_driving(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)), pose=self.POSE)
        self.assertIsNone(film.show(scene.shoot(square=(150, 100)), pose=(5.0, 0.0, 0.0, 0.0)))

    def test_a_heading_wrapping_around_is_not_a_turn(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)), pose=(0.0, 0.0, math.pi - 0.001, 0.0))
        motion = film.show(scene.shoot(square=(150, 100)), pose=(0.0, 0.0, -math.pi + 0.001, 0.0))
        self.assertIsNotNone(motion)

    def test_a_camera_at_rest_is_compared(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot(square=(140, 100)), pose=self.POSE)
        motion = film.show(scene.shoot(square=(150, 100)), pose=self.POSE)
        assert motion is not None
        self.assertIsNotNone(motion.centroid)


class TestPeripheralRegions(unittest.TestCase):

    def wiggle(self, film: Film, scene: Scene, x: int, y: int, frames: int) -> List[dict]:
        """ Move a square back and forth at (x, y), and collect what each region reported. """
        reported = []
        for i in range(frames):
            motion = film.show(scene.shoot(square=(x + 10 * (i % 2), y)))
            reported.append(dict(motion.regions) if motion is not None else {})
        return reported

    def test_motion_at_the_left_edge_is_reported_there(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot())
        reported = self.wiggle(film, scene, 20, 170, 6)
        self.assertEqual(set(reported[-1]), {"left"})
        x, y = reported[-1]["left"]
        self.assertTrue(20 <= x <= 60 and 170 <= y <= 200, (x, y))

    def test_each_region_reports_its_own_motion(self):
        for x, y, region in ((270, 170, "right"), (145, 10, "top")):
            with self.subTest(region=region):
                scene, film = Scene(), Film()
                film.show(scene.shoot())
                reported = self.wiggle(film, scene, x, y, 6)
                self.assertEqual(set(reported[-1]), {region})

    def test_motion_adds_up_before_it_is_reported(self):
        # The accumulator has to fill up before a region reports anything.
        config = MotionDetectorConfig(increase_factor=10.0)
        scene, film = Scene(), Film(MotionDetector(config))
        film.show(scene.shoot())
        motion = film.show(scene.shoot(square=(20, 170)))
        assert motion is not None
        self.assertEqual(motion.regions, {})

    def test_a_region_stops_reporting_once_the_motion_stops(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot())
        self.wiggle(film, scene, 20, 170, 6)
        still = scene.shoot(square=(20, 170))
        reported = [film.show(still) for _ in range(5)]
        assert reported[-1] is not None
        self.assertEqual(reported[-1].regions, {})

    def test_the_centre_is_no_region(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot())
        reported = self.wiggle(film, scene, 145, 170, 6)
        self.assertEqual(reported[-1], {})

    def test_moving_the_camera_empties_the_regions(self):
        scene, film = Scene(), Film()
        film.show(scene.shoot())
        self.wiggle(film, scene, 20, 170, 6)
        film.show(scene.shoot(), moving=True)
        self.assertEqual(film.detector._accumulators, dict.fromkeys(motion_detection.REGIONS, 0.0))


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAnkiConfiguration(unittest.TestCase):

    def test_the_parameters_are_read(self):
        config = motion_detection.load_motion_detector_config(str(pycozmo.util.get_cozmo_asset_dir()))
        self.assertEqual((config.horizontal_size, config.vertical_size, config.increase_factor,
                          config.decrease_factor, config.max_value, config.centroid_stability),
                         (0.3, 0.4, 100.0, 1.0, 3.0, 0.6))


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestBrain(unittest.TestCase):
    """ The brain runs the images the client receives through the detector. """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def setUp(self):
        self.brain.motion_detector.reset()
        self.observed: List[motion_detection.ObservedMotion] = []
        handler = self.brain.cli.add_handler(pycozmo.event.EvtMotionObserved,
                                             lambda cli, motion: self.observed.append(motion))
        self.addCleanup(self.brain.cli.del_handler, pycozmo.event.EvtMotionObserved, handler)

    def receive(self, image: Image.Image) -> None:
        self.brain.cli.dispatch(pycozmo.event.EvtNewRawCameraImage, self.brain.cli, image)

    def test_motion_is_announced(self):
        scene = Scene()
        self.receive(scene.shoot(square=(140, 100)))
        self.receive(scene.shoot(square=(150, 100)))
        self.assertEqual(len(self.observed), 1)
        self.assertIsNotNone(self.observed[0].centroid)

    def test_a_still_scene_announces_nothing(self):
        scene = Scene()
        for _ in range(5):
            self.receive(scene.shoot())
        self.assertEqual(self.observed, [])

    def test_nothing_is_announced_while_the_robot_moves(self):
        scene = Scene()
        self.brain.cli.robot_status = pycozmo.robot.RobotStatusFlag.IS_MOVING
        self.addCleanup(setattr, self.brain.cli, "robot_status", 0)
        self.receive(scene.shoot(square=(140, 100)))
        self.receive(scene.shoot(square=(150, 100)))
        self.assertEqual(self.observed, [])
