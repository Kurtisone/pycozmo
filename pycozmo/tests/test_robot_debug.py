
import unittest
import logging

from pycozmo.robot_debug import get_log_level, get_debug_message


class TestLogLevel(unittest.TestCase):

    def test_invalid(self):
        level = get_log_level(-1)
        self.assertEqual(level, logging.DEBUG)

    def test_debug(self):
        level = get_log_level(1)
        self.assertEqual(level, logging.DEBUG)

    def test_debug2(self):
        level = get_log_level(2)
        self.assertEqual(level, logging.DEBUG)

    def test_info(self):
        level = get_log_level(3)
        self.assertEqual(level, logging.INFO)

    def test_warning(self):
        level = get_log_level(4)
        self.assertEqual(level, logging.WARNING)

    def test_error(self):
        level = get_log_level(5)
        self.assertEqual(level, logging.ERROR)


class TestDebugMessage(unittest.TestCase):

    def test_no_name_no_format(self):
        msg = get_debug_message(-1, -1, [])
        self.assertEqual(msg, "")

    def test_no_format(self):
        msg = get_debug_message(0, -1, [])
        self.assertEqual(msg, "ASSERT")

    def test_no_name(self):
        msg = get_debug_message(-1, 0, [])
        self.assertEqual(msg, "Invalid format ID")

    def test_name_format(self):
        msg = get_debug_message(7, 3, [])
        self.assertEqual(msg, "HeadController: Initializing")

    def test_name_format_args(self):
        msg = get_debug_message(409, 624, [0, 0x11, 0x22, 0x33, 0x44, 0x55])
        self.assertEqual(msg, "macaddr.soft_ap: 00:11:22:33:44:55")

    def test_name_format_invalid_args(self):
        with self.assertRaises(AssertionError):
            get_debug_message(409, 624, [])

    def test_a_float_argument_is_read_as_one(self):
        # Words as a robot sent them: 456 and 300 degrees per second, as IEEE floats.
        msg = get_debug_message(244, 533, [0x43E40000, 0x43960000])
        self.assertEqual(msg, "SteeringController.ExecutePointTurn_2.PointTurnTooFast: "
                              "Speed of 456.000000 deg/s exceeds limit of 300.000000 deg/s. Clamping.")

    def test_a_negative_integer_is_read_as_one(self):
        msg = get_debug_message(-1, 6, [0xFFFFFFFF, 747])
        self.assertEqual(msg, "BufferKeyFrame.BufferFull -1 bytes available, 747 needed.")

    def test_a_percent_sign_takes_no_argument(self):
        from pycozmo.robot_debug import _typed_args
        self.assertEqual(_typed_args("%d%% of %.2f", [5, 0x3FC00000]), (5, 1.5))

    def test_every_format_takes_the_arguments_it_declares(self):
        from pycozmo.robot_debug import ROBOT_FORMAT_IDS, _typed_args
        for format_id, (fmt, count) in ROBOT_FORMAT_IDS.items():
            with self.subTest(format_id=format_id):
                typed = _typed_args(fmt, [0] * count)
                self.assertEqual(len(typed), count)
                fmt % typed
