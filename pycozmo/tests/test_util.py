import unittest

import pycozmo


class TestAngleComparison(unittest.TestCase):

    def setUp(self):
        self.angle = pycozmo.util.Angle(radians=1.0)

    def test_equal(self):
        self.assertEqual(self.angle, pycozmo.util.Angle(radians=1.0))
        self.assertNotEqual(self.angle, pycozmo.util.Angle(radians=2.0))

    def test_ordering(self):
        smaller = pycozmo.util.Angle(radians=0.5)
        self.assertTrue(smaller < self.angle)
        self.assertTrue(self.angle > smaller)
        self.assertTrue(smaller <= self.angle)
        self.assertTrue(self.angle >= smaller)

    def test_degrees(self):
        self.assertEqual(pycozmo.util.Angle(degrees=180.0), pycozmo.util.Angle(radians=3.141592653589793))

    def test_equality_with_other_types(self):
        # Comparing an object against an unrelated type answers, it does not raise. Angles reach application code
        # through Client.head_angle and Client.pose_pitch, where a comparison against None is ordinary.
        for other in (None, "1.0", 1.0, object()):
            self.assertFalse(self.angle == other)
            self.assertTrue(self.angle != other)

    def test_membership(self):
        self.assertIn(pycozmo.util.Angle(radians=1.0), [None, "x", self.angle])
        self.assertNotIn(self.angle, [None, "x"])

    def test_ordering_against_other_types(self):
        # Ordering is genuinely undefined against another type, so it still raises, through Python rather than
        # through a hand written check.
        for other in (None, "1.0"):
            with self.assertRaises(TypeError):
                self.angle < other       # noqa: B015
            with self.assertRaises(TypeError):
                self.angle >= other      # noqa: B015


class TestHexLoad(unittest.TestCase):

    def test_roundtrip(self):
        data = b"\x00\x01\xfe\xff"
        self.assertEqual(pycozmo.util.hex_load(pycozmo.util.hex_dump(data)), data)

    def test_returns_bytes(self):
        # Declared to return bytes, and callers may rely on it being immutable.
        self.assertIsInstance(pycozmo.util.hex_load("00:01"), bytes)
        self.assertNotIsInstance(pycozmo.util.hex_load("00:01"), bytearray)
