import unittest
import io

from pycozmo.audiokinetic._chunk import Chunk


class TestChunk(unittest.TestCase):

    def test_header(self):
        f = io.BytesIO(b"BKHD" + b"\x04\x00\x00\x00" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=False, align=False)
        self.assertEqual(chunk.getname(), b"BKHD")
        self.assertEqual(chunk.getsize(), 4)

    def test_bigendian(self):
        f = io.BytesIO(b"FORM" + b"\x00\x00\x00\x04" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=True, align=False)
        self.assertEqual(chunk.getsize(), 4)

    def test_inclheader(self):
        f = io.BytesIO(b"FORM" + b"\x00\x00\x00\x0c" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=True, align=False, inclheader=True)
        self.assertEqual(chunk.getsize(), 4)

    def test_read(self):
        f = io.BytesIO(b"DIDX" + b"\x04\x00\x00\x00" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=False, align=False)
        self.assertEqual(chunk.read(2), b"\x01\x02")
        self.assertEqual(chunk.tell(), 2)
        self.assertEqual(chunk.read(), b"\x03\x04")
        self.assertEqual(chunk.tell(), 4)

    def test_read_past_end(self):
        f = io.BytesIO(b"DIDX" + b"\x02\x00\x00\x00" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=False, align=False)
        # Reads are clamped to the chunk size and never reach into the next chunk.
        self.assertEqual(chunk.read(100), b"\x01\x02")
        self.assertEqual(chunk.read(1), b"")

    def test_lazy(self):
        # Chunk data must be left in the stream. Reading the DATA chunk of a SoundBank relies on the stream being
        # positioned right after the chunk header.
        f = io.BytesIO(b"DATA" + b"\x08\x00\x00\x00" + b"\x01\x02\x03\x04\x05\x06\x07\x08")
        chunk = Chunk(f, bigendian=False, align=False)
        self.assertEqual(f.tell(), 8)
        chunk.skip()
        self.assertEqual(f.tell(), 16)

    def test_skip(self):
        f = io.BytesIO(b"HIRC" + b"\x04\x00\x00\x00" + b"\x01\x02\x03\x04" + b"STMG" + b"\x00\x00\x00\x00")
        chunk = Chunk(f, bigendian=False, align=False)
        chunk.skip()
        chunk = Chunk(f, bigendian=False, align=False)
        self.assertEqual(chunk.getname(), b"STMG")

    def test_skip_after_partial_read(self):
        f = io.BytesIO(b"HIRC" + b"\x04\x00\x00\x00" + b"\x01\x02\x03\x04" + b"STMG" + b"\x00\x00\x00\x00")
        chunk = Chunk(f, bigendian=False, align=False)
        chunk.read(1)
        chunk.skip()
        chunk = Chunk(f, bigendian=False, align=False)
        self.assertEqual(chunk.getname(), b"STMG")

    def test_align(self):
        f = io.BytesIO(b"ENVS" + b"\x01\x00\x00\x00" + b"\x01" + b"\x00" + b"PLAT" + b"\x00\x00\x00\x00")
        chunk = Chunk(f, bigendian=False, align=True)
        self.assertEqual(chunk.read(), b"\x01")
        # The pad byte following an odd-sized chunk is consumed.
        chunk = Chunk(f, bigendian=False, align=False)
        self.assertEqual(chunk.getname(), b"PLAT")

    def test_no_align(self):
        f = io.BytesIO(b"ENVS" + b"\x01\x00\x00\x00" + b"\x01" + b"PLAT" + b"\x00\x00\x00\x00")
        chunk = Chunk(f, bigendian=False, align=False)
        chunk.skip()
        chunk = Chunk(f, bigendian=False, align=False)
        self.assertEqual(chunk.getname(), b"PLAT")

    def test_seek(self):
        f = io.BytesIO(b"DIDX" + b"\x04\x00\x00\x00" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=False, align=False)
        chunk.seek(2)
        self.assertEqual(chunk.read(), b"\x03\x04")
        chunk.seek(0)
        self.assertEqual(chunk.read(1), b"\x01")
        chunk.seek(1, 1)
        self.assertEqual(chunk.read(1), b"\x03")
        chunk.seek(-1, 2)
        self.assertEqual(chunk.read(), b"\x04")

    def test_seek_outside(self):
        f = io.BytesIO(b"DIDX" + b"\x04\x00\x00\x00" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=False, align=False)
        with self.assertRaises(RuntimeError):
            chunk.seek(5)
        with self.assertRaises(RuntimeError):
            chunk.seek(-1)

    def test_eof(self):
        # An empty stream marks the end of the chunk sequence.
        with self.assertRaises(EOFError):
            Chunk(io.BytesIO(b""), bigendian=False, align=False)

    def test_truncated_name(self):
        with self.assertRaises(EOFError):
            Chunk(io.BytesIO(b"DID"), bigendian=False, align=False)

    def test_truncated_size(self):
        with self.assertRaises(EOFError):
            Chunk(io.BytesIO(b"DIDX" + b"\x04\x00"), bigendian=False, align=False)

    def test_close(self):
        f = io.BytesIO(b"DIDX" + b"\x04\x00\x00\x00" + b"\x01\x02\x03\x04")
        chunk = Chunk(f, bigendian=False, align=False)
        chunk.close()
        self.assertEqual(f.tell(), 12)
        with self.assertRaises(ValueError):
            chunk.read()
        # Closing twice is harmless.
        chunk.close()
