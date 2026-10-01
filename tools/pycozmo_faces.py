#!/usr/bin/env python
"""

Face manager: the models that find faces, and the people Cozmo knows by name.

Finding faces needs OpenCV (pip install pycozmo[faces]) and two small models, which this fetches. The people Cozmo
knows are kept as features - 128 numbers a face - not pictures, in the user's own PyCozmo directory.

"""

import sys
from typing import Dict
import argparse

import pycozmo
from pycozmo import face_detection


def do_status() -> None:
    """ Show what is needed to find faces, and who is known. """
    directory = pycozmo.util.get_face_model_dir()
    opencv = "found" if face_detection.opencv_available() else "NOT found - pip install pycozmo[faces]"
    print("OpenCV:  {}".format(opencv))
    print("Models:  {} in {}".format("found" if face_detection.models_present() else "NOT found", directory))
    names = pycozmo.faces.FaceGallery().names()
    print("Known:   {}".format(", ".join(names) if names else "nobody"))


def do_download() -> None:
    """ Fetch the models. """
    shown: Dict[str, int] = {}

    def progress(name: str, read: int, total: int) -> None:
        percent = 100 * read // total if total else 0
        if shown.get(name) != percent // 10:
            shown[name] = percent // 10
            print("{}: {}%".format(name, percent))

    face_detection.download_models(progress=progress)
    print("Models in {}".format(pycozmo.util.get_face_model_dir()))


def do_forget(name: str) -> None:
    """ Forget a person. """
    if not pycozmo.faces.FaceGallery().forget(name):
        print("Nobody called {}.".format(name))
        sys.exit(1)
    print("Forgot {}.".format(name))


def do_rename(name: str, new_name: str) -> None:
    """ Call a person by another name. """
    if not pycozmo.faces.FaceGallery().rename(name, new_name):
        print("Nobody called {}.".format(name))
        sys.exit(1)
    print("{} is now {}.".format(name, new_name))


def parse_args() -> argparse.Namespace:
    """ Parse command-line arguments. """
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="cmd")
    subparsers.add_parser("status", help="show what is needed to find faces, and who is known")
    subparsers.add_parser("download", help="download the face detection and recognition models")
    forget = subparsers.add_parser("forget", help="forget a person")
    forget.add_argument("name")
    rename = subparsers.add_parser("rename", help="call a person by another name")
    rename.add_argument("name")
    rename.add_argument("new_name")
    args = parser.parse_args()
    if not args.cmd:
        print(parser.format_usage())
        sys.exit(1)
    return args


def main():

    # Parse command-line.
    args = parse_args()

    if args.cmd == "status":
        do_status()
    elif args.cmd == "download":
        do_download()
    elif args.cmd == "forget":
        do_forget(args.name)
    elif args.cmd == "rename":
        do_rename(args.name, args.new_name)
    else:
        assert False


if __name__ == "__main__":
    main()
