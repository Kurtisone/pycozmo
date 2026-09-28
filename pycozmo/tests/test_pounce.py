"""

Tests for PounceOnMotion: watching the ground, creeping up on what moves, and pouncing on it.

The timers are caught rather than run, and fired by hand, so that a test does not wait for a robot to
get bored.

"""

import math
import time
import unittest
from typing import Any, Callable, Dict, List, Optional, Tuple, cast
from unittest import mock

import pycozmo
from pycozmo import behavior, event, motion_detection, robot

from .test_behavior import BehaviorTestCase, FakeClient
from .test_brain import cozmo_assets_available


class PounceClient(FakeClient):
    """ The fake client, with a head, a lift, the pounce animations, and the last motion seen. """

    POUNCE_GROUPS = ("PounceInitial", "PouncePounce", "PounceSuccess", "PounceFail", "PounceGetOut")

    def __init__(self) -> None:
        super().__init__()
        self.animation_groups.update({name: None for name in self.POUNCE_GROUPS})
        self.head_angles: List[float] = []
        self.lift_position = robot.LiftPosition(height=robot.MIN_LIFT_HEIGHT)
        self.last_ground_motion: Optional[Tuple[float, Tuple[float, float]]] = None

    def set_head_angle(self, angle: float, *args: Any, **kwargs: Any) -> None:
        self.head_angles.append(angle)


CONF = {
    "behaviorClass": "PounceOnMotion",
    "needsActionID": "Pounce",
    "maxNoGroundMotionBeforeBored_running_Sec": 20.0,
    "maxNoGroundMotionBeforeBored_notRunning_Sec": 3.0,
    "backUpDistance": -50.0,
    "timeBeforeRotate_Sec": 6.0,
    "oddsOfPouncingOnTurn": 0.0,
    "searchAmplitudeDeg": 90.0,
}


def seen(x: float, y: float) -> motion_detection.ObservedMotion:
    return motion_detection.ObservedMotion(timestamp=None, width=320, height=240, area=0.02,
                                           centroid=(160.0, 120.0), ground_area=0.05, ground_centroid=(x, y))


class FakeTimer:
    """ A timer that only goes off when a test fires it. """

    def __init__(self, delay: float, f: Callable) -> None:
        self.delay = delay
        self.f = f
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def __getitem__(self, i: int) -> Any:
        return (self.delay, self.f)[i]


class PounceTestCase(BehaviorTestCase):

    cli: PounceClient

    def setUp(self) -> None:
        self.cli = PounceClient()
        self.needs = mock.Mock()
        self.timers: Dict[str, Any] = {}

    def make_pounce(self, **conf: Any) -> behavior.BehaviorPounceOnMotion:
        b = cast(behavior.BehaviorPounceOnMotion, self.make(behavior.BehaviorPounceOnMotion, dict(CONF, **conf)))
        b.needs = self.needs
        self.timers = b.timers

        def after(name: str, delay: float, f: Callable) -> None:
            b._cancel(name)
            b.timers[name] = cast(Any, FakeTimer(delay, f))

        setattr(b, "_after", after)
        return b

    def fire(self, name: str) -> None:
        self.timers.pop(name).f()

    def completed(self) -> None:
        self.complete_animation()

    def watching(self, **conf: Any) -> behavior.BehaviorPounceOnMotion:
        """ A behavior that has got in and is watching the ground. """
        b = self.make_pounce(**conf)
        b.activate()
        self.completed()
        self.assertEqual(b.state, "watching")
        return b

    def motion(self, x: float, y: float) -> None:
        self.cli.dispatch(event.EvtMotionObserved, self.cli, seen(x, y))


class TestWantingToRun(PounceTestCase):

    def test_not_without_motion(self):
        self.assertFalse(self.make_pounce().wants_to_run())

    def test_after_motion_on_the_ground(self):
        self.cli.last_ground_motion = (time.perf_counter(), (100.0, 0.0))
        self.assertTrue(self.make_pounce().wants_to_run())

    def test_not_once_that_motion_is_old(self):
        # Hiking's configuration: never run unless we see motion.
        self.cli.last_ground_motion = (time.perf_counter() - 2.0, (100.0, 0.0))
        self.assertFalse(self.make_pounce(maxNoGroundMotionBeforeBored_notRunning_Sec=1.5).wants_to_run())

    def test_not_without_the_pounce_animation(self):
        del self.cli.animation_groups["PouncePounce"]
        self.cli.last_ground_motion = (time.perf_counter(), (100.0, 0.0))
        self.assertFalse(self.make_pounce().wants_to_run())


class TestWatching(PounceTestCase):

    def test_it_gets_in_then_looks_at_the_ground(self):
        b = self.make_pounce()
        b.activate()
        self.assertEqual(self.cli.played, ["PounceInitial"])
        self.completed()
        self.assertAlmostEqual(self.cli.head_angles[-1], robot.MIN_HEAD_ANGLE.radians)
        self.assertEqual(self.timers["rotate"][0], 6.0)
        self.assertAlmostEqual(self.timers["bored"][0], 20.0, delta=0.5)

    def test_motion_within_reach_is_pounced_on(self):
        self.watching()
        self.motion(100.0, 0.0)
        self.assertEqual(self.cli.played[-1], "PouncePounce")
        self.needs.apply_action.assert_called_once_with("Pounce")
        self.assertIn("MotionReact", self.posted_emotion_events())

    def test_motion_to_one_side_is_turned_towards(self):
        b = self.watching()
        self.motion(100.0, 60.0)
        self.assertEqual(b.state, "turning")
        left, right = self.cli.wheel_speeds[-1]
        self.assertLess(left, 0.0)
        self.assertGreater(right, 0.0)
        self.assertAlmostEqual(self.timers["move"][0], math.atan2(60.0, 100.0) / b.TURN_SPEED)
        self.fire("move")
        self.assertEqual(b.state, "watching")
        self.assertGreater(self.cli.stopped, 0)

    def test_motion_to_the_right_turns_right(self):
        self.watching()
        self.motion(100.0, -60.0)
        left, right = self.cli.wheel_speeds[-1]
        self.assertGreater(left, 0.0)
        self.assertLess(right, 0.0)

    def test_motion_too_far_is_crept_up_on(self):
        b = self.watching()
        self.motion(180.0, 0.0)
        self.assertEqual(b.state, "approaching")
        left, right = self.cli.wheel_speeds[-1]
        self.assertGreater(left, 0.0)
        self.assertEqual(left, right)
        self.assertAlmostEqual(self.timers["move"][0], (180.0 - b.APPROACH_DISTANCE) / b.DRIVE_SPEED)
        self.fire("move")
        self.assertEqual(b.state, "watching")

    def test_creeping_goes_a_limited_way_at_once(self):
        b = self.watching()
        self.motion(390.0, 0.0)
        self.assertAlmostEqual(self.timers["move"][0], b.MAX_APPROACH / b.DRIVE_SPEED)

    def test_motion_while_busy_is_ignored(self):
        b = self.watching()
        self.motion(100.0, 0.0)
        played = list(self.cli.played)
        self.motion(100.0, 0.0)
        self.assertEqual(self.cli.played, played)
        self.assertEqual(b.pounces, 1)


class TestPouncing(PounceTestCase):

    def pounce(self, lift_mm: float) -> behavior.BehaviorPounceOnMotion:
        b = self.watching()
        self.motion(100.0, 0.0)
        self.cli.lift_position = robot.LiftPosition(height=pycozmo.util.Distance(mm=lift_mm))
        self.completed()
        return b

    def test_a_lift_that_stays_up_caught_something(self):
        b = self.pounce(robot.MIN_LIFT_HEIGHT.mm + 10.0)
        self.assertEqual(self.cli.played[-1], "PounceSuccess")
        self.assertEqual(b.catches, 1)

    def test_a_lift_that_reached_the_bottom_missed(self):
        b = self.pounce(robot.MIN_LIFT_HEIGHT.mm)
        self.assertEqual(self.cli.played[-1], "PounceFail")
        self.assertEqual(b.catches, 0)

    def test_it_backs_off_and_watches_again(self):
        b = self.pounce(robot.MIN_LIFT_HEIGHT.mm)
        self.completed()
        self.assertEqual(b.state, "backing_up")
        left, right = self.cli.wheel_speeds[-1]
        self.assertLess(left, 0.0)
        self.assertAlmostEqual(self.timers["move"][0], 50.0 / b.DRIVE_SPEED)
        self.fire("move")
        self.assertEqual(b.state, "watching")


class TestNothingMoving(PounceTestCase):

    def test_it_looks_elsewhere(self):
        b = self.watching()
        self.fire("rotate")
        self.assertEqual(b.state, "turning")
        angle = self.timers["move"][0] * b.TURN_SPEED
        self.assertLessEqual(angle, math.radians(90.0) + 1e-9)
        self.fire("move")
        self.assertEqual(b.state, "watching")

    def test_it_sometimes_pounces_after_a_turn(self):
        self.watching(oddsOfPouncingOnTurn=1.0)
        self.fire("rotate")
        self.fire("move")
        self.assertEqual(self.cli.played[-1], "PouncePounce")

    def test_it_gets_bored(self):
        b = self.watching()
        self.fire("bored")
        self.assertEqual(self.cli.played[-1], "PounceGetOut")
        self.assertNotDone()
        self.completed()
        self.assertDone()
        self.assertEqual(b.state, "idle")

    def test_without_its_get_out_it_is_done_at_once(self):
        self.watching(skipGetOutAnim=True)
        self.fire("bored")
        self.assertNotIn("PounceGetOut", self.cli.played)
        self.assertDone()

    def test_a_time_limit_ends_it(self):
        self.make_pounce(maxTimeBehaviorTimeout_Sec=60.0).activate()
        self.assertEqual(self.timers["max_time"][0], 60.0)
        self.fire("max_time")
        self.assertEqual(self.cli.played[-1], "PounceGetOut")


class TestTakenOff(PounceTestCase):

    def test_deactivating_stops_everything(self):
        b = self.watching()
        self.motion(100.0, 60.0)
        stale = self.timers["move"][1]
        b.deactivated = True
        b.deactivate()
        self.assertEqual(self.timers, {})
        self.assertGreater(self.cli.cancelled, 0)
        stopped = self.cli.stopped
        stale()
        self.assertEqual(self.cli.stopped, stopped, "a stale timer does nothing")
        self.assertNotDone()


class TestSocialize(unittest.TestCase):
    """ Socialize offers the behaviors behind its objectives itself: they score nothing in its chooser. """

    def socialize(self) -> pycozmo.activity.SocializeActivity:
        objective = pycozmo.activity.Objective.from_json({
            "objective": "PouncedAndCaught", "behaviorID": "PounceOnMotion_Socialize",
            "ignoreIfLocked": "PounceOnMotionAction", "probabilityToRequireObjective": 0.5,
            "randomCompletionsNeededMin": 1, "randomCompletionsNeededMax": 3})
        activity = pycozmo.activity.SocializeActivity.__new__(pycozmo.activity.SocializeActivity)
        activity.required_objectives = [objective]
        activity.interlude_chooser = None
        activity.behavior_chooser = None
        return activity

    def test_the_pounce_is_offered_when_it_can_run(self):
        self.assertEqual(self.socialize().choose(lambda b: True), "PounceOnMotion_Socialize")

    def test_otherwise_the_chooser_decides(self):
        self.assertIsNone(self.socialize().choose(lambda b: False))


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAgainstCozmoAssets(unittest.TestCase):

    def test_the_four_pounce_behaviors_are_this_class(self):
        behaviors = behavior.load_behaviors(str(pycozmo.util.get_cozmo_asset_dir()),
                                            cast(pycozmo.client.Client, PounceClient()))
        pounces = {name: b for name, b in behaviors.items() if isinstance(b, behavior.BehaviorPounceOnMotion)}
        self.assertEqual(set(pounces), {"PounceOnMotion_Socialize", "Hiking_PounceOnMotion",
                                        "SparksPounceOnMotion", "VC_PounceOnMotion"})
        self.assertEqual(pounces["Hiking_PounceOnMotion"].bored_not_running, 1.5)
        self.assertEqual(pounces["SparksPounceOnMotion"].max_time, 60.0)
        self.assertTrue(pounces["VC_PounceOnMotion"].skip_get_out)
