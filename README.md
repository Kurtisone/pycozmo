
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


Documentation
-------------

[https://pycozmo.readthedocs.io/](https://pycozmo.readthedocs.io/)


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
- [ ] Personality engine - work in progress
- [ ] Cozmo behaviors - work in progress
- [ ] Motion detection
- [ ] Object (cube and platform) detection
- [ ] Cube marker recognition
- [ ] Face detection
- [ ] Face recognition
- [ ] Facial expression estimation
- [ ] Pet detection
- [ ] Camera calibration
- [ ] Navigation map building
- [ ] Text-to-speech
- [ ] Songs

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
git clone http://192.168.1.20:3002/kurtisone/pycozmo.git
cd pycozmo
pip install --user .

pycozmo_resources.py download
```

From source, for development:

```
git clone http://192.168.1.20:3002/kurtisone/pycozmo.git
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

`flake8` and `pytest` are expected to pass. `mypy` currently reports a backlog of pre-existing errors in project
code and is advisory.

 
Support
-------

Bug reports and changes for this fork:

[http://192.168.1.20:3002/kurtisone/pycozmo](http://192.168.1.20:3002/kurtisone/pycozmo)

The upstream project, for reference:

[https://github.com/zayfod/pycozmo](https://github.com/zayfod/pycozmo)

DDL Robot Discord server, channel #development-cozmo:

[https://discord.gg/ew92haS](https://discord.gg/ew92haS)


Disclaimer
----------

This project is not affiliated with [Digital Dream Labs](https://www.digitaldreamlabs.com/) or
[Anki](https://anki.com/).
