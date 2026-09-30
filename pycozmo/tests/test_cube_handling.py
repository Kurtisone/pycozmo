import math
import threading
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import pycozmo
from pycozmo import cube_handling, util
from pycozmo.protocol_encoder import ObjectType

from .test_marker_detection import drawing, render, turn

CUBE1 = ObjectType.Block_LIGHTCUBE1
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

    def test_the_distances_measured_on_a_robot(self):
        # Anki's engine, through its SDK: set down 50 mm ahead, picked up from 44.4, stacked from 36.1, rolled from
        # 31 and a wheelie from 30 - each 2.5 mm more as PyCozmo places cubes.
        self.assertEqual(cube_handling.DOCK_DISTANCE, 52.5)
        self.assertEqual((cube_handling.PICKUP_DISTANCE, cube_handling.PLACE_ON_DISTANCE, cube_handling.ROLL_DISTANCE,
                          cube_handling.WHEELIE_DISTANCE), (47.0, 38.5, 34.0, 32.5))


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


class TestPutDownBy(unittest.TestCase):

    def setUp(self):
        self.cli = pycozmo.client.Client()
        self.cube = self.cli.cubes[CUBE2]
        self.cli.cubes.carried = self.cube
        self.cli.lift_position = pycozmo.robot.LiftPosition(height=pycozmo.robot.MAX_LIFT_HEIGHT)

    def report(self, x, lift):
        """ Have the robot report where it stands and where its lift is, as its RobotState does. """
        self.cli.pose = util.Pose(x, 0.0, 0.0, angle_z=util.Angle(radians=0.0))
        self.cli.lift_position = pycozmo.robot.LiftPosition(height=util.Distance(mm=lift))
        self.cli.dispatch(pycozmo.event.EvtRobotStateUpdated, self.cli)

    def test_where_the_lift_came_down(self):
        # The animation drives, sets the cube down and pushes it on a little, backs off, and lowers the empty lift
        # once more.
        def animation():
            for x, lift in ((0.0, 92.0), (30.0, 60.0), (35.0, 32.0), (40.0, 32.0), (-20.0, 32.0), (-20.0, 92.0),
                            (-40.0, 32.0), (60.0, 32.0)):
                self.report(x, lift)

        self.assertTrue(cube_handling.put_down_by(self.cli, animation))
        assert self.cube.pose is not None
        self.assertAlmostEqual(self.cube.pose.x, 40.0 + cube_handling.DOCK_DISTANCE)
        self.assertIsNone(self.cli.cubes.carried)

    def test_a_lift_that_stays_up_says_so(self):
        self.assertFalse(cube_handling.put_down_by(self.cli, lambda: self.report(0.0, 92.0)))
        self.assertIs(self.cli.cubes.carried, self.cube)


class GestureClient:
    """ What the manoeuvres ask of a client, recorded: the lift, the wheels, stopping at cliffs, backing off. """

    def __init__(self) -> None:
        self.cubes = pycozmo.cubes.Cubes(self)
        self.pose = util.Pose(0.0, 0.0, 0.0, angle_z=util.Angle(radians=0.0))
        self.pose_pitch = util.Angle(radians=0.0)
        self.robot_status = 0
        self.orders: list = []
        self.handlers: list = []

    def set_lift_height(self, height, accel=10.0, max_speed=10.0, duration=0.0):
        self.orders.append(("lift", height))

    def drive_wheels(self, left, right, *args, **kwargs):
        self.orders.append(("wheels", left))

    def enable_stop_on_cliff(self, enable=True):
        self.orders.append(("cliff", enable))

    def drive_straight(self, distance, speed=None, **kwargs):
        self.orders.append(("drive", distance.mm))
        return True

    def add_handler(self, evt, f, one_shot=False):
        self.handlers.append((evt, f))
        return f

    def del_handler(self, evt, f):
        pass


class GestureTestCase(unittest.TestCase):

    go_to_cube: mock.Mock
    dock_with_cube: mock.Mock

    def setUp(self):
        self.cli = GestureClient()
        self.cube = self.cli.cubes[CUBE2]
        self.cli.cubes.place(self.cube, 200.0, 0.0, math.pi)
        self.seen_on_the_ground = False
        for name in ("go_to_cube", "dock_with_cube"):
            patcher = mock.patch.object(cube_handling, name, return_value=True)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

        def observe(cli, timeout=1.0):
            return [self.cube] if self.seen_on_the_ground else []

        seeing = mock.patch.object(cube_handling, "observe", side_effect=observe)
        seeing.start()
        self.addCleanup(seeing.stop)
        for name in ("SETTLE_TIME", "PICKUP_CREEP", "ROLL_PULL_TIME", "WHEELIE_TIME"):
            quick = mock.patch.object(cube_handling, name, 0.0)
            quick.start()
            self.addCleanup(quick.stop)

    def moves(self):
        """ The cube reports moving, as it does when the fork takes it. """
        for evt, f in self.cli.handlers:
            if evt is pycozmo.event.EvtCubeMovingChange:
                f(self.cli, self.cube, True)

    def instead(self, name, side_effect):
        """ Stand in for one of the client's methods. """
        patcher = mock.patch.object(self.cli, name, side_effect=side_effect)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def lifting_moves_it(self):
        """ The lift going up moves the cube, as the fork takes it or knocks it. """
        return self.instead("set_lift_height", lambda h, *a, **k: self.moves() if h > 50 else None)


class TestPickUp(GestureTestCase):

    def test_the_lift_rises_as_the_robot_creeps_on(self):
        self.lifting_moves_it()
        self.assertTrue(cube_handling.pick_up_cube(self.cli, self.cube))
        self.assertIs(self.cli.cubes.carried, self.cube)
        self.assertEqual(self.dock_with_cube.call_args.kwargs.get("distance", cube_handling.PICKUP_DISTANCE),
                         cube_handling.PICKUP_DISTANCE)

    def test_a_cube_left_on_the_ground_was_not_picked_up(self):
        # The fork knocked it, and it moved: but there it is, still in front of the robot.
        lift = self.lifting_moves_it()
        self.seen_on_the_ground = True
        self.assertFalse(cube_handling.pick_up_cube(self.cli, self.cube))
        self.assertIsNone(self.cli.cubes.carried)
        self.assertEqual(lift.call_args.args[0], pycozmo.robot.MIN_LIFT_HEIGHT.mm)


class TestPlaceOn(GestureTestCase):

    def test_let_go_at_76_mm_then_back_off_then_down(self):
        carried = self.cli.cubes[CUBE1]
        self.cli.cubes.carried = carried
        with mock.patch.object(cube_handling, "find_cube", return_value=True):
            self.assertTrue(cube_handling.place_on_cube(self.cli, self.cube))
        self.assertEqual(self.dock_with_cube.call_args.kwargs["distance"], cube_handling.PLACE_ON_DISTANCE)
        self.assertEqual(self.cli.orders, [("lift", 76.0), ("drive", -cube_handling.PLACE_ON_BACKOFF),
                                           ("lift", pycozmo.robot.MIN_LIFT_HEIGHT.mm)])
        assert carried.pose is not None
        self.assertAlmostEqual(carried.pose.z, 22.5 + 45.0)


class TestRoll(GestureTestCase):

    def test_hook_the_top_edge_then_pull_it_over(self):
        def rolled(left, right, *args, **kwargs):
            self.cli.orders.append(("wheels", left))
            if left < 0:
                self.cube.up_axis = pycozmo.protocol_encoder.UpAxis.XPositive

        self.instead("drive_wheels", rolled)
        self.assertTrue(cube_handling.roll_cube(self.cli, self.cube))
        self.assertEqual(self.dock_with_cube.call_args.kwargs["distance"], cube_handling.ROLL_DISTANCE)
        self.assertEqual(self.cli.orders, [
            ("lift", cube_handling.CARRY_HEIGHT), ("cliff", False), ("lift", cube_handling.ROLL_HOOK_HEIGHT),
            ("lift", pycozmo.robot.MIN_LIFT_HEIGHT.mm), ("wheels", -cube_handling.ROLL_PULL_SPEED), ("wheels", 0.0),
            ("cliff", True)])
        self.assertIsNone(self.cube.pose)

    def test_a_cube_that_did_not_turn_over_did_not_roll(self):
        self.assertFalse(cube_handling.roll_cube(self.cli, self.cube))


class TestWheelie(GestureTestCase):

    def test_down_hard_driving_on(self):
        def tipped(left, right, *args, **kwargs):
            self.cli.orders.append(("wheels", left))
            if left > 0:
                self.cli.pose_pitch = util.Angle(degrees=74.0)

        self.instead("drive_wheels", tipped)
        self.assertTrue(cube_handling.pop_a_wheelie(self.cli, self.cube))
        self.assertEqual(self.dock_with_cube.call_args.kwargs["distance"], cube_handling.WHEELIE_DISTANCE)
        self.assertEqual(self.cli.orders, [
            ("lift", cube_handling.CARRY_HEIGHT), ("cliff", False), ("lift", pycozmo.robot.MIN_LIFT_HEIGHT.mm),
            ("wheels", cube_handling.WHEELIE_SPEED), ("wheels", 0.0), ("cliff", True)])

    def test_still_on_its_treads(self):
        self.assertFalse(cube_handling.pop_a_wheelie(self.cli, self.cube))
