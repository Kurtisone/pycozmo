import threading
import unittest
from types import SimpleNamespace
from typing import Any, Optional
from unittest import mock

from pycozmo import charger_behaviors, robot
from pycozmo.charger_behaviors import BehaviorGoHome

NOT_ON_CHARGER = 0
ON_CHARGER = robot.RobotStatusFlag.IS_ON_CHARGER
PICKED_UP = robot.RobotStatusFlag.IS_PICKED_UP


def make(voltage: float = 4.0, status: int = NOT_ON_CHARGER) -> BehaviorGoHome:
    cli = SimpleNamespace(battery_voltage=voltage, robot_status=status, enable_camera=mock.Mock(),
                          stop_all_motors=mock.Mock(), cancel_anim=mock.Mock())
    return BehaviorGoHome(cli)


def cli(behavior: BehaviorGoHome) -> Any:
    return behavior.cli


def read(behavior: BehaviorGoHome, voltage: float, seconds: int = 15, start: float = 1000.0) -> float:
    """ A second's reading, for that many seconds; the time after the last. """
    behavior.cli.battery_voltage = voltage
    now = start
    for _ in range(seconds):
        behavior.sample(now)
        now += 1.0
    return now


class TestBattery(unittest.TestCase):

    def test_it_has_an_id(self):
        self.assertEqual(make().get_id(), "GoHome")

    def test_it_waits_for_enough_readings(self):
        behavior = make()
        read(behavior, 3.5, seconds=BehaviorGoHome.MIN_SAMPLES - 1)
        self.assertIsNone(behavior.battery_voltage())
        self.assertFalse(behavior.battery_is_low())

    def test_a_low_battery_is_low(self):
        behavior = make()
        read(behavior, 3.7)
        self.assertAlmostEqual(behavior.battery_voltage() or 0.0, 3.7)
        self.assertTrue(behavior.battery_is_low())

    def test_a_full_battery_is_not(self):
        behavior = make()
        read(behavior, 4.05)
        self.assertFalse(behavior.battery_is_low())

    def test_a_dip_under_the_motors_is_not_a_low_battery(self):
        behavior = make()
        now = read(behavior, 4.0, seconds=14)
        read(behavior, 3.6, seconds=3, start=now)
        self.assertFalse(behavior.battery_is_low())

    def test_readings_are_taken_once_a_second_and_forgotten(self):
        behavior = make()
        for step in range(100):
            behavior.sample(1000.0 + step * 0.1)
        self.assertEqual(len(behavior.samples), 10)
        read(behavior, 4.0, seconds=60, start=2000.0)
        self.assertLessEqual(len(behavior.samples), BehaviorGoHome.LOW_WINDOW + 1)

    def test_nothing_heard_from_the_robot_is_not_a_reading(self):
        behavior = make(voltage=0.0)
        read(behavior, 0.0)
        self.assertEqual(len(behavior.samples), 0)

    def test_the_charger_is_no_measure_of_the_battery(self):
        behavior = make()
        read(behavior, 3.6, seconds=5)
        behavior.cli.robot_status = ON_CHARGER
        behavior.cli.battery_voltage = 4.5
        behavior.sample(2000.0)
        self.assertEqual(len(behavior.samples), 0)


class TestWantsToRun(unittest.TestCase):

    def low(self, status: int = NOT_ON_CHARGER) -> BehaviorGoHome:
        behavior = make(voltage=3.6, status=status)
        read(behavior, 3.6)
        behavior.cli.robot_status = status
        return behavior

    def test_a_low_battery_off_the_charger_wants_to_go_home(self):
        self.assertTrue(self.low().wants_to_run(1100.0))

    def test_not_on_the_charger(self):
        self.assertFalse(self.low(ON_CHARGER).wants_to_run(1100.0))

    def test_not_in_somebody_s_hand(self):
        self.assertFalse(self.low(PICKED_UP).wants_to_run(1100.0))

    def test_not_with_a_full_battery(self):
        behavior = make()
        read(behavior, 4.0)
        self.assertFalse(behavior.wants_to_run(1100.0))

    def test_a_try_that_failed_is_not_repeated_at_once(self):
        behavior = self.low()
        behavior.last_attempt, behavior.last_success = 1000.0, False
        self.assertFalse(behavior.wants_to_run(1000.0 + BehaviorGoHome.RETRY_DELAY - 1.0))
        self.assertTrue(behavior.wants_to_run(1000.0 + BehaviorGoHome.RETRY_DELAY))


class TestScript(unittest.TestCase):

    def run_script(self, behavior: BehaviorGoHome, result: bool, cancel: Optional[threading.Event] = None) -> mock.Mock:
        cancel = cancel or threading.Event()
        with mock.patch.object(charger_behaviors.charger_handling, "go_to_charger", return_value=result) as go:
            behavior.script(cancel)
        return go

    def test_it_goes_to_the_charger_and_forgets_the_battery_it_had(self):
        behavior = make(voltage=3.6)
        read(behavior, 3.6)
        go = self.run_script(behavior, True)
        go.assert_called_once()
        self.assertTrue(behavior.last_success)
        self.assertEqual(len(behavior.samples), 0)
        # The brain's camera is on again.
        cli(behavior).enable_camera.assert_called_with(True, color=False)

    def test_a_charger_it_could_not_find_is_tried_again_later(self):
        behavior = make(voltage=3.6)
        read(behavior, 3.6)
        self.run_script(behavior, False)
        self.assertFalse(behavior.last_success)
        self.assertGreater(len(behavior.samples), 0)
        self.assertFalse(behavior.wants_to_run((behavior.last_attempt or 0.0) + 1.0))

    def test_it_reports_itself_done_when_it_has_run(self):
        behavior = make(voltage=3.6)
        done = threading.Event()
        with mock.patch.object(charger_behaviors.charger_handling, "go_to_charger", return_value=True), \
                mock.patch.object(behavior, "done", side_effect=done.set):
            behavior.activate()
            self.assertTrue(done.wait(2.0))

    def test_a_cancelled_one_leaves_the_camera_alone(self):
        behavior = make(voltage=3.6)
        cancel = threading.Event()
        cancel.set()
        self.run_script(behavior, False, cancel)
        cli(behavior).enable_camera.assert_not_called()
