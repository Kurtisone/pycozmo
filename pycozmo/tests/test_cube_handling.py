import math
import threading
import unittest
from typing import Tuple
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

    def test_a_quarter_turn_round(self):
        # Seen by its side facing -x; the next side anticlockwise faces -y, the one before +y.
        self.cubes.place(self.cube, 300.0, 100.0, math.pi)
        pose = cube_handling.dock_pose(self.cube, 100.0, side=1)
        self.assertAlmostEqual(pose.position.x, 300.0)
        self.assertAlmostEqual(pose.position.y, 0.0)
        self.assertAlmostEqual(math.sin(pose.rotation.angle_z.radians), 1.0)
        pose = cube_handling.dock_pose(self.cube, 100.0, side=-1)
        self.assertAlmostEqual(pose.position.y, 200.0)

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


class TestDockError(unittest.TestCase):

    def test_squarely_in_front(self):
        # The marker 70 mm ahead, facing the robot: the cube's centre is half a side behind it.
        along, left, turn = cube_handling.dock_error((70.0, 0.0, 22.5), (-1.0, 0.0, 0.0))
        self.assertAlmostEqual(along, 70.0 + 22.5)
        self.assertAlmostEqual(left, 0.0)
        self.assertAlmostEqual(turn, 0.0)

    def test_the_robot_to_one_side_of_the_line(self):
        # A cube to the robot's left is a robot to the right of the line it makes, looking at the cube.
        _, left, _ = cube_handling.dock_error((70.0, 20.0, 22.5), (-1.0, 0.0, 0.0))
        self.assertAlmostEqual(left, -20.0)
        _, left, _ = cube_handling.dock_error((70.0, -20.0, 22.5), (-1.0, 0.0, 0.0))
        self.assertAlmostEqual(left, 20.0)

    def test_the_robot_turned_from_the_line(self):
        # The side faces the robot's left a little: the robot has to turn that way to face along it.
        _, _, turn = cube_handling.dock_error((70.0, 0.0, 22.5), (-math.cos(0.2), -math.sin(0.2), 0.0))
        self.assertAlmostEqual(turn, 0.2)
        _, _, turn = cube_handling.dock_error((70.0, 0.0, 22.5), (-math.cos(0.2), math.sin(0.2), 0.0))
        self.assertAlmostEqual(turn, -0.2)


class TestServo(unittest.TestCase):
    """ The steering, with a robot that is only a pair of wheels and a marker seen a moment late. """

    DISTANCE = cube_handling.PICKUP_DISTANCE
    STEP = 0.1
    DELAY = 2

    def drive_in(self, along: float, side: float, turn: float) -> Tuple[float, float, float]:
        """
        A cube's centre at the origin, its side facing along +x; the robot that far along, that far to the side
        and turned that much off facing it. Drive in until the robot is where it should be; say where it ended up,
        and how it faces.
        """
        x, y, heading = along, side, math.pi + turn
        width = pycozmo.robot.TRACK_WIDTH.mm
        sights: list = []
        for _ in range(400):
            # The cube as the robot sees it, in its own frame: ahead, to its left.
            c, s = math.cos(-heading), math.sin(-heading)
            centre = (c * (0 - x) - s * (0 - y), s * (0 - x) + c * (0 - y))
            out = (c * 1.0, s * 1.0)
            sights.append(((centre[0] + 22.5 * out[0], centre[1] + 22.5 * out[1], 22.5), (out[0], out[1], 0.0)))
            if len(sights) <= self.DELAY:
                continue
            position, normal = sights[-1 - self.DELAY]
            a, left, t = cube_handling.dock_error(position, normal)
            if x - self.DISTANCE <= 0.0 or a <= self.DISTANCE:
                break
            left_wheel, right_wheel = cube_handling.servo_wheels(a, left, t, self.DISTANCE)
            # Both wheels turn forwards, and fast enough to turn at all.
            self.assertGreaterEqual(min(left_wheel, right_wheel), 20.0)
            v, omega = (left_wheel + right_wheel) / 2, (right_wheel - left_wheel) / width
            heading += omega * self.STEP
            x += v * math.cos(heading) * self.STEP
            y += v * math.sin(heading) * self.STEP
        else:
            self.fail("It never got there.")
        c, s = math.cos(-heading), math.sin(-heading)
        centre = (c * (0 - x) - s * (0 - y), s * (0 - x) + c * (0 - y))
        return cube_handling.dock_error((centre[0] + 22.5 * c, centre[1] + 22.5 * s, 22.5), (c, s, 0.0))

    def test_in_from_wherever_it_is_the_robot_ends_up_square_on(self):
        for along, side, off in ((150.0, 0.0, 0.0), (150.0, 30.0, 0.0), (150.0, -30.0, 0.0),
                                 (150.0, 0.0, math.radians(15)), (150.0, 0.0, math.radians(-15)),
                                 (150.0, 25.0, math.radians(10)), (150.0, -25.0, math.radians(-10)),
                                 (150.0, 25.0, math.radians(-15)), (150.0, -25.0, math.radians(15)),
                                 (120.0, 15.0, math.radians(20))):
            with self.subTest(along=along, side=side, off=math.degrees(off)):
                _, left, t = self.drive_in(along, side, off)
                self.assertLess(abs(left), cube_handling.SERVO_LATERAL_TOLERANCE)
                self.assertLess(abs(t), cube_handling.SERVO_ANGLE_TOLERANCE)

    def test_slowing_down_as_it_comes_in(self):
        far = cube_handling.servo_wheels(200.0, 0.0, 0.0, self.DISTANCE)
        near = cube_handling.servo_wheels(self.DISTANCE + 5.0, 0.0, 0.0, self.DISTANCE)
        self.assertEqual(far, (cube_handling.SERVO_SPEED, cube_handling.SERVO_SPEED))
        self.assertLess(near[0], far[0])
        self.assertGreaterEqual(near[0], cube_handling.SERVO_MIN_SPEED)


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
        for name in ("SETTLE_TIME", "ROLL_PULL_TIME", "WHEELIE_TIME"):
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
        def lift(height, *args, **kwargs):
            self.cli.orders.append(("lift", height))
            if height > 50:
                self.moves()

        self.instead("set_lift_height", lift)
        self.assertTrue(cube_handling.pick_up_cube(self.cli, self.cube))
        # A path, not the wheels: a robot told to turn them at a few mm/s does not move.
        self.assertIn(("drive", cube_handling.PICKUP_CREEP), self.cli.orders)
        self.assertLess(self.cli.orders.index(("lift", cube_handling.CARRY_HEIGHT)),
                        self.cli.orders.index(("drive", cube_handling.PICKUP_CREEP)))
        self.assertNotIn("wheels", [order[0] for order in self.cli.orders])
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
