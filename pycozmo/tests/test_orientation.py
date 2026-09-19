import unittest
from typing import List

import pycozmo


G = pycozmo.robot.GRAVITY_MM_S2


def accel(x=0.0, y=0.0, z=G):
    return pycozmo.util.Vector3(x, y, z)


class TestGetOrientation(unittest.TestCase):
    """ The accelerometer reads the direction of "up" in the robot's frame: x forward, y left. """

    def test_on_treads(self):
        self.assertEqual(pycozmo.robot.get_orientation(accel(), 0.0),
                         pycozmo.robot.RobotOrientation.ON_THREADS)

    def test_on_left_side(self):
        # Lying on the left side puts "up" to the robot's right, which is -y.
        self.assertEqual(pycozmo.robot.get_orientation(accel(y=-G, z=0.0), 0.0),
                         pycozmo.robot.RobotOrientation.ON_LEFT_SIDE)

    def test_on_right_side(self):
        self.assertEqual(pycozmo.robot.get_orientation(accel(y=G, z=0.0), 0.0),
                         pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE)

    def test_on_face(self):
        self.assertEqual(pycozmo.robot.get_orientation(accel(x=G, z=0.0), -1.5),
                         pycozmo.robot.RobotOrientation.ON_FACE)

    def test_on_back(self):
        self.assertEqual(pycozmo.robot.get_orientation(accel(x=-G, z=0.0), 1.5),
                         pycozmo.robot.RobotOrientation.ON_BACK)

    def test_a_side_wins_over_the_pitch(self):
        # Pitch is meaningless once the robot is on a side, so the lateral reading decides.
        self.assertEqual(pycozmo.robot.get_orientation(accel(y=G, z=0.0), 1.5),
                         pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE)

    def test_a_small_tilt_is_still_on_treads(self):
        for y in (-0.4 * G, -0.1 * G, 0.0, 0.1 * G, 0.4 * G):
            with self.subTest(accel_y=y):
                self.assertEqual(pycozmo.robot.get_orientation(accel(y=y), 0.0),
                                 pycozmo.robot.RobotOrientation.ON_THREADS)

    def test_a_shallow_pitch_is_still_on_treads(self):
        for pitch in (-0.9, -0.5, 0.0, 0.5, 0.9):
            with self.subTest(pitch=pitch):
                self.assertEqual(pycozmo.robot.get_orientation(accel(), pitch),
                                 pycozmo.robot.RobotOrientation.ON_THREADS)


class TestClientOrientation(unittest.TestCase):
    """ Orientation tracking on the client, fed the state packets the robot sends. """

    def setUp(self):
        self.cli = pycozmo.client.Client()
        self.seen: List[pycozmo.robot.RobotOrientation] = []
        self.cli.add_handler(pycozmo.event.EvtRobotOrientationChange,
                             lambda cli, orientation: self.seen.append(orientation))

    def state(self, pose_angle_rad=0.0, pose_pitch_rad=0.0, accel_x=0.0, accel_y=0.0, accel_z=G):
        return pycozmo.protocol_encoder.RobotState(
            pose_angle_rad=pose_angle_rad, pose_pitch_rad=pose_pitch_rad,
            accel_x=accel_x, accel_y=accel_y, accel_z=accel_z,
            cliff_data_raw=(0, 0, 0, 0))

    def feed(self, pkt, times=1):
        for _ in range(times):
            self.cli._on_robot_state(self.cli.conn, pkt)

    def hold(self, pkt):
        """ Feed a state until it has held long enough to be accepted. """
        self.feed(pkt)
        self.cli._candidate_orientation_time -= pycozmo.robot.ORIENTATION_HOLD_TIME
        self.feed(pkt)

    def test_turning_is_not_a_roll(self):
        # pose_angle_rad is the heading in the world frame. Reading the lateral axis from it used
        # to report every turn of more than 23 degrees as the robot lying on a side.
        for angle in (-3.0, -1.5, -0.5, 0.0, 0.5, 1.5, 3.0):
            self.hold(self.state(pose_angle_rad=angle))
        self.assertEqual(self.seen, [])
        self.assertEqual(self.cli.robot_orientation, pycozmo.robot.RobotOrientation.ON_THREADS)

    def test_rolling_onto_a_side_is_announced(self):
        self.hold(self.state(accel_y=G, accel_z=0.0))
        self.assertEqual(self.seen, [pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE])
        self.assertEqual(self.cli.robot_orientation, pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE)

    def test_announced_once_while_it_lasts(self):
        self.hold(self.state(accel_y=G, accel_z=0.0))
        self.feed(self.state(accel_y=G, accel_z=0.0), times=20)
        self.assertEqual(self.seen, [pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE])

    def test_returning_to_treads_is_announced(self):
        self.hold(self.state(accel_y=G, accel_z=0.0))
        self.hold(self.state())
        self.assertEqual(self.seen, [pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE,
                                     pycozmo.robot.RobotOrientation.ON_THREADS])

    def test_a_transient_is_ignored(self):
        # A righting animation throws the robot around; without the hold time, the transients on
        # the way retrigger the reactions.
        self.feed(self.state(accel_y=G, accel_z=0.0), times=5)
        self.feed(self.state(accel_x=G, accel_z=0.0, pose_pitch_rad=-1.5), times=5)
        self.feed(self.state(), times=5)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.cli.robot_orientation, pycozmo.robot.RobotOrientation.ON_THREADS)

    def test_a_transient_does_not_delay_a_lasting_orientation(self):
        self.feed(self.state(accel_x=G, accel_z=0.0, pose_pitch_rad=-1.5), times=3)
        self.hold(self.state(accel_y=-G, accel_z=0.0))
        self.assertEqual(self.seen, [pycozmo.robot.RobotOrientation.ON_LEFT_SIDE])


class TestBrainOrientationReactions(unittest.TestCase):
    """ The reaction each orientation posts. Those used to be commented out in the brain. """

    def test_every_orientation_posts_a_reaction(self):
        expected = {
            pycozmo.robot.RobotOrientation.ON_THREADS: "ReturnedToTreads",
            pycozmo.robot.RobotOrientation.ON_BACK: "RobotOnBack",
            pycozmo.robot.RobotOrientation.ON_FACE: "RobotOnFace",
            pycozmo.robot.RobotOrientation.ON_LEFT_SIDE: "RobotOnSide",
            pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE: "RobotOnSide",
        }
        self.assertEqual(pycozmo.brain.Brain.ORIENTATION_REACTIONS, expected)
        for orientation in pycozmo.robot.RobotOrientation:
            with self.subTest(orientation=orientation):
                self.assertIn(orientation, pycozmo.brain.Brain.ORIENTATION_REACTIONS)


class TestRobotStateHandling(unittest.TestCase):
    """
    A robot state packet has to be handled through to the end.

    The animation controller registered three handlers for status flag change events that took no
    argument, so the dispatch raised TypeError. Every flag change after the animating one, and the
    orientation update that follows them, was lost for that packet.
    """

    def setUp(self):
        self.cli = pycozmo.client.Client()
        self.cli.anim_controller.start = lambda: None  # type: ignore[method-assign]
        # Register the animation controller's handlers, as its start() does.
        for evt, handler in (
                (pycozmo.event.EvtRobotAnimatingChange,
                 self.cli.anim_controller._on_animating_change),
                (pycozmo.event.EvtRobotAnimBufferFullChange,
                 self.cli.anim_controller._on_anim_buffer_full_change),
                (pycozmo.event.EvtRobotAnimatingIdleChange,
                 self.cli.anim_controller._on_amimating_idle_change)):
            self.cli.add_handler(evt, handler)

    def test_a_state_with_every_flag_set_is_handled(self):
        flags = 0
        for flag in pycozmo.event.STATUS_EVENTS:
            flags |= flag
        pkt = pycozmo.protocol_encoder.RobotState(
            status=flags, accel_z=G, cliff_data_raw=(0, 0, 0, 0))
        # This used to raise TypeError from the animating flag handler.
        self.cli._on_robot_state(self.cli.conn, pkt)
        self.assertEqual(self.cli.robot_status, flags)

    def test_the_orientation_is_still_updated(self):
        seen = []
        self.cli.add_handler(pycozmo.event.EvtRobotOrientationChange,
                             lambda cli, orientation: seen.append(orientation))
        flags = pycozmo.robot.RobotStatusFlag.IS_ANIMATING
        pkt = pycozmo.protocol_encoder.RobotState(
            status=flags, accel_y=G, accel_z=0.0, cliff_data_raw=(0, 0, 0, 0))
        self.cli._on_robot_state(self.cli.conn, pkt)
        self.cli._candidate_orientation_time -= pycozmo.robot.ORIENTATION_HOLD_TIME
        self.cli._on_robot_state(self.cli.conn, pkt)
        self.assertEqual(seen, [pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE])
