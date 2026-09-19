#!/usr/bin/env python
"""

PyCozmo application.

"""

import sys
import time
import argparse

import pycozmo


def robot_address(value):
    """ Parse a robot address, given as HOST or HOST:PORT. """
    if ":" in value:
        host, _, port = value.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            raise argparse.ArgumentTypeError("'{}' is not a port number.".format(port))
    return value, pycozmo.conn.ROBOT_ADDR[1]


def parse_args():
    """ Parse command-line arguments. """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose")
    parser.add_argument(
        "-r", "--robot-addr", type=robot_address, metavar="HOST[:PORT]",
        help="address of the robot, to reach an emulator instead of the default {}:{}".format(
            *pycozmo.conn.ROBOT_ADDR))
    parser.add_argument(
        "--no-face", action="store_true",
        help="do not draw the procedural face, which redraws the screen 30 times a second")
    args = parser.parse_args()
    return args


def main():
    # Parse command-line.
    args = parse_args()

    if args.robot_addr:
        pycozmo.conn.ROBOT_ADDR = args.robot_addr

    try:
        with pycozmo.connect(
                log_level="DEBUG" if args.verbose else "INFO",
                protocol_log_level="INFO",
                robot_log_level="INFO",
                enable_procedural_face=not args.no_face) as cli:
            brain = pycozmo.brain.Brain(cli)
            brain.start()
            while True:
                try:
                    time.sleep(1.0)
                except KeyboardInterrupt:
                    break
            brain.stop()
    except Exception as e:
        print("ERROR: {}".format(e))
        sys.exit(1)


if __name__ == '__main__':
    main()
