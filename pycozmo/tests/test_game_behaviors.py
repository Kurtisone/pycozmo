"""

Tests for the behaviors that ask for a game. The client is stood in for, its animations ending as soon as they
start; Quick Tap itself is stood in for too, tested in test_quick_tap.py.

"""

import math
import threading
import unittest
from typing import Any
from unittest import mock

import pycozmo
from pycozmo import event, game_behaviors, memory_match, quick_tap
from pycozmo.protocol_encoder import ObjectType

from .test_brain import cozmo_assets_available
from .test_cube_behaviors import ScriptClient

CUBE1 = ObjectType.Block_LIGHTCUBE1
CUBE2 = ObjectType.Block_LIGHTCUBE2

#: RequestSpeedTap, as the resources configure it, but for what the behavior does not read.
CONF = {
    "behaviorClass": "RequestGameSimple",
    "behaviorID": "RequestSpeedTap",
    "requiredUnlockId": "QuickTapGame",
    "one_block_config": {
        "initial_animName": "RequestGameSpeedTapInitial1",
        "preDrive_animName": "RequestGameSpeedTapPreDrive1",
        "request_animName": "RequestGameSpeedTapRequest1",
        "idle_animName": "RequestGameSpeedTapIdle1",
        "deny_animName": "RequestGameSpeedTapDeny1",
    },
}


class GameClient(ScriptClient):
    """ The fake client, with real cubes, two of them connected and one seen. """

    def __init__(self) -> None:
        super().__init__()
        self.pose = pycozmo.util.Pose(0.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(radians=0.0))
        self.cubes = pycozmo.cubes.Cubes(self)
        for cube in self.cubes:
            cube.connected = cube.object_type in (CUBE1, CUBE2)
        self.cubes.place(self.cubes[CUBE1], 200.0, 0.0, math.pi)
        self.animation_groups.update({name: None for name in (
            "RequestGameSpeedTapInitial1", "RequestGameSpeedTapRequest1", "RequestGameSpeedTapIdle1",
            "RequestGameSpeedTapDeny1", "RequestGameSpeedTapAccept1", "MemoryMatchCozmoGetOut")})


class TestRequestGame(unittest.TestCase):

    def setUp(self):
        self.cli = GameClient()
        self.needs = mock.Mock()
        patcher = mock.patch.object(game_behaviors, "REQUEST_TIMEOUT", 0.3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, **conf: Any) -> game_behaviors.BehaviorRequestGameSimple:
        return game_behaviors.BehaviorRequestGameSimple(self.cli, dict(CONF, **conf), self.needs)

    def run_script(self, script: Any) -> None:
        script.activate()
        script.thread.join(5.0)
        self.assertFalse(script.thread.is_alive())

    def answers(self):
        return [args[1] for evt, args in self.cli.conn.events if evt is event.EvtGameRequestAnswered]

    def test_it_asks_for_quick_tap_with_two_cubes_and_one_seen(self):
        self.assertTrue(self.make().wants_to_run())
        self.assertFalse(self.make(requiredUnlockId="MemoryMatchGame").wants_to_run())
        self.cli.cubes[CUBE2].connected = False
        self.assertFalse(self.make().wants_to_run())

    def test_a_tap_during_the_question_is_heard(self):
        player_cube = self.cli.cubes[CUBE2]
        played = self.cli.play_anim_group

        def play_anim_group(name: str) -> None:
            if name == "RequestGameSpeedTapInitial1":
                self.cli.dispatch(event.EvtCubeTapped, self.cli, player_cube, 1)
            played(name)

        with mock.patch.object(self.cli, "play_anim_group", side_effect=play_anim_group), \
                mock.patch.object(game_behaviors.BehaviorRequestGameSimple, "play_quick_tap"):
            self.run_script(self.make())
        self.assertEqual(self.answers(), [True])

    def test_memory_match_wants_the_three_cubes(self):
        script = self.make(requiredUnlockId="MemoryMatchGame")
        self.assertFalse(script.wants_to_run())
        self.cli.cubes[ObjectType.Block_LIGHTCUBE3].connected = True
        self.assertTrue(script.wants_to_run())

    def test_memory_match_is_played(self):
        self.cli.cubes[ObjectType.Block_LIGHTCUBE3].connected = True
        timer = threading.Timer(0.1, self.cli.dispatch, (event.EvtCubeTapped, self.cli, self.cli.cubes[CUBE2], 1))
        timer.start()
        self.addCleanup(timer.cancel)
        with mock.patch.object(memory_match, "face_cubes") as face_cubes, \
                mock.patch.object(memory_match.MemoryMatch, "play", return_value=memory_match.PLAYER):
            self.run_script(self.make(requiredUnlockId="MemoryMatchGame"))
        self.assertEqual(len(face_cubes.call_args.args[1]), 3)
        self.needs.apply_action.assert_called_once_with("MemoryMatchLose")
        self.assertEqual(self.cli.played[-1], "MemoryMatchCozmoGetOut")

    def test_no_tap_is_a_no(self):
        self.run_script(self.make())
        self.assertEqual(self.cli.played[:3], ["RequestGameSpeedTapInitial1", "RequestGameSpeedTapRequest1",
                                               "RequestGameSpeedTapIdle1"])
        self.assertEqual(self.cli.played[-1], "RequestGameSpeedTapDeny1")
        self.assertEqual(self.answers(), [False])

    def test_a_tap_is_a_yes_and_the_cube_the_player_s(self):
        player_cube = self.cli.cubes[CUBE2]
        timer = threading.Timer(0.1, self.cli.dispatch, (event.EvtCubeTapped, self.cli, player_cube, 1))
        timer.start()
        self.addCleanup(timer.cancel)
        with mock.patch.object(quick_tap, "take_position", return_value=True) as take_position, \
                mock.patch.object(quick_tap, "leave_position"), \
                mock.patch.object(quick_tap.QuickTap, "play", return_value=quick_tap.COZMO):
            self.run_script(self.make())
        self.assertEqual(self.answers(), [True])
        self.assertIn("RequestGameSpeedTapAccept1", self.cli.played)
        # The robot plays with the cube it knows of; the need action is its win.
        self.assertIs(take_position.call_args.args[1], self.cli.cubes[CUBE1])
        self.needs.apply_action.assert_called_once_with("QuickTapWin")


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestInTheBrain(unittest.TestCase):

    def test_play_with_humans_asks_the_brain(self):
        brain = pycozmo.brain.Brain(pycozmo.client.Client())
        self.assertIsInstance(brain.behaviors["RequestSpeedTap"], game_behaviors.BehaviorRequestGameSimple)
        activity = brain.activities["PlayWithHumans"]
        # No cube: nothing to ask for.
        self.assertFalse(activity.wants_to_run(brain.get_mood(), now=1000.0))
        with mock.patch.object(brain.behaviors["RequestSpeedTap"], "wants_to_run", return_value=True):
            self.assertTrue(activity.wants_to_run(brain.get_mood(), now=1000.0))
        brain.on_game_request_answered(brain.cli, False)
        self.assertEqual(activity.strategy.rejections, 1)
