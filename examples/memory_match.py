#!/usr/bin/env python
"""
Play Memory Match with Cozmo: line the three cubes up in front of it, watch them light up one after another, and tap
them in the same order. Cozmo repeats the pattern too, pointing at the cubes. Pass "solo" to play alone.
"""

import sys
import time

import pycozmo
from pycozmo import cube_handling, memory_match


with pycozmo.connect() as cli:

    cli.load_anims()
    cli.cubes.auto_connect = True
    print("Waiting for the three cubes...")
    while not all(cube.connected for cube in cli.cubes):
        time.sleep(0.5)
    cubes = sorted(cli.cubes, key=lambda cube: cube.object_type.value)

    print("Looking at the cubes...")
    cli.enable_camera(True, color=False)
    cli.set_head_angle(cube_handling.LOOK_HEAD_ANGLE)
    time.sleep(1.0)
    for _ in range(10):
        cube_handling.observe(cli)
    if not memory_match.face_cubes(cli, cubes):
        print("No cube seen: Cozmo will point straight ahead.")

    game = memory_match.MemoryMatch(cli, cubes)
    if sys.argv[1:] == ["solo"]:
        print("Your best: {} cubes.".format(game.play_solo()))
    else:
        winner = game.play()
        print("Cozmo wins!" if winner == memory_match.COZMO else "You win!" if winner else "Nobody wins.")
