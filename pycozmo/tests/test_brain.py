import unittest
from typing import List
from unittest import mock

import pycozmo


def cozmo_assets_available():
    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        return False
    return True


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestResumeLast(unittest.TestCase):
    """
    Reactions marked shouldResumeLast put back the behavior they interrupted.

    Three of the twenty-one reaction triggers are marked that way: CliffDetected, MotorCalibration
    and UnexpectedMovement. The others end whatever was running.
    """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        # A client that is never started: activating a behavior only dispatches events.
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def setUp(self):
        self.brain.behavior = None
        self.brain.behavior_to_resume = None

    def current(self):
        return self.brain.behavior.get_id() if self.brain.behavior else None

    def to_resume(self):
        return self.brain.behavior_to_resume.get_id() if self.brain.behavior_to_resume else None

    def test_the_three_resuming_triggers(self):
        resuming = {trigger.name
                    for reactions in self.brain.reaction_trigger_behavior_map.values()
                    for trigger in reactions if trigger.should_resume_last}
        self.assertEqual(resuming, {"CliffDetected", "MotorCalibration", "UnexpectedMovement"})

    def test_a_resuming_reaction_remembers_what_it_interrupted(self):
        self.brain.activate_behavior("Hiccup")
        self.assertEqual(self.current(), "Hiccup")
        self.brain.activate_behavior("ReactToCliff", resume_last=True)
        self.assertEqual(self.current(), "ReactToCliff")
        self.assertEqual(self.to_resume(), "Hiccup")

    def test_the_interrupted_behavior_comes_back(self):
        self.brain.activate_behavior("Hiccup")
        self.brain.activate_behavior("ReactToCliff", resume_last=True)
        self.brain.deactivate_behavior()
        self.brain.resume_behavior()
        self.assertEqual(self.current(), "Hiccup")
        self.assertIsNone(self.to_resume(), "resuming twice would loop")

    def test_a_reaction_that_does_not_resume_ends_what_it_interrupted(self):
        self.brain.activate_behavior("Hiccup")
        self.brain.activate_behavior("ReactToPickup")
        self.assertIsNone(self.to_resume())
        self.brain.deactivate_behavior()
        self.brain.resume_behavior()
        self.assertIsNone(self.current())

    def test_nothing_to_resume_when_nothing_was_running(self):
        self.brain.activate_behavior("ReactToCliff", resume_last=True)
        self.assertIsNone(self.to_resume())
        self.brain.deactivate_behavior()
        self.brain.resume_behavior()
        self.assertIsNone(self.current())

    def test_a_reaction_does_not_resume_itself(self):
        # A cliff detected while already reacting to a cliff would otherwise queue itself forever.
        self.brain.activate_behavior("ReactToCliff", resume_last=True)
        self.brain.activate_behavior("ReactToCliff", resume_last=True)
        self.assertIsNone(self.to_resume())

    def test_only_the_last_interruption_is_remembered(self):
        self.brain.activate_behavior("Hiccup")
        self.brain.activate_behavior("ReactToCliff", resume_last=True)
        self.brain.activate_behavior("ReactToUnexpectedMovement", resume_last=True)
        self.assertEqual(self.to_resume(), "ReactToCliff")

    def test_an_unknown_behavior_leaves_the_current_one_alone(self):
        self.brain.activate_behavior("Hiccup")
        self.brain.activate_behavior("NoSuchBehavior")
        self.assertEqual(self.current(), "Hiccup")


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestReactionCoverage(unittest.TestCase):
    """ Every reaction the brain can post has to reach a behavior. """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def test_every_orientation_reaction_is_in_the_trigger_map(self):
        for orientation, reaction in pycozmo.brain.Brain.ORIENTATION_REACTIONS.items():
            with self.subTest(orientation=orientation, reaction=reaction):
                self.assertIn(reaction, self.brain.reaction_trigger_behavior_map)

    def test_every_reaction_the_brain_posts_is_in_the_trigger_map(self):
        # The reactions the brain posts from its own handlers and heartbeat.
        posted = {"CliffDetected", "RobotPickedUp", "RobotFalling", "PlacedOnCharger", "Hiccup"}
        posted.update(pycozmo.brain.Brain.ORIENTATION_REACTIONS.values())
        for reaction in sorted(posted):
            with self.subTest(reaction=reaction):
                self.assertIn(reaction, self.brain.reaction_trigger_behavior_map)


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestChooseReaction(unittest.TestCase):
    """
    A trigger can name several behaviors, graded by how confident the robot is.

    Frustration is the only one in the resources: ReactToFrustrationMinor applies at or below -0.6
    confidence, ReactToFrustrationMajor at or below -0.9. The map used to keep one behavior per
    trigger, so the minor variant was dropped and the major one always ran.
    """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def setUp(self):
        for emotion_type in self.brain.emotion_types.values():
            emotion_type.value = 0.0
            emotion_type.base_value = 0.0
        for reactions in self.brain.reaction_trigger_behavior_map.values():
            for reaction in reactions:
                reaction.last_run = 0.0

    def choose(self, confidence):
        self.brain.emotion_types["Confident"].value = confidence
        reactions = self.brain.reaction_trigger_behavior_map["Frustration"]
        chosen = self.brain.choose_reaction(reactions)
        return chosen.behavior_id if chosen else None

    def test_a_deeply_unconfident_robot_reacts_badly(self):
        self.assertEqual(self.choose(-1.0), "ReactToFrustrationMajor")
        self.assertEqual(self.choose(-0.9), "ReactToFrustrationMajor")

    def test_a_mildly_unconfident_one_reacts_mildly(self):
        self.assertEqual(self.choose(-0.89), "ReactToFrustrationMinor")
        self.assertEqual(self.choose(-0.6), "ReactToFrustrationMinor")

    def test_a_confident_robot_still_reacts_mildly(self):
        # A trigger that fired is better answered than ignored.
        self.assertEqual(self.choose(0.0), "ReactToFrustrationMinor")
        self.assertEqual(self.choose(1.0), "ReactToFrustrationMinor")

    def test_a_single_behavior_needs_no_grading(self):
        reactions = self.brain.reaction_trigger_behavior_map["RobotPickedUp"]
        self.assertEqual(len(reactions), 1)
        chosen = self.brain.choose_reaction(reactions)
        assert chosen is not None
        self.assertEqual(chosen.behavior_id, "ReactToPickup")

    def test_a_cooldown_holds_a_reaction_back(self):
        # Only the minor frustration declares one, of 60 s.
        minor = next(r for r in self.brain.reaction_trigger_behavior_map["Frustration"]
                     if r.behavior_id == "ReactToFrustrationMinor")
        minor.ran()
        self.assertTrue(minor.is_on_cooldown())
        self.assertEqual(self.choose(0.0), "ReactToFrustrationMajor",
                         "the other variant is still free to run")

    def test_nothing_runs_when_every_variant_is_on_cooldown(self):
        for reaction in self.brain.reaction_trigger_behavior_map["Frustration"]:
            reaction.cooldown_time = 60.0
            reaction.ran()
        self.assertIsNone(self.choose(0.0))

    def test_a_reaction_without_a_cooldown_is_never_held_back(self):
        pickup = self.brain.reaction_trigger_behavior_map["RobotPickedUp"][0]
        self.assertEqual(pickup.cooldown_time, 0.0)
        pickup.ran()
        self.assertFalse(pickup.is_on_cooldown())


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestHiccups(unittest.TestCase):
    """
    Hiccups come in bouts, as hiccupParams asks: five to ten of them, 4.5 to 8 s apart, every 5 to
    55 minutes. The brain used to post one every 60 s, often enough to cut into what was running.
    """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def setUp(self):
        while not self.brain.reaction_queue.empty():
            self.brain.reaction_queue.get()

    def posted(self):
        n = 0
        while not self.brain.reaction_queue.empty():
            self.assertEqual(self.brain.reaction_queue.get(), "Hiccup")
            n += 1
        return n

    def test_the_parameters_come_from_the_resources(self):
        params = self.brain.get_hiccup_params()
        self.assertEqual(params["minHiccupOccurrenceFrequency_s"], 300)
        self.assertEqual(params["maxHiccupOccurrenceFrequency_s"], 3300)
        self.assertEqual(params["minNumberOfHiccupsToDo"], 5)
        self.assertEqual(params["maxNumberOfHiccupsToDo"], 10)
        self.assertEqual(params["minHiccupSpacing_ms"], 4500)
        self.assertEqual(params["maxHiccupSpacing_ms"], 8000)

    def test_a_bout_is_scheduled_far_off(self):
        self.brain.schedule_hiccup_bout(now=0.0)
        self.assertGreaterEqual(self.brain.next_hiccup_time, 300.0)
        self.assertLessEqual(self.brain.next_hiccup_time, 3300.0)
        self.assertGreaterEqual(self.brain.hiccups_left, 5)
        self.assertLessEqual(self.brain.hiccups_left, 10)

    def test_nothing_happens_before_the_bout_is_due(self):
        self.brain.schedule_hiccup_bout(now=0.0)
        for now in (0.0, 60.0, 120.0, 299.0):
            with self.subTest(now=now):
                self.brain.update_hiccups(now=now)
        self.assertEqual(self.posted(), 0, "60 s used to be enough for a hiccup")

    def test_a_bout_is_a_run_of_hiccups_then_a_long_wait(self):
        self.brain.schedule_hiccup_bout(now=0.0)
        expected = self.brain.hiccups_left
        now = self.brain.next_hiccup_time
        spacings = []
        for _ in range(expected):
            due = self.brain.next_hiccup_time
            spacings.append(due - now)
            now = due
            self.brain.update_hiccups(now=now)
        self.assertEqual(self.posted(), expected)
        # The first is the wait for the bout; the rest are the spacing inside it.
        for spacing in spacings[1:]:
            self.assertGreaterEqual(spacing, 4.5)
            self.assertLessEqual(spacing, 8.0)
        # And the bout over, the next one is minutes away.
        self.assertGreaterEqual(self.brain.next_hiccup_time - now, 300.0)

    def test_the_heartbeat_does_not_hiccup_every_minute(self):
        # A whole hour of heartbeat frames, counted rather than run.
        self.brain.schedule_hiccup_bout(now=0.0)
        for frame in range(pycozmo.robot.FRAME_RATE * 3600):
            self.brain.update_hiccups(now=frame / float(pycozmo.robot.FRAME_RATE))
        hiccups = self.posted()
        self.assertGreaterEqual(hiccups, 5, "at least one bout in an hour")
        self.assertLess(hiccups, 60, "sixty is what one a minute would have given")


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestStop(unittest.TestCase):
    """ Stopping the brain has to let go of the robot, not leave a behavior running on it. """

    def setUp(self):
        self.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def test_the_running_behavior_is_deactivated(self):
        # It used to keep its animation playing and, for ReactToOnCharger, its timers armed.
        self.brain.start()
        charger = self.brain.behaviors["ReactToOnCharger"]
        assert isinstance(charger, pycozmo.behavior.BehaviorReactToOnCharger)
        self.brain.activate_behavior("ReactToOnCharger")
        charger.on_sequence_completed()
        self.assertIsNotNone(charger.timer)
        self.brain.stop()
        self.assertIsNone(self.brain.behavior)
        self.assertIsNone(charger.timer, "a timer left armed fires into a stopped session")
        self.assertNotIn(charger, self.brain.cli.dispatch_children)

    def test_nothing_is_left_to_resume(self):
        self.brain.start()
        self.brain.activate_behavior("Hiccup")
        self.brain.activate_behavior("ReactToCliff", resume_last=True)
        self.assertIsNotNone(self.brain.behavior_to_resume)
        self.brain.stop()
        self.assertIsNone(self.brain.behavior_to_resume)

    def test_the_brain_stops_listening(self):
        self.brain.start()
        self.assertTrue(self.brain.handlers)
        self.brain.stop()
        self.assertEqual(self.brain.handlers, [])
        # A reaction posted now would queue up for a thread that no longer runs.
        self.brain.cli.dispatch(pycozmo.event.EvtRobotPickedUpChange, self.brain.cli, True)
        self.assertTrue(self.brain.reaction_queue.empty())


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestActivityEngine(unittest.TestCase):
    """
    What the robot does when nothing has happened to it.

    Freeplay lists twenty-five sub-activities in priority order, and nearly all of them need a cube,
    a face or a player asking for a game. What is left is the hiking intro and, at the bottom of the
    list, the bored animations - which is close to what a real Cozmo does when left alone.
    """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def setUp(self):
        self.brain.behavior = None
        self.brain.behavior_to_resume = None
        self.brain.sub_activity = None
        self.brain.next_choice_time = 0.0
        self.brain.on_treads_time = None
        self.brain.drive_off_charger_time = None
        for activity in self.brain.activities.values():
            activity.start_time = None
            activity.cooldown_end_time = 0.0
            for chooser in (activity.behavior_chooser, activity.interlude_chooser):
                if chooser is not None:
                    chooser.reset()
        self.played: List[str] = []
        patcher = mock.patch.object(self.brain.cli, "play_anim_group", self.played.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def choose(self, now):
        """ The activity and behavior the engine would run, by identifier. """
        chosen = self.brain.choose_activity(now)
        return (chosen[0].id, chosen[1]) if chosen else None

    def current_activity(self):
        return self.brain.sub_activity.id if self.brain.sub_activity else None

    def current_behavior(self):
        return self.brain.behavior.get_id() if self.brain.behavior else None

    def test_the_robot_looks_around_first(self):
        self.assertEqual(self.choose(1000.0), ("Hiking", "Hiking_FirstLookIntro"))

    def test_an_activity_that_can_offer_nothing_is_passed_over(self):
        # Socialize outranks Hiking and its mood gate is open at rest, but everything it names needs
        # a cube or a face. Entering it would leave the robot doing nothing for five minutes.
        socialize = self.brain.activities["Socialize"]
        self.assertTrue(socialize.wants_to_run(self.brain.get_mood(), now=1000.0))
        self.assertEqual(self.choose(1000.0)[0], "Hiking")

    def test_the_hiking_intro_only_runs_just_after_the_switch(self):
        # It asks for a quarter of a second since the activity was entered.
        self.assertTrue(self.brain.can_run_behavior("Hiking_FirstLookIntro", 1000.0, 1000.1))
        self.assertFalse(self.brain.can_run_behavior("Hiking_FirstLookIntro", 1000.0, 1001.0))

    def test_the_hiking_wake_up_needs_a_recent_drive_off_the_charger(self):
        self.assertFalse(self.brain.can_run_behavior("Hiking_FirstLookWakeUp", 1000.0, 1000.0))
        self.brain.drive_off_charger_time = 1000.0
        self.assertTrue(self.brain.can_run_behavior("Hiking_FirstLookWakeUp", 1000.0, 1000.5))
        self.assertFalse(self.brain.can_run_behavior("Hiking_FirstLookWakeUp", 1000.0, 1002.0))

    def test_only_the_animations_of_nothing_to_do_can_run(self):
        # The other four need to drive into a cube, see an obstacle or be holding something.
        chooser = self.brain.activities["NothingToDo"].behavior_chooser
        assert chooser is not None
        runnable = [behavior_id for behavior_id in chooser.behavior_names
                    if self.brain.can_run_behavior(behavior_id, 1000.0, 1000.0)]
        self.assertEqual(sorted(runnable), ["NothingToDo_BoredAnim", "NothingToDo_Idle"])

    def test_it_settles_into_the_bored_animations(self):
        now = 1000.0
        self.brain.update_activity(now)
        self.assertEqual(self.current_activity(), "Hiking")
        self.assertEqual(self.played, ["HikingIntro"])
        # Hiking has nothing else to offer once its intro is behind it.
        self.brain.behavior = None
        now += 3.0
        self.brain.update_activity(now)
        self.assertEqual(self.current_activity(), "NothingToDo")
        self.assertIn(self.current_behavior(), ("NothingToDo_Idle", "NothingToDo_BoredAnim"))

    def test_hiking_goes_on_cooldown_when_it_is_passed_over(self):
        self.brain.update_activity(1000.0)
        self.brain.behavior = None
        self.brain.update_activity(1003.0)
        # Fifteen seconds, as hiking.json asks.
        self.assertEqual(self.brain.activities["Hiking"].cooldown_end_time, 1018.0)

    def test_a_behavior_runs_to_completion(self):
        self.brain.update_activity(1000.0)
        running = self.brain.behavior
        self.brain.update_activity(1001.0)
        self.assertIs(self.brain.behavior, running)
        self.assertEqual(self.played, ["HikingIntro"], "an animation was cut short")

    def test_nothing_starts_while_a_reaction_has_something_to_put_back(self):
        self.brain.behavior_to_resume = self.brain.behaviors["Hiccup"]
        self.brain.update_activity(1000.0)
        self.assertIsNone(self.brain.behavior)

    def test_it_waits_before_looking_again_when_it_finds_nothing(self):
        # Every activity is consulted each time, which is not free at thirty times a second.
        with mock.patch.object(self.brain, "choose_activity", return_value=None) as choose:
            self.brain.update_activity(1000.0)
            self.brain.update_activity(1000.5)
            self.assertEqual(choose.call_count, 1)
            self.brain.update_activity(1001.0)
            self.assertEqual(choose.call_count, 2)

    def test_running_a_behavior_holds_it_back(self):
        chooser = self.brain.activities["NothingToDo"].behavior_chooser
        assert chooser is not None
        self.brain.activities["NothingToDo"].ran("NothingToDo_BoredAnim", now=1000.0)
        bored = chooser.behavior_names.index("NothingToDo_BoredAnim")
        self.assertEqual(chooser.get_scores(now=1000.0)[bored], 0.5)
        self.assertEqual(chooser.get_scores(now=1009.0)[bored], 1.0)

    def test_the_robot_gets_off_its_charger(self):
        # Nothing did before: the brain's start() carried a note to do it and the behavior reported
        # itself done without moving.
        self.brain.cli.robot_status = pycozmo.robot.RobotStatusFlag.IS_ON_CHARGER
        self.addCleanup(setattr, self.brain.cli, "robot_status", 0)
        with mock.patch.object(self.brain.cli, "drive_wheels") as drive_wheels:
            self.brain.update_activity(1000.0)
            self.assertEqual(self.current_behavior(), "DriveOffCharger")
            drive_wheels.assert_called_once()
        assert self.brain.behavior is not None
        self.brain.behavior.deactivate()


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestStopGivesTheActivityUp(unittest.TestCase):

    def test_the_activity_is_ended(self):
        brain = pycozmo.brain.Brain(pycozmo.client.Client())
        brain.start()
        brain.start_sub_activity(brain.activities["NothingToDo"])
        brain.stop()
        self.assertIsNone(brain.sub_activity)
