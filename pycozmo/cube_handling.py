"""

Handling the Light Cubes: going to one, docking with it, picking it up and putting it down.

The robot has no notion of a cube. It drives and moves its lift, and a cube that sits in the lift's fork goes up
with it. Anki's engine did the rest - seeing the cube, working out where to stand, getting there - and so does
this module, for the brain's behaviors and for applications.

Where a cube's centre is when the lift holds it comes from the robot's 3D model, not from a measurement: the
fork reaches 58 mm ahead of the robot's origin with the lift down. The cozmo-emu emulator takes the same, so
the whole of it can be tried without a robot, but it has to be checked on one.

Everything here blocks until done and waits for the robot's reports, so it must not run on the thread that
dispatches the client's events: a behavior's own thread will do. Each step can be cut short with a cancel event.

"""

import math
import threading
import time
from typing import Any, List, Optional

from . import camera
from . import event
from . import marker_detection
from . import robot
from . import util
from .cubes import CUBE_SIDE, LightCube


__all__ = [
    "DOCK_DISTANCE",
    "PREDOCK_GAP",
    "LOOK_HEAD_ANGLE",
    "CARRY_HEIGHT",

    "Cancelled",

    "observe",
    "look_for_cube",
    "dock_pose",
    "go_to_cube",
    "dock_with_cube",
    "pick_up_cube",
    "put_down_cube",
]


#: Where the centre of a cube in the lift's fork is, ahead of the robot's origin, in mm: the fork reaches 58 mm
#: ahead, by the robot's 3D model, and the cube's centre is half a side beyond. Not measured on a robot.
DOCK_DISTANCE = 58.0 + CUBE_SIDE / 2
#: How far short of docking the robot stops to have a last look at the cube, in mm.
PREDOCK_GAP = 60.0
#: The head's angle for looking at a cube on the ground nearby, in radians: the whole marker is in sight from
#: the lift's fork to 450 mm.
LOOK_HEAD_ANGLE = math.radians(-8.0)
#: The lift's height for carrying a cube, in mm.
CARRY_HEIGHT = robot.MAX_LIFT_HEIGHT.mm
#: How long the head and the lift take to get where they are sent, at most, in seconds.
SETTLE_TIME = 1.0


class Cancelled(Exception):
    """ Raised by the steps below when their cancel event is set. """


def _check(cancel: Optional[threading.Event]) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _pause(seconds: float, cancel: Optional[threading.Event]) -> None:
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise Cancelled()


def observe(cli: Any, timeout: float = 1.0) -> List[LightCube]:
    """
    Look for cube markers in the next camera image, place the cubes seen in Client.cubes, and say which they
    were. The camera has to be on.
    """
    images: List[Any] = []
    got = threading.Event()

    def on_image(_: Any, image: Any) -> None:
        images.append(image)
        got.set()

    handler = cli.add_handler(event.EvtNewRawCameraImage, on_image, one_shot=True)
    try:
        if not got.wait(timeout):
            return []
    finally:
        cli.del_handler(event.EvtNewRawCameraImage, handler)
    calibration = cli.camera_calibration or camera.DEFAULT_CALIBRATION
    seen = []
    for marker in marker_detection.observe_markers(images[0], calibration, cli.head_angle.radians,
                                                   cli.pose_pitch.radians):
        if marker.cube is not None:
            cube = cli.cubes.observe(marker.cube, marker.position, marker.normal)
            if cube is not None:
                seen.append(cube)
    return seen


def look_for_cube(cli: Any, cube: LightCube, timeout: float = 3.0,
                  cancel: Optional[threading.Event] = None) -> bool:
    """ Point the head at the ground nearby, and say whether the cube is seen there before the timeout. """
    cli.enable_camera(True, color=False)
    cli.set_head_angle(LOOK_HEAD_ANGLE)
    _pause(SETTLE_TIME, cancel)
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        _check(cancel)
        if cube in observe(cli):
            return True
    return False


def dock_pose(cube: LightCube, distance: float) -> util.Pose:
    """
    Where the robot's origin stands to face the side of the cube it last saw, squarely, with the cube's centre
    that far ahead.
    """
    if cube.pose is None:
        raise ValueError("Cube {} has not been seen.".format(cube.object_type.name))
    angle = cube.pose.angle
    return util.Pose(cube.pose.x + distance * math.cos(angle), cube.pose.y + distance * math.sin(angle), 0.0,
                     angle_z=util.Angle(radians=angle + math.pi))


def go_to_cube(cli: Any, cube: LightCube, gap: float = PREDOCK_GAP,
               cancel: Optional[threading.Event] = None) -> bool:
    """ Drive to face the side of the cube last seen, that far short of docking, and say whether it got there. """
    _check(cancel)
    if cube.pose is None:
        return False
    return bool(cli.go_to_pose(dock_pose(cube, DOCK_DISTANCE + gap)))


def dock_with_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    From in front of a cube, look at it once more and drive up until it sits in the lift's fork, and say
    whether it got there. The last few centimetres are driven blind: the marker is too close to be seen whole.
    """
    if not look_for_cube(cli, cube, cancel=cancel):
        return False
    target = dock_pose(cube, DOCK_DISTANCE)
    x, y = cli.pose.position.x, cli.pose.position.y
    dx, dy = target.position.x - x, target.position.y - y
    distance = math.hypot(dx, dy)
    if distance > 1.0:
        # Face the spot, drive to it, and face the cube.
        _check(cancel)
        if not cli.turn_in_place(util.Angle(radians=_wrap(math.atan2(dy, dx) - cli.pose.rotation.angle_z.radians))):
            return False
        _check(cancel)
        if not cli.drive_straight(util.Distance(mm=distance), speed=robot.DOCK_SPEED):
            return False
    _check(cancel)
    return bool(cli.turn_in_place(util.Angle(
        radians=_wrap(target.rotation.angle_z.radians - cli.pose.rotation.angle_z.radians))))


def pick_up_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    Go to a cube, dock with it and lift it, and say whether the cube came up. A connected cube tells: it reports
    moving as the lift rises.
    """
    cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
    if not (go_to_cube(cli, cube, cancel=cancel) and dock_with_cube(cli, cube, cancel=cancel)):
        return False
    moved = threading.Event()

    def on_moving(_: Any, moving_cube: LightCube, moving: bool) -> None:
        if moving_cube is cube and moving:
            moved.set()

    handler = cli.add_handler(event.EvtCubeMovingChange, on_moving)
    try:
        cli.set_lift_height(CARRY_HEIGHT)
        _pause(SETTLE_TIME, cancel)
    finally:
        cli.del_handler(event.EvtCubeMovingChange, handler)
    return moved.is_set() or bool(cli.robot_status & robot.RobotStatusFlag.IS_CARRYING_BLOCK)


def put_down_cube(cli: Any, cube: Optional[LightCube] = None, cancel: Optional[threading.Event] = None) -> bool:
    """
    Lower the lift, set down the cube it carries, and back off. The cube is then taken to be where the fork put
    it until it is seen again.
    """
    cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
    _pause(SETTLE_TIME, cancel)
    if cube is not None:
        heading = cli.pose.rotation.angle_z.radians
        cli.cubes.place(cube, cli.pose.position.x + DOCK_DISTANCE * math.cos(heading),
                        cli.pose.position.y + DOCK_DISTANCE * math.sin(heading), heading + math.pi)
    return bool(cli.drive_straight(util.Distance(mm=-(PREDOCK_GAP / 2))))


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
