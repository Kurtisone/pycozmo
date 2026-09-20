"""

Tests for laying an animation's keyframes onto the robot's frame grid.

The robot plays one frame every 33 ms, so Client.play_anim_ppclip() has to turn keyframe times
into frame positions. It used to add each gap to a running total that already counted the frame it
had just sent, so every keyframe cost two frames instead of one: animations ran at about half speed
and a sound came out one frame on, one frame off, which is audible as a stutter. These tests pin
the mapping down.

"""

import unittest
from unittest import mock

import pycozmo
from pycozmo import protocol_encoder, robot


def audio(value: int) -> protocol_encoder.OutputAudio:
    return protocol_encoder.OutputAudio(samples=bytes([value]) * 744)


def image(value: int) -> protocol_encoder.DisplayImage:
    return protocol_encoder.DisplayImage(image=bytes([value, value]))


class PacingTestCase(unittest.TestCase):

    def setUp(self):
        # A client that is never started, with the connection stubbed out.
        self.cli = pycozmo.client.Client()
        for attribute in ("send", "post_event"):
            patcher = mock.patch.object(self.cli.conn, attribute)
            self.addCleanup(patcher.stop)
            patcher.start()

    def play(self, keyframes):
        """ The frames one clip lays down, as (audio, image, packets) triples. """
        with mock.patch.object(self.cli.anim_controller, "play_anim_frame") as played:
            self.cli.play_anim_ppclip(pycozmo.anim.PreprocessedClip(keyframes=keyframes))
        # The first frame carries StartAnimation and the last EndAnimation; neither is part of the
        # clip's own timeline.
        return [call.args for call in played.call_args_list][1:-1]

    def audio_frames(self, frames):
        """ Which frames carry sound, as a string of dots and hashes. """
        return "".join("#" if audio_pkt else "." for audio_pkt, _, _ in frames)


class TestFrameGrid(PacingTestCase):

    def test_consecutive_keyframes_take_one_frame_each(self):
        frames = self.play({0: [audio(1)], 33: [audio(2)], 66: [audio(3)]})
        self.assertEqual("###", self.audio_frames(frames))

    def test_a_gap_is_filled_with_empty_frames(self):
        # 132 ms is four frames on, so three empty frames come between.
        frames = self.play({0: [audio(1)], 132: [audio(2)]})
        self.assertEqual("#...#", self.audio_frames(frames))

    def test_a_clip_lasts_as_long_as_its_keyframes_say(self):
        keyframes = {i * robot.FRAME_MS: [image(i & 0x3f)] for i in range(90)}
        self.assertEqual(90, len(self.play(keyframes)))

    def test_an_empty_clip_plays_no_frames(self):
        self.assertEqual([], self.play({}))


class TestOffGridKeyframes(PacingTestCase):
    """
    3 % of the keyframes in Cozmo's own resources do not sit on the 33 ms grid.

    Each one belongs to the frame it is nearest, so rounding never accumulates - anim_bored_01
    triggers its sound at 272 ms, which is 8 ms past frame 8.
    """

    def test_a_keyframe_goes_to_its_nearest_frame(self):
        # 272 ms is nearest frame 8, and 289 ms nearest frame 9.
        self.assertEqual("........#", self.audio_frames(self.play({0: [image(1)], 272: [audio(1)]})))
        self.assertEqual(".........#", self.audio_frames(self.play({0: [image(1)], 289: [audio(1)]})))

    def test_rounding_does_not_accumulate(self):
        # Sound laid from an off-grid trigger, one frame every 33 ms as the resources do it. The
        # last of the 30 lands at 272 + 29 * 33 = 1229 ms, which is frame 37.
        keyframes = {272 + i * robot.FRAME_MS: [audio(i & 0x3f)] for i in range(30)}
        frames = self.play(keyframes)
        self.assertEqual(38, len(frames))
        self.assertEqual("." * 8 + "#" * 30, self.audio_frames(frames))

    def test_an_off_grid_keyframe_does_not_push_the_ones_after_it(self):
        # An off-grid sound running alongside on-grid faces. Sharing frames rather than taking one
        # each is what keeps the clip its own length.
        keyframes: dict = {}
        for i in range(30):
            keyframes.setdefault(i * robot.FRAME_MS, []).append(image(i & 0x3f))
            keyframes.setdefault(272 + i * robot.FRAME_MS, []).append(audio(i & 0x3f))
        frames = self.play(keyframes)
        self.assertEqual(38, len(frames))


class TestSharedFrames(PacingTestCase):
    """ Two keyframes rounding to the same frame share it, as the robot's one slot per frame does. """

    def test_the_last_sound_on_a_frame_is_the_one_heard(self):
        # 264 ms and 272 ms are both nearest frame 8.
        frames = self.play({264: [audio(1)], 272: [audio(2)]})
        self.assertEqual(9, len(frames))
        self.assertEqual(audio(2).samples, frames[8][0].samples)

    def test_everything_else_on_a_shared_frame_still_goes_out(self):
        turn = protocol_encoder.AnimHead(duration_ms=33)
        lift = protocol_encoder.AnimLift(duration_ms=33)
        frames = self.play({264: [turn], 272: [lift]})
        self.assertEqual([turn, lift], list(frames[8][2]))


class TestAgainstCozmoAssets(PacingTestCase):
    """ The pacing against the robot's own animations, which is what the stutter was heard in. """

    cli: pycozmo.client.Client

    @classmethod
    def setUpClass(cls):
        try:
            pycozmo.util.check_assets()
        except Exception as e:
            raise unittest.SkipTest(str(e))

    def clip(self, name):
        import os
        anim_dir = str(pycozmo.util.get_cozmo_anim_dir())
        metadata = pycozmo.anim_encoder.get_clip_metadata(anim_dir)
        self.assertIn(name, metadata)
        clips = pycozmo.anim_encoder.AnimClips.from_fb_file(
            os.path.join(anim_dir, os.path.basename(metadata[name].fspec))).clips
        return next(clip for clip in clips if clip.name == name)

    def test_an_animation_plays_for_as_long_as_it_says(self):
        clip = self.clip("anim_bored_02")
        ppclip = pycozmo.anim.PreprocessedClip.from_anim_clip(clip)
        nominal = max(ppclip.keyframes) + robot.FRAME_MS
        frames = self.play(ppclip.keyframes)
        # Within one frame of the length its last keyframe implies.
        self.assertAlmostEqual(nominal / robot.FRAME_MS, len(frames), delta=1)

    def test_an_animation_sound_comes_out_unbroken(self):
        library = pycozmo.audiolib.load_audio_library(str(pycozmo.util.get_cozmo_asset_dir()))
        if not library.events:
            self.skipTest("No sound banks in the resources.")
        clip = self.clip("anim_bored_02")
        ppclip = pycozmo.anim.PreprocessedClip.from_anim_clip(clip, library)
        sound = self.audio_frames(self.play(ppclip.keyframes))
        self.assertIn("#", sound)
        # No single frame of sound sitting alone between two silences, which is what the stutter
        # was: the sound of this animation is one unbroken run.
        self.assertNotIn(".#.", "." + sound + ".")


if __name__ == "__main__":
    unittest.main()
