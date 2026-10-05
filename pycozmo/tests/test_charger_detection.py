"""

Tests for finding the charger's marker in camera images and placing it.

Images are drawn the way the camera would see the marker: every pixel's line of sight, through the lens of a real
robot's calibration, meets the marker's plane somewhere, and takes the brightness the drawing has there, dimmed, with a
little noise, on dark plastic.

"""

import math
import unittest
from typing import Optional

import numpy as np
from PIL import Image

from pycozmo import camera, charger_detection
from pycozmo.charger_detection import RING_HEIGHT, RING_WIDTH

CALIBRATION = camera.DEFAULT_CALIBRATION
WIDTH, HEIGHT = 320, 240
HEAD_ANGLE = math.radians(-5.0)
#: How far the camera is above the ground, and how high the marker's centre is, in mm.
MARKER_HEIGHT = 25.0


def render(distance: float, heading: float = math.pi, lateral: float = 0.0, head_angle: float = HEAD_ANGLE,
           brightness: float = 60.0, noise: float = 2.0, seed: int = 1) -> Image.Image:
    """
    The marker, as the camera sees it with its centre `distance` mm ahead of the robot's origin and `lateral` to the
    left, facing `heading` in the robot's frame.
    """
    centre = np.array([distance, lateral, MARKER_HEIGHT])
    right = np.array([-math.sin(heading), math.cos(heading), 0.0])
    down = np.array([0.0, 0.0, -1.0])
    normal = np.array([math.cos(heading), math.sin(heading), 0.0])
    u, v = np.meshgrid(np.arange(WIDTH, dtype=np.float64), np.arange(HEIGHT, dtype=np.float64))
    x, y = CALIBRATION.undistort(u, v)
    origin = camera.camera_to_robot(np.zeros(3), head_angle)
    rays = camera.camera_to_robot(np.stack([x, y, np.ones_like(x)], axis=-1), head_angle) - origin
    along = rays @ normal
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((centre - origin) @ normal) / along
    hits = origin + t[..., None] * rays - centre
    a, b = hits @ right, hits @ down
    template = charger_detection._template()
    mm = RING_WIDTH / charger_detection._RING_WIDTH
    # The drawing's own pixel for each point of the marker, inside the sticker only.
    column = a / mm + (template.shape[1] / 2.0)
    row = b / mm + (template.shape[0] / 2.0)
    inside = (t > 0) & (column >= 0) & (row >= 0) & (column <= template.shape[1] - 1.01) & \
        (row <= template.shape[0] - 1.01)
    image = np.full((HEIGHT, WIDTH), 12.0)
    sampled = np.zeros((HEIGHT, WIDTH))
    r0, c0 = np.floor(np.where(inside, row, 0)).astype(int), np.floor(np.where(inside, column, 0)).astype(int)
    fr, fc = np.where(inside, row, 0) - r0, np.where(inside, column, 0) - c0
    sampled = (template[r0, c0] * (1 - fr) * (1 - fc) + template[r0, c0 + 1] * (1 - fr) * fc +
               template[r0 + 1, c0] * fr * (1 - fc) + template[r0 + 1, c0 + 1] * fr * fc)
    image = np.where(inside, 12.0 + brightness * sampled, image)
    image = image + np.random.default_rng(seed).normal(0.0, noise, image.shape)
    return to_image(image)


def to_image(values: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(values, 0, 255).astype(np.uint8))


def observe(image: Image.Image, head_angle: float = HEAD_ANGLE) -> Optional[charger_detection.ObservedCharger]:
    return charger_detection.observe_charger(image, CALIBRATION, head_angle)


def seen(image: Image.Image) -> charger_detection.ObservedCharger:
    """ What the detector finds in an image, which has to be something. """
    found = observe(image)
    assert found is not None
    return found


class TestObserveCharger(unittest.TestCase):

    def test_the_drawing_has_the_size_asked_for(self):
        marker = charger_detection.draw_marker(40.0)
        # The ring is 40 px wide, the sticker round it a little more than that.
        self.assertTrue(40 < marker.shape[1] < 60)
        self.assertTrue(marker.min() >= 0.0 and marker.max() <= 1.0)
        squeezed = charger_detection.draw_marker(40.0, 0.5)
        self.assertAlmostEqual(squeezed.shape[1] / marker.shape[1], 0.5, delta=0.05)

    def test_a_marker_ahead_is_found_where_it_is(self):
        for distance in (200.0, 300.0, 400.0):
            for lateral in (-40.0, 0.0, 50.0):
                with self.subTest(distance=distance, lateral=lateral):
                    found = seen(render(distance, lateral=lateral))
                    self.assertAlmostEqual(found.position[0], distance, delta=0.04 * distance)
                    self.assertAlmostEqual(found.position[1], lateral, delta=6.0)
                    self.assertAlmostEqual(found.position[2], MARKER_HEIGHT, delta=12.0)
                    self.assertGreater(found.score, charger_detection.MIN_SCORE)

    def test_the_way_it_faces_is_told_from_the_side_it_is_seen_from(self):
        # Seen squarely the heading is hard to tell; from a way round the marker is narrower, and it is easy.
        for degrees in (-45.0, -30.0, 30.0, 45.0):
            with self.subTest(degrees=degrees):
                heading = math.pi + math.radians(degrees)
                found = seen(render(250.0, heading=heading))
                error = (found.facing - heading + math.pi) % (2.0 * math.pi) - math.pi
                self.assertLess(abs(math.degrees(error)), 8.0)

    def test_the_corners_are_those_of_the_ring(self):
        found = seen(render(250.0))
        corners = np.array(found.corners)
        width = np.linalg.norm(corners[1] - corners[0])
        height = np.linalg.norm(corners[3] - corners[0])
        # 24 mm at 250 mm, with a focal length of 297 px.
        self.assertAlmostEqual(width, 297.0 * RING_WIDTH / 250.0, delta=2.0)
        self.assertAlmostEqual(width / height, RING_WIDTH / RING_HEIGHT, delta=0.12)
        # Clockwise from the top left one.
        self.assertLess(corners[0][0], corners[1][0])
        self.assertLess(corners[0][1], corners[3][1])

    def test_a_dim_marker_is_found_as_well(self):
        for brightness in (25.0, 150.0):
            with self.subTest(brightness=brightness):
                self.assertIsNotNone(observe(render(250.0, brightness=brightness)))

    def test_a_marker_too_far_to_see_is_not_found(self):
        self.assertIsNone(observe(render(900.0)))

    def test_nothing_is_not_found(self):
        rng = np.random.default_rng(5)
        flat = to_image(rng.normal(40.0, 4.0, (HEIGHT, WIDTH)))
        self.assertIsNone(observe(flat))
        ramp = np.tile(np.linspace(10.0, 120.0, WIDTH), (HEIGHT, 1)) + rng.normal(0.0, 3.0, (HEIGHT, WIDTH))
        self.assertIsNone(observe(to_image(ramp)))

    def test_a_square_frame_with_a_symbol_is_not_the_marker(self):
        # What a cube's side looks like: a dark square ring on white, a dark mark in it.
        image = np.full((HEIGHT, WIDTH), 30.0)
        image[90:140, 120:170] = 200.0
        image[95:135, 125:165] = 20.0
        image[100:130, 130:160] = 200.0
        image[108:122, 138:152] = 20.0
        image += np.random.default_rng(2).normal(0.0, 2.0, image.shape)
        self.assertIsNone(observe(to_image(image)))

    def test_what_is_known_to_be_something_else_is_avoided(self):
        frame = np.array(seen(render(250.0)).corners)
        self.assertIsNone(charger_detection.observe_charger(render(250.0), CALIBRATION, HEAD_ANGLE, avoid=[frame]))

    def test_a_match_not_at_the_height_of_the_marker_is_not_taken_for_it(self):
        # The same picture, with the head 0.3 rad out: the marker's fit goes under the floor, or high up in the
        # air, and is not the marker.
        image = render(250.0)
        self.assertIsNotNone(charger_detection.observe_charger(image, CALIBRATION, HEAD_ANGLE))
        self.assertIsNone(charger_detection.observe_charger(image, CALIBRATION, HEAD_ANGLE + 0.3))
        self.assertIsNone(charger_detection.observe_charger(image, CALIBRATION, HEAD_ANGLE - 0.3))
