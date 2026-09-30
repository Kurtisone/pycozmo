"""

Tests for the behaviors that handle the cubes. The cube handling steps are stood in for: what is tested here is the
scripts - their order, their animations, their ending and their cancelling.

"""

import threading
import unittest
from typing import Any, Dict, List
from unittest import mock

import pycozmo
from pycozmo import behavior, cube_behaviors, event
from pycozmo.cube_handling import Cancelled
from pycozmo.protocol_encoder import ObjectType, UpAxis

from .test_behavior import FakeClient
from .test_brain import cozmo_assets_available

CUBE1 = ObjectType.Block_LIGHTCUBE1
CUBE2 = ObjectType.Block_LIGHTCUBE2
CUBE3 = ObjectType.Block_LIGHTCUBE3

#: The strongest of Anki's workouts, as workout_config.json gives it.
STRONG_WORKOUT: Dict[str, Any] = {
    "preLiftAnim": "WorkoutPreLift_highEnergy",
    "postLiftAnim": "WorkoutPostLift_highEnergy",
    "strongLiftAnim": "WorkoutStrongLift_highEnergy",
    "transitionAnim": "WorkoutTransition_highEnergy",
    "weakLiftAnim": "WorkoutWeakLift_highEnergy",
    "putDownAnim": "WorkoutPutDown_highEnergy",
    "numStrongLifts": [{"emotionType": "Confident", "trackDelta": False,
                        "scoreGraph": {"nodes": [{"x": -1.0, "y": 2}, {"x": 0, "y": 5}, {"x": 0.3, "y": 7}]}}],
    "numWeakLifts": [{"emotionType": "Confident", "trackDelta": False,
                      "scoreGraph": {"nodes": [{"x": -1.0, "y": 0}, {"x": -0.2, "y": 1}, {"x": 0.3, "y": 2}]}}],
    "emotionEventOnComplete": "StrongWorkoutCompleted",
}


def workout(energy: str, event_name: str) -> cube_behaviors.Workout:
    data = {key: value.replace("highEnergy", energy) if key.endswith("Anim") else value
            for key, value in STRONG_WORKOUT.items()}
    data["emotionEventOnComplete"] = event_name
    return cube_behaviors.Workout(data)


class TestUsableCubes(unittest.TestCase):

    def setUp(self):
        self.cli = pycozmo.client.Client()
        for cube in self.cli.cubes:
            cube.connected = True

    def test_nearest_first(self):
        self.cli.cubes.place(self.cli.cubes[CUBE1], 400.0, 0.0, 0.0)
        self.cli.cubes.place(self.cli.cubes[CUBE2], 0.0, 200.0, 0.0)
        self.assertEqual([cube.object_type for cube in cube_behaviors.usable_cubes(self.cli)], [CUBE2, CUBE1])

    def test_only_those_known_on_the_ground_the_right_way_up(self):
        cubes = self.cli.cubes
        cubes.place(cubes[CUBE1], 200.0, 0.0, 0.0)
        # One on top of another, elsewhere.
        cubes.place(cubes[CUBE2], 400.0, 0.0, 0.0, z=22.5 + 45.0)
        cubes.place(cubes[CUBE3], 300.0, 0.0, 0.0)
        cubes[CUBE3].up_axis = UpAxis.XNegative
        self.assertEqual(cube_behaviors.usable_cubes(self.cli), [cubes[CUBE1]])
        cubes.carried = cubes[CUBE1]
        self.assertEqual(cube_behaviors.usable_cubes(self.cli), [])

    def test_nor_one_under_another(self):
        cubes = self.cli.cubes
        cubes.place(cubes[CUBE1], 200.0, 0.0, 0.0)
        cubes.place(cubes[CUBE2], 205.0, 3.0, 0.0, z=22.5 + 45.0)
        cubes.place(cubes[CUBE3], 300.0, 0.0, 0.0)
        self.assertEqual(cube_behaviors.usable_cubes(self.cli), [cubes[CUBE3]])

    def test_not_the_disconnected(self):
        self.cli.cubes[CUBE1].connected = False
        self.cli.cubes.place(self.cli.cubes[CUBE1], 200.0, 0.0, 0.0)
        self.assertEqual(cube_behaviors.usable_cubes(self.cli), [])


class TestWorkout(unittest.TestCase):

    def test_the_mood_sets_the_lifts(self):
        strong = cube_behaviors.Workout(STRONG_WORKOUT)
        # Neither confident nor not: five strong lifts, and one weak after the transition.
        self.assertEqual(strong.lifts({"Confident": 0.0}),
                         ["WorkoutStrongLift_highEnergy"] * 5 + ["WorkoutTransition_highEnergy",
                                                                 "WorkoutWeakLift_highEnergy"])
        # Not confident at all: two strong lifts, no weak one and so no transition.
        self.assertEqual(strong.lifts({"Confident": -1.0}), ["WorkoutStrongLift_highEnergy"] * 2)
        # A mood without the emotion reads it at 0.
        self.assertEqual(len(strong.lifts({})), 7)

    @unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
    def test_anki_s_four(self):
        workouts = cube_behaviors.load_workouts(str(pycozmo.util.get_cozmo_asset_dir()))
        self.assertEqual([w.emotion_event for w in workouts], ["StrongWorkoutCompleted", "MediumWorkoutCompleted",
                                                               "WeakWorkoutCompleted", "WeakWorkoutCompleted"])
        self.assertEqual(workouts[3].put_down, "WorkoutPutDown_lowEnergy_simple")


class ScriptClient(FakeClient):
    """ The fake client, whose animations end as soon as they start. """

    def __init__(self) -> None:
        super().__init__()
        self.cubes: Any = mock.Mock(carried=None)

    def play_anim_group(self, name):
        super().play_anim_group(name)
        self.dispatch(event.EvtAnimationCompleted, self)


class FakeNeeds:

    def __init__(self, energy: str = "Full") -> None:
        self.energy = energy
        self.actions: List[str] = []

    def bracket(self, name: str) -> str:
        return self.energy if name == "Energy" else "Full"

    def apply_action(self, action_id: str) -> bool:
        self.actions.append(action_id)
        return True


class ScriptTestCase(unittest.TestCase):

    #: The cubes the scripts find: two, the nearest first.
    CUBES = [mock.sentinel.near, mock.sentinel.far]

    def setUp(self):
        self.cli = ScriptClient()
        self.needs = FakeNeeds()
        self.steps: List[Any] = []
        self.pick_up_cube = self.patch("pick_up_cube")
        self.put_down_cube = self.patch("put_down_cube")
        self.place_on_cube = self.patch("place_on_cube")
        patcher = mock.patch.object(cube_behaviors, "usable_cubes", return_value=self.CUBES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch(self, name: str) -> Any:
        """ Stand in for a cube handling step, which succeeds. """
        patcher = mock.patch.object(cube_behaviors.cube_handling, name, side_effect=self.step(name, True))
        self.addCleanup(patcher.stop)
        return patcher.start()

    def step(self, name: str, result: bool) -> Any:
        def run(cli: Any, *args: Any, **kwargs: Any) -> bool:
            self.steps.append((name, ) + args)
            return result
        return run

    def make(self, behavior_class: Any, **conf: Any) -> Any:
        conf.setdefault("behaviorID", "TestBehavior")
        script = behavior_class(self.cli, conf, self.needs)
        self.cli.add_child_dispatcher(script)
        self.addCleanup(self.cli.del_child_dispatcher, script)
        self.cli.animation_groups.update({name: None for name in (
            "ReactToBlockRetryPickup", "ReactToBlockPickupSuccess", "StackBlocksSuccess")})
        return script

    def run_script(self, script: Any) -> None:
        script.activate()
        script.thread.join(5.0)
        self.assertFalse(script.thread.is_alive())

    def done(self) -> int:
        return len([evt for evt, _ in self.cli.conn.events if evt is event.EvtBehaviorDone])


class TestScripts(ScriptTestCase):

    def test_stack_blocks(self):
        script = self.make(cube_behaviors.BehaviorStackBlocks, needsActionID="StackCube")
        self.assertTrue(script.wants_to_run())
        self.run_script(script)
        self.assertEqual(self.steps, [("pick_up_cube", mock.sentinel.near), ("place_on_cube", mock.sentinel.far)])
        self.assertEqual(self.cli.played, ["StackBlocksSuccess"])
        self.assertEqual(self.needs.actions, ["StackCube"])
        self.assertEqual(self.done(), 1)

    def test_a_stack_that_fails_puts_the_cube_down(self):
        self.place_on_cube.side_effect = self.step("place_on_cube", False)
        script = self.make(cube_behaviors.BehaviorStackBlocks, needsActionID="StackCube")
        self.run_script(script)
        self.assertEqual([step[0] for step in self.steps], ["pick_up_cube", "place_on_cube", "put_down_cube"])
        self.assertEqual(self.cli.played, [])
        self.assertEqual(self.needs.actions, [])
        self.assertEqual(self.done(), 1)

    def test_a_missed_pick_up_is_reacted_to(self):
        self.pick_up_cube.side_effect = self.step("pick_up_cube", False)
        script = self.make(cube_behaviors.BehaviorPickUpAndPutDownCube)
        self.run_script(script)
        self.assertEqual(self.cli.played, ["ReactToBlockRetryPickup"])
        self.assertEqual(self.done(), 1)

    def test_pick_up_and_put_down(self):
        script = self.make(cube_behaviors.BehaviorPickUpAndPutDownCube, needsActionID="PickupCube_Sparked")
        self.run_script(script)
        self.assertEqual([step[0] for step in self.steps], ["pick_up_cube", "put_down_cube"])
        self.assertEqual(self.cli.played, ["ReactToBlockPickupSuccess"])
        self.assertEqual(self.needs.actions, ["PickupCube_Sparked"])

    def test_put_down_wants_a_cube_in_the_lift(self):
        script = self.make(cube_behaviors.BehaviorPutDownBlock)
        self.assertFalse(script.wants_to_run())
        self.cli.cubes.carried = mock.sentinel.near
        self.assertTrue(script.wants_to_run())
        self.run_script(script)
        self.assertEqual([step[0] for step in self.steps], ["put_down_cube"])

    def test_an_animation_the_client_does_not_have_is_skipped(self):
        script = self.make(cube_behaviors.BehaviorPickUpCube)
        del self.cli.animation_groups["ReactToBlockPickupSuccess"]
        self.run_script(script)
        self.assertEqual(self.cli.played, [])
        self.assertEqual(self.done(), 1)

    def test_a_script_that_fails_is_done(self):
        self.pick_up_cube.side_effect = RuntimeError("no robot")
        script = self.make(cube_behaviors.BehaviorPickUpCube)
        with self.assertLogs(pycozmo.logger, "ERROR"):
            self.run_script(script)
        self.assertEqual(self.done(), 1)

    def test_deactivating_cancels(self):
        started = threading.Event()

        def pick_up(cli: Any, cube: Any, cancel: threading.Event) -> bool:
            started.set()
            cancel.wait(5.0)
            raise Cancelled()

        self.pick_up_cube.side_effect = pick_up
        script = self.make(cube_behaviors.BehaviorStackBlocks)
        script.activate()
        self.assertTrue(started.wait(5.0))
        script.deactivate()
        script.thread.join(5.0)
        self.assertFalse(script.thread.is_alive())
        self.assertEqual(self.cli.stopped, 1)
        self.assertEqual(self.done(), 0)


class TestCubeLiftWorkout(ScriptTestCase):

    def make_workout(self, confident: float = -1.0) -> Any:
        script = self.make(cube_behaviors.BehaviorCubeLiftWorkout, needsActionID="Workout")
        script._workouts = [workout("highEnergy", "StrongWorkoutCompleted"),
                            workout("mediumEnergy", "MediumWorkoutCompleted"),
                            workout("lowEnergy", "WeakWorkoutCompleted")]
        script.get_mood = lambda: {"Confident": confident}
        for w in script._workouts:
            self.cli.animation_groups.update({name: None for name in (
                w.pre_lift, w.strong_lift, w.transition, w.weak_lift, w.post_lift, w.put_down)})
        return script

    def test_a_full_robot_works_out_hard(self):
        script = self.make_workout()
        self.run_script(script)
        self.assertEqual(self.cli.played, ["WorkoutPreLift_highEnergy"] + ["WorkoutStrongLift_highEnergy"] * 2 +
                         ["WorkoutPostLift_highEnergy", "WorkoutPutDown_highEnergy"])
        self.assertEqual([step[0] for step in self.steps], ["pick_up_cube", "put_down_cube"])
        emotion_events = [args[1] for evt, args in self.cli.conn.events if evt is event.EvtEmotionEvent]
        self.assertEqual(emotion_events, ["StrongWorkoutCompleted"])
        self.assertEqual(self.needs.actions, ["Workout"])
        self.assertEqual(self.done(), 1)

    def test_a_tired_one_less(self):
        self.needs.energy = "Warning"
        script = self.make_workout()
        self.run_script(script)
        self.assertEqual(self.cli.played[0], "WorkoutPreLift_lowEnergy")
        # A critical one gets the last workout there is.
        self.needs.energy = "Critical"
        self.assertEqual(script.workout().emotion_event, "WeakWorkoutCompleted")


class TestRegistered(unittest.TestCase):

    def test_by_class_name(self):
        for name, cls in (("PutDownBlock", cube_behaviors.BehaviorPutDownBlock),
                          ("PickUpCube", cube_behaviors.BehaviorPickUpCube),
                          ("PickUpAndPutDownCube", cube_behaviors.BehaviorPickUpAndPutDownCube),
                          ("CubeLiftWorkout", cube_behaviors.BehaviorCubeLiftWorkout),
                          ("StackBlocks", cube_behaviors.BehaviorStackBlocks)):
            self.assertIs(behavior.get_behavior_class_from_dict({"behaviorClass": name}), cls)
