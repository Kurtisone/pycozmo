import math
import threading
import unittest

import numpy as np
from PIL import Image

import pycozmo
from pycozmo import cube_handling, util
from pycozmo.protocol_encoder import ObjectType

from .test_marker_detection import drawing, render, turn

CUBE2 = ObjectType.Block_LIGHTCUBE2


class TestDockPose(unittest.TestCase):

    def setUp(self):
        self.cubes = pycozmo.cubes.Cubes(pycozmo.client.Client())
        self.cube = self.cubes[CUBE2]

    def test_facing_the_side_seen_squarely(self):
        # A cube at (300, 100), seen by its side facing -x.
        self.cubes.place(self.cube, 300.0, 100.0, math.pi)
        pose = cube_handling.dock_pose(self.cube, cube_handling.DOCK_DISTANCE)
        self.assertAlmostEqual(pose.position.x, 300.0 - cube_handling.DOCK_DISTANCE)
        self.assertAlmostEqual(pose.position.y, 100.0)
        self.assertAlmostEqual(math.cos(pose.rotation.angle_z.radians), 1.0)

    def test_from_any_side(self):
        self.cubes.place(self.cube, 0.0, 0.0, math.pi / 2)
        pose = cube_handling.dock_pose(self.cube, 100.0)
        self.assertAlmostEqual(pose.position.x, 0.0)
        self.assertAlmostEqual(pose.position.y, 100.0)
        self.assertAlmostEqual(math.sin(pose.rotation.angle_z.radians), -1.0)

    def test_a_cube_never_seen_has_none(self):
        with self.assertRaises(ValueError):
            cube_handling.dock_pose(self.cube, 100.0)

    def test_the_fork_holds_a_cube_at_its_centre(self):
        # The fork reaches 58 mm ahead of the origin, by the robot's 3D model.
        self.assertEqual(cube_handling.DOCK_DISTANCE, 58.0 + 22.5)


class TestObserve(unittest.TestCase):

    def test_a_cube_in_the_next_image_is_placed(self):
        cli = pycozmo.client.Client()
        cli.head_angle = util.Angle(radians=0.0)
        # A Lamp 150 mm ahead of the camera, facing it.
        picture = drawing(CUBE2)
        image = Image.fromarray(render(turn(), (0.0, 0.0, 150.0), picture=picture).astype(np.uint8))
        threading.Timer(0.1, cli.dispatch, (pycozmo.event.EvtNewRawCameraImage, cli, image)).start()
        seen = cube_handling.observe(cli, timeout=2.0)
        self.assertEqual([cube.object_type for cube in seen], [CUBE2])
        cube = cli.cubes[CUBE2]
        assert cube.pose is not None
        # The camera is ahead of the origin, and the cube's centre half a side behind its marker.
        camera_x = pycozmo.camera.camera_to_robot(np.zeros(3), 0.0)[0]
        self.assertAlmostEqual(cube.pose.x, camera_x + 150.0 + 22.5, delta=2.0)
        self.assertAlmostEqual(cube.pose.y, 0.0, delta=2.0)
        self.assertAlmostEqual(abs(cube.pose.angle), math.pi, delta=math.radians(5.0))

    def test_no_image_nothing(self):
        self.assertEqual(cube_handling.observe(pycozmo.client.Client(), timeout=0.05), [])


class TestCancel(unittest.TestCase):

    def test_a_cancelled_step_stops(self):
        cli = pycozmo.client.Client()
        cube = cli.cubes[CUBE2]
        cli.cubes.place(cube, 200.0, 0.0, math.pi)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(cube_handling.Cancelled):
            cube_handling.go_to_cube(cli, cube, cancel=cancel)
