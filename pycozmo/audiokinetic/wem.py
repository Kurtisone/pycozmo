"""

AudioKinetic WWise WEM file representation and reading.

A WEM file is a RIFF/WAVE container holding audio in one of WWise's own codecs. Cozmo ships two of
them, in cozmo_resources/sound:

- IMA ADPCM, declared with the WAVE_FORMAT_ADPCM tag. WWise reuses the tag but not Microsoft's
  layout: there is no coefficient table in the format chunk and each block carries a four byte
  header per channel, which is the IMA state rather than Microsoft's seven byte one. Generic
  decoders reject it for that reason. 227 of the 469 files in the sound directory use it, and they
  are the sounds the animations reach for most often - the screen, the servos, the blinks.
- Vorbis, declared with tag 0xFFFF. WWise strips the Vorbis setup header out of the stream and
  keeps the codebooks in its own sound engine, which shipped inside the Cozmo application rather
  than in these resources - there is not one codebook anywhere under cozmo_resources. Those files
  cannot be decoded from what the robot's own resources hold, so they are reported as unsupported.

References:
    - https://en.wikipedia.org/wiki/Interactive_Multimedia_Association
    - https://wiki.multimedia.cx/index.php/IMA_ADPCM

"""

import struct
from typing import Dict, List, Optional, Tuple

from . import exception


__all__ = [
    "ADPCM",
    "VORBIS",

    "Wem",

    "load_wem",
]


#: Format tag of the IMA ADPCM files, borrowed from WAVE_FORMAT_ADPCM.
ADPCM = 0x0002
#: Format tag of the Vorbis files, which WWise ships without their codebooks.
VORBIS = 0xFFFF

#: Bytes of IMA state at the start of each block, per channel: predictor, step index, padding.
ADPCM_HEADER_SIZE = 4

#: IMA step sizes. The step index walks this table and scales the difference each nibble codes for.
STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
    279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166,
    1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428,
    4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289,
    16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)

#: How the step index moves for each of the eight magnitudes a nibble can code for.
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)


class Wem:
    """ A WEM audio file. """

    __slots__ = [
        "format",
        "channels",
        "sample_rate",
        "byte_rate",
        "block_align",
        "bits_per_sample",
        "data",
    ]

    def __init__(self,
                 audio_format: int,
                 channels: int,
                 sample_rate: int,
                 byte_rate: int,
                 block_align: int,
                 bits_per_sample: int,
                 data: bytes) -> None:
        self.format = int(audio_format)
        self.channels = int(channels)
        self.sample_rate = int(sample_rate)
        self.byte_rate = int(byte_rate)
        self.block_align = int(block_align)
        self.bits_per_sample = int(bits_per_sample)
        self.data = data

    @classmethod
    def from_bytes(cls, buf: bytes) -> "Wem":
        """ Read a WEM file from memory. """
        if len(buf) < 12 or buf[0:4] != b"RIFF" or buf[8:12] != b"WAVE":
            raise exception.AudioKineticFormatError("Not a RIFF/WAVE file.")
        chunks = cls._read_chunks(buf)
        if "fmt " not in chunks:
            raise exception.AudioKineticFormatError("WEM file has no format chunk.")
        off, size = chunks["fmt "]
        if size < 16:
            raise exception.AudioKineticFormatError("WEM format chunk is {} bytes.".format(size))
        audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample = \
            struct.unpack_from("<HHIIHH", buf, off)
        data_off, data_size = chunks.get("data", (0, 0))
        return cls(audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample,
                   buf[data_off:data_off + data_size])

    @classmethod
    def from_file(cls, fspec: str) -> "Wem":
        """ Read a WEM file from disk. """
        with open(fspec, "rb") as f:
            return cls.from_bytes(f.read())

    @staticmethod
    def _read_chunks(buf: bytes) -> Dict[str, Tuple[int, int]]:
        """ The offset and length of each RIFF chunk, by tag. """
        chunks = {}
        pos = 12
        while pos + 8 <= len(buf):
            tag = buf[pos:pos + 4].decode("latin1")
            size = struct.unpack_from("<I", buf, pos + 4)[0]
            chunks[tag] = (pos + 8, min(size, len(buf) - pos - 8))
            # Chunks are padded to an even length.
            pos += 8 + size + (size & 1)
        return chunks

    @property
    def is_supported(self) -> bool:
        """ Whether decode() can read this file. """
        return self.format == ADPCM

    @property
    def samples_per_block(self) -> int:
        """ How many samples per channel each block of the ADPCM data holds. """
        data_bytes = self.block_align - ADPCM_HEADER_SIZE * self.channels
        return data_bytes * 2 // self.channels

    def decode(self) -> List[int]:
        """
        Decode to 16 bit samples, interleaved by channel.

        Raises AudioKineticFormatError for the Vorbis files, whose codebooks are not in the
        resources. See the module docstring.
        """
        if self.format == VORBIS:
            raise exception.AudioKineticFormatError(
                "WEM file is WWise Vorbis, whose codebooks ship inside the Cozmo application "
                "rather than in the robot's resources.")
        if self.format != ADPCM:
            raise exception.AudioKineticFormatError(
                "Unsupported WEM format 0x{:04x}.".format(self.format))
        if self.channels < 1 or self.block_align <= ADPCM_HEADER_SIZE * self.channels:
            raise exception.AudioKineticFormatError(
                "WEM file has {} channels in a {} byte block.".format(self.channels, self.block_align))
        return self._decode_adpcm()

    def _decode_adpcm(self) -> List[int]:
        out: List[int] = []
        step = self.block_align
        for start in range(0, len(self.data) - step + 1, step):
            block = self.data[start:start + step]
            channels = []
            for channel in range(self.channels):
                predictor, index, _ = struct.unpack_from("<hBB", block, channel * ADPCM_HEADER_SIZE)
                channels.append([predictor, min(max(index, 0), len(STEP_TABLE) - 1)])
            payload = block[ADPCM_HEADER_SIZE * self.channels:]
            decoded = [self._decode_channel(payload, ch, channels[ch])
                       for ch in range(self.channels)]
            # Interleave the channels back together.
            for i in range(len(decoded[0])):
                for ch in range(self.channels):
                    out.append(decoded[ch][i])
        return out

    def _decode_channel(self, payload: bytes, channel: int, state: List[int]) -> List[int]:
        """
        Decode one channel's nibbles out of a block's payload.

        A mono block is simply every nibble in order, low nibble of each byte first. A stereo block
        interleaves the two channels in four byte groups, which is how WWise writes them.
        """
        predictor, index = state
        samples = []
        for byte in self._channel_bytes(payload, channel):
            for nibble in (byte & 0x0F, byte >> 4):
                step = STEP_TABLE[index]
                diff = step >> 3
                if nibble & 1:
                    diff += step >> 2
                if nibble & 2:
                    diff += step >> 1
                if nibble & 4:
                    diff += step
                predictor += -diff if nibble & 8 else diff
                predictor = min(max(predictor, -32768), 32767)
                index = min(max(index + INDEX_TABLE[nibble & 7], 0), len(STEP_TABLE) - 1)
                samples.append(predictor)
        return samples

    def _channel_bytes(self, payload: bytes, channel: int) -> bytes:
        if self.channels == 1:
            return payload
        out = bytearray()
        group = 4
        for start in range(channel * group, len(payload), group * self.channels):
            out += payload[start:start + group]
        return bytes(out)


def load_wem(fspec: str) -> Optional[Wem]:
    """ Read a WEM file, or None if it cannot be read at all. """
    try:
        return Wem.from_file(fspec)
    except (OSError, exception.AudioKineticFormatError):
        return None
