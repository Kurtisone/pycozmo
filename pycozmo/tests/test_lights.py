import unittest

import pycozmo


class TestColor(unittest.TestCase):

    def test_unnamed(self):
        # The name defaulted to the typing form Optional[None], which evaluates to the NoneType class rather than
        # to None. An unnamed color therefore had a truthy name.
        color = pycozmo.lights.Color(rgb=(255, 0, 0))
        self.assertIsNone(color.name)
        self.assertFalse(color.name)

    def test_named(self):
        color = pycozmo.lights.Color(rgb=(255, 0, 0), name="red")
        self.assertEqual(color.name, "red")

    def test_predefined(self):
        for name in ("green", "red", "blue", "white", "off"):
            self.assertEqual(getattr(pycozmo.lights, name).name, name)

    def test_int_color(self):
        self.assertEqual(pycozmo.lights.Color(rgb=(255, 0, 0)).int_color, 0xff0000ff)

    def test_empty(self):
        color = pycozmo.lights.Color()
        self.assertIsNone(color.name)
        self.assertEqual(color.int_color, 0)
