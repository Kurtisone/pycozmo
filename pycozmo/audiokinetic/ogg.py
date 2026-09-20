"""

Ogg bitstream framing.

Vorbis packets have to travel inside Ogg pages before anything will play them, and WWise's files
carry the packets without the container. This builds the container back: pages of up to 255 segments,
each page carrying whole packets and stating how many samples have been decodable by the end of it.

Reference:
    - https://www.rfc-editor.org/rfc/rfc3533 - The Ogg Encapsulation Format

"""

import struct
from typing import List, Tuple


__all__ = [
    "OggWriter",
]


#: Ogg's own CRC: the usual CRC-32 polynomial, but fed high bit first and neither inverted nor
#: reflected, which is why zlib's cannot stand in for it.
_CRC_POLYNOMIAL = 0x04c11db7


def _crc_table() -> List[int]:
    table = []
    for i in range(256):
        value = i << 24
        for _ in range(8):
            value = ((value << 1) ^ _CRC_POLYNOMIAL) & 0xffffffff if value & 0x80000000 \
                else (value << 1) & 0xffffffff
        table.append(value)
    return table


_CRC = _crc_table()

#: Segments a page can hold, and the length one segment stands for.
MAX_SEGMENTS = 255
SEGMENT_SIZE = 255


def crc32(data: bytes) -> int:
    """ Ogg's page checksum. """
    crc = 0
    table = _CRC
    for byte in data:
        crc = ((crc << 8) & 0xffffffff) ^ table[(crc >> 24) ^ byte]
    return crc


def _segments(length: int) -> List[int]:
    """ The lacing values a packet of this length needs. """
    out = [SEGMENT_SIZE] * (length // SEGMENT_SIZE)
    out.append(length % SEGMENT_SIZE)
    return out


class OggWriter:
    """ Packs packets into Ogg pages. """

    __slots__ = ["serial", "packets"]

    def __init__(self, serial: int = 1) -> None:
        self.serial = serial
        #: Each packet, the samples decodable once it is done, and whether it ends its page.
        self.packets: List[Tuple[bytes, int, bool]] = []

    def add(self, packet: bytes, granule: int = 0, end_page: bool = False) -> None:
        """ Add a packet. end_page closes the page after it, as the headers require. """
        self.packets.append((packet, granule, end_page))

    def to_bytes(self) -> bytes:
        """ The whole bitstream. """
        pages = self._pages()
        out = bytearray()
        for index, (payload, segments, granule) in enumerate(pages):
            flags = 0
            if index == 0:
                flags |= 0x02                           # beginning of stream
            if index == len(pages) - 1:
                flags |= 0x04                           # end of stream
            header = bytearray(struct.pack("<4sBBqIIIB", b"OggS", 0, flags, granule,
                                           self.serial, index, 0, len(segments)))
            header += bytes(segments)
            page = header + payload
            crc = crc32(bytes(page))
            page[22:26] = struct.pack("<I", crc)
            out += page
        return bytes(out)

    def _pages(self) -> List[Tuple[bytes, List[int], int]]:
        """ Group the packets into pages, as (payload, lacing values, granule). """
        pages: List[Tuple[bytes, List[int], int]] = []
        payload = bytearray()
        segments: List[int] = []
        granule = 0
        for packet, packet_granule, end_page in self.packets:
            lacing = _segments(len(packet))
            if len(lacing) > MAX_SEGMENTS:
                raise ValueError("A {} byte packet does not fit in one page.".format(len(packet)))
            if segments and len(segments) + len(lacing) > MAX_SEGMENTS:
                pages.append((bytes(payload), segments, granule))
                payload, segments = bytearray(), []
            payload += packet
            segments += lacing
            granule = packet_granule
            if end_page:
                pages.append((bytes(payload), segments, granule))
                payload, segments = bytearray(), []
        if segments:
            pages.append((bytes(payload), segments, granule))
        return pages
