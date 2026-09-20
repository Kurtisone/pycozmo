"""

Robot audio library - the sounds Cozmo's animations play.

An animation does not carry its sound. It names a WWise event, by the 32 bit identifier WWise
hashed its name into, and the application is left to work out what that event plays and to stream
the samples to the robot's speaker. Going from the identifier to samples takes three steps:

- The sound banks under cozmo_resources/sound map an event to an event action, the action to a
  container of takes, and each take to a media file. Cozmo's own bank, holding all 380 events its
  animations name, is the one bank that is not unpacked on disk: it sits inside AudioAssets.zip.
- The media file is a WEM, read by pycozmo.audiokinetic.wem . Two thirds of the events Cozmo's
  animations trigger resolve to IMA ADPCM, which decodes; the rest are WWise Vorbis, whose
  codebooks never shipped with the robot's resources, and stay silent.
- The samples are resampled to the rate the robot's speaker runs at and U-law encoded into the
  744 sample frames OutputAudio carries, one per animation frame.

"""

import os
import random
import time
import zipfile
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .logger import logger
from . import audio
from . import protocol_encoder
from .audiokinetic import soundbank, soundbanksinfo, wem


__all__ = [
    "AudioLibrary",

    "load_audio_library",
]


#: Sample rate the robot's speaker runs at.
SAMPLE_RATE = 22050

#: Samples in one OutputAudio packet, which is also one animation frame.
FRAME_SAMPLES = 744

#: Bank that is only present inside AudioAssets.zip, and the archive member holding it.
PACKED_BANK = "Cozmo.bnk"


class AudioLibrary:
    """ The sounds the animations can play, by WWise event identifier. """

    __slots__ = [
        "events",
        "actions",
        "containers",
        "sounds",
        "files",
        "_pcm",
        "_frames",
    ]

    def __init__(self) -> None:
        self.events: Dict[int, soundbank.Event] = {}
        self.actions: Dict[int, soundbank.EventAction] = {}
        self.containers: Dict[int, soundbank.Container] = {}
        self.sounds: Dict[int, soundbank.SFX] = {}
        # Media file identifier -> path on disk.
        self.files: Dict[int, str] = {}
        # Decoded samples by media file identifier, and encoded frames by file and volume.
        # Decoding and U-law encoding are both slow enough to be worth keeping.
        self._pcm: Dict[int, Tuple[List[int], int, int]] = {}
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
        playable = [f for f in self.get_takes(event_id) if self._get_pcm(f)[0]]
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
            self._frames[key] = cached
        return cached

    def _get_pcm(self, file_id: int) -> Tuple[List[int], int, int]:
        """ Decode a media file to samples, keeping the result. Empty if it cannot be decoded. """
        cached = self._pcm.get(file_id)
        if cached is not None:
            return cached
        result: Tuple[List[int], int, int] = ([], 1, SAMPLE_RATE)
        path = self.files.get(file_id)
        if path is not None:
            media = wem.load_wem(path)
            if media is not None and media.is_supported:
                try:
                    result = (media.decode(), media.channels, media.sample_rate)
                except Exception as e:
                    logger.warning("Failed to decode audio file %s. %s", file_id, e)
        self._pcm[file_id] = result
        return result

    @staticmethod
    def encode(samples: Sequence[int], channels: int, sample_rate: int,
               volume: float = 1.0) -> List[protocol_encoder.OutputAudio]:
        """ Mix down, resample to the robot's rate, and U-law encode into OutputAudio frames. """
        if not samples:
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

    logger.debug("Loaded %s audio events and %s media files in %.02f s.",
                 len(library.events), len(library.files), time.perf_counter() - start_time)
    return library


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
