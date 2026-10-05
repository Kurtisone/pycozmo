"""

Tests for going back to the charger. The client is stood in for: its moves are recorded, and the robot says it is on
the charger when the test has it so.

"""

import math
import threading
import unittest
from typing import Any, List, Tuple
from unittest import mock

import pycozmo
from pycozmo import charger, charger_handling, event, robot, util

from .test_charger import client, see
from .test_charger_detection import HEAD_ANGLE, render


class Fake:
    """ A client whose moves are noted, and whose pose follows them. """

    def __init__(self, x: float = 0.0, y: float = 0.0, heading: float = 0.0) -> None:
        self.cli = client(x, y, heading)
        self.moves: List[Tuple[Any, ...]] = []
        self.on_charger_after: Any = None
        self.enable_camera: Any = None
        patches = [
            mock.patch.object(self.cli, "turn_in_place", side_effect=self.turn_in_place),
            mock.patch.object(self.cli, "drive_straight", side_effect=self.drive_straight),
            mock.patch.object(self.cli, "go_to_pose", side_effect=self.go_to_pose),
            mock.patch.object(self.cli, "stop_all_motors", side_effect=self.stop_all_motors),
            mock.patch.object(self.cli, "set_head_angle"),
            mock.patch.object(self.cli, "set_lift_height"),
            mock.patch.object(self.cli, "enable_stop_on_cliff", side_effect=self.enable_stop_on_cliff),
        ]
        for patch in patches:
            patch.start()
        self.enable_camera = mock.patch.object(self.cli, "enable_camera").start()
        self.read_calibration: Any = mock.patch.object(self.cli, "read_camera_calibration", return_value=None).start()

    def charger_pose(self) -> charger.ChargerPose:
        pose = self.cli.charger.pose
        assert pose is not None
        return pose

    def turn_in_place(self, angle: util.Angle, *args: object, **kwargs: object) -> bool:
        self.moves.append(("turn", angle.radians))
        pose = self.cli.pose
        self.cli.pose = util.Pose(pose.position.x, pose.position.y, 0.0, origin_id=pose.origin_id,
                                  angle_z=util.Angle(radians=pose.rotation.angle_z.radians + angle.radians))
        return True

    def drive_straight(self, distance: util.Distance, speed: float = 0.0, wait: bool = True, **kwargs: object) -> bool:
        self.moves.append(("drive", distance.mm))
        pose = self.cli.pose
        heading = pose.rotation.angle_z.radians
        self.cli.pose = util.Pose(pose.position.x + distance.mm * math.cos(heading),
                                  pose.position.y + distance.mm * math.sin(heading), 0.0, origin_id=pose.origin_id,
                                  angle_z=pose.rotation.angle_z)
        if self.on_charger_after is not None:
            threading.Timer(self.on_charger_after, self.say_on_charger).start()
        return True

    def go_to_pose(self, pose: util.Pose, *args: object, **kwargs: object) -> bool:
        self.moves.append(("go", pose.position.x, pose.position.y))
        self.cli.pose = util.Pose(pose.position.x, pose.position.y, 0.0, origin_id=self.cli.pose.origin_id,
                                  angle_z=pose.rotation.angle_z)
        return True

    def stop_all_motors(self) -> None:
        self.moves.append(("stop", 0.0))

    def enable_stop_on_cliff(self, enable: bool = True) -> None:
        self.moves.append(("cliff", float(enable)))

    def say_on_charger(self) -> None:
        self.cli.robot_status |= robot.RobotStatusFlag.IS_ON_CHARGER


class TestPredock(unittest.TestCase):

    def test_in_front_of_the_marker_on_the_axis_facing_it(self):
        pose = charger.ChargerPose(x=100.0, y=50.0, angle=math.pi / 2, origin_id=1, time=0.0)
        predock = charger_handling.predock_pose(pose)
        # The marker faces up the y axis; the charger's axis is AXIS_OFFSET to the left of it, looking that way.
        self.assertAlmostEqual(predock.position.x, 100.0 - charger.AXIS_OFFSET)
        self.assertAlmostEqual(predock.position.y, 50.0 + charger_handling.PREDOCK_DISTANCE)
        # Facing the marker: down the y axis.
        self.assertAlmostEqual(math.sin(predock.rotation.angle_z.radians), -1.0)

    def test_a_point_in_the_chargers_own_frame(self):
        pose = charger.ChargerPose(x=0.0, y=0.0, angle=math.pi / 2, origin_id=1, time=0.0)
        along, left = pose.in_its_frame(-30.0, 200.0)
        self.assertAlmostEqual(along, 200.0)
        self.assertAlmostEqual(left, 30.0 - charger.AXIS_OFFSET)


class TestObserve(unittest.TestCase):

    def test_the_marker_in_the_next_image_is_put_with_what_is_known(self):
        cli = client(100.0, 0.0, 0.0)
        cli.head_angle = util.Angle(radians=HEAD_ANGLE)
        image = render(250.0, lateral=20.0)
        threading.Timer(0.1, cli.dispatch, (event.EvtNewRawCameraImage, cli, image)).start()
        seen = charger_handling.observe(cli, timeout=3.0)
        self.assertIsNotNone(seen)
        pose = cli.charger.pose
        assert pose is not None
        # 250 mm ahead of the robot, 20 to its left, in a world where the robot is at (100, 0), facing the robot.
        self.assertAlmostEqual(pose.x, 100.0 + 250.0, delta=10.0)
        self.assertAlmostEqual(pose.y, 20.0, delta=6.0)
        self.assertAlmostEqual(abs(pose.angle), math.pi, delta=math.radians(12.0))

    def test_no_image_nothing(self):
        self.assertIsNone(charger_handling.observe(client(), timeout=0.05))

    def test_an_image_without_it_changes_nothing(self):
        cli = client()
        cli.head_angle = util.Angle(radians=HEAD_ANGLE)
        image = render(900.0)
        threading.Timer(0.05, cli.dispatch, (event.EvtNewRawCameraImage, cli, image)).start()
        self.assertIsNone(charger_handling.observe(cli, timeout=3.0))
        self.assertIsNone(cli.charger.pose)


class TestBackOnto(unittest.TestCase):

    def setUp(self):
        # The robot stands 200 mm in front of the marker, facing it.
        self.fake = Fake(200.0, 0.0, math.pi)
        see(self.fake.cli, 200.0, 0.0)
        patcher = mock.patch.object(charger_handling, "BACK_SPEED", 400.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_turns_round_and_backs_until_the_robot_says_it_is_on(self):
        self.fake.on_charger_after = 0.05
        self.assertTrue(charger_handling.back_onto_charger(self.fake.cli))
        kinds = [move[0] for move in self.fake.moves]
        self.assertEqual(kinds[0], "turn")
        # The gyro says the robot turns 1.3% less than it does: half a turn is asked for that much less.
        self.assertAlmostEqual(abs(self.fake.moves[0][1]), math.pi / charger_handling.TURN_SCALE, places=2)
        # The marker is 200 mm away; the robot backs to DOCKED_DISTANCE of it and a margin on.
        drive = next(move for move in self.fake.moves if move[0] == "drive")
        self.assertAlmostEqual(drive[1], -(200.0 - charger.DOCKED_DISTANCE + charger_handling.BACK_MARGIN), delta=12.0)
        self.assertIn("stop", kinds)
        # The cliff sensors, which take the charger's lip for a drop, are off for the backing, and on again.
        self.assertEqual([move[1] for move in self.fake.moves if move[0] == "cliff"], [0.0, 1.0])

    def test_without_the_robots_word_it_is_not_on(self):
        with mock.patch.object(charger_handling.time, "perf_counter", side_effect=iter([0.0] + [100.0] * 50)):
            self.assertFalse(charger_handling.back_onto_charger(self.fake.cli))
        self.assertIn("stop", [move[0] for move in self.fake.moves])
        self.assertEqual([move[1] for move in self.fake.moves if move[0] == "cliff"], [0.0, 1.0])

    def test_a_robot_that_does_not_move_is_held_and_stopped(self):
        # The fake's backing moves the pose at once, and then nothing moves.
        with mock.patch.object(charger_handling, "STALL_GRACE", 0.0), \
                mock.patch.object(charger_handling, "STALL_TIME", 0.1):
            self.assertFalse(charger_handling.back_onto_charger(self.fake.cli))
        self.assertIn("stop", [move[0] for move in self.fake.moves])

    def test_a_robot_that_turns_is_not_held(self):
        # A tread held while the other turns, the ramp's rails turning the robot in: it goes on, and gets on.
        self.fake.on_charger_after = 0.45
        turning = threading.Event()

        def turn() -> None:
            while not turning.is_set():
                pose = self.fake.cli.pose
                self.fake.cli.pose = util.Pose(pose.position.x, pose.position.y, 0.0, origin_id=pose.origin_id,
                                               angle_z=util.Angle(radians=pose.rotation.angle_z.radians + 0.05))
                turning.wait(0.05)

        thread = threading.Thread(target=turn, daemon=True)
        thread.start()
        self.addCleanup(turning.set)
        with mock.patch.object(charger_handling, "STALL_GRACE", 0.0), \
                mock.patch.object(charger_handling, "STALL_TIME", 0.2):
            self.assertTrue(charger_handling.back_onto_charger(self.fake.cli))

    def test_a_charger_not_known_is_not_backed_onto(self):
        fake = Fake(200.0, 0.0, math.pi)
        self.assertFalse(charger_handling.back_onto_charger(fake.cli))
        self.assertEqual(fake.moves, [])

    def test_cancelling(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(charger_handling.Cancelled):
            charger_handling.back_onto_charger(self.fake.cli, cancel)


def known(x: float, y: float, heading: float = 0.0) -> Fake:
    """ A robot at a place that remembers its charger from when it was on it, at the origin, facing +x. """
    fake = Fake()
    fake.cli.charger.docked()
    fake.cli.pose = util.Pose(x, y, 0.0, angle_z=util.Angle(radians=heading), origin_id=1)
    return fake


class TestDetour(unittest.TestCase):

    def test_in_front_of_the_charger_the_robot_goes_straight(self):
        fake = known(300.0, 0.0, math.pi)
        self.assertTrue(charger_handling._detour(fake.cli, fake.charger_pose()))
        self.assertEqual(fake.moves, [])

    def test_beside_it_a_long_way_off_the_robot_goes_straight_too(self):
        fake = known(50.0, 300.0)
        self.assertTrue(charger_handling._detour(fake.cli, fake.charger_pose()))
        self.assertEqual(fake.moves, [])

    def test_behind_it_the_robot_drives_round(self):
        # The charger is at x = -10, facing +x. Behind it, to its left, the robot goes to a point in front of it, and to
        # the left.
        fake = known(-250.0, 250.0)
        self.assertTrue(charger_handling._detour(fake.cli, fake.charger_pose()))
        self.assertEqual(len(fake.moves), 1)
        _, x, y = fake.moves[0]
        self.assertAlmostEqual(x, -charger.DOCKED_DISTANCE + charger_handling.DETOUR_DISTANCE)
        self.assertAlmostEqual(y, charger_handling.DETOUR_SIDE)

    def test_and_to_the_right_of_it_round_the_other_way(self):
        fake = known(-250.0, -250.0)
        charger_handling._detour(fake.cli, fake.charger_pose())
        _, x, y = fake.moves[0]
        self.assertAlmostEqual(y, -charger_handling.DETOUR_SIDE)

    def test_close_beside_it_too(self):
        fake = known(60.0, 100.0)
        charger_handling._detour(fake.cli, fake.charger_pose())
        self.assertEqual(len(fake.moves), 1)


class TestGoToCharger(unittest.TestCase):

    def test_a_robot_that_is_on_it_stays(self):
        fake = Fake()
        fake.cli.robot_status = robot.RobotStatusFlag.IS_ON_CHARGER
        self.assertTrue(charger_handling.go_to_charger(fake.cli))
        self.assertEqual(fake.moves, [])

    def test_a_charger_not_found_is_not_gone_to(self):
        fake = Fake()
        with mock.patch.object(charger_handling, "find_charger", return_value=False):
            self.assertFalse(charger_handling.go_to_charger(fake.cli))

    def test_it_goes_looks_and_backs(self):
        fake = known(300.0, 100.0, 0.5)
        with mock.patch.object(charger_handling, "go_to_predock", return_value=True) as predock, \
                mock.patch.object(charger_handling, "back_onto_charger", return_value=True) as back:
            self.assertTrue(charger_handling.go_to_charger(fake.cli))
        predock.assert_called_once()
        back.assert_called_once()

    def test_a_charger_not_where_it_was_remembered_is_looked_for_all_round(self):
        fake = known(300.0, 100.0, 0.5)
        with mock.patch.object(charger_handling, "go_to_predock", side_effect=[False, True]), \
                mock.patch.object(charger_handling, "find_charger", return_value=True) as find, \
                mock.patch.object(charger_handling, "back_onto_charger", return_value=True):
            self.assertTrue(charger_handling.go_to_charger(fake.cli))
        find.assert_called_once()

    def test_a_backing_that_does_not_take_is_tried_again(self):
        fake = known(60.0, 0.0, 0.0)
        with mock.patch.object(charger_handling, "go_to_predock", return_value=True), \
                mock.patch.object(charger_handling, "back_onto_charger", side_effect=[False, True]):
            self.assertTrue(charger_handling.go_to_charger(fake.cli))
        # It drove straight out to OUT_DISTANCE in front of the marker, not round the charger.
        along, _ = fake.charger_pose().in_its_frame(60.0, 0.0)
        self.assertIn(("drive", charger_handling.OUT_DISTANCE - along), fake.moves)

    def test_a_robot_well_in_front_does_not_drive_out_further(self):
        fake = known(charger_handling.OUT_DISTANCE + 50.0, 0.0, 0.0)
        with mock.patch.object(charger_handling, "go_to_predock", return_value=True), \
                mock.patch.object(charger_handling, "back_onto_charger", side_effect=[False, True]):
            self.assertTrue(charger_handling.go_to_charger(fake.cli))
        self.assertEqual([move for move in fake.moves if move[0] == "drive"], [])

    def test_it_gives_up(self):
        fake = known(300.0, 100.0, 0.5)
        with mock.patch.object(charger_handling, "go_to_predock", return_value=True), \
                mock.patch.object(charger_handling, "back_onto_charger", return_value=False):
            self.assertFalse(charger_handling.go_to_charger(fake.cli))

    def test_cancelling_stops_the_robot(self):
        fake = known(300.0, 100.0, 0.5)
        with mock.patch.object(charger_handling, "go_to_predock", side_effect=charger_handling.Cancelled()):
            with self.assertRaises(charger_handling.Cancelled):
                charger_handling.go_to_charger(fake.cli)
        self.assertIn(("stop", 0.0), fake.moves)


class TestPredockStep(unittest.TestCase):

    def setUp(self):
        for name, value in (("LOOKS", 2), ("MIN_LOOKS", 1), ("SETTLE_TIME", 0.0)):
            patcher = mock.patch.object(charger_handling, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_where_it_should_be_and_seeing_it_the_robot_is_there(self):
        fake = known(-charger.DOCKED_DISTANCE + charger_handling.PREDOCK_DISTANCE, 0.0, math.pi)
        with mock.patch.object(charger_handling, "observe", return_value=object()):
            self.assertTrue(charger_handling.go_to_predock(fake.cli))
        self.assertEqual([move for move in fake.moves if move[0] == "go"], [])

    def test_off_the_place_it_drives_there_and_looks_again(self):
        fake = known(200.0, 150.0, math.pi)
        with mock.patch.object(charger_handling, "observe", return_value=object()):
            self.assertTrue(charger_handling.go_to_predock(fake.cli))
        goes = [move for move in fake.moves if move[0] == "go"]
        self.assertEqual(len(goes), 1)
        self.assertAlmostEqual(goes[0][1], -charger.DOCKED_DISTANCE + charger_handling.PREDOCK_DISTANCE)
        self.assertAlmostEqual(goes[0][2], 0.0)

    def test_there_and_not_seeing_the_marker_the_charger_is_not_where_it_was(self):
        fake = known(-charger.DOCKED_DISTANCE + charger_handling.PREDOCK_DISTANCE, 0.0, math.pi)
        with mock.patch.object(charger_handling, "observe", return_value=None):
            self.assertFalse(charger_handling.go_to_predock(fake.cli))

    def test_near_the_place_but_facing_away_the_robot_turns_to_look(self):
        # Not seeing the marker with its back to it says nothing: it turns to face it, and sees it.
        fake = known(-charger.DOCKED_DISTANCE + charger_handling.PREDOCK_DISTANCE, 0.0, 0.0)
        views = [None, None, object(), object()]

        def observing(*args: object, **kwargs: object) -> object:
            return views.pop(0) if views else object()

        with mock.patch.object(charger_handling, "observe", side_effect=observing):
            self.assertTrue(charger_handling.go_to_predock(fake.cli))
        self.assertEqual(len([move for move in fake.moves if move[0] == "go"]), 1)

    def test_a_charger_not_known_is_not_gone_to(self):
        fake = Fake()
        with mock.patch.object(charger_handling, "observe", return_value=None):
            self.assertFalse(charger_handling.go_to_predock(fake.cli))


class TestFind(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.object(charger_handling, "SETTLE_TIME", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_turns_to_where_the_charger_is_remembered_and_looks(self):
        fake = known(300.0, 0.0, 0.0)
        with mock.patch.object(charger_handling, "look_for_charger", return_value=True):
            self.assertTrue(charger_handling.find_charger(fake.cli))
        # The charger is behind the robot: it turns right round.
        self.assertAlmostEqual(abs(fake.moves[0][1]), math.pi, delta=0.1)

    def test_it_looks_all_round_for_one_it_does_not_know(self):
        fake = Fake()
        with mock.patch.object(charger_handling, "look_for_charger", side_effect=[False] * 4 + [True]):
            self.assertTrue(charger_handling.find_charger(fake.cli))
        turns = [move for move in fake.moves if move[0] == "turn"]
        self.assertEqual(len(turns), 5)
        self.assertAlmostEqual(turns[0][1], charger_handling.SEARCH_STEP)

    def test_a_charger_nowhere_is_not_found(self):
        fake = Fake()
        with mock.patch.object(charger_handling, "look_for_charger", return_value=False):
            self.assertFalse(charger_handling.find_charger(fake.cli))
        self.assertEqual(len([move for move in fake.moves if move[0] == "turn"]), 12)

    def test_a_look_is_the_robots_camera_for_a_while(self):
        fake = Fake()
        seen = [None, None, object()]
        with mock.patch.object(charger_handling, "observe", side_effect=lambda *a, **k: seen.pop(0)):
            self.assertTrue(charger_handling.look_for_charger(fake.cli, timeout=5.0))
        self.assertEqual(seen, [])
        fake.enable_camera.assert_called_with(True, color=False)

    def test_a_look_gives_up(self):
        fake = Fake()
        with mock.patch.object(charger_handling, "observe", return_value=None):
            self.assertFalse(charger_handling.look_for_charger(fake.cli, timeout=0.05))


class TestLens(unittest.TestCase):

    def test_the_robots_own_calibration_is_read_once(self):
        fake = Fake()
        with mock.patch.object(charger_handling, "observe", return_value=None):
            charger_handling.look_for_charger(fake.cli, timeout=0.05)
        fake.read_calibration.assert_called_once()

    def test_one_that_is_read_is_left_be(self):
        fake = Fake()
        fake.cli.camera_calibration = pycozmo.camera.DEFAULT_CALIBRATION
        with mock.patch.object(charger_handling, "observe", return_value=None):
            charger_handling.look_for_charger(fake.cli, timeout=0.05)
        fake.read_calibration.assert_not_called()


class TestLearning(unittest.TestCase):

    def test_a_tread_held_is_told_by_its_speed_over_the_last_seconds(self):
        now = 100.0
        left_held = [(now - 1.0 + 0.1 * i, 5.0, 35.0) for i in range(10)]
        right_held = [(now - 1.0 + 0.1 * i, 35.0, 8.0) for i in range(10)]
        both = [(now - 1.0 + 0.1 * i, 34.0, 36.0) for i in range(10)]
        self.assertEqual(charger_handling._held_tread(left_held), "left")
        self.assertEqual(charger_handling._held_tread(right_held), "right")
        self.assertIsNone(charger_handling._held_tread(both))

    def test_a_robot_that_did_not_move_has_no_tread_held(self):
        self.assertIsNone(charger_handling._held_tread([(1.0 + 0.1 * i, 0.0, 0.0) for i in range(20)]))
        self.assertIsNone(charger_handling._held_tread([]))
        # Too few moving samples to say.
        self.assertIsNone(charger_handling._held_tread([(1.0, 5.0, 35.0), (1.1, 5.0, 35.0)]))

    def test_the_old_speeds_do_not_count(self):
        speeds = [(float(i), 5.0, 35.0) for i in range(5)] + [(10.0 + 0.1 * i, 34.0, 36.0) for i in range(10)]
        self.assertIsNone(charger_handling._held_tread(speeds))

    def test_a_backing_held_on_the_left_moves_the_aim_to_the_right(self):
        fake = known(60.0, 0.0, 0.0)
        before = fake.cli.charger.aim

        def backing(cli: object, cancel: object = None) -> bool:
            fake.cli.charger.last_held = "left"
            return False

        with mock.patch.object(charger_handling, "go_to_predock", return_value=True), \
                mock.patch.object(charger_handling, "back_onto_charger", side_effect=backing):
            charger_handling.go_to_charger(fake.cli)
        # Three tries, each moving the aim a step the other way.
        self.assertAlmostEqual(fake.cli.charger.aim, before - 3 * charger_handling.AIM_STEP)

    def test_a_backing_held_on_the_right_moves_the_aim_to_the_left(self):
        fake = known(60.0, 0.0, 0.0)

        def backing(cli: object, cancel: object = None) -> bool:
            fake.cli.charger.last_held = "right"
            return False

        with mock.patch.object(charger_handling, "go_to_predock", return_value=True), \
                mock.patch.object(charger_handling, "back_onto_charger", side_effect=backing):
            charger_handling.go_to_charger(fake.cli)
        self.assertAlmostEqual(fake.cli.charger.aim, 3 * charger_handling.AIM_STEP)

    def test_a_backing_not_held_either_side_leaves_the_aim(self):
        fake = known(60.0, 0.0, 0.0)
        with mock.patch.object(charger_handling, "go_to_predock", return_value=True), \
                mock.patch.object(charger_handling, "back_onto_charger", return_value=False):
            charger_handling.go_to_charger(fake.cli)
        self.assertEqual(fake.cli.charger.aim, 0.0)

    def test_a_backing_that_took_teaches_where_the_robot_stood(self):
        fake = known(60.0, 0.0, 0.0)

        def backing(cli: object, cancel: object = None) -> bool:
            fake.cli.charger.last_lateral = -10.0
            return True

        with mock.patch.object(charger_handling, "go_to_predock", return_value=True), \
                mock.patch.object(charger_handling, "back_onto_charger", side_effect=backing):
            self.assertTrue(charger_handling.go_to_charger(fake.cli))
        self.assertAlmostEqual(fake.cli.charger.aim, -10.0 * charger_handling.AIM_LEARNING)

    def test_backing_notes_where_the_robot_stood_and_which_tread_was_held(self):
        fake = known(charger_handling.PREDOCK_DISTANCE - 10.0, 7.0, math.pi)
        fake.cli.left_wheel_speed = util.Speed(mmps=-30.0)
        fake.cli.right_wheel_speed = util.Speed(mmps=-4.0)
        with mock.patch.object(charger_handling, "STALL_GRACE", 0.0), \
                mock.patch.object(charger_handling, "STALL_TIME", 0.15), \
                mock.patch.object(charger_handling, "HELD_WINDOW", 5.0):
            self.assertFalse(charger_handling.back_onto_charger(fake.cli))
        # The robot is 7 mm to the left of the axis, which it knows from where it rested.
        self.assertAlmostEqual(fake.cli.charger.last_lateral, 7.0, delta=0.01)
        self.assertEqual(fake.cli.charger.last_held, "right")


class TestAim(unittest.TestCase):

    def test_the_aim_moves_the_axis_the_robot_goes_to(self):
        fake = known(300.0, 0.0, math.pi)
        before = charger_handling.predock_pose(fake.charger_pose())
        fake.cli.charger.aim = 10.0
        after = charger_handling.predock_pose(fake.charger_pose())
        # The marker faces +x: to its left is +y.
        self.assertAlmostEqual(after.position.y - before.position.y, 10.0)
        self.assertAlmostEqual(after.position.x, before.position.x)
