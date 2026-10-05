"""

The charger, as far as the robot knows where it is.

The robot is told nothing about its charger. It starts on it, and from then on it has to remember where that was,
and see it - its marker, see pycozmo.charger_detection - to be sure. The marker is where the robot's own frame is
anchored to the charger: a robot that has driven off its charger has its charger behind it, where it left it, until
it is picked up, which starts its position again at zero in a new frame. Seen from a few cm away, the marker
places the charger to some 5 mm, but not which way it faces, to better than 10 or 15 degrees; so the views are put
together, in the world frame, nearer ones counting for more, and the charger stays where the views say it is.

"""

import math
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from . import event


__all__ = [
    "DOCKED_DISTANCE",
    "ChargerPose",
    "Charger",
]


#: How far the robot's origin is from the marker's plane, along the way the marker faces, with the robot on the charger
#: and its back to the charger, in mm: measured by the robot's driving back onto the charger from the distance a marker
#: was seen at, and the robot's own count of how far it went.
DOCKED_DISTANCE = 10.0
#: How many views are kept, and how far, in mm, a view may be from the middle of the others and still count.
MAX_VIEWS = 16
OUTLIER_DISTANCE = 30.0


@dataclass(frozen=True)
class ChargerPose:
    """ Where the charger is, in the robot's world frame. """

    #: The centre of the marker, in mm.
    x: float
    y: float
    #: The heading the marker faces, in radians: the way the robot drives away from the charger, and the way round it
    #: stands on it.
    angle: float
    #: The frame the position is in: see Pose.origin_id. A pose in another frame says nothing of where things are now.
    origin_id: int
    #: When it was last seen or stood on, by time.perf_counter().
    time: float
    #: How many views it is made of; 0 for a charger the robot was on and has not seen.
    views: int = 0


@dataclass(frozen=True)
class _View:
    x: float
    y: float
    angle: float
    weight: float
    origin_id: int
    time: float


class Charger:
    """ The robot's charger: where it was seen, and where the robot was when it stood on it. """

    def __init__(self, cli: Any) -> None:
        self.cli = cli
        self.lock = threading.RLock()
        self._pose: Optional[ChargerPose] = None
        self._views: List[_View] = []

    @property
    def pose(self) -> Optional[ChargerPose]:
        """ Where the charger is, if the robot knows, in its frame now. """
        with self.lock:
            pose = self._pose
        if pose is None or pose.origin_id != self.cli.pose.origin_id:
            return None
        return pose

    @property
    def known(self) -> bool:
        return self.pose is not None

    def forget(self) -> None:
        with self.lock:
            self._pose = None
            self._views = []

    def docked(self) -> ChargerPose:
        """
        Take the robot to be on its charger, its back to it: the charger is where it is, behind it. Whatever the robot
        thought of where its charger was it takes back, for it is standing on it.
        """
        pose = self.cli.pose
        heading = pose.rotation.angle_z.radians
        with self.lock:
            self._views = []
            self._pose = ChargerPose(x=pose.position.x - DOCKED_DISTANCE * math.cos(heading),
                                     y=pose.position.y - DOCKED_DISTANCE * math.sin(heading),
                                     angle=heading, origin_id=pose.origin_id, time=time.perf_counter(), views=0)
            result = self._pose
        return result

    def observe(self, position: Sequence[float], normal: Sequence[float], distance: float,
                now: Optional[float] = None) -> ChargerPose:
        """
        Put a view of the marker with what the robot has seen: its centre, in mm, and the way it faces, a unit vector,
        both in the robot's frame, and how far it was from the camera.
        """
        now = time.perf_counter() if now is None else now
        pose = self.cli.pose
        heading = pose.rotation.angle_z.radians
        c, s = math.cos(heading), math.sin(heading)
        facing = heading + math.atan2(normal[1], normal[0])
        view = _View(x=pose.position.x + c * position[0] - s * position[1],
                     y=pose.position.y + s * position[0] + c * position[1],
                     angle=facing, weight=1.0 / max(distance, 50.0) ** 2, origin_id=pose.origin_id, time=now)
        with self.lock:
            views = [v for v in self._views if v.origin_id == view.origin_id] + [view]
            self._views = views[-MAX_VIEWS:]
            self._pose = self._combine(self._views)
            result = self._pose
        self.cli.dispatch(event.EvtChargerObserved, self.cli, result)
        return result

    @staticmethod
    def _combine(views: List[_View]) -> ChargerPose:
        """ Where the views put the charger: their mean, the nearer weighing more, without any far from the rest. """
        middle = (statistics.median(v.x for v in views), statistics.median(v.y for v in views))
        kept = [v for v in views if math.hypot(v.x - middle[0], v.y - middle[1]) <= OUTLIER_DISTANCE] or views
        total = sum(v.weight for v in kept)
        x = sum(v.weight * v.x for v in kept) / total
        y = sum(v.weight * v.y for v in kept) / total
        angle = math.atan2(sum(v.weight * math.sin(v.angle) for v in kept),
                           sum(v.weight * math.cos(v.angle) for v in kept))
        last = views[-1]
        return ChargerPose(x=x, y=y, angle=angle, origin_id=last.origin_id, time=last.time, views=len(kept))
