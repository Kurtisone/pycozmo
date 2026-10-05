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
from typing import Any, List, Optional, Sequence, Tuple

from . import event


__all__ = [
    "AXIS_OFFSET",
    "DOCKED_DISTANCE",
    "ChargerPose",
    "Charger",
]


#: How far the charger's axis - the line the robot's origin follows, backing on - is to the left of its marker's centre,
#: looking the way the marker faces, in mm. A robot that stood 24 mm to its right docked; five that stood 4 to 24 mm
#: to its other side, by the marker, did not, and the user saw them go off to the right of the charger, the right of a
#: Cozmo on it. The marker is not in the middle of the charger.
AXIS_OFFSET = 24.0
#: How far the robot's origin is from the marker's plane, along the way the marker faces, with the robot on the charger
#: and its back to the charger, in mm: measured by the robot's driving back onto the charger from the distance a marker
#: was seen at, and the robot's own count of how far it went.
DOCKED_DISTANCE = 10.0
#: How many views are kept, and how far, in mm, a view may be from the middle of the others and still count.
MAX_VIEWS = 16
OUTLIER_DISTANCE = 30.0
#: How much less a view counts for each view that came after it, in where the charger is.
RECENCY = 0.8
#: How much a view of the marker seen squarely counts for the heading, against one seen from the side, which counts 1.
SQUARE_VIEW_WEIGHT = 0.1


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

    def on_axis(self, distance: float) -> Tuple[float, float]:
        """ The point of the charger's axis that far in front of the marker's plane, in the world frame. """
        c, s = math.cos(self.angle), math.sin(self.angle)
        return (self.x + distance * c - AXIS_OFFSET * s, self.y + distance * s + AXIS_OFFSET * c)

    def in_its_frame(self, x: float, y: float) -> Tuple[float, float]:
        """ A point of the world frame in the charger's: in front of the marker's plane, left of its axis. """
        dx, dy = x - self.x, y - self.y
        c, s = math.cos(self.angle), math.sin(self.angle)
        return dx * c + dy * s, -dx * s + dy * c - AXIS_OFFSET


@dataclass(frozen=True)
class _View:
    x: float
    y: float
    angle: float
    weight: float
    #: How much the view says of the heading: seen squarely, the marker is as wide to the left as to the right, and its
    #: heading is told to 15 degrees; seen from a way round, it is told to a few.
    angle_weight: float
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
            c, s = math.cos(heading), math.sin(heading)
            self._pose = ChargerPose(x=pose.position.x - DOCKED_DISTANCE * c + AXIS_OFFSET * s,
                                     y=pose.position.y - DOCKED_DISTANCE * s - AXIS_OFFSET * c,
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
        # The angle between the way the marker faces and the way it is seen from.
        length = math.hypot(position[0], position[1]) * math.hypot(normal[0], normal[1])
        cosine = -(position[0] * normal[0] + position[1] * normal[1]) / length if length > 0.0 else 1.0
        oblique = 1.0 - min(1.0, max(-1.0, cosine)) ** 2
        weight = 1.0 / max(distance, 50.0) ** 2
        view = _View(x=pose.position.x + c * position[0] - s * position[1],
                     y=pose.position.y + s * position[0] + c * position[1],
                     angle=facing, weight=weight, angle_weight=weight * (SQUARE_VIEW_WEIGHT + oblique),
                     origin_id=pose.origin_id, time=now)
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
        # Where the robot was, when it saw, is known by its wheels to some 20 mm from one place to the next: the views
        # of the place it is at count for more, each newer view making an older one worth RECENCY less.
        weights = [v.weight * RECENCY ** (len(views) - 1 - views.index(v)) for v in kept]
        total = sum(weights)
        x = sum(w * v.x for w, v in zip(weights, kept)) / total
        y = sum(w * v.y for w, v in zip(weights, kept)) / total
        angle = math.atan2(sum(v.angle_weight * math.sin(v.angle) for v in kept),
                           sum(v.angle_weight * math.cos(v.angle) for v in kept))
        last = views[-1]
        return ChargerPose(x=x, y=y, angle=angle, origin_id=last.origin_id, time=last.time, views=len(kept))
