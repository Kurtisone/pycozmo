import unittest

from PIL import Image

import pycozmo
from pycozmo.procedural_face import DEFAULT_WIDTH, DEFAULT_HEIGHT, ProceduralFace, interpolate


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
