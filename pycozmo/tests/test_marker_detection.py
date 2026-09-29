"""

Tests for finding cube marker frames in camera images and placing them.

Images are drawn the way the camera would see a marker: every pixel's line of sight, through the lens of a
real robot's calibration, meets the marker's plane somewhere, and takes the brightness there.

"""

import math
import os
import unittest
from typing import Any, Optional, Tuple

import numpy as np
from PIL import Image

import pycozmo
from pycozmo import camera, marker_detection
from pycozmo.marker_detection import CUBE_MARKERS, MARKER_SIZE

CALIBRATION = camera.DEFAULT_CALIBRATION

# Frames found on a real robot, with the head angle and the robot's tilt they were filmed at. The cube stood
# 100 mm ahead of the treads, where the ground under it put its near side 119 mm ahead of the origin.
ROBOT_FRAMES = (
    (-0.2918010652065277, 0.015123814344406128,
     [(116.2, 22.39), (187.26, 21.67), (186.87, 91.4), (119.74, 92.69)]),
    (-0.2059856355190277, 0.01767020672559738,
     [(117.05, 53.46), (186.95, 52.84), (187.0, 120.67), (119.61, 122.06)]),
    (-0.12017020583152771, 0.022273676469922066,
     [(117.45, 84.35), (187.0, 83.05), (186.99, 150.97), (119.14, 152.81)]),
    (-0.02532157301902771, 0.028170984238386154,
     [(117.57, 118.06), (186.8, 116.61), (187.98, 185.95), (117.84, 187.78)]),
)


def turn(yaw: float = 0.0, tilt: float = 0.0) -> np.ndarray:
    """ Axes of a marker facing the camera, turned by yaw about the vertical and tilted back by tilt. """
    cy, sy = math.cos(yaw), math.sin(yaw)
    ct, st = math.cos(tilt), math.sin(tilt)
    yaw_m = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    tilt_m = np.array([[1.0, 0.0, 0.0], [0.0, ct, -st], [0.0, st, ct]])
    rotation: np.ndarray = yaw_m @ tilt_m
    return rotation


def drawing(cube: pycozmo.protocol_encoder.ObjectType, turns: int = 0, bar: bool = True) -> np.ndarray:
    """
    Anki's drawing of a cube's marker, turned quarter turns clockwise, as brightness from 0 to 1. The
    stickers also have a bar under the symbol, which the drawings do not.
    """
    path = os.path.join(os.path.dirname(marker_detection.__file__), "cube_markers", "{}.png".format(cube.value))
    pixels = np.asarray(Image.open(path).convert("L"), dtype=np.float64) / 255.0
    if bar:
        pixels[196:206, 72:184] = 0.1
    turned: np.ndarray = np.rot90(pixels, -turns)
    return turned


def marker_texture(x: np.ndarray, y: np.ndarray, size: float, symbol: bool = True, ring: bool = True,
                   dark: float = 40.0, light: float = 200.0, background: float = 90.0,
                   picture: Optional[np.ndarray] = None) -> np.ndarray:
    """
    Brightness on a cube side at points x, y in mm from the marker's centre, x right and y down: a frame and
    a bar across it, or a picture of the marker from dark to light.
    """
    face = 45.0
    radius = 0.12 * size
    thickness = 0.1 * size

    def rounded_square(half: float, r: float) -> np.ndarray:
        dx = np.maximum(np.abs(x) - (half - r), 0.0)
        dy = np.maximum(np.abs(y) - (half - r), 0.0)
        inside: np.ndarray = (np.abs(x) <= half) & (np.abs(y) <= half) & (dx * dx + dy * dy <= r * r)
        return inside

    out = np.full(x.shape, background)
    out[(np.abs(x) <= face / 2) & (np.abs(y) <= face / 2)] = light
    if picture is not None:
        side = picture.shape[0]
        inside = (np.abs(x) < size / 2) & (np.abs(y) < size / 2)
        u = np.clip(((x[inside] / size + 0.5) * side).astype(int), 0, side - 1)
        v = np.clip(((y[inside] / size + 0.5) * side).astype(int), 0, side - 1)
        out[inside] = dark + (light - dark) * picture[v, u]
        return out
    outer = rounded_square(size / 2, radius)
    inner = rounded_square(size / 2 - thickness, max(radius - thickness, 0.5))
    out[outer & (~inner if ring else True)] = dark
    if symbol:
        bar = (np.abs(x - y) < 0.08 * size) & (np.abs(x) < 0.22 * size) & (np.abs(y) < 0.22 * size)
        out[bar] = dark
    return out


def render(rotation: np.ndarray, centre: Tuple[float, float, float], size: float = MARKER_SIZE,
           brightness: float = 1.0, noise: float = 3.0, supersampling: int = 6,
           seed: int = 0, **texture: Any) -> np.ndarray:
    """
    What the camera sees of a marker with these axes and centre, in the camera's frame in mm.

    Each pixel over the cube's side averages supersampling squared lines of sight, which places an edge to
    within half a sample: a twelfth of a pixel with six. Three placed them up to a sixth off, more than the
    detector is off by.
    """
    width, height = CALIBRATION.width, CALIBRATION.height
    background = texture.get("background", 90.0)
    image = np.full((height, width), background)
    # The cube's side, where anything but background is.
    face = projected_corners(rotation, centre, 45.0 + 4.0)
    left, top = np.floor(face.min(axis=0)).astype(int)
    right, bottom = np.ceil(face.max(axis=0)).astype(int)
    left, top = max(left, 0), max(top, 0)
    right, bottom = min(right, width - 1), min(bottom, height - 1)
    if right >= left and bottom >= top:
        columns, rows = right - left + 1, bottom - top + 1
        offsets = (np.arange(supersampling) + 0.5) / supersampling - 0.5
        u = np.arange(left, right + 1)[None, :, None, None] + offsets[None, None, None, :] \
            + np.zeros((rows, 1, supersampling, 1))
        v = np.arange(top, bottom + 1)[:, None, None, None] + offsets[None, None, :, None] \
            + np.zeros((1, columns, 1, supersampling))
        x, y = CALIBRATION.undistort(u.ravel(), v.ravel())
        rays = np.stack([x, y, np.ones_like(x)], axis=1)
        normal = rotation[:, 2]
        t = np.asarray(centre, dtype=np.float64)
        along = rays @ normal
        valid = np.abs(along) > 1e-9
        depth = np.where(valid, (normal @ t) / np.where(valid, along, 1.0), -1.0)
        local = (rays * depth[:, None] - t) @ rotation
        values = marker_texture(local[:, 0], local[:, 1], size, **texture)
        values[depth <= 0] = background
        image[top:bottom + 1, left:right + 1] = values.reshape(rows, columns, supersampling, supersampling) \
            .mean(axis=(2, 3))
    image = image * brightness + np.random.default_rng(seed).normal(0.0, noise, image.shape)
    clipped: np.ndarray = np.clip(image, 0, 255)
    return clipped


def projected_corners(rotation: np.ndarray, centre: Tuple[float, float, float],
                      size: float = MARKER_SIZE) -> np.ndarray:
    half = size / 2
    square = np.array([(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0), (-half, half, 0.0)])
    points = square @ rotation.T + np.asarray(centre)
    u, v = CALIBRATION.distort(points[:, 0] / points[:, 2], points[:, 1] / points[:, 2])
    corners: np.ndarray = np.stack([u, v], axis=1)
    return corners


def only_frame(image: np.ndarray) -> Optional[np.ndarray]:
    frames = marker_detection.find_frames(image, CALIBRATION)
    return frames[0] if len(frames) == 1 else None


class TestFinding(unittest.TestCase):

    def test_the_corners_of_a_marker_facing_the_camera(self):
        rotation = turn()
        frame = only_frame(render(rotation, (0.0, 0.0, 150.0)))
        assert frame is not None
        # A twelfth of a pixel of it is how the image is drawn.
        np.testing.assert_allclose(frame, projected_corners(rotation, (0.0, 0.0, 150.0)), atol=0.2)

    def test_the_corners_of_a_marker_turned_and_off_centre(self):
        for yaw, tilt, centre in ((0.5, 0.0, (30.0, 10.0, 200.0)), (-0.6, 0.3, (-40.0, -20.0, 180.0)),
                                  (0.2, -0.4, (10.0, 30.0, 120.0))):
            with self.subTest(yaw=yaw, tilt=tilt):
                rotation = turn(yaw, tilt)
                frame = only_frame(render(rotation, centre))
                assert frame is not None
                np.testing.assert_allclose(frame, projected_corners(rotation, centre), atol=0.2)

    def test_the_corners_come_clockwise_from_the_top_left(self):
        # A marker on its side still starts from the corner nearest the top left of the screen.
        rotation = turn() @ np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        frame = only_frame(render(rotation, (0.0, 0.0, 150.0)))
        assert frame is not None
        self.assertLess(frame[0].sum(), frame[2].sum())
        self.assertLess(frame[0][0], frame[1][0])
        self.assertLess(frame[1][1], frame[2][1])

    def test_a_dim_marker_is_found(self):
        self.assertIsNotNone(only_frame(render(turn(), (0.0, 0.0, 150.0), brightness=0.35, noise=1.5)))

    def test_a_far_marker_is_found(self):
        self.assertIsNotNone(only_frame(render(turn(), (0.0, 0.0, 450.0))))

    def test_a_marker_cut_off_by_the_edge_is_not(self):
        # Its corners are not in the image.
        self.assertEqual(marker_detection.find_frames(render(turn(), (-60.0, 0.0, 120.0))), [])

    def test_a_dark_square_is_not_a_marker(self):
        self.assertEqual(marker_detection.find_frames(render(turn(), (0.0, 0.0, 150.0), ring=False)), [])

    def test_the_symbol_inside_is_not_a_marker_of_its_own(self):
        self.assertEqual(len(marker_detection.find_frames(render(turn(), (0.0, 0.0, 100.0)))), 1)

    def test_nothing_in_a_blank_image(self):
        blank = np.random.default_rng(1).normal(120.0, 3.0, (240, 320))
        self.assertEqual(marker_detection.find_frames(blank), [])


class TestIdentity(unittest.TestCase):

    POSES = ((turn(), (0.0, 0.0, 150.0)), (turn(0.5, 0.0), (30.0, 10.0, 200.0)),
             (turn(-0.6, 0.3), (-40.0, -20.0, 180.0)), (turn(), (0.0, 0.0, 350.0)))

    def identify(self, image: np.ndarray) -> Optional[Tuple[pycozmo.protocol_encoder.ObjectType, int]]:
        frame = only_frame(image)
        assert frame is not None
        return marker_detection.identify(image, frame)

    def test_each_cube_is_told_whichever_way_it_is_turned(self):
        for cube in CUBE_MARKERS:
            for turns in range(4):
                for rotation, centre in self.POSES:
                    with self.subTest(cube=cube.name, turns=turns, centre=centre):
                        image = render(rotation, centre, picture=drawing(cube, turns))
                        self.assertEqual(self.identify(image), (cube, turns))

    def test_a_frame_around_another_symbol_holds_no_cube(self):
        for rotation, centre in self.POSES:
            with self.subTest(centre=centre):
                self.assertIsNone(self.identify(render(rotation, centre)))

    def test_an_empty_frame_holds_no_cube(self):
        picture = drawing(pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE1, bar=False)
        picture[40:216, 40:216] = picture[128, 30]
        self.assertIsNone(self.identify(render(turn(), (0.0, 0.0, 150.0), picture=picture)))

    def test_observed_markers_say_their_cube(self):
        cube = pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE2
        image = Image.fromarray(render(turn(), (0.0, 0.0, 150.0), picture=drawing(cube, 1)).astype(np.uint8))
        observed = marker_detection.observe_markers(image, CALIBRATION, 0.0)
        self.assertEqual([(marker.cube, marker.turns) for marker in observed], [(cube, 1)])


class TestPose(unittest.TestCase):

    def test_where_a_marker_is(self):
        for yaw, tilt, centre in ((0.0, 0.0, (0.0, 0.0, 150.0)), (0.5, 0.0, (30.0, 10.0, 200.0)),
                                  (-0.6, 0.3, (-40.0, -20.0, 180.0)), (0.0, 0.0, (0.0, 0.0, 400.0))):
            with self.subTest(yaw=yaw, tilt=tilt, centre=centre):
                rotation = turn(yaw, tilt)
                frame = only_frame(render(rotation, centre))
                assert frame is not None
                found_rotation, found_centre = marker_detection.frame_pose(frame, CALIBRATION)
                # Half a millimetre, or a third of a percent of the distance.
                np.testing.assert_allclose(found_centre, centre, atol=max(0.5, 0.003 * centre[2]))
                # Which way a small square faces shows in how its sides converge, which a tenth of a pixel
                # changes by degrees.
                facing = math.degrees(math.acos(np.clip(found_rotation[:, 2] @ rotation[:, 2], -1, 1)))
                self.assertLess(facing, 5.0)

    def test_a_marker_in_the_robot_s_frame(self):
        # Head 10 degrees down, a marker standing 150 mm ahead and 20 mm to the left, 22 mm up, facing the
        # robot: in the camera's frame, x right, y down, z ahead.
        head = math.radians(-10.0)
        target = np.array([150.0, 20.0, 22.0])
        origin = camera.camera_to_robot(np.zeros(3), head)
        axes = np.stack([camera.camera_to_robot(np.eye(3)[i], head) - origin for i in range(3)])
        centre = axes @ (target - origin)
        # The marker's axes: x to the robot's right, y down, z away from the robot.
        rotation = np.stack([axes @ np.array([0.0, -1.0, 0.0]), axes @ np.array([0.0, 0.0, -1.0]),
                             axes @ np.array([1.0, 0.0, 0.0])], axis=1)
        image = Image.fromarray(render(rotation, tuple(centre)).astype(np.uint8))
        observed = marker_detection.observe_markers(image, CALIBRATION, head)
        self.assertEqual(len(observed), 1)
        np.testing.assert_allclose(observed[0].position, target, atol=1.0)
        self.assertAlmostEqual(abs(observed[0].facing), math.pi, delta=math.radians(3.0))
        self.assertAlmostEqual(observed[0].distance, float(np.linalg.norm(centre)), delta=1.0)

    def test_on_a_robot(self):
        # Wherever the head was, the marker is where the cube stood, and halfway up its 45 mm side.
        for head, pitch, corners in ROBOT_FRAMES:
            with self.subTest(head=round(math.degrees(head), 1)):
                _, centre = marker_detection.frame_pose(np.array(corners), CALIBRATION)
                position = camera.camera_to_robot(centre, head, pitch)
                self.assertAlmostEqual(position[0], 119.0, delta=0.8)
                self.assertAlmostEqual(position[2], 24.0, delta=0.8)
