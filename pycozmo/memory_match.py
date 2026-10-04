"""

Memory Match, a game of Anki's app: the three cubes light up one after another, and the player repeats the pattern by
tapping them in the same order. So does Cozmo, pointing at them.

The app played it, not the engine, and its rules are not in the resources: these are PyCozmo's reading of the game
as the app presented it. Each round shows a pattern one longer than the last, from three. The player repeats it, then
Cozmo does; whoever gets it wrong is out, and the other wins. If both do in the same round, the round is played again
with a new pattern as long. Played solo, the player goes on until a mistake, and scores the longest pattern repeated.

Cozmo points at a cube by turning to it with Anki's animations for the game - a nod straight ahead, a small turn or a
big one - chosen by where the cube is from where the robot faces, again before each cube. It remembers four cubes
without fail, and is less and less sure beyond.

Everything here blocks until done, so it must not run on the thread that dispatches the client's events.

"""

import math
import random
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from . import event
from . import lights
from . import util
from .cube_behaviors import play_and_wait
from .cube_handling import Cancelled
from .cube_lights import steady
from .cubes import LightCube, in_use


__all__ = [
    "COZMO",
    "PLAYER",
    "START_LENGTH",
    "COLORS",

    "Skill",
    "MemoryMatch",

    "face_cubes",
]


#: Who wins.
COZMO = "cozmo"
PLAYER = "player"

#: How long the first pattern is, and the longest there can be.
START_LENGTH = 3
MAX_LENGTH = 20
#: How long a cube stays lit in a pattern, and dark before the next, in seconds.
FLASH_TIME = 0.5
GAP_TIME = 0.25
#: How long a tapped cube stays lit, in seconds.
TAP_FLASH_TIME = 0.3
#: How long the player has for each tap, in seconds. A cube misses some knocks and shows no sign of it - one in five
#: on a robot - and a player who taps again once they see no light needs the time: with 6 s, a retry that came 6.1 s
#: after the tap before it lost the hand.
INPUT_TIMEOUT = 10.0
#: How long after a cube has said it was tapped it is taken to be saying the same tap again, in seconds. On a robot a
#: cube said so twice 0.12 s apart for one knock, and up to three times, 0.13 s apart, in another game: the second
#: was taken for a wrong tap, and the player lost a hand they had won.
TAP_DEBOUNCE = 0.3
#: Each cube's colour, in the order the cubes are given.
COLORS = (
    lights.Color(name="blue", rgb=(0, 0, 255)),
    lights.Color(name="green", rgb=(0, 255, 0)),
    lights.Color(name="yellow", rgb=(255, 200, 0)),
)
#: The colour of a wrong tap.
WRONG_COLOR = lights.red
#: The name the game's lights are shown under: see Cubes.show_lights().
LIGHTS_NAME = "MemoryMatch"
#: How far off the robot's heading a cube is pointed at straight ahead, or with a small turn, in radians: half way
#: between Anki's turns, which on a robot were 20 to 27 degrees small, and 46 big.
CENTER_ANGLE = math.radians(12.0)
SMALL_TURN_ANGLE = math.radians(35.0)
#: From how long a pattern Cozmo points the quick way.
FAST_LENGTH = 6


class Skill:
    """ How good Cozmo is at the game. """

    def __init__(self, span: int = 4, falloff: float = 0.15) -> None:
        #: How long a pattern Cozmo always gets right.
        self.span = span
        #: How much less sure it is of each cube beyond.
        self.falloff = falloff

    def success_odds(self, length: int) -> float:
        return max(0.0, 1.0 - self.falloff * max(0, length - self.span))


def face_cubes(cli: Any, cubes: Sequence[LightCube], cancel: Optional[threading.Event] = None) -> bool:
    """ Turn to face the middle of the cubes the robot knows of. Say whether it knew of any. """
    known = [cube.pose for cube in cubes if cube.pose is not None]
    if not known:
        return False
    x = sum(pose.x for pose in known) / len(known)
    y = sum(pose.y for pose in known) / len(known)
    heading = math.atan2(y - cli.pose.position.y, x - cli.pose.position.x)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    cli.turn_in_place(util.Angle(radians=_wrap(heading - cli.pose.rotation.angle_z.radians)))
    return True


class MemoryMatch:
    """ A game of Memory Match, over three cubes. Cozmo should face them: see face_cubes(). """

    def __init__(self, cli: Any, cubes: Sequence[LightCube], skill: Optional[Skill] = None,
                 rng: Optional[random.Random] = None) -> None:
        if len(cubes) != len(COLORS):
            raise ValueError("Memory Match takes {} cubes.".format(len(COLORS)))
        self.cli = cli
        self.cubes = list(cubes)
        self.skill = skill or Skill()
        self.rng = rng or random.Random()
        #: The longest pattern repeated, by each.
        self.best = {COZMO: 0, PLAYER: 0}

    def play(self, cancel: Optional[threading.Event] = None) -> Optional[str]:
        """ Play against Cozmo, and say who won: COZMO or PLAYER, or None if the patterns grew too long for both. """
        with in_use(*self.cubes):
            try:
                return self._play(cancel)
            finally:
                self._all_off()

    def _play(self, cancel: Optional[threading.Event]) -> Optional[str]:
        length = START_LENGTH
        while length <= MAX_LENGTH:
            pattern = self.pattern(length)
            self.show_pattern(pattern, cancel)
            player = self.player_repeats(pattern, cancel)
            play_and_wait(self.cli, "MemoryMatchPlayerWinHand" if player else "MemoryMatchPlayerLoseHand", cancel)
            cozmo = self.cozmo_repeats(pattern, cancel)
            play_and_wait(self.cli, "MemoryMatchCozmoWinHand" if cozmo else "MemoryMatchCozmoLoseHand", cancel)
            if player:
                self.best[PLAYER] = length
            if cozmo:
                self.best[COZMO] = length
            if player != cozmo:
                winner = PLAYER if player else COZMO
                play_and_wait(self.cli, "MemoryMatchCozmoWinGame" if winner == COZMO else "MemoryMatchPlayerWinGame",
                              cancel)
                return winner
            if player:
                length += 1
        return None

    def play_solo(self, cancel: Optional[threading.Event] = None) -> int:
        """ Play solo, and say the longest pattern the player repeated. """
        with in_use(*self.cubes):
            try:
                length = START_LENGTH
                while length <= MAX_LENGTH:
                    pattern = self.pattern(length)
                    self.show_pattern(pattern, cancel)
                    if not self.player_repeats(pattern, cancel):
                        play_and_wait(self.cli, "MemoryMatchPlayerLoseHandSolo", cancel)
                        break
                    self.best[PLAYER] = length
                    play_and_wait(self.cli, "MemoryMatchPlayerWinHandSolo", cancel)
                    length += 1
                play_and_wait(self.cli, "MemoryMatchSoloGameOver", cancel)
                return self.best[PLAYER]
            finally:
                self._all_off()

    def pattern(self, length: int) -> List[int]:
        """ A pattern of cubes, by index, no cube twice in a row. """
        pattern: List[int] = []
        while len(pattern) < length:
            choice = self.rng.randrange(len(self.cubes))
            if not pattern or choice != pattern[-1]:
                pattern.append(choice)
        return pattern

    def show_pattern(self, pattern: Sequence[int], cancel: Optional[threading.Event] = None) -> None:
        """ Light the cubes up one after another. """
        self._all_off()
        self._pause(GAP_TIME * 2, cancel)
        for index in pattern:
            self._show(index, COLORS[index])
            self._pause(FLASH_TIME, cancel)
            self._show(index, lights.off)
            self._pause(GAP_TIME, cancel)

    def player_repeats(self, pattern: Sequence[int], cancel: Optional[threading.Event] = None) -> bool:
        """ Wait for the player to tap the pattern, and say whether they got it right. """
        taps: List[int] = []
        tapped = threading.Event()
        # When each cube last spoke, a repeat of what it said included.
        spoke: Dict[int, float] = {}

        def on_tapped(_: Any, cube: LightCube, count: int) -> None:
            if cube in self.cubes:
                index = self.cubes.index(cube)
                now = time.perf_counter()
                before = spoke.get(index)
                spoke[index] = now
                if before is None or now - before >= TAP_DEBOUNCE:
                    taps.append(index)
                    tapped.set()

        handler = self.cli.add_handler(event.EvtCubeTapped, on_tapped)
        try:
            for expected in pattern:
                deadline = time.perf_counter() + INPUT_TIMEOUT
                while not taps:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    if time.perf_counter() > deadline:
                        return False
                    tapped.wait(0.05)
                    tapped.clear()
                index = taps.pop(0)
                if index != expected:
                    self._flash(index, WRONG_COLOR, cancel)
                    return False
                self.cli.play_anim_group("MemoryMatchCozmoFollowTapsSoundOnly")
                # Taps that come while the cube is lit count as they came.
                self._flash(index, COLORS[index], cancel)
            return True
        finally:
            self.cli.del_handler(event.EvtCubeTapped, handler)

    def cozmo_repeats(self, pattern: Sequence[int], cancel: Optional[threading.Event] = None) -> bool:
        """ Have Cozmo point the pattern out, and say whether it got it right. """
        # Whether Cozmo gets it right, and if not, where it goes wrong.
        wrong_at = None
        if self.rng.random() >= self.skill.success_odds(len(pattern)):
            wrong_at = self.rng.randrange(len(pattern))
        fast = len(pattern) >= FAST_LENGTH
        for i, expected in enumerate(pattern):
            index = expected
            if i == wrong_at:
                index = self.rng.choice([other for other in range(len(self.cubes)) if other != expected])
            play_and_wait(self.cli, self.point_trigger(self.cubes[index], fast), cancel)
            if index != expected:
                self._flash(index, WRONG_COLOR, cancel)
                return False
            self._flash(index, COLORS[index], cancel)
        return True

    def point_trigger(self, cube: LightCube, fast: bool = False) -> str:
        """ The animation that points at a cube from where the robot faces. A cube not seen is pointed ahead. """
        side = "Center"
        if cube.pose is not None:
            bearing = _wrap(math.atan2(cube.pose.y - self.cli.pose.position.y, cube.pose.x - self.cli.pose.position.x)
                            - self.cli.pose.rotation.angle_z.radians)
            if abs(bearing) >= CENTER_ANGLE:
                side = ("Left" if bearing > 0.0 else "Right") + \
                    ("Small" if abs(bearing) < SMALL_TURN_ANGLE else "Big")
        return "MemoryMatchPoint" + side + ("Fast" if fast else "")

    def _flash(self, index: int, color: lights.Color, cancel: Optional[threading.Event]) -> None:
        self._show(index, color)
        self._pause(TAP_FLASH_TIME, cancel)
        self._show(index, lights.off)

    def _show(self, index: int, color: lights.Color) -> None:
        state = steady(color)
        self.cli.cubes.show_lights(self.cubes[index], LIGHTS_NAME, (state, ) * 4)

    def _all_off(self) -> None:
        for index in range(len(self.cubes)):
            self._show(index, lights.off)

    @staticmethod
    def _pause(seconds: float, cancel: Optional[threading.Event]) -> None:
        if cancel is None:
            time.sleep(seconds)
        elif cancel.wait(seconds):
            raise Cancelled()


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
