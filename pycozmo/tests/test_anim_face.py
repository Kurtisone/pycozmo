"""

Tests for face animations: the image sequences some animations show instead of a procedural face.

They were not read at all, so anim_bored_event_02 and anim_bored_event_04 - a swinging clock and a
slot machine - played their sound over a face that stayed still, or black.

"""

import os
import tempfile
import unittest
from typing import List

import numpy as np
from PIL import Image

import pycozmo
from pycozmo import anim, anim_encoder, image_encoder, protocol_encoder, robot

from .test_brain import cozmo_assets_available


def decoded(pkt: protocol_encoder.DisplayImage) -> np.ndarray:
    """ What the screen shows for an image, as a 32 x 128 array of booleans. """
    decoder = image_encoder.ImageDecoder(pkt.image)
    decoder.decode()
    return np.array(decoder.image, dtype=bool).reshape(32, 128)


class FaceAnimationDir:
    """ A face animation directory, drawn the way Anki drew them: 128 x 64 greyscale, lines doubled. """

    def __init__(self, frames: List[np.ndarray]) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "face_test")
        os.mkdir(self.path)
        # Written out of order, to show the order comes from the names.
        for i in reversed(range(len(frames))):
            doubled = np.repeat(frames[i], 2, axis=0)
            Image.fromarray(doubled, "L").save(os.path.join(self.path, "face_test_{:05d}.png".format(i)))

    def cleanup(self) -> None:
        self.tmp.cleanup()


def bar(row: int, value: int = 255) -> np.ndarray:
    """ A 32 x 128 frame with one lit line. """
    frame = np.zeros((32, 128), dtype=np.uint8)
    frame[row, 10:100] = value
    return frame


class TestLoading(unittest.TestCase):

    def setUp(self):
        self.dir = FaceAnimationDir([bar(3), bar(15), bar(28)])
        self.addCleanup(self.dir.cleanup)

    def test_one_image_per_file_in_name_order(self):
        images = anim.load_face_animation(self.dir.path)
        self.assertEqual(len(images), 3)
        for image, row in zip(images, (3, 15, 28)):
            shown = decoded(image)
            self.assertTrue(shown[row, 10:100].all())
            self.assertEqual(shown.sum(), 90, "one line of 128 x 64 is one line on the screen")

    def test_half_grey_and_up_is_lit(self):
        tmp = FaceAnimationDir([bar(5, 127), bar(5, 128)])
        self.addCleanup(tmp.cleanup)
        dim, lit = anim.load_face_animation(tmp.path)
        self.assertEqual(decoded(dim).sum(), 0)
        self.assertEqual(decoded(lit).sum(), 90)

    def test_a_missing_animation_is_empty(self):
        self.assertEqual(anim.load_face_animation(os.path.join(self.dir.tmp.name, "nothing")), [])


class TestInClips(unittest.TestCase):

    def setUp(self):
        self.dir = FaceAnimationDir([bar(3), bar(15), bar(28)])
        self.addCleanup(self.dir.cleanup)
        self.face_dir = os.path.dirname(self.dir.path)

    def images(self, keyframes, face_dir=None):
        clip = anim_encoder.AnimClip("test", keyframes)
        ppclip = anim.PreprocessedClip.from_anim_clip(clip, face_animation_dir=face_dir)
        return {time_ms: [p for p in pkts if isinstance(p, protocol_encoder.DisplayImage)]
                for time_ms, pkts in ppclip.keyframes.items()}

    def test_one_image_per_frame_from_the_trigger(self):
        laid = self.images([anim_encoder.AnimFaceAnimation(100, "face_test")], self.face_dir)
        self.assertEqual(sorted(laid), [100, 100 + robot.FRAME_MS, 100 + 2 * robot.FRAME_MS])

    def test_without_a_directory_nothing_shows(self):
        self.assertEqual(self.images([anim_encoder.AnimFaceAnimation(0, "face_test")]), {})

    def test_the_image_sequence_shows_over_a_procedural_face(self):
        face = anim_encoder.AnimProceduralFace(trigger_time_ms=0, scale_x=1.0, scale_y=1.0,
                                               left_eye=[0.0] * 19, right_eye=[0.0] * 19)
        laid = self.images([anim_encoder.AnimFaceAnimation(0, "face_test"), face], self.face_dir)
        # The procedural face was laid first on that frame; the last image laid is the one shown.
        self.assertEqual(len(laid[0]), 2)
        self.assertTrue(decoded(laid[0][-1])[3, 10:100].all())


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAgainstCozmoAssets(unittest.TestCase):

    def test_the_two_animations_that_show_one(self):
        cli = pycozmo.client.Client()
        cli.load_anims()
        assert cli.face_animation_dir is not None
        for name, count in (("anim_bored_event_02", 151), ("anim_bored_event_04", 401)):
            with self.subTest(name=name):
                cli._load_clips(cli._clip_metadata[name].fspec)
                clip = cli._clips[name]
                faces = [k for k in clip.keyframes if isinstance(k, anim_encoder.AnimFaceAnimation)]
                images = anim.load_face_animation(os.path.join(cli.face_animation_dir, faces[0].anim_name))
                self.assertEqual(len(images), count)
                self.assertGreater(sum(decoded(image).any() for image in images), count * 0.85)
