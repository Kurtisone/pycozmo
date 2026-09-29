"""

Behaviors that handle the Light Cubes.

Their configurations in the resources hardly say more than their class and the need they answer; what they did
was Anki's engine's code. The sequences here are PyCozmo's, over pycozmo.cube_handling and Anki's animation
triggers for them.

Each runs a script of blocking steps - finding a cube, docking, lifting, animations - on a thread of its own: the
brain activates behaviors with its behavior lock held, and a script takes seconds. Deactivating one cancels its
script between steps and stops the motors.

"""

import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import activity
from . import behavior
from . import cube_handling
from . import event
from . import util
from .cube_handling import Cancelled
from .cubes import CUBE_SIDE, LightCube
from .json_loader import load_json_file
from .logger import logger
from .needs import BRACKETS
from .protocol_encoder import UpAxis


__all__ = [
    "BehaviorScript",
    "BehaviorPutDownBlock",
    "BehaviorPickUpCube",
    "BehaviorPickUpAndPutDownCube",
    "BehaviorCubeLiftWorkout",
    "BehaviorStackBlocks",
    "Workout",

    "usable_cubes",
    "load_workouts",
]


def usable_cubes(cli: Any) -> List[LightCube]:
    """
    The cubes a behavior can go for: connected, seen since they last moved, standing on the ground the right
    way up, and not in the lift. Nearest first.
    """
    x, y = cli.pose.position.x, cli.pose.position.y
    cubes = [cube for cube in cli.cubes
             if cube.connected and cube.pose is not None and cube is not cli.cubes.carried
             and cube.up_axis in (None, UpAxis.ZPositive) and abs(cube.pose.z - CUBE_SIDE / 2) < CUBE_SIDE / 4]
    return sorted(cubes, key=lambda cube: (cube.pose.x - x) ** 2 + (cube.pose.y - y) ** 2)


class BehaviorScript(behavior.Behavior):
    """ A behavior that runs a script on a thread of its own. See the module's description. """

    #: The longest an animation is waited for, in seconds.
    ANIMATION_TIMEOUT = 15.0

    def __init__(self, cli: Any, conf: Any, robot_needs: Any = None) -> None:
        super().__init__(cli, conf, robot_needs)
        self.cancel = threading.Event()
        self.thread: Optional[threading.Thread] = None
        # The mood, for the scripts that ask about it. The brain hands its own over; a behavior driven
        # without one finds every emotion at 0.
        self.get_mood: Callable[[], Dict[str, float]] = dict

    def activate(self) -> None:
        self.cancel = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(self.cancel, ), daemon=True, name=self.get_id())
        self.thread.start()

    def _run(self, cancel: threading.Event) -> None:
        try:
            self.script(cancel)
        except Cancelled:
            return
        except Exception as e:
            logger.error("Behavior '{}' failed: {}".format(self.get_id(), e))
        if not cancel.is_set():
            self.done()

    def script(self, cancel: threading.Event) -> None:
        """ What the behavior does, step by step, checking cancel between them. """
        raise NotImplementedError

    def deactivate(self) -> None:
        self.cancel.set()
        self.cli.stop_all_motors()
        self.cli.cancel_anim()

    def play(self, trigger: str, cancel: threading.Event) -> bool:
        """ Play an animation trigger and wait for it to end; say whether it did. """
        if trigger not in (self.cli.animation_groups or {}):
            logger.warning("Behavior '{}' has no animation for {}.".format(self.get_id(), trigger))
            return False
        done = threading.Event()
        handler = self.cli.add_handler(event.EvtAnimationCompleted, lambda cli: done.set(), one_shot=True)
        try:
            self.cli.play_anim_group(trigger)
            deadline = time.perf_counter() + self.ANIMATION_TIMEOUT
            while not done.wait(0.05):
                if cancel.is_set():
                    raise Cancelled()
                if time.perf_counter() > deadline:
                    return False
            return True
        finally:
            self.cli.del_handler(event.EvtAnimationCompleted, handler)

    def need_action(self) -> None:
        """ Credit the need action the configuration names, once the behavior has done its part. """
        action = self.conf.get("needsActionID")
        if action and self.needs is not None:
            self.needs.apply_action(action)

    def pick_up(self, cube: LightCube, cancel: threading.Event) -> bool:
        """ Pick a cube up, and react to a miss the way Anki's engine did. """
        if cube_handling.pick_up_cube(self.cli, cube, cancel=cancel):
            return True
        self.play("ReactToBlockRetryPickup", cancel)
        return False


class BehaviorPutDownBlock(BehaviorScript):
    """ PutDownBlock - set down the cube in the lift. """

    def wants_to_run(self) -> bool:
        return self.cli.cubes.carried is not None

    def script(self, cancel: threading.Event) -> None:
        cube_handling.put_down_cube(self.cli, cancel=cancel)


class BehaviorPickUpCube(BehaviorScript):
    """ PickUpCube - pick the nearest cube up, and keep it. """

    def wants_to_run(self) -> bool:
        return self.cli.cubes.carried is None and bool(usable_cubes(self.cli))

    def script(self, cancel: threading.Event) -> None:
        cubes = usable_cubes(self.cli)
        if cubes and self.pick_up(cubes[0], cancel):
            self.play("ReactToBlockPickupSuccess", cancel)
            self.need_action()


class BehaviorPickUpAndPutDownCube(BehaviorPickUpCube):
    """ PickUpAndPutDownCube - pick the nearest cube up, and set it down again. """

    def script(self, cancel: threading.Event) -> None:
        cubes = usable_cubes(self.cli)
        if cubes and self.pick_up(cubes[0], cancel):
            self.play("ReactToBlockPickupSuccess", cancel)
            cube_handling.put_down_cube(self.cli, cancel=cancel)
            self.need_action()


class Workout:
    """
    One of the workouts of Anki's workout_config.json: its animations, and how many strong and weak lifts the
    mood asks for.
    """

    def __init__(self, data: Dict) -> None:
        self.pre_lift = str(data["preLiftAnim"])
        self.strong_lift = str(data["strongLiftAnim"])
        self.transition = str(data["transitionAnim"])
        self.weak_lift = str(data["weakLiftAnim"])
        self.post_lift = str(data["postLiftAnim"])
        self.put_down = str(data["putDownAnim"])
        self.strong_lifts = [activity.MoodScorer.from_json(scorer) for scorer in data.get("numStrongLifts", [])]
        self.weak_lifts = [activity.MoodScorer.from_json(scorer) for scorer in data.get("numWeakLifts", [])]
        self.emotion_event: Optional[str] = data.get("emotionEventOnComplete")

    @staticmethod
    def _count(scorers: List[activity.MoodScorer], mood: Dict[str, float]) -> int:
        return max(0, round(sum(scorer.score(mood) for scorer in scorers)))

    def lifts(self, mood: Dict[str, float]) -> List[str]:
        """ The lift animations to play, in order, for a mood. """
        strong = self._count(self.strong_lifts, mood)
        weak = self._count(self.weak_lifts, mood)
        return [self.strong_lift] * strong + ([self.transition] if strong and weak else []) + [self.weak_lift] * weak


def load_workouts(resource_dir: str) -> List[Workout]:
    """ The workouts of Anki's resources, strongest first. """
    filename = os.path.join(resource_dir, "cozmo_resources", "config", "engine", "behaviorSystem",
                            "workout_config.json")
    return [Workout(data) for data in load_json_file(filename)["workouts"]]


class BehaviorCubeLiftWorkout(BehaviorScript):
    """
    CubeLiftWorkout - pick a cube up, lift it a few times, and set it down.

    Anki's resources give four workouts, from strong to weak with a simpler put-down, and the confidence of the
    mood sets how many lifts each makes. Which one the engine chose is not in them; here the robot's energy
    does, one workout per bracket of the Energy need. Each ends with an emotion event: a strong workout makes
    the robot more confident, a weak one less.
    """

    def __init__(self, cli: Any, conf: Any, robot_needs: Any = None) -> None:
        super().__init__(cli, conf, robot_needs)
        self._workouts: Optional[List[Workout]] = None

    @property
    def workouts(self) -> List[Workout]:
        if self._workouts is None:
            self._workouts = load_workouts(str(util.get_cozmo_asset_dir()))
        return self._workouts

    def wants_to_run(self) -> bool:
        return self.cli.cubes.carried is None and bool(usable_cubes(self.cli))

    def workout(self) -> Workout:
        """ The workout the robot's energy calls for. """
        bracket = self.needs.bracket("Energy") if self.needs is not None else BRACKETS[0]
        return self.workouts[min(BRACKETS.index(bracket), len(self.workouts) - 1)]

    def script(self, cancel: threading.Event) -> None:
        cubes = usable_cubes(self.cli)
        if not cubes or not self.pick_up(cubes[0], cancel):
            return
        workout = self.workout()
        for trigger in [workout.pre_lift] + workout.lifts(self.get_mood()) + [workout.post_lift]:
            self.play(trigger, cancel)
        # The put-down animation sets the cube down, then backs off and turns away from it.
        if not cube_handling.put_down_by(self.cli, lambda: self.play(workout.put_down, cancel)):
            cube_handling.put_down_cube(self.cli, cancel=cancel)
        if workout.emotion_event:
            self.post_emotion_event(workout.emotion_event)
        self.need_action()


class BehaviorStackBlocks(BehaviorScript):
    """ StackBlocks - pick the nearest cube up and set it on top of another. """

    def wants_to_run(self) -> bool:
        return self.cli.cubes.carried is None and len(usable_cubes(self.cli)) >= 2

    def script(self, cancel: threading.Event) -> None:
        cubes = usable_cubes(self.cli)
        if len(cubes) < 2:
            return
        top, bottom = cubes[0], cubes[1]
        if not self.pick_up(top, cancel):
            return
        if cube_handling.place_on_cube(self.cli, bottom, cancel=cancel):
            self.play("StackBlocksSuccess", cancel)
            self.need_action()
        else:
            cube_handling.put_down_cube(self.cli, cancel=cancel)
