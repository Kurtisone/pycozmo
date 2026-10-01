What the robot sees
===================

The robot streams its camera, and PyCozmo looks in it, as Anki's engine did, for motion, for the markers on the Light
Cubes and, with OpenCV, for faces. The brain does all three while the robot keeps still; what it does with cubes is in
[cubes_and_games.md](cubes_and_games.md).


Motion
------

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


Cube markers
------------

The markers on the Light Cubes can be found as well. Each is a symbol in a dark frame with rounded corners;
`pycozmo.marker_detection` finds the frames, places them in the robot's frame, and tells which cube each belongs to:

```python
from pycozmo import marker_detection

for marker in marker_detection.observe_markers(image, calibration, cli.head_angle.radians, cli.pose_pitch.radians):
    print(marker.cube, marker.position, marker.facing, marker.distance)
```

`cube` is `ObjectType.Block_LIGHTCUBE1` to `3` - the Paperclip, the Anglepoise Lamp and the Deli Slicer - or None,
`position` the middle of the marker in mm, ahead of the robot, to its left and up from the ground, `facing` the way it
faces, `turns` the quarter turns its symbol is turned by on the screen, and `corners` where it is in the image. The
symbol is told by comparing it with Anki's drawings of the three, in `pycozmo/cube_markers`. The frame's sides are
fitted to a fraction of a pixel, and the lens' distortion taken out, before the frame is placed from the camera's
calibration and its size, 25 mm. Checked on a robot, a cube filmed from four head angles was found in 16 images out of
16, where it stood with a standard deviation of 0.27 mm, and told for the Deli Slicer it was in all of them; nothing
else in the room was taken for a marker. Against Anki's own engine, through its SDK, which named a Paperclip or an
Anglepoise Lamp in 656 frames over four sessions, pycozmo named 655 the same and left one unnamed, and placed them
within 2% of where Anki did, and 0.3 mm to the side. A cube carries its symbol either way round, mirrored on some sides,
which is how Anki told them apart: both ways are compared. Close to, the cube's black corners touch the frame, and the
dark pixels no longer outline it: the hole inside them does, and is taken for a frame if it holds a cube's symbol - 130
more of the cubes Anki saw, over 4092 images of two robots. Finding them takes about 20 ms an image. Which way a marker
faces is less sure, a few degrees at best.


Faces
-----

PyCozmo finds faces with OpenCV's two small face models: YuNet, which gives a face's box and five landmarks, and
SFace, which turns a face into 128 numbers that are close for the same person and far for another. Neither is Anki's,
whose detector was native code that is no resource, so what is found and told apart differs from Cozmo's own. OpenCV is
not a dependency of the library; it is an extra, with the models fetched once:

```
pip install opencv-python-headless        # or, from a source tree: pip install ".[faces]"
pycozmo_faces.py download
```

The brain looks for faces five times a second while the robot keeps still, as it does for markers, and keeps track of
them in `cli.faces`: a face seen where one was a moment ago is that face, one that left and is back is told by its
features, and where each is follows from the distance between its eyes, 63 mm on average, which is good to some 15%.
A face that appears is acknowledged (`FacePositionUpdated`, Anki's `AcknowledgeFace`) unless the robot is at a game or
handling a cube. The events are `EvtFaceAppeared`, `EvtFaceObserved`, `EvtFaceIdentified` and `EvtFaceDisappeared`.

```python
cli.faces.enroll("Eileen")          # looks at the nearest face for a few seconds
cli.faces.by_name("Eileen")         # where she was last seen
```

The people Cozmo knows are kept as features, not pictures, in `~/.pycozmo/faces/gallery.json`: nothing leaves the
machine, and it is no part of a repository. `pycozmo_faces.py` lists, renames and forgets them, and
`examples/faces.py` shows what Cozmo sees live.

On a robot's camera - 320 by 240, in grayscale - a face 40 pixels across, a metre or so away, is found, and one of 28
is not; a blurred small one is still told from another person. Finding one takes 4.6 ms an image on a Steam Deck, and
telling it 14.6 ms, once a second a face. Over 4092 images of cubes and rooms, from two robots, the detector found
nothing at its threshold of 0.9, and four blurred cubes at 0.6. Not tried on a robot, nor on faces of people: the
numbers above come from one public domain photograph, shrunk and blurred to the camera's quality.
