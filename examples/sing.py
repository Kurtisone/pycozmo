#!/usr/bin/env python
"""
Have Cozmo sing one of its songs, with Anki's animations: by default Pop Goes the Weasel, or the one given, as its
singing behavior names it - Singing_DannyBoy, say. Without an argument it also lists them.

The sung notes are WWise Vorbis: convert them first with tools/pycozmo_convert_audio.py, or Cozmo sings silently.
"""

import glob
import json
import os
import sys
import time

import pycozmo
from pycozmo import song_behaviors


def songs() -> dict:
    """ The singing behaviors' configurations, by behavior ID. """
    pattern = os.path.join(str(pycozmo.util.get_cozmo_asset_dir()), "cozmo_resources", "config", "engine",
                           "behaviorSystem", "behaviors", "freeplay", "singing", "*.json")
    confs = (json.load(open(path)) for path in glob.glob(pattern))
    return {conf["behaviorID"]: conf for conf in confs}


confs = songs()
name = sys.argv[1] if len(sys.argv) > 1 else "Singing_PopGoesTheWeasel"
if len(sys.argv) < 2:
    print("Songs:", ", ".join(sorted(confs)))
if name not in confs:
    sys.exit("No song {}.".format(name))

with pycozmo.connect() as cli:

    cli.load_anims()
    done = []
    cli.add_handler(pycozmo.event.EvtBehaviorDone, lambda cli: done.append(True))
    behavior = song_behaviors.BehaviorSinging(cli, confs[name])
    if not behavior.wants_to_run():
        sys.exit("Cozmo cannot sing {}: are its notes converted?".format(name))
    cli.activate_behavior(behavior)
    while not done:
        time.sleep(0.5)
    cli.deactivate_behavior(behavior)
