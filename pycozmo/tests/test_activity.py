import unittest

import pycozmo


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
