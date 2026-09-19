"""

Helper functions for running PyCozmo applications.

"""

from typing import Iterator, Optional, TextIO, Union
import sys
import os
import logging
from contextlib import contextmanager

from .logger import logger, logger_protocol, logger_robot, logger_reaction, logger_behavior, \
    logger_animation, logger_emotion
from . import client
from . import exception


__all__ = [
    'setup_basic_logging',
    'connect',
]


def setup_basic_logging(
        log_level: Optional[Union[int, str]] = None,
        protocol_log_level: Optional[Union[int, str]] = None,
        robot_log_level: Optional[Union[int, str]] = None,
        target: TextIO = sys.stderr) -> None:

    if log_level is None:
        env_level = os.environ.get('PYCOZMO_LOG_LEVEL')
        log_level = logging.INFO if env_level is None else env_level
    if protocol_log_level is None:
        env_level = os.environ.get('PYCOZMO_PROTOCOL_LOG_LEVEL')
        protocol_log_level = logging.INFO if env_level is None else env_level
    if robot_log_level is None:
        # Keeping the default to WARNING due to "AnimationController.IsReadyToPlay.BufferStarved" messages.
        env_level = os.environ.get('PYCOZMO_ROBOT_LOG_LEVEL')
        robot_log_level = logging.WARNING if env_level is None else env_level
    handler = logging.StreamHandler(stream=target)
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(log_level)
    logger_protocol.addHandler(handler)
    logger_protocol.setLevel(protocol_log_level)
    logger_robot.addHandler(handler)
    logger_robot.setLevel(robot_log_level)
    logger_reaction.addHandler(handler)
    logger_reaction.setLevel(robot_log_level)
    logger_behavior.addHandler(handler)
    logger_behavior.setLevel(robot_log_level)
    logger_animation.addHandler(handler)
    logger_animation.setLevel(robot_log_level)
    logger_emotion.addHandler(handler)
    logger_emotion.setLevel(robot_log_level)


@contextmanager
def connect(
        log_level: Optional[str] = None,
        protocol_log_level: Optional[str] = None,
        protocol_log_messages: Optional[list] = None,
        robot_log_level: Optional[str] = None,
        auto_initialize: bool = True,
        enable_animations: bool = True,
        enable_procedural_face: bool = True) -> Iterator[client.Client]:

    setup_basic_logging(log_level=log_level, protocol_log_level=protocol_log_level, robot_log_level=robot_log_level)

    try:
        cli = client.Client(
            protocol_log_messages=protocol_log_messages,
            auto_initialize=auto_initialize,
            enable_animations=enable_animations,
            enable_procedural_face=enable_procedural_face)
        cli.start()
        cli.connect()
        cli.wait_for_robot()
    except exception.PyCozmoException as e:
        logger.error(e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted...")
        sys.exit(0)

    try:
        # Exceptions, generated from the application are intentionally not handled.
        yield cli
    except KeyboardInterrupt:
        logger.info("Interrupted...")
    finally:
        cli.disconnect()
        cli.stop()

    logger.info("Done.")
