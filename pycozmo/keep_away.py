"""

Keep Away, a game of Anki's app: the player keeps a cube in front of Cozmo and pulls it away when Cozmo pounces.

The app played it, not the engine, and its rules are not in the resources: these are PyCozmo's reading of the game
as the app presented it. Cozmo raises its lift, waits, and pounces - or only pretends to. Caught, the cube is Cozmo's
point; pulled away in time, the player's; moved while Cozmo only waited or pretended, the player flinched, and the
point is Cozmo's. Five points win a round, two rounds the game.

Cozmo pounces with Anki's animations for the game: a lunge and the lift coming down, which catches the cube if it
is still there. Whether it was, the cube says: it reports a tap when the lift comes down on it, and a
move when the player pulls it away. Before each hand Cozmo looks at the cube again and moves to where its pounce
reaches it, since the player never puts it back quite where it was.

Everything here blocks until done, so it must not run on the thread that dispatches the client's events.

"""

import math
import random
import threading
import time
from typing import Any, List, Optional

from . import cube_handling
from . import cube_lights
from . import event
from . import robot
from . import util
from .cube_behaviors import play_and_wait
from .cube_handling import Cancelled
from .cubes import LightCube, in_use


__all__ = [
    "COZMO",
    "PLAYER",
    "POUNCE_DISTANCE",

    "Skill",
    "KeepAway",
]


#: Who wins.
COZMO = "cozmo"
PLAYER = "player"

#: Points to win a round, and rounds to win the game.
POINTS_TO_WIN_ROUND = 5
ROUNDS_TO_WIN_GAME = 2
#: Where the cube's centre has to be, ahead of the robot's origin, for the pounce to catch it, in mm. On a robot,
#: Anki's three pounces had it 39, 47 and 57 mm further on when the lift came down, and each caught a cube there.
POUNCE_DISTANCE = 88.0
#: How far off that the robot moves to put it right, in mm, and how far off its heading it turns, in radians.
POSITION_TOLERANCE = 10.0
HEADING_TOLERANCE = math.radians(8.0)
#: How long Cozmo waits, lift up, before pouncing or pretending to, in seconds: a time drawn in this range.
WAIT_TIME = (1.0, 4.0)
#: How long after an animation a move of the cube still counts towards it, in seconds.
MOVE_GRACE = 0.3
#: How long the robot looks for the cube before a hand, in seconds, and how many hands in a row it does not see it
#: before it gives the game up.
LOOK_TIMEOUT = 10.0
MAX_MISSED_HANDS = 3


class Skill:
    """ How Cozmo plays. """

    def __init__(self, fake_odds: float = 0.35, max_fakes: int = 2) -> None:
        #: How often Cozmo pretends before it pounces, and how many times at most.
        self.fake_odds = fake_odds
        self.max_fakes = max_fakes


class KeepAway:
    """ A game of Keep Away, with the player's cube. """

    def __init__(self, cli: Any, cube: LightCube, skill: Optional[Skill] = None,
                 rng: Optional[random.Random] = None) -> None:
        self.cli = cli
        self.cube = cube
        self.skill = skill or Skill()
        self.rng = rng or random.Random()
        #: The score of the round being played, and the rounds won.
        self.points = {COZMO: 0, PLAYER: 0}
        self.rounds = {COZMO: 0, PLAYER: 0}
        # What the cube reported during what the robot is doing: taps and moves, when.
        self._taps: List[float] = []
        self._moves: List[float] = []
        self._lock = threading.Lock()

    def play(self, cancel: Optional[threading.Event] = None) -> Optional[str]:
        """ Play a game, and say who won it: COZMO or PLAYER, or None if the cube went and did not come back. """
        self._load_resources()
        with in_use(self.cube):
            play_and_wait(self.cli, "CubePounceGetIn", cancel)
            while max(self.rounds.values()) < ROUNDS_TO_WIN_GAME:
                self.points = {COZMO: 0, PLAYER: 0}
                missed = 0
                while max(self.points.values()) < POINTS_TO_WIN_ROUND:
                    winner = self.play_hand(cancel)
                    if winner is not None:
                        missed = 0
                        self.points[winner] += 1
                        self._hand_over(winner, cancel)
                    else:
                        missed += 1
                        if missed >= MAX_MISSED_HANDS:
                            play_and_wait(self.cli, "CubePounceGetOut", cancel)
                            return None
                winner = COZMO if self.points[COZMO] > self.points[PLAYER] else PLAYER
                self.rounds[winner] += 1
                play_and_wait(self.cli, "CubePounceWinRound" if winner == COZMO else "CubePounceLoseRound", cancel)
            winner = COZMO if self.rounds[COZMO] > self.rounds[PLAYER] else PLAYER
            play_and_wait(self.cli, "CubePounceWinSession" if winner == COZMO else "CubePounceLoseSession", cancel)
            play_and_wait(self.cli, "CubePounceGetOut", cancel)
            return winner

    def play_hand(self, cancel: Optional[threading.Event] = None) -> Optional[str]:
        """ Play one hand, and say who won it: COZMO, PLAYER, or None when the cube was not seen, or out of reach. """
        self._load_resources()
        taps = self.cli.add_handler(event.EvtCubeTapped, self._on_tapped)
        moves = self.cli.add_handler(event.EvtCubeMovingChange, self._on_moving)
        try:
            with in_use(self.cube):
                return self._play_hand(cancel)
        finally:
            self.cli.del_handler(event.EvtCubeTapped, taps)
            self.cli.del_handler(event.EvtCubeMovingChange, moves)

    def _play_hand(self, cancel: Optional[threading.Event]) -> Optional[str]:
        if not self.take_position(cancel):
            return None
        play_and_wait(self.cli, "CubePounceGetReady", cancel)
        # Waiting: a move now is a flinch.
        self._clear()
        self.cli.play_anim_group("CubePounceIdleLiftUp")
        self._pause(self.rng.uniform(*WAIT_TIME), cancel)
        if self._moved():
            return COZMO
        fakes = 0
        while fakes < self.skill.max_fakes and self.rng.random() < self.skill.fake_odds:
            fakes += 1
            self._clear()
            play_and_wait(self.cli, "CubePounceFake", cancel)
            self._pause(MOVE_GRACE, cancel)
            if self._moved():
                return COZMO
            self._pause(self.rng.uniform(0.5, 1.5), cancel)
            if self._moved():
                return COZMO
        self._clear()
        play_and_wait(self.cli, "CubePouncePounceNormal", cancel)
        self._pause(MOVE_GRACE, cancel)
        with self._lock:
            tapped, moved = bool(self._taps), bool(self._moves)
        if tapped:
            return COZMO
        # Not caught: pulled away, or not in reach to begin with.
        return PLAYER if moved else None

    def take_position(self, cancel: Optional[threading.Event] = None) -> bool:
        """ Look for the cube, and move so that the pounce reaches it. Say whether the robot saw it. """
        cli = self.cli
        cli.set_lift_height(cube_handling.CARRY_HEIGHT)
        if not cube_handling.look_for_cube(cli, self.cube, timeout=LOOK_TIMEOUT, cancel=cancel):
            return False
        assert self.cube.pose is not None
        dx, dy = self.cube.pose.x - cli.pose.position.x, self.cube.pose.y - cli.pose.position.y
        bearing = _wrap(math.atan2(dy, dx) - cli.pose.rotation.angle_z.radians)
        if abs(bearing) > HEADING_TOLERANCE:
            cli.turn_in_place(util.Angle(radians=bearing))
        error = math.hypot(dx, dy) - POUNCE_DISTANCE
        if abs(error) > POSITION_TOLERANCE:
            cli.drive_straight(util.Distance(mm=error), speed=robot.DOCK_SPEED)
        return True

    def _hand_over(self, winner: str, cancel: Optional[threading.Event]) -> None:
        if "CubePouncePlayerWin" in self.cli.cubes.light_animations:
            self.cli.cubes.play_lights(self.cube, "CubePouncePlayerLose" if winner == COZMO else "CubePouncePlayerWin")
        play_and_wait(self.cli, "CubePounceWinHand" if winner == COZMO else "CubePounceLoseHand", cancel)

    def _clear(self) -> None:
        with self._lock:
            self._taps.clear()
            self._moves.clear()

    def _moved(self) -> bool:
        with self._lock:
            return bool(self._moves)

    def _on_tapped(self, _: Any, cube: LightCube, taps: int) -> None:
        if cube is self.cube:
            with self._lock:
                self._taps.append(time.perf_counter())

    def _on_moving(self, _: Any, cube: LightCube, moving: bool) -> None:
        if cube is self.cube and moving:
            with self._lock:
                self._moves.append(time.perf_counter())

    @staticmethod
    def _pause(seconds: float, cancel: Optional[threading.Event]) -> None:
        if cancel is None:
            time.sleep(seconds)
        elif cancel.wait(seconds):
            raise Cancelled()

    def _load_resources(self) -> None:
        if "CubePouncePlayerWin" not in self.cli.cubes.light_animations:
            self.cli.cubes.light_animations.update(cube_lights.load_cube_light_animations(
                str(util.get_cozmo_asset_dir())))


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
