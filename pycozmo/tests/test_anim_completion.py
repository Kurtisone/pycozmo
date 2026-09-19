import unittest
from unittest import mock

import pycozmo


class AnimationTestCase(unittest.TestCase):

    def setUp(self):
        # A client that is never started, with the connection stubbed out.
        self.cli = pycozmo.client.Client()
        self.controller = self.cli.anim_controller
        self.post_event = self.patch(self.cli.conn, "post_event")
        self.send = self.patch(self.cli.conn, "send")

    def patch(self, target, attribute):
        patcher = mock.patch.object(target, attribute)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def posted(self):
        """ The events posted, in order. """
        return [call.args[0] for call in self.post_event.call_args_list]


class TestAnimationCompletion(AnimationTestCase):
    """
    A completion has to belong to the animation that is playing.

    EndAnimation carries no identifier, so the robot answers with the identifier of whatever was
    actually playing. Cancelling one animation to start another therefore produces an end for the
    old one, which used to be passed on and taken by the new behavior for its own.
    """

    def end(self, anim_id):
        self.controller._on_animation_ended(
            self.cli.conn, pycozmo.protocol_encoder.AnimationEnded(anim_id=anim_id))

    def test_the_expected_animation_completes(self):
        self.controller.expect_anim(7)
        self.end(7)
        self.assertEqual(self.posted(), [pycozmo.event.EvtAnimationCompleted])

    def test_another_animation_does_not(self):
        self.controller.expect_anim(8)
        self.end(7)
        self.assertEqual(self.posted(), [])

    def test_nothing_is_expected_by_default(self):
        self.end(7)
        self.assertEqual(self.posted(), [])

    def test_a_completion_is_reported_once(self):
        self.controller.expect_anim(7)
        self.end(7)
        self.end(7)
        self.assertEqual(self.posted(), [pycozmo.event.EvtAnimationCompleted])

    def test_cancelling_drops_the_expectation(self):
        # A behavior cancels its animation when it is deactivated. The end that comes back belongs
        # to an animation nobody is waiting for any more.
        self.controller.expect_anim(7)
        self.controller.cancel_anim()
        self.end(7)
        self.assertEqual(self.posted(), [])

    def start(self, anim_id):
        """ The robot acknowledging an animation it has started. """
        self.controller._on_animation_started(
            self.cli.conn, pycozmo.protocol_encoder.AnimationStarted(anim_id=anim_id))

    def test_an_animation_the_robot_acknowledges_completes(self):
        # pycozmo is a protocol library: an application may send StartAnimation itself rather than
        # going through play_anim(). The robot acknowledges whatever it starts, and that answer is
        # what the end is matched against, so such an animation still completes.
        self.start(3)
        self.end(3)
        self.assertEqual(self.posted(), [pycozmo.event.EvtAnimationCompleted])

    def test_the_acknowledgement_wins_over_a_stale_expectation(self):
        self.controller.expect_anim(7)
        self.start(3)
        self.end(3)
        self.assertEqual(self.posted(), [pycozmo.event.EvtAnimationCompleted])

    def test_the_animation_started_after_a_cancel_still_completes(self):
        self.controller.expect_anim(7)
        self.controller.cancel_anim()
        self.controller.expect_anim(8)
        self.end(7)
        self.assertEqual(self.posted(), [], "the cancelled animation")
        self.end(8)
        self.assertEqual(self.posted(), [pycozmo.event.EvtAnimationCompleted])


class TestAnimationId(AnimationTestCase):
    """ The identifier is a uint8 on the wire, so it has to wrap rather than overflow. """

    def setUp(self):
        super().setUp()
        self.play_anim_frame = self.patch(self.controller, "play_anim_frame")

    def play(self, times):
        """ Play an empty clip a number of times, and return the identifiers it started with. """
        for _ in range(times):
            self.cli.play_anim_ppclip(pycozmo.anim.PreprocessedClip())
        started = []
        for call in self.play_anim_frame.call_args_list:
            for pkt in call.args[2] or ():
                if isinstance(pkt, pycozmo.protocol_encoder.StartAnimation):
                    started.append(pkt.anim_id)
        return started

    def test_ids_stay_in_range(self):
        # Incrementing without wrapping used to raise on the 255th animation of a session, when
        # StartAnimation refused a value its uint8 field cannot hold.
        ids = self.play(600)
        self.assertEqual(len(ids), 600)
        self.assertEqual(min(ids), 1)
        self.assertEqual(max(ids), 255)

    def test_consecutive_ids_differ(self):
        # Telling one animation from the next is the whole point of the identifier.
        ids = self.play(600)
        for first, second in zip(ids, ids[1:]):
            self.assertNotEqual(first, second)

    def test_the_animation_being_played_is_the_one_expected(self):
        ids = self.play(1)
        self.assertEqual(self.controller.expected_anim_id, ids[-1])

    def test_starting_an_animation_cancels_the_one_before(self):
        self.play(2)
        self.assertEqual(
            sum(1 for call in self.send.call_args_list
                if isinstance(call.args[0], pycozmo.protocol_encoder.EndAnimation)), 2)


class TestScreenImage(AnimationTestCase):
    """
    An image already on the robot's screen is not sent again thirty times a second.

    The robot keeps the last image and only blanks it after DISPLAY_BLANKING_TIME, so resending an
    identical one is only needed to beat that deadline. The procedural face is redrawn on every
    frame but comes out different about nine times a second, so two thirds of these packets used to
    carry an image that was already on the screen.
    """

    def image(self, payload):
        return pycozmo.protocol_encoder.DisplayImage(image=payload)

    def sent_images(self):
        return [call.args[0].image for call in self.send.call_args_list
                if isinstance(call.args[0], pycozmo.protocol_encoder.DisplayImage)]

    def test_the_first_image_is_sent(self):
        self.assertTrue(self.controller._send_image(self.image(b"aa"), 0.0))
        self.assertEqual(self.sent_images(), [b"aa"])

    def test_an_identical_image_is_not_sent_again(self):
        self.controller._send_image(self.image(b"aa"), 0.0)
        for frame in range(1, 30):
            with self.subTest(frame=frame):
                self.assertFalse(self.controller._send_image(self.image(b"aa"), frame / 30.0))
        self.assertEqual(self.sent_images(), [b"aa"])

    def test_a_changed_image_is_sent(self):
        self.controller._send_image(self.image(b"aa"), 0.0)
        self.assertTrue(self.controller._send_image(self.image(b"bb"), 0.1))
        self.assertEqual(self.sent_images(), [b"aa", b"bb"])

    def test_an_identical_image_is_refreshed_before_the_screen_blanks(self):
        self.controller._send_image(self.image(b"aa"), 0.0)
        self.assertLess(pycozmo.robot.DISPLAY_REFRESH_TIME, pycozmo.robot.DISPLAY_BLANKING_TIME,
                        "the refresh has to beat the blanking deadline")
        just_before = pycozmo.robot.DISPLAY_REFRESH_TIME - 0.001
        self.assertFalse(self.controller._send_image(self.image(b"aa"), just_before))
        self.assertTrue(
            self.controller._send_image(self.image(b"aa"), pycozmo.robot.DISPLAY_REFRESH_TIME))
        self.assertEqual(self.sent_images(), [b"aa", b"aa"])

    def test_a_static_screen_is_sent_about_once_per_refresh(self):
        # Thirty frames a second for a minute, with nothing ever changing.
        sent = sum(1 for frame in range(30 * 60)
                   if self.controller._send_image(self.image(b"aa"), frame / 30.0))
        expected = 60.0 / pycozmo.robot.DISPLAY_REFRESH_TIME
        self.assertAlmostEqual(sent, expected, delta=1)
        self.assertLess(sent, 30 * 60 / 10, "the whole point is to send far fewer")

    def test_starting_forgets_what_was_on_the_screen(self):
        # A robot just connected to may be showing anything, so the first image always goes out.
        self.controller._send_image(self.image(b"aa"), 0.0)
        self.patch(self.controller, "_run")
        self.controller.start()
        self.addCleanup(setattr, self.controller, "stop_flag", True)
        self.assertIsNone(self.controller.displayed_image)
        self.assertIsNone(self.controller.expected_anim_id)
        self.assertTrue(self.controller._send_image(self.image(b"aa"), 0.1))
