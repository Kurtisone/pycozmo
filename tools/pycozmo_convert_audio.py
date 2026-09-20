#!/usr/bin/env python
"""

Convert the robot's WWise Vorbis sounds into something PyCozmo can play.

A third of the sounds Cozmo's animations trigger are WWise Vorbis, and WWise does not store playable
Vorbis: it strips the setup header's codebooks out and leaves 10 bit indices into a library that
lives in its sound engine. That engine shipped inside the Cozmo application, not with the robot's
resources, so PyCozmo cannot decode those sounds from the resources alone and plays them as silence.

Point this at the application and it converts them once, into one WAV per media file under
util.get_converted_sound_dir(), which PyCozmo then uses in place of the files it cannot read. Nothing
from the application is copied: the codebooks are only read, and only while converting.

Needs ffmpeg for the Vorbis decoding itself, which PyCozmo deliberately does not depend on - this is
why the conversion happens here, once, rather than in the library at playback time.

Usage:

    pycozmo_convert_audio.py com.anki.cozmo.apk
    pycozmo_convert_audio.py libcozmoEngine.so --output /tmp/sound

"""

import argparse
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import time
import wave
import zipfile
from typing import List, Optional, Tuple

import pycozmo
from pycozmo.audiokinetic import codebooks, exception, vorbis, wem


#: Where the sound engine sits inside the application. Anki shipped one library per architecture and
#: any of them holds the same codebooks.
ENGINE_NAME = "libcozmoEngine.so"


def read_engine(fspec: pathlib.Path) -> bytes:
    """ The sound engine, out of an APK or straight off disk. """
    if fspec.suffix.lower() in (".apk", ".zip"):
        with zipfile.ZipFile(str(fspec)) as archive:
            members = [name for name in archive.namelist()
                       if os.path.basename(name) == ENGINE_NAME]
            if not members:
                raise exception.AudioKineticFormatError(
                    "No {} in {}. It has to be the Cozmo application.".format(ENGINE_NAME, fspec))
            with archive.open(members[0]) as f:
                return f.read()
    with open(str(fspec), "rb") as f:
        return f.read()


def find_vorbis(sound_dir: pathlib.Path) -> List[Tuple[int, pathlib.Path]]:
    """ Every WWise Vorbis media file under the resources, by identifier. """
    out = []
    for root, _, names in os.walk(str(sound_dir)):
        for name in sorted(names):
            stem, ext = os.path.splitext(name)
            if ext != ".wem" or not stem.isdigit():
                continue
            fspec = pathlib.Path(root) / name
            # Only the format chunk is needed to tell the codecs apart.
            with open(str(fspec), "rb") as f:
                head = f.read(64)
            if len(head) < 40 or head[0:4] != b"RIFF":
                continue
            if struct.unpack_from("<H", head, 20)[0] == wem.VORBIS:
                out.append((int(stem), fspec))
    return out


def decode(ogg: bytes, rate: int, ffmpeg: str) -> Optional[bytes]:
    """ Decode an Ogg Vorbis stream to 16 bit mono samples at a rate. """
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "ogg", "-i", "pipe:0",
         "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"],
        input=ogg, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        sys.stderr.write(result.stderr.decode("utf-8", "replace").strip() + "\n")
        return None
    return result.stdout


def fit(samples: bytes, expected: int) -> bytes:
    """
    Trim or pad samples to the length the source says it has.

    A rebuilt stream comes out within about 30 ms of its stated length, because the granule
    positions that say where a Vorbis stream starts and stops have to be worked out again rather
    than read. The file's own sample count is the authority, so it settles it.
    """
    wanted = expected * 2
    if len(samples) > wanted:
        return samples[:wanted]
    return samples + b"\x00" * (wanted - len(samples))


def write_wav(fspec: pathlib.Path, samples: bytes, rate: int) -> None:
    with wave.open(str(fspec), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples)


def convert(engine: pathlib.Path, sound_dir: pathlib.Path, output: pathlib.Path,
            ffmpeg: str, force: bool) -> int:
    library = codebooks.find_library(read_engine(engine))
    print("Codebooks: {} in {}.".format(len(library), engine.name))

    files = find_vorbis(sound_dir)
    if not files:
        print("No WWise Vorbis sounds in {}.".format(sound_dir))
        return 1
    print("Sounds to convert: {} in {}.".format(len(files), sound_dir))

    os.makedirs(str(output), exist_ok=True)
    start_time = time.perf_counter()
    done = skipped = failed = 0
    written = 0
    for file_id, fspec in files:
        target = output / "{}.wav".format(file_id)
        if target.exists() and not force:
            skipped += 1
            continue
        media = wem.load_wem(str(fspec))
        if media is None:
            failed += 1
            continue
        try:
            stream = vorbis.WwiseVorbis(media)
            ogg = stream.to_ogg(library)
        except (exception.AudioKineticFormatError, EOFError, ValueError) as e:
            sys.stderr.write("{}: {}\n".format(fspec.name, e))
            failed += 1
            continue
        samples = decode(ogg, pycozmo.audiolib.SAMPLE_RATE, ffmpeg)
        if samples is None:
            failed += 1
            continue
        expected = round(stream.sample_count * pycozmo.audiolib.SAMPLE_RATE / stream.sample_rate)
        samples = fit(samples, expected)
        write_wav(target, samples, pycozmo.audiolib.SAMPLE_RATE)
        written += len(samples)
        done += 1
        if done % 100 == 0:
            print("  {} of {}...".format(done + skipped, len(files)))

    print("Converted {}, kept {}, failed {}, in {:.0f} s.".format(
        done, skipped, failed, time.perf_counter() - start_time))
    print("{:.0f} MB of sound in {}.".format(written / 1e6, output))
    if failed:
        print("The sounds that failed stay silent; the rest now play.")
    return 1 if done == 0 and skipped == 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("engine", type=pathlib.Path,
                        help="the Cozmo application (.apk), or {} taken out of it".format(ENGINE_NAME))
    parser.add_argument("-o", "--output", type=pathlib.Path, default=None,
                        help="where to leave the converted sounds (default: {})".format(
                            pycozmo.util.get_converted_sound_dir()))
    parser.add_argument("-f", "--force", action="store_true",
                        help="convert sounds that have already been converted")
    parser.add_argument("--ffmpeg", default=None, help="the ffmpeg to decode with")
    args = parser.parse_args()

    if not args.engine.exists():
        sys.stderr.write("{} does not exist.\n".format(args.engine))
        return 1
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        sys.stderr.write("No ffmpeg found. It decodes the Vorbis; PyCozmo itself does not need it.\n")
        return 1

    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound as e:
        sys.stderr.write("{}\n".format(e))
        return 1
    sound_dir = pycozmo.util.get_cozmo_asset_dir() / "cozmo_resources" / "sound"
    output = args.output or pycozmo.util.get_converted_sound_dir()

    try:
        return convert(args.engine, sound_dir, output, ffmpeg, args.force)
    except exception.AudioKineticFormatError as e:
        sys.stderr.write("{}\n".format(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
