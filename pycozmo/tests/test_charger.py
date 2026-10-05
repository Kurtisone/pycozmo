"""

Tests for what the robot knows of where its charger is.

"""

import math
import time
import unittest
from typing import Optional, Tuple

import pycozmo
from pycozmo import charger, event, util


def client(x: float = 0.0, y: float = 0.0, heading: float = 0.0, origin_id: int = 1) -> pycozmo.client.Client:
    cli = pycozmo.client.Client()
    cli.pose = util.Pose(x, y, 0.0, angle_z=util.Angle(radians=heading), origin_id=origin_id)
    return cli


def see(cli: pycozmo.client.Client, ahead: float, left: float = 0.0, facing: float = math.pi,
        distance: Optional[float] = None) -> charger.ChargerPose:
    """ A view of the marker, ahead of the robot, facing it by default. """
    normal = (math.cos(facing), math.sin(facing), 0.0)
    return cli.charger.observe((ahead, left, 25.0), normal, math.hypot(ahead, left) if distance is None else distance)


class TestCharger(unittest.TestCase):

    def test_nothing_is_known_at_first(self):
        cli = client()
        self.assertIsNone(cli.charger.pose)
        self.assertFalse(cli.charger.known)

    def test_a_view_is_put_in_the_world_frame(self):
        # The robot at (100, 50) faces up the y axis; the marker is 200 mm ahead of it and 30 to its left, facing it.
        cli = client(100.0, 50.0, math.pi / 2)
        pose = see(cli, 200.0, 30.0)
        self.assertAlmostEqual(pose.x, 100.0 - 30.0)
        self.assertAlmostEqual(pose.y, 50.0 + 200.0)
        # Facing the robot: down the y axis.
        self.assertAlmostEqual(math.sin(pose.angle), -1.0)
        self.assertEqual(pose.views, 1)
        self.assertIs(cli.charger.pose, pose)

    def test_views_are_put_together_the_nearer_counting_for_more_and_the_newer(self):
        cli = client()
        see(cli, 240.0, 20.0)
        pose = see(cli, 200.0, 0.0)
        # Weighed by the inverse square of the distance, and the older view by RECENCY less.
        w1, w2 = charger.RECENCY / math.hypot(240.0, 20.0) ** 2, 1.0 / 200.0 ** 2
        self.assertAlmostEqual(pose.x, (240.0 * w1 + 200.0 * w2) / (w1 + w2), 6)

    def test_the_views_of_where_the_robot_is_count_for_more_than_those_of_where_it_was(self):
        cli = client()
        for _ in range(4):
            see(cli, 250.0, 40.0)
        for _ in range(4):
            pose = see(cli, 200.0, 10.0)
        # Odometry is some 20 mm out from one place to the next: the marker is where the last views say, to a few mm.
        self.assertAlmostEqual(pose.y, 10.0, delta=12.0)
        self.assertLess(pose.y, 25.0)

    def test_a_view_far_from_the_others_does_not_count(self):
        cli = client()
        for _ in range(4):
            see(cli, 200.0)
        pose = see(cli, 200.0, 120.0)
        self.assertAlmostEqual(pose.y, 0.0)
        self.assertEqual(pose.views, 4)

    def test_the_heading_is_the_mean_of_the_headings(self):
        cli = client()
        see(cli, 200.0, facing=math.pi - 0.2)
        pose = see(cli, 200.0, facing=-math.pi + 0.2)
        # Either side of pi, not the mean of -pi + 0.2 and pi - 0.2.
        self.assertAlmostEqual(abs(pose.angle), math.pi, places=6)

    def test_a_view_from_the_side_tells_the_heading_better_than_one_seen_squarely(self):
        cli = client()
        # Seen from a way round, the marker says it faces pi + 0.1; seen squarely, pi - 0.1.
        cli.charger.observe((250.0, 150.0, 25.0), (math.cos(math.pi + 0.1), math.sin(math.pi + 0.1), 0.0), 292.0)
        pose = cli.charger.observe((250.0, 0.0, 25.0), (math.cos(math.pi - 0.1), math.sin(math.pi - 0.1), 0.0), 250.0)
        # The mean of the two would be pi; the heading is nearer the one that says more, on pi + 0.1's side.
        deviation = math.atan2(math.sin(pose.angle - math.pi), math.cos(pose.angle - math.pi))
        self.assertGreater(deviation, 0.02)
        self.assertLess(deviation, 0.1)

    def test_only_the_last_views_are_kept(self):
        cli = client()
        for _ in range(charger.MAX_VIEWS):
            see(cli, 200.0)
        pose = see(cli, 200.0, 20.0)
        self.assertEqual(pose.views, charger.MAX_VIEWS)

    def test_a_charger_in_another_frame_is_not_known(self):
        cli = client()
        see(cli, 200.0)
        self.assertTrue(cli.charger.known)
        # The robot was picked up, and its position began again at zero.
        cli.pose = util.Pose(0.0, 0.0, 0.0, angle_z=util.Angle(radians=0.0), origin_id=2)
        self.assertIsNone(cli.charger.pose)
        # Seen again, only the views in the new frame count.
        pose = see(cli, 300.0, 10.0)
        self.assertEqual(pose.views, 1)
        self.assertAlmostEqual(pose.x, 300.0)

    def test_the_aim_moves_the_axis_and_is_kept_with_the_pose(self):
        cli = client()
        pose = see(cli, 200.0)
        self.assertEqual(pose.aim, 0.0)
        cli.charger.aim = 6.0
        moved = cli.charger.pose
        assert moved is not None
        self.assertEqual(moved.aim, 6.0)
        # The axis is AXIS_OFFSET and the aim to the left of the marker, looking the way it faces.
        c, sn = math.cos(moved.angle), math.sin(moved.angle)
        axis = charger.AXIS_OFFSET + 6.0
        along, left = moved.in_its_frame(moved.x + 50.0 * c - axis * sn, moved.y + 50.0 * sn + axis * c)
        self.assertAlmostEqual(along, 50.0)
        self.assertAlmostEqual(left, 0.0)
        # A view that comes keeps it.
        self.assertEqual(see(cli, 200.0).aim, 6.0)
        # And forgetting leaves what was learnt, which is the robot's and the charger's, not a view's.
        cli.charger.forget()
        self.assertEqual(cli.charger.aim, 6.0)

    def test_the_aim_is_kept_within_what_the_robot_may_move_it(self):
        cli = client()
        cli.charger.aim = 100.0
        self.assertEqual(cli.charger.aim, charger.MAX_AIM)
        cli.charger.aim = -100.0
        self.assertEqual(cli.charger.aim, -charger.MAX_AIM)

    def test_forgetting(self):
        cli = client()
        see(cli, 200.0)
        cli.charger.forget()
        self.assertIsNone(cli.charger.pose)

    def test_on_the_charger_the_robot_knows_where_it_is(self):
        # Its back is to the charger: the marker is behind it, and faces the way the robot does. The robot's axis is the
        # charger's, which is AXIS_OFFSET to the left of the marker, looking the way it faces: the marker is that far to
        # the robot's right.
        cli = client(50.0, 20.0, math.pi / 2)
        pose = cli.charger.docked()
        self.assertAlmostEqual(pose.x, 50.0 + charger.AXIS_OFFSET)
        self.assertAlmostEqual(pose.y, 20.0 - charger.DOCKED_DISTANCE)
        self.assertAlmostEqual(pose.angle, math.pi / 2)
        self.assertEqual(pose.views, 0)
        # And the robot is on the charger's axis, DOCKED_DISTANCE in front of the marker's plane.
        along, left = pose.in_its_frame(50.0, 20.0)
        self.assertAlmostEqual(along, charger.DOCKED_DISTANCE)
        self.assertAlmostEqual(left, 0.0)

    def test_the_robot_says_it_is_on_the_charger(self):
        cli = client()
        # The client takes the status flag as it comes: see Client.start().
        cli._on_robot_on_charger(cli, True)
        pose = cli.charger.pose
        assert pose is not None
        self.assertAlmostEqual(pose.x, -charger.DOCKED_DISTANCE)
        # And driving off it does not make the robot forget.
        cli._on_robot_on_charger(cli, False)
        self.assertTrue(cli.charger.known)

    def test_standing_on_it_takes_back_what_was_seen(self):
        cli = client()
        see(cli, 500.0, 90.0)
        cli.charger.docked()
        pose = cli.charger.pose
        assert pose is not None
        self.assertAlmostEqual(pose.y, -charger.AXIS_OFFSET)
        self.assertEqual(pose.views, 0)

    def test_a_view_tells_the_listeners(self):
        cli = client()
        heard = []
        cli.add_handler(event.EvtChargerObserved, lambda c, pose: heard.append(pose))
        see(cli, 200.0)
        time.sleep(0.05)
        self.assertEqual(len(heard), 1)


def turned_to_face_it(cli: pycozmo.client.Client, x: float = 200.0, y: float = 0.0) -> None:
    """ The robot, which rested on its charger facing 0, has driven out and turned to face it, at (x, y). """
    cli.pose = util.Pose(x, y, 0.0, angle_z=util.Angle(radians=math.pi), origin_id=cli.pose.origin_id)


def saying(cli: pycozmo.client.Client, ahead: float, left: float, off: float) -> charger.ChargerPose:
    """ A view of the marker that says it faces `off` radians from the way it faces the robot squarely. """
    normal = (math.cos(math.pi + off), math.sin(math.pi + off), 0.0)
    return cli.charger.observe((ahead, left, 25.0), normal, math.hypot(ahead, left))


class TestPrior(unittest.TestCase):

    def test_the_heading_the_robot_rested_with_holds_against_views_squarely_seen(self):
        cli = client()
        cli.charger.docked()
        turned_to_face_it(cli)
        # Four views seen squarely that say the charger faces 4 degrees one way: its heading is nearer 0 than they say.
        for _ in range(4):
            pose = saying(cli, 200.0, 0.0, 0.07)
        self.assertLess(abs(math.atan2(math.sin(pose.angle), math.cos(pose.angle))), 0.07 / 2)

    def test_views_from_the_side_outweigh_it(self):
        cli = client()
        cli.charger.docked()
        turned_to_face_it(cli, 250.0, -175.0)
        for _ in range(16):
            pose = saying(cli, 250.0, 175.0, 0.17)
        # Sixteen views against the one: the heading has gone more than half way to theirs.
        self.assertGreater(abs(math.atan2(math.sin(pose.angle), math.cos(pose.angle))), 0.17 / 2)

    def test_forgetting_forgets_it_too(self):
        cli = client()
        cli.charger.docked()
        turned_to_face_it(cli)
        cli.charger.forget()
        pose = saying(cli, 200.0, 0.0, 0.07)
        self.assertAlmostEqual(math.atan2(math.sin(pose.angle), math.cos(pose.angle)), 0.07)

    def test_a_heading_from_another_frame_says_nothing(self):
        cli = client()
        cli.charger.docked()
        cli.pose = util.Pose(200.0, 0.0, 0.0, angle_z=util.Angle(radians=math.pi), origin_id=2)
        pose = saying(cli, 200.0, 0.0, 0.07)
        self.assertAlmostEqual(math.atan2(math.sin(pose.angle), math.cos(pose.angle)), 0.07)


def facing(angle: float) -> Tuple[float, float, float]:
    """ A marker's normal in the robot's frame, as a unit vector at an angle. """
    return math.cos(angle), math.sin(angle), 0.0


class TestPlausible(unittest.TestCase):

    def test_a_marker_seen_squarely_is_one(self):
        cli = client()
        self.assertTrue(cli.charger.plausible((200.0, 0.0, 25.0), facing(math.pi)))
        self.assertTrue(cli.charger.plausible((250.0, 100.0, 25.0), facing(math.pi + 0.3)))

    def test_a_marker_facing_away_or_far_off_the_way_it_is_seen_from_is_not(self):
        cli = client()
        # Facing 67 degrees off the robot, sideways, which a real marker seen from here does not.
        self.assertFalse(cli.charger.plausible((200.0, 0.0, 25.0), facing(math.pi - 1.17)))
        self.assertFalse(cli.charger.plausible((200.0, 0.0, 25.0), facing(0.0)))
        self.assertFalse(cli.charger.plausible((0.0, 0.0, 25.0), facing(math.pi)))

    def test_a_view_that_disagrees_with_what_is_known_is_not_one(self):
        cli = client()
        # The robot rested on its charger facing 0: the marker faces 0 in the world, and the robot now looks at it.
        cli.charger.docked()
        cli.pose = util.Pose(200.0, 0.0, 0.0, angle_z=util.Angle(radians=math.pi), origin_id=1)
        self.assertTrue(cli.charger.plausible((200.0, 0.0, 25.0), facing(math.pi)))
        # 50 degrees round: seen from the side that much, and the charger is not remembered so.
        self.assertFalse(cli.charger.plausible((200.0, 0.0, 25.0), facing(math.pi + 0.87)))


class TestDockAxis(unittest.TestCase):

    def test_nothing_is_known_before_it_docks(self):
        self.assertIsNone(client().charger.offset_from_dock_axis(0.0, 0.0))

    def test_how_far_to_the_left_of_the_line_it_docked_on_a_point_is(self):
        cli = client(100.0, 50.0, math.pi / 2)
        cli.charger.docked()
        # It docked facing up the y axis: to its left is -x.
        self.assertAlmostEqual(cli.charger.offset_from_dock_axis(100.0, 300.0) or 0.0, 0.0)
        self.assertAlmostEqual(cli.charger.offset_from_dock_axis(90.0, 10.0) or 0.0, 10.0)
        self.assertAlmostEqual(cli.charger.offset_from_dock_axis(120.0, 80.0) or 0.0, -20.0)

    def test_another_frame_says_nothing(self):
        cli = client()
        cli.charger.docked()
        cli.pose = util.Pose(0.0, 0.0, 0.0, angle_z=util.Angle(radians=0.0), origin_id=2)
        self.assertIsNone(cli.charger.offset_from_dock_axis(0.0, 10.0))

    def test_forgetting_forgets_where_it_docked(self):
        cli = client()
        cli.charger.docked()
        cli.charger.forget()
        self.assertIsNone(cli.charger.offset_from_dock_axis(0.0, 10.0))
