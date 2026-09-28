import unittest
from typing import List
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

    def test_without_a_frame_loop_it_is_sent_at_once(self):
        self.controller.thread = None
        self.controller.cancel_anim()
        self.assertEqual(self.kinds(), ["EndAnimation"])
