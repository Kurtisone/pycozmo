import time
import unittest
from typing import Any, Dict

import pycozmo


def cozmo_assets_available():
    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        return False
    return True


class TestObjective(unittest.TestCase):

    def test_completions_needed(self):
        # The maximum used to be assigned from the minimum, so an objective that allowed a range always reported
        # the bottom of it.
        objective = pycozmo.activity.Objective(
            objective="obj", behavior_id="beh", ignore_if_locked="false",
            probability_to_require_objective=1.0,
            random_completions_needed_min=1,
            random_completions_needed_max=5)
        self.assertEqual(objective.random_completions_needed_min, 1)
        self.assertEqual(objective.random_completions_needed_max, 5)

    def test_completions_needed_none(self):
        objective = pycozmo.activity.Objective(
            objective="obj", behavior_id="beh", ignore_if_locked="false",
            probability_to_require_objective=1.0,
            random_completions_needed_min=None,
            random_completions_needed_max=None)
        self.assertEqual(objective.random_completions_needed_min, 0)
        self.assertEqual(objective.random_completions_needed_max, 0)

    def test_completions_needed_mixed(self):
        # A maximum with no minimum used to reach int(None).
        objective = pycozmo.activity.Objective(
            objective="obj", behavior_id="beh", ignore_if_locked="false",
            probability_to_require_objective=1.0,
            random_completions_needed_min=None,
            random_completions_needed_max=5)
        self.assertEqual(objective.random_completions_needed_min, 0)
        self.assertEqual(objective.random_completions_needed_max, 5)

    def test_from_json(self):
        objective = pycozmo.activity.Objective.from_json({
            "objective": "obj",
            "behaviorID": "beh",
            "ignoreIfLocked": "false",
            "probabilityToRequireObjective": 0.5,
            "randomCompletionsNeededMin": 2,
            "randomCompletionsNeededMax": 7,
        })
        self.assertEqual(objective.random_completions_needed_min, 2)
        self.assertEqual(objective.random_completions_needed_max, 7)


class TestBehaviorChooser(unittest.TestCase):

    @staticmethod
    def scoring(*behaviors):
        return pycozmo.activity.BehaviorChooser.from_json(
            {"type": "Scoring", "behaviors": list(behaviors)})

    @staticmethod
    def scored(behavior_id, score, penalty_nodes=None):
        scoring: Dict[str, Any] = {"flatScore": score}
        if penalty_nodes is not None:
            scoring["repetitionPenalty"] = {"nodes": [{"x": x, "y": y} for x, y in penalty_nodes]}
        return {"behaviorID": behavior_id, "scoring": scoring}

    def test_selection_chooses_nothing(self):
        chooser = pycozmo.activity.BehaviorChooser.from_json({"type": "Selection"})
        self.assertIsNone(chooser.get_sorted_choices())

    def test_strict_priority_keeps_its_order(self):
        # The branch used to hand back the raw entries, which nothing could look a behavior up by.
        chooser = pycozmo.activity.BehaviorChooser.from_json(
            {"type": "StrictPriority", "behaviors": ["First", "Second"]})
        self.assertEqual(chooser.get_sorted_choices(), ["First", "Second"])

    def test_scoring_offers_every_behavior(self):
        chooser = self.scoring(self.scored("High", 100.0), self.scored("Low", 1.0))
        choices = chooser.get_sorted_choices()
        assert choices is not None
        self.assertEqual(sorted(choices), ["High", "Low"])

    def test_the_high_scorer_usually_comes_first(self):
        chooser = self.scoring(self.scored("High", 100.0), self.scored("Low", 1.0))
        firsts = []
        for _ in range(200):
            choices = chooser.get_sorted_choices()
            assert choices is not None
            firsts.append(choices[0])
        self.assertGreater(firsts.count("High"), firsts.count("Low"))

    def test_a_behavior_that_just_ran_is_held_back(self):
        # NothingToDo_BoredAnim's graph: half its score for the nine seconds after it ran, so that
        # the robot idles normally rather than playing two bored sequences in a row.
        chooser = self.scoring(self.scored("Bored", 1.0, [(0.0, 0.5), (9.0, 1.0)]),
                               self.scored("Idle", 1.0))
        chooser.behavior_ran("Bored", now=100.0)
        self.assertEqual(chooser.get_scores(now=100.0), [0.5, 1.0])
        self.assertEqual(chooser.get_scores(now=104.5), [0.75, 1.0])
        self.assertEqual(chooser.get_scores(now=109.0), [1.0, 1.0])

    def test_a_penalty_stops_at_its_last_node(self):
        # The graph used to be read at a number of repetitions and subtracted, which took a
        # behavior to nought for good after two runs. The x axis is seconds, and the y axis is the
        # fraction of the score won back, so a behavior recovers rather than tiring out for ever.
        chooser = self.scoring(self.scored("Bored", 1.0, [(0.0, 0.5), (9.0, 1.0)]))
        chooser.behavior_ran("Bored", now=100.0)
        self.assertEqual(chooser.get_scores(now=1000.0), [1.0])

    def test_a_lockout_ends_at_its_own_node(self):
        # MeetCozmo_InteractWithFaces carries two nodes sharing an x: nothing for eight seconds,
        # whole afterwards. The line through them is flat at nought, so it must not be extended.
        chooser = self.scoring(self.scored("Interact", 3.0, [(8.0, 0.0), (8.0, 1.0)]))
        chooser.behavior_ran("Interact", now=0.0)
        self.assertEqual(chooser.get_scores(now=7.9), [0.0])
        self.assertEqual(chooser.get_scores(now=8.0), [3.0])
        self.assertEqual(chooser.get_scores(now=80.0), [3.0])

    def test_nothing_is_offered_while_everything_is_held_back(self):
        # Every score at nought used to be divided by a total of nought.
        chooser = self.scoring(self.scored("Only", 1.0, [(8.0, 0.0), (8.0, 1.0)]))
        chooser.behavior_ran("Only", now=0.0)
        self.assertIsNone(chooser.get_sorted_choices(now=1.0))
        self.assertEqual(chooser.get_sorted_choices(now=8.0), ["Only"])

    def test_reset_forgets_what_ran(self):
        chooser = self.scoring(self.scored("Bored", 1.0, [(0.0, 0.5), (9.0, 1.0)]))
        chooser.behavior_ran("Bored", now=100.0)
        chooser.reset()
        self.assertEqual(chooser.get_scores(now=100.0), [1.0])

    def test_an_unknown_type_is_reported(self):
        chooser = pycozmo.activity.BehaviorChooser.from_json({"type": "Telepathy"})
        with self.assertRaises(ValueError):
            chooser.get_sorted_choices()


class TestActivity(unittest.TestCase):

    @staticmethod
    def make(strategy=None, behaviors=None, interludes=None):
        data: Dict[str, Any] = {
            "activityID": "Test",
            "activityType": "BehaviorsOnly",
            "activityStrategy": dict({"type": "Simple"}, **(strategy or {})),
        }
        if behaviors is not None:
            data["behaviorChooser"] = {"type": "StrictPriority", "behaviors": behaviors}
        if interludes is not None:
            data["interludeBehaviorChooser"] = {"type": "StrictPriority", "behaviors": interludes}
        return pycozmo.activity.Activity(**pycozmo.activity.Activity.base_kwargs(data))

    def test_an_activity_with_no_duration_never_ends_by_itself(self):
        activity = self.make()
        activity.started(now=0.0)
        self.assertFalse(activity.should_end(now=10000.0))

    def test_a_negative_duration_never_ends(self):
        # That is how the severe-needs activities hold on until the need is met.
        activity = self.make({"activityShouldEndDurationSecs": -1.0})
        activity.started(now=0.0)
        self.assertFalse(activity.should_end(now=10000.0))

    def test_it_ends_once_its_duration_is_up(self):
        activity = self.make({"activityShouldEndDurationSecs": 25.0})
        activity.started(now=0.0)
        self.assertFalse(activity.should_end(now=24.0))
        self.assertTrue(activity.should_end(now=25.0))

    def test_an_activity_that_has_not_started_does_not_end(self):
        self.assertFalse(self.make({"activityShouldEndDurationSecs": 1.0}).should_end(now=100.0))

    def test_a_cooldown_keeps_it_from_starting_again(self):
        activity = self.make({"cooldownBaseSecs": 30.0})
        activity.started(now=0.0)
        activity.ended(now=10.0)
        self.assertFalse(activity.wants_to_run(now=39.0))
        self.assertTrue(activity.wants_to_run(now=40.0))

    def test_an_unsupported_strategy_never_wants_to_run(self):
        self.assertFalse(self.make({"type": "Spark"}).wants_to_run(now=0.0))

    def test_the_mood_can_hold_it_back(self):
        activity = self.make({
            "requiredMinStartMoodScore": 0.5,
            "startMoodScorer": [{
                "emotionType": "Social",
                "scoreGraph": {"nodes": [{"x": -1.0, "y": 1.0}, {"x": 0.3, "y": 1.0},
                                         {"x": 0.3, "y": 0.0}, {"x": 1.0, "y": 0.0}]},
                "trackDelta": False,
            }],
        })
        self.assertTrue(activity.wants_to_run({"Social": 0.0}, now=0.0))
        self.assertFalse(activity.wants_to_run({"Social": 0.9}, now=0.0))

    def test_it_can_ask_for_the_robot_to_have_just_been_put_down(self):
        activity = self.make({"requiredRecentOnTreadsEventSecs": 5.0})
        self.assertFalse(activity.wants_to_run(now=100.0, on_treads_time=None))
        self.assertFalse(activity.wants_to_run(now=100.0, on_treads_time=94.0))
        self.assertTrue(activity.wants_to_run(now=100.0, on_treads_time=96.0))

    def test_choose_skips_what_cannot_run(self):
        activity = self.make(behaviors=["First", "Second"])
        self.assertEqual(activity.choose(lambda behavior_id: behavior_id == "Second"), "Second")

    def test_choose_gives_up_when_nothing_can_run(self):
        activity = self.make(behaviors=["First", "Second"])
        self.assertIsNone(activity.choose(lambda behavior_id: False))

    def test_an_activity_with_no_chooser_runs_nothing(self):
        self.assertIsNone(self.make().choose(lambda behavior_id: True))

    def test_interludes_come_first(self):
        activity = self.make(behaviors=["Ordinary"], interludes=["Interlude"])
        self.assertEqual(activity.choose(lambda behavior_id: True), "Interlude")


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAgainstCozmoAssets(unittest.TestCase):
    """ Check the activities against the Anki resources they are read from. """

    activities: Dict[str, pycozmo.activity.Activity]

    @classmethod
    def setUpClass(cls):
        cls.activities = pycozmo.activity.load_activities(str(pycozmo.util.get_cozmo_asset_dir()))

    def test_the_strategies_that_are_not_evaluated(self):
        # Every one of them gates on what this library does not have: a spark from the application,
        # the nurture needs, a pyramid of cubes or a player asking for a game.
        unsupported = {activity.strategy.type
                       for activity in self.activities.values() if not activity.strategy.is_supported}
        self.assertEqual(unsupported, {"Spark", "Needs", "SevereNeedTransition",
                                       "NeedBasedCooldown", "PlayWithHumans", "Pyramid"})

    def test_every_sub_activity_is_known(self):
        for activity in self.activities.values():
            for entry in activity.sub_activities:
                self.assertIn(entry["activityID"], self.activities, activity.id)

    def test_freeplay_holds_the_others(self):
        self.assertEqual(len(self.activities["Freeplay"].sub_activities), 25)

    def test_socialize_waits_for_the_robot_to_feel_unsocial(self):
        # The graph steps at 0.3, two of its nodes sharing that x. Which side of the step 0.3
        # itself falls on is not worth reading anything into.
        strategy = self.activities["Socialize"].strategy
        self.assertTrue(strategy.mood_allows({"Social": -1.0}))
        self.assertTrue(strategy.mood_allows({"Social": 0.29}))
        self.assertFalse(strategy.mood_allows({"Social": 0.31}))
        self.assertFalse(strategy.mood_allows({"Social": 1.0}))

    def test_singing_starts_in_cooldown(self):
        singing = self.activities["Singing"]
        self.assertTrue(singing.strategy.start_in_cooldown)
        self.assertGreater(singing.cooldown_end_time, time.perf_counter())

    def test_the_feeding_chooser_is_read(self):
        # Feeding keeps its behaviors under "universalChooser", having no sub-activities to share
        # them with. They used to be read into a list of names nothing consulted.
        chooser = self.activities["Feeding"].behavior_chooser
        assert chooser is not None
        choices = chooser.get_sorted_choices()
        assert choices is not None
        self.assertEqual(choices[0], "DriveOffCharger")

    def test_nothing_to_do_gives_the_robot_up_after_every_behavior(self):
        # Its one-second duration is how the resources let a higher priority take over.
        self.assertEqual(self.activities["NothingToDo"].strategy.should_end_duration, 1.0)
