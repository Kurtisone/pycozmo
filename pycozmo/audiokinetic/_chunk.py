"""

Minimal replacement for the Python standard library "chunk" module.

The "chunk" module was deprecated in Python 3.11 and removed in Python 3.13 by PEP 594. Only the subset of its
interface that is needed for reading AudioKinetic WWise SoundBank files is implemented here.

Like the original, this reader is lazy - chunk data is left in the underlying stream and is only consumed by read()
or skip(). Code that inspects the stream position right after the chunk header depends on that behavior.

References:
    - https://peps.python.org/pep-0594/#chunk

"""

import struct
from typing import BinaryIO


__all__ = [
    "Chunk",
]


class Chunk:
    """ Reader for a single IFF-style chunk of a binary stream. """

    def __init__(self, file: BinaryIO, align: bool = True, bigendian: bool = True,
                 inclheader: bool = False) -> None:
        self.closed = False
        self.align = bool(align)
        self.file = file

        self.chunkname = file.read(4)
        if len(self.chunkname) < 4:
            raise EOFError("Truncated chunk name.")

        try:
            self.chunksize = struct.unpack(">L" if bigendian else "<L", file.read(4))[0]
        except struct.error:
            raise EOFError("Truncated chunk size.") from None
        if inclheader:
            # The size declared by the chunk includes its own 8-byte header.
            self.chunksize -= 8

        self.size_read = 0
        try:
            self.offset = file.tell()
        except (AttributeError, OSError):
            self.seekable = False
        else:
            self.seekable = True

    def getname(self) -> bytes:
        """ Return the name of the chunk. """
        return self.chunkname

    def getsize(self) -> int:
        """ Return the size of the chunk data, in bytes. """
        return self.chunksize

    def tell(self) -> int:
        """ Return the current position within the chunk data. """
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return self.size_read

    def seek(self, pos: int, whence: int = 0) -> None:
        """ Seek within the chunk data. """
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if not self.seekable:
            raise OSError("Underlying stream is not seekable.")
        if whence == 1:
            pos += self.size_read
        elif whence == 2:
            pos += self.chunksize
        if pos < 0 or pos > self.chunksize:
            raise RuntimeError("Seek outside of chunk.")
        self.file.seek(self.offset + pos, 0)
        self.size_read = pos

    def read(self, size: int = -1) -> bytes:
        """ Read at most size bytes of chunk data. Reading past the end of the chunk returns b"". """
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self.size_read >= self.chunksize:
            return b""
        remaining = self.chunksize - self.size_read
        if size < 0 or size > remaining:
            size = remaining
        data = self.file.read(size)
        self.size_read += len(data)
        if self.size_read == self.chunksize and self.align and (self.chunksize & 1):
            # Chunks of odd size are followed by a pad byte.
            self.size_read += len(self.file.read(1))
        return data

    def skip(self) -> None:
        """ Advance the underlying stream to the end of the chunk. """
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self.seekable:
            n = self.chunksize - self.size_read
            if self.align and (self.chunksize & 1):
                n += 1
            try:
                self.file.seek(n, 1)
            except OSError:
                pass
            else:
                self.size_read += n
                return
        while self.size_read < self.chunksize:
            data = self.read(min(8192, self.chunksize - self.size_read))
            if not data:
                raise EOFError("Truncated chunk data.")

    def close(self) -> None:
        """ Skip to the end of the chunk and mark it as closed. """
        if not self.closed:
            try:
                self.skip()
            finally:
                self.closed = True
