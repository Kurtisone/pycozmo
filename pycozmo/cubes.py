"""

The Light Cubes, as the robot hears them and as it sees them.

The robot talks to its cubes over Bluetooth LE and passes on what they say. ObjectAvailable announces a cube in
range, about once a second, until it is connected with ObjectConnect; ObjectConnectionState confirms that and
gives it an object ID for the time of the connection. A connected cube reports being moved and stopping, taps,
and which of its sides is up; its lights are set by selecting it with CubeId, then CubeLights.

Where a cube is comes from the camera: the brain finds the markers, tells whose they are, and hands them here,
where each places its cube in the robot's world frame.

Anki's engine connected one cube of each of the three kinds and lit them: a dim breath once connected, a steady
cyan while the robot saw them. Cubes does the same with auto_connect set, the light animations loaded, and
update() called a few times a second.

"""

import contextlib
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence

from . import event
from . import protocol_encoder
from .cube_lights import CubeLightPattern
from .logger import logger


__all__ = [
    "CUBE_SIDE",
    "CUBE_TYPES",

    "CubePose",
    "LightCube",
    "Cubes",

    "in_use",
]


#: A Light Cube's side, in mm.
CUBE_SIDE = 45.0
#: The three kinds of Light Cube.
CUBE_TYPES = (
    protocol_encoder.ObjectType.Block_LIGHTCUBE1,
    protocol_encoder.ObjectType.Block_LIGHTCUBE2,
    protocol_encoder.ObjectType.Block_LIGHTCUBE3,
)


@dataclass(frozen=True)
class CubePose:
    """ Where a cube was seen, in the robot's world frame. """

    #: Its centre, in mm.
    x: float
    y: float
    z: float
    #: The heading, in radians, the side it was seen by faces: a quarter turn from any other side's.
    angle: float
    #: When it was seen, by time.perf_counter().
    time: float


class LightCube:
    """ One of the robot's Light Cubes. """

    def __init__(self, object_type: protocol_encoder.ObjectType) -> None:
        self.object_type = object_type
        #: Its serial number, once it has been heard.
        self.factory_id: Optional[int] = None
        self.rssi: Optional[int] = None
        #: The ID the robot gave it, while it is connected.
        self.object_id: Optional[int] = None
        self.connected = False
        #: When it was last asked to connect, while it has not.
        self.connecting_since: Optional[float] = None
        self.moving = False
        #: When the cube last started moving, by time.perf_counter().
        self.moving_since: Optional[float] = None
        #: Whether the robot is handling the cube, or playing with it: its moves are then the robot's doing, or the
        #: player's, and no news. See in_use().
        self.in_use = False
        self.up_axis: Optional[protocol_encoder.UpAxis] = None
        self.battery_level: Optional[int] = None
        #: Where it was last seen.
        self.pose: Optional[CubePose] = None
        #: The light animation it shows, by trigger, and when its current step ends.
        self.lights: Optional[str] = None
        self._light_steps: List[CubeLightPattern] = []
        self._light_step = 0
        self._light_until: Optional[float] = None

    def seen_within(self, seconds: float, now: Optional[float] = None) -> bool:
        """ Whether it was seen less than that long ago. """
        now = time.perf_counter() if now is None else now
        return self.pose is not None and now - self.pose.time < seconds

    def __repr__(self) -> str:
        return "LightCube({}, factory_id={}, object_id={}, connected={})".format(
            self.object_type.name, None if self.factory_id is None else "0x{:08x}".format(self.factory_id),
            self.object_id, self.connected)


class Cubes:
    """ The robot's three Light Cubes. """

    #: How long a cube is taken for visible after it was last seen, in seconds.
    VISIBLE_TIME = 1.0
    #: How long a connection is waited for before it is asked for again, in seconds.
    CONNECT_TIMEOUT = 5.0
    #: Light animations the cubes go back and forth between on their own: see update().
    IDLE_LIGHTS = ("Connected", "Visible")

    def __init__(self, cli: Any) -> None:
        # The client. Typed Any, since importing it here would be circular.
        self.cli = cli
        self.lock = threading.RLock()
        self.cubes: Dict[protocol_encoder.ObjectType, LightCube] = {
            object_type: LightCube(object_type) for object_type in CUBE_TYPES}
        #: Whether a cube of each kind is connected as soon as one is heard.
        self.auto_connect = False
        #: Cube light animations by trigger. See cube_lights.load_cube_light_animations().
        self.light_animations: Dict[str, List[CubeLightPattern]] = {}
        #: The cube in the lift, as far as cube_handling knows.
        self.carried: Optional[LightCube] = None

    def __iter__(self):
        return iter(self.cubes.values())

    def __getitem__(self, object_type: protocol_encoder.ObjectType) -> LightCube:
        return self.cubes[object_type]

    def by_object_id(self, object_id: int) -> Optional[LightCube]:
        for cube in self.cubes.values():
            if cube.connected and cube.object_id == object_id:
                return cube
        return None

    # What the robot hears.

    def on_object_available(self, cli: Any, pkt: protocol_encoder.ObjectAvailable) -> None:
        with self.lock:
            cube = self.cubes.get(protocol_encoder.ObjectType(pkt.object_type))
            if cube is None or cube.connected:
                return
            if cube.factory_id != pkt.factory_id and cube.connecting_since is not None:
                # Another cube of the same kind: the one asked for may answer yet.
                return
            cube.factory_id = pkt.factory_id
            cube.rssi = pkt.rssi
            now = time.perf_counter()
            if self.auto_connect and (cube.connecting_since is None or
                                      now - cube.connecting_since > self.CONNECT_TIMEOUT):
                self.connect(cube, now)

    def on_object_connection_state(self, cli: Any, pkt: protocol_encoder.ObjectConnectionState) -> None:
        with self.lock:
            cube = self.cubes.get(protocol_encoder.ObjectType(pkt.object_type))
            if cube is None:
                return
            cube.connecting_since = None
            if pkt.connected:
                cube.factory_id = pkt.factory_id
                cube.object_id = pkt.object_id
                cube.connected = True
                logger.info("Cube {} connected, S/N 0x{:08x}, ID {}.".format(
                    cube.object_type.name, pkt.factory_id, pkt.object_id))
                if "Connected" in self.light_animations:
                    self.play_lights(cube, "Connected")
            else:
                cube.connected = False
                cube.object_id = None
                cube.moving = False
                cube.lights = None
                logger.info("Cube {} disconnected.".format(cube.object_type.name))
        self.cli.dispatch(event.EvtCubeConnectionChange, self.cli, cube, bool(pkt.connected))

    def on_object_moved(self, cli: Any, pkt: protocol_encoder.ObjectMoved) -> None:
        self._set_moving(pkt.object_id, True)

    def on_object_stopped_moving(self, cli: Any, pkt: protocol_encoder.ObjectStoppedMoving) -> None:
        self._set_moving(pkt.object_id, False)

    def _set_moving(self, object_id: int, moving: bool) -> None:
        with self.lock:
            cube = self.by_object_id(object_id)
            if cube is None or cube.moving == moving:
                return
            cube.moving = moving
            if moving:
                cube.moving_since = time.perf_counter()
            elif cube is not self.carried and not cube.in_use and cube.pose is not None and \
                    cube.moving_since is not None and cube.pose.time < cube.moving_since:
                # Moved by someone else, it has to be seen again to be known where it is. One the lift set down
                # has been placed since it started moving.
                cube.pose = None
        self.cli.dispatch(event.EvtCubeMovingChange, self.cli, cube, moving)

    def on_object_tapped(self, cli: Any, pkt: protocol_encoder.ObjectTapped) -> None:
        cube = self.by_object_id(pkt.object_id)
        if cube is not None:
            self.cli.dispatch(event.EvtCubeTapped, self.cli, cube, pkt.num_taps)

    def on_object_up_axis_changed(self, cli: Any, pkt: protocol_encoder.ObjectUpAxisChanged) -> None:
        cube = self.by_object_id(pkt.object_id)
        if cube is not None:
            cube.up_axis = protocol_encoder.UpAxis(pkt.axis)

    def on_object_power_level(self, cli: Any, pkt: protocol_encoder.ObjectPowerLevel) -> None:
        cube = self.by_object_id(pkt.object_id)
        if cube is not None:
            cube.battery_level = pkt.battery_level

    # What is asked of them.

    def connect(self, cube: LightCube, now: Optional[float] = None) -> None:
        """ Ask the robot to connect a cube it has heard. """
        if cube.factory_id is None:
            raise ValueError("Cube {} has not been heard.".format(cube.object_type.name))
        cube.connecting_since = time.perf_counter() if now is None else now
        self.cli.conn.send(protocol_encoder.ObjectConnect(factory_id=cube.factory_id, connect=True))

    def disconnect(self, cube: LightCube) -> None:
        if cube.factory_id is not None:
            self.cli.conn.send(protocol_encoder.ObjectConnect(factory_id=cube.factory_id, connect=False))

    def set_lights(self, cube: LightCube, states: Sequence[protocol_encoder.LightState],
                   rotation_period_frames: int = 0) -> None:
        """ Set a connected cube's four lights. """
        if not cube.connected or cube.object_id is None:
            return
        self.cli.conn.send(protocol_encoder.CubeId(object_id=cube.object_id,
                                                   rotation_period_frames=rotation_period_frames))
        self.cli.conn.send(protocol_encoder.CubeLights(states=tuple(states)))

    def play_lights(self, cube: LightCube, trigger: str, now: Optional[float] = None) -> None:
        """ Show one of Anki's cube light animations, by trigger. """
        now = time.perf_counter() if now is None else now
        with self.lock:
            cube.lights = trigger
            cube._light_steps = list(self.light_animations[trigger])
            cube._light_step = 0
            self._show_step(cube, now)

    def show_lights(self, cube: LightCube, name: str, states: Sequence[protocol_encoder.LightState],
                    rotation_period_frames: int = 0) -> None:
        """ Show lights of one's own, under a name: update() leaves them be until another animation is played. """
        with self.lock:
            cube.lights = name
            cube._light_steps = []
            cube._light_until = None
        self.set_lights(cube, states, rotation_period_frames)

    def _show_step(self, cube: LightCube, now: float) -> None:
        step = cube._light_steps[cube._light_step]
        cube._light_until = now + step.duration if step.duration else None
        self.set_lights(cube, step.states, step.rotation_period_frames)

    # What the robot sees.

    def observe(self, object_type: protocol_encoder.ObjectType, position: Sequence[float],
                normal: Sequence[float], now: Optional[float] = None) -> Optional[LightCube]:
        """
        Place a cube from one of its markers, seen at a position, in mm, facing a way, as a unit vector, both in
        the robot's frame: its centre is half a side behind the marker.
        """
        now = time.perf_counter() if now is None else now
        cube = self.cubes.get(object_type)
        if cube is None:
            return None
        heading = math.atan2(normal[1], normal[0])
        x = position[0] - CUBE_SIDE / 2 * math.cos(heading)
        y = position[1] - CUBE_SIDE / 2 * math.sin(heading)
        pose = self.cli.pose
        angle = pose.rotation.angle_z.radians
        c, s = math.cos(angle), math.sin(angle)
        with self.lock:
            cube.pose = CubePose(x=pose.position.x + c * x - s * y, y=pose.position.y + s * x + c * y,
                                 z=pose.position.z + CUBE_SIDE / 2, angle=_wrap(angle + heading), time=now)
            if cube.connected and cube.lights == "Connected" and "Visible" in self.light_animations:
                self.play_lights(cube, "Visible", now)
        self.cli.dispatch(event.EvtCubeObserved, self.cli, cube)
        return cube

    def place(self, cube: LightCube, x: float, y: float, angle: float, z: float = CUBE_SIDE / 2,
              now: Optional[float] = None) -> None:
        """
        Take a cube to be somewhere it was not seen: where the lift set it down, say, in the robot's world frame,
        its centre z up and its side facing the heading given.
        """
        now = time.perf_counter() if now is None else now
        with self.lock:
            cube.pose = CubePose(x=x, y=y, z=z, angle=_wrap(angle), time=now)

    def update(self, now: Optional[float] = None) -> None:
        """ Move the light animations on, and let the cubes the robot no longer sees go back to Connected. """
        now = time.perf_counter() if now is None else now
        with self.lock:
            for cube in self.cubes.values():
                if not cube.connected or cube.lights is None:
                    continue
                if cube.lights == "Visible" and not cube.seen_within(self.VISIBLE_TIME, now) and \
                        "Connected" in self.light_animations:
                    self.play_lights(cube, "Connected", now)
                elif cube._light_until is not None and now >= cube._light_until:
                    if cube._light_step + 1 < len(cube._light_steps):
                        cube._light_step += 1
                        self._show_step(cube, now)
                    else:
                        cube._light_until = None
                        # An animation that ends gives the cube back to its idle lights.
                        if cube.lights not in self.IDLE_LIGHTS and "Connected" in self.light_animations:
                            self.play_lights(cube, "Visible" if cube.seen_within(self.VISIBLE_TIME, now) and
                                             "Visible" in self.light_animations else "Connected", now)

    def reset(self) -> None:
        """ Forget the connections: the robot drops them when the connection to it ends. """
        with self.lock:
            for cube in self.cubes.values():
                cube.connected = False
                cube.object_id = None
                cube.connecting_since = None
                cube.moving = False
                cube.lights = None
            self.carried = None


@contextlib.contextmanager
def in_use(*cubes: LightCube) -> Iterator[None]:
    """ Mark cubes in use for as long as the context lasts. See LightCube.in_use. """
    were = [cube.in_use for cube in cubes]
    for cube in cubes:
        cube.in_use = True
    try:
        yield
    finally:
        for cube, was in zip(cubes, were):
            cube.in_use = was


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
