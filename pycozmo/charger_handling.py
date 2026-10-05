"""

Going back to the charger: finding it, driving to stand in front of it, turning round, and backing onto it.

Anki's robot is put on its charger by hand: its engine had no way to take it there. This does: the robot looks for the
charger's marker, in the camera's images and where it remembers the charger to be (see pycozmo.charger); drives to
stand some 20 cm in front of it, facing it, and has a look again; turns round, for the charger is backed onto, the
robot's back to it, and drives backwards until its back touches the charger and the robot says it is on it.

The marker is found from 45 cm at most, and tells which way it faces no better than 10 or 15 degrees; the views put
together, from more than one place, tell better, and the backing is guided by the charger itself, which takes a robot
that is some mm and a few degrees off. Which is how far, and how, was found on a robot.

Everything here blocks until done and waits for the robot's reports, so it must not run on the thread that dispatches
the client's events: a behavior's own thread will do. Each step can be cut short with a cancel event.

"""

import math
import threading
import time
from typing import Any, Optional

import numpy as np

from . import camera
from . import charger
from . import charger_detection
from . import event
from . import marker_detection
from . import robot
from . import util
from .cube_handling import Cancelled, SETTLE_TIME, _check, _pause, _wrap


__all__ = [
    "PREDOCK_DISTANCE",
    "LOOK_HEAD_ANGLE",

    "observe",
    "look_for_charger",
    "find_charger",
    "predock_pose",
    "go_to_predock",
    "back_onto_charger",
    "go_to_charger",
]


#: How far from the marker's plane the robot stands to look at it a last time before it turns round, in mm: the marker
#: is 36 px wide there, and well placed.
PREDOCK_DISTANCE = 200.0
#: How near the robot has to be to where it is to stand, in mm, and how nearly it has to face the marker, in radians,
#: for it to turn round and back on.
PREDOCK_TOLERANCE = 12.0
PREDOCK_ANGLE = math.radians(6.0)
#: The head's angle for looking at the charger's marker, which is 25 mm up: seen from 45 cm, and from 15.
LOOK_HEAD_ANGLE = math.radians(-5.0)
#: How many images are looked at, at most, at each stop, and how many of them have to show the marker.
LOOKS = 4
MIN_LOOKS = 2
#: How far the robot turns at a time looking round for the charger, in radians: about half the camera's field.
SEARCH_STEP = math.radians(30.0)
#: How fast the robot backs onto the charger, in mm/s, and how far beyond where it should touch it the robot is sent, in
#: mm: the charger stops it.
BACK_SPEED = 35.0
BACK_MARGIN = 25.0
#: How far from the charger's marker, along the way it faces, and to the side, a robot that is behind it or beside it
#: drives round to, in mm, and how near the charger counts as in the way.
DETOUR_DISTANCE = 160.0
DETOUR_SIDE = 150.0
IN_THE_WAY = 130.0
#: How much further than the gyro says the robot really turns: on a robot, two half turns that the gyro said made 360
#: degrees took the marker's bearing 4.7 degrees (3.4 to 5.7) round, 1.3%. A half turn is asked for that much less.
TURN_SCALE = 1.013
#: A robot that has not gone back by STALL_DISTANCE mm, nor turned by STALL_ANGLE radians, for STALL_TIME seconds, after
#: the first STALL_GRACE seconds in which its wheels get going, is held, against the ramp's edge or the charger: it
#: stops, for its treads going on push the charger, which slides on the floor. A tread held while the other turns is
#: not that: it is the ramp's rails turning the robot in, which they did from 9 degrees out, and it goes on.
STALL_DISTANCE = 4.0
STALL_ANGLE = math.radians(2.0)
STALL_GRACE = 0.8
STALL_TIME = 0.8
#: How far in front of the marker's plane the robot drives out to when backing has not taken, in mm, and how fast.
OUT_DISTANCE = PREDOCK_DISTANCE - 20.0
OUT_SPEED = 50.0
#: How many times the robot tries to get on the charger.
ATTEMPTS = 3


def observe(cli: Any, timeout: float = 1.0) -> Optional[charger_detection.ObservedCharger]:
    """
    Look for the charger's marker in the next camera image, put what is seen with what the robot knows of the charger
    - Client.charger - and say what was seen. The camera has to be on.
    """
    images = []
    got = threading.Event()

    def on_image(_: Any, image: Any) -> None:
        images.append(image)
        got.set()

    handler = cli.add_handler(event.EvtNewRawCameraImage, on_image, one_shot=True)
    try:
        if not got.wait(timeout):
            return None
    finally:
        cli.del_handler(event.EvtNewRawCameraImage, handler)
    calibration = cli.camera_calibration or camera.DEFAULT_CALIBRATION
    head, pitch = cli.head_angle.radians, cli.pose_pitch.radians
    # A cube's side is much like the charger's marker: what the cubes' own detection takes for one is not it.
    cubes = [np.array(marker.corners) for marker in
             marker_detection.observe_markers(images[0], calibration, head, pitch) if marker.cube is not None]
    seen = charger_detection.observe_charger(images[0], calibration, head, pitch, avoid=cubes)
    if seen is not None:
        cli.charger.observe(seen.position, seen.normal, seen.distance)
    return seen


def _use_the_robots_lens(cli: Any) -> None:
    """
    Read the robot's own camera calibration if it has not been: on the robot this was tried on, its focal length and
    optical centre differed from a typical robot's by 4% and 12 px, which at 20 cm puts the marker 8 mm to one side
    and 4% too far.
    """
    if cli.camera_calibration is None:
        cli.read_camera_calibration()


def look_for_charger(cli: Any, timeout: float = 2.0, cancel: Optional[threading.Event] = None,
                     head_angle: float = LOOK_HEAD_ANGLE) -> bool:
    """ Point the head at the marker's height and say whether it is seen before the timeout. """
    _use_the_robots_lens(cli)
    cli.enable_camera(True, color=False)
    cli.set_head_angle(head_angle)
    _pause(SETTLE_TIME, cancel)
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        _check(cancel)
        if observe(cli) is not None:
            return True
    return False


def find_charger(cli: Any, cancel: Optional[threading.Event] = None) -> bool:
    """
    Turn to where the charger is remembered to be, and look there, or look round for it, and say whether it was
    seen. The robot ends up facing it, within a few cm.
    """
    pose = cli.charger.pose
    if pose is not None:
        position = cli.pose.position
        heading = math.atan2(pose.y - position.y, pose.x - position.x)
        _check(cancel)
        cli.turn_in_place(util.Angle(radians=_wrap(heading - cli.pose.rotation.angle_z.radians)))
        if look_for_charger(cli, timeout=1.5, cancel=cancel):
            return True
    for _ in range(round(2 * math.pi / SEARCH_STEP)):
        _check(cancel)
        cli.turn_in_place(util.Angle(radians=SEARCH_STEP))
        if look_for_charger(cli, timeout=1.0, cancel=cancel):
            return True
    return False


def predock_pose(pose: charger.ChargerPose, distance: float = PREDOCK_DISTANCE) -> util.Pose:
    """ Where the robot stands to look at the charger a last time: that far in front of it, on its axis, facing it. """
    x, y = pose.on_axis(distance)
    return util.Pose(x, y, 0.0, angle_z=util.Angle(radians=_wrap(pose.angle + math.pi)))


def _stare(cli: Any, cancel: Optional[threading.Event]) -> int:
    """ Look at the marker several times, standing still, and say how many of the looks showed it. """
    seen = 0
    for _ in range(LOOKS):
        _check(cancel)
        if observe(cli, timeout=1.0) is not None:
            seen += 1
    return seen


def _detour(cli: Any, pose: charger.ChargerPose) -> bool:
    """ A robot that is behind the charger, or beside it, drives round it, not through it. """
    position = cli.pose.position
    along, left = pose.in_its_frame(position.x, position.y)
    if along >= IN_THE_WAY or (along >= 0.0 and abs(left) >= IN_THE_WAY):
        return True
    side = 1.0 if left >= 0.0 else -1.0
    x, y = pose.on_axis(DETOUR_DISTANCE)
    c, s = math.cos(pose.angle), math.sin(pose.angle)
    waypoint = util.Pose(x - side * DETOUR_SIDE * s, y + side * DETOUR_SIDE * c, 0.0,
                         angle_z=util.Angle(radians=_wrap(pose.angle + math.pi)))
    return bool(cli.go_to_pose(waypoint))


def go_to_predock(cli: Any, cancel: Optional[threading.Event] = None) -> bool:
    """
    Drive to where the charger is, to stand in front of it, and look again from there, until the robot is where it
    should be, within PREDOCK_TOLERANCE, and sees the marker. Say whether it got there. The charger has to be known:
    seen, or remembered, and a robot that stands where it should and does not see the marker has remembered wrong.
    """
    _use_the_robots_lens(cli)
    cli.enable_camera(True, color=False)
    cli.set_head_angle(LOOK_HEAD_ANGLE)
    _pause(SETTLE_TIME, cancel)
    for _ in range(6):
        _check(cancel)
        looks = _stare(cli, cancel)
        pose = cli.charger.pose
        if pose is None:
            return False
        target = predock_pose(pose)
        here = cli.pose
        error = math.hypot(target.position.x - here.position.x, target.position.y - here.position.y)
        turn = _wrap(target.rotation.angle_z.radians - here.rotation.angle_z.radians)
        there = error <= PREDOCK_TOLERANCE and abs(turn) <= PREDOCK_ANGLE
        if looks >= MIN_LOOKS and there:
            return True
        if looks < MIN_LOOKS and error <= 3.0 * PREDOCK_TOLERANCE:
            return False
        if not _detour(cli, pose):
            return False
        _check(cancel)
        if not cli.go_to_pose(predock_pose(pose)):
            return False
        # What the cubes' marker tells from a few mm, the charger's does not: the robot settles before it looks.
        _pause(0.5, cancel)
    return False


def back_onto_charger(cli: Any, cancel: Optional[threading.Event] = None) -> bool:
    """
    Turn round and drive backwards onto the charger, from in front of it, and say whether the robot says it is on it. It
    stops as it does.
    """
    pose = cli.charger.pose
    if pose is None:
        return False
    cli.enable_camera(False)
    _check(cancel)
    here = cli.pose
    if not cli.turn_in_place(util.Angle(radians=_wrap(pose.angle - here.rotation.angle_z.radians) / TURN_SCALE)):
        return False
    along, _ = pose.in_its_frame(cli.pose.position.x, cli.pose.position.y)
    travel = max(0.0, along - charger.DOCKED_DISTANCE) + BACK_MARGIN
    cli.enable_stop_on_cliff(False)
    try:
        cli.drive_straight(util.Distance(mm=-travel), speed=BACK_SPEED, wait=False)
        start = time.perf_counter()
        deadline = start + travel / BACK_SPEED * 1.5 + 3.0
        # Where the robot was when it last moved, and when.
        moved_at = start
        moved_from = cli.pose
        while time.perf_counter() < deadline:
            _check(cancel)
            if cli.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER:
                # Not quite stopped: the wheels go on a moment, and the robot settles on the contacts.
                cli.stop_all_motors()
                _pause(0.3, cancel)
                return True
            now = time.perf_counter()
            here = cli.pose
            if _moved(moved_from, here):
                moved_at, moved_from = now, here
            elif now - start > STALL_GRACE and now - moved_at > STALL_TIME:
                break
            time.sleep(0.02)
        cli.stop_all_motors()
        _pause(0.3, cancel)
        return bool(cli.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER)
    finally:
        cli.enable_stop_on_cliff(True)


def _moved(before: util.Pose, after: util.Pose) -> bool:
    """ Whether a robot has gone, or turned, far enough between two poses to be said to be moving. """
    distance = math.hypot(after.position.x - before.position.x, after.position.y - before.position.y)
    turn = abs(_wrap(after.rotation.angle_z.radians - before.rotation.angle_z.radians))
    return distance >= STALL_DISTANCE or turn >= STALL_ANGLE


def go_to_charger(cli: Any, cancel: Optional[threading.Event] = None) -> bool:
    """
    Go back to the charger, from wherever the robot is, and say whether it is on it. It looks for the charger's
    marker, where it remembers the charger to be and then all round, drives to stand in front of it, and backs onto
    it; if that does not take, it tries again.
    """
    if cli.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER:
        return True
    cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
    try:
        for _ in range(ATTEMPTS):
            if cli.charger.pose is None and not find_charger(cli, cancel):
                return False
            if not go_to_predock(cli, cancel):
                # Where it was remembered, it is not: look all round.
                cli.charger.forget()
                continue
            if back_onto_charger(cli, cancel):
                return True
            # It did not take: drive straight out, to well in front of the charger, where the way to the front of it is
            # clear, and look again.
            pose = cli.charger.pose
            if pose is not None:
                along, _ = pose.in_its_frame(cli.pose.position.x, cli.pose.position.y)
                if along < OUT_DISTANCE:
                    cli.drive_straight(util.Distance(mm=OUT_DISTANCE - along), speed=OUT_SPEED)
        return False
    except Cancelled:
        cli.stop_all_motors()
        raise
    finally:
        cli.enable_camera(False)
