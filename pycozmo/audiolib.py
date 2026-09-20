"""

Robot audio library - the sounds Cozmo's animations play.

An animation does not carry its sound. It names a WWise event, by the 32 bit identifier WWise
hashed its name into, and the application is left to work out what that event plays and to stream
the samples to the robot's speaker. Going from the identifier to samples takes three steps:

- The sound banks under cozmo_resources/sound map an event to an event action, the action to a
  container of takes, and each take to a media file. Cozmo's own bank, holding all 380 events its
  animations name, is the one bank that is not unpacked on disk: it sits inside AudioAssets.zip.
- The media file is a WEM, read by pycozmo.audiokinetic.wem . Two thirds of the events Cozmo's
  animations trigger resolve to IMA ADPCM, which decodes. The rest are WWise Vorbis, whose
  codebooks never shipped with the robot's resources; those play only once they have been converted
  by tools/pycozmo_convert_audio.py, which leaves a WAV file per media identifier under
  util.get_converted_sound_dir() and is what this looks for first.
- The samples are resampled to the rate the robot's speaker runs at and U-law encoded into the
  744 sample frames OutputAudio carries, one per animation frame.

"""

import os
import pathlib
import random
import time
import wave
import zipfile
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .logger import logger
from . import audio
from . import protocol_encoder
from . import util
from .audiokinetic import soundbank, soundbanksinfo, wem


__all__ = [
    "AudioLibrary",

    "add_converted_sound",
    "load_audio_library",
]


#: Sample rate the robot's speaker runs at.
SAMPLE_RATE = 22050

#: Samples in one OutputAudio packet, which is also one animation frame.
FRAME_SAMPLES = 744

#: Bank that is only present inside AudioAssets.zip, and the archive member holding it.
PACKED_BANK = "Cozmo.bnk"

#: How many encoded frames to keep. Cozmo's sounds run to 97 minutes once the converted Vorbis is
#: counted, which is too much to hold all of; the oldest go when the count is reached.
FRAME_CACHE_SIZE = 40000


class AudioLibrary:
    """ The sounds the animations can play, by WWise event identifier. """

    __slots__ = [
        "events",
        "actions",
        "containers",
        "sounds",
        "files",
        "converted",
        "_playable",
        "_frames",
    ]

    def __init__(self) -> None:
        self.events: Dict[int, soundbank.Event] = {}
        self.actions: Dict[int, soundbank.EventAction] = {}
        self.containers: Dict[int, soundbank.Container] = {}
        self.sounds: Dict[int, soundbank.SFX] = {}
        # Media file identifier -> path on disk.
        self.files: Dict[int, str] = {}
        # Media file identifier -> converted WAV, for the files PyCozmo cannot decode itself.
        self.converted: Dict[int, str] = {}
        # Whether a media file can be played at all, which costs a header read to answer.
        self._playable: Dict[int, bool] = {}
        # Encoded frames by media file and volume. Decoding and U-law encoding are both slow enough
        # to be worth keeping, and this is what playback actually asks for.
        self._frames: Dict[Tuple[int, int], List[protocol_encoder.OutputAudio]] = {}

    def add_bank(self, bank: soundbank.SoundBank) -> None:
        """ Take in everything a sound bank knows. """
        for obj in bank.objs.values():
            if isinstance(obj, soundbank.Event):
                self.events[obj.id] = obj
            elif isinstance(obj, soundbank.EventAction):
                self.actions[obj.id] = obj
            elif isinstance(obj, soundbank.Container):
                self.containers[obj.id] = obj
            elif isinstance(obj, soundbank.SFX):
                self.sounds[obj.id] = obj

    def get_takes(self, event_id: int) -> List[int]:
        """
        The media files an event could play, one of which it picks.

        An event names actions, an action names a container or a sound, and a container names more
        of either. The takes are the sounds at the bottom.
        """
        event = self.events.get(event_id)
        if event is None:
            return []
        takes = []
        for action_id in event.action_ids:
            action = self.actions.get(action_id)
            if action is not None:
                takes += self._collect(action.reference_id, 0)
        return takes

    def _collect(self, object_id: int, depth: int) -> List[int]:
        # Containers nest, so stop rather than trust the data not to loop.
        if depth > 8:
            logger.warning("Audio object %s nests deeper than expected.", object_id)
            return []
        sound = self.sounds.get(object_id)
        if sound is not None:
            return [sound.file_id]
        container = self.containers.get(object_id)
        if container is None:
            return []
        out = []
        for child in container.children:
            out += self._collect(child, depth + 1)
        return out

    def get_frames(self, event_id: int, volume: float = 1.0) -> List[protocol_encoder.OutputAudio]:
        """
        The audio frames an event plays, or nothing if it cannot be decoded.

        A take is drawn at random, the way a WWise random container behaves. The takes of one event
        are variants of the same sound, so which one plays is not meant to be predictable.
        """
        playable = [f for f in self.get_takes(event_id) if self.is_playable(f)]
        if not playable:
            return []
        file_id = random.choice(playable)
        # Volume is quantised so that a cached encoding is actually reused. Every keyframe in the
        # resources asks for a round figure anyway.
        key = (file_id, int(round(min(max(volume, 0.0), 1.0) * 100)))
        cached = self._frames.get(key)
        if cached is None:
            samples, channels, sample_rate = self._get_pcm(file_id)
            cached = self.encode(samples, channels, sample_rate, key[1] / 100.0)
            self._remember(key, cached)
        return cached

    def is_playable(self, file_id: int) -> bool:
        """
        Whether a media file can be turned into sound.

        Answered from the file's header rather than by decoding it, so that choosing between an
        event's takes costs nothing. The answer is kept, since an event is asked for over and over.
        """
        known = self._playable.get(file_id)
        if known is not None:
            return known
        answer = False
        if file_id in self.converted:
            answer = True
        else:
            path = self.files.get(file_id)
            if path is not None:
                media = wem.load_wem(path)
                answer = media is not None and media.is_supported
        self._playable[file_id] = answer
        return answer

    def _remember(self, key: Tuple[int, int], frames: List[protocol_encoder.OutputAudio]) -> None:
        """ Keep an encoding, dropping the oldest once there are too many frames. """
        self._frames[key] = frames
        held = sum(len(value) for value in self._frames.values())
        while held > FRAME_CACHE_SIZE and len(self._frames) > 1:
            oldest = next(iter(self._frames))
            held -= len(self._frames.pop(oldest))

    def _get_pcm(self, file_id: int) -> Tuple[np.ndarray, int, int]:
        """
        Decode a media file to samples. Empty if it cannot be decoded.

        The result is not kept: a converted Vorbis file can run to minutes, and it is the encoded
        frames that playback asks for again, not these.
        """
        empty = np.zeros(0, dtype=np.int16)
        path = self.converted.get(file_id)
        if path is not None:
            try:
                return self._read_wav(path)
            except (OSError, wave.Error, ValueError) as e:
                logger.warning("Failed to read converted audio file %s. %s", file_id, e)
                return empty, 1, SAMPLE_RATE
        path = self.files.get(file_id)
        if path is None:
            return empty, 1, SAMPLE_RATE
        media = wem.load_wem(path)
        if media is None or not media.is_supported:
            return empty, 1, SAMPLE_RATE
        try:
            return np.array(media.decode(), dtype=np.int16), media.channels, media.sample_rate
        except Exception as e:
            logger.warning("Failed to decode audio file %s. %s", file_id, e)
            return empty, 1, SAMPLE_RATE

    @staticmethod
    def _read_wav(fspec: str) -> Tuple[np.ndarray, int, int]:
        """ Read a converted sound. tools/pycozmo_convert_audio.py writes 16 bit PCM. """
        with wave.open(fspec, "rb") as f:
            if f.getsampwidth() != 2:
                raise ValueError("{} is {} bit, not 16.".format(fspec, f.getsampwidth() * 8))
            channels, rate = f.getnchannels(), f.getframerate()
            raw = f.readframes(f.getnframes())
        return np.frombuffer(raw, dtype="<i2"), channels, rate

    @staticmethod
    def encode(samples: Union[Sequence[int], np.ndarray], channels: int, sample_rate: int,
               volume: float = 1.0) -> List[protocol_encoder.OutputAudio]:
        """ Mix down, resample to the robot's rate, and U-law encode into OutputAudio frames. """
        if len(samples) == 0:
            return []
        data = np.array(samples, dtype=np.float64)
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if sample_rate != SAMPLE_RATE:
            count = max(int(round(len(data) * SAMPLE_RATE / sample_rate)), 1)
            data = np.interp(np.linspace(0.0, len(data) - 1, count),
                             np.arange(len(data)), data)
        if volume != 1.0:
            data = data * volume
        data = np.clip(np.rint(data), -32768, 32767).astype(np.int64)
        frames = []
        for start in range(0, len(data), FRAME_SAMPLES):
            chunk = data[start:start + FRAME_SAMPLES]
            # The tail of a short final frame is silence, which in U-law is 0xFF rather than nought.
            frame = bytearray([audio.SILENCE]) * FRAME_SAMPLES
            for i, sample in enumerate(chunk):
                frame[i] = audio.u_law_encoding(int(sample))
            frames.append(protocol_encoder.OutputAudio(samples=bytes(frame)))
        return frames


def load_audio_library(resource_dir: str) -> AudioLibrary:
    """ Load the sound banks and index the media files beside them. """

    start_time = time.perf_counter()
    library = AudioLibrary()
    sound_dir = os.path.join(resource_dir, "cozmo_resources", "sound")
    info_fspec = os.path.join(sound_dir, "SoundbanksInfo.xml")
    if not os.path.exists(info_fspec):
        logger.warning("No sound resources in %s.", sound_dir)
        return library

    info = soundbanksinfo.load_soundbanksinfo(info_fspec)
    reader = soundbank.SoundBankReader(info)

    for entry in sorted(os.listdir(sound_dir)):
        if entry.endswith(".bnk"):
            _load_bank(library, reader, os.path.join(sound_dir, entry))

    # Cozmo's own bank, the one holding the events the animations name, is only in the archive.
    archive = os.path.join(sound_dir, "AudioAssets.zip")
    if os.path.exists(archive):
        _load_packed_bank(library, reader, archive)

    # Index the media files, which sit loose beside the banks, the voice ones under a language.
    for root, _, names in os.walk(sound_dir):
        for name in names:
            stem, ext = os.path.splitext(name)
            if ext == ".wem" and stem.isdigit():
                library.files.setdefault(int(stem), os.path.join(root, name))

    add_converted_sound(library)

    logger.debug("Loaded %s audio events, %s media files and %s converted ones in %.02f s.",
                 len(library.events), len(library.files), len(library.converted),
                 time.perf_counter() - start_time)
    return library


def add_converted_sound(library: AudioLibrary, converted_dir: Optional[str] = None) -> None:
    """
    Index the sounds converted out of a format PyCozmo cannot decode.

    One WAV per media file identifier, as tools/pycozmo_convert_audio.py leaves them. They take
    precedence over the WEM of the same identifier, which is the point: the WEM is the one PyCozmo
    could not read.
    """
    directory = util.get_converted_sound_dir() if converted_dir is None else pathlib.Path(converted_dir)
    if not directory.is_dir():
        return
    for entry in sorted(os.listdir(directory)):
        stem, ext = os.path.splitext(entry)
        if ext == ".wav" and stem.isdigit():
            library.converted[int(stem)] = str(directory / entry)


def _load_bank(library: AudioLibrary, reader: soundbank.SoundBankReader, fspec: str) -> None:
    try:
        library.add_bank(reader.load(fspec))
    except Exception as e:
        logger.warning("Failed to load sound bank %s. %s", os.path.basename(fspec), e)


def _load_packed_bank(library: AudioLibrary, reader: soundbank.SoundBankReader,
                      archive: str) -> None:
    """ Load Cozmo.bnk out of AudioAssets.zip, where it is the only copy. """
    try:
        with zipfile.ZipFile(archive) as zf:
            members = [n for n in zf.namelist() if os.path.basename(n) == PACKED_BANK]
            if not members:
                return
            with zf.open(members[0]) as f:
                library.add_bank(reader.load_file(f, members[0]))  # type: ignore[arg-type]
    except Exception as e:
        logger.warning("Failed to load %s from %s. %s", PACKED_BANK, os.path.basename(archive), e)
