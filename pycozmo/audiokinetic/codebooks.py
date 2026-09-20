"""

WWise's Vorbis codebook library.

A WWise Vorbis file does not carry its codebooks. Its setup packet names them by a 10 bit index into
a library of 598 that lives in WWise's sound engine, and for Cozmo that engine shipped inside the
phone application rather than with the robot's resources. The library is held in a compacted form of
the Vorbis codebook syntax - narrower fields, no sync pattern - so expanding an entry back into a
real Vorbis codebook is a matter of copying its fields into wider ones.

The library has no header to find it by, so find_library() goes looking for its table of offsets:
599 little endian offsets into the binary, rising, closing on a nought. A run of that shape is
unlikely on its own, and every candidate is checked by decoding all of its codebooks, which only
succeeds for the real thing.

"""

import struct
from typing import List, Optional, Tuple

from . import exception
from .bits import BitReader, BitWriter, ilog


__all__ = [
    "CodebookLibrary",

    "find_library",
]


#: Vorbis marks the start of a codebook with this; WWise's packed form leaves it out.
SYNC_PATTERN = 0x564342

#: Shortest run of offsets worth checking, well under the 599 the library is known to have.
MIN_CODEBOOKS = 64


def lookup1_values(entries: int, dimensions: int) -> int:
    """
    Vorbis' lookup1_values: the largest n whose n**dimensions still fits in entries.

    It is how many multiplicands a lookup type 1 codebook stores, which the codebook does not say.
    """
    if dimensions < 1:
        return 0
    n = 0
    while (n + 1) ** dimensions <= entries:
        n += 1
    return n


class CodebookLibrary:
    """ The packed Vorbis codebooks WWise's sound engine holds. """

    __slots__ = ["data", "offsets"]

    def __init__(self, data: bytes, offsets: List[int]) -> None:
        #: The packed codebooks, back to back.
        self.data = data
        #: Where each codebook starts within data, with a final entry for the end of the last one.
        self.offsets = offsets

    def __len__(self) -> int:
        return max(len(self.offsets) - 1, 0)

    def packed(self, index: int) -> bytes:
        """ One codebook, still packed. """
        if index < 0 or index >= len(self):
            raise exception.AudioKineticFormatError(
                "Codebook {} is outside the library's {}.".format(index, len(self)))
        return self.data[self.offsets[index]:self.offsets[index + 1]]

    def expand(self, index: int, out: BitWriter) -> None:
        """ Write one codebook to a bit stream in the Vorbis syntax. """
        codebook = BitReader(self.packed(index))

        out.write(SYNC_PATTERN, 24)
        dimensions = codebook.read(4)
        out.write(dimensions, 16)
        entries = codebook.read(14)
        out.write(entries, 24)

        ordered = out.copy(codebook, 1)
        if ordered:
            # Lengths in one rising run, which Vorbis and WWise both write the same way.
            out.copy(codebook, 5)
            current = 0
            while current < entries:
                number = out.copy(codebook, ilog(entries - current))
                current += number
                if current > entries:
                    raise exception.AudioKineticFormatError(
                        "Codebook {} orders more entries than it has.".format(index))
        else:
            # WWise sizes the length field to the codebook; Vorbis always spends five bits.
            length_bits = codebook.read(3)
            if not 1 <= length_bits <= 5:
                raise exception.AudioKineticFormatError(
                    "Codebook {} sizes its lengths in {} bits.".format(index, length_bits))
            sparse = out.copy(codebook, 1)
            for _ in range(entries):
                if sparse and not out.copy(codebook, 1):
                    continue
                out.write(codebook.read(length_bits), 5)

        # WWise only ever uses lookup types 0 and 1, so it spends one bit where Vorbis spends four.
        lookup = codebook.read(1)
        out.write(lookup, 4)
        if lookup == 1:
            out.copy(codebook, 32)                      # minimum value
            out.copy(codebook, 32)                      # delta value
            value_bits = out.copy(codebook, 4) + 1
            out.copy(codebook, 1)                       # sequence flag
            for _ in range(lookup1_values(entries, dimensions)):
                out.copy(codebook, value_bits)

        if not codebook.at_end:
            raise exception.AudioKineticFormatError(
                "Codebook {} has {} bits left over.".format(index, codebook.bits_left))

    def verify(self) -> None:
        """ Expand every codebook, which fails unless this really is the library. """
        for index in range(len(self)):
            self.expand(index, BitWriter())


def _elf_segments(buf: bytes) -> List[Tuple[int, int, int]]:
    """ An ELF's loadable segments, as (virtual address, file offset, size). """
    if len(buf) < 64 or buf[:4] != b"\x7fELF":
        return []
    is_64 = buf[4] == 2
    if is_64:
        ph_off, ph_size, ph_num = struct.unpack_from("<QHH", buf, 0x20)[0], \
            struct.unpack_from("<H", buf, 0x36)[0], struct.unpack_from("<H", buf, 0x38)[0]
    else:
        ph_off = struct.unpack_from("<I", buf, 0x1c)[0]
        ph_size = struct.unpack_from("<H", buf, 0x2a)[0]
        ph_num = struct.unpack_from("<H", buf, 0x2c)[0]
    out = []
    for i in range(ph_num):
        base = ph_off + i * ph_size
        if base + ph_size > len(buf):
            break
        if is_64:
            p_type, = struct.unpack_from("<I", buf, base)
            p_offset, p_vaddr = struct.unpack_from("<QQ", buf, base + 0x08)
            p_filesz, = struct.unpack_from("<Q", buf, base + 0x20)
        else:
            p_type, p_offset, p_vaddr = struct.unpack_from("<III", buf, base)
            p_filesz, = struct.unpack_from("<I", buf, base + 0x10)
        if p_type == 1:                                 # PT_LOAD
            out.append((p_vaddr, p_offset, p_filesz))
    return out


def _to_file_offset(segments: List[Tuple[int, int, int]], vaddr: int) -> Optional[int]:
    """ Where a virtual address sits in the file. """
    if not segments:
        return vaddr
    for p_vaddr, p_offset, p_filesz in segments:
        if p_vaddr <= vaddr < p_vaddr + p_filesz:
            return vaddr - p_vaddr + p_offset
    return None


def _offset_runs(buf: bytes) -> List[Tuple[int, int]]:
    """ Every run of rising little endian words, longest first, as (position, length). """
    count = len(buf) // 4
    words = struct.unpack_from("<{}I".format(count), buf, 0)
    runs = []
    i = 0
    while i < count:
        j = i
        while j + 1 < count and words[j] < words[j + 1] and words[j + 1] - words[j] < 4096:
            j += 1
        if j - i + 1 >= MIN_CODEBOOKS:
            runs.append((i * 4, j - i + 1))
        i = j + 1
    runs.sort(key=lambda run: -run[1])
    return runs


def find_library(buf: bytes) -> CodebookLibrary:
    """
    Find the codebook library in a sound engine binary.

    Raises AudioKineticFormatError when no run of offsets in it yields codebooks that all decode.
    """
    segments = _elf_segments(buf)
    for position, length in _offset_runs(buf):
        vaddrs = list(struct.unpack_from("<{}I".format(length), buf, position))
        offsets = [_to_file_offset(segments, vaddr) for vaddr in vaddrs]
        if any(offset is None for offset in offsets):
            continue
        # The offsets point into the binary itself, so the data is already in hand; keeping the
        # whole buffer and indexing into it saves copying 17 MB to reach 72 KB.
        library = CodebookLibrary(buf, [offset for offset in offsets if offset is not None])
        try:
            library.verify()
        except (exception.AudioKineticFormatError, EOFError, ValueError):
            continue
        return library
    raise exception.AudioKineticFormatError(
        "No WWise codebook library in this binary. It has to be the sound engine the sounds were "
        "built for - for Cozmo, libcozmoEngine.so out of the application.")
