"""

Tests for Memory Match. The client is stood in for, its animations ending as soon as they start; the player taps from
a thread. The waits are taken out.

"""

import math
import random
import threading
import unittest
from typing import List
from unittest import mock

from pycozmo import cube_lights, event, lights, memory_match
from pycozmo.protocol_encoder import ObjectType

from .test_quick_tap import GameClient

CUBES = (ObjectType.Block_LIGHTCUBE1, ObjectType.Block_LIGHTCUBE2, ObjectType.Block_LIGHTCUBE3)


class MatchTestCase(unittest.TestCase):

    def setUp(self):
        self.cli = GameClient()
        self.cubes = [self.cli.cubes[object_type] for object_type in CUBES]
        for name in ("FLASH_TIME", "GAP_TIME", "TAP_FLASH_TIME", "TAP_DEBOUNCE"):
            patcher = mock.patch.object(memory_match, name, 0.0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def game(self, span: int = 20, falloff: float = 0.0) -> memory_match.MemoryMatch:
        return memory_match.MemoryMatch(self.cli, self.cubes, memory_match.Skill(span, falloff), random.Random(3))

    def player_taps(self, indexes: List[int]) -> None:
        def tap():
            for index in indexes:
                threading.Event().wait(0.02)
                self.cli.dispatch(event.EvtCubeTapped, self.cli, self.cubes[index], 1)

        thread = threading.Thread(target=tap, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2.0)


class TestPattern(MatchTestCase):

    def test_no_cube_twice_in_a_row(self):
        pattern = self.game().pattern(50)
        self.assertEqual(len(pattern), 50)
        self.assertTrue(all(0 <= index < 3 for index in pattern))
        self.assertTrue(all(a != b for a, b in zip(pattern, pattern[1:])))

    def test_the_cubes_light_up_one_after_another_in_their_colours(self):
        with mock.patch.object(self.cli.cubes, "show_lights") as show_lights:
            self.game().show_pattern([2, 0, 1])
        lit = [(self.cubes.index(args[0]), args[2][0].on_color) for args, _ in show_lights.call_args_list
               if args[2][0].on_color != lights.off.to_int16()]
        self.assertEqual(lit, [(i, cube_lights.steady(memory_match.COLORS[i]).on_color) for i in (2, 0, 1)])

    def test_three_cubes_or_none(self):
        with self.assertRaises(ValueError):
            memory_match.MemoryMatch(self.cli, self.cubes[:2])


class TestPlayer(MatchTestCase):

    def test_the_right_taps(self):
        self.player_taps([1, 0, 2])
        self.assertTrue(self.game().player_repeats([1, 0, 2]))

    def test_a_knock_a_cube_says_twice_is_one_tap(self):
        # A cube reported one knock twice, 0.12 s apart, and the second counted for a wrong tap.
        self.player_taps([1, 1, 0, 0, 0, 2])
        with mock.patch.object(memory_match, "TAP_DEBOUNCE", 0.3):
            self.assertTrue(self.game().player_repeats([1, 0, 2]))

    def test_a_wrong_one(self):
        self.player_taps([1, 2])
        with mock.patch.object(self.cli.cubes, "show_lights") as show_lights:
            self.assertFalse(self.game().player_repeats([1, 0, 2]))
        # The wrong cube lights up red.
        self.assertIn((self.cubes[2], lights.red.to_int16()),
                      [(args[0], args[2][0].on_color) for args, _ in show_lights.call_args_list])

    def test_too_slow(self):
        self.player_taps([1])
        with mock.patch.object(memory_match, "INPUT_TIMEOUT", 0.2):
            self.assertFalse(self.game().player_repeats([1, 0]))


class TestCozmo(MatchTestCase):

    def place(self, index: int, degrees: float) -> None:
        angle = math.radians(degrees)
        self.cli.cubes.place(self.cubes[index], 150.0 * math.cos(angle), 150.0 * math.sin(angle), math.pi)

    def test_it_points_where_the_cubes_are(self):
        self.place(0, 30.0)
        self.place(1, 5.0)
        self.place(2, -60.0)
        game = self.game()
        self.assertEqual([game.point_trigger(cube) for cube in self.cubes],
                         ["MemoryMatchPointLeftSmall", "MemoryMatchPointCenter", "MemoryMatchPointRightBig"])
        self.assertEqual(game.point_trigger(self.cubes[2], fast=True), "MemoryMatchPointRightBigFast")
        # One not seen is pointed at straight ahead.
        self.cubes[0].pose = None
        self.assertEqual(game.point_trigger(self.cubes[0]), "MemoryMatchPointCenter")

    def test_within_its_span_it_gets_it_right(self):
        self.assertTrue(self.game().cozmo_repeats([0, 1, 2, 1]))
        self.assertEqual(len([name for name in self.cli.played if name.startswith("MemoryMatchPoint")]), 4)

    def test_beyond_it_less_and_less(self):
        skill = memory_match.Skill(span=4, falloff=0.15)
        self.assertEqual([round(skill.success_odds(length), 2) for length in (3, 4, 5, 8, 11)],
                         [1.0, 1.0, 0.85, 0.4, 0.0])
        self.assertFalse(self.game(span=0, falloff=1.0).cozmo_repeats([0, 1, 2]))


class TestGame(MatchTestCase):

    def test_whoever_gets_it_wrong_first_loses(self):
        game = self.game()
        with mock.patch.object(game, "player_repeats", side_effect=[True, True, False]), \
                mock.patch.object(game, "show_pattern") as show_pattern:
            self.assertEqual(game.play(), memory_match.COZMO)
        self.assertEqual([len(args[0]) for args, _ in show_pattern.call_args_list], [3, 4, 5])
        self.assertEqual(game.best, {memory_match.COZMO: 5, memory_match.PLAYER: 4})
        self.assertEqual(self.cli.played[-1], "MemoryMatchCozmoWinGame")

    def test_both_wrong_and_the_round_is_played_again(self):
        game = self.game()
        with mock.patch.object(game, "player_repeats", side_effect=[False, True]), \
                mock.patch.object(game, "cozmo_repeats", side_effect=[False, False]), \
                mock.patch.object(game, "show_pattern") as show_pattern:
            self.assertEqual(game.play(), memory_match.PLAYER)
        self.assertEqual([len(args[0]) for args, _ in show_pattern.call_args_list], [3, 3])
        self.assertEqual(self.cli.played[-1], "MemoryMatchPlayerWinGame")

    def test_solo(self):
        game = self.game()
        with mock.patch.object(game, "player_repeats", side_effect=[True, True, False]), \
                mock.patch.object(game, "show_pattern"):
            self.assertEqual(game.play_solo(), 4)
        self.assertEqual(self.cli.played[-2:], ["MemoryMatchPlayerLoseHandSolo", "MemoryMatchSoloGameOver"])

    def test_the_cubes_are_in_play(self):
        game = self.game()
        in_use: List[bool] = []

        def player_repeats(pattern, cancel=None):
            in_use.append(all(cube.in_use for cube in self.cubes))
            return False

        with mock.patch.object(game, "player_repeats", side_effect=player_repeats), \
                mock.patch.object(game, "show_pattern"):
            game.play()
        self.assertEqual(in_use, [True])
        self.assertFalse(any(cube.in_use for cube in self.cubes))


class TestFacing(MatchTestCase):

    def test_the_middle_of_the_cubes(self):
        self.cli.cubes.place(self.cubes[0], 100.0, 100.0, math.pi)
        self.cli.cubes.place(self.cubes[1], 100.0, 0.0, math.pi)
        with mock.patch.object(self.cli, "turn_in_place", create=True) as turn_in_place:
            self.assertTrue(memory_match.face_cubes(self.cli, self.cubes))
        self.assertAlmostEqual(turn_in_place.call_args.args[0].radians, math.atan2(50.0, 100.0))
        for cube in self.cubes:
            cube.pose = None
        self.assertFalse(memory_match.face_cubes(self.cli, self.cubes))
