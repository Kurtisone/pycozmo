import unittest

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
