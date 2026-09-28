import time
import unittest
from collections import deque
from typing import Deque, List, Optional, Tuple
from unittest import mock

import pycozmo
from pycozmo.anim_controller import AnimationQueue


class TestAnimationQueue(unittest.TestCase):

    def setUp(self):
        self.queue = AnimationQueue()
        self.audio = pycozmo.protocol_encoder.OutputAudio(samples=bytes(744))
        self.image = pycozmo.protocol_encoder.DisplayImage(image=b"\x3f\x3f")

    def test_empty(self):
        self.assertTrue(self.queue.is_empty())
        self.assertEqual(self.queue.get(), (None, None, None))

    def test_put_audio(self):
        self.queue.put_audio([self.audio])
        self.assertFalse(self.queue.is_empty())
        audio, image, pkts = self.queue.get()
        # The queue hands back the packets it was given, not their serialized form.
        self.assertIs(audio, self.audio)
        self.assertIsNone(image)
        self.assertIsNone(pkts)

    def test_put_image(self):
        self.queue.put_image(self.image)
        audio, image, pkts = self.queue.get()
        self.assertIsNone(audio)
        self.assertIs(image, self.image)

    def test_put_anim_frame(self):
        action = pycozmo.protocol_encoder.SetHeadAngle(angle_rad=0.5)
        self.queue.put_anim_frame(self.audio, self.image, [action])
        audio, image, pkts = self.queue.get()
        self.assertIs(audio, self.audio)
        self.assertIs(image, self.image)
        assert pkts is not None
        self.assertEqual(list(pkts), [action])
        self.assertTrue(self.queue.is_empty())

    def test_order(self):
        second = pycozmo.protocol_encoder.OutputAudio(samples=bytes(744))
        self.queue.put_audio([self.audio, second])
        self.assertIs(self.queue.get()[0], self.audio)
        self.assertIs(self.queue.get()[0], second)

    def test_clear(self):
        self.queue.put_anim_frame(self.audio, self.image, None)
        self.assertFalse(self.queue.is_empty())
        self.queue.clear()
        self.assertTrue(self.queue.is_empty())


class TestFPSTimer(unittest.TestCase):

    def test_rejects_non_positive(self):
        for fps in (0, -1):
            with self.assertRaises(ValueError):
                pycozmo.util.FPSTimer(fps)

    def test_first_call_starts_the_sequence(self):
        timer = pycozmo.util.FPSTimer(1000)
        self.assertIsNone(timer._start)
        timer.sleep()
        self.assertIsNotNone(timer._start)

    def test_maintains_frame_count(self):
        # A sequence that keeps up increments the frame count rather than restarting.
        timer = pycozmo.util.FPSTimer(1000)
        timer.sleep()
        start = timer._start
        timer.sleep()
        self.assertEqual(timer._start, start)
        self.assertEqual(timer._frames, 3)

    def test_resets_when_behind(self):
        timer = pycozmo.util.FPSTimer(1000)
        timer.sleep()
        # Pretend the sequence began long ago, so the next frame is already overdue.
        assert timer._start is not None
        timer._start -= 10.0
        timer.sleep()
        self.assertEqual(timer._frames, 1)


class TestFrames(unittest.TestCase):
    """
    The robot reads its animation buffer a frame at a time, and every frame starts with its audio or
    silence. Any animation message where a frame should start is an error on the robot - "Expecting
    either audio sample or silence next in animation buffer" - and the frame is lost.
    """

    def setUp(self):
        # A client that is never started, with its connection stubbed out.
        self.cli = pycozmo.client.Client()
        self.sent: List[pycozmo.protocol_base.Packet] = []
        for name, side_effect in (("send", self.sent.append), ("post_event", None)):
            patcher = mock.patch.object(self.cli.conn, name, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = self.cli.anim_controller
        self.controller.enable_animations(True)
        # As if started: the frame loop is running.
        self.controller.thread = mock.Mock()
        self.audio = pycozmo.protocol_encoder.OutputAudio(samples=bytes(744))
        self.image = pycozmo.protocol_encoder.DisplayImage(image=b"\x3f\x3f")

    def kinds(self):
        return [type(pkt).__name__ for pkt in self.sent]

    def start_animation(self, anim_id=1):
        """ Have an animation start on the robot: its StartAnimation goes out. """
        self.controller.play_anim_frame(None, None, (pycozmo.protocol_encoder.StartAnimation(anim_id=anim_id),))
        self.assertIn("StartAnimation", self.frame())

    def frame(self):
        """ Send one frame and return what went out. """
        del self.sent[:]
        self.controller._send_frame()
        return self.kinds()

    def test_a_frame_starts_with_its_audio(self):
        head = pycozmo.protocol_encoder.AnimHead(duration_ms=33, angle_deg=10)
        self.controller.play_anim_frame(self.audio, self.image, [head])
        self.assertEqual(self.frame(), ["OutputAudio", "DisplayImage", "AnimHead"])

    def test_a_frame_without_audio_starts_with_silence(self):
        self.controller.play_anim_frame(None, self.image, None)
        self.assertEqual(self.frame()[0], "OutputSilence")

    def test_cancelling_sends_nothing_by_itself(self):
        # It used to send EndAnimation there and then, between two of the frame loop's packets.
        self.start_animation()
        del self.sent[:]
        self.controller.cancel_anim()
        self.assertEqual(self.sent, [])

    def test_end_animation_goes_out_in_a_frame_of_its_own(self):
        # It ends the frame it is in: the robot expects audio right after it, and reported the
        # StartAnimation that followed it in the same frame - "Got 0x9b instead".
        self.start_animation()
        self.controller.cancel_anim()
        self.assertEqual(self.frame(), ["OutputSilence", "EndAnimation"])
        self.assertNotIn("EndAnimation", self.frame(), "it goes out once")

    def test_a_new_animation_starts_in_the_next_frame(self):
        # What play_anim_ppclip() does: cancel, then queue the StartAnimation frame.
        self.start_animation()
        self.controller.cancel_anim()
        start = pycozmo.protocol_encoder.StartAnimation(anim_id=3)
        self.controller.play_anim_frame(None, None, (start,))
        self.assertEqual(self.frame(), ["OutputSilence", "EndAnimation"])
        kinds = self.frame()
        self.assertEqual(kinds[0], "OutputSilence")
        self.assertIn("StartAnimation", kinds)

    def test_nothing_is_ended_when_nothing_was_started(self):
        self.controller.cancel_anim()
        self.assertNotIn("EndAnimation", self.frame())

    def test_an_animation_that_ended_on_its_own_is_not_ended_again(self):
        # Its own EndAnimation has gone out; a second one right after a clip ended and the next
        # began is what the robot reported as "Got 0x9a instead".
        self.start_animation()
        self.controller.play_anim_frame(None, None, (pycozmo.protocol_encoder.EndAnimation(),))
        self.assertIn("EndAnimation", self.frame())
        self.controller.cancel_anim()
        self.assertNotIn("EndAnimation", self.frame())

    def test_an_animation_whose_start_never_went_out_is_not_ended(self):
        self.controller.play_anim_frame(None, None, (pycozmo.protocol_encoder.StartAnimation(anim_id=2),))
        self.controller.cancel_anim()
        self.assertNotIn("EndAnimation", self.frame())
        self.assertNotIn("StartAnimation", self.kinds(), "the queued start was dropped")

    def test_cancelling_twice_ends_once(self):
        self.start_animation()
        self.controller.cancel_anim()
        self.controller.cancel_anim()
        self.assertEqual(self.frame(), ["OutputSilence", "EndAnimation"])
        self.assertNotIn("EndAnimation", self.frame())

    def test_a_frame_held_back_for_room_goes_out_before_the_end_of_its_animation(self):
        # The robot has played nothing yet, and has room for a few bytes more.
        controller = self.controller
        controller.num_audio_frames_played = 0
        controller._played_time = time.perf_counter()
        controller._frames_sent = 1
        controller._unplayed.append((0, controller.MAX_BYTES_AHEAD - 10))
        controller._unplayed_bytes = controller.MAX_BYTES_AHEAD - 10
        controller.play_anim_frame(self.audio, None, (pycozmo.protocol_encoder.StartAnimation(anim_id=1),))
        self.assertEqual(self.frame(), [])
        controller.cancel_anim()
        # Room is made.
        controller.num_audio_frames_played = 1
        self.assertEqual([kind for kind in self.frame() if kind != "DisplayImage"], ["OutputAudio", "StartAnimation"])
        self.assertEqual(self.frame(), ["OutputSilence", "EndAnimation"])

    def test_without_a_frame_loop_it_is_sent_at_once(self):
        self.controller.thread = None
        self.controller.cancel_anim()
        self.assertEqual(self.kinds(), ["EndAnimation"])


class FakeRobot:
    """
    The robot's animation buffer, as measured on a robot: 8 KB, each message taking its length and 3 bytes,
    played a frame at a time 29.906 times a second. Everything travels with a delay each way.

    When a message does not fit, the robot clears its buffer and counts what it drops as played.
    """

    CAPACITY = 8192
    FRAME_TIME = 1 / 29.906

    def __init__(self, latency: float) -> None:
        self.latency = latency
        self.in_transit: Deque[Tuple[float, pycozmo.protocol_base.Packet]] = deque()
        self.buffer: Deque[List[pycozmo.protocol_base.Packet]] = deque()
        self.buffered = 0
        self.frames_played = 0
        self.bytes_played = 0
        self.next_play: Optional[float] = None
        self.reports: Deque[Tuple[float, int, int]] = deque()
        self.overflows = 0
        self.starved = 0
        # When each audio frame was played.
        self.audio: List[float] = []

    def receive(self, now: float, pkt: pycozmo.protocol_base.Packet) -> None:
        self.in_transit.append((now + self.latency, pkt))

    def step(self, now: float) -> None:
        while self.in_transit and self.in_transit[0][0] <= now:
            _, pkt = self.in_transit.popleft()
            if self.buffered + len(pkt) + 3 > self.CAPACITY:
                self.overflows += 1
                self.frames_played += len(self.buffer)
                self.bytes_played += sum(len(p) + 1 for frame in self.buffer for p in frame)
                self.buffer.clear()
                self.buffered = 0
                continue
            if isinstance(pkt, (pycozmo.protocol_encoder.OutputAudio, pycozmo.protocol_encoder.OutputSilence)):
                self.buffer.append([])
            self.buffer[-1].append(pkt)
            self.buffered += len(pkt) + 3
        if self.next_play is None and self.buffer:
            self.next_play = now
        while self.next_play is not None and self.next_play <= now:
            if self.buffer:
                frame = self.buffer.popleft()
                self.buffered -= sum(len(p) + 3 for p in frame)
                self.frames_played += 1
                self.bytes_played += sum(len(p) + 1 for p in frame)
                if isinstance(frame[0], pycozmo.protocol_encoder.OutputAudio):
                    self.audio.append(self.next_play)
            elif self.audio and len(self.audio) < 900:
                # Nothing to play in the middle of a sound.
                self.starved += 1
            self.next_play += self.FRAME_TIME
        self.reports.append((now + self.latency, self.frames_played, self.bytes_played))

    def reported(self, now: float) -> List[pycozmo.protocol_encoder.AnimationState]:
        states = []
        while self.reports and self.reports[0][0] <= now:
            _, frames, played = self.reports.popleft()
            states.append(pycozmo.protocol_encoder.AnimationState(
                num_anim_bytes_played=played, num_audio_frames_played=frames))
        return states


class TestFlowControl(unittest.TestCase):
    """ Frames go out as the robot plays them, not 30 times a second whatever it makes of them. """

    SOUND = 900

    def setUp(self):
        self.cli = pycozmo.client.Client()
        self.now = 100.0
        self.robot = FakeRobot(latency=0.03)
        for name, side_effect in (("send", lambda pkt: self.robot.receive(self.now, pkt)), ("post_event", None)):
            patcher = mock.patch.object(self.cli.conn, name, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = mock.patch("pycozmo.anim_controller.time.perf_counter", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.controller = self.cli.anim_controller
        self.controller.enable_animations(True)
        # The face would be drawn anew for every frame, and it is not what is being tested.
        self.controller.enable_procedural_face(False)
        self.controller.thread = mock.Mock()

    def run_for(self, seconds, report=True):
        """ Run the frame loop and the robot, a frame at a time, for that long. """
        for _ in range(round(seconds * 30)):
            self.now += 1 / 30
            self.robot.step(self.now)
            for state in self.robot.reported(self.now):
                if report:
                    self.controller._on_animation_state(self.cli.conn, state)
            self.controller._send_frames()

    def play_sound(self):
        self.controller.play_audio([pycozmo.protocol_encoder.OutputAudio(samples=bytes(744))] * self.SOUND)
        return self.now

    def test_a_sound_after_a_long_silence_fits(self):
        # Silences piled up ahead of the robot two minutes long, then gave way to audio frames the buffer
        # could not hold.
        self.run_for(120)
        self.play_sound()
        self.run_for(40)
        self.assertEqual(self.robot.overflows, 0)
        self.assertEqual(len(self.robot.audio), self.SOUND)

    def test_a_sound_starts_as_soon_after_ten_minutes_as_after_ten_seconds(self):
        # The silences piling up ahead of the robot delayed whatever came next by five frames more each
        # minute: two seconds after ten minutes.
        delays = []
        for idle in (10, 600):
            self.run_for(idle)
            queued = self.play_sound()
            self.run_for(40)
            delays.append(self.robot.audio[-self.SOUND] - queued)
        self.assertLess(delays[1], 0.5)
        self.assertAlmostEqual(delays[0], delays[1], delta=0.05)

    def test_a_sound_plays_through_without_a_gap(self):
        self.run_for(10)
        self.play_sound()
        self.run_for(40)
        self.assertEqual(self.robot.starved, 0)
        audio = self.robot.audio
        self.assertAlmostEqual(audio[-1] - audio[0], (self.SOUND - 1) * FakeRobot.FRAME_TIME, delta=0.001)

    def test_the_buffer_is_kept_full_enough_to_ride_out_a_hiccup(self):
        self.run_for(10)
        self.play_sound()
        self.run_for(5)
        # Eight audio frames are waiting, a quarter of a second of sound. Two more are on their way.
        self.assertGreaterEqual(self.robot.buffered, 8 * 747)

    def test_a_robot_that_does_not_report_gets_a_frame_a_tick(self):
        self.run_for(10, report=False)
        self.assertEqual(self.controller._frames_sent, 300)

    def test_a_robot_that_stops_reporting_gets_a_frame_a_tick_after_a_while(self):
        self.run_for(10)
        played = self.controller.num_audio_frames_played
        assert played is not None
        self.run_for(0.9, report=False)
        self.assertEqual(self.controller._frames_sent - played, self.controller.MAX_FRAMES_AHEAD)
        sent = self.controller._frames_sent
        self.run_for(1.0, report=False)
        self.assertAlmostEqual(self.controller._frames_sent - sent, 30 - 3, delta=1)
