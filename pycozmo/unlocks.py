"""

Unlocks: what the robot has earned the right to do.

Anki's app kept them. A new robot had those of unlock_config_nurture.json - the games, picking up, rolling and
stacking cubes, knocking a stack over, pouncing, a few songs - and earned the rest a level at a time, as
needs_level_config.json rewards them: the fist bump first, then the fire truck alarm, the wheelie, the pyramid, the
laser, the workout...

Seventy-seven behaviors name the unlock they need. PyCozmo keeps no progression, so the brain takes the robot to
have earned them all; an application that keeps one narrows Brain.unlocks.

"""

import os
from typing import List, Set

from .json_loader import load_json_file


__all__ = [
    "load_default_unlocks",
    "load_level_unlocks",
    "load_all_unlocks",
]


def load_default_unlocks(resource_dir: str) -> List[str]:
    """ The unlocks a new robot has. """
    filename = os.path.join(resource_dir, "cozmo_resources", "config", "engine", "unlock_config_nurture.json")
    return [str(unlock) for unlock in load_json_file(filename)["defaultUnlocks"]]


def load_level_unlocks(resource_dir: str) -> List[List[str]]:
    """ The unlocks each level rewards, level by level. """
    filename = os.path.join(resource_dir, "cozmo_resources", "config", "engine", "needs_level_config.json")
    return [[str(reward["data"]) for reward in level.get("rewards", []) if reward.get("rewardType") == "Unlock"]
            for level in load_json_file(filename)["unlockLevels"]]


def load_all_unlocks(resource_dir: str) -> Set[str]:
    """ Every unlock there is to have. """
    unlocks = set(load_default_unlocks(resource_dir))
    for level in load_level_unlocks(resource_dir):
        unlocks.update(level)
    return unlocks
