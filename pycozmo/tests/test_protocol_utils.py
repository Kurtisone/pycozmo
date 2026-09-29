import unittest

from pycozmo import protocol_encoder


class TestByteArrays(unittest.TestCase):

    def test_come_back_as_bytes_whatever_their_length(self):
        # The last chunk of an image can hold a single byte, or none.
        for data in (b"", b"\x05", b"\x05\x06"):
            pkt = protocol_encoder.ImageChunk(image_chunk_count=1, data=data)
            decoded = protocol_encoder.ImageChunk.from_bytes(pkt.to_bytes())
            self.assertEqual(decoded.data, data)
            self.assertIsInstance(decoded.data, (bytes, bytearray))
