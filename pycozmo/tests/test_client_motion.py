import math
import unittest
from typing import List, Optional
from unittest import mock

import pycozmo
from pycozmo import protocol_encoder, util
from pycozmo.protocol_encoder import PathEventType


class MotionTestCase(unittest.TestCase):
    """ A client whose robot answers each path it is sent to follow, as it is told to. """

    def setUp(self):
        self.cli = pycozmo.client.Client()
        self.cli.pose = util.Pose(100.0, 50.0, 0.0, angle_z=util.Angle(degrees=90.0))
        self.sent: List[pycozmo.protocol_base.Packet] = []
        # What the robot answers, or nothing.
        self.answer: Optional[PathEventType] = PathEventType.PATH_COMPLETED
        self.answer_id: Optional[int] = None
        patcher = mock.patch.object(self.cli.conn, "send", side_effect=self.receive)
        patcher.start()
        self.addCleanup(patcher.stop)

    def receive(self, pkt):
        self.sent.append(pkt)
        if isinstance(pkt, protocol_encoder.ExecutePath) and self.answer is not None:
            event_id = pkt.event_id if self.answer_id is None else self.answer_id
            for event_type in (PathEventType.PATH_STARTED, self.answer):
                self.cli.dispatch(protocol_encoder.PathFollowingEvent, self.cli.conn,
                                  protocol_encoder.PathFollowingEvent(event_id=event_id, event_type=event_type))

    def segments(self):
        return [pkt for pkt in self.sent if not isinstance(pkt, protocol_encoder.ExecutePath)]


class TestTurnInPlace(MotionTestCase):

    def test_it_turns_from_the_heading_the_robot_has(self):
        self.assertTrue(self.cli.turn_in_place(util.Angle(degrees=90.0)))
        turn, = self.segments()
        assert isinstance(turn, protocol_encoder.AppendPathSegPointTurn)
        self.assertEqual((turn.x, turn.y), (100.0, 50.0))
        self.assertAlmostEqual(turn.angle_rad, math.pi, places=5)

    def test_at_anki_s_speed_in_radians_a_second(self):
        # 40, as go_to_pose() sent, was the robot's top speed.
        self.cli.turn_in_place(util.Angle(degrees=-30.0))
        turn, = self.segments()
        self.assertEqual(turn.speed_mmps, pycozmo.robot.POINT_TURN_SPEED)
        self.assertEqual(turn.speed_mmps, 2.0)

    def test_an_interrupted_turn_says_so(self):
        self.answer = PathEventType.PATH_INTERRUPTED
        self.assertFalse(self.cli.turn_in_place(util.Angle(degrees=90.0)))

    def test_only_its_own_path_ends_it(self):
        self.answer_id = 1000
        self.assertFalse(self.cli.turn_in_place(util.Angle(degrees=90.0), timeout=0.1))

    def test_a_robot_that_never_answers_is_not_waited_for_for_good(self):
        self.answer = None
        self.assertFalse(self.cli.turn_in_place(util.Angle(degrees=90.0), timeout=0.1))

    def test_nothing_is_left_listening(self):
        self.cli.turn_in_place(util.Angle(degrees=90.0))
        self.answer = None
        self.cli.turn_in_place(util.Angle(degrees=90.0), timeout=0.05)
        self.assertEqual(self.cli.dispatch_handlers[protocol_encoder.PathFollowingEvent], [])

    def test_without_waiting(self):
        self.answer = None
        self.assertTrue(self.cli.turn_in_place(util.Angle(degrees=90.0), wait=False))
        self.assertIsInstance(self.sent[-1], protocol_encoder.ExecutePath)

    def test_each_path_has_an_id_of_its_own(self):
        self.cli.turn_in_place(util.Angle(degrees=10.0))
        self.cli.turn_in_place(util.Angle(degrees=10.0))
        ids = [pkt.event_id for pkt in self.sent if isinstance(pkt, protocol_encoder.ExecutePath)]
        self.assertEqual(len(set(ids)), 2)


class TestDriveStraight(MotionTestCase):

    def test_ahead(self):
        self.assertTrue(self.cli.drive_straight(util.Distance(mm=80.0)))
        line, = self.segments()
        assert isinstance(line, protocol_encoder.AppendPathSegLine)
        # The robot faces +y.
        self.assertAlmostEqual(line.to_x, 100.0, places=4)
        self.assertAlmostEqual(line.to_y, 130.0, places=4)
        self.assertEqual(line.speed_mmps, 100.0)

    def test_back_at_a_negative_speed(self):
        self.cli.drive_straight(util.Distance(mm=-40.0), speed=60.0)
        line, = self.segments()
        self.assertAlmostEqual(line.to_y, 10.0, places=4)
        self.assertEqual(line.speed_mmps, -60.0)


class TestGoToPose(MotionTestCase):

    def test_a_line_then_a_turn(self):
        target = util.Pose(200.0, 50.0, 0.0, angle_z=util.Angle(degrees=0.0))
        self.assertTrue(self.cli.go_to_pose(target))
        line, turn = self.segments()
        self.assertEqual((line.to_x, line.to_y), (200.0, 50.0))
        self.assertEqual((turn.x, turn.y, turn.angle_rad), (200.0, 50.0, 0.0))

    def test_an_interrupted_path_says_so(self):
        self.answer = PathEventType.PATH_INTERRUPTED
        self.assertFalse(self.cli.go_to_pose(util.Pose(200.0, 50.0, 0.0, angle_z=util.Angle(degrees=0.0))))


class TestSettings(MotionTestCase):

    def test_stop_on_cliff(self):
        self.cli.enable_stop_on_cliff()
        pkt, = self.sent
        assert isinstance(pkt, protocol_encoder.EnableStopOnCliff)
        self.assertTrue(pkt.enable)

    def test_camera_exposure(self):
        self.cli.set_camera_exposure(20, 1.5)
        self.cli.enable_auto_exposure()
        manual, automatic = self.sent
        assert isinstance(manual, protocol_encoder.SetCameraParams)
        assert isinstance(automatic, protocol_encoder.SetCameraParams)
        self.assertEqual((manual.exposure_ms, manual.gain, manual.auto_exposure_enabled), (20, 1.5, False))
        self.assertTrue(automatic.auto_exposure_enabled)
