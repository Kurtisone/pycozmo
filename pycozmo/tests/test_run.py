import io
import logging
import os
import unittest

import pycozmo


class TestSetupBasicLogging(unittest.TestCase):

    LOGGERS = ("pycozmo.general", "pycozmo.protocol", "pycozmo.robot",
               "pycozmo.reaction", "pycozmo.behavior", "pycozmo.animation", "pycozmo.emotion")

    def setUp(self):
        # setup_basic_logging() mutates process wide loggers, so put them back afterwards.
        saved = [(logging.getLogger(n), list(logging.getLogger(n).handlers), logging.getLogger(n).level)
                 for n in self.LOGGERS]

        def restore():
            for logger, handlers, level in saved:
                logger.handlers = handlers
                logger.setLevel(level)
        self.addCleanup(restore)

        for name in ("PYCOZMO_LOG_LEVEL", "PYCOZMO_PROTOCOL_LOG_LEVEL", "PYCOZMO_ROBOT_LOG_LEVEL"):
            if name in os.environ:
                value = os.environ[name]
                self.addCleanup(os.environ.__setitem__, name, value)
            else:
                self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

        self.stream = io.StringIO()

    def test_defaults(self):
        pycozmo.setup_basic_logging(target=self.stream)
        self.assertEqual(logging.getLogger("pycozmo.general").level, logging.INFO)
        self.assertEqual(logging.getLogger("pycozmo.protocol").level, logging.INFO)
        # The robot logger stays quieter on purpose.
        self.assertEqual(logging.getLogger("pycozmo.robot").level, logging.WARNING)

    def test_explicit_name(self):
        pycozmo.setup_basic_logging(log_level="DEBUG", target=self.stream)
        self.assertEqual(logging.getLogger("pycozmo.general").level, logging.DEBUG)

    def test_explicit_number(self):
        # The environment path already produced numbers, so the signature accepts them too.
        pycozmo.setup_basic_logging(log_level=logging.ERROR, target=self.stream)
        self.assertEqual(logging.getLogger("pycozmo.general").level, logging.ERROR)

    def test_environment(self):
        os.environ["PYCOZMO_LOG_LEVEL"] = "DEBUG"
        os.environ["PYCOZMO_ROBOT_LOG_LEVEL"] = "ERROR"
        pycozmo.setup_basic_logging(target=self.stream)
        self.assertEqual(logging.getLogger("pycozmo.general").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("pycozmo.robot").level, logging.ERROR)

    def test_argument_beats_environment(self):
        os.environ["PYCOZMO_LOG_LEVEL"] = "DEBUG"
        pycozmo.setup_basic_logging(log_level="ERROR", target=self.stream)
        self.assertEqual(logging.getLogger("pycozmo.general").level, logging.ERROR)

    def test_output_reaches_target(self):
        pycozmo.setup_basic_logging(log_level="DEBUG", target=self.stream)
        logging.getLogger("pycozmo.general").info("hello from the test")
        self.assertIn("hello from the test", self.stream.getvalue())
