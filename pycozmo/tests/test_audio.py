"""

Tests for the robot's audio: the U-law encoding, the WEM decoder, and the sound the animations play.

"""

import glob
import os
import struct
import unittest

import pycozmo
from pycozmo.audiokinetic import exception as ak_exception, wem


def cozmo_assets_available():
    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        return False
    return True


def u_law_decode(byte: int) -> int:
    """ Reference U-law decoder, to check the encoder against. """
    byte = ~byte & 0xFF
    sign = byte & 0x80
    exponent = (byte >> 4) & 0x07
    mantissa = byte & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


class TestULaw(unittest.TestCase):
    """
    The encoder used to negate the complement instead of masking it, which is the same as adding
    one to the uncomplemented byte. The samples came out as noise and 0xFF overflowed a bytearray.
    """

    def test_every_sample_encodes_to_a_byte(self):
        for sample in range(-32768, 32768, 37):
            with self.subTest(sample=sample):
                self.assertIn(pycozmo.audio.u_law_encoding(sample), range(256))

    def test_a_round_trip_keeps_the_sample(self):
        # U-law is lossy and coarse at high amplitude, so allow 4% of full scale.
        for sample in range(-32000, 32000, 101):
            with self.subTest(sample=sample):
                decoded = u_law_decode(pycozmo.audio.u_law_encoding(sample))
                self.assertLess(abs(decoded - sample), 0.04 * 32768)

    def test_the_sign_survives(self):
        for sample in (-30000, -1000, -100, 100, 1000, 30000):
            with self.subTest(sample=sample):
                decoded = u_law_decode(pycozmo.audio.u_law_encoding(sample))
                self.assertEqual(decoded < 0, sample < 0)

    def test_silence_is_not_a_nought_byte(self):
        # Once the encoder complements properly, a nought byte is very nearly full scale negative,
        # so a frame padded with noughts clicks. Silence is 0xFF.
        self.assertEqual(pycozmo.audio.u_law_encoding(0), pycozmo.audio.SILENCE)
        self.assertEqual(pycozmo.audio.SILENCE, 0xFF)
        self.assertLess(u_law_decode(0), -30000)

    def test_a_short_frame_is_padded_with_silence(self):
        frame = pycozmo.audio.bytes_to_cozmo(struct.pack("<3h", 0, 1000, -1000), 1, 1)
        self.assertEqual(len(frame), 744)
        self.assertEqual(set(frame[3:]), {pycozmo.audio.SILENCE})

    def test_a_ramp_stays_monotonic(self):
        # Noise rather than audio was what the old encoder produced; a monotonic input has to come
        # back monotonic.
        decoded = [u_law_decode(pycozmo.audio.u_law_encoding(s)) for s in range(0, 32000, 250)]
        self.assertEqual(decoded, sorted(decoded))


class TestWem(unittest.TestCase):

    @staticmethod
    def make_adpcm(blocks, channels=1, sample_rate=48000):
        """ Build a WEM file in memory, with the geometry Cozmo's ADPCM files use. """
        block_align = wem.ADPCM_HEADER_SIZE * channels + 32 * channels
        fmt = struct.pack("<HHIIHH", wem.ADPCM, channels, sample_rate,
                          block_align * sample_rate // 64, block_align, 4)
        data = b"".join(blocks)
        body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + \
               b"data" + struct.pack("<I", len(data)) + data
        return b"RIFF" + struct.pack("<I", len(body)) + body

    def test_a_block_holds_sixty_four_samples(self):
        block = struct.pack("<hBB", 0, 0, 0) + bytes(32)
        media = wem.Wem.from_bytes(self.make_adpcm([block]))
        self.assertEqual(media.samples_per_block, 64)
        self.assertEqual(len(media.decode()), 64)

    def test_silence_stays_near_zero(self):
        # Nibble 0 is the smallest positive step, so a run of them drifts but only slowly.
        block = struct.pack("<hBB", 0, 0, 0) + bytes(32)
        samples = wem.Wem.from_bytes(self.make_adpcm([block])).decode()
        self.assertLess(max(abs(s) for s in samples), 100)

    def test_the_block_header_sets_the_starting_point(self):
        block = struct.pack("<hBB", 12345, 0, 0) + bytes(32)
        samples = wem.Wem.from_bytes(self.make_adpcm([block])).decode()
        # The first sample is the header's predictor plus one step, the smallest one there is.
        self.assertAlmostEqual(samples[0], 12345, delta=2)

    def test_a_stereo_block_interleaves_its_channels(self):
        # Each channel's nibbles come in four byte groups, alternating. Channel 0 is given rising
        # nibbles and channel 1 falling ones, so the two must come out different.
        header = struct.pack("<hBB", 0, 20, 0) * 2
        payload = bytearray()
        # A stereo block is 72 bytes: 4 of header per channel, then 32 of nibbles per channel.
        for group in range(8):
            payload += bytes([0x11] * 4)     # canal 0
            payload += bytes([0x99] * 4)     # canal 1
        block = header + bytes(payload)
        media = wem.Wem.from_bytes(self.make_adpcm([block], channels=2))
        samples = media.decode()
        self.assertEqual(len(samples), 64 * 2)
        left, right = samples[0::2], samples[1::2]
        self.assertGreater(left[-1], 0, "channel 0 codes rising steps")
        self.assertLess(right[-1], 0, "channel 1 codes falling steps")

    def test_vorbis_is_reported_rather_than_guessed_at(self):
        fmt = struct.pack("<HHIIHH", wem.VORBIS, 1, 48000, 9824, 0, 0)
        body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 0)
        media = wem.Wem.from_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
        self.assertFalse(media.is_supported)
        with self.assertRaises(ak_exception.AudioKineticFormatError):
            media.decode()

    def test_a_file_that_is_not_riff_is_refused(self):
        with self.assertRaises(ak_exception.AudioKineticFormatError):
            wem.Wem.from_bytes(b"not a riff file at all")


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAgainstCozmoAssets(unittest.TestCase):
    """ Check the decoder and the sound library against the resources they read. """

    resource_dir: str
    sound_dir: str
    library: pycozmo.audiolib.AudioLibrary

    @classmethod
    def setUpClass(cls):
        cls.resource_dir = str(pycozmo.util.get_cozmo_asset_dir())
        cls.sound_dir = os.path.join(cls.resource_dir, "cozmo_resources", "sound")
        cls.library = pycozmo.audiolib.load_audio_library(cls.resource_dir)

    def test_every_adpcm_file_decodes_to_the_expected_length(self):
        # The byte rate in the format chunk implies 64 samples per block, which is how the layout
        # was settled: a four byte IMA header per channel and every remaining nibble a sample.
        checked = 0
        for fspec in sorted(glob.glob(os.path.join(self.sound_dir, "*.wem"))):
            media = wem.Wem.from_file(fspec)
            if not media.is_supported:
                continue
            expected = len(media.data) // media.block_align * media.samples_per_block * media.channels
            self.assertEqual(len(media.decode()), expected, os.path.basename(fspec))
            checked += 1
        self.assertGreater(checked, 200, "the ADPCM files should be there")

    def test_the_sound_banks_load(self):
        # Cozmo's own bank is the one that holds the events its animations name, and it is the one
        # bank that is not unpacked on disk - it is read out of AudioAssets.zip.
        self.assertGreater(len(self.library.events), 800)
        self.assertGreater(len(self.library.containers), 500)
        self.assertGreater(len(self.library.files), 2000)

    def test_a_named_event_reaches_its_own_sound(self):
        # Play__Robot_SFX__Bored_Pendulum, which NothingToDo_BoredAnim plays.
        frames = self.library.get_frames(62303530)
        self.assertTrue(frames)
        for frame in frames:
            self.assertEqual(len(frame.samples), pycozmo.audiolib.FRAME_SAMPLES)

    def test_an_unknown_event_is_silent_rather_than_fatal(self):
        self.assertEqual(self.library.get_frames(1), [])

    def test_the_animations_carry_sound(self):
        cli = pycozmo.client.Client(auto_initialize=False)
        cli.load_anims()
        with_audio = 0
        for name in ("anim_bored_02", "anim_hiccup_01", "anim_hiking_getin_01"):
            if name not in cli.get_anim_names():
                continue
            cli._load_clips(cli._clip_metadata[name].fspec)
            clip = pycozmo.anim.PreprocessedClip.from_anim_clip(cli._clips[name], cli.audio_library)
            audio_frames = [pkt for pkts in clip.keyframes.values() for pkt in pkts
                            if isinstance(pkt, pycozmo.protocol_encoder.OutputAudio)]
            if audio_frames:
                with_audio += 1
        self.assertTrue(with_audio, "no animation produced any sound")

    def test_an_animation_is_silent_without_a_library(self):
        # Which is what every animation did before there was one.
        cli = pycozmo.client.Client(auto_initialize=False)
        cli.load_anims()
        cli._load_clips(cli._clip_metadata["anim_bored_02"].fspec)
        clip = pycozmo.anim.PreprocessedClip.from_anim_clip(cli._clips["anim_bored_02"])
        audio_frames = [pkt for pkts in clip.keyframes.values() for pkt in pkts
                        if isinstance(pkt, pycozmo.protocol_encoder.OutputAudio)]
        self.assertEqual(audio_frames, [])
