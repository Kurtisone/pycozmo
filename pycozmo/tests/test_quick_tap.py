"""

Tests for Quick Tap. The client is stood in for, its animations ending as soon as they start and Cozmo's tap
reported by its cube at once; the player taps from a timer. The waits are shortened.

"""

import random
import threading
import time
import unittest
from typing import Any, List
from unittest import mock

import pycozmo
from pycozmo import event, quick_tap
from pycozmo.protocol_encoder import ObjectType

from .test_brain import cozmo_assets_available
from .test_cubes import FakeClient as CubesClient, pattern


class Everything(dict):
    """ Animation groups the fake has: all of them. """

    def __contains__(self, name: object) -> bool:
        return True


class GameClient(CubesClient):
    """ What the game needs of a client: cubes, animations, and events. """

    def __init__(self) -> None:
        super().__init__()
        self.cubes = pycozmo.cubes.Cubes(self)
        self.cubes.light_animations = {quick_tap.WIN_LIGHTS: [pattern((255, 255, 255))],
                                       quick_tap.LOSE_LIGHTS: [pattern((255, 0, 0))]}
        self.animation_groups = Everything()
        self.played: List[str] = []
        #: Whether Cozmo's cube reports the lift coming down on it.
        self.cube_reports_taps = True
        # Moving about, for the tests that look at it.
        self.turn_in_place: Any = None
        self.drive_straight: Any = None

    def play_anim_group(self, name: str) -> None:
        self.played.append(name)
        if name == quick_tap.TAP and self.cube_reports_taps:
            self.dispatch(event.EvtCubeTapped, self, self.cubes[ObjectType.Block_LIGHTCUBE1], 1)
        self.dispatch(event.EvtAnimationCompleted, self)


class GameTestCase(unittest.TestCase):

    def setUp(self):
        self.cli = GameClient()
        self.cozmo_cube = self.cli.cubes[ObjectType.Block_LIGHTCUBE1]
        self.player_cube = self.cli.cubes[ObjectType.Block_LIGHTCUBE2]
        for name, value in (("DARK_TIME", (0.0, 0.0)), ("TAP_WINDOW", 0.3), ("ANIMATION_DELAY", 0.0)):
            patcher = mock.patch.object(quick_tap, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def game(self, match: bool, reaction: float = 0.05, fake_odds: float = 0.0,
             mistake_odds: float = 0.0) -> quick_tap.QuickTap:
        patcher = mock.patch.object(quick_tap, "MATCH_ODDS", 1.0 if match else 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        skill = quick_tap.Skill(reaction=(reaction, reaction), fake_odds=fake_odds, mistake_odds=mistake_odds)
        return quick_tap.QuickTap(self.cli, self.cozmo_cube, self.player_cube, skill, random.Random(1))

    def player_taps(self, after: float) -> None:
        timer = threading.Timer(after, self.cli.dispatch, (event.EvtCubeTapped, self.cli, self.player_cube, 1))
        timer.start()
        self.addCleanup(timer.cancel)


class TestHand(GameTestCase):

    def test_on_a_match_the_quicker_wins(self):
        game = self.game(match=True, reaction=0.2)
        self.player_taps(0.05)
        self.assertEqual(game.play_hand(), quick_tap.PLAYER)
        self.assertNotIn(quick_tap.TAP, self.cli.played)

    def test_the_tap_is_asked_for_before_the_robot_is_to_hit_the_cube(self):
        # The robot takes ANIMATION_DELAY to start an animation, so Cozmo's tap, to land 0.5 s after the lights, is
        # asked for 0.3 s after them.
        asked: List[float] = []
        play = self.cli.play_anim_group

        def playing(name: str, *args: object) -> None:
            if name == quick_tap.TAP:
                asked.append(time.perf_counter())
            play(name, *args)

        game = self.game(match=True, reaction=0.5)
        with mock.patch.object(quick_tap, "ANIMATION_DELAY", 0.3), \
                mock.patch.object(self.cli, "play_anim_group", side_effect=playing):
            start = time.perf_counter()
            self.assertEqual(game.play_hand(), quick_tap.COZMO)
        self.assertEqual(len(asked), 1)
        self.assertTrue(0.1 < asked[0] - start < 0.4, asked[0] - start)

    def test_a_reaction_shorter_than_the_robots_delay_is_asked_for_at_once(self):
        asked: List[float] = []
        play = self.cli.play_anim_group

        def playing(name: str, *args: object) -> None:
            if name == quick_tap.TAP:
                asked.append(time.perf_counter())
            play(name, *args)

        game = self.game(match=True, reaction=0.1)
        with mock.patch.object(quick_tap, "ANIMATION_DELAY", 0.4), \
                mock.patch.object(self.cli, "play_anim_group", side_effect=playing):
            start = time.perf_counter()
            self.assertEqual(game.play_hand(), quick_tap.COZMO)
        self.assertLess(asked[0] - start, 0.15)

    def test_cozmo_taps_on_a_match(self):
        in_use: List[bool] = []
        dispatch = self.cli.dispatch

        def dispatching(*args: object) -> None:
            in_use.append(self.cozmo_cube.in_use)
            dispatch(*args)

        with mock.patch.object(self.cli, "dispatch", side_effect=dispatching):
            self.assertEqual(self.game(match=True).play_hand(), quick_tap.COZMO)
        self.assertIn(quick_tap.TAP, self.cli.played)
        # The cubes are in play during the hand, the tap no news to the brain; not after.
        self.assertTrue(all(in_use))
        self.assertFalse(self.cozmo_cube.in_use)

    def test_tapping_on_different_colours_loses(self):
        game = self.game(match=False)
        self.player_taps(0.05)
        self.assertEqual(game.play_hand(), quick_tap.COZMO)

    def test_so_does_cozmo_s_mistake(self):
        self.assertEqual(self.game(match=False, mistake_odds=1.0).play_hand(), quick_tap.PLAYER)

    def test_a_fake_is_not_a_tap(self):
        game = self.game(match=False, fake_odds=1.0)
        self.assertIsNone(game.play_hand())
        self.assertIn(quick_tap.FAKE, self.cli.played)

    def test_a_tap_the_cube_does_not_report_counts_when_the_animation_ends(self):
        self.cli.cube_reports_taps = False
        self.assertEqual(self.game(match=True).play_hand(), quick_tap.COZMO)

    def test_a_tap_that_never_ends_does_not_hold_the_hand_up(self):
        self.cli.cube_reports_taps = False
        with mock.patch.object(self.cli, "play_anim_group"), mock.patch.object(quick_tap, "ANIMATION_TIMEOUT", 0.2):
            self.assertIsNone(self.game(match=True).play_hand())

    def test_the_colours(self):
        with mock.patch.object(self.cli.cubes, "show_lights") as show_lights:
            self.game(match=False).play_hand()
        shown = [(args[0], args[2][0].on_color) for args, _ in show_lights.call_args_list]
        # Both dark, then both lit, in different colours.
        self.assertEqual(len(shown), 4)
        self.assertEqual(shown[0][1], shown[1][1])
        self.assertNotEqual(shown[2][1], shown[3][1])


class TestPlace(GameTestCase):

    def setUp(self):
        super().setUp()
        self.cli.turn_in_place = mock.Mock()
        self.cli.drive_straight = mock.Mock()

    def test_the_robot_goes_back_to_where_it_started(self):
        game = self.game(match=False)
        game.play_hand()
        self.cli.turn_in_place.assert_not_called()
        # The animations turned it 6 degrees and moved it 10 mm on.
        self.cli.pose = pycozmo.util.Pose(10.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(degrees=6.0))
        game.play_hand()
        self.assertAlmostEqual(self.cli.turn_in_place.call_args.args[0].degrees, -6.0)
        self.assertAlmostEqual(self.cli.drive_straight.call_args.args[0].mm, -10.0)

    def test_a_new_frame_makes_the_place_the_robot_is_at(self):
        # A robot that had been lifted began its position again at zero, in a new frame; the place it held was in the
        # old one, and going back to it drove the robot 130 mm at its cube.
        game = self.game(match=False)
        self.cli.pose = pycozmo.util.Pose(297.0, 66.0, 0.0, angle_z=pycozmo.util.Angle(degrees=-15.0), origin_id=1)
        game.play_hand()
        self.cli.pose = pycozmo.util.Pose(0.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(degrees=-1.0), origin_id=2)
        game.play_hand()
        self.cli.turn_in_place.assert_not_called()
        self.cli.drive_straight.assert_not_called()
        # And it goes back to that place after.
        self.cli.pose = pycozmo.util.Pose(10.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(degrees=-1.0), origin_id=2)
        game.play_hand()
        self.assertAlmostEqual(self.cli.drive_straight.call_args.args[0].mm, -10.0, delta=0.1)

    def test_it_does_not_drive_a_long_way_back(self):
        game = self.game(match=False)
        game.play_hand()
        self.cli.pose = pycozmo.util.Pose(130.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(degrees=0.0))
        game.play_hand()
        self.cli.drive_straight.assert_not_called()

    def test_a_little_drift_is_let_be(self):
        game = self.game(match=False)
        game.play_hand()
        self.cli.pose = pycozmo.util.Pose(2.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(degrees=1.0))
        game.play_hand()
        self.cli.turn_in_place.assert_not_called()
        self.cli.drive_straight.assert_not_called()


class TestGame(GameTestCase):

    def test_five_points_a_round_two_rounds_the_game(self):
        # The player never taps: Cozmo wins every hand.
        game = self.game(match=True)
        self.assertEqual(game.play(), quick_tap.COZMO)
        self.assertEqual(game.rounds, {quick_tap.COZMO: 2, quick_tap.PLAYER: 0})
        self.assertEqual(self.cli.played.count(quick_tap.WIN_HAND), 10)
        # Five to nothing, and two rounds to nothing: big wins.
        self.assertEqual(self.cli.played.count("ag_speedtap_winround_intensity02"), 2)
        self.assertEqual(self.cli.played[-1], "ag_speedtap_wingame_intensity03")

    def test_cancelling(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(pycozmo.cube_handling.Cancelled):
            self.game(match=True).play(cancel)


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestResources(unittest.TestCase):

    def test_anki_s_animations_and_lights(self):
        groups, light_animations = quick_tap.load_resources(str(pycozmo.util.get_cozmo_asset_dir()))
        for name in (quick_tap.TAP, quick_tap.FAKE, quick_tap.WAIT, quick_tap.WIN_HAND, quick_tap.LOSE_HAND,
                     quick_tap.GET_OUT, "ag_speedtap_winround_intensity01", "ag_speedtap_losegame_intensity03"):
            self.assertIn(name, groups)
        self.assertEqual([member.name for member in groups[quick_tap.TAP].members],
                         ["anim_speedtap_tap_01", "anim_speedtap_tap_02", "anim_speedtap_tap_03"])
        self.assertEqual(sorted(light_animations), [quick_tap.LOSE_LIGHTS, quick_tap.WIN_LIGHTS])
