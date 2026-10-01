"""

Tests for Cozmo's songs: reading WWise's MIDI tracks, choosing the sung notes, rendering them, and the behavior
that sings.

"""

import glob
import json
import os
import random
import struct
import unittest
from unittest import mock
from typing import Any, Dict, List, Tuple

import numpy as np

import pycozmo
from pycozmo import anim, anim_encoder, audiolib, protocol_encoder, song_behaviors, songs
from pycozmo.audiokinetic import nodes, soundbank

from .test_brain import cozmo_assets_available
from .test_cube_behaviors import ScriptTestCase


RATE = 1000


def midi(ticks_per_quarter: int, tempo: float, events: bytes) -> bytes:
    """ A MIDI track as WWise keeps one. """
    return struct.pack(">H", ticks_per_quarter) + struct.pack("<f", tempo) + events + b"\x00\xff\x2f\x00"


class TestReadMidi(unittest.TestCase):

    def test_notes_in_seconds(self):
        # 960 ticks a quarter at 120 bpm: a quarter is half a second. Note offs as note ons at 0 too, and running
        # status, as WWise writes them; and a two byte delta.
        data = midi(960, 120.0, b"\x00\x90\x3c\x64" + b"\x87\x40\x3c\x00" + b"\x00\x3e\x50" + b"\x83\x60\x80\x3e\x00")
        self.assertEqual(songs.read_midi(data), [songs.Note(0.0, 0.5, 60, 100), songs.Note(0.5, 0.75, 62, 80)])

    def test_what_is_not_a_note_is_passed_over(self):
        data = midi(960, 60.0, b"\x00\xff\x51\x03\x07\xa1\x20" + b"\x00\xc0\x05" + b"\x00\x90\x40\x40" +
                    b"\x60\x80\x40\x00")
        self.assertEqual(songs.read_midi(data), [songs.Note(0.0, 0.1, 64, 64)])


class FakeNode(nodes.Node):

    def __init__(self, node_id: int, node_type: int, parent_id: int, source_id: int = 0, **props: Any) -> None:
        names = {"volume": nodes.PROP_VOLUME, "pitch": nodes.PROP_PITCH, "low": nodes.PROP_MIDI_KEY_RANGE_MIN,
                 "high": nodes.PROP_MIDI_KEY_RANGE_MAX, "play_on": nodes.PROP_MIDI_PLAY_ON_NOTE_TYPE,
                 "loop": nodes.PROP_LOOP, "target": nodes.PROP_MIDI_TARGET_NODE}
        super().__init__(node_id, node_type, parent_id, {names[k]: v for k, v in props.items()}, source_id)


class FakeLibrary:
    """ An instrument like Cozmo's: held notes for keys 60 and 62, a softer one on release, an onset for any. """

    def __init__(self) -> None:
        self.nodes: Dict[int, Any] = {}
        self.children: Dict[int, List[int]] = {}
        self.switches: Dict[int, int] = {}
        self.media: Dict[int, bytes] = {}
        self.decoded: List[int] = []
        self.add(FakeNode(1, nodes.ACTOR_MIXER, 0, volume=-2.0))
        self.add(FakeNode(2, nodes.BLEND, 1))
        # The held notes, which a release envelope silences.
        self.add(FakeNode(10, nodes.BLEND, 2))
        self.nodes[10].modulators.append((90, nodes.PARAM_VOLUME, [(0.0, 0.0), (1.0, -1.0)]))
        self.add(nodes.Modulator(90, nodes.ENVELOPE_MODULATOR, {nodes.MOD_ENVELOPE_TRIGGER_ON: 2}))
        self.add(FakeNode(11, nodes.RANDOM_SEQUENCE, 10, low=60, high=60, volume=-4.0))
        self.add(FakeNode(12, nodes.SOUND, 11, source_id=600, loop=0))
        self.add(FakeNode(13, nodes.SOUND, 11, source_id=601, loop=0))
        self.add(FakeNode(14, nodes.RANDOM_SEQUENCE, 10, low=62, high=62, pitch=100.0))
        self.add(FakeNode(15, nodes.SOUND, 14, source_id=620, loop=0))
        # On release.
        self.add(FakeNode(20, nodes.BLEND, 2, volume=-14.0, play_on=2))
        self.add(FakeNode(21, nodes.RANDOM_SEQUENCE, 20, low=60, high=60))
        self.add(FakeNode(22, nodes.SOUND, 21, source_id=700))
        # Onsets: a random container whose children take a key, or any.
        self.add(FakeNode(30, nodes.RANDOM_SEQUENCE, 2))
        self.add(FakeNode(31, nodes.SOUND, 30, source_id=800, low=62, high=62))
        self.add(FakeNode(32, nodes.SOUND, 30, source_id=801))

    def add(self, node: Any) -> None:
        self.nodes[node.id] = node
        if isinstance(node, nodes.Node) and node.parent_id:
            self.children.setdefault(node.parent_id, []).append(node.id)

    def get_pcm(self, source_id: int) -> Tuple[np.ndarray, int, int]:
        """ Each sound a constant level, its own, a tenth of a second long at RATE. """
        self.decoded.append(source_id)
        return np.full(100, 1000, dtype=np.int16), 1, RATE


class TestVoices(unittest.TestCase):

    def setUp(self):
        self.library = FakeLibrary()

    def test_each_layer_plays_its_part(self):
        voices = songs.voices(self.library, 2, 60, random.Random(0))
        by_source = {voice.source_id: voice for voice in voices}
        self.assertEqual(len(voices), 3)
        held = by_source.get(600) or by_source[601]
        # The containers above add up: the mixer's -2 dB, the key's -4 dB.
        self.assertEqual(held, songs.Voice(held.source_id, -6.0, 0.0, False, True, True))
        self.assertEqual(by_source[700], songs.Voice(700, -16.0, 0.0, True, False, False))
        # The onset that takes any key.
        self.assertEqual(by_source[801], songs.Voice(801, -2.0, 0.0, False, False, False))

    def test_a_random_container_picks_among_those_that_take_the_key(self):
        sources = {voice.source_id for seed in range(20)
                   for voice in songs.voices(self.library, 2, 62, random.Random(seed))}
        self.assertEqual(sources & {800, 801}, {800, 801})
        self.assertEqual(sources & {600, 601, 700}, set())
        # The key's pitch correction comes along.
        voice = next(v for v in songs.voices(self.library, 2, 62, random.Random(0)) if v.source_id == 620)
        self.assertEqual(voice.pitch, 100.0)

    def test_a_key_out_of_range_plays_only_what_takes_any(self):
        self.assertEqual({voice.source_id for voice in songs.voices(self.library, 2, 70, random.Random(0))}, {801})


class TestRender(unittest.TestCase):

    def setUp(self):
        self.library = FakeLibrary()

    def test_the_held_note_until_released_then_the_release(self):
        # A held note longer than its sound loops it; the release silences it, and plays its own sound.
        notes = [songs.Note(0.0, 0.25, 60, 100)]
        rendered = songs.render(self.library, notes, 10, RATE, random.Random(0)).astype(float)
        fade = int(songs.RELEASE_FADE * RATE)
        self.assertEqual(len(rendered), 250 + fade)
        # The mixer's -2 dB above, and the key's -4 dB.
        self.assertTrue(np.allclose(rendered[:250], 1000 * 10 ** (-6 / 20.0), atol=1.0))
        self.assertLess(rendered[-1], 100)
        rendered = songs.render(self.library, notes, 20, RATE, random.Random(0)).astype(float)
        self.assertEqual(len(rendered), 350)
        self.assertTrue(np.allclose(rendered[:250], 0.0))
        self.assertTrue(np.allclose(rendered[250:], 1000 * 10 ** (-16 / 20.0), atol=1.0))

    def test_a_raised_note_plays_faster(self):
        # 100 cents up: the tenth of a second ends that much sooner.
        self.assertEqual(len(songs._decode(self.library, 620, 100.0, RATE)), int(100 / 2 ** (1 / 12.0)))

    def test_what_is_above_the_instrument_counts(self):
        # Sung from a key's own container, the held note still falls silent on release.
        voice, = songs.voices(self.library, 14, 62, random.Random(0))
        self.assertEqual(voice, songs.Voice(620, -2.0, 100.0, False, True, True))

    def test_each_sound_is_decoded_once(self):
        notes = [songs.Note(i * 0.2, i * 0.2 + 0.1, 60, 100) for i in range(5)]
        songs.render(self.library, notes, 20, RATE, random.Random(0))
        self.assertEqual(self.library.decoded, [700])

    def test_nothing_to_sing(self):
        self.assertEqual(len(songs.render(self.library, [], 2, RATE)), 0)

    def test_nor_anything_to_sing_with(self):
        with mock.patch.object(self.library, "get_pcm", return_value=(np.zeros(0, dtype=np.int16), 1, RATE)):
            self.assertEqual(len(songs.render(self.library, [songs.Note(0.0, 0.1, 60, 100)], 2, RATE)), 0)


class TestLibrary(unittest.TestCase):
    """ The audio library's side: which events sing, and what. """

    def setUp(self):
        self.library = audiolib.AudioLibrary()
        fake = FakeLibrary()
        for node in fake.nodes.values():
            self.library.nodes[node.id] = node
        self.library.children = fake.children
        group, song = nodes.fnv_hash("Cozmo_Sings_100Bpm"), nodes.fnv_hash("Cozmo_Sings_Twinkle")
        self.library.nodes[50] = nodes.MusicSwitch(50, nodes.MUSIC_SWITCH, 0, {nodes.PROP_MIDI_TARGET_NODE: 2},
                                                   group, {song: 51})
        for node in (nodes.Node(51, nodes.MUSIC_PLAYLIST, 50, {}), nodes.Node(52, nodes.MUSIC_SEGMENT, 51, {}),
                     nodes.MusicTrack(53, nodes.MUSIC_TRACK, 52, {}, [(nodes.MIDI_PLUGIN, 900)])):
            self.library.nodes[node.id] = node
            self.library.children.setdefault(node.parent_id, []).append(node.id)
        self.midi = midi(960, 120.0, b"\x00\x90\x3c\x64\x87\x40\x3c\x00")
        self.library.media[900] = self.midi
        self.library.events[1] = soundbank.Event(0, 1, "Play__Singing", [11])
        self.library.actions[11] = soundbank.EventAction(0, 11, 3, audiolib.PLAY_ACTION, 50)
        self.library.events[2] = soundbank.Event(0, 2, "Stop__Singing", [12])
        self.library.actions[12] = soundbank.EventAction(0, 12, 3, audiolib.STOP_ACTION, 50)

    def test_which_events_sing_and_stop(self):
        self.assertTrue(self.library.is_song(1))
        self.assertFalse(self.library.is_song(2))
        self.assertEqual(self.library.song_switch(1), 50)
        self.assertEqual(self.library.song_stops(2), [50])
        self.assertEqual(self.library.song_stops(1), [])

    def test_the_switch_picks_the_song(self):
        self.assertIsNone(self.library.get_song(1))
        self.library.set_switch("Cozmo_Sings_100Bpm", "Cozmo_Sings_Twinkle")
        self.assertEqual(self.library.get_song(1), (self.midi, 2))

    def test_a_song_it_has_and_can_sing(self):
        with mock.patch.object(audiolib.AudioLibrary, "is_playable", return_value=True):
            self.assertTrue(self.library.has_song("Cozmo_Sings_100Bpm", "Cozmo_Sings_Twinkle"))
            self.assertFalse(self.library.has_song("Cozmo_Sings_100Bpm", "Cozmo_Sings_Bingo"))
        # Its notes not converted, it cannot.
        self.assertFalse(self.library.has_song("Cozmo_Sings_100Bpm", "Cozmo_Sings_Twinkle"))

    def test_the_song_as_frames(self):
        self.library.set_switch("Cozmo_Sings_100Bpm", "Cozmo_Sings_Twinkle")
        with mock.patch.object(audiolib.AudioLibrary, "get_pcm",
                               return_value=(np.full(4410, 1000, dtype=np.int16), 1, 22050)):
            frames = self.library.get_frames(1)
        # Half a second of held note, then the release's fifth of a second.
        self.assertEqual(len(frames), -(-int(0.7 * 22050) // audiolib.FRAME_SAMPLES))


class TestStopInAnimation(unittest.TestCase):

    def test_a_stop_cuts_the_song_short(self):
        library = mock.Mock()
        song = [protocol_encoder.OutputAudio(samples=bytes([i]) * 744) for i in range(10)]
        library.get_frames.side_effect = lambda event_id, volume: song if event_id == 1 else []
        library.song_switch.side_effect = lambda event_id: 50 if event_id == 1 else None
        library.song_stops.side_effect = lambda event_id: [50] if event_id == 2 else []
        clip = anim_encoder.AnimClip("singing", [
            anim_encoder.AnimRobotAudio(trigger_time_ms=0, audio_event_ids=[1]),
            anim_encoder.AnimRobotAudio(trigger_time_ms=4 * pycozmo.robot.FRAME_MS + 10, audio_event_ids=[2]),
        ])
        clip_frames = anim.PreprocessedClip.from_anim_clip(clip, library)
        audio = [pkt for pkts in clip_frames.keyframes.values() for pkt in pkts
                 if isinstance(pkt, protocol_encoder.OutputAudio)]
        self.assertEqual(audio, song[:5])


class TestSinging(ScriptTestCase):

    def setUp(self):
        super().setUp()
        self.cli.animation_groups.update({name: None for name in (
            "Singing_GetIn", "Singing_100bpm", "Singing_GetOut")})
        cli: Any = self.cli
        cli.audio_library = mock.Mock()
        self.switches: List[Any] = []
        cli.set_audio_switch = lambda group, value: self.switches.append((group, value))

    def make_singing(self, group: str = "Cozmo_Sings_100Bpm") -> Any:
        return self.make(song_behaviors.BehaviorSinging, audioSwitchGroup=group,
                         audioSwitch="Cozmo_Sings_Pop_Goes_The_Weasel", needsActionID="CozmoSingsCompleted")

    def test_the_song_at_its_tempo(self):
        script = self.make_singing()
        self.assertEqual(script.tempo_trigger(), "Singing_100bpm")
        self.assertTrue(script.wants_to_run())
        self.run_script(script)
        self.assertEqual(self.switches, [("Cozmo_Sings_100Bpm", "Cozmo_Sings_Pop_Goes_The_Weasel")])
        self.assertEqual(self.cli.played, ["Singing_GetIn", "Singing_100bpm", "Singing_GetOut"])
        self.assertEqual(self.needs.actions, ["CozmoSingsCompleted"])

    def test_not_a_song_it_does_not_know(self):
        cli: Any = self.cli
        cli.audio_library.has_song.return_value = False
        self.assertFalse(self.make_singing().wants_to_run())

    def test_nor_at_a_tempo_without_animations(self):
        self.assertFalse(self.make_singing("Cozmo_Sings_90Bpm").wants_to_run())

    def test_registered(self):
        self.assertIs(pycozmo.behavior.get_behavior_class_from_dict({"behaviorClass": "Singing"}),
                      song_behaviors.BehaviorSinging)
        self.assertIs(pycozmo.behavior.get_behavior_class_from_dict({"behaviorClass": "Dance"}),
                      song_behaviors.BehaviorDance)

    def test_a_dance(self):
        self.cli.animation_groups.update({"DanceMambo": None})
        script = self.make(song_behaviors.BehaviorDance, animTriggers=["DanceMambo"])
        self.assertTrue(script.wants_to_run())
        self.run_script(script)
        self.assertEqual(self.cli.played, ["DanceMambo"])


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestCozmoSongs(unittest.TestCase):
    """ The 39 songs Cozmo's singing behaviors name, from its own sound bank. """

    resource_dir: str
    library: audiolib.AudioLibrary

    @classmethod
    def setUpClass(cls):
        cls.resource_dir = str(pycozmo.util.get_cozmo_asset_dir())
        cls.library = audiolib.load_audio_library(cls.resource_dir)

    def test_every_song_is_there_with_every_note(self):
        pattern = os.path.join(self.resource_dir, "cozmo_resources", "config", "engine", "behaviorSystem",
                               "behaviors", "freeplay", "singing", "*.json")
        confs = [json.load(open(path)) for path in sorted(glob.glob(pattern))]
        self.assertEqual(len(confs), 39)
        for conf in confs:
            with self.subTest(song=conf["audioSwitch"]):
                self.library.set_switch(conf["audioSwitchGroup"], conf["audioSwitch"])
                switch = next(node for node in self.library.nodes.values()
                              if isinstance(node, nodes.MusicSwitch) and
                              node.group_id == nodes.fnv_hash(conf["audioSwitchGroup"]))
                found = songs.find_song(self.library, switch.id)
                assert found is not None
                data, target = found
                notes = songs.read_midi(data)
                self.assertGreater(len(notes), 10)
                for key in {note.key for note in notes}:
                    held = [v for v in songs.voices(self.library, target, key, random.Random(0)) if not v.on_release]
                    self.assertTrue(held, "key {}".format(key))
