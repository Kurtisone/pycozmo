"""

Tests for reading WWise Vorbis and putting it back into an Ogg container.

WWise takes a Vorbis stream apart and keeps only what its own decoder cannot work out again, so
playing one means rebuilding the parts it threw away: the identification and comment headers, the
codebooks its setup header only points at, the bits that say an audio packet is an audio packet, and
the Ogg container around the lot. Each of those is checked here against the format it has to match
rather than against the code that writes it - the codebook expansion is read back by a reader written
from the Vorbis specification, and the Ogg checksum against the value the CRC catalogue publishes.

"""

import os
import struct
import unittest

import pycozmo
from pycozmo.audiokinetic import codebooks, exception, ogg, vorbis, wem
from pycozmo.audiokinetic.bits import BitReader, BitWriter, ilog


def find_engine():
    """ The sound engine holding the codebooks, if this machine has a copy. """
    fspec = os.environ.get("PYCOZMO_SOUND_ENGINE")
    if fspec and os.path.exists(fspec):
        return fspec
    # The application, when it has been left at the top of a working copy.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    fspec = os.path.join(root, "com.anki.cozmo.apk")
    return fspec if os.path.exists(fspec) else None


def make_wem(ext_fields=(), data=b"", channels=1, sample_rate=32000):
    """ A WWise Vorbis WEM whose format extension says what a test wants it to. """
    ext = bytearray(vorbis.EXT_SIZE)
    sample_count, setup_offset, first_audio_offset, pow_0, pow_1 = ext_fields
    struct.pack_into("<I", ext, 6, sample_count)
    struct.pack_into("<I", ext, 22, setup_offset)
    struct.pack_into("<I", ext, 26, first_audio_offset)
    ext[46], ext[47] = pow_0, pow_1
    return wem.Wem(wem.VORBIS, channels, sample_rate, 4000, 0, 0, bytes(ext), data)


class TestBits(unittest.TestCase):

    def test_ilog(self):
        # From the Vorbis specification: nought for anything below one, then the bits it takes.
        self.assertEqual([0, 0, 1, 2, 2, 3, 3, 3, 3, 4], [ilog(i) for i in range(-1, 9)])

    def test_a_field_comes_back_as_it_went_in(self):
        writer = BitWriter()
        for value, width in ((1, 1), (0, 1), (0x3f, 6), (0x1234, 16), (5, 3), (0xffffffff, 32)):
            writer.write(value, width)
        reader = BitReader(writer.to_bytes())
        self.assertEqual([1, 0, 0x3f, 0x1234, 5, 0xffffffff],
                         [reader.read(width) for width in (1, 1, 6, 16, 3, 32)])

    def test_bytes_written_whole_match_bytes_written_one_at_a_time(self):
        # write_bytes() exists only to be faster, so it has to agree with the slow way exactly.
        for offset in range(0, 17):
            payload = bytes(range(offset, offset + 11))
            quick, slow = BitWriter(), BitWriter()
            quick.write(offset, offset)
            slow.write(offset, offset)
            quick.write_bytes(payload)
            for byte in payload:
                slow.write(byte, 8)
            self.assertEqual(slow.to_bytes(), quick.to_bytes(), "at offset {}".format(offset))
            self.assertEqual(slow.pos, quick.pos)

    def test_a_stream_is_spent_once_only_padding_is_left(self):
        reader = BitReader(b"\xff\xff")
        self.assertFalse(reader.at_end)
        reader.read(8)
        self.assertTrue(reader.at_end)
        reader.read(7)
        self.assertTrue(reader.at_end)

    def test_reading_past_the_end_is_an_error(self):
        with self.assertRaises(EOFError):
            BitReader(b"\x01").read(9)


class TestOgg(unittest.TestCase):

    def test_the_checksum_matches_the_published_one(self):
        # Ogg uses CRC-32/CKSUM without its final inversion, so the catalogue's check value for
        # "123456789", 0x765e7680, inverts to this.
        self.assertEqual(0x89a1897f, ogg.crc32(b"123456789"))
        self.assertEqual(0, ogg.crc32(b""))

    def test_a_packet_is_laced_as_the_format_says(self):
        # RFC 3533: a packet is counted out in 255s and always ends on a value below 255, so one of
        # exactly 255 bytes needs a nought after it to say it has finished.
        self.assertEqual([0], ogg._segments(0))
        self.assertEqual([7], ogg._segments(7))
        self.assertEqual([255, 0], ogg._segments(255))
        self.assertEqual([255, 45], ogg._segments(300))

    def pages(self, data):
        """ Walk a bitstream, checking every page's checksum, and return the headers. """
        out = []
        pos = 0
        while pos < len(data):
            self.assertEqual(b"OggS", data[pos:pos + 4])
            count = data[pos + 26]
            length = sum(data[pos + 27:pos + 27 + count])
            end = pos + 27 + count + length
            page = bytearray(data[pos:end])
            stated, = struct.unpack_from("<I", page, 22)
            page[22:26] = b"\x00\x00\x00\x00"
            self.assertEqual(stated, ogg.crc32(bytes(page)), "checksum of page at {}".format(pos))
            _, _, flags, granule, serial, sequence = struct.unpack_from("<4sBBqII", page, 0)
            out.append((flags, granule, serial, sequence))
            pos = end
        return out

    def test_a_stream_is_framed_as_the_format_says(self):
        writer = ogg.OggWriter(serial=42)
        writer.add(b"first", 0, end_page=True)
        writer.add(b"second")
        writer.add(b"third", 700, end_page=True)
        writer.add(b"fourth", 1400)
        pages = self.pages(writer.to_bytes())

        self.assertEqual(3, len(pages))
        self.assertEqual([0, 1, 2], [page[3] for page in pages])
        self.assertEqual([42, 42, 42], [page[2] for page in pages])
        self.assertEqual(0x02, pages[0][0] & 0x02, "the first page begins the stream")
        self.assertEqual(0x04, pages[-1][0] & 0x04, "the last page ends it")
        self.assertEqual([0, 700, 1400], [page[1] for page in pages])

    def test_packets_share_a_page_until_it_is_full(self):
        writer = ogg.OggWriter()
        for i in range(300):
            writer.add(b"x" * 10, i)
        pages = self.pages(writer.to_bytes())
        # 255 one segment packets fill a page, so the rest go on a second.
        self.assertEqual(2, len(pages))
        self.assertEqual([254, 299], [page[1] for page in pages])

    def test_a_packet_too_big_for_a_page_is_refused(self):
        writer = ogg.OggWriter()
        writer.add(b"x" * (255 * 255))
        with self.assertRaises(ValueError):
            writer.to_bytes()


class TestCodebooks(unittest.TestCase):

    def test_how_many_multiplicands_a_codebook_holds(self):
        # From the Vorbis specification: the largest n whose n**dimensions fits in the entries.
        self.assertEqual(8, codebooks.lookup1_values(64, 2))
        self.assertEqual(8, codebooks.lookup1_values(80, 2))
        self.assertEqual(3, codebooks.lookup1_values(27, 3))
        self.assertEqual(2, codebooks.lookup1_values(26, 3))

    @staticmethod
    def packed_codebook(lengths=(1, 3, 3, 2), multiplicands=(4, 9)):
        """ A codebook in WWise's form: narrower fields and no sync pattern. """
        out = BitWriter()
        out.write(2, 4)                                 # dimensions
        out.write(len(lengths), 14)                     # entries
        out.write(0, 1)                                 # not ordered
        out.write(3, 3)                                 # lengths are three bits each
        out.write(0, 1)                                 # not sparse
        for length in lengths:
            out.write(length, 3)
        out.write(1, 1)                                 # lookup type one
        out.write(0x1000, 32)                           # minimum value
        out.write(0x2000, 32)                           # delta value
        out.write(3, 4)                                 # values are four bits each
        out.write(0, 1)                                 # not a sequence
        for value in multiplicands:
            out.write(value, 4)
        return out.to_bytes()

    def test_expanding_a_codebook_widens_every_field(self):
        lengths = (1, 3, 3, 2)
        library = codebooks.CodebookLibrary(self.packed_codebook(lengths), [0, 99])
        out = BitWriter()
        library.expand(0, out)

        # Read it back against the Vorbis specification rather than against the writer.
        got = BitReader(out.to_bytes())
        self.assertEqual(codebooks.SYNC_PATTERN, got.read(24))
        self.assertEqual(2, got.read(16), "dimensions widen from 4 bits to 16")
        self.assertEqual(len(lengths), got.read(24), "entries widen from 14 bits to 24")
        self.assertEqual(0, got.read(1), "not ordered")
        self.assertEqual(0, got.read(1), "not sparse")
        self.assertEqual(list(lengths), [got.read(5) for _ in lengths], "lengths widen to 5 bits")
        self.assertEqual(1, got.read(4), "the lookup type widens from 1 bit to 4")
        self.assertEqual(0x1000, got.read(32))
        self.assertEqual(0x2000, got.read(32))
        self.assertEqual(3, got.read(4))
        self.assertEqual(0, got.read(1))
        self.assertEqual([4, 9], [got.read(4) for _ in range(2)], "two multiplicands for 4 entries")
        self.assertTrue(got.at_end, "nothing left over")

    def test_a_codebook_that_does_not_fill_its_room_is_refused(self):
        library = codebooks.CodebookLibrary(self.packed_codebook() + b"\x00\x00\x00", [0, 99])
        with self.assertRaises(exception.AudioKineticFormatError):
            library.expand(0, BitWriter())

    def test_asking_for_a_codebook_that_is_not_there(self):
        library = codebooks.CodebookLibrary(self.packed_codebook(), [0, 99])
        self.assertEqual(1, len(library))
        with self.assertRaises(exception.AudioKineticFormatError):
            library.packed(1)

    def test_no_library_in_something_that_is_not_a_sound_engine(self):
        with self.assertRaises(exception.AudioKineticFormatError):
            codebooks.find_library(bytes(range(256)) * 400)


class TestWwiseVorbisHeader(unittest.TestCase):

    def test_the_stream_is_read_out_of_the_format_extension(self):
        stream = vorbis.WwiseVorbis(make_wem((45753, 100, 265, 8, 11), b"\x00" * 400))
        self.assertEqual(45753, stream.sample_count)
        self.assertEqual(100, stream.setup_offset)
        self.assertEqual(265, stream.first_audio_offset)
        self.assertEqual((8, 11), (stream.blocksize_0_pow, stream.blocksize_1_pow))

    def test_the_identification_header_says_what_the_format_chunk_said(self):
        stream = vorbis.WwiseVorbis(make_wem((100, 10, 20, 8, 11), b"\x00" * 400,
                                             channels=2, sample_rate=32000))
        header = stream.identification_header()
        self.assertEqual(30, len(header), "the identification header is a fixed 30 bytes")
        self.assertEqual(b"\x01vorbis", header[:7])
        self.assertEqual(0, struct.unpack_from("<I", header, 7)[0], "Vorbis version")
        self.assertEqual(2, header[11])
        self.assertEqual(32000, struct.unpack_from("<I", header, 12)[0])
        self.assertEqual(8 | (11 << 4), header[28], "both block sizes in one byte")
        self.assertEqual(1, header[29], "the framing bit")

    def test_the_comment_header_is_well_formed(self):
        header = vorbis.WwiseVorbis.comment_header()
        self.assertEqual(b"\x03vorbis", header[:7])
        length = struct.unpack_from("<I", header, 7)[0]
        self.assertEqual(b"pycozmo", header[11:11 + length])
        self.assertEqual(0, struct.unpack_from("<I", header, 11 + length)[0], "no comments")
        self.assertEqual(1, header[15 + length], "the framing bit")

    def test_a_file_that_is_not_wwise_vorbis_is_refused(self):
        media = wem.Wem(wem.ADPCM, 1, 22050, 22050, 36, 4, b"", b"")
        with self.assertRaises(exception.AudioKineticFormatError):
            vorbis.WwiseVorbis(media)

    def test_impossible_block_sizes_are_refused(self):
        with self.assertRaises(exception.AudioKineticFormatError):
            vorbis.WwiseVorbis(make_wem((100, 10, 20, 11, 8), b"\x00" * 400))

    def test_packets_past_the_end_of_the_data_are_refused(self):
        with self.assertRaises(exception.AudioKineticFormatError):
            vorbis.WwiseVorbis(make_wem((100, 5000, 20, 8, 11), b"\x00" * 400))

    def test_a_short_format_extension_is_refused(self):
        media = wem.Wem(wem.VORBIS, 1, 32000, 4000, 0, 0, b"\x00" * 20, b"")
        with self.assertRaises(exception.AudioKineticFormatError):
            vorbis.WwiseVorbis(media)


class TestAgainstCozmoAssets(unittest.TestCase):
    """ The robot's own sounds, which is what all of this is for. """

    sound_dir: str
    paths: list

    @classmethod
    def setUpClass(cls):
        try:
            pycozmo.util.check_assets()
        except Exception as e:
            raise unittest.SkipTest(str(e))
        cls.sound_dir = os.path.join(str(pycozmo.util.get_cozmo_asset_dir()),
                                     "cozmo_resources", "sound")
        cls.paths = []
        for root, _, names in os.walk(cls.sound_dir):
            for name in sorted(names):
                stem, ext = os.path.splitext(name)
                if ext == ".wem" and stem.isdigit():
                    cls.paths.append(os.path.join(root, name))
        if not cls.paths:
            raise unittest.SkipTest("No sound media in {}.".format(cls.sound_dir))

    def vorbis_paths(self, limit=None):
        out = []
        for path in self.paths:
            media = wem.load_wem(path)
            if media is not None and media.format == wem.VORBIS:
                out.append((path, media))
                if limit and len(out) >= limit:
                    break
        return out

    def test_every_vorbis_file_describes_itself_consistently(self):
        block_sizes = set()
        found = 0
        for path, media in self.vorbis_paths():
            with self.subTest(path=os.path.basename(path)):
                stream = vorbis.WwiseVorbis(media)
                self.assertGreater(stream.sample_count, 0)
                self.assertLess(stream.setup_offset, stream.first_audio_offset)
                self.assertLessEqual(stream.first_audio_offset, len(media.data))
                block_sizes.add((stream.blocksize_0_pow, stream.blocksize_1_pow))
            found += 1
        self.assertGreater(found, 0, "the resources hold WWise Vorbis")
        # Anki built these sounds with two settings and no others.
        self.assertEqual({(8, 11), (9, 10)}, block_sizes)

    def test_the_sounds_rebuild_into_a_well_formed_ogg_stream(self):
        engine = find_engine()
        if engine is None:
            self.skipTest("No Cozmo sound engine to take the codebooks from. Set "
                          "PYCOZMO_SOUND_ENGINE to the application or to libcozmoEngine.so .")
        with open(engine, "rb") as f:
            buf = f.read()
        if buf[:2] == b"PK":
            import zipfile
            with zipfile.ZipFile(engine) as archive:
                member = next(name for name in archive.namelist()
                              if os.path.basename(name) == "libcozmoEngine.so")
                with archive.open(member) as f:
                    buf = f.read()
        library = codebooks.find_library(buf)
        self.assertGreater(len(library), 500, "the library holds hundreds of codebooks")

        checker = TestOgg()
        for path, media in self.vorbis_paths(limit=8):
            with self.subTest(path=os.path.basename(path)):
                stream = vorbis.WwiseVorbis(media)
                data = stream.to_ogg(library)
                pages = checker.pages(data)
                self.assertGreater(len(pages), 2)
                self.assertEqual(0x02, pages[0][0] & 0x02)
                self.assertEqual(0x04, pages[-1][0] & 0x04)
                # The stream says it is as long as the file says it is.
                self.assertEqual(stream.sample_count, pages[-1][1])

    def test_converted_sounds_are_used_in_place_of_the_files_they_replace(self):
        converted = str(pycozmo.util.get_converted_sound_dir())
        if not os.path.isdir(converted) or not os.listdir(converted):
            self.skipTest("Nothing converted yet. See tools/pycozmo_convert_audio.py .")
        library = pycozmo.audiolib.load_audio_library(str(pycozmo.util.get_cozmo_asset_dir()))
        self.assertTrue(library.converted, "the converted sounds were found")
        # A file PyCozmo cannot decode on its own plays once it has been converted.
        file_id = next(iter(library.converted))
        self.assertTrue(library.is_playable(file_id))
        samples, channels, rate = library._get_pcm(file_id)
        self.assertGreater(len(samples), 0)
        self.assertEqual(1, channels)
        self.assertEqual(pycozmo.audiolib.SAMPLE_RATE, rate)


if __name__ == "__main__":
    unittest.main()
