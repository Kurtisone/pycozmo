
PyCozmo
=======

`PyCozmo` is a pure-Python communication library, alternative SDK, and application for the
[Cozmo robot](https://anki.bot/pages/cozmo) . It allows controlling a Cozmo robot directly, without
having to go through a mobile device, running the Cozmo app.

The library is loosely based on the [Anki Cozmo Python SDK](https://github.com/anki/cozmo-python-sdk) and the
[cozmoclad](https://pypi.org/project/cozmoclad/) ("C-Like Abstract Data") library.

This project is a tool for exploring the hardware and software of the Digital Dream Labs (originally Anki) Cozmo robot.
It is unstable and heavily under development.

It is for people who program robots, or who want to understand one: developers who would rather write Python on their
own computer than in a phone app, hobbyists, teachers and students, and anyone studying how Cozmo works - its protocol,
its firmware, and the way the Cozmo app drove it. It is a library and a set of tools, not an app: you work in Python or
on the command line, and you connect the computer to the robot's Wi-Fi network yourself. Cozmo's block-based
programming, Code Lab, is not reproduced.


About This Fork
---------------

This is an actively maintained fork of [zayfod/pycozmo](https://github.com/zayfod/pycozmo). Upstream development
stopped in November 2020: its last release on PyPI, v0.8.0, fails to import on current Python, and the issues and pull
requests opened since 2021 went unanswered.

The Cozmo protocol is not a moving target - Anki shut down in 2019 and the robot firmware is frozen - so this fork is
not chasing upstream changes. It pays off the maintenance debt first: support for current Python versions, dependencies
pinned to versions known to work, and continuous integration running the linter, the type checker and the unit tests on
Python 3.11 through 3.14. It then goes on to reproduce what the Cozmo app did off-board: Cozmo's own behavior, its
sounds and songs, its cubes and games, and its vision.

The protocol layer is deliberately left alone. `protocol_declaration.py`, `protocol_encoder.py` and the generator
that produces them are the parts hardest to verify without a robot on the desk, so they are touched only for a
demonstrated bug.

Changes made here are listed separately from the upstream history in [CHANGES.md](CHANGES.md), and are kept in a
shape that could be offered upstream if that project ever becomes active again.


Installation
------------

Python 3.11 or newer. pip installs what the library needs - NumPy, Pillow, FlatBuffers and dpkt; the exact versions
this revision is tested against are in [requirements.txt](requirements.txt).

This fork is not published on PyPI: installing `pycozmo` from PyPI gives upstream v0.8.0, which fails to import on
Python 3.13 and newer. Install it from source:

```
git clone https://github.com/Kurtisone/pycozmo.git
cd pycozmo
pip install --user .

pycozmo_resources.py download
```

`pycozmo_resources.py download` fetches Anki's resources - the animations, sound banks and configuration - which the
animations, the sounds and the brain need. Controlling the robot does not.

Finding faces needs OpenCV, which is an extra rather than a dependency: install `".[faces]"` instead of `.` - or add
OpenCV to an install you have, with `pip install opencv-python-headless` - and fetch the face models with
`pycozmo_faces.py download`.

For development, install it editable, with the tools the checks use:

```
pip install --user -e ".[faces]"
pip install --user -r requirements-dev.txt
```


Connecting to Cozmo over Wi-Fi
------------------------------

A Wi-Fi connection needs to be established with Cozmo before using PyCozmo applications.

1. Wake up Cozmo by placing it on the charging platform
2. Make Cozmo display its Wi-Fi PSK by raising and lowering its lift
3. Scan for Cozmo's Wi-Fi SSID (depends on the OS)
4. Connect using Cozmo's Wi-Fi PSK (depends on the OS)

[This video](https://www.youtube.com/watch?v=-k_oiQhBa5o) summarizes the connection process.


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

The [examples](#examples) show the rest.


Cozmo's Own Behavior
--------------------

Cozmo's personality engine runs off-board, in the Cozmo app, not on the robot. PyCozmo replaces that app, so
reproducing the robot's own behavior means reading Anki's resource files and driving the robot from them. With the
resources downloaded, run the application:

```
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
Cozmo app. As the needs fall it starts asking for something, and it sings, plays with its cubes, and notices motion and
faces. In a program of your own, the engine is one class:

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


What PyCozmo Does
-----------------

On the robot, everything the hardware has is supported: the camera, the cliff sensor, the accelerometers and gyro, the
battery and the cubes' sensors, the backpack button (v1.5 hardware and newer); the wheels, head and lift motors, the
OLED display, the speaker, the backpack, IR, cube and platform LEDs; and the on-board functions - the Wi-Fi AP,
Bluetooth LE, localization, path tracking, NV RAM storage and over-the-air firmware updates. See
[docs/functions.md](docs/functions.md).

What the Cozmo app did off-board is reproduced as far as the resources and recordings of Anki's engine allow:

| Area | What works | Details |
|---|---|---|
| Animations and sound | Anki's animations from FlatBuffers `.bin` files and procedural faces; the sound of 98% of them once converted; Cozmo's 39 songs | [sound.md](docs/sound.md) |
| Personality | The mood engine, the three nurture needs, the 21 reaction triggers (12 raised), and the activity engine that keeps a robot busy; 35 of the 76 behavior classes | [own_behavior.md](docs/own_behavior.md) |
| Cubes and games | Connecting the cubes, their lights, and where they are; picking up, putting down, stacking, rolling, popping a wheelie; Quick Tap, Memory Match and Keep Away | [cubes_and_games.md](docs/cubes_and_games.md) |
| Vision | Motion, in the image and on the ground; the cubes' markers; faces, with OpenCV, found, followed and known by name | [vision.md](docs/vision.md) |

Not done: facial expressions, gaze and pets; a map of the surroundings; text-to-speech, since the resources' voices are
Acapela's and their engine is not among them; the SDK's custom objects; Vector's animations and behaviors; voice
commands. [docs/offboard_functions.md](docs/offboard_functions.md) is the full account, function by function, and
compares it with the Cozmo SDK and the Cozmo app.

Much of the cube handling, the games, the songs and the faces is checked against an emulator of the robot, which is not
part of this repository, and against recordings of Anki's own engine through the SDK, and not yet tried on a robot as
written; the details say which.


Tools
-----

- [pycozmo_app.py](tools/pycozmo_app.py) - an alternative Cozmo application, implementing off-board functions.
- [pycozmo_resources.py](tools/pycozmo_resources.py) - downloads, shows the status of, or removes Anki's resources.
- [pycozmo_convert_audio.py](tools/pycozmo_convert_audio.py) - converts Cozmo's Vorbis sounds, which need codebooks from
    the Cozmo application: see [sound.md](docs/sound.md).
- [pycozmo_faces.py](tools/pycozmo_faces.py) - fetches the face detection models, and lists, renames and forgets the
    people Cozmo knows.
- [pycozmo_dump.py](tools/pycozmo_dump.py) - a command-line application that can read and annotate Cozmo communication
    from [pcap files](https://en.wikipedia.org/wiki/Pcap) or capture it live using
    [pypcap](https://github.com/pynetwork/pypcap).
- [pycozmo_replay.py](tools/pycozmo_replay.py) - a basic command-line application that can replay .pcap files back to
    Cozmo.
- [pycozmo_anim.py](tools/pycozmo_anim.py) - a tool for examining and manipulating animation files.
- [pycozmo_update.py](tools/pycozmo_update.py) - a tool for over-the-air (OTA) updates of Cozmo's firmware.
- [pycozmo_protocol_generator.py](tools/pycozmo_protocol_generator.py) - a tool for generating Cozmo protocol encoder
    code.

**Note**: PyCozmo and `pycozmo_protocol_generator.py` in particular could be used as a base for creating a Cozmo
protocol encoder code generator for languages other than Python (C/C++, Java, etc.).


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
- [imu.py](examples/imu.py) - prints the robot's orientation, from its accelerometers and gyro

Display, sound and animation:
- [display_lines.py](examples/display_lines.py) - demonstrates 2D graphics, using
    [PIL.ImageDraw](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html) on Cozmo's display
    ([video](https://youtu.be/ZT81PlmItrU))
- [video.py](examples/video.py) - demonstrates visualizing video captured from the camera back on display
- [procedural_face.py](examples/procedural_face.py) - demonstrates drawing a procedural face on Cozmo's display
- [procedural_face_show.py](examples/procedural_face_show.py) - demonstrates generating a procedural face
- [procedural_face_expressions.py](examples/procedural_face_expressions.py) - demonstrates displaying various facial
    expressions ([video](https://youtu.be/UyMTEOG2FyU))
- [audio.py](examples/audio.py) - demonstrates 22 kHz, 16-bit, mono WAVE file playback through Cozmo's speaker
- [anim.py](examples/anim.py) - demonstrates animating Cozmo
- [sing.py](examples/sing.py) - has Cozmo sing one of its 39 songs

Cubes, games and faces:
- [cube_lights.py](examples/cube_lights.py) - demonstrates cube connection and LED control
- [cube_light_animation.py](examples/cube_light_animation.py) - demonstrates cube LED animation control
- [quick_tap.py](examples/quick_tap.py) - plays Quick Tap, the cube game of Anki's app, with Cozmo
- [memory_match.py](examples/memory_match.py) - plays Memory Match, another cube game of Anki's app, with Cozmo
- [keep_away.py](examples/keep_away.py) - plays Keep Away, the third cube game of Anki's app, with Cozmo
- [faces.py](examples/faces.py) - shows the faces Cozmo sees and who they are, and enrolls a name given

Other:
- [rc.py](examples/rc.py) - turns Cozmo into an RC tank that can be driven with an XBox 360 Wireless controller or
    Logitech Gamepad F310
- [charger_lights.py](examples/charger_lights.py) - demonstrates Cozmo charging platform LED control
- [nvram.py](examples/nvram.py) - demonstrates reading data from Cozmo's NVRAM (non-volatile memory)
- [client.py](examples/client.py) and [server.py](examples/server.py) - a protocol-level client and a server that
    stands in for a robot, on the loopback address


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


Documentation
-------------

In [docs/](docs/):

- [offboard_functions.md](docs/offboard_functions.md) - what the Cozmo app did off-board, and what PyCozmo does of it,
    compared with the Cozmo SDK and the app; with [own_behavior.md](docs/own_behavior.md), [sound.md](docs/sound.md),
    [vision.md](docs/vision.md) and [cubes_and_games.md](docs/cubes_and_games.md), which give the details
- [functions.md](docs/functions.md) - what the robot does by itself
- [architecture.md](docs/architecture.md), [protocol.md](docs/protocol.md) and [capturing.md](docs/capturing.md) - how
    the library is built, the protocol it speaks, and how to capture it
- [versions.md](docs/versions.md), [hardware_versions.md](docs/hardware_versions.md) and
    [esp8266.md](docs/esp8266.md) - the robot's firmware and hardware

[pycozmo.readthedocs.io](https://pycozmo.readthedocs.io/) documents upstream v0.8.0. It predates this fork and does not
describe its changes. To build the documentation for this tree:

```
pip install --user -r requirements-dev.txt
sphinx-build -b html docs/source docs/source/build
```


Checks
------

None of the checks below need a robot or a network connection. They are what the CI workflow runs, and CI treats any of
them failing as a build failure:

```
flake8 .
mypy .
pytest pycozmo/
```

The tests share nothing, so `pytest -n 4 pycozmo/` runs them on four processes, with `pytest-xdist` from
`requirements-dev.txt`: 75 seconds on a Steam Deck, where `pytest pycozmo/` takes some three minutes.

Test coverage, which CI does not gate on:

```
coverage run --source=pycozmo -m pytest pycozmo/
coverage report --skip-covered --sort=cover
```

[CONTRIBUTING.md](CONTRIBUTING.md) says how to send changes.


Support
-------

Bug reports and changes for this fork, and ideas for other functionality:

[https://github.com/Kurtisone/pycozmo](https://github.com/Kurtisone/pycozmo)

Development happens on a private Forgejo instance, which mirrors to GitHub. The mirror is one way: it overwrites
the branches and tags of the GitHub repository on every synchronization. Issues and pull requests opened on GitHub
are not touched by that and are read, but a pull request cannot be merged on GitHub, because the next
synchronization would undo it. Changes are applied on the Forgejo side and reach GitHub with the following sync.

The upstream project, for reference:

[https://github.com/zayfod/pycozmo](https://github.com/zayfod/pycozmo)


PyCozmo In The Wild
-------------------

- [Expressive Eyes](https://git.brl.ac.uk/ca2-chambers/expressive-eyes) - rendering various facial expressions using
    PyCozmo's procedural_face module
- a [ROS2 driver](https://github.com/solosito/cozmo_ros2_ws/)
- another [ROS2 driver](https://github.com/brean/cozmo_ros)


Disclaimer
----------

This project is not affiliated with [Digital Dream Labs](https://anki.bot/) or Anki.
