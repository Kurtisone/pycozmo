"""

Behaviors that ask the player for a game, and play it.

In Anki's app, the robot asked from its behavior - RequestGameSimple, one configuration per game - and the player
answered on the phone. Here the player answers on a cube: a tap takes the game up, and that cube is theirs; no tap
before the request times out turns it down. The robot does not look for the player's face first, nor carry a cube
over to them as the app's robot did: it asks from where it is.

Quick Tap and Memory Match are played; the requests for the others never want to run.

"""

import threading
import time
from typing import Any, List, Optional

from . import cube_handling
from . import event
from . import memory_match
from . import quick_tap
from .cube_behaviors import BehaviorScript, play_and_wait, usable_cubes
from .cubes import LightCube, in_use


__all__ = [
    "REQUEST_TIMEOUT",

    "BehaviorRequestGameSimple",
]


#: How long the robot waits for an answer, in seconds.
REQUEST_TIMEOUT = 15.0

#: The games played, by the unlock their request needs.
GAMES = ("QuickTapGame", "MemoryMatchGame")


class BehaviorRequestGameSimple(BehaviorScript):
    """
    RequestGameSimple - ask the player for a game, and play it if they take it up.

    Its configuration names the animations of the request, with a cube known or without: the one-cube ones are
    played, since a game needs one.
    """

    def game(self) -> Optional[str]:
        game = self.conf.get("requiredUnlockId")
        return game if game in GAMES else None

    def wants_to_run(self) -> bool:
        game = self.game()
        if game is None or self.cli.cubes.carried is not None:
            return False
        connected = [cube for cube in self.cli.cubes if cube.connected]
        if game == "QuickTapGame":
            # A cube for each, and the robot's where it knows.
            return len(connected) >= 2 and bool(usable_cubes(self.cli))
        # The three cubes, one of them seen at least, to face them.
        return len(connected) == len(memory_match.COLORS) and any(cube.pose is not None for cube in connected)

    def animation(self, name: str) -> str:
        """ One of the request's animation triggers, as the one-cube configuration names it. """
        return str(self.conf.get("one_block_config", {}).get(name + "_animName", ""))

    def script(self, cancel: threading.Event) -> None:
        connected = [cube for cube in self.cli.cubes if cube.connected]
        # The player may answer as soon as the robot starts asking.
        tapped: List[LightCube] = []
        answered = threading.Event()

        def on_tapped(_: Any, cube: LightCube, taps: int) -> None:
            if cube in connected:
                tapped.append(cube)
                answered.set()

        handler = self.cli.add_handler(event.EvtCubeTapped, on_tapped)
        try:
            with in_use(*connected):
                self.play(self.animation("initial"), cancel)
                self.play(self.animation("request"), cancel)
                self.wait_for_answer(answered, cancel)
        finally:
            self.cli.del_handler(event.EvtCubeTapped, handler)
        player_cube = tapped[0] if tapped else None
        self.cli.conn.post_event(event.EvtGameRequestAnswered, self.cli, player_cube is not None)
        if player_cube is None:
            self.play(self.animation("deny"), cancel)
            return
        # The confirmation the app played has no entry in the configuration; its trigger follows the others'.
        self.play(self.animation("initial").replace("Initial", "Accept"), cancel)
        if self.game() == "QuickTapGame":
            self.play_quick_tap(player_cube, cancel)
        else:
            self.play_memory_match(cancel)

    def wait_for_answer(self, answered: threading.Event, cancel: threading.Event) -> None:
        """ Wait for the player's answer, or for the request to time out, playing the request's idle meanwhile. """
        deadline = time.perf_counter() + REQUEST_TIMEOUT
        while not answered.is_set() and time.perf_counter() < deadline:
            self.cli.play_anim_group(self.animation("idle"))
            answered.wait(1.0)
            if cancel.is_set():
                raise cube_handling.Cancelled()

    def play_quick_tap(self, player_cube: LightCube, cancel: threading.Event) -> None:
        # The robot's cube is the nearest it knows of, the player's aside. The player may have taken the only one it
        # knew of: it then looks for another.
        cubes = [cube for cube in usable_cubes(self.cli) if cube is not player_cube]
        if cubes:
            cozmo_cube = cubes[0]
        else:
            others = [cube for cube in self.cli.cubes if cube.connected and cube is not player_cube]
            if not others or not cube_handling.find_cube(self.cli, others[0], cancel=cancel):
                return
            cozmo_cube = others[0]
        if not quick_tap.take_position(self.cli, cozmo_cube, cancel=cancel):
            return
        try:
            winner = quick_tap.QuickTap(self.cli, cozmo_cube, player_cube).play(cancel)
        finally:
            if not cancel.is_set():
                quick_tap.leave_position(self.cli)
        # The needs are the robot's: it won, or it lost.
        if self.needs is not None:
            self.needs.apply_action("QuickTapWin" if winner == quick_tap.COZMO else "QuickTapLose")

    def play_memory_match(self, cancel: threading.Event) -> None:
        cubes = sorted((cube for cube in self.cli.cubes if cube.connected), key=lambda cube: cube.object_type.value)
        memory_match.face_cubes(self.cli, cubes, cancel)
        winner = memory_match.MemoryMatch(self.cli, cubes).play(cancel)
        if winner is not None and self.needs is not None:
            self.needs.apply_action("MemoryMatchWin" if winner == memory_match.COZMO else "MemoryMatchLose")
        play_and_wait(self.cli, "MemoryMatchCozmoGetOut", cancel)
