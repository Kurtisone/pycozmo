"""

Tests for what the robot knows of where its charger is.

"""

import math
import time
import unittest
from typing import Optional

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

    def test_views_are_put_together_the_nearer_counting_for_more(self):
        cli = client()
        see(cli, 240.0, 20.0)
        pose = see(cli, 200.0, 0.0)
        # Weighed by the inverse square of the distance: the second counts as 1.44 times the first.
        w1, w2 = 1.0 / math.hypot(240.0, 20.0) ** 2, 1.0 / 200.0 ** 2
        self.assertAlmostEqual(pose.x, (240.0 * w1 + 200.0 * w2) / (w1 + w2), 6)
        self.assertEqual(pose.views, 2)

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

    def test_forgetting(self):
        cli = client()
        see(cli, 200.0)
        cli.charger.forget()
        self.assertIsNone(cli.charger.pose)

    def test_on_the_charger_the_robot_knows_where_it_is(self):
        # Its back is to the charger: the marker is behind it, and faces the way the robot does.
        cli = client(50.0, 20.0, math.pi / 2)
        pose = cli.charger.docked()
        self.assertAlmostEqual(pose.x, 50.0)
        self.assertAlmostEqual(pose.y, 20.0 - charger.DOCKED_DISTANCE)
        self.assertAlmostEqual(pose.angle, math.pi / 2)
        self.assertEqual(pose.views, 0)

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
        self.assertAlmostEqual(pose.y, 0.0)
        self.assertEqual(pose.views, 0)

    def test_a_view_tells_the_listeners(self):
        cli = client()
        heard = []
        cli.add_handler(event.EvtChargerObserved, lambda c, pose: heard.append(pose))
        see(cli, 200.0)
        time.sleep(0.05)
        self.assertEqual(len(heard), 1)
