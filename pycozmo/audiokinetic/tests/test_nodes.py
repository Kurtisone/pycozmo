import io
import struct
import unittest

import pycozmo
from pycozmo.audiokinetic import nodes


def node_base(parent, props=(), effects=0):
    """ The start of a node's parameters, as a bank of version 120 has it. """
    out = bytes([0, effects])
    if effects:
        out += b"\x00" + b"\x00" * 7 * effects
    out += b"\x00" + struct.pack("<LL", 0, parent) + b"\x00"
    out += bytes([len(props)]) + bytes(prop_id for prop_id, _ in props)
    for prop_id, value in props:
        out += struct.pack("<l" if isinstance(value, int) else "<f", value)
    return out + b"\x00"


def modulator_entry(modulator_id, param, points):
    """ An RTPC entry for a modulator, as it sits further on in a node's parameters. """
    out = struct.pack("<LBBBLBH", modulator_id, 2, 1, param, 0x1234, 2, len(points))
    return out + b"".join(struct.pack("<ffL", x, y, 4) for x, y in points)


def sound(source_id, parent, props=(), tail=b""):
    return struct.pack("<LBLLB", 0x40001, 1, source_id, 1000, 0) + node_base(parent, props) + tail


def music_track(parent, midi_id, midi_size=6):
    out = b"\x00" + struct.pack("<L", 1) + struct.pack("<LBLLB", nodes.MIDI_PLUGIN, 0, midi_id, midi_size, 0)
    out += struct.pack("<L", 1) + struct.pack("<LL", 0, midi_id) + b"\x00" * 32 + struct.pack("<L", 1)
    out += struct.pack("<L", 0)
    return out + node_base(parent) + b"\x00" * 12


def music_switch(parent, group_id, children, props=()):
    tree = struct.pack("<LHHHH", 0, 1, len(children), 50, 100)
    for value_id, child in children.items():
        tree += struct.pack("<LLHH", value_id, child, 50, 100)
    return b"\x00" + node_base(parent, props) + b"\x00" * 20 + struct.pack("<LLB", 1, group_id, 0) + \
        struct.pack("<LB", len(tree), 0) + tree


class TestHash(unittest.TestCase):

    def test_as_wwise_names_them(self):
        # The switch group Cozmo's 100 bpm songs follow, as its sound bank has it.
        self.assertEqual(nodes.fnv_hash("Cozmo_Sings_100Bpm"), 3759662965)
        self.assertEqual(nodes.fnv_hash("cozmo_sings_100bpm"), nodes.fnv_hash("COZMO_SINGS_100BPM"))


class TestReadNode(unittest.TestCase):

    def test_a_sound_and_its_properties(self):
        node = nodes.read_node(nodes.SOUND, 7, sound(1234, 99, ((nodes.PROP_VOLUME, -3.0), (nodes.PROP_LOOP, 0))))
        assert isinstance(node, nodes.Node)
        self.assertEqual((node.id, node.type, node.parent_id, node.source_id), (7, nodes.SOUND, 99, 1234))
        self.assertEqual(node.props, {nodes.PROP_VOLUME: -3.0, nodes.PROP_LOOP: 0})

    def test_past_its_effects(self):
        data = struct.pack("<LBLLB", 0x40001, 1, 1234, 1000, 0) + \
            node_base(99, ((nodes.PROP_MIDI_KEY_RANGE_MIN, 48), (nodes.PROP_MIDI_KEY_RANGE_MAX, 50)), effects=2)
        node = nodes.read_node(nodes.SOUND, 7, data)
        assert isinstance(node, nodes.Node)
        self.assertEqual(node.parent_id, 99)
        self.assertEqual(node.props, {nodes.PROP_MIDI_KEY_RANGE_MIN: 48, nodes.PROP_MIDI_KEY_RANGE_MAX: 50})

    def test_the_modulators_acting_on_a_container(self):
        data = node_base(5, ((nodes.PROP_PITCH, 30.0), )) + b"\x07" * 13 + \
            modulator_entry(4242, nodes.PARAM_VOLUME, ((0.0, 0.0), (1.0, -1.0))) + b"\x00" * 6
        node = nodes.read_node(nodes.BLEND, 8, data)
        assert isinstance(node, nodes.Node)
        self.assertEqual(node.props, {nodes.PROP_PITCH: 30.0})
        self.assertEqual(node.modulators, [(4242, nodes.PARAM_VOLUME, [(0.0, 0.0), (1.0, -1.0)])])

    def test_a_music_switch_and_its_tree(self):
        node = nodes.read_node(nodes.MUSIC_SWITCH, 9, music_switch(
            3, 1111, {2222: 30, 3333: 31}, ((nodes.PROP_MIDI_TARGET_NODE, 77), )))
        assert isinstance(node, nodes.MusicSwitch)
        self.assertEqual(node.group_id, 1111)
        self.assertEqual(node.children_by_switch, {2222: 30, 3333: 31})
        self.assertEqual(node.props[nodes.PROP_MIDI_TARGET_NODE], 77)

    def test_a_music_track_and_its_midi(self):
        node = nodes.read_node(nodes.MUSIC_TRACK, 10, music_track(20, 555))
        assert isinstance(node, nodes.MusicTrack)
        self.assertEqual(node.sources, [(nodes.MIDI_PLUGIN, 555)])
        self.assertEqual(node.parent_id, 20)

    def test_a_modulator(self):
        data = bytes([2, nodes.MOD_ENVELOPE_SUSTAIN_LEVEL, nodes.MOD_ENVELOPE_TRIGGER_ON]) + \
            struct.pack("<fl", 9.5, 2) + b"\x00"
        node = nodes.read_node(nodes.ENVELOPE_MODULATOR, 11, data)
        assert isinstance(node, nodes.Modulator)
        self.assertEqual(node.props, {nodes.MOD_ENVELOPE_SUSTAIN_LEVEL: 9.5, nodes.MOD_ENVELOPE_TRIGGER_ON: 2})

    def test_what_does_not_read_is_left_out(self):
        self.assertIsNone(nodes.read_node(nodes.SOUND, 7, b"\x01\x02"))
        self.assertIsNone(nodes.read_node(4, 7, b"\x00" * 20))


class TestBank(unittest.TestCase):

    HEADER = b"BKHD\x20\0\0\0\x78\0\0\0\x11\x22\x33\x44" + b"\0" * 24

    def bank(self, objects, data=b"", index=b""):
        hirc = struct.pack("<L", len(objects))
        for object_type, object_id, body in objects:
            hirc += struct.pack("<BLL", object_type, len(body) + 4, object_id) + body
        out = self.HEADER
        if index:
            out += b"DIDX" + struct.pack("<L", len(index)) + index
        if data:
            out += b"DATA" + struct.pack("<L", len(data)) + data
        out += b"HIRC" + struct.pack("<L", len(hirc)) + hirc
        reader = pycozmo.audiokinetic.soundbank.SoundBankReader({})
        return reader.load_file(io.BytesIO(out), "test")

    def test_the_hierarchy_and_the_midi_its_tracks_play(self):
        midi = b"\x25\x80\x00\x00\xc8\x42"
        data = b"\xff" * 10 + midi
        bank = self.bank([(nodes.MUSIC_TRACK, 10, music_track(20, 555, len(midi))),
                          (nodes.SOUND, 7, sound(1234, 99))],
                         data=data, index=struct.pack("<LLL", 555, 10, len(midi)))
        self.assertEqual(set(bank.nodes), {7, 10})
        self.assertEqual(bank.media, {555: midi})

    def test_other_versions_are_not_read(self):
        self.HEADER = b"BKHD\x20\0\0\0\x71\0\0\0\x11\x22\x33\x44" + b"\0" * 24
        bank = self.bank([(nodes.SOUND, 7, sound(1234, 99))])
        self.assertEqual(bank.nodes, {})
