"""

Handling the Light Cubes: going to one, docking with it, picking it up, putting it down, setting it on another,
rolling it, and popping a wheelie against it.

The robot has no notion of a cube. It drives and moves its lift, and a cube that sits in the lift's fork goes up
with it. Anki's engine did the rest - seeing the cube, working out where to stand, getting there - and so does
this module, for the brain's behaviors and for applications.

What it does is what Anki's engine was seen doing, through the official SDK, on a robot: it goes to stand some
15 cm from the cube and has a look, then docks with its head down, steering by the marker in every camera image so
as to come in along the line the cube's side makes - an angle off by ten degrees, or a few mm to the side, and the
fork does not take it - and from there makes the manoeuvre's own moves. Picking a cube up, it lifts while creeping
on, over the 0.75 s that Anki's engine took: the fork slides under as it rises. Setting one on another, it lets go
at 76 mm, not with the lift all the way down. Rolling one, it hooks the top edge with the fork at 74 mm, and lowers
the lift backing off: the cube tips over towards it. Popping a wheelie, it brings the lift down hard on the cube
driving on at 150 mm/s, and ends up on its back. The distances are Anki's, 2.5 mm longer: PyCozmo places a cube that
much further than Anki's engine did on the same images.

Everything here blocks until done and waits for the robot's reports, so it must not run on the thread that
dispatches the client's events: a behavior's own thread will do. Each step can be cut short with a cancel event.

"""

import math
import threading
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

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
    "dock_error",
    "servo_wheels",
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
#: and how fast. Anki's engine stopped at 44.4 mm, by its own reckoning, and crept on 7 mm at some 17 mm/s. The creep
#: is a path: the wheels, told 15 mm/s, do not turn, and moved a robot 1 mm in half a second, where a path of 8 mm
#: took it 8.3 to 8.6.
PICKUP_DISTANCE = 47.0
PICKUP_CREEP = 8.0
CREEP_SPEED = 30.0
#: How long the lift takes to rise with the cube, in seconds: Anki's engine took 0.74 s from the lowest to the top,
#: the first 0.2 s slowly. The lift's own speed does it in 0.3 s, and by then it was up without the cube.
PICKUP_LIFT_TIME = 0.75
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
#: The head's angle for looking at a cube on the ground nearby, in radians: the whole marker is in sight from
#: the lift's fork to 450 mm.
LOOK_HEAD_ANGLE = math.radians(-8.0)
#: The head's angle docking, as Anki's engine had it: the marker stays in sight down to 40 mm.
DOCK_HEAD_ANGLE = math.radians(-17.0)
#: The lift's height for carrying a cube, in mm.
CARRY_HEIGHT = robot.MAX_LIFT_HEIGHT.mm
#: Steering in on a cube by its marker, as Anki's engine did, a sight of it at a time: how fast the robot drives, at
#: the most and the least - its wheels do not turn below some 20 mm/s - and how it slows, in mm/s and per mm to go; how
#: much it steers by the way it faces, in 1/s, and by its distance from the line the cube's side makes, as the speed
#: that distance takes to be made up in; how long it goes on without seeing the marker, and how long in all, in
#: seconds; and how far off the line, in mm, and how far turned from it, in radians, it may be at the end by the last
#: sight - which is off by some 4 mm and 3 degrees: to catch a robot that is badly off, not to place it.
SERVO_SPEED = 45.0
SERVO_MIN_SPEED = 28.0
SERVO_SLOWING = 0.5
SERVO_TURN_GAIN = 2.5
SERVO_LINE_SPEED = 40.0
SERVO_LOST_TIME = 0.5
SERVO_TIME = 12.0
SERVO_LATERAL_TOLERANCE = 10.0
SERVO_ANGLE_TOLERANCE = math.radians(10.0)
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


def dock_pose(cube: LightCube, distance: float, side: int = 0) -> util.Pose:
    """
    Where the robot's origin stands to face the side of the cube it last saw, squarely, with the cube's centre
    that far ahead. Or another side: so many quarter turns round the cube from that one, anticlockwise seen from
    above.
    """
    if cube.pose is None:
        raise ValueError("Cube {} has not been seen.".format(cube.object_type.name))
    angle = cube.pose.angle + side * math.pi / 2
    return util.Pose(cube.pose.x + distance * math.cos(angle), cube.pose.y + distance * math.sin(angle), 0.0,
                     angle_z=util.Angle(radians=angle + math.pi))


def go_to_cube(cli: Any, cube: LightCube, gap: float = PREDOCK_GAP,
               cancel: Optional[threading.Event] = None, side: int = 0) -> bool:
    """
    Drive to face the side of the cube last seen, or another: see dock_pose(); that far short of docking. Say
    whether it got there.
    """
    _check(cancel)
    if cube.pose is None:
        return False
    with in_use(cube):
        return bool(cli.go_to_pose(dock_pose(cube, DOCK_DISTANCE + gap, side)))


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
        # Squarely facing the cube's side, which the turn and the drive by where the cube was last seen - a few mm off,
        # and an angle off by ten degrees at 25 cm - do not come to: the fork does not take the cube if it is not.
        return _servo_to_cube(cli, cube, distance, cancel)


def dock_error(position: Sequence[float], normal: Sequence[float]) -> Tuple[float, float, float]:
    """
    Where a robot is to the line a cube's side makes, from the marker it sees, as it is in the robot's frame: its
    centre and the way it faces, a unit vector out of the cube. The distance along the line from the cube's centre
    to the robot's origin, how far to the left of the line - looking at the cube - the origin is, and how far the
    robot has to turn, to the left, to face along the line.
    """
    n = math.hypot(normal[0], normal[1])
    out_x, out_y = normal[0] / n, normal[1] / n
    centre_x, centre_y = position[0] - CUBE_SIDE / 2 * out_x, position[1] - CUBE_SIDE / 2 * out_y
    along = -(centre_x * out_x + centre_y * out_y)
    # Looking into the cube, along -out; the origin is at -centre from its centre.
    left = out_x * centre_y - out_y * centre_x
    turn = _wrap(math.atan2(-out_y, -out_x))
    return along, left, turn


def servo_wheels(along: float, left: float, turn: float, distance: float) -> Tuple[float, float]:
    """
    How fast to turn the wheels, left and right, in mm/s, steering in on a cube: to face along the line its side makes,
    and onto it, as a car would, by the angle that the distance off the line takes to make up at some speed.
    """
    speed = min(SERVO_SPEED, max(SERVO_MIN_SPEED, SERVO_MIN_SPEED + SERVO_SLOWING * (along - distance)))
    steer = turn - math.atan2(left, SERVO_LINE_SPEED)
    # Both wheels have to turn forwards, however far off the robot is: it is not to turn on the spot.
    limit = 2.0 * (speed - SERVO_MIN_SPEED + 4.0) / robot.TRACK_WIDTH.mm
    omega = max(-limit, min(limit, SERVO_TURN_GAIN * steer))
    return speed - omega * robot.TRACK_WIDTH.mm / 2, speed + omega * robot.TRACK_WIDTH.mm / 2


class _Eyes:
    """ The camera's newest image, and the wait for a newer one. """

    def __init__(self, cli: Any) -> None:
        self.cli = cli
        self.condition = threading.Condition()
        self.image: Any = None
        self.count = 0
        self.handler = cli.add_handler(event.EvtNewRawCameraImage, self._on_image)

    def _on_image(self, _: Any, image: Any) -> None:
        with self.condition:
            self.image = image
            self.count += 1
            self.condition.notify_all()

    def after(self, count: int, timeout: float) -> Tuple[Any, int]:
        """ The image after the count-th, or None, and its number. """
        with self.condition:
            self.condition.wait_for(lambda: self.count > count, timeout)
            return (self.image, self.count) if self.count > count else (None, count)

    def close(self) -> None:
        self.cli.del_handler(event.EvtNewRawCameraImage, self.handler)


def _marker_of(cli: Any, cube: LightCube, image: Any) -> Optional[marker_detection.ObservedMarker]:
    """ The marker of a cube in an image, the nearest if there are several: the robot's own head and pitch are used. """
    calibration = cli.camera_calibration or camera.DEFAULT_CALIBRATION
    markers = marker_detection.observe_markers(image, calibration, cli.head_angle.radians, cli.pose_pitch.radians)
    found = [marker for marker in markers if marker.cube == cube.object_type]
    return min(found, key=lambda marker: marker.distance) if found else None


def _servo_to_cube(cli: Any, cube: LightCube, distance: float, cancel: Optional[threading.Event]) -> bool:
    """
    Drive in on a cube, its centre that far ahead at the end, steering by the marker in each camera image, as Anki's
    engine did: the robot comes in along the line the cube's side makes, whatever it was off by. Near, the marker
    is out of sight, and the rest of the way is driven blind. Say whether the robot ended up there, facing it.
    """
    eyes = _Eyes(cli)
    start = time.perf_counter()
    count = eyes.count
    last: Optional[Tuple[float, float, float, float, util.Pose]] = None
    try:
        while time.perf_counter() - start < SERVO_TIME:
            _check(cancel)
            image, count = eyes.after(count, 0.3)
            marker = None if image is None else _marker_of(cli, cube, image)
            now = time.perf_counter()
            if marker is None:
                if last is None and now - start > 2.0:
                    return False
                if last is not None and now - last[0] > SERVO_LOST_TIME:
                    break
                continue
            along, left, turn = dock_error(marker.position, marker.normal)
            last = (now, along, left, turn, cli.pose)
            if along <= distance:
                break
            cli.drive_wheels(*servo_wheels(along, left, turn, distance))
        else:
            return False
    finally:
        cli.drive_wheels(0.0, 0.0)
        eyes.close()
    if last is None:
        return False
    _, along, left, turn, pose = last
    # The rest is blind: what the marker said last, less what the robot has driven since.
    heading = pose.rotation.angle_z.radians
    driven = ((cli.pose.position.x - pose.position.x) * math.cos(heading) +
              (cli.pose.position.y - pose.position.y) * math.sin(heading))
    left_to_go = along - driven - distance
    if left_to_go > 2.0:
        _check(cancel)
        if not cli.drive_straight(util.Distance(mm=min(left_to_go, 40.0)), speed=CREEP_SPEED):
            return False
    return abs(left) <= SERVO_LATERAL_TOLERANCE and abs(turn) <= SERVO_ANGLE_TOLERANCE


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
        # The fork takes the cube as the lift rises and the robot goes on, both at once.
        cli.set_lift_height(CARRY_HEIGHT, duration=PICKUP_LIFT_TIME)
        cli.drive_straight(util.Distance(mm=PICKUP_CREEP), speed=CREEP_SPEED, wait=False)
        _pause(SETTLE_TIME, cancel)
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


def roll_cube(cli: Any, cube: LightCube, cancel: Optional[threading.Event] = None, side: int = 0) -> bool:
    """
    Roll a cube over onto its side towards the robot, and say whether it did - whether the cube says its up axis
    changed. The robot comes from the side of the cube it last saw, or another: see dock_pose(). It stops at
    cliffs again afterwards; it cannot while the roll tips it up.
    """
    with in_use(cube):
        axis = cube.up_axis
        cli.set_lift_height(CARRY_HEIGHT)
        if not (go_to_cube(cli, cube, cancel=cancel, side=side) and
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
