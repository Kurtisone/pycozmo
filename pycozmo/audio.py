"""

Cozmo audio encoding.

References:
    - https://en.wikipedia.org/wiki/%CE%9C-law_algorithm
    - http://dystopiancode.blogspot.com/2012/02/pcm-law-and-u-law-companding-algorithms.html

"""

from typing import List
import struct
import wave
import time

from .logger import logger
from . import protocol_encoder


__all__ = [
    "SILENCE",

    "load_wav",
]


MULAW_MAX = 0x7FFF
MULAW_BIAS = 132

#: U-law byte for a sample of nought. Frames are padded with it.
SILENCE = 0xFF


def load_wav(filename: str) -> List[protocol_encoder.OutputAudio]:
    """ Load a WAVE file into a list of OutputAudio packets. """

    start_time = time.perf_counter()

    with wave.open(filename, "r") as w:
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        if sampwidth != 2 or (framerate != 22050 and framerate != 48000):
            raise ValueError('Invalid audio format, only 16 bit samples are supported, ' +
                             'with 22050Hz or 48000Hz frame rates.')

        ratediv = 2 if framerate == 48000 else 1
        channels = w.getnchannels()
        pkts = []

        while True:
            frame_in = w.readframes(744 * ratediv)
            if not frame_in:
                break
            frame_out = bytes_to_cozmo(frame_in, ratediv, channels)
            pkt = protocol_encoder.OutputAudio(samples=frame_out)
            pkts.append(pkt)

    logger.debug("Loaded WAVE file in {:.02f} s.".format(time.perf_counter() - start_time))

    return pkts


def bytes_to_cozmo(byte_string: bytes, rate_correction: int, channels: int) -> bytearray:
    """ Convert a 744 sample, 16-bit audio frame into a U-law encoded frame. """
    # A short final frame is padded with silence, which in U-law is 0xFF and not nought - a nought
    # byte is very nearly full scale negative, so padding with it clicks.
    out = bytearray([SILENCE]) * 744
    n = channels * rate_correction
    bs = struct.unpack('{}h'.format(int(len(byte_string) / 2)), byte_string)[0::n]
    for i, s in enumerate(bs):
        out[i] = u_law_encoding(s)
    return out


def u_law_encoding(sample: int) -> int:
    """ U-law encode a 16-bit PCM sample. """
    mask = 0x4000
    position = 14
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    sample += MULAW_BIAS
    if sample > MULAW_MAX:
        sample = MULAW_MAX

    while (sample & mask) != mask and position >= 7:
        mask >>= 1
        position -= 1

    lsb = (sample >> (position - 4)) & 0x0f
    # U-law transmits the one's complement of the sign, exponent and mantissa. This used to negate
    # the complement instead of masking it, which is the same as adding one to the uncomplemented
    # byte: the samples came out uncorrelated with the input - noise - and a byte of 0xFF overflowed
    # the bytearray it was being stored into.
    return ~(sign | ((position - 7) << 4) | lsb) & 0xFF
