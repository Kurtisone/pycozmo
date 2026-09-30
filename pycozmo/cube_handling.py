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
from typing import Any, Callable, List, Optional

from . import camera
from . import event
from . import marker_detection
from . import robot
from . import util
from .cubes import CUBE_SIDE, LightCube, in_use


__all__ = [
    "DOCK_DISTANCE",
    "PREDOCK_GAP",
    "LOOK_HEAD_ANGLE",
    "CARRY_HEIGHT",

    "Cancelled",

    "observe",
    "look_for_cube",
    "find_cube",
    "dock_pose",
    "go_to_cube",
    "dock_with_cube",
    "pick_up_cube",
    "put_down_cube",
    "put_down_by",
    "place_on_cube",
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
#: How far the robot turns at a time looking round for a cube, in radians: a little less than the camera's
#: 57 degrees.
SEARCH_STEP = math.radians(45.0)
#: How close to its lowest the lift is taken to have set a cube down, in mm.
LIFT_DOWN_MARGIN = 5.0
#: How far the robot turns, at most, pushing a cube it has set down, in radians.
PUSH_TURN_TOLERANCE = math.radians(10.0)


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


def find_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    Turn to where the cube was last seen and look for it there, or look round for it, and say whether it was
    found. The robot ends up facing it.
    """
    if cube.pose is not None:
        heading = math.atan2(cube.pose.y - cli.pose.position.y, cube.pose.x - cli.pose.position.x)
        _check(cancel)
        cli.turn_in_place(util.Angle(radians=_wrap(heading - cli.pose.rotation.angle_z.radians)))
        if look_for_cube(cli, cube, timeout=1.0, cancel=cancel):
            return True
    for _ in range(round(2 * math.pi / SEARCH_STEP)):
        _check(cancel)
        cli.turn_in_place(util.Angle(radians=SEARCH_STEP))
        if look_for_cube(cli, cube, timeout=1.0, cancel=cancel):
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
    with in_use(cube):
        return bool(cli.go_to_pose(dock_pose(cube, DOCK_DISTANCE + gap)))


def dock_with_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    From in front of a cube, look at it once more and drive up until it sits in the lift's fork, and say
    whether it got there. The last few centimetres are driven blind: the marker is too close to be seen whole.
    """
    with in_use(cube):
        return _dock_with_cube(cli, cube, cancel)


def _dock_with_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event]) -> bool:
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
    with in_use(cube):
        return _pick_up_cube(cli, cube, cancel)


def _pick_up_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event]) -> bool:
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
    if moved.is_set() or cli.robot_status & robot.RobotStatusFlag.IS_CARRYING_BLOCK:
        cli.cubes.carried = cube
        return True
    return False


def put_down_cube(cli: Any, cube: Optional[LightCube] = None, cancel: Optional[threading.Event] = None) -> bool:
    """
    Lower the lift, set down the cube it carries, and back off. The cube is then taken to be where the fork put
    it until it is seen again.
    """
    cube = cube or cli.cubes.carried
    with in_use(*([cube] if cube is not None else [])):
        cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
        _pause(SETTLE_TIME, cancel)
        if cube is not None:
            _place_ahead(cli, cube, cli.pose)
        cli.cubes.carried = None
        return bool(cli.drive_straight(util.Distance(mm=-(PREDOCK_GAP / 2))))


def put_down_by(cli: Any, action: Callable[[], Any], cube: Optional[LightCube] = None) -> bool:
    """
    Have something else set down the cube in the lift - an animation, usually, which drives about after - and
    take the cube to be where the lift came down, pushed along by the fork for as long as the robot drove on
    straight with it down. Say whether it did come down.
    """
    cube = cube or cli.cubes.carried
    # Where the robot stood when the lift came down, then further along as long as it pushed the cube.
    lowered: List[util.Pose] = []
    left = threading.Event()

    def on_state(_: Any) -> None:
        down = cli.lift_position.height.mm <= robot.MIN_LIFT_HEIGHT.mm + LIFT_DOWN_MARGIN
        if not lowered:
            if down:
                lowered.append(cli.pose)
            return
        if left.is_set():
            return
        heading = lowered[0].rotation.angle_z.radians
        ahead = _ahead(cli.pose, lowered[0])
        if not down or ahead < _ahead(lowered[-1], lowered[0]) - 1.0 or \
                abs(_wrap(cli.pose.rotation.angle_z.radians - heading)) > PUSH_TURN_TOLERANCE:
            left.set()
        elif ahead > _ahead(lowered[-1], lowered[0]):
            lowered.append(cli.pose)

    handler = cli.add_handler(event.EvtRobotStateUpdated, on_state)
    try:
        with in_use(*([cube] if cube is not None else [])):
            action()
    finally:
        cli.del_handler(event.EvtRobotStateUpdated, handler)
    if not lowered:
        return False
    if cube is not None:
        _place_ahead(cli, cube, util.Pose(lowered[-1].position.x, lowered[-1].position.y, 0.0,
                                          angle_z=lowered[0].rotation.angle_z))
    cli.cubes.carried = None
    return True


def _ahead(pose: util.Pose, start: util.Pose) -> float:
    """ How far a pose is ahead of another, along the other's heading. """
    heading = start.rotation.angle_z.radians
    return ((pose.position.x - start.position.x) * math.cos(heading) +
            (pose.position.y - start.position.y) * math.sin(heading))


def _place_ahead(cli: Any, cube: LightCube, pose: util.Pose) -> None:
    """ Take a cube to be in the lift's fork of the robot standing there, on the ground. """
    heading = pose.rotation.angle_z.radians
    cli.cubes.place(cube, pose.position.x + DOCK_DISTANCE * math.cos(heading),
                    pose.position.y + DOCK_DISTANCE * math.sin(heading), heading + math.pi)


def place_on_cube(cli: Any, target: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    Carrying a cube, set it on top of another, and back off: find the other, dock with it the lift up, the
    carried cube over it, and lower the lift, which leaves the cube on top. Say whether it got to lowering it.
    """
    carried = cli.cubes.carried
    if not find_cube(cli, target, cancel=cancel):
        return False
    with in_use(*([target] if carried is None else [target, carried])):
        if not (go_to_cube(cli, target, cancel=cancel) and dock_with_cube(cli, target, cancel=cancel)):
            return False
        cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
        _pause(SETTLE_TIME, cancel)
        if carried is not None and target.pose is not None:
            cli.cubes.place(carried, target.pose.x, target.pose.y, target.pose.angle, z=target.pose.z + CUBE_SIDE)
        cli.cubes.carried = None
        return bool(cli.drive_straight(util.Distance(mm=-PREDOCK_GAP)))


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
