import unittest

import pycozmo


def graph(*nodes):
    return pycozmo.emotions.DecayGraph([pycozmo.emotions.Node(x=x, y=y) for x, y in nodes])


#: The decay graph most emotions use: full value for 10 s, nothing left by 150 s.
DEFAULT_GRAPH = ((0, 1), (10, 1), (30, 0.9), (75, 0.6), (150, 0))
#: WantToPlay, a single node, which never decays.
CONSTANT_GRAPH = ((0, 1), )


def emotion_type(nodes=DEFAULT_GRAPH, name="Happy"):
    return pycozmo.emotions.EmotionType(name, graph(*nodes), graph(*DEFAULT_GRAPH))


class TestEmotionType(unittest.TestCase):

    def test_starts_at_rest(self):
        self.assertEqual(emotion_type().value, 0.0)

    def test_add(self):
        emotion = emotion_type()
        emotion.add(0.5, now=0.0)
        self.assertAlmostEqual(emotion.value, 0.5)

    def test_add_accumulates(self):
        emotion = emotion_type()
        emotion.add(0.3, now=0.0)
        emotion.add(0.3, now=1.0)
        self.assertAlmostEqual(emotion.value, 0.6)

    def test_clamped_to_the_range(self):
        emotion = emotion_type()
        emotion.add(1.0, now=0.0)
        emotion.add(1.0, now=1.0)
        self.assertAlmostEqual(emotion.value, pycozmo.emotions.EmotionType.MAX_VALUE)
        emotion = emotion_type()
        emotion.add(-1.0, now=0.0)
        emotion.add(-1.0, now=1.0)
        self.assertAlmostEqual(emotion.value, pycozmo.emotions.EmotionType.MIN_VALUE)

    def test_decays_along_the_graph(self):
        emotion = emotion_type()
        emotion.add(1.0, now=0.0)
        for elapsed, expected in ((0.0, 1.0), (10.0, 1.0), (30.0, 0.9), (75.0, 0.6), (150.0, 0.0)):
            with self.subTest(elapsed=elapsed):
                emotion.update(now=elapsed)
                self.assertAlmostEqual(emotion.value, expected)

    def test_decay_is_proportional_to_the_value(self):
        emotion = emotion_type()
        emotion.add(0.5, now=0.0)
        emotion.update(now=75.0)
        self.assertAlmostEqual(emotion.value, 0.3)

    def test_a_forgotten_emotion_does_not_come_back_inverted(self):
        # Past its last node the graph extrapolates below zero. Left unclamped, the value would
        # flip sign and grow without bound the longer it was left alone.
        emotion = emotion_type()
        emotion.add(1.0, now=0.0)
        for elapsed in (150.0, 200.0, 1000.0, 100000.0):
            with self.subTest(elapsed=elapsed):
                emotion.update(now=elapsed)
                self.assertEqual(emotion.value, 0.0)

    def test_a_negative_value_decays_towards_zero_too(self):
        emotion = emotion_type()
        emotion.add(-1.0, now=0.0)
        emotion.update(now=75.0)
        self.assertAlmostEqual(emotion.value, -0.6)
        emotion.update(now=150.0)
        self.assertEqual(emotion.value, 0.0)

    def test_a_single_node_graph_never_decays(self):
        emotion = emotion_type(CONSTANT_GRAPH, name="WantToPlay")
        emotion.add(1.0, now=0.0)
        emotion.update(now=100000.0)
        self.assertAlmostEqual(emotion.value, 1.0)

    def test_adding_restarts_the_decay(self):
        emotion = emotion_type()
        emotion.add(1.0, now=0.0)
        emotion.update(now=75.0)
        self.assertAlmostEqual(emotion.value, 0.6)
        emotion.add(0.1, now=75.0)
        self.assertAlmostEqual(emotion.value, 0.7)
        emotion.update(now=80.0)
        self.assertAlmostEqual(emotion.value, 0.7, msg="decay should have started over")

    def test_update_without_a_time_uses_the_clock(self):
        emotion = emotion_type()
        emotion.add(1.0)
        emotion.update()
        self.assertAlmostEqual(emotion.value, 1.0, places=3)


def cozmo_assets_available():
    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        return False
    return True


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestMood(unittest.TestCase):
    """ The brain applying the shipped emotion events to the shipped emotion types. """

    brain: pycozmo.brain.Brain

    @classmethod
    def setUpClass(cls):
        # A client that is never started: the brain only reads resources and adds handlers.
        cls.brain = pycozmo.brain.Brain(pycozmo.client.Client())

    def setUp(self):
        for emotion_type in self.brain.emotion_types.values():
            emotion_type.value = 0.0
            emotion_type.base_value = 0.0

    def test_mood_starts_neutral(self):
        self.assertEqual(set(self.brain.get_mood()),
                         {"WantToPlay", "Social", "Confident", "Excited", "Happy", "Calm", "Brave"})
        self.assertEqual(set(self.brain.get_mood().values()), {0.0})
        self.assertEqual(self.brain.get_mood_description(), "neutral")

    def test_a_cliff_is_unsettling(self):
        # The CliffDetected emotion event is named exactly after the reaction trigger.
        self.brain.post_emotion_event("CliffDetected")
        mood = self.brain.get_mood()
        self.assertAlmostEqual(mood["Happy"], -0.12)
        self.assertAlmostEqual(mood["Calm"], -0.12)
        self.assertAlmostEqual(mood["Brave"], -0.12)
        self.assertAlmostEqual(mood["Confident"], 0.0)

    def test_getting_over_frustration_restores_confidence(self):
        self.brain.post_emotion_event("FinishedMinorFrustration")
        self.assertAlmostEqual(self.brain.get_mood()["Confident"], 0.1)
        self.brain.post_emotion_event("FinishedMajorFrustration")
        self.assertAlmostEqual(self.brain.get_mood()["Confident"], 1.0)

    def test_an_unknown_name_is_ignored(self):
        # Most reaction triggers have no emotion event of their own name.
        self.brain.post_emotion_event("RobotPickedUp")
        self.assertEqual(self.brain.get_mood_description(), "neutral")

    def test_mood_description_lists_what_moved(self):
        self.brain.post_emotion_event("ReactToUnexpectedMovement")
        self.assertEqual(self.brain.get_mood_description(), "Confident -0.20")

    def test_repeated_events_accumulate_and_stay_in_range(self):
        for _ in range(20):
            self.brain.post_emotion_event("CliffDetected")
        self.assertAlmostEqual(self.brain.get_mood()["Happy"],
                               pycozmo.emotions.EmotionType.MIN_VALUE)

    def test_the_heartbeat_decays_the_mood(self):
        self.brain.post_emotion_event("FinishedMajorFrustration")
        confident = self.brain.emotion_types["Confident"]
        self.assertAlmostEqual(confident.value, 1.0)
        # Confident holds for 30 s and is gone by 70 s.
        confident.last_change_time -= 70.0
        self.brain.update_emotion_types()
        self.assertEqual(confident.value, 0.0)
