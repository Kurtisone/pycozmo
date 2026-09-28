"""

Tests for how an animation's head, lift and body keyframes become what the robot is sent.

What each message does was measured on a robot:

- AnimHead and AnimLift move for their duration, in ms, which is a byte. Four AnimHead of 250 ms in a
  row took the head from -20 to +20 degrees at an even pace in 0.93 s.
- AnimBody's speed is in mm/s along the path and its second field is a curvature radius in mm, from
  which the robot works out each wheel itself: 32767 is straight ahead, a radius of 50 turned it left
  and one of -50 right, and 0 turns it in place with speed in degrees per second - 30 turned it 26.5
  degrees in a second, and 90, 86.

"""

import glob
import os
import unittest
from typing import Dict, List, Tuple
from unittest import mock

import pycozmo
from pycozmo import anim, anim_encoder, protocol_encoder
from pycozmo.anim import MAX_MOVE_MS, Move, split_moves

from .test_brain import cozmo_assets_available


def pieces(moves: List[Move], start: float = 0.0) -> List[Tuple[int, int, int, int]]:
    return list(split_moves(moves, start))


class TestSplitMoves(unittest.TestCase):

    def test_a_short_move_goes_out_as_it_is(self):
        self.assertEqual(pieces([Move(100, 200, 10, 0)]), [(100, 200, 10, 0)])

    def test_a_long_move_goes_out_in_equal_pieces(self):
        # What was measured on a robot: four pieces of 250 ms, -20 to +20 degrees.
        self.assertEqual(pieces([Move(0, 1000, 20, 0)], start=-20),
                         [(0, 250, -10, 0), (250, 250, 0, 0), (500, 250, 10, 0), (750, 250, 20, 0)])

    def test_no_piece_is_longer_than_a_byte_allows(self):
        for duration in (1, 254, 255, 256, 509, 510, 511, 1000, 4500, 8900):
            with self.subTest(duration=duration):
                laid = pieces([Move(40, duration, 30, 0)])
                self.assertTrue(all(d <= MAX_MOVE_MS for _, d, _, _ in laid))
                self.assertEqual(sum(d for _, d, _, _ in laid), duration)
                self.assertEqual(laid[-1][2], 30)
                self.assertEqual(laid[0][0], 40)

    def test_the_pace_is_the_keyframes(self):
        # One piece per 255 ms at most, each covering its share of the angle.
        laid = pieces([Move(0, 900, 36, 0)])
        self.assertEqual([target for _, _, target, _ in laid], [9, 18, 27, 36])

    def test_the_first_move_starts_where_the_head_is(self):
        self.assertEqual([t for _, _, t, _ in pieces([Move(0, 500, 25, 0)], start=-25)], [0, 25])
        self.assertEqual([t for _, _, t, _ in pieces([Move(0, 500, 25, 0)], start=15)], [20, 25])

    def test_a_move_starts_where_the_last_one_ended(self):
        laid = pieces([Move(0, 200, 10, 0), Move(400, 600, 40, 0)], start=-20)
        self.assertEqual([t for _, _, t, _ in laid], [10, 20, 30, 40])

    def test_a_move_that_starts_early_takes_over(self):
        # The first move is half way, at 20 degrees, when the second starts.
        laid = pieces([Move(0, 1000, 40, 0), Move(500, 500, 0, 0)])
        self.assertEqual(laid, [(0, 250, 10, 0), (250, 250, 20, 0), (500, 250, 10, 0), (750, 250, 0, 0)])

    def test_variability_goes_on_the_last_piece(self):
        self.assertEqual([v for _, _, _, v in pieces([Move(0, 600, 30, 5)])], [0, 0, 5])

    def test_an_instant_move_is_one_piece(self):
        self.assertEqual(pieces([Move(66, 0, 12, 0)], start=-5), [(66, 0, 12, 0)])


def body(keyframes):
    """ What a clip made of these body keyframes sends, as {time: [(speed, radius)]}. """
    clip = anim_encoder.AnimClip("test", keyframes)
    ppclip = anim.PreprocessedClip.from_anim_clip(clip)
    laid: Dict[int, List[Tuple[int, int]]] = {}
    for time_ms, pkts in sorted(ppclip.keyframes.items()):
        for pkt in pkts:
            assert isinstance(pkt, protocol_encoder.AnimBody), type(pkt).__name__
            laid.setdefault(time_ms, []).append((pkt.speed, pkt.curvature_radius_mm))
    return laid


class TestBodyMotion(unittest.TestCase):

    def test_straight_ahead(self):
        self.assertEqual(body([anim_encoder.AnimBodyMotion(100, 500, "STRAIGHT", 30)]),
                         {100: [(30, anim.STRAIGHT)], 600: [(0, anim.STRAIGHT)]})

    def test_a_turn_in_place_keeps_its_degrees_per_second(self):
        # It used to go out as TurnInPlaceAtSpeed with the degrees taken for mm/s.
        self.assertEqual(body([anim_encoder.AnimBodyMotion(0, 561, "TURN_IN_PLACE", 55)]),
                         {0: [(55, anim.TURN_IN_PLACE)], 561: [(0, anim.STRAIGHT)]})

    def test_an_arc_goes_out_with_its_radius(self):
        # It used to go out as DriveWheels with each wheel at the speed times a radius: 22 mm/s on a
        # radius of 7 mm asked for 649 mm/s.
        self.assertEqual(body([anim_encoder.AnimBodyMotion(0, 165, 7.0, 22)])[0], [(22, 7)])
        self.assertEqual(body([anim_encoder.AnimBodyMotion(0, 99, -104.0, 220)])[0], [(220, -104)])

    def test_an_arc_never_reads_as_something_else(self):
        # 0 would turn in place and 32767 drive straight.
        self.assertEqual(body([anim_encoder.AnimBodyMotion(0, 33, 0.3, 20)])[0], [(20, 1)])
        self.assertEqual(body([anim_encoder.AnimBodyMotion(0, 33, -0.2, 20)])[0], [(20, -1)])
        self.assertEqual(body([anim_encoder.AnimBodyMotion(0, 33, 99999.0, 20)])[0], [(20, 32766)])

    def test_a_motion_that_follows_on_is_not_stopped_first(self):
        laid = body([anim_encoder.AnimBodyMotion(0, 300, "STRAIGHT", 30),
                     anim_encoder.AnimBodyMotion(300, 300, "TURN_IN_PLACE", -40)])
        self.assertEqual(laid, {0: [(30, anim.STRAIGHT)], 300: [(-40, anim.TURN_IN_PLACE)],
                                600: [(0, anim.STRAIGHT)]})

    def test_nothing_drives_the_wheels_directly(self):
        clip = anim_encoder.AnimClip("test", [anim_encoder.AnimBodyMotion(0, 100, 23.0, 220),
                                              anim_encoder.AnimBodyMotion(200, 100, "TURN_IN_PLACE", 61)])
        kinds = {type(pkt).__name__ for pkts in anim.PreprocessedClip.from_anim_clip(clip).keyframes.values()
                 for pkt in pkts}
        self.assertEqual(kinds, {"AnimBody"})


class TestPlaying(unittest.TestCase):
    """ The head and lift moves are split when the clip plays, from where the head and lift are. """

    def setUp(self):
        self.cli = pycozmo.client.Client()
        for name in ("send", "post_event"):
            patcher = mock.patch.object(self.cli.conn, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def played(self, ppclip):
        with mock.patch.object(self.cli.anim_controller, "play_anim_frame") as frame:
            self.cli.play_anim_ppclip(ppclip)
        return [pkt for call in frame.call_args_list for pkt in (call.args[2] or ())]

    def test_the_head_moves_from_where_it_is(self):
        self.cli.head_angle = pycozmo.util.Angle(degrees=-20)
        sent = self.played(anim.PreprocessedClip(head_moves=[Move(0, 1000, 20, 0)]))
        heads = [(pkt.duration_ms, pkt.angle_deg) for pkt in sent if isinstance(pkt, protocol_encoder.AnimHead)]
        self.assertEqual(heads, [(250, -10), (250, 0), (250, 10), (250, 20)])

    def test_the_lift_moves_from_where_it_is(self):
        self.cli.lift_position = pycozmo.robot.LiftPosition(height=pycozmo.util.Distance(mm=32.0))
        sent = self.played(anim.PreprocessedClip(lift_moves=[Move(0, 510, 92, 0)]))
        lifts = [(pkt.duration_ms, pkt.height_mm) for pkt in sent if isinstance(pkt, protocol_encoder.AnimLift)]
        self.assertEqual(lifts, [(255, 62), (255, 92)])


class TestLiftAngle(unittest.TestCase):
    """ RobotState carries the lift's angle, which the client used to read as a height in mm. """

    def test_the_lift_position_comes_from_its_angle(self):
        cli = pycozmo.client.Client()
        # 0.202 rad is what a robot reported with its lift sent to 60 mm.
        cli._on_robot_state(cli.conn, protocol_encoder.RobotState(lift_angle_rad=0.202, cliff_data_raw=(0, 0, 0, 0)))
        self.assertAlmostEqual(cli.lift_position.height.mm, 58.2, places=1)
        cli._on_robot_state(cli.conn, protocol_encoder.RobotState(lift_angle_rad=-0.2074, cliff_data_raw=(0, 0, 0, 0)))
        self.assertLess(cli.lift_position.height.mm, pycozmo.robot.MIN_LIFT_HEIGHT.mm + 1.0)


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAgainstCozmoAssets(unittest.TestCase):

    def test_every_animation_moves_within_what_the_robot_takes(self):
        anim_dir = str(pycozmo.util.get_cozmo_anim_dir())
        clips = [clip for fspec in sorted(glob.glob(os.path.join(anim_dir, "*.bin")))
                 for clip in anim_encoder.AnimClips.from_fb_file(fspec).clips]
        self.assertGreater(len(clips), 900)
        motion_types = (anim_encoder.AnimHeadAngle, anim_encoder.AnimLiftHeight, anim_encoder.AnimBodyMotion)
        for clip in clips:
            # Only the motion: rendering every procedural face would take most of a minute.
            motion_only = anim_encoder.AnimClip(clip.name, [k for k in clip.keyframes if isinstance(k, motion_types)])
            ppclip = anim.PreprocessedClip.from_anim_clip(motion_only)
            motion = ppclip.motion_keyframes(-10.0, 40.0)
            for pkts in list(ppclip.keyframes.values()) + list(motion.values()):
                for pkt in pkts:
                    self.assertNotIsInstance(pkt, (protocol_encoder.DriveWheels,
                                                   protocol_encoder.TurnInPlaceAtSpeed), clip.name)
                    if isinstance(pkt, (protocol_encoder.AnimHead, protocol_encoder.AnimLift)):
                        self.assertLessEqual(pkt.duration_ms, MAX_MOVE_MS, clip.name)
            head_time = sum(move.duration_ms for move in ppclip.head_moves)
            laid = sum(pkt.duration_ms for pkts in motion.values() for pkt in pkts
                       if isinstance(pkt, protocol_encoder.AnimHead))
            # Only overlapping keyframes lose time, and none gains any.
            self.assertLessEqual(laid, head_time, clip.name)
