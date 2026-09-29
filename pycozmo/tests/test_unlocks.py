import unittest

import pycozmo
from pycozmo import unlocks

from .test_brain import cozmo_assets_available


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestUnlocks(unittest.TestCase):

    def setUp(self):
        self.resource_dir = str(pycozmo.util.get_cozmo_asset_dir())

    def test_a_new_robot_can_stack_but_not_work_out(self):
        default = unlocks.load_default_unlocks(self.resource_dir)
        self.assertIn("StackTwoCubes", default)
        self.assertIn("PickupCube", default)
        self.assertNotIn("Workout", default)

    def test_the_levels_reward_the_rest(self):
        levels = unlocks.load_level_unlocks(self.resource_dir)
        self.assertEqual(levels[:6], [["FistBump"], ["FireTruckAlarm"], ["PopAWheelieAction"], ["BuildPyramid"],
                                      ["TrackLaser"], ["Workout"]])
        self.assertLessEqual({"Workout", "StackTwoCubes"}, unlocks.load_all_unlocks(self.resource_dir))
