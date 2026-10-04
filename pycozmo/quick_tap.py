"""

Quick Tap, a game of Anki's app: Cozmo and a player each have a cube, and tap it when the two light up the same
colour.

The app played it, not the engine, and its rules are not in the resources: these are PyCozmo's reading of the game
as the app presented it. Both cubes go dark, then light up. The same colour on both, and the first to tap their cube
wins the point; different colours, and whoever taps loses it. Five points win a round, two rounds the game.

Cozmo sits in front of its cube, the lift raised over it, and taps it by bringing the lift down on it, with Anki's
animations for the game - which the app played by their group's name, not by trigger. Those animations turn the robot
a few degrees at a time, which moves the fork off the cube: before each hand, the robot turns back to where it was
when the game started, and drives back if need be. The taps are timed when the
cubes report them, Cozmo's as the player's: both come the same way, by radio through the robot, so whichever comes
first came first. A tap of Cozmo's that its cube does not report counts when its animation ends.

Everything here blocks until done, so it must not run on the thread that dispatches the client's events.

"""

import math
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import anim
from . import cube_handling
from . import event
from . import lights
from . import robot
from . import util
from .cube_behaviors import play_and_wait
from .cube_handling import Cancelled
from .cube_lights import CubeLightPattern, steady
from .cubes import LightCube, in_use
from .json_loader import get_json_files, load_json_file


__all__ = [
    "COZMO",
    "PLAYER",
    "POINTS_TO_WIN_ROUND",
    "ROUNDS_TO_WIN_GAME",
    "COLORS",

    "Skill",
    "QuickTap",

    "load_resources",
    "take_position",
    "leave_position",
]


#: Who wins.
COZMO = "cozmo"
PLAYER = "player"

#: Points to win a round, and rounds to win the game.
POINTS_TO_WIN_ROUND = 5
ROUNDS_TO_WIN_GAME = 2
#: How long the cubes stay dark before lighting up, in seconds: a time drawn in this range.
DARK_TIME = (1.0, 3.0)
#: How often the two cubes light up the same colour.
MATCH_ODDS = 0.5
#: How long the players have to tap once the cubes light up, in seconds. A hand no one taps is played again.
TAP_WINDOW = 2.0
#: The colours the cubes light up in. White and red are those of the lights that say who won a hand.
COLORS = (
    lights.Color(name="green", rgb=(0, 255, 0)),
    lights.Color(name="blue", rgb=(0, 0, 255)),
    lights.Color(name="yellow", rgb=(255, 200, 0)),
    lights.Color(name="purple", rgb=(160, 0, 255)),
)
#: Where Cozmo stands to tap its cube, the cube's centre that far ahead, in mm. At 50 the fork came down to 65 mm and
#: the cube said so two times out of three, but the fork was short: it reaches 30 mm ahead of the robot, and the
#: cube's near face was at 27.5, so it hardly covered the cube's top, and the user saw it not touch the cube. At 42 it
#: comes down on the cube, and the user saw it touch it.
TAP_DISTANCE = 42.0
#: The longest Cozmo's tap or fake is waited for, in seconds.
ANIMATION_TIMEOUT = 2.0
#: How long a robot takes to start playing an animation it has been sent, in seconds: on a robot, the tap's lift met
#: the cube 0.50 to 0.57 s after the animation was asked for, 0.40 s over the 0.13 s the clip takes, and no different
#: after the waiting animation than at rest. The frames the controller keeps ahead of the robot, 0.33 s of them, are
#: most of it. The tap is asked for that long before Cozmo is to hit the cube.
ANIMATION_DELAY = 0.4
#: How far off its place the robot may have drifted before it goes back, in radians and in mm.
DRIFT_ANGLE = math.radians(1.5)
DRIFT_DISTANCE = 4.0
#: The furthest the robot goes back, in mm: the animations move it some 10 mm. More than this, the place it holds is
#: not where the robot was - on a robot that had been lifted its position began again at zero, in a new frame - and it
#: stays where it is rather than drive at the cube.
MAX_DRIFT_DISTANCE = 60.0
#: The name the game's lights are shown under: see Cubes.show_lights().
LIGHTS_NAME = "QuickTap"

#: Anki's animation groups for the game, by name.
TAP = "ag_speedtap_tap"
FAKE = "ag_speedtap_fake"
WAIT = "ag_speedtap_wait"
WIN_HAND = "ag_speedtap_winhand"
LOSE_HAND = "ag_speedtap_losehand"
GET_OUT = "ag_speedtap_getout"
#: The light animations that say who won a hand.
WIN_LIGHTS = "speedTapWin"
LOSE_LIGHTS = "speedTapLose"


def load_resources(resource_dir: str) -> Tuple[Dict[str, anim.AnimationGroup], Dict[str, List[CubeLightPattern]]]:
    """ The game's animation groups, by name, and its light animations. """
    base = os.path.join(resource_dir, "cozmo_resources")
    groups_dir = os.path.join(base, "assets", "animationGroups", "SpeedTap")
    groups = {os.path.splitext(filename)[0]: anim.AnimationGroup.from_json(
        load_json_file(os.path.join(groups_dir, filename))) for filename in sorted(os.listdir(groups_dir))}
    light_animations = {}
    for filename in get_json_files(base, [os.path.join("config", "engine", "lights", "cubeLights")]):
        for name, steps in load_json_file(filename).items():
            if name in (WIN_LIGHTS, LOSE_LIGHTS):
                light_animations[name] = [CubeLightPattern.from_json(step) for step in steps]
    return groups, light_animations


def take_position(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """ Go and sit in front of a cube, the lift raised over it, ready to tap it. Say whether it got there. """
    cli.set_lift_height(robot.MAX_LIFT_HEIGHT.mm)
    return cube_handling.go_to_cube(cli, cube, cancel=cancel) and \
        cube_handling.dock_with_cube(cli, cube, cancel=cancel, distance=TAP_DISTANCE)


def leave_position(cli: Any) -> bool:
    """ Back away from the cube, clear of it, and bring the lift down. """
    backed = bool(cli.drive_straight(util.Distance(mm=-cube_handling.PREDOCK_GAP)))
    play_and_wait(cli, GET_OUT)
    return backed


class Skill:
    """ How good Cozmo is at the game. """

    def __init__(self, reaction: Tuple[float, float] = (0.3, 0.8), fake_odds: float = 0.3,
                 mistake_odds: float = 0.05) -> None:
        #: How long Cozmo takes to tap once the colours match, in seconds: a time drawn in this range, which the
        #: robot cannot make shorter than ANIMATION_DELAY, its time to start the animation. The tap itself, from
        #: the start of the animation to the lift meeting the cube, takes 0.13 s more.
        self.reaction = reaction
        #: How often Cozmo pretends to tap when the colours differ, to fool the player.
        self.fake_odds = fake_odds
        #: How often Cozmo taps when the colours differ.
        self.mistake_odds = mistake_odds


class QuickTap:
    """ A game of Quick Tap. Cozmo has to be in position at its cube: see take_position(). """

    def __init__(self, cli: Any, cozmo_cube: LightCube, player_cube: LightCube, skill: Optional[Skill] = None,
                 rng: Optional[random.Random] = None) -> None:
        self.cli = cli
        self.cozmo_cube = cozmo_cube
        self.player_cube = player_cube
        self.skill = skill or Skill()
        self.rng = rng or random.Random()
        #: The score of the round being played, and the rounds won.
        self.points = {COZMO: 0, PLAYER: 0}
        self.rounds = {COZMO: 0, PLAYER: 0}
        # The taps reported since the cubes last lit up: when, and on which cube.
        self._taps: List[Tuple[float, LightCube]] = []
        self._lock = threading.Lock()
        #: Where the robot stands to tap its cube: where it was when the game started.
        self.place: Optional[util.Pose] = None
        # Whether Cozmo's cube has said it moved since the robot last took its place: its tap can push it.
        self._pushed = False

    def play(self, cancel: Optional[threading.Event] = None) -> str:
        """ Play a game, and say who won it: COZMO or PLAYER. """
        self._load_resources()
        try:
            with in_use(self.cozmo_cube, self.player_cube):
                return self._play(cancel)
        finally:
            self._show(self.cozmo_cube, lights.off)
            self._show(self.player_cube, lights.off)

    def _play(self, cancel: Optional[threading.Event]) -> str:
        while max(self.rounds.values()) < ROUNDS_TO_WIN_GAME:
            self.points = {COZMO: 0, PLAYER: 0}
            while max(self.points.values()) < POINTS_TO_WIN_ROUND:
                winner = self.play_hand(cancel)
                if winner is not None:
                    self.points[winner] += 1
                    self._hand_over(winner, cancel)
            winner = COZMO if self.points[COZMO] > self.points[PLAYER] else PLAYER
            self.rounds[winner] += 1
            # A round won by three points or more is a big win.
            self._play_for(winner, "round", "02" if abs(self.points[COZMO] - self.points[PLAYER]) >= 3 else "01",
                           cancel)
        winner = COZMO if self.rounds[COZMO] > self.rounds[PLAYER] else PLAYER
        # A game won without losing a round is a bigger win.
        self._play_for(winner, "game", "03" if min(self.rounds.values()) == 0 else "02", cancel)
        return winner

    def play_hand(self, cancel: Optional[threading.Event] = None) -> Optional[str]:
        """ Play one hand, and say who won it: COZMO, PLAYER, or None when no one tapped. """
        self._load_resources()
        moves = self.cli.add_handler(event.EvtCubeMovingChange, self._on_moving)
        try:
            with in_use(self.cozmo_cube, self.player_cube):
                return self._play_hand(cancel)
        finally:
            self.cli.del_handler(event.EvtCubeMovingChange, moves)

    def _on_moving(self, _: Any, cube: LightCube, moving: bool) -> None:
        if moving and cube is self.cozmo_cube:
            self._pushed = True

    def _play_hand(self, cancel: Optional[threading.Event]) -> Optional[str]:
        self._go_back(cancel)
        self._show(self.cozmo_cube, lights.off)
        self._show(self.player_cube, lights.off)
        self.cli.play_anim_group(WAIT)
        self._pause(self.rng.uniform(*DARK_TIME), cancel)

        match = self.rng.random() < MATCH_ODDS
        cozmo_color, player_color = self._colors(match)
        # What Cozmo does, and when.
        action: Optional[str] = None
        if match or self.rng.random() < self.skill.mistake_odds:
            action = TAP
        elif self.rng.random() < self.skill.fake_odds:
            action = FAKE
        with self._lock:
            self._taps.clear()
        start = time.perf_counter()
        action_time = start + max(0.0, self.rng.uniform(*self.skill.reaction) - ANIMATION_DELAY)
        self._show(self.cozmo_cube, cozmo_color)
        self._show(self.player_cube, player_color)

        animated = threading.Event()
        animation_end: List[float] = []

        def on_completed(_: Any) -> None:
            animation_end.append(time.perf_counter())
            animated.set()

        handler = None
        first: Optional[Tuple[float, str]] = None
        tap_handler = self.cli.add_handler(event.EvtCubeTapped, self._on_tapped)
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                now = time.perf_counter()
                first = self._first_tap()
                if first is None and action == TAP and animated.is_set():
                    # The lift came down, but the cube did not say so.
                    first = (animation_end[0], COZMO)
                if first is not None:
                    break
                if action is not None and handler is None and now >= action_time:
                    handler = self.cli.add_handler(event.EvtAnimationCompleted, on_completed, one_shot=True)
                    self.cli.play_anim_group(action)
                # Cozmo's tap has its time to play out, but the hand does not wait for an animation that never
                # says it ended.
                if now > start + TAP_WINDOW and (handler is None or animated.is_set() or
                                                 now > start + TAP_WINDOW + ANIMATION_TIMEOUT):
                    break
                time.sleep(0.01)
            # Cozmo's animation plays out before whatever comes next.
            if handler is not None:
                animated.wait(ANIMATION_TIMEOUT)
        finally:
            self.cli.del_handler(event.EvtCubeTapped, tap_handler)
            if handler is not None:
                self.cli.del_handler(event.EvtAnimationCompleted, handler)
        if first is None:
            return None
        tapper = first[1]
        return tapper if match else self._other(tapper)

    def _go_back(self, cancel: Optional[threading.Event]) -> None:
        """ Go back to where the robot stood when the game started, if its animations have moved it off. """
        if self._pushed and self.place is not None:
            # Cozmo's tap pushed its cube, and the next would fall short of it: back off, look at the cube, and take
            # the place again. Without the cube found, the robot stays where it is.
            self._pushed = False
            self.cli.drive_straight(util.Distance(mm=-cube_handling.PREDOCK_GAP), speed=robot.DOCK_SPEED)
            if cube_handling.find_cube(self.cli, self.cozmo_cube, cancel=cancel):
                # The cube's marker is not always seen at 15 cm, in a dim room: once more.
                for _ in range(2):
                    if take_position(self.cli, self.cozmo_cube, cancel):
                        break
            self.place = self.cli.pose
            return
        pose = self.cli.pose
        if self.place is None or self.place.origin_id != pose.origin_id:
            # The first hand, or the robot's position has begun again in a new frame: this is the place now.
            self.place = pose
            return
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        heading = self.place.rotation.angle_z.radians
        # How far ahead of its place the robot stands, along its heading.
        ahead = ((pose.position.x - self.place.position.x) * math.cos(heading) +
                 (pose.position.y - self.place.position.y) * math.sin(heading))
        if math.hypot(pose.position.x - self.place.position.x, pose.position.y - self.place.position.y) > \
                MAX_DRIFT_DISTANCE:
            self.place = pose
            return
        error = _wrap(heading - pose.rotation.angle_z.radians)
        if abs(error) > DRIFT_ANGLE:
            self.cli.turn_in_place(util.Angle(radians=error))
        if abs(ahead) > DRIFT_DISTANCE:
            self.cli.drive_straight(util.Distance(mm=-ahead), speed=robot.DOCK_SPEED)

    def _hand_over(self, winner: str, cancel: Optional[threading.Event]) -> None:
        winner_cube, loser_cube = self._cubes(winner)
        self.cli.cubes.play_lights(winner_cube, WIN_LIGHTS)
        self.cli.cubes.play_lights(loser_cube, LOSE_LIGHTS)
        play_and_wait(self.cli, WIN_HAND if winner == COZMO else LOSE_HAND, cancel)

    def _play_for(self, winner: str, what: str, intensity: str, cancel: Optional[threading.Event]) -> None:
        """ Cozmo's reaction to a round or a game won or lost. """
        play_and_wait(self.cli, "ag_speedtap_{}{}_intensity{}".format(
            "win" if winner == COZMO else "lose", what, intensity), cancel)

    def _colors(self, match: bool) -> Tuple[lights.Color, lights.Color]:
        if match:
            color = self.rng.choice(COLORS)
            return color, color
        cozmo_color, player_color = self.rng.sample(COLORS, 2)
        return cozmo_color, player_color

    def _cubes(self, winner: str) -> Tuple[LightCube, LightCube]:
        """ The winner's cube, and the loser's. """
        if winner == COZMO:
            return self.cozmo_cube, self.player_cube
        return self.player_cube, self.cozmo_cube

    @staticmethod
    def _other(who: str) -> str:
        return PLAYER if who == COZMO else COZMO

    def _first_tap(self) -> Optional[Tuple[float, str]]:
        with self._lock:
            if not self._taps:
                return None
            when, cube = min(self._taps, key=lambda tap: tap[0])
        return when, COZMO if cube is self.cozmo_cube else PLAYER

    def _on_tapped(self, _: Any, cube: LightCube, taps: int) -> None:
        if cube is self.cozmo_cube or cube is self.player_cube:
            with self._lock:
                self._taps.append((time.perf_counter(), cube))

    def _show(self, cube: LightCube, color: lights.Color) -> None:
        state = steady(color)
        self.cli.cubes.show_lights(cube, LIGHTS_NAME, (state, ) * 4)

    def _pause(self, seconds: float, cancel: Optional[threading.Event]) -> None:
        """ Wait, moving the light animations on - the brain's heartbeat does, but the game may run without it. """
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            if cancel is not None and cancel.wait(0.05):
                raise Cancelled()
            if cancel is None:
                time.sleep(0.05)
            self.cli.cubes.update()

    def _load_resources(self) -> None:
        if WIN_LIGHTS in self.cli.cubes.light_animations and TAP in self.cli.animation_groups:
            return
        groups, light_animations = load_resources(str(util.get_cozmo_asset_dir()))
        self.cli.animation_groups.update(groups)
        self.cli.cubes.light_animations.update(light_animations)


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
