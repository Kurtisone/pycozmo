
PyCozmo
=======

`PyCozmo` is a pure-Python communication library, alternative SDK, and application for the
[Cozmo robot](https://www.digitaldreamlabs.com/pages/cozmo) . It allows controlling a Cozmo robot directly, without
having to go through a mobile device, running the Cozmo app.

The library is loosely based on the [Anki Cozmo Python SDK](https://github.com/anki/cozmo-python-sdk) and the
[cozmoclad](https://pypi.org/project/cozmoclad/) ("C-Like Abstract Data") library.

This project is a tool for exploring the hardware and software of the Digital Dream Labs (originally Anki) Cozmo robot.
It is unstable and heavily under development.


About This Fork
---------------

This is an actively maintained fork of [zayfod/pycozmo](https://github.com/zayfod/pycozmo).

Upstream development stopped in November 2020. The last release on PyPI is v0.8.0 and the issue tracker has
collected reports and pull requests that have gone unanswered since 2021, among them the one that makes the package
fail to import at all on current Python.

The Cozmo protocol is not a moving target: Anki shut down in 2019 and the robot firmware is frozen. This fork is
therefore not chasing upstream changes, it is paying off the maintenance debt that accumulated after the project was
abandoned:

- Support for current Python versions. `import pycozmo` failed outright on Python 3.13, for every user, because the
  standard library module it depended on was removed.
- Dependencies pinned to versions that are known to work, rather than open ranges.
- Continuous integration running the linter, the type checker and the unit tests on Python 3.11 through 3.14.

The protocol layer is deliberately left alone. `protocol_declaration.py`, `protocol_encoder.py` and the generator
that produces them are the parts hardest to verify without a robot on the desk, so they are touched only for a
demonstrated bug.

Changes made here are listed separately from the upstream history in [CHANGES.md](CHANGES.md), and are kept in a
shape that could be offered upstream if that project ever becomes active again.


Usage
-----

Basic:

```python
import time
import pycozmo

with pycozmo.connect() as cli:
    cli.set_head_angle(angle=0.6)
    time.sleep(1)
```

Advanced:

```python
import pycozmo

cli = pycozmo.Client()
cli.start()
cli.connect()
cli.wait_for_robot()

cli.drive_wheels(lwheel_speed=50.0, rwheel_speed=50.0, duration=2.0)

cli.disconnect()
cli.stop()
```


Cozmo's Own Behavior
--------------------

Cozmo's personality engine runs off-board, in the Cozmo app, not on the robot. PyCozmo replaces that app, so
reproducing the robot's own behavior means reading Anki's resource files and driving the robot from them. Download the
resources once, then run the application:

```
pycozmo_resources.py download
pycozmo_app.py
```

Left alone, the robot keeps itself busy: it gets off its charger, looks around, and settles into the idle and bored
animations Anki wrote for a Cozmo with nothing to do.

```
pycozmo.behavior     INFO     Starting activity Hiking
pycozmo.behavior     INFO     Activating Hiking_FirstLookIntro
pycozmo.animation    INFO     Playing animation group HikingIntro
pycozmo.animation    INFO     Playing animation anim_hiking_getin_01
pycozmo.behavior     INFO     Ending activity Hiking
pycozmo.behavior     INFO     Starting activity NothingToDo
pycozmo.behavior     INFO     Activating NothingToDo_BoredAnim
```

Pick it up, set it down on an edge, put it on the charger or turn it on its back, and it reacts the way it did with the
Cozmo app, playing the animation Anki authored for that reaction, then goes back to what it was doing:

```
pycozmo.reaction     INFO     Processing CliffDetected
pycozmo.emotion      INFO     CliffDetected: Brave -0.12, Calm -0.12, Happy -0.12
pycozmo.behavior     INFO     Activating ReactToCliff
pycozmo.animation    INFO     Playing animation group ReactToCliff
pycozmo.animation    INFO     Playing animation anim_reacttocliff_edgeliftup_01
```

In a program of your own, the engine is one class:

```python
import time
import pycozmo

with pycozmo.connect() as cli:
    brain = pycozmo.brain.Brain(cli)
    brain.start()
    time.sleep(120.0)
    brain.stop()
```

The `reaction`, `behavior`, `animation` and `emotion` loggers sit at the *robot* log level, which defaults to
`WARNING`, so that snippet reacts silently. `pycozmo_app.py` raises it to `INFO` itself; elsewhere, pass
`robot_log_level="INFO"` to `connect()` or set `PYCOZMO_ROBOT_LOG_LEVEL=INFO` in the environment.

### What is reproduced

`reactionTrigger_behavior_map.json` maps 21 reaction triggers to a behavior. 18 of them play the animation group Anki
gave them, resolved through `AnimationTriggerMap.json`. The remaining three - `MotorCalibration`, `RobotPlacedOnSlope`
and `ReturnedToTreads` - have no animation anywhere in the resources, under their behavior ID, their trigger name or
any name close to either, so they log a warning and end.

Of those 21 triggers, eleven are raised today: `CliffDetected`, `RobotPickedUp`, `RobotFalling`, `PlacedOnCharger`,
`Hiccup`, the four the robot's attitude produces, `RobotOnBack`, `RobotOnFace`, `RobotOnSide` and
`ReturnedToTreads`, and for the cubes `ObjectPositionUpdated` and `CubeMoved` - see below. The rest wait on parts
that are not implemented: the other vision triggers need face and pet detection, and the others come from game and
engine states the activity engine does not reach yet. None of them needs
motion detection - `UnexpectedMovement`, despite its name, is not something the camera sees.

Two details matter for the result to look right rather than merely work:

- Animations are chosen for the current head angle. 43 of the animation groups hold one animation per head angle band,
  with the angle baked into the animation, so playing the wrong one makes the head jump. Per-animation cooldowns are
  honoured too, which is what keeps a reaction from repeating itself.
- A reaction marked `shouldResumeLast` puts back what it interrupted. A cliff, a shove or a motor calibration
  interrupts what the robot was doing rather than ending it.

The mood engine works: emotion events shift the seven emotions and each decays on its own schedule. It gates the one
activity whose configuration asks it to - see below - and does not yet weigh anything else.

### What the robot needs

Alongside the mood sit three nurture needs - `Repair`, `Energy` and `Play`. A need is not an emotion: an emotion is a
shove that decays back to nothing within a couple of minutes, while a need starts full, falls for hours, and only
something done to the robot puts it back. They are what turns a robot left to itself from merely idle into one that
starts asking for something.

They fall at the rates Anki set, which depend on how low they already are, and each holds at full for twenty minutes
first. Left alone from a fresh start, a robot reaches each bracket at:

| Need | Normal | Warning | Critical |
|---|---|---|---|
| `Play` | 20 min | 55 min | 1 h 23 |
| `Energy` | 22 min | 1 h 39 | 3 h 24 |
| `Repair` | 34 min | 9 h 51 | 19 h 11 |

One need also drags on another: `Repair` between 0.03 and 0.3 makes `Play` fall twice as fast, so a broken robot gets
bored quicker. It is the only cross-effect in the whole configuration - every other multiplier in the file is one.

**What you see.** `NothingToDo`, `PlayAlone`, `Hiking`, `Socialize`, `BuildPyramid` and `PlayWithHumans` all list the
five needs requests as interludes, so from about 55 minutes in the robot starts slipping them between whatever else it
is doing, the more often the lower the need:

```
pycozmo.behavior     INFO     Activating Needs_MildLowPlayRequest
pycozmo.animation    INFO     Playing animation group NeedsMildLowPlayRequest
```

**Once a need is critical**, an activity of its own takes the robot and keeps it. The announcement plays once, and then
the robot wanders and asks for help, over and over, until the need is met:

```
pycozmo.behavior     INFO     Starting activity NeedsSevereLowEnergy
pycozmo.behavior     INFO     Activating Needs_SevereLowEnergyGetIn
pycozmo.animation    INFO     Playing animation group NeedsSevereLowEnergyGetIn
pycozmo.behavior     INFO     Activating Needs_SevereLowEnergyState
pycozmo.animation    INFO     Playing animation group NeedsSevereLowEnergyRequest
pycozmo.behavior     INFO     Activating Needs_SevereLowEnergyState
```

One round of that is a turn, a drive of 1.5 to 6.5 seconds, and the request animation - and then the round ends, which
is what lets a fed robot get on with its life. Neither of Anki's two `DriveInDesperation` behaviors names the need it
belongs to, so watching one is not on offer; ending each round and letting the engine think again does the same job
without risking a robot that begs for ever because nothing was watching. `useCubes` is not read: on a real robot it
sent a hungry Cozmo towards a cube to be fed from.

`Repair` outranks `Energy`, so a robot both broken and starving asks to be mended rather than fed - that is
`needsSevereLowEnergy` standing aside through its `higherPriorityStrategyConfig`.

**Putting a need back** is something no part of this library does on its own, because on a real robot it was a thing
the player did in the app: `Feed` is worth a third of `Energy`, and `RepairHead`, `RepairLift` and `RepairTreads` a
third of `Repair` each. An application built on PyCozmo offers them the same way:

```python
brain.apply_need_action("Feed")
```

The other eighty-odd actions are what the robot and the player do together, most of them worth `Play` alone. Three are
applied from here already: a fall costs 0.15 of `Repair`, being laid on its side 0.03 of `Play`, and a behavior whose
name is also an action - `FistBump`, `PopAWheelie` - is worth that action when it finishes. The rest wait on the games
and the cube work they belong to.

`needs_handlers_config.json`, which describes the face glitching as `Repair` falls, is the one part of the needs not
read yet.

### Sound

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

#### Converting the Vorbis sounds

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

#### Songs

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

### What the robot sees

The brain turns the camera on, in grayscale, and compares each image with the one before it. What moved is announced
as `EvtMotionObserved`, with the fraction of the image that moved and where:

```python
def on_motion(cli, motion):
    print(motion.area, motion.centroid, motion.regions)

cli.add_handler(pycozmo.event.EvtMotionObserved, on_motion)
```

`centroid` is the middle of the motion in image pixels, once at least 0.5% of the image moved. `regions` holds the
three peripheral regions Anki's `vision_config.json` describes - left, right and top - each reporting only once motion
in it has added up, which with Anki's values takes 4% of the region in one image or less of it over several.

Nothing is compared while the robot moves its camera, since the whole scene would move with it: not while it reports
a motor moving, not for 0.3 s after, and not across two images between which its pose or head angle changed. A change
of exposure or of the room's lighting is not motion either.

Nor is anything compared for the first 2.5 s of a stream. A robot's camera often starts out of step with its sensor:
for a second or two the picture scrolls vertically, a little further each image, with its left third garbled, and
every image of it looks like motion across the whole frame. Measured on a robot, it lasted 1.8 s. Nothing in what the
robot sends marks those images, so a stream is taken to start with its first image and after any gap of over 0.5 s.

Motion is also placed on the ground: `ground_centroid` is where it is in mm, ahead of the robot and to its left, and
`ground_area` how much of the visible ground moved. Each pixel is traced back through the lens and out to the table,
from the head's angle and the robot's own tilt. The lens is described by the calibration every robot got in the
factory, which the brain reads from the robot's NV storage when it starts - `Client.read_camera_calibration()` - and
the camera's place in the head by Anki's engine values. Checked on a robot, a cube 100 mm ahead of the treads comes
out at the same place, within a millimetre, from seven head angles between -24 and -1.5 degrees. Where that place is
may be 8% too far, though: 119 mm ahead of the robot's origin, where the cube's own marker puts it at 110 mm, and the
marker agrees with Anki's engine. A camera tilted 1 to 3 degrees further down than pycozmo takes it to be would
account for it; that is being measured.

With the head down, the bottom of the image is the lift seen from above, which would pass for ground 60 mm ahead; so
ground closer than 65 mm is left out, as is ground beyond 400 mm.

The markers on the Light Cubes can be found as well. Each is a symbol in
a dark frame with rounded corners; `pycozmo.marker_detection` finds the frames, places them in the robot's frame, and
tells which cube each belongs to:

```python
from pycozmo import marker_detection

for marker in marker_detection.observe_markers(image, calibration, cli.head_angle.radians, cli.pose_pitch.radians):
    print(marker.cube, marker.position, marker.facing, marker.distance)
```

`cube` is `ObjectType.Block_LIGHTCUBE1` to `3` - the Paperclip, the Anglepoise Lamp and the Deli Slicer - or None,
`position` the middle of the marker in mm, ahead of the robot, to its left and up from the ground, `facing` the way it
faces, `turns` the quarter turns its symbol is turned by on the screen, and `corners` where it is in the image. The
symbol is told by comparing it with Anki's drawings of the three, in `pycozmo/cube_markers`. The frame's sides are fitted to a fraction of a pixel, and the lens' distortion taken out, before the frame is
placed from the camera's calibration and its size, 25 mm. Checked on a robot, a cube filmed from four head angles
was found in 16 images out of 16, where it stood with a standard deviation of 0.27 mm, and told for the Deli Slicer it
was in all of them; nothing else in the room was taken for a marker. Against Anki's own engine, through its SDK, which
named a Paperclip or an Anglepoise Lamp in 656 frames over four sessions, pycozmo named 655 the same and left one
unnamed, and placed them within 2% of where Anki did, and 0.3 mm to the side. A cube carries its symbol either way
round, mirrored on some sides, which is how Anki told them apart: both ways are compared. Close to, the cube's black
corners touch the frame, and the dark pixels no longer outline it: the hole inside them does, and is taken for a frame
if it holds a cube's symbol - 130 more of the cubes Anki saw, over 4092 images of two robots. Finding them takes about
20 ms an image. Which way a marker faces is less sure, a few degrees at best.

The brain does as the Cozmo application did with the cubes. It connects one of each kind as soon as the robot hears
it, and lights it with Anki's own cube light animations: a dim cyan breath every five seconds once connected, a steady
cyan while the robot sees it. It looks for markers five times a second while the robot keeps still, and places each
cube it sees in the robot's world frame, in `cli.cubes`. A cube seen for the first time, or where it was moved to, is
acknowledged (`ObjectPositionUpdated`), and one moved while the robot sees it is reacted to (`CubeMoved`) - unless
the robot moved it itself, lifting it, docking with it or tapping it in a game. Taps and moves come as `EvtCubeTapped`
and `EvtCubeMovingChange`, sightings as `EvtCubeObserved`. A light frame lasts 33.3 ms, measured on a robot.

`pycozmo.cube_handling` handles the cubes as Anki's engine was seen doing it, recorded through the official SDK on a
robot: it goes to stand some 15 cm from a cube and has a look, docks with its head down, looking at the marker again on
the way, and makes the manoeuvre's own moves. It picks the cube up, the lift rising as the robot creeps on; puts it
down; sets it on another, letting go at 76 mm; rolls it, the fork hooking the top edge at 74 mm and coming down as the
robot backs off, which tips the cube over towards it; and pops a wheelie, the lift slamming down on the cube as the
robot drives on at 150 mm/s. The brain's behaviors build on it. `PlayAlone` picks a cube up and works out with it
(`CubeLiftWorkout`, as many lifts as the robot is confident, in a workout its energy chooses), stacks one on another
(`StackBlocks`), rolls one lying on its side back upright (`RollBlock`) and pops wheelies (`PopAWheelie`). A cube says
which side is up, not which way its top points; when a roll shows it points aside, `RollBlock` goes round the cube to
its bottom. All this is checked in the emulator, not yet on a robot as written. Knocking a stack over is not done:
recorded on a robot, Anki's own behavior gave it up.

`pycozmo.quick_tap` plays Quick Tap, the cube game of the Cozmo application: both cubes light up, and on the same
colour the first to tap their cube wins the point, on different colours whoever taps loses it. The robot sits at its
cube, the lift raised over it, and taps it with Anki's animations. The game's rules were the application's code; these
are PyCozmo's reading of them. `pycozmo.memory_match` plays Memory Match: the three cubes light up one after
another, the player repeats the pattern by tapping them, and so does Cozmo, turning to point at each with Anki's
animations; the pattern grows by one each round, and whoever gets it wrong first loses. `pycozmo.keep_away` plays
Keep Away: Cozmo raises its lift and pounces on the player's cube, or pretends to; pulled away in time, the point is
the player's, caught or flinched, Cozmo's. `PlayWithHumans` has the robot ask for a game now and then: the player
takes it up by tapping a cube - for Quick Tap and Keep Away, the cube becomes theirs - and turning it down, letting
the request time out, makes the robot wait longer before asking again.

That is what `PounceOnMotion` plays with. Once motion has been seen on the ground, Socialize and Hiking give it the
robot: it puts its head down to watch, turns towards what moves, creeps up on it, and pounces with the lift. A lift
that stays up after a pounce is taken to have come down on something - a finger, say - and gets `PounceSuccess`; one
that reached the bottom missed, and gets `PounceFail`. On a real robot that does not work yet: its lift came down all
the way on a finger too. It looks elsewhere after a few seconds without motion and gets bored after
more, both as each of Anki's four configurations says. How it goes about it is PyCozmo's own, built on Anki's pounce
animations, since the configurations only tune it: in particular, motion is pounced on within 120 mm, which the pounce
animations' 45 mm lunge suggests but no file states.

### What the robot does when nothing has happened

When the brain starts, the robot first wakes up, with one of the five `anim_launch_wakeup` animations Anki's
`ConnectWakeUp` trigger names - what the Cozmo application played on connecting - and nothing else is chosen for it
until it has.

Reactions answer events. Between them, the activity engine decides what the robot does of its own accord. `Freeplay`
lists 25 sub-activities in priority order, and the first one that wants to run and has a behavior to offer gets the
robot:

- 14 spark activities, which need the application to send a spark.
- 3 severe-need activities, which need the nurture needs (Energy, Repair, Play). Those are not read, so these never
  run - which is also what the robot does while its needs are full.
- `PutDownDispatch`, when the robot was set down on its treads in the last 5 s.
- `Socialize`, when the robot has not been social lately: its configuration scores the `Social` emotion through a graph
  and asks for 0.5, which the graph gives while `Social` is at or below 0.3. This is the only place in Anki's resources
  where the mood decides an activity.
- `Singing`, which has Cozmo sing one of its 39 songs every 10 to 40 minutes, the sooner the better its Play need is
  met; and `BuildPyramid`, which needs a pyramid of cubes.
- `PlayWithHumans`, when the robot can ask for a game: Keep Away with a cube seen, Quick Tap with two cubes
  connected and one of them seen, Memory Match with the three connected and one seen.
- `PlayAlone`, `Hiking` and `NothingToDo`.

Within an activity, behaviors are drawn in a random order weighted by their score. A behavior that has just run loses
part of its score and wins it back over time: `GuardDog` scores nothing for 5 minutes and is whole again a quarter of
an hour on, and the bored animations keep half their score for 9 seconds, which is what keeps the robot from playing
two bored sequences in a row.

What actually runs today is what needs no face: `DriveOffCharger`, the hiking intro, the `NothingToDo` idle and bored
animations, `PounceOnMotion`, and with cubes the workout, stacking, rolling a cube back upright, popping wheelies,
putting a carried cube down and asking for a game. An activity that wants the robot but can offer nothing is passed
over rather than entered, since entering it would leave the robot still for as long as its duration - 25 s for
`PlayAlone`, a minute for `Hiking`.


Documentation
-------------

[https://pycozmo.readthedocs.io/](https://pycozmo.readthedocs.io/) documents upstream v0.8.0. It predates this fork
and does not describe its changes.

To build the documentation for this tree:

```
pip install --user -r requirements-dev.txt
sphinx-build -b html docs/source docs/source/build
```


Robot Support
-------------

Sensors:
- [x] Camera
- [x] Cliff sensor
- [x] Accelerometers
- [x] Gyro
- [x] Battery voltage
- [x] Cube battery voltage
- [x] Cube accelerometers
- [x] Backpack button (v1.5 hardware and newer)

Actuators:
- [x] Wheel motors
- [x] Head motor
- [x] Lift motor
- [x] OLED display
- [x] Speaker
- [x] Backpack LEDs
- [x] IR LED
- [x] Cube LEDs
- [x] Platform LEDs (when available)

On-board functions (see [docs/functions.md](docs/functions.md) for details:
- [x] Wi-Fi AP
- [x] Bluetooth LE
- [x] Localization
- [x] Path tracking
- [x] NV RAM storage
- [x] Over-the-air (OTA) firmware updates

Off-board functions (see [docs/offboard_functions.md](docs/offboard_functions.md) for details:
- [x] Procedural face generation
- [x] Cozmo animations from FlatBuffers .bin files
- [x] Cube connection and lights - the brain connects one cube of each kind and lights it with Anki's cube light
    animations, see [What the robot sees](#what-the-robot-sees)
- [ ] Personality engine - the mood engine works and gates the one activity whose configuration asks it to, and the
    three nurture needs fall and drive the requests and activities that read them, see
    [What the robot needs](#what-the-robot-needs)
- [ ] Cozmo behaviors - reactions play Cozmo's own animations and the activity engine keeps the robot busy between
    them, see [Cozmo's Own Behavior](#cozmos-own-behavior); 35 of the 76 behavior classes are implemented, and what
    the others need is in [docs/offboard_functions.md](docs/offboard_functions.md)
- [ ] Cube handling - going to a cube, docking with it, picking it up, putting it down, stacking it, rolling it and
    popping a wheelie against it are done as Anki's engine was recorded doing them, not yet tried on a robot as
    written; knocking a stack over is not
- [x] Games - Quick Tap, Memory Match and Keep Away are played, and asked for in freeplay; their moves are measured on
    a robot, the games not yet played through on one
- [x] Motion detection - in the image, with Anki's peripheral regions, and on the ground, see
    [What the robot sees](#what-the-robot-sees)
- [ ] Object (cube and platform) detection - the cubes are placed by their markers, see
    [What the robot sees](#what-the-robot-sees); the platform is not
- [x] Cube marker recognition - the markers are found, placed in space and told apart, and the brain looks for them,
    see [What the robot sees](#what-the-robot-sees)
- [ ] Face detection
- [ ] Face recognition
- [ ] Facial expression estimation
- [ ] Pet detection
- [ ] Camera calibration - the robot's factory calibration is read and used; calibrating anew is not done
- [ ] Navigation map building
- [ ] Text-to-speech - the resources' voices are Acapela's, whose engine is not among them
- [x] Songs - Cozmo sings its 39 songs, rendered from the MIDI tracks of its sound bank with its sung notes, see
    [Songs](#songs)
- [x] Animation audio - two thirds of what the animations trigger plays from the robot's own
    resources, and 98% once the WWise Vorbis files have been converted with the codebooks from the
    Cozmo application, see [Sound](#sound)

Extra off-board functions:
- [ ] Vector animations from FlatBuffers .bin files
- [ ] Vector behaviors
- [ ] ArUco marker recognition
- [ ] Cozmo and Vector robot detection
- [ ] Drivable area estimation
- [ ] Voice commands

If you have ideas for other functionality [share them via GitHub](https://github.com/zayfod/pycozmo/issues).


Tools
-----

- [pycozmo_app.py](tools/pycozmo_app.py) - an alternative Cozmo application, implementing off-board functions
    ([video](https://youtu.be/gMEc6RzIm-E)).
- [pycozmo_dump.py](tools/pycozmo_dump.py) - a command-line application that can read and annotate Cozmo communication
    from [pcap files](https://en.wikipedia.org/wiki/Pcap) or capture it live using
    [pypcap](https://github.com/pynetwork/pypcap).
- [pycozmo_replay.py](tools/pycozmo_replay.py) - a basic command-line application that can replay .pcap files back to
    Cozmo.
- [pycozmo_anim.py](tools/pycozmo_anim.py) - a tool for examining and manipulating animation files.
- [pycozmo_update.py](tools/pycozmo_update.py) - a tool for over-the-air (OTA) updates of Cozmo's firmware.
- [pycozmo_protocol_generator.py](tools/pycozmo_protocol_generator.py) - a tool for generating Cozmo protocol encoder
    code.

**Note**: PyCozmo and `pycozmo_protocol_generator.py` in particular could be used as a base for creating a Cozmo protocol
encoder code generator for languages other than Python (C/C++, Java, etc.).
 

Examples
--------

Basic:
- [minimal.py](examples/minimal.py) - minimal code to communicate with Cozmo, using PyCozmo
- [extremes.py](examples/extremes.py) - demonstrates Cozmo lift and head control
- [backpack_lights.py](examples/backpack_lights.py) - demonstrates Cozmo backpack LED control
- [display_image.py](examples/display_image.py) - demonstrates visualization of image files on Cozmo's display
- [events.py](examples/events.py) - demonstrates event handling
- [camera.py](examples/camera.py) - demonstrates capturing a camera image 
- [go_to_pose.py](examples/go_to_pose.py) - demonstrates moving to a specific pose (position and orientation) 
- [path.py](examples/path.py) - demonstrates following a predefined path

Advanced:
- [display_lines.py](examples/display_lines.py) - demonstrates 2D graphics, using
    [PIL.ImageDraw](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html) on Cozmo's display
    ([video](https://youtu.be/ZT81PlmItrU))
- [rc.py](examples/rc.py) - turns Cozmo into an RC tank that can be driven with an XBox 360 Wireless controller or 
    Logitech Gamepad F310
- [video.py](examples/video.py) - demonstrates visualizing video captured from the camera back on display
- [cube_lights.py](examples/cube_lights.py) - demonstrates cube connection and LED control
- [cube_light_animation.py](examples/cube_light_animation.py) - demonstrates cube LED animation control
- [quick_tap.py](examples/quick_tap.py) - plays Quick Tap, the cube game of Anki's app, with Cozmo
- [memory_match.py](examples/memory_match.py) - plays Memory Match, another cube game of Anki's app, with Cozmo
- [keep_away.py](examples/keep_away.py) - plays Keep Away, the third cube game of Anki's app, with Cozmo
- [sing.py](examples/sing.py) - has Cozmo sing one of its 39 songs
- [charger_lights.py](examples/charger_lights.py) - demonstrates Cozmo charging platform LED control
- [audio.py](examples/audio.py) - demonstrates 22 kHz, 16-bit, mono WAVE file playback through Cozmo's speaker 
- [nvram.py](examples/nvram.py) - demonstrates reading data from Cozmo's NVRAM (non-volatile memory)
- [procedural_face.py](examples/procedural_face.py) - demonstrates drawing a procedural face on Cozmo's display
- [procedural_face_show.py](examples/procedural_face_show.py) - demonstrates generating a procedural face
- [procedural_face_expressions.py](examples/procedural_face_expressions.py) - demonstrates displaying various facial
    expressions ([video](https://youtu.be/UyMTEOG2FyU))
- [anim.py](examples/anim.py) - demonstrates animating Cozmo


PyCozmo In The Wild
-------------------

- [Expressive Eyes](https://git.brl.ac.uk/ca2-chambers/expressive-eyes) - rendering various facial expressions using
    PyCozmo's procedural_face module
- an [application](https://github.com/paulbaumgarten/paulbaumgarten/blob/6d9025ec715e0b5f133ce9b7e39e74a5389c5334/myp/cozmo/assets/02-camera.py)
    that recognizes [ArUco markers](https://docs.opencv.org/master/d5/dae/tutorial_aruco_detection.html), using OpenCV
- a [ROS2 driver](https://github.com/solosito/cozmo_ros2_ws/)
- another [ROS2 driver](https://github.com/brean/cozmo_ros)


Connecting to Cozmo over Wi-Fi
------------------------------

A Wi-Fi connection needs to be established with Cozmo before using PyCozmo applications.

1. Wake up Cozmo by placing it on the charging platform
2. Make Cozmo display it's Wi-Fi PSK by rising and lowering its lift
3. Scan for Cozmo's Wi-Fi SSID (depends on the OS)
4. Connect using Cozmo's Wi-Fi PSK (depends on the OS)

[This video](https://www.youtube.com/watch?v=-k_oiQhBa5o) summarizes the connection process.


PyCozmo vs. the Cozmo SDK
-------------------------

A Cozmo SDK application (aka "game") acts as a client to the Cozmo app (aka "engine") that runs on a mobile device.
The low-level communication happens over USB and is handled by the `cozmoclad` library.

In contrast, an application using PyCozmo basically replaces the Cozmo app and acts as the "engine". PyCozmo handles
the low-level UDP communication with Cozmo.
   
```
+------------------+                   +------------------+                   +------------------+
|     SDK app      |     Cozmo SDK     |    Cozmo app     |       Cozmo       |      Cozmo       |
|      "game"      |     cozmoclad     |     "engine"     |      protocol     |     "robot"      |
|                  | ----------------> |   Wi-Fi client   | ----------------> |     Wi-Fi AP     |
|                  |        USB        |    UDP client    |     UDP/Wi-Fi     |    UDP Server    |
+------------------+                   +------------------+                   +------------------+
```


Requirements
------------

- [Python](https://www.python.org/downloads/) 3.11 or newer
- [Pillow](https://github.com/python-pillow/Pillow) 6.0.0 - Python image library
- [FlatBuffers](https://github.com/google/flatbuffers) - serialization library
- [dpkt](https://github.com/kbandla/dpkt) - TCP/IP packet parsing library 


Installation
------------

This fork is not published on PyPI. Installing `pycozmo` from PyPI gives upstream v0.8.0, which fails to import on
Python 3.13 and newer.

From source:

```
git clone https://github.com/Kurtisone/pycozmo.git
cd pycozmo
pip install --user .

pycozmo_resources.py download
```

From source, for development:

```
git clone https://github.com/Kurtisone/pycozmo.git
cd pycozmo
pip install --user -e .
pip install --user -r requirements-dev.txt

pycozmo_resources.py download
```

`setup.py install` and `setup.py develop`, which earlier revisions documented, are deprecated by setuptools. The pip
invocations above replace them.


Checks
------

None of the checks below need a robot or a network connection. They are what the CI workflow runs:

```
flake8 .
mypy .
pytest pycozmo/
```

All three are expected to pass, and CI treats any of them failing as a build failure.

Test coverage, which CI does not gate on:

```
coverage run --source=pycozmo -m pytest pycozmo/
coverage report --skip-covered --sort=cover
```

 
Support
-------

Bug reports and changes for this fork:

[https://github.com/Kurtisone/pycozmo](https://github.com/Kurtisone/pycozmo)

Development happens on a private Forgejo instance, which mirrors to GitHub. The mirror is one way: it overwrites
the branches and tags of the GitHub repository on every synchronization. Issues and pull requests opened on GitHub
are not touched by that and are read, but a pull request cannot be merged on GitHub, because the next
synchronization would undo it. Changes are applied on the Forgejo side and reach GitHub with the following sync.

The upstream project, for reference:

[https://github.com/zayfod/pycozmo](https://github.com/zayfod/pycozmo)

DDL Robot Discord server, channel #development-cozmo:

[https://discord.gg/ew92haS](https://discord.gg/ew92haS)


Disclaimer
----------

This project is not affiliated with [Digital Dream Labs](https://www.digitaldreamlabs.com/) or
[Anki](https://anki.com/).
