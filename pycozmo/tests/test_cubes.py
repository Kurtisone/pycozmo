import math
import unittest
from typing import Any, List, Tuple

import pycozmo
from pycozmo import cube_lights, cubes, event, lights, util
from pycozmo.protocol_encoder import ObjectType

from .test_brain import cozmo_assets_available

CUBE1 = ObjectType.Block_LIGHTCUBE1


class FakeClient(pycozmo.event.Dispatcher):
    """ What Cubes needs of a client: a connection to send through, the robot's pose, and dispatching. """

    def __init__(self) -> None:
        super().__init__()
        self.sent: List[pycozmo.protocol_base.Packet] = []
        self.conn = self
        self.pose = util.Pose(0.0, 0.0, 0.0, angle_z=util.Angle(radians=0.0))
        self.events: List[Tuple[type, Tuple[Any, ...]]] = []

    def send(self, pkt: pycozmo.protocol_base.Packet) -> None:
        self.sent.append(pkt)

    def dispatch(self, event: Any, *args: Any, **kwargs: Any) -> Any:
        self.events.append((event, args))
        return super().dispatch(event, *args, **kwargs)


def pattern(color: Tuple[int, int, int], duration: float = 0.0) -> cube_lights.CubeLightPattern:
    state = pycozmo.protocol_encoder.LightState(on_color=lights.Color(rgb=color).to_int16())
    return cube_lights.CubeLightPattern(states=(state, ) * 4, rotation_period_frames=0, duration=duration)


class CubesTestCase(unittest.TestCase):

    def setUp(self):
        self.cli = FakeClient()
        self.cubes = cubes.Cubes(self.cli)
        self.cubes.light_animations = {"Connected": [pattern((0, 127, 127))], "Visible": [pattern((0, 230, 230))],
                                       "Flourish": [pattern((255, 0, 0), 0.5), pattern((0, 255, 0), 0.5)]}

    def hear(self, object_type=CUBE1, factory_id=0x1234, now=None):
        self.cubes.on_object_available(self.cli, pycozmo.protocol_encoder.ObjectAvailable(
            factory_id=factory_id, object_type=object_type, rssi=-50))

    def connected(self, object_type=CUBE1, object_id=5, connected=True):
        self.cubes.on_object_connection_state(self.cli, pycozmo.protocol_encoder.ObjectConnectionState(
            object_id=object_id, factory_id=0x1234, object_type=object_type, connected=connected))
        return self.cubes[object_type]

    def sent(self, kind):
        return [pkt for pkt in self.cli.sent if isinstance(pkt, kind)]

    def colors(self):
        """ The on colour of the first light of each CubeLights sent. """
        return [lights.Color.from_int16(pkt.states[0].on_color).int_color for pkt in
                self.sent(pycozmo.protocol_encoder.CubeLights)]


class TestConnection(CubesTestCase):

    def test_nothing_is_connected_unless_asked(self):
        self.hear()
        self.assertEqual(self.cli.sent, [])
        self.assertEqual(self.cubes[CUBE1].factory_id, 0x1234)

    def test_a_cube_heard_is_connected_once(self):
        self.cubes.auto_connect = True
        self.hear()
        self.hear()
        connects = self.sent(pycozmo.protocol_encoder.ObjectConnect)
        self.assertEqual([(pkt.factory_id, pkt.slot) for pkt in connects], [(0x1234, 0)])

    def test_each_kind_has_a_slot_of_its_own(self):
        # A cube asked for in the slot of another takes its place: the robot kept one cube at a time.
        self.cubes.auto_connect = True
        for object_type, factory_id in ((CUBE1, 0x1), (ObjectType.Block_LIGHTCUBE2, 0x2),
                                        (ObjectType.Block_LIGHTCUBE3, 0x3)):
            self.hear(object_type, factory_id)
        connects = self.sent(pycozmo.protocol_encoder.ObjectConnect)
        self.assertEqual([(pkt.factory_id, pkt.slot) for pkt in connects], [(0x1, 0), (0x2, 1), (0x3, 2)])

    def test_the_cube_asked_for_among_several(self):
        # Two robots' cubes about: this one's are asked for by their factory IDs.
        self.cubes.auto_connect = True
        self.cubes.factory_ids[CUBE1] = 0x5678
        self.hear(factory_id=0x1234)
        self.assertEqual(self.sent(pycozmo.protocol_encoder.ObjectConnect), [])
        self.hear(factory_id=0x5678)
        self.assertEqual([pkt.factory_id for pkt in self.sent(pycozmo.protocol_encoder.ObjectConnect)], [0x5678])

    def test_dropping_a_cube_empties_its_slot(self):
        cube = self.connected()
        self.cubes.disconnect(cube)
        pkt = self.sent(pycozmo.protocol_encoder.ObjectConnect)[-1]
        self.assertEqual((pkt.factory_id, pkt.slot), (0, 0))

    def test_a_connection_that_does_not_come_is_asked_for_again(self):
        self.cubes.auto_connect = True
        self.hear()
        cube = self.cubes[CUBE1]
        assert cube.connecting_since is not None
        cube.connecting_since -= cubes.Cubes.CONNECT_TIMEOUT + 1.0
        self.hear()
        self.assertEqual(len(self.sent(pycozmo.protocol_encoder.ObjectConnect)), 2)

    def test_a_connected_cube_breathes(self):
        cube = self.connected()
        self.assertTrue(cube.connected)
        self.assertEqual(cube.object_id, 5)
        self.assertEqual(self.sent(pycozmo.protocol_encoder.CubeId)[0].object_id, 5)
        self.assertEqual(cube.lights, "Connected")
        self.assertIn((event.EvtCubeConnectionChange, (self.cli, cube, True)), self.cli.events)

    def test_a_cube_that_drops_out_is_forgotten(self):
        self.connected()
        cube = self.connected(connected=False)
        self.assertFalse(cube.connected)
        self.assertIsNone(cube.object_id)
        self.assertIsNone(self.cubes.by_object_id(5))


class TestEvents(CubesTestCase):

    def test_moving_taps_and_up_axis(self):
        cube = self.connected()
        self.cubes.on_object_moved(self.cli, pycozmo.protocol_encoder.ObjectMoved(object_id=5))
        self.cubes.on_object_moved(self.cli, pycozmo.protocol_encoder.ObjectMoved(object_id=5))
        self.cubes.on_object_stopped_moving(self.cli, pycozmo.protocol_encoder.ObjectStoppedMoving(object_id=5))
        self.cubes.on_object_tapped(self.cli, pycozmo.protocol_encoder.ObjectTapped(object_id=5, num_taps=2))
        self.cubes.on_object_up_axis_changed(self.cli, pycozmo.protocol_encoder.ObjectUpAxisChanged(
            object_id=5, axis=pycozmo.protocol_encoder.UpAxis.ZPositive))
        cube_events = [(evt, args[2:]) for evt, args in self.cli.events if evt is not event.EvtCubeConnectionChange]
        self.assertEqual(cube_events, [(event.EvtCubeMovingChange, (True, )), (event.EvtCubeMovingChange, (False, )),
                                       (event.EvtCubeTapped, (2, ))])
        self.assertEqual(cube.up_axis, pycozmo.protocol_encoder.UpAxis.ZPositive)

    def test_what_an_unknown_object_says_is_ignored(self):
        self.cubes.on_object_tapped(self.cli, pycozmo.protocol_encoder.ObjectTapped(object_id=9, num_taps=1))
        self.assertEqual(self.cli.events, [])


class TestMovedAway(CubesTestCase):

    def move(self):
        self.cubes.on_object_moved(self.cli, pycozmo.protocol_encoder.ObjectMoved(object_id=5))
        self.cubes.on_object_stopped_moving(self.cli, pycozmo.protocol_encoder.ObjectStoppedMoving(object_id=5))

    def test_a_cube_moved_is_no_longer_known_where_it_is(self):
        cube = self.connected()
        self.cubes.place(cube, 200.0, 0.0, math.pi)
        self.move()
        self.assertIsNone(cube.pose)

    def test_one_in_the_lift_is(self):
        cube = self.connected()
        self.cubes.place(cube, 200.0, 0.0, math.pi)
        self.cubes.carried = cube
        self.move()
        self.assertIsNotNone(cube.pose)

    def test_and_one_in_use(self):
        cube = self.connected()
        self.cubes.place(cube, 200.0, 0.0, math.pi)
        with cubes.in_use(cube):
            with cubes.in_use(cube):
                self.move()
            self.assertTrue(cube.in_use)
        self.assertFalse(cube.in_use)
        self.assertIsNotNone(cube.pose)

    def test_so_is_one_placed_since_it_started_moving(self):
        # The lift sets a cube down: it moves, cube_handling places it, and it stops.
        cube = self.connected()
        self.cubes.on_object_moved(self.cli, pycozmo.protocol_encoder.ObjectMoved(object_id=5))
        self.cubes.place(cube, 200.0, 0.0, math.pi)
        self.cubes.on_object_stopped_moving(self.cli, pycozmo.protocol_encoder.ObjectStoppedMoving(object_id=5))
        assert cube.pose is not None
        self.assertEqual(cube.pose.x, 200.0)


class TestSeeing(CubesTestCase):

    def test_where_a_cube_is(self):
        # The robot at (100, 50), facing +y; a marker 150 mm ahead of it, facing it squarely.
        self.cli.pose = util.Pose(100.0, 50.0, 0.0, angle_z=util.Angle(degrees=90.0))
        cube = self.cubes.observe(CUBE1, (150.0, 0.0, 24.0), (-1.0, 0.0, 0.0), now=10.0)
        assert cube is not None and cube.pose is not None
        # Its centre is half a side further, 172.5 mm ahead of the robot: up the y axis.
        self.assertAlmostEqual(cube.pose.x, 100.0)
        self.assertAlmostEqual(cube.pose.y, 50.0 + 150.0 + 22.5)
        self.assertAlmostEqual(cube.pose.z, 22.5)
        # The side it was seen by faces -y, back at the robot.
        self.assertAlmostEqual(cube.pose.angle, -math.pi / 2)
        self.assertIn((event.EvtCubeObserved, (self.cli, cube)), self.cli.events)

    def test_a_cube_seen_lights_up_and_dims_again(self):
        self.connected()
        self.cubes.observe(CUBE1, (150.0, 0.0, 24.0), (-1.0, 0.0, 0.0), now=10.0)
        self.assertEqual(self.cubes[CUBE1].lights, "Visible")
        self.cubes.update(now=10.5)
        self.assertEqual(self.cubes[CUBE1].lights, "Visible")
        self.cubes.update(now=10.0 + cubes.Cubes.VISIBLE_TIME + 0.1)
        self.assertEqual(self.cubes[CUBE1].lights, "Connected")
        # 5 bits a colour: 127 comes back as 123, 230 as 222.
        self.assertEqual(self.colors(), [0x007b7bff, 0x00dedeff, 0x007b7bff])

    def test_an_animation_plays_through_and_gives_the_cube_back(self):
        cube = self.connected()
        self.cubes.play_lights(cube, "Flourish", now=0.0)
        self.cubes.update(now=0.4)
        self.cubes.update(now=0.6)
        self.cubes.update(now=1.1)
        self.assertEqual(cube.lights, "Connected")
        self.assertEqual(self.colors()[1:], [0xff0000ff, 0x00ff00ff, 0x007b7bff])


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestLightAnimations(unittest.TestCase):

    def test_anki_s_forty(self):
        animations = cube_lights.load_cube_light_animations(str(util.get_cozmo_asset_dir()))
        self.assertEqual(len(animations), 40)
        connected = animations["Connected"]
        self.assertEqual(len(connected), 1)
        state = connected[0].states[0]
        # A dim cyan breath: on 10 ms, off 5 s, a second's fade either way.
        self.assertEqual(lights.Color.from_int16(state.on_color).int_color, 0x007b7bff)
        self.assertEqual((state.on_frames, state.off_frames, state.transition_on_frames,
                          state.transition_off_frames), (1, 167, 33, 33))
        self.assertEqual(connected[0].duration, 0.0)
