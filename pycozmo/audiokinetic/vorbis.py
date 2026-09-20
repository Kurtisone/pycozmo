"""

WWise Vorbis, and turning it back into Ogg Vorbis.

WWise does not store playable Vorbis. It takes a Vorbis stream apart and keeps only what its own
decoder cannot work out for itself:

- The identification and comment headers are gone. Everything they hold - channel count, sample
  rate, block sizes - sits in the WEM's format chunk instead, so they can be written afresh.
- The setup header is kept, but stripped: its codebooks are replaced by 10 bit indices into the
  library in WWise's sound engine, the time domain transforms and the constant fields of the
  floors, mappings and modes are dropped, and the sync pattern goes with them. What is left is the
  Vorbis syntax field for field, which is why it can be copied straight back out into a wider form.
- Each audio packet loses the bit saying it is an audio packet and, on a long block, the two bits
  saying which windows it joins. The window bits depend on the packets either side, so they have to
  be worked out again from the modes of the neighbours.
- The Ogg container is gone entirely. Packets sit back to back, each behind its 16 bit length.

Rebuilding all of that gives a file any Vorbis decoder will read. It needs the codebook library,
which is not in the robot's resources - see pycozmo.audiokinetic.codebooks .

References:
    - https://xiph.org/vorbis/doc/Vorbis_I_spec.html
    - https://github.com/hcs64/ww2ogg - the original reading of this format

"""

import struct
from typing import List, Tuple

from . import exception
from . import ogg
from . import wem
from .bits import BitReader, BitWriter, ilog
from .codebooks import CodebookLibrary


__all__ = [
    "WwiseVorbis",

    "to_ogg",
]


#: Where WWise's description of the stream sits inside the format chunk's extension. It is what
#: used to be a "vorb" chunk of its own, and the extension is exactly long enough to hold it.
_SAMPLE_COUNT = 6
_SETUP_OFFSET = 22
_FIRST_AUDIO_OFFSET = 26
_BLOCKSIZE_0_POW = 46
_BLOCKSIZE_1_POW = 47
EXT_SIZE = 48


class WwiseVorbis:
    """ The Vorbis stream a WEM file holds. """

    __slots__ = [
        "channels",
        "sample_rate",
        "byte_rate",
        "sample_count",
        "setup_offset",
        "first_audio_offset",
        "blocksize_0_pow",
        "blocksize_1_pow",
        "data",
    ]

    def __init__(self, media: wem.Wem) -> None:
        if media.format != wem.VORBIS:
            raise exception.AudioKineticFormatError(
                "WEM format 0x{:04x} is not WWise Vorbis.".format(media.format))
        if len(media.ext) < EXT_SIZE:
            raise exception.AudioKineticFormatError(
                "WWise Vorbis needs {} bytes of format extension, not {}.".format(
                    EXT_SIZE, len(media.ext)))
        self.channels = media.channels
        self.sample_rate = media.sample_rate
        self.byte_rate = media.byte_rate
        self.sample_count, = struct.unpack_from("<I", media.ext, _SAMPLE_COUNT)
        self.setup_offset, = struct.unpack_from("<I", media.ext, _SETUP_OFFSET)
        self.first_audio_offset, = struct.unpack_from("<I", media.ext, _FIRST_AUDIO_OFFSET)
        self.blocksize_0_pow = media.ext[_BLOCKSIZE_0_POW]
        self.blocksize_1_pow = media.ext[_BLOCKSIZE_1_POW]
        self.data = media.data
        if not 6 <= self.blocksize_0_pow <= self.blocksize_1_pow <= 13:
            raise exception.AudioKineticFormatError(
                "WWise Vorbis block sizes 2^{} and 2^{}.".format(
                    self.blocksize_0_pow, self.blocksize_1_pow))
        if self.setup_offset >= len(self.data) or self.first_audio_offset > len(self.data):
            raise exception.AudioKineticFormatError("WWise Vorbis packets sit past the data.")

    def _packet(self, offset: int) -> bytes:
        """ One packet, which WWise puts behind its 16 bit length. """
        size, = struct.unpack_from("<H", self.data, offset)
        body = self.data[offset + 2:offset + 2 + size]
        if len(body) != size:
            raise exception.AudioKineticFormatError(
                "A packet at {} claims {} bytes and has {}.".format(offset, size, len(body)))
        return body

    def _packets(self, offset: int) -> List[bytes]:
        """ Every packet from an offset to the end of the data. """
        out = []
        while offset + 2 <= len(self.data):
            body = self._packet(offset)
            if not body:
                break
            out.append(body)
            offset += 2 + len(body)
        return out

    def identification_header(self) -> bytes:
        """ The header WWise drops, rebuilt from the format chunk. """
        return struct.pack("<B6sIBIiiiBB", 1, b"vorbis", 0, self.channels, self.sample_rate,
                           0, self.byte_rate * 8, 0,
                           self.blocksize_0_pow | (self.blocksize_1_pow << 4), 1)

    @staticmethod
    def comment_header() -> bytes:
        """ An empty comment header, which Vorbis requires and WWise does not keep. """
        vendor = b"pycozmo"
        return struct.pack("<B6sI", 3, b"vorbis", len(vendor)) + vendor + struct.pack("<IB", 0, 1)

    def setup_header(self, library: CodebookLibrary) -> Tuple[bytes, List[int]]:
        """
        The setup header, widened back out of WWise's stripped one.

        Also returns each mode's block flag, which the audio packets need: it is what says whether a
        packet uses the long window, and so whether it carries the two window bits WWise leaves out.
        """
        stripped = BitReader(self._packet(self.setup_offset))
        out = BitWriter()
        out.write(5, 8)
        out.write_bytes(b"vorbis")

        # Codebooks, by index into the library.
        count = stripped.read(8) + 1
        out.write(count - 1, 8)
        for _ in range(count):
            library.expand(stripped.read(10), out)

        # Time domain transforms. Vorbis asks for a count and a value and only ever allows nought,
        # so WWise stores neither.
        out.write(0, 6)
        out.write(0, 16)

        for _ in range(out.copy(stripped, 6) + 1):
            self._copy_floor(stripped, out)
        for _ in range(out.copy(stripped, 6) + 1):
            self._copy_residue(stripped, out)
        for _ in range(out.copy(stripped, 6) + 1):
            self._copy_mapping(stripped, out)

        blockflags = []
        for _ in range(out.copy(stripped, 6) + 1):
            blockflags.append(out.copy(stripped, 1))
            out.write(0, 16)                            # window type, always nought
            out.write(0, 16)                            # transform type, always nought
            out.copy(stripped, 8)                       # which mapping the mode uses
        out.write(1, 1)                                 # framing bit

        if not stripped.at_end:
            raise exception.AudioKineticFormatError(
                "The setup packet has {} bits left after reading it.".format(stripped.bits_left))
        return out.to_bytes(), blockflags

    @staticmethod
    def _copy_floor(stripped: BitReader, out: BitWriter) -> None:
        """ One floor. WWise leaves out the type, which is always the curve floor. """
        out.write(1, 16)
        partitions = out.copy(stripped, 5)
        classes = [out.copy(stripped, 4) for _ in range(partitions)]
        dimensions = {}
        for class_number in range(max(classes) + 1 if classes else 0):
            dimensions[class_number] = out.copy(stripped, 3) + 1
            subclasses = out.copy(stripped, 2)
            if subclasses:
                out.copy(stripped, 8)                   # master book
            for _ in range(1 << subclasses):
                out.copy(stripped, 8)                   # sub class books, offset by one
        out.copy(stripped, 2)                           # multiplier, offset by one
        rangebits = out.copy(stripped, 4)
        for class_number in classes:
            for _ in range(dimensions[class_number]):
                out.copy(stripped, rangebits)

    @staticmethod
    def _copy_residue(stripped: BitReader, out: BitWriter) -> None:
        """ One residue. Its type takes two bits in WWise's form and sixteen in Vorbis'. """
        out.write(stripped.read(2), 16)
        out.copy(stripped, 24)                          # begin
        out.copy(stripped, 24)                          # end
        out.copy(stripped, 24)                          # partition size, offset by one
        classifications = out.copy(stripped, 6) + 1
        out.copy(stripped, 8)                           # classification book
        cascade = []
        for _ in range(classifications):
            low = out.copy(stripped, 3)
            high = out.copy(stripped, 5) if out.copy(stripped, 1) else 0
            cascade.append(low | (high << 3))
        for bits in cascade:
            for bit in range(8):
                if bits & (1 << bit):
                    out.copy(stripped, 8)               # book for this pass

    def _copy_mapping(self, stripped: BitReader, out: BitWriter) -> None:
        """ One mapping. WWise leaves out the type, which is always nought. """
        out.write(0, 16)
        submaps = out.copy(stripped, 4) + 1 if out.copy(stripped, 1) else 1
        if out.copy(stripped, 1):                       # channel coupling
            steps = out.copy(stripped, 8) + 1
            width = ilog(self.channels - 1)
            for _ in range(steps):
                out.copy(stripped, width)               # magnitude channel
                out.copy(stripped, width)               # angle channel
        if out.copy(stripped, 2):
            raise exception.AudioKineticFormatError("A mapping uses its reserved field.")
        if submaps > 1:
            for _ in range(self.channels):
                out.copy(stripped, 4)                   # which submap each channel belongs to
        for _ in range(submaps):
            out.copy(stripped, 8)                       # unused, was the time domain transform
            out.copy(stripped, 8)                       # floor
            out.copy(stripped, 8)                       # residue

    def audio_packets(self, blockflags: List[int]) -> List[Tuple[bytes, int]]:
        """
        The audio packets, each with the samples decodable once it has been read.

        WWise keeps the mode number at the front of a packet but drops the bit marking it as audio
        and, for a long block, the two bits saying whether it joins a short or a long window either
        side. Those depend on the neighbours' modes, so the modes are all read first.
        """
        bodies = self._packets(self.first_audio_offset)
        width = ilog(len(blockflags) - 1)
        modes = [BitReader(body).read(width) if width else 0 for body in bodies]
        if any(mode >= len(blockflags) for mode in modes):
            raise exception.AudioKineticFormatError("An audio packet names a mode that is not set.")

        sizes = (1 << self.blocksize_0_pow, 1 << self.blocksize_1_pow)
        out = []
        granule = 0
        for i, body in enumerate(bodies):
            source = BitReader(body)
            packet = BitWriter()
            packet.write(0, 1)                          # this is an audio packet
            packet.write(source.read(width), width)
            # The rest of the first byte has to follow the window bits, not precede them.
            remainder = source.read(8 - width)
            if blockflags[modes[i]]:
                packet.write(blockflags[modes[i - 1]] if i else 0, 1)
                packet.write(blockflags[modes[i + 1]] if i + 1 < len(bodies) else 0, 1)
            packet.write(remainder, 8 - width)
            packet.write_bytes(body[1:])

            # A packet only yields sound once the one before it has been read, since Vorbis fades
            # each block into the next.
            if i:
                granule += (sizes[blockflags[modes[i - 1]]] + sizes[blockflags[modes[i]]]) // 4
            out.append((packet.to_bytes(), granule))

        # The last block runs past the end of the sound; the granule is what trims it.
        if out and granule > self.sample_count:
            out[-1] = (out[-1][0], self.sample_count)
        return out

    def to_ogg(self, library: CodebookLibrary, serial: int = 1) -> bytes:
        """ The whole stream, as an Ogg Vorbis file. """
        setup, blockflags = self.setup_header(library)
        stream = ogg.OggWriter(serial)
        # Vorbis wants the identification header alone on the first page.
        stream.add(self.identification_header(), 0, end_page=True)
        stream.add(self.comment_header())
        stream.add(setup, 0, end_page=True)
        for packet, granule in self.audio_packets(blockflags):
            stream.add(packet, granule)
        return stream.to_bytes()


def to_ogg(media: wem.Wem, library: CodebookLibrary, serial: int = 1) -> bytes:
    """ Turn a WWise Vorbis WEM file into an Ogg Vorbis one. """
    return WwiseVorbis(media).to_ogg(library, serial)
