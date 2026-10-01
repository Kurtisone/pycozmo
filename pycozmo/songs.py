"""

Cozmo's songs: the MIDI tracks of its sound bank, sung with the notes recorded for it.

Cozmo sings 46 tunes, from Pop Goes the Weasel to the Toccata. Each is a MIDI track in Cozmo's sound bank, and the
sound engine of the Cozmo application played it at run time with an instrument of sung notes: a WWise container
whose sounds are Cozmo's voice holding a note, each kept for its MIDI key. Nothing of that reaches the robot as a
song: the application sent it the sound, as it sends any. So PyCozmo renders the song the same way, into sound for
the robot's speaker.

A singing animation's sound event plays a music switch, which picks the song from the switch its behavior set -
Cozmo_Sings_Pop_Goes_The_Weasel in the group Cozmo_Sings_100Bpm, for instance; see AudioLibrary.set_switch(). The
song's playlist plays a segment, the segment a MIDI track, and the switch names the instrument the track's notes
go to. In Cozmo's bank, the instrument blends three layers, which all play each note: the held note, one recorded
take of three at random for that key; a softer one, 14 dB down, as the note is released; and, for some notes, a
short syllable as it starts. A held note is a vowel sung for six seconds, at full voice to the end: an envelope,
which the note's release sets off, silences it then. How WWise works that envelope out is not known, but the
notes are at most a fraction of a second long, and left to ring they would make the tune a cluster of notes, so
the release is taken to cut the held note short. A vibrato the instrument also has is left out: its depth, read
as the bank has it, would take each note up most of a fourth, and the recorded notes waver already.

The MIDI tracks are not standard MIDI files: two bytes of ticks per quarter note, big endian, the tempo in beats
per minute as a float, then MIDI events with variable length deltas, as in a standard MIDI track.

"""

import random
import struct
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import numpy as np

from .audiokinetic import nodes


__all__ = [
    "Note",
    "Voice",

    "read_midi",
    "find_song",
    "voices",
    "render",
]


#: How long the held note takes to fall silent once released, in seconds: enough not to click.
RELEASE_FADE = 0.01


class Note(NamedTuple):
    """ A note of a song. """

    #: When it starts and ends, in seconds.
    start: float
    end: float
    #: MIDI key and velocity.
    key: int
    velocity: int


class Voice(NamedTuple):
    """ A sound a note plays, and how. """

    #: Its media file.
    source_id: int
    #: Gain, in dB, and pitch, in cents, from the containers above it and the sound itself.
    volume: float
    pitch: float
    #: Whether it plays as the note is released rather than as it starts.
    on_release: bool
    #: Whether the release silences it.
    cut_on_release: bool
    #: Whether it loops for as long as the note is held.
    loops: bool


def read_midi(data: bytes) -> List[Note]:
    """ The notes of one of WWise's MIDI tracks, in time order of their start. """
    ticks_per_quarter = struct.unpack_from(">H", data, 0)[0]
    tempo = struct.unpack_from("<f", data, 2)[0]
    seconds_per_tick = 60.0 / tempo / ticks_per_quarter
    notes = []
    held: Dict[Tuple[int, int], Tuple[int, int]] = {}
    offset, tick, status = 6, 0, 0
    while offset < len(data):
        delta, offset = _variable_length(data, offset)
        tick += delta
        byte = data[offset]
        if byte == 0xFF:
            # A meta event: its type, its length, its data. The end of the track ends it.
            kind, length = data[offset + 1], data[offset + 2]
            offset += 3 + length
            if kind == 0x2F:
                break
            continue
        if byte == 0xF0 or byte == 0xF7:
            length, offset = _variable_length(data, offset + 1)
            offset += length
            continue
        if byte & 0x80:
            status = byte
            offset += 1
        kind, channel = status & 0xF0, status & 0x0F
        size = 1 if kind in (0xC0, 0xD0) else 2
        args = data[offset:offset + size]
        offset += size
        if kind == 0x90 and args[1] > 0:
            held[(channel, args[0])] = (tick, args[1])
        elif kind in (0x80, 0x90) and (channel, args[0]) in held:
            start, velocity = held.pop((channel, args[0]))
            notes.append(Note(start * seconds_per_tick, tick * seconds_per_tick, args[0], velocity))
    return sorted(notes)


def _variable_length(data: bytes, offset: int) -> Tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset


def find_song(library: Any, music_switch_id: int, switch_value: Optional[int] = None) -> Optional[Tuple[bytes, int]]:
    """
    The song a music switch plays, as its switch stands in the library or for a value of it: the MIDI track's data,
    and the instrument its notes go to. None if the switch picks nothing, or what it picks holds no MIDI.
    """
    switch = library.nodes.get(music_switch_id)
    if not isinstance(switch, nodes.MusicSwitch):
        return None
    if switch_value is None:
        switch_value = library.switches.get(switch.group_id, 0)
    child = switch.children_by_switch.get(switch_value)
    if child is None:
        return None
    # Down the playlist and the segment to the track.
    for _ in range(4):
        node = library.nodes.get(child)
        if isinstance(node, nodes.MusicTrack):
            for plugin_id, source_id in node.sources:
                data = library.media.get(source_id)
                if plugin_id == nodes.MIDI_PLUGIN and data is not None:
                    target = _midi_target(library, node.id)
                    return (data, target) if target else None
            return None
        children = library.children.get(child, [])
        if not children:
            return None
        child = children[0]
    return None


def _midi_target(library: Any, node_id: int) -> int:
    """ The instrument a music node's notes go to: its own, or the nearest one above it. """
    for _ in range(16):
        node = library.nodes.get(node_id)
        if node is None:
            return 0
        target = node.props.get(nodes.PROP_MIDI_TARGET_NODE)
        if target:
            return int(target)
        node_id = node.parent_id
    return 0


def voices(library: Any, target_id: int, key: int, rng: random.Random) -> List[Voice]:
    """
    The sounds an instrument plays for a key, as WWise plays MIDI: a blend plays all its children, a random
    container one of those whose key range takes the key, and a node outside its own key range nothing.
    """
    # The containers above the instrument count too: the nearest says when a note plays.
    volume, pitch, on_release, cut_on_release = 0.0, 0.0, None, False
    parent = library.nodes[target_id].parent_id if target_id in library.nodes else 0
    for _ in range(16):
        node = library.nodes.get(parent)
        if node is None:
            break
        volume += node.props.get(nodes.PROP_VOLUME, 0.0)
        pitch += node.props.get(nodes.PROP_PITCH, 0.0)
        play_on = node.props.get(nodes.PROP_MIDI_PLAY_ON_NOTE_TYPE)
        if on_release is None and play_on is not None:
            on_release = play_on == 2
        cut_on_release = cut_on_release or _silenced_on_release(library, node)
        parent = node.parent_id
    return _voices(library, target_id, key, rng, volume, pitch, bool(on_release), cut_on_release, 0)


def _voices(library: Any, node_id: int, key: int, rng: random.Random, volume: float, pitch: float,
            on_release: bool, cut_on_release: bool, depth: int) -> List[Voice]:
    node = library.nodes.get(node_id)
    if node is None or depth > 16 or not _takes_key(node, key):
        return []
    volume += node.props.get(nodes.PROP_VOLUME, 0.0)
    pitch += node.props.get(nodes.PROP_PITCH, 0.0)
    play_on = node.props.get(nodes.PROP_MIDI_PLAY_ON_NOTE_TYPE)
    if play_on is not None:
        on_release = play_on == 2
    cut_on_release = cut_on_release or _silenced_on_release(library, node)
    if node.type == nodes.SOUND:
        loops = node.props.get(nodes.PROP_LOOP) == 0
        return [Voice(node.source_id, volume, pitch, on_release, cut_on_release, loops)]
    children = library.children.get(node_id, [])
    if node.type == nodes.RANDOM_SEQUENCE:
        eligible = [child for child in children
                    if child in library.nodes and _takes_key(library.nodes[child], key)]
        children = [rng.choice(eligible)] if eligible else []
    elif node.type != nodes.BLEND:
        # Switch containers follow a switch songs do not set.
        return []
    out = []
    for child in children:
        out += _voices(library, child, key, rng, volume, pitch, on_release, cut_on_release, depth + 1)
    return out


def _takes_key(node: Any, key: int) -> bool:
    low = node.props.get(nodes.PROP_MIDI_KEY_RANGE_MIN, 0)
    high = node.props.get(nodes.PROP_MIDI_KEY_RANGE_MAX, 127)
    return bool(low <= key <= high)


def _silenced_on_release(library: Any, node: Any) -> bool:
    """ Whether an envelope the release sets off takes the node's volume down to nothing. """
    for modulator_id, param, curve in node.modulators:
        modulator = library.nodes.get(modulator_id)
        if not isinstance(modulator, nodes.Modulator) or modulator.type != nodes.ENVELOPE_MODULATOR:
            continue
        if param == nodes.PARAM_VOLUME and modulator.props.get(nodes.MOD_ENVELOPE_TRIGGER_ON) == 2 and \
                min(y for _, y in curve) <= -1.0:
            return True
    return False


def render(library: Any, notes: List[Note], target_id: int, sample_rate: int,
           rng: Optional[random.Random] = None) -> np.ndarray:
    """ Sing the notes with an instrument, as 16 bit samples at a rate. """
    rng = rng or random.Random()
    plan = [(note, voice) for note in notes for voice in voices(library, target_id, note.key, rng)]
    if not plan:
        return np.zeros(0, dtype=np.int16)
    decoded: Dict[Tuple[int, float], np.ndarray] = {}
    pieces = []
    for note, voice in plan:
        key = (voice.source_id, voice.pitch)
        if key not in decoded:
            decoded[key] = _decode(library, voice.source_id, voice.pitch, sample_rate)
        samples = decoded[key] * 10.0 ** (voice.volume / 20.0)
        if not len(samples):
            continue
        start = note.end if voice.on_release else note.start
        if not voice.on_release:
            held = int(round((note.end - note.start) * sample_rate))
            if voice.loops and held > len(samples):
                samples = np.tile(samples, -(-held // len(samples)))
            if voice.cut_on_release:
                fade = int(RELEASE_FADE * sample_rate)
                samples = samples[:held + fade].copy()
                tail = samples[held:]
                tail *= np.linspace(1.0, 0.0, len(tail), endpoint=False)
        pieces.append((int(round(start * sample_rate)), samples))
    if not pieces:
        # Nothing decodes: the sung notes are WWise Vorbis, which play once converted.
        return np.zeros(0, dtype=np.int16)
    out = np.zeros(max(at + len(samples) for at, samples in pieces))
    for at, samples in pieces:
        out[at:at + len(samples)] += samples
    rendered: np.ndarray = np.clip(np.rint(out), -32768, 32767).astype(np.int16)
    return rendered


def _decode(library: Any, source_id: int, pitch: float, sample_rate: int) -> np.ndarray:
    """ A sound as floats at a rate, raised by a pitch in cents: played that much faster. """
    samples, channels, rate = library.get_pcm(source_id)
    data = np.array(samples, dtype=np.float64)
    if not len(data):
        return data
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    step = rate / sample_rate * 2.0 ** (pitch / 1200.0)
    count = max(int(len(data) / step), 1)
    resampled: np.ndarray = np.interp(np.arange(count) * step, np.arange(len(data)), data)
    return resampled
