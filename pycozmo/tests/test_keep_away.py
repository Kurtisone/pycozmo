"""

Tests for Keep Away. The client is stood in for, its animations ending as soon as they start; what the cube reports
during one - a tap, a move - is set per animation. The robot's looking and driving are stood in for too, and the
waits taken out.

"""

import math
import random
import unittest
from typing import Callable, Dict
from unittest import mock

from pycozmo import cube_handling, event, keep_away, robot, util
from pycozmo.protocol_encoder import ObjectType

from .test_quick_tap import GameClient


class PounceClient(GameClient):
    """ The fake client, the cube reporting what each animation is set to make it. """

    def __init__(self) -> None:
        super().__init__()
        self.cube = self.cubes[ObjectType.Block_LIGHTCUBE2]
        self.reports: Dict[str, Callable[[], None]] = {}
        self.turn_in_place = mock.Mock()
        self.drive_straight = mock.Mock()
        self.set_lift_height = mock.Mock()

    def tap(self) -> None:
        self.dispatch(event.EvtCubeTapped, self, self.cube, 1)

    def move(self) -> None:
        self.dispatch(event.EvtCubeMovingChange, self, self.cube, True)

    def lift_to(self, height: float) -> None:
        """ The robot says its lift is that high. """
        self.lift_position = robot.LiftPosition(height=util.Distance(mm=height))
        self.dispatch(event.EvtRobotStateUpdated, self)

    def play_anim_group(self, name: str) -> None:
        if name in self.reports:
            self.reports[name]()
        super().play_anim_group(name)


class AwayTestCase(unittest.TestCase):

    def setUp(self):
        self.cli = PounceClient()
        for name, value in (("WAIT_TIME", (0.0, 0.0)), ("MOVE_GRACE", 0.0)):
            patcher = mock.patch.object(keep_away, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # The cube is seen where the pounce reaches it.
        self.seen = True

        def look_for_cube(cli, cube, timeout=3.0, cancel=None):
            if self.seen:
                cli.cubes.place(cube, keep_away.POUNCE_DISTANCE, 0.0, math.pi)
            return self.seen

        looking = mock.patch.object(cube_handling, "look_for_cube", side_effect=look_for_cube)
        looking.start()
        self.addCleanup(looking.stop)

    def game(self, fake_odds: float = 0.0) -> keep_away.KeepAway:
        return keep_away.KeepAway(self.cli, self.cli.cube, keep_away.Skill(fake_odds=fake_odds), random.Random(2))


class TestHand(AwayTestCase):

    def test_caught(self):
        self.cli.reports["CubePouncePounceNormal"] = self.cli.tap
        self.assertEqual(self.game().play_hand(), keep_away.COZMO)

    def test_caught_when_the_lift_stops_on_the_cube(self):
        # The lift came down on the cube: it stopped at 52 mm on a robot. The cube may not say it was tapped.
        self.cli.reports["CubePouncePounceNormal"] = lambda: self.cli.lift_to(58.0)
        self.assertEqual(self.game().play_hand(), keep_away.COZMO)

    def test_caught_though_the_cube_is_pulled_away_after(self):
        def pounce() -> None:
            self.cli.lift_to(58.0)
            self.cli.move()

        self.cli.reports["CubePouncePounceNormal"] = pounce
        self.assertEqual(self.game().play_hand(), keep_away.COZMO)

    def test_a_lift_only_just_above_the_floor_with_the_cube_pulled_away_is_no_catch(self):
        # A clip that sends the lift to 57 mm brought it to 49 on the floor, 4 mm from where a cube stops it.
        def pounce() -> None:
            self.cli.tap()
            self.cli.lift_to(49.0)
            self.cli.move()

        self.cli.reports["CubePouncePounceNormal"] = pounce
        self.assertEqual(self.game().play_hand(), keep_away.PLAYER)

    def test_a_lift_only_just_above_the_floor_with_the_cube_left_and_tapped_is_a_catch(self):
        def pounce() -> None:
            self.cli.tap()
            self.cli.lift_to(49.0)

        self.cli.reports["CubePouncePounceNormal"] = pounce
        self.assertEqual(self.game().play_hand(), keep_away.COZMO)

    def test_a_lift_only_just_above_the_floor_with_nothing_said_is_out_of_reach(self):
        self.cli.reports["CubePouncePounceNormal"] = lambda: self.cli.lift_to(49.0)
        self.assertIsNone(self.game().play_hand())

    def test_a_tap_with_the_lift_at_the_bottom_is_not_a_catch(self):
        # The cube pulled away in time: the lift slammed down on the floor, and the cube said it was tapped, and
        # moved. The player had won the hand and lost it.
        def pounce() -> None:
            self.cli.tap()
            self.cli.lift_to(30.0)
            self.cli.move()

        self.cli.reports["CubePouncePounceNormal"] = pounce
        self.assertEqual(self.game().play_hand(), keep_away.PLAYER)

    def test_a_tap_with_the_lift_at_the_bottom_and_no_move_is_out_of_reach(self):
        def pounce() -> None:
            self.cli.tap()
            self.cli.lift_to(30.0)

        self.cli.reports["CubePouncePounceNormal"] = pounce
        self.assertIsNone(self.game().play_hand())

    def test_the_animations_last_lowering_of_the_lift_is_not_the_pounce(self):
        # The pounce ends with the lift to the bottom, the robot having backed off the cube: it stopped at 60 mm on the
        # cube, rose, and went down to 30 at the end.
        def pounce() -> None:
            for height in (92.0, 75.0, 60.0, 80.0, 92.0, 30.0):
                self.cli.lift_to(height)

        self.cli.reports["CubePouncePounceNormal"] = pounce
        self.assertEqual(self.game().play_hand(), keep_away.COZMO)

    def test_a_lift_that_did_not_come_down_is_no_catch(self):
        self.cli.reports["CubePouncePounceNormal"] = lambda: self.cli.lift_to(92.0)
        self.assertIsNone(self.game().play_hand())

    def test_pulled_away(self):
        self.cli.reports["CubePouncePounceNormal"] = self.cli.move
        self.assertEqual(self.game().play_hand(), keep_away.PLAYER)

    def test_flinching_while_cozmo_waits(self):
        self.cli.reports["CubePounceIdleLiftUp"] = self.cli.move
        self.assertEqual(self.game().play_hand(), keep_away.COZMO)
        self.assertNotIn("CubePouncePounceNormal", self.cli.played)

    def test_flinching_at_a_fake(self):
        self.cli.reports["CubePounceFake"] = self.cli.move
        self.assertEqual(self.game(fake_odds=1.0).play_hand(), keep_away.COZMO)
        self.assertNotIn("CubePouncePounceNormal", self.cli.played)

    def test_a_fake_sat_through_then_the_pounce(self):
        self.cli.reports["CubePouncePounceNormal"] = self.cli.tap
        self.assertEqual(self.game(fake_odds=1.0).play_hand(), keep_away.COZMO)
        self.assertEqual(self.cli.played.count("CubePounceFake"), 2)

    def test_out_of_reach(self):
        self.assertIsNone(self.game().play_hand())

    def test_not_seen(self):
        self.seen = False
        self.assertIsNone(self.game().play_hand())
        self.assertNotIn("CubePounceGetReady", self.cli.played)


class TestPosition(AwayTestCase):

    def test_it_moves_to_where_its_pounce_reaches(self):
        def look_for_cube(cli, cube, timeout=3.0, cancel=None):
            # 200 mm ahead and 30 mm to the left.
            cli.cubes.place(cube, 200.0, 30.0, math.pi)
            return True

        with mock.patch.object(cube_handling, "look_for_cube", side_effect=look_for_cube):
            self.assertTrue(self.game().take_position())
        self.assertAlmostEqual(self.cli.turn_in_place.call_args.args[0].radians, math.atan2(30.0, 200.0))
        self.assertAlmostEqual(self.cli.drive_straight.call_args.args[0].mm,
                               math.hypot(200.0, 30.0) - keep_away.POUNCE_DISTANCE)

    def test_where_it_is_already(self):
        self.assertTrue(self.game().take_position())
        self.cli.turn_in_place.assert_not_called()
        self.cli.drive_straight.assert_not_called()


class TestGame(AwayTestCase):

    def test_always_caught(self):
        self.cli.reports["CubePouncePounceNormal"] = self.cli.tap
        game = self.game()
        self.assertEqual(game.play(), keep_away.COZMO)
        self.assertEqual(game.rounds, {keep_away.COZMO: 2, keep_away.PLAYER: 0})
        self.assertEqual(self.cli.played.count("CubePounceWinHand"), 10)
        self.assertEqual(self.cli.played[-2:], ["CubePounceWinSession", "CubePounceGetOut"])
        self.assertFalse(self.cli.cube.in_use)

    def test_a_cube_gone_for_good_ends_the_game(self):
        self.seen = False
        self.assertIsNone(self.game().play())
        self.assertEqual(self.cli.played[-1], "CubePounceGetOut")


class TestReach(unittest.TestCase):

    def test_beyond_the_fork_by_a_lunge(self):
        # On a robot, the pounces lunged 39 to 57 mm, and caught a cube there every time.
        self.assertEqual(keep_away.POUNCE_DISTANCE, 88.0)
        self.assertGreater(keep_away.POUNCE_DISTANCE - cube_handling.DOCK_DISTANCE, 30.0)
