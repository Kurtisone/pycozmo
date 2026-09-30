"""

Handling the Light Cubes: going to one, docking with it, picking it up, putting it down, setting it on another,
rolling it, and popping a wheelie against it.

The robot has no notion of a cube. It drives and moves its lift, and a cube that sits in the lift's fork goes up
with it. Anki's engine did the rest - seeing the cube, working out where to stand, getting there - and so does
this module, for the brain's behaviors and for applications.

What it does is what Anki's engine was seen doing, through the official SDK, on a robot: it goes to stand some
15 cm from the cube and has a look, then docks with its head down, looking at the marker again on the way, and
from there makes the manoeuvre's own moves. Picking a cube up, it lifts while creeping on: the fork slides under
as it rises. Setting one on another, it lets go at 76 mm, not with the lift all the way down. Rolling one, it
hooks the top edge with the fork at 74 mm, and lowers the lift backing off: the cube tips over towards it. Popping
a wheelie, it brings the lift down hard on the cube driving on at 150 mm/s, and ends up on its back. The distances
are Anki's, 2.5 mm longer: PyCozmo places a cube that much further than Anki's engine did on the same images.

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
    "PICKUP_DISTANCE",
    "PLACE_ON_DISTANCE",
    "ROLL_DISTANCE",
    "WHEELIE_DISTANCE",
    "PREDOCK_GAP",
    "LOOK_HEAD_ANGLE",
    "DOCK_HEAD_ANGLE",
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
    "roll_cube",
    "pop_a_wheelie",
]


#: Where the centre of a cube in the lift's fork is, ahead of the robot's origin, in mm: where it was set down,
#: measured on a robot after Anki's engine picked it up.
DOCK_DISTANCE = 52.5
#: Where the robot stops, the cube's centre that far ahead, before it lifts; and how far it creeps on while it does,
#: and how fast. Anki's engine stopped at 44.4 mm, by its own reckoning, and crept on 7 mm.
PICKUP_DISTANCE = 47.0
PICKUP_CREEP = 8.0
CREEP_SPEED = 15.0
#: How far the robot backs off from a cube it has set down, in mm, as Anki's engine did.
PUT_DOWN_BACKOFF = 30.0
#: Where the robot stops to set the cube it carries on another, the other's centre that far ahead, and how far it
#: lowers the lift to let go of it, and backs off before lowering it all the way. Anki's engine stopped at 36.1 mm.
PLACE_ON_DISTANCE = 38.5
PLACE_ON_LIFT_HEIGHT = 76.0
PLACE_ON_BACKOFF = 55.0
#: Rolling a cube: where the robot stops, the lift up; the lift's height hooking the cube's top edge; and how fast
#: and how long it backs off lowering the lift. Anki's engine stopped at 31 mm.
ROLL_DISTANCE = 34.0
ROLL_HOOK_HEIGHT = 74.0
ROLL_PULL_SPEED = 55.0
ROLL_PULL_TIME = 1.0
#: Popping a wheelie: where the robot stops, the lift up; and how fast and how long it drives on, bringing the lift
#: down. Anki's engine stopped at 30 mm, and drove on at 150 mm/s; the robot was on its back, at 74 degrees, a
#: third of a second later.
WHEELIE_DISTANCE = 32.5
WHEELIE_SPEED = 150.0
WHEELIE_TIME = 0.35
#: How far beyond where it docks the robot stands to have a look at the cube, in mm: some 15 cm from it.
PREDOCK_GAP = 100.0
#: Where the robot stops on the way in to have another look, beyond where it docks, in mm.
DOCK_STAGE_GAP = 35.0
#: The head's angle for looking at a cube on the ground nearby, in radians: the whole marker is in sight from
#: the lift's fork to 450 mm.
LOOK_HEAD_ANGLE = math.radians(-8.0)
#: The head's angle docking, as Anki's engine had it: the marker stays in sight down to 40 mm.
DOCK_HEAD_ANGLE = math.radians(-17.0)
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
                  cancel: Optional[threading.Event] = None, head_angle: float = LOOK_HEAD_ANGLE) -> bool:
    """ Point the head at the ground nearby, and say whether the cube is seen there before the timeout. """
    cli.enable_camera(True, color=False)
    cli.set_head_angle(head_angle)
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


def dock_with_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None,
                   distance: float = PICKUP_DISTANCE) -> bool:
    """
    From in front of a cube, look at it once more, and drive up until its centre is that far ahead, facing it
    squarely; say whether it got there. The head goes down, and the robot stops on the way to look again, as Anki's
    engine kept the marker in sight to the end.
    """
    with in_use(cube):
        if not look_for_cube(cli, cube, cancel=cancel):
            return False
        cli.set_head_angle(DOCK_HEAD_ANGLE)
        if not _drive_up(cli, cube, distance + DOCK_STAGE_GAP, cancel):
            return False
        # Closer, the cube is placed better. Not seen, the first look will do.
        look_for_cube(cli, cube, timeout=1.0, cancel=cancel, head_angle=DOCK_HEAD_ANGLE)
        return _drive_up(cli, cube, distance, cancel)


def _drive_up(cli: Any, cube: LightCube, distance: float, cancel: Optional[threading.Event]) -> bool:
    """ Drive to where the cube's centre is that far ahead, facing it squarely. """
    target = dock_pose(cube, distance)
    x, y = cli.pose.position.x, cli.pose.position.y
    dx, dy = target.position.x - x, target.position.y - y
    remaining = math.hypot(dx, dy)
    if remaining > 1.0:
        # Face the spot, drive to it, and face the cube. Backwards, if the spot is behind.
        heading = math.atan2(dy, dx)
        backwards = abs(_wrap(heading - cli.pose.rotation.angle_z.radians)) > math.pi / 2
        if backwards:
            heading += math.pi
        _check(cancel)
        if not cli.turn_in_place(util.Angle(radians=_wrap(heading - cli.pose.rotation.angle_z.radians))):
            return False
        _check(cancel)
        if not cli.drive_straight(util.Distance(mm=-remaining if backwards else remaining), speed=robot.DOCK_SPEED):
            return False
    _check(cancel)
    return bool(cli.turn_in_place(util.Angle(
        radians=_wrap(target.rotation.angle_z.radians - cli.pose.rotation.angle_z.radians))))


def _creep(cli: Any, speed: float, seconds: float, cancel: Optional[threading.Event]) -> None:
    """ Drive straight at a speed for a while, the lift and the head left to what they are doing. """
    cli.drive_wheels(speed, speed)
    try:
        _pause(seconds, cancel)
    finally:
        cli.drive_wheels(0.0, 0.0)


def pick_up_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    Go to a cube, dock with it and lift it, and say whether the cube came up.

    A cube that the fork only knocks moves too: what tells is that it is no longer on the ground in front of the
    robot. Anki's engine, the cube up, saw only the ground there.
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
        _creep(cli, CREEP_SPEED, PICKUP_CREEP / CREEP_SPEED, cancel)
        _pause(SETTLE_TIME / 2, cancel)
    finally:
        cli.del_handler(event.EvtCubeMovingChange, handler)
    if _on_the_ground_ahead(cli, cube):
        cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
        return False
    if moved.is_set() or cli.robot_status & robot.RobotStatusFlag.IS_CARRYING_BLOCK:
        cli.cubes.carried = cube
        return True
    return False


def _on_the_ground_ahead(cli: Any, cube: LightCube) -> bool:
    """ Whether the cube is still seen on the ground in front of the robot. """
    for _ in range(3):
        if cube in observe(cli) and cube.pose is not None and cube.pose.z < CUBE_SIDE:
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
        return bool(cli.drive_straight(util.Distance(mm=-PUT_DOWN_BACKOFF)))


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
        if not (go_to_cube(cli, target, cancel=cancel) and
                dock_with_cube(cli, target, cancel=cancel, distance=PLACE_ON_DISTANCE)):
            return False
        # Low enough for the cube to rest on the other, and no lower: the fork lets go of it.
        cli.set_lift_height(PLACE_ON_LIFT_HEIGHT)
        _pause(SETTLE_TIME, cancel)
        if carried is not None and target.pose is not None:
            cli.cubes.place(carried, target.pose.x, target.pose.y, target.pose.angle, z=target.pose.z + CUBE_SIDE)
        cli.cubes.carried = None
        backed = bool(cli.drive_straight(util.Distance(mm=-PLACE_ON_BACKOFF)))
        cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
        return backed


def roll_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    Roll a cube over onto its side towards the robot, and say whether it did - whether the cube says its up axis
    changed. The robot stops at cliffs again afterwards; it cannot while the roll tips it up.
    """
    with in_use(cube):
        axis = cube.up_axis
        cli.set_lift_height(CARRY_HEIGHT)
        if not (go_to_cube(cli, cube, cancel=cancel) and
                dock_with_cube(cli, cube, cancel=cancel, distance=ROLL_DISTANCE)):
            return False
        cli.enable_stop_on_cliff(False)
        try:
            # The fork on the cube's top edge, then down, pulling it over.
            cli.set_lift_height(ROLL_HOOK_HEIGHT)
            _pause(SETTLE_TIME / 2, cancel)
            cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm)
            _creep(cli, -ROLL_PULL_SPEED, ROLL_PULL_TIME, cancel)
            _pause(SETTLE_TIME / 2, cancel)
        finally:
            cli.enable_stop_on_cliff(True)
        # Where it is now has to be seen: on its side, a side's length nearer.
        cube.pose = None
        return cube.up_axis != axis


def pop_a_wheelie(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None) -> bool:
    """
    Pop a wheelie against a cube: dock with it the lift up, and bring the lift down hard driving on, which tips the
    robot onto its back. Say whether it got there. The robot stops at cliffs again afterwards.
    """
    with in_use(cube):
        cli.set_lift_height(CARRY_HEIGHT)
        if not (go_to_cube(cli, cube, cancel=cancel) and
                dock_with_cube(cli, cube, cancel=cancel, distance=WHEELIE_DISTANCE)):
            return False
        cli.enable_stop_on_cliff(False)
        try:
            cli.set_lift_height(robot.MIN_LIFT_HEIGHT.mm, accel=100.0, max_speed=10.0)
            _creep(cli, WHEELIE_SPEED, WHEELIE_TIME, cancel)
            _pause(SETTLE_TIME / 2, cancel)
        finally:
            cli.enable_stop_on_cliff(True)
        return bool(cli.pose_pitch.radians > robot.ON_BACK_PITCH)


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
