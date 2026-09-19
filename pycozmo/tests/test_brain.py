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
        resuming = {trigger.name for trigger in self.brain.reaction_trigger_beahvior_map.values()
                    if trigger.should_resume_last}
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
                self.assertIn(reaction, self.brain.reaction_trigger_beahvior_map)

    def test_every_reaction_the_brain_posts_is_in_the_trigger_map(self):
        # The reactions the brain posts from its own handlers and heartbeat.
        posted = {"CliffDetected", "RobotPickedUp", "RobotFalling", "PlacedOnCharger", "Hiccup"}
        posted.update(pycozmo.brain.Brain.ORIENTATION_REACTIONS.values())
        for reaction in sorted(posted):
            with self.subTest(reaction=reaction):
                self.assertIn(reaction, self.brain.reaction_trigger_beahvior_map)
