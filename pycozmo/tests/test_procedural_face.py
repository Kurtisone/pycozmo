import unittest

from PIL import Image

import pycozmo
from pycozmo.procedural_face import DEFAULT_WIDTH, DEFAULT_HEIGHT, ProceduralFace, interpolate


def cozmo_assets_available():
    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        return False
    return True


class TestProceduralFace(unittest.TestCase):

    def test_render_shape(self):
        im = ProceduralFace().render()
        self.assertIsInstance(im, Image.Image)
        self.assertEqual(im.size, (DEFAULT_WIDTH, DEFAULT_HEIGHT))
        self.assertEqual(im.mode, "1")

    def test_render_is_not_blank(self):
        # A face that draws nothing would still have the right shape, so check that something was drawn.
        # getbbox() is None for an image with no set pixels.
        self.assertIsNotNone(ProceduralFace().render().getbbox())

    def test_expressions_differ(self):
        # Distinct expressions have to produce distinct images, or the face parameters are not reaching the render.
        rendered = {}
        for name in ("Neutral", "Anger", "Sadness", "Happiness", "Surprise"):
            face = getattr(pycozmo.expressions, name)()
            rendered[name] = face.render().tobytes()
        self.assertEqual(len(set(rendered.values())), len(rendered))

    def test_small_scale_does_not_raise(self):
        # Pillow rejects a resize to zero in either axis. The renderer catches that and composes nothing rather
        # than propagating the error.
        for scale in (0.5, 0.01, 0.001, 0.0):
            face = ProceduralFace()
            face.scale_x = scale
            face.scale_y = scale
            im = face.render()
            self.assertEqual(im.size, (DEFAULT_WIDTH, DEFAULT_HEIGHT))

    def test_zero_scale_renders_nothing(self):
        face = ProceduralFace()
        face.scale_x = 0.0
        face.scale_y = 0.0
        self.assertIsNone(face.render().getbbox())

    def test_rotation(self):
        face = ProceduralFace()
        face.angle = 45.0
        im = face.render()
        self.assertEqual(im.size, (DEFAULT_WIDTH, DEFAULT_HEIGHT))
        self.assertNotEqual(im.tobytes(), ProceduralFace().render().tobytes())

    def test_deterministic(self):
        # The same parameters have to render the same image, which is what makes a visual regression detectable.
        self.assertEqual(ProceduralFace().render().tobytes(), ProceduralFace().render().tobytes())

    def test_interpolate(self):
        frames = list(interpolate(pycozmo.expressions.Neutral(), pycozmo.expressions.Happiness(), 4))
        self.assertEqual(len(frames), 4)
        for frame in frames:
            self.assertIsInstance(frame, ProceduralFace)
            self.assertEqual(frame.render().size, (DEFAULT_WIDTH, DEFAULT_HEIGHT))

    def test_a_negative_lid_bend_does_not_raise(self):
        # anim_bored_02 carries one. Pillow used to normalise a bounding box passed with its low and
        # high corners swapped; current Pillow raises ValueError instead, and a negative bend swaps
        # the chord's box exactly that way. The chord's shape does not depend on which corner was
        # passed first - only on the box's actual extremes - so the fix changes nothing about what
        # gets drawn.
        face = ProceduralFace()
        face.eyes[0].lids[0].bend = -1.0
        im = face.render()
        self.assertEqual(im.size, (DEFAULT_WIDTH, DEFAULT_HEIGHT))

    def test_bend_sign_does_not_change_the_render(self):
        # The chord's bounding box is the same set of extremes either way, so the two must render
        # identically - this failing would mean the fix silently changed the face's appearance.
        positive = ProceduralFace()
        positive.eyes[0].lids[0].bend = 0.7
        negative = ProceduralFace()
        negative.eyes[0].lids[0].bend = -0.7
        self.assertEqual(positive.render().tobytes(), negative.render().tobytes())

    @unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
    def test_every_animation_renders(self):
        # Sweep every animation clip Anki ships through the same rendering path a behavior playing it
        # would take. This is what caught anim_bored_02 crashing the heartbeat thread: nothing in the
        # unit tests below exercises resource data, only hand-built ProceduralFace instances.
        cli = pycozmo.client.Client(auto_initialize=False)
        cli.load_anims()
        failures = []
        for name in sorted(cli.get_anim_names()):
            if name not in cli._clips:
                cli._load_clips(cli._clip_metadata[name].fspec)
            clip = cli._clips[name]
            try:
                pycozmo.anim.PreprocessedClip.from_anim_clip(clip)
            except Exception as e:
                failures.append("{}: {}: {}".format(name, type(e).__name__, e))
        self.assertEqual(failures, [])
