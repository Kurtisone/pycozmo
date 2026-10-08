"""

The behavior that takes the robot back to its charger when its battery runs low.

The resources have none: a robot that ran down in Anki's engine asked for help, driving about (see
BehaviorDriveInDesperation), and the charger was for the user to put it on. This one is PyCozmo's own, over
pycozmo.charger_handling: once the battery has been low for a while, and the robot is off its charger and on its
treads, it goes back and backs onto it, as it can see and remember where it is.

"""

import statistics
import time
from collections import deque
from typing import Any, Deque, Optional, Tuple

from . import cube_behaviors
from . import charger_handling
from . import robot
from .logger import logger


__all__ = [
    "BehaviorGoHome",
]


class BehaviorGoHome(cube_behaviors.BehaviorScript):
    """
    Go back to the charger, when the battery is low.

    The robot's voltage dips by a tenth of a volt or more when its motors are running, and a lone low reading says
    little: the battery is low when the middle of the readings of the last LOW_WINDOW seconds is at or under
    LOW_VOLTAGE. The readings are kept by sample(), which the brain calls as it goes about; they are not kept on the
    charger, whose voltage says nothing of the battery.

    A robot that could not find its way back is left to what it was doing, and tries again after RETRY_DELAY.
    """

    #: The battery is low at this voltage, or under it. A full battery off its charger reads 4.05 to 4.1 V, and the
    #: robot's motors falter at about 3.6.
    LOW_VOLTAGE = 3.75
    #: How many seconds of readings are looked at, and how many are the least that tells anything.
    LOW_WINDOW = 20.0
    MIN_SAMPLES = 10
    #: How often a reading is taken, at most, in seconds.
    SAMPLE_INTERVAL = 1.0
    #: How long after a try that failed the robot tries again, in seconds.
    RETRY_DELAY = 300.0

    def __init__(self, cli: Any, conf: Any = None, robot_needs: Any = None) -> None:
        super().__init__(cli, {"behaviorID": "GoHome"} if conf is None else conf, robot_needs)
        self.samples: Deque[Tuple[float, float]] = deque()
        self.last_attempt: Optional[float] = None
        self.last_success = False

    def sample(self, now: Optional[float] = None) -> None:
        """ Take a reading of the battery, if it is time for one. """
        now = time.perf_counter() if now is None else now
        if self.cli.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER:
            self.samples.clear()
            return
        if self.samples and now - self.samples[-1][0] < self.SAMPLE_INTERVAL:
            return
        voltage = self.cli.battery_voltage
        if voltage <= 0.0:
            # Nothing has been heard from the robot yet.
            return
        self.samples.append((now, voltage))
        while self.samples and now - self.samples[0][0] > self.LOW_WINDOW:
            self.samples.popleft()

    def battery_voltage(self) -> Optional[float]:
        """ The middle of the last readings, in V; None if there are too few to tell. """
        if len(self.samples) < self.MIN_SAMPLES:
            return None
        return statistics.median(voltage for _, voltage in self.samples)

    def battery_is_low(self) -> bool:
        voltage = self.battery_voltage()
        return voltage is not None and voltage <= self.LOW_VOLTAGE

    def wants_to_run(self, now: Optional[float] = None) -> bool:
        now = time.perf_counter() if now is None else now
        if self.cli.robot_status & (robot.RobotStatusFlag.IS_ON_CHARGER | robot.RobotStatusFlag.IS_PICKED_UP):
            return False
        if not self.battery_is_low():
            return False
        return self.last_attempt is None or self.last_success or now - self.last_attempt >= self.RETRY_DELAY

    def script(self, cancel: Any) -> None:
        self.last_attempt = time.perf_counter()
        self.last_success = False
        logger.info("Battery at {:.2f} V: going back to the charger.".format(self.battery_voltage() or 0.0))
        try:
            self.last_success = charger_handling.go_to_charger(self.cli, cancel)
        finally:
            # The brain keeps the camera on, for what it sees; going to the charger turns it off when it is done.
            if not cancel.is_set():
                self.cli.enable_camera(True, color=False)
        if self.last_success:
            self.samples.clear()
            logger.info("Back on the charger.")
        else:
            logger.warning("Could not get back to the charger. Trying again in {:.0f} s.".format(self.RETRY_DELAY))
