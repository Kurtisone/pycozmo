import io
import unittest

import numpy as np
from PIL import Image

import pycozmo
from pycozmo import camera


def payload(length=200, fill=0x55, color=0):
    """ Build a minimized JPEG payload: the colour flag byte followed by entropy data. """
    return np.array([color] + [fill] * length, dtype=np.uint8)


class TestMiniToJpeg(unittest.TestCase):

    def test_gray_is_decodable(self):
        data = camera.minigray_to_jpeg(payload(), 320, 240).tobytes()
        im = Image.open(io.BytesIO(data))
        im.load()
        self.assertEqual(im.size, (320, 240))

    def test_color_is_decodable(self):
        # Colour frames arrive at half width and are stretched back afterwards.
        data = camera.minicolor_to_jpeg(payload(color=1), 160, 240).tobytes()
        im = Image.open(io.BytesIO(data))
        im.load()
        self.assertEqual(im.size, (160, 240))

    def test_markers(self):
        data = camera.minigray_to_jpeg(payload(), 320, 240).tobytes()
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertIn(b"\xff\xd9", data)

    def test_dimensions_are_embedded(self):
        for width, height in ((320, 240), (160, 120), (40, 30)):
            data = camera.minigray_to_jpeg(payload(), width, height).tobytes()
            self.assertEqual((data[0x5e] << 8) | data[0x5f], height)
            self.assertEqual((data[0x60] << 8) | data[0x61], width)

    def test_byte_stuffing(self):
        # A 0xff in the entropy data has to be followed by 0x00, or a decoder reads it as a marker.
        data = camera.minigray_to_jpeg(np.array([0, 0xff, 0x11], dtype=np.uint8), 8, 8).tobytes()
        self.assertIn(b"\xff\x00\x11", data)

    def test_flag_byte_is_dropped(self):
        # The first byte says whether the frame is in colour and is not part of the image data.
        first = camera.minigray_to_jpeg(np.array([0, 0x11, 0x22], dtype=np.uint8), 8, 8).tobytes()
        second = camera.minigray_to_jpeg(np.array([1, 0x11, 0x22], dtype=np.uint8), 8, 8).tobytes()
        self.assertEqual(first, second)


class TestProcessCompletedImage(unittest.TestCase):

    def setUp(self):
        self.cli = pycozmo.Client()
        self.addCleanup(self.cli.conn.sock.close)

    def _run(self, data, resolution, color):
        self.cli._reset_partial_state()
        self.cli._partial_data = data
        self.cli._partial_size = len(data)
        self.cli._partial_image_encoding = pycozmo.protocol_encoder.ImageEncoding.JPEGMinimizedGray
        self.cli._partial_image_resolution = resolution
        self.cli._partial_image_timestamp = 1234
        received = []
        self.cli.add_handler(pycozmo.event.EvtNewRawCameraImage, lambda cli, im: received.append(im))
        self.cli._process_completed_image()
        return received

    def test_gray_frame(self):
        received = self._run(payload(color=0), pycozmo.protocol_encoder.ImageResolution.QVGA, False)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].size, (320, 240))
        self.assertEqual(received[0].mode, "RGB")
        self.assertEqual(self.cli.last_image_timestamp, 1234)

    def test_color_frame_is_resized(self):
        # Colour frames are encoded at half width and stretched back to the reported resolution.
        received = self._run(payload(color=1), pycozmo.protocol_encoder.ImageResolution.QVGA, True)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].size, (320, 240))

    def test_latest_image_is_recorded(self):
        # _latest_image has no public accessor and is not initialised in the constructor, so application code
        # reads frames through EvtNewRawCameraImage instead. Asserted on the attribute it is actually stored in.
        self._run(payload(), pycozmo.protocol_encoder.ImageResolution.QQVGA, False)
        self.assertIsNotNone(self.cli._latest_image)
