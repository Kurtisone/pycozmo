#!/usr/bin/env python
"""
Show the faces Cozmo sees, and who they are. Given a name, Cozmo first gets to know the face in front of it: look
straight at its camera, a little way off, until it says it has.

Needs OpenCV and the models: pip install opencv-python-headless, then pycozmo_faces.py download. The people Cozmo knows
are kept as features, not pictures, in your PyCozmo directory; pycozmo_faces.py lists, renames and forgets them.
"""

import sys
import time

import pycozmo


name = sys.argv[1] if len(sys.argv) > 1 else None

with pycozmo.connect() as cli:

    if not cli.faces.available():
        sys.exit("Faces need OpenCV and the models: pip install opencv-python-headless; pycozmo_faces.py download")

    calibration = cli.read_camera_calibration()
    cli.set_lift_height(pycozmo.robot.MIN_LIFT_HEIGHT.mm)
    cli.set_head_angle(pycozmo.util.Angle(degrees=20.0).radians)
    time.sleep(1.0)
    cli.enable_camera(True, color=False)

    def on_image(cli, image):
        cli.faces.process(image, calibration, cli.head_angle.radians, cli.pose_pitch.radians)

    cli.add_handler(pycozmo.event.EvtNewRawCameraImage, on_image)
    cli.add_handler(pycozmo.event.EvtFaceAppeared, lambda cli, face: print("A face appeared:", face))
    cli.add_handler(pycozmo.event.EvtFaceIdentified, lambda cli, face: print("It is {}.".format(face.name)))
    cli.add_handler(pycozmo.event.EvtFaceDisappeared, lambda cli, face: print("Gone:", face))

    if name:
        print("Look at Cozmo, {}...".format(name))
        print("Done." if cli.faces.enroll(name) else "No face seen for long enough.")

    print("Watching. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1.0)
            for face in cli.faces.visible():
                print("{} at {:.0f} mm".format(face.name or "Somebody", face.distance))
    except KeyboardInterrupt:
        pass
