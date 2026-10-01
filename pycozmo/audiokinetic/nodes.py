"""

AudioKinetic WWise object hierarchy, as far as MIDI playback needs it: who is whose parent, the properties that
choose and shape a note, the modulators that act on them, and the music objects that lead from an event to a MIDI
track.

Cozmo sings this way. An animation's event plays a music switch container; the song, set as a switch, picks a
playlist out of it, which plays a segment, which plays a MIDI track. The notes of that track go to an instrument,
a container whose sounds are sung notes, each kept for a range of MIDI keys. Wwise did the rest at run time: the
objects below are what PyCozmo needs to do it too.

The layouts are those of Cozmo's bank, version 120 - WWise 2016.2 - as bnnm's wwiser, https://github.com/bnnm/wwiser,
documents them in its parser. A node's parameters start the same way whatever the object, and that start - effects,
parent, properties - is all that is read of most; the modulators acting on a node come further on, after sections whose
layout varies, and are found by their identifiers instead. Other versions are not read.


"""

import struct
from typing import Dict, List, Optional, Tuple, Union


__all__ = [
    "VERSION",
    "SOUND",
    "RANDOM_SEQUENCE",
    "SWITCH",
    "ACTOR_MIXER",
    "BLEND",
    "MUSIC_SEGMENT",
    "MUSIC_TRACK",
    "MUSIC_SWITCH",
    "MUSIC_PLAYLIST",
    "LFO_MODULATOR",
    "ENVELOPE_MODULATOR",
    "MIDI_PLUGIN",

    "PROP_VOLUME",
    "PROP_PITCH",
    "PROP_MIDI_PLAY_ON_NOTE_TYPE",
    "PROP_MIDI_KEY_RANGE_MIN",
    "PROP_MIDI_KEY_RANGE_MAX",
    "PROP_MIDI_TARGET_NODE",
    "PROP_LOOP",
    "PARAM_VOLUME",
    "PARAM_PITCH",
    "MOD_ENVELOPE_TRIGGER_ON",

    "Node",
    "MusicSwitch",
    "MusicTrack",
    "Modulator",

    "read_node",
    "fnv_hash",
]


#: The bank version these layouts are those of.
VERSION = 120

#: HIRC object types.
SOUND = 2
RANDOM_SEQUENCE = 5
SWITCH = 6
ACTOR_MIXER = 7
BLEND = 9
MUSIC_SEGMENT = 10
MUSIC_TRACK = 11
MUSIC_SWITCH = 12
MUSIC_PLAYLIST = 13
LFO_MODULATOR = 21
ENVELOPE_MODULATOR = 22

#: The plugin identifier of a MIDI source.
MIDI_PLUGIN = 0x00100001

#: Node property identifiers. A loop count of 0 loops for ever.
PROP_VOLUME = 0x00
PROP_PITCH = 0x02
PROP_MIDI_PLAY_ON_NOTE_TYPE = 0x2E
PROP_MIDI_KEY_RANGE_MIN = 0x31
PROP_MIDI_KEY_RANGE_MAX = 0x32
PROP_MIDI_TARGET_NODE = 0x38
PROP_LOOP = 0x3A
# Properties held as integers; the others are floats.
_INT_PROPS = frozenset(range(0x2D, 0x39)) | {PROP_LOOP}

#: Parameters a modulator can act on.
PARAM_VOLUME = 0
PARAM_PITCH = 2

#: Modulator property identifiers.
MOD_LFO_DEPTH = 0x2
MOD_LFO_ATTACK = 0x3
MOD_LFO_FREQUENCY = 0x4
MOD_ENVELOPE_ATTACK_TIME = 0x9
MOD_ENVELOPE_DECAY_TIME = 0xB
MOD_ENVELOPE_SUSTAIN_LEVEL = 0xC
MOD_ENVELOPE_RELEASE_TIME = 0xE
MOD_ENVELOPE_TRIGGER_ON = 0xF
_INT_MOD_PROPS = frozenset({0x0, 0x1, 0x5, MOD_ENVELOPE_TRIGGER_ON})

# How a node's modulator entry is laid out: the modulator, its RTPC type, accumulation, the parameter, the curve's
# identifier, its scaling, its point count, then its points.
_RTPC_TYPE_MODULATOR = 2


class Node:
    """ A node of the hierarchy: a sound, a container, or a music object. """

    __slots__ = [
        "id",
        "type",
        "parent_id",
        "props",
        "modulators",
        "source_id",
    ]

    def __init__(self, node_id: int, node_type: int, parent_id: int, props: Dict[int, float],
                 source_id: int = 0) -> None:
        self.id = node_id
        self.type = node_type
        self.parent_id = parent_id
        # Property values by identifier: see PROP_*.
        self.props = props
        # The modulators acting on the node: modulator identifier, the parameter it acts on, and the curve that
        # maps its output onto the parameter, as points.
        self.modulators: List[Tuple[int, int, List[Tuple[float, float]]]] = []
        # A sound's media file.
        self.source_id = source_id


class MusicSwitch(Node):
    """ A music switch container: which child plays for which value of the switch group it follows. """

    __slots__ = [
        "group_id",
        "children_by_switch",
    ]

    def __init__(self, node_id: int, node_type: int, parent_id: int, props: Dict[int, float],
                 group_id: int, children_by_switch: Dict[int, int]) -> None:
        super().__init__(node_id, node_type, parent_id, props)
        # The switch group, by its hash: see fnv_hash().
        self.group_id = group_id
        # Switch value hash -> child node.
        self.children_by_switch = children_by_switch


class MusicTrack(Node):
    """ A music track: its sources, MIDI ones among them. """

    __slots__ = [
        "sources",
    ]

    def __init__(self, node_id: int, node_type: int, parent_id: int, props: Dict[int, float],
                 sources: List[Tuple[int, int]]) -> None:
        super().__init__(node_id, node_type, parent_id, props)
        # Plugin identifier and media file of each source.
        self.sources = sources


class Modulator:
    """ An LFO or an envelope, which a node's properties can follow. """

    __slots__ = [
        "id",
        "type",
        "props",
    ]

    def __init__(self, modulator_id: int, modulator_type: int, props: Dict[int, float]) -> None:
        self.id = modulator_id
        self.type = modulator_type
        # Property values by identifier: see MOD_*.
        self.props = props


def fnv_hash(name: str) -> int:
    """ The identifier WWise gives a name: the 32 bit FNV-1 hash of it in lower case. """
    value = 2166136261
    for byte in name.lower().encode():
        value = (value * 16777619) & 0xFFFFFFFF
        value ^= byte
    return value


def read_node(object_type: int, object_id: int, data: bytes) -> Optional[Union[Node, Modulator]]:
    """ Read a HIRC object of the hierarchy, or None for one of another kind or that does not read. """
    try:
        if object_type == SOUND:
            return _read_sound(object_id, data)
        if object_type in (RANDOM_SEQUENCE, SWITCH, ACTOR_MIXER, BLEND):
            parent, props, _ = _node_base(data, 0)
            return _with_modulators(Node(object_id, object_type, parent, props), data)
        if object_type in (MUSIC_SEGMENT, MUSIC_PLAYLIST):
            # Music nodes start with their MIDI flags.
            parent, props, _ = _node_base(data, 1)
            return Node(object_id, object_type, parent, props)
        if object_type == MUSIC_SWITCH:
            return _read_music_switch(object_id, data)
        if object_type == MUSIC_TRACK:
            return _read_music_track(object_id, data)
        if object_type in (LFO_MODULATOR, ENVELOPE_MODULATOR):
            props, _ = _props(data, 0, _INT_MOD_PROPS)
            return Modulator(object_id, object_type, props)
    except (struct.error, IndexError):
        pass
    return None


def _read_sound(object_id: int, data: bytes) -> Node:
    plugin_id, _, source_id, _, _ = struct.unpack_from("<LBLLB", data, 0)
    offset = 14
    # Source plugins carry their parameters.
    if plugin_id & 0x0F in (2, 5):
        offset += 4 + struct.unpack_from("<L", data, offset)[0]
    parent, props, _ = _node_base(data, offset)
    return _with_modulators(Node(object_id, SOUND, parent, props, source_id), data)


def _read_music_switch(object_id: int, data: bytes) -> MusicSwitch:
    parent, props, _ = _node_base(data, 1)
    group_id, children = _decision_tree(data)
    return MusicSwitch(object_id, MUSIC_SWITCH, parent, props, group_id, children)


def _read_music_track(object_id: int, data: bytes) -> MusicTrack:
    offset = 1
    count = struct.unpack_from("<L", data, offset)[0]
    offset += 4
    sources = []
    for _ in range(count):
        plugin_id, _, source_id, _, _ = struct.unpack_from("<LBLLB", data, offset)
        offset += 14
        if plugin_id & 0x0F in (2, 5):
            offset += 4 + struct.unpack_from("<L", data, offset)[0]
        sources.append((plugin_id, source_id))
    # The playlist of the sources, then the clip automation, before the node's parameters.
    count = struct.unpack_from("<L", data, offset)[0]
    offset += 4 + count * 40
    if count:
        offset += 4
    count = struct.unpack_from("<L", data, offset)[0]
    offset += 4
    for _ in range(count):
        points = struct.unpack_from("<L", data, offset + 8)[0]
        offset += 12 + points * 12
    parent, props, _ = _node_base(data, offset)
    return MusicTrack(object_id, MUSIC_TRACK, parent, props, sources)


def _node_base(data: bytes, offset: int) -> Tuple[int, Dict[int, float], int]:
    """ The start of a node's parameters: its effects, its parent and its properties. """
    offset += 1                                         # whether it overrides its parent's effects
    effects = data[offset]
    offset += 1
    if effects:
        offset += 1 + effects * 7                       # which are bypassed; index, identifier and flags of each
    offset += 1                                         # whether it overrides attachment parameters
    _, parent = struct.unpack_from("<LL", data, offset)  # output bus, parent
    offset += 8 + 1                                     # and priority and MIDI flags
    props, offset = _props(data, offset, _INT_PROPS)
    # Then ranged properties, which nothing here needs.
    count = data[offset]
    offset += 1 + count + count * 8
    return parent, props, offset


def _props(data: bytes, offset: int, integers: frozenset) -> Tuple[Dict[int, float], int]:
    """ A property bundle: a count, the identifiers, then a 32 bit value each. """
    count = data[offset]
    ids = data[offset + 1:offset + 1 + count]
    offset += 1 + count
    props: Dict[int, float] = {}
    for prop_id in ids:
        fmt = "<l" if prop_id in integers else "<f"
        props[prop_id] = struct.unpack_from(fmt, data, offset)[0]
        offset += 4
    return props, offset


def _with_modulators(node: Node, data: bytes) -> Node:
    """
    Find the modulators acting on a node. Its RTPC entries come after sections whose layout varies from one
    object to another; an entry for a modulator is told by its shape instead: the modulator type, a parameter, a
    scaling and a curve whose points go up along their first axis.
    """
    offset = 0
    while True:
        offset = _find_rtpc(data, offset)
        if offset < 0:
            return node
        modulator_id, _, _, param, _, _, count = struct.unpack_from("<LBBBLBH", data, offset)
        points = [struct.unpack_from("<ff", data, offset + 14 + 12 * i) for i in range(count)]
        node.modulators.append((modulator_id, param, points))
        offset += 14 + 12 * count


def _find_rtpc(data: bytes, start: int) -> int:
    for offset in range(start, len(data) - 14):
        rtpc_type, accumulation, param = data[offset + 4], data[offset + 5], data[offset + 6]
        if rtpc_type != _RTPC_TYPE_MODULATOR or accumulation > 4 or param > 0x40:
            continue
        scaling, count = struct.unpack_from("<BH", data, offset + 11)
        if scaling > 4 or not 2 <= count <= 32 or offset + 14 + 12 * count > len(data):
            continue
        points = [struct.unpack_from("<ffL", data, offset + 14 + 12 * i) for i in range(count)]
        if all(b[0] > a[0] for a, b in zip(points, points[1:])) and all(p[2] <= 9 for p in points):
            return offset
    return -1


def _decision_tree(data: bytes) -> Tuple[int, Dict[int, int]]:
    """
    A music switch's decision tree, which ends its data: the group it follows, and the child for each value of
    the group. Only trees one group deep, as Cozmo's are, are read.
    """
    for size in range(12, len(data), 12):
        offset = len(data) - size - 5
        if offset < 9:
            break
        if struct.unpack_from("<L", data, offset)[0] != size:
            continue
        # Before the size: the depth and the group, then its type.
        depth, group_id, _ = struct.unpack_from("<LLB", data, offset - 9)
        if depth != 1:
            continue
        nodes = [struct.unpack_from("<LL", data, offset + 5 + 12 * i) for i in range(size // 12)]
        # The root first, then its children: a switch value and the node it plays.
        return group_id, {key: child for key, child in nodes[1:]}
    return 0, {}
