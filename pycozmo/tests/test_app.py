import argparse
import importlib.util
import os
import unittest
from typing import Any

import pycozmo


APP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools", "pycozmo_app.py")


def load_app():
    """ Load pycozmo_app.py as a module. Importing it does not run main(). """
    spec = importlib.util.spec_from_file_location("pycozmo_app", APP)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.path.isfile(APP), "pycozmo_app.py not in this tree.")
class TestRobotAddress(unittest.TestCase):
    """ The address of the robot, so the application can be pointed at an emulator. """

    app: Any

    @classmethod
    def setUpClass(cls):
        cls.app = load_app()

    def test_host_and_port(self):
        self.assertEqual(self.app.robot_address("127.0.0.1:5551"), ("127.0.0.1", 5551))

    def test_host_alone_keeps_the_default_port(self):
        self.assertEqual(self.app.robot_address("127.0.0.1"),
                         ("127.0.0.1", pycozmo.conn.ROBOT_ADDR[1]))

    def test_a_name_is_accepted(self):
        self.assertEqual(self.app.robot_address("cozmo.local:9000"), ("cozmo.local", 9000))

    def test_a_port_that_is_not_a_number_is_refused(self):
        # argparse turns this into a usage error rather than a traceback.
        with self.assertRaises(argparse.ArgumentTypeError):
            self.app.robot_address("127.0.0.1:port")


@unittest.skipUnless(os.path.isfile(APP), "pycozmo_app.py not in this tree.")
class TestArguments(unittest.TestCase):

    app: Any

    @classmethod
    def setUpClass(cls):
        cls.app = load_app()

    def parse(self, argv):
        import sys
        saved = sys.argv
        sys.argv = ["pycozmo_app.py"] + argv
        try:
            return self.app.parse_args()
        finally:
            sys.argv = saved

    def test_defaults(self):
        args = self.parse([])
        self.assertIsNone(args.robot_addr, "the library default is left alone")
        self.assertFalse(args.no_face)
        self.assertFalse(args.verbose)

    def test_robot_addr(self):
        self.assertEqual(self.parse(["--robot-addr", "127.0.0.1:41754"]).robot_addr,
                         ("127.0.0.1", 41754))
        self.assertEqual(self.parse(["-r", "127.0.0.1:1"]).robot_addr, ("127.0.0.1", 1))

    def test_no_face(self):
        self.assertTrue(self.parse(["--no-face"]).no_face)

    def test_verbose(self):
        self.assertTrue(self.parse(["-v"]).verbose)
