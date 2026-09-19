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
