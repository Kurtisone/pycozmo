Sound
=====

An animation does not carry its sound. It names a WWise event by the 32 bit identifier WWise hashed its name into, and
the application has to work out what that event plays and stream the samples to the robot's speaker. PyCozmo now does:
893 of the 993 animations come with sound, and the speaker is given a volume on connection, which nothing did before -
the robot came up silent and stayed that way.

Getting from an identifier to samples needs Cozmo's own sound bank, which holds all 380 events its animations name and
is the one bank the resources do not unpack - it is read straight out of `AudioAssets.zip`.

**Two thirds plays out of the box.** Weighted by how often the animations actually trigger them, 66% of the audio
events resolve to IMA ADPCM, which `pycozmo.audiokinetic.wem` decodes: the screen, the servos, the blinks, the bored
noises. Another 32% are WWise Vorbis, which take one conversion first, and the last 2% are events the sound banks do
not resolve to a file at all.


Converting the Vorbis sounds
----------------------------

WWise does not store playable Vorbis. It strips the setup header's codebooks out and leaves 10 bit indices into a
library of 598 that lives in its sound engine - and that engine shipped inside the Cozmo application, not with the
robot's resources. There is not one codebook anywhere under `cozmo_resources`, so those sounds cannot be decoded from
what the robot itself came with, which is most of Cozmo's voice.

With a copy of the application, they can. `tools/pycozmo_convert_audio.py` reads the codebooks out of it, rebuilds each
file into a real Ogg Vorbis stream, and leaves a WAV per sound under `~/.pycozmo/converted_sound/`, which PyCozmo then
plays in place of the files it cannot read:

```
pycozmo_convert_audio.py com.anki.cozmo.apk
```

All 1987 of them convert, in under three minutes, for 255 MB - and animation sound goes from 66% to **98%**. Nothing
from the application is copied: the codebooks are read while converting and never stored. The Vorbis decoding itself is
done by `ffmpeg`, which is why this happens once, in a tool, rather than in the library - PyCozmo gains no dependency
from it.


Songs
-----

Cozmo sings 46 tunes, 39 of them in its singing behaviors, from Pop Goes the Weasel to the Toccata - and none of them is
a recording. Each is a MIDI track in Cozmo's sound bank, which the application's sound engine played with an instrument
of Cozmo's sung notes: a recording of each key from C2 to C3, three takes of each, plus a softer one for the end of a
note and short syllables for its start. The behavior picks the song as a WWise switch, and the singing animation's
event plays whichever the switch picks. `pycozmo.songs` does all of that from the bank: it reads the music objects that
lead from the event to the track, the track's notes, the instrument's key ranges, volumes and tunings, and sings the
notes into sound for the speaker, cut short where the animation stops the song.

```python
cli.set_audio_switch("Cozmo_Sings_100Bpm", "Cozmo_Sings_Pop_Goes_The_Weasel")
cli.play_anim_group("Singing_100bpm")
```

Two things WWise worked out at run time are PyCozmo's reading of the bank, not known: a held note, which is a vowel
sung for six seconds, is taken to fall silent as its key is released, as the tune would otherwise turn into a cluster
of notes; and a vibrato the instrument has is left out, since read as the bank has it, it would take each note up most
of a fourth. The notes are WWise Vorbis, so Cozmo sings once they are converted.
