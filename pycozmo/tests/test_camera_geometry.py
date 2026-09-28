"""

Tests for the camera's geometry: its calibration, and where on the ground it sees.

The ground tests are held to measurements of a robot: a Light Cube standing 100 mm ahead of the robot's
treads, filmed at seven head angles, and the image row where its near face met the table in each.

"""

import math
import struct
import unittest
from typing import List, Tuple

import numpy as np

import pycozmo
from pycozmo import camera, protocol_encoder


#: NVEntry_CameraCalib, as read from a robot.
ROBOT_CALIBRATION = bytes.fromhex(
    "045f9443b7d492432a882743f9f5de4200000000f00040012f498cbd20f2623f"
    "23e8edba463e14bb201103c0000000000000000000000000")

#: Head angle and pitch in degrees, and the row of a cube's near face on the table, 100 mm ahead of the
#: treads, at column 155 of a 320 x 240 image. The last one repeats the first after the others.
CUBE_OBSERVATIONS = [(-23.96, 1.42, 77.5), (-21.12, 0.60, 89.0), (-16.72, 0.87, 116.0),
                     (-11.80, 1.01, 146.0), (-6.89, 1.28, 177.0), (-1.45, 1.61, 213.0),
                     (-24.22, 1.62, 77.0)]


def distort(calibration: camera.CameraCalibration, x: float, y: float) -> Tuple[float, float]:
    """ Where a lens with this calibration puts a point given in normalized coordinates, in pixels. """
    k1, k2, p1, p2, k3 = calibration.distortion[:5]
    r2 = x * x + y * y
    radial = 1.0 + k1 * r2 + k2 * r2 ** 2 + k3 * r2 ** 3
    xd = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
    yd = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
    return calibration.cx + calibration.fx * xd, calibration.cy + calibration.fy * yd


def on_ground(calibration, u, v, head_deg, pitch_deg=0.0):
    x, y = calibration.undistort(np.array([u]), np.array([v]))
    ground_x, ground_y, reaches = camera.ground_points(x, y, math.radians(head_deg), math.radians(pitch_deg))
    return float(ground_x[0]), float(ground_y[0]), bool(reaches[0])


class TestCalibration(unittest.TestCase):

    def test_a_robots_calibration_is_read(self):
        calibration = camera.CameraCalibration.from_nv(ROBOT_CALIBRATION)
        self.assertEqual((calibration.width, calibration.height), (320, 240))
        self.assertAlmostEqual(calibration.fx, 296.74, places=2)
        self.assertAlmostEqual(calibration.fy, 293.66, places=2)
        self.assertAlmostEqual(calibration.cx, 167.53, places=2)
        self.assertAlmostEqual(calibration.cy, 111.48, places=2)
        self.assertEqual(len(calibration.distortion), 8)
        self.assertEqual(calibration.distortion[5:], (0.0, 0.0, 0.0))

    def test_the_field_of_view_is_a_cozmos(self):
        calibration = camera.CameraCalibration.from_nv(ROBOT_CALIBRATION)
        horizontal = 2.0 * math.degrees(math.atan(calibration.width / 2.0 / calibration.fx))
        vertical = 2.0 * math.degrees(math.atan(calibration.height / 2.0 / calibration.fy))
        self.assertAlmostEqual(horizontal, 56.7, places=1)
        self.assertAlmostEqual(vertical, 44.5, places=1)

    def test_the_default_is_that_robots(self):
        calibration = camera.CameraCalibration.from_nv(ROBOT_CALIBRATION)
        default = camera.DEFAULT_CALIBRATION
        for name in ("fx", "fy", "cx", "cy"):
            self.assertAlmostEqual(getattr(default, name), getattr(calibration, name), places=3)
        np.testing.assert_allclose(default.distortion, calibration.distortion, atol=1e-6)

    def test_a_wrong_size_is_refused(self):
        with self.assertRaises(ValueError):
            camera.CameraCalibration.from_nv(ROBOT_CALIBRATION[:-4])

    def test_the_optical_centre_is_on_the_axis(self):
        calibration = camera.DEFAULT_CALIBRATION
        x, y = calibration.undistort(np.array([calibration.cx]), np.array([calibration.cy]))
        self.assertAlmostEqual(float(x[0]), 0.0)
        self.assertAlmostEqual(float(y[0]), 0.0)

    def test_undistorting_undoes_the_lens(self):
        calibration = camera.DEFAULT_CALIBRATION
        for x, y in ((0.3, 0.2), (-0.45, 0.35), (0.1, -0.38), (-0.5, -0.3)):
            with self.subTest(x=x, y=y):
                u, v = distort(calibration, x, y)
                ux, uy = calibration.undistort(np.array([u]), np.array([v]))
                self.assertAlmostEqual(float(ux[0]), x, places=5)
                self.assertAlmostEqual(float(uy[0]), y, places=5)

    def test_distorting_is_what_the_model_says(self):
        calibration = camera.DEFAULT_CALIBRATION
        for x, y in ((0.3, 0.2), (-0.45, 0.35), (0.1, -0.38), (-0.5, -0.3)):
            with self.subTest(x=x, y=y):
                u, v = calibration.distort(np.array([x]), np.array([y]))
                np.testing.assert_allclose((float(u[0]), float(v[0])), distort(calibration, x, y), atol=1e-9)

    def test_undistorting_reaches_across_the_image(self):
        calibration = camera.DEFAULT_CALIBRATION
        u, v = np.meshgrid(np.arange(320.0), np.arange(240.0))
        x, y = calibration.undistort(u, v)
        back_u, back_v = calibration.distort(x, y)
        error = np.hypot(back_u - u, back_v - v)
        # All but the very corners, which lie beyond where the calibration's model folds back.
        self.assertGreater((error < 1e-6).mean(), 0.99)
        self.assertTrue((error[20:220, 20:300] < 1e-6).all())

    def test_the_corners_of_the_image_keep_their_direction(self):
        # The fixed point iteration this replaced diverged there: pixel (0, 239) came out at the centre,
        # which put what the bottom left corner of the image saw where the middle of the image looks.
        calibration = camera.DEFAULT_CALIBRATION
        for u, v in ((0.0, 0.0), (0.0, 239.0), (319.0, 239.0), (319.0, 0.0)):
            with self.subTest(pixel=(u, v)):
                x, y = calibration.undistort(np.array([u]), np.array([v]))
                direction = math.atan2((v - calibration.cy) / calibration.fy, (u - calibration.cx) / calibration.fx)
                self.assertAlmostEqual(math.atan2(y[0], x[0]), direction, delta=0.05)
                self.assertGreater(math.hypot(x[0], y[0]), 0.6)
                back_u, back_v = calibration.distort(x, y)
                self.assertLess(math.hypot(back_u[0] - u, back_v[0] - v), 15.0)

    def test_it_scales_to_another_resolution(self):
        half = camera.DEFAULT_CALIBRATION.scaled(160, 120)
        self.assertAlmostEqual(half.fx, camera.DEFAULT_CALIBRATION.fx / 2.0)
        self.assertAlmostEqual(half.cy, camera.DEFAULT_CALIBRATION.cy / 2.0)
        self.assertEqual(half.distortion, camera.DEFAULT_CALIBRATION.distortion)


class TestGround(unittest.TestCase):

    def setUp(self):
        self.calibration = camera.CameraCalibration.from_nv(ROBOT_CALIBRATION)

    def test_a_cube_is_seen_at_the_same_place_from_every_head_angle(self):
        distances = [on_ground(self.calibration, 155.0, v, head, pitch)[0] for head, pitch, v in CUBE_OBSERVATIONS]
        self.assertLess(np.std(distances), 1.5, distances)

    def test_it_is_where_it_was_put(self):
        # 100 mm ahead of the treads, whose front is about 19 mm ahead of the origin.
        for head, pitch, v in CUBE_OBSERVATIONS:
            with self.subTest(head=head):
                self.assertAlmostEqual(on_ground(self.calibration, 155.0, v, head, pitch)[0], 119.0, delta=2.5)

    def test_leaving_the_pitch_out_is_worse(self):
        # The robot's own tilt counts, and in the same direction as the head's.
        with_pitch = [on_ground(self.calibration, 155.0, v, head, pitch)[0] for head, pitch, v in CUBE_OBSERVATIONS]
        without = [on_ground(self.calibration, 155.0, v, head)[0] for head, pitch, v in CUBE_OBSERVATIONS]
        self.assertLess(np.std(with_pitch), np.std(without))

    def test_left_of_the_image_is_left_of_the_robot(self):
        left = on_ground(self.calibration, 60.0, 200.0, -10.0)
        right = on_ground(self.calibration, 260.0, 200.0, -10.0)
        self.assertGreater(left[1], 0.0)
        self.assertLess(right[1], 0.0)

    def test_lower_in_the_image_is_closer(self):
        near = on_ground(self.calibration, 160.0, 220.0, -10.0)
        far = on_ground(self.calibration, 160.0, 150.0, -10.0)
        self.assertLess(near[0], far[0])

    def test_above_the_horizon_is_not_ground(self):
        # With the head 20 degrees up, the horizon is near the bottom of the image.
        self.assertFalse(on_ground(self.calibration, 160.0, 20.0, 20.0)[2])
        self.assertFalse(on_ground(self.calibration, 160.0, 200.0, 20.0)[2])
        self.assertTrue(on_ground(self.calibration, 160.0, 239.0, 20.0)[2])
        self.assertGreater(on_ground(self.calibration, 160.0, 239.0, 20.0)[0], 500.0)


class TestCameraToRobot(unittest.TestCase):

    def test_the_camera_s_own_position(self):
        # Head level: the camera 17.52 mm ahead of the neck joint and 8 mm below it.
        position = camera.camera_to_robot(np.zeros(3), 0.0)
        np.testing.assert_allclose(position, (-13.0 + 17.52, 0.0, 47.7 - 8.0))

    def test_its_axes(self):
        # Head level: the optical axis is ahead, the image's x the robot's right, its y down.
        origin = camera.camera_to_robot(np.zeros(3), 0.0)
        np.testing.assert_allclose(camera.camera_to_robot(np.array([0.0, 0.0, 1.0]), 0.0) - origin, (1, 0, 0))
        np.testing.assert_allclose(camera.camera_to_robot(np.array([1.0, 0.0, 0.0]), 0.0) - origin, (0, -1, 0))
        np.testing.assert_allclose(camera.camera_to_robot(np.array([0.0, 1.0, 0.0]), 0.0) - origin, (0, 0, -1))

    def test_a_point_on_the_ground_is_on_the_ground(self):
        head, pitch = math.radians(-20.0), math.radians(1.5)
        x, y = np.array([0.1, -0.2, 0.0]), np.array([0.3, 0.25, 0.4])
        ground_x, ground_y, reaches = camera.ground_points(x, y, head, pitch)
        self.assertTrue(reaches.all())
        # Along each line of sight, at the depth that reaches the ground.
        origin = camera.camera_to_robot(np.zeros(3), head, pitch)
        for xi, yi, gx, gy in zip(x, y, ground_x, ground_y):
            direction = camera.camera_to_robot(np.array([xi, yi, 1.0]), head, pitch) - origin
            point = origin + direction * (-origin[2] / direction[2])
            np.testing.assert_allclose(point, (gx, gy, 0.0), atol=1e-9)


class TestReadingTheCalibration(unittest.TestCase):
    """ The client asks the robot for its calibration, and the robot answers through NvStorageOpResult. """

    def answer(self, *results: Tuple[protocol_encoder.NvResult, bytes]) -> pycozmo.client.Client:
        """ A client whose robot answers a read with these (result, data) pairs, in order. """
        cli = pycozmo.client.Client()
        sent: List[pycozmo.protocol_base.Packet] = []

        def send(pkt: pycozmo.protocol_base.Packet) -> None:
            sent.append(pkt)
            for result, data in results:
                cli.conn.dispatch(protocol_encoder.NvStorageOpResult, cli.conn, protocol_encoder.NvStorageOpResult(
                    tag=protocol_encoder.NvEntryTag.NVEntry_CameraCalib, length=0,
                    op=protocol_encoder.NvOperation.NVOP_READ, result=result, data=data))

        cli.conn.send = send  # type: ignore[method-assign]
        self.sent = sent
        return cli

    def test_the_robots_calibration_is_returned(self):
        cli = self.answer((protocol_encoder.NvResult.NV_OKAY, ROBOT_CALIBRATION))
        calibration = cli.read_camera_calibration(timeout=1.0)
        assert calibration is not None
        self.assertAlmostEqual(calibration.fx, 296.74, places=2)
        request = self.sent[0]
        assert isinstance(request, protocol_encoder.NvStorageOp)
        self.assertEqual(request.tag, protocol_encoder.NvEntryTag.NVEntry_CameraCalib)

    def test_an_answer_in_several_parts_is_put_together(self):
        cli = self.answer((protocol_encoder.NvResult.NV_MORE, ROBOT_CALIBRATION[:30]),
                          (protocol_encoder.NvResult.NV_OKAY, ROBOT_CALIBRATION[30:]))
        calibration = cli.read_camera_calibration(timeout=1.0)
        assert calibration is not None
        self.assertAlmostEqual(calibration.cy, 111.48, places=2)

    def test_a_robot_without_one_gives_none(self):
        cli = self.answer((protocol_encoder.NvResult.NV_NOT_FOUND, b""))
        self.assertIsNone(cli.read_camera_calibration(timeout=1.0))

    def test_a_robot_that_does_not_answer_gives_none(self):
        cli = self.answer()
        self.assertIsNone(cli.read_camera_calibration(timeout=0.1))

    def test_a_garbled_one_gives_none(self):
        cli = self.answer((protocol_encoder.NvResult.NV_OKAY, ROBOT_CALIBRATION[:20]))
        self.assertIsNone(cli.read_camera_calibration(timeout=1.0))

    def test_the_format_is_what_the_robot_stores(self):
        self.assertEqual(struct.calcsize(camera.CameraCalibration.NV_FORMAT), len(ROBOT_CALIBRATION))
