"""

Bit-level reading and writing, in Vorbis order.

Vorbis packs its fields into a bit stream rather than bytes, least significant bit of each byte
first, and a field can straddle a byte boundary. WWise's own formats - its packed codebooks and the
setup packet it strips out of a Vorbis file - use the same order, which is what makes rebuilding a
Vorbis stream out of them a matter of copying fields across.

"""

__all__ = [
    "BitReader",
    "BitWriter",

    "ilog",
]


def ilog(value: int) -> int:
    """
    Vorbis' ilog: how many bits it takes to hold a value, and 0 for anything below 1.

    Vorbis uses it to size the fields whose width depends on a count read earlier.
    """
    return value.bit_length() if value > 0 else 0


class BitReader:
    """ Reads fields out of a bit stream, least significant bit first. """

    __slots__ = ["data", "pos"]

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        #: Position in bits, not bytes.
        self.pos = pos

    def read(self, count: int) -> int:
        """ Read a field of the given width. """
        value = 0
        for i in range(count):
            index = self.pos >> 3
            if index >= len(self.data):
                raise EOFError("Bit stream ran out after {} bits.".format(self.pos))
            value |= ((self.data[index] >> (self.pos & 7)) & 1) << i
            self.pos += 1
        return value

    @property
    def bits_left(self) -> int:
        return len(self.data) * 8 - self.pos

    @property
    def at_end(self) -> bool:
        """
        Whether nothing but padding is left.

        A bit stream ends inside a byte and the rest of that byte is padding, so consuming one
        exactly means coming to rest in its last byte. WWise leaves a whole spare byte behind some
        of its packed codebooks, so a byte of slack counts as the end too.
        """
        return 0 <= self.bits_left <= 8


class BitWriter:
    """ Builds a bit stream, least significant bit first. """

    __slots__ = ["data", "pos"]

    def __init__(self) -> None:
        self.data = bytearray()
        #: Position in bits, not bytes.
        self.pos = 0

    def write(self, value: int, count: int) -> None:
        """ Append a field of the given width. """
        for i in range(count):
            if self.pos & 7 == 0:
                self.data.append(0)
            if (value >> i) & 1:
                self.data[-1] |= 1 << (self.pos & 7)
            self.pos += 1

    def write_bytes(self, data: bytes) -> None:
        """
        Append whole bytes at the current position, which need not be a byte boundary.

        Vorbis packets are mostly copied through unchanged after a few bits have been put in front
        of them, and doing that a bit at a time is far too slow for a whole sound library. Shifting
        the run as one big integer moves it in a single operation.
        """
        if not data:
            return
        shift = self.pos & 7
        if shift:
            blob = (int.from_bytes(data, "little") << shift).to_bytes(len(data) + 1, "little")
            # The low bits of the first byte fall into the byte already being filled.
            self.data[-1] |= blob[0]
            self.data += blob[1:]
        else:
            self.data += data
        self.pos += len(data) * 8

    def copy(self, reader: BitReader, count: int) -> int:
        """ Move a field across from a reader, and return it. """
        value = reader.read(count)
        self.write(value, count)
        return value

    def to_bytes(self) -> bytes:
        """ The stream so far, padded out to a whole byte with zeroes. """
        return bytes(self.data)
