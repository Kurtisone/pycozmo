import unittest

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
