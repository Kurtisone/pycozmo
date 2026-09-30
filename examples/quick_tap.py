#!/usr/bin/env python
"""
Play Quick Tap with Cozmo: put a cube in front of it, keep another, and tap yours when both light up the same
colour - before Cozmo does. Tapping on different colours loses the point.
"""

import time

import pycozmo
from pycozmo import cube_handling, quick_tap


with pycozmo.connect() as cli:

    cli.load_anims()
    cli.cubes.auto_connect = True
    print("Waiting for two cubes...")
    while len([cube for cube in cli.cubes if cube.connected]) < 2:
        time.sleep(0.5)

    print("Looking for a cube...")
    cli.enable_camera(True, color=False)
    cli.set_head_angle(cube_handling.LOOK_HEAD_ANGLE)
    time.sleep(1.0)
    seen = []
    for _ in range(3):
        seen = [cube for cube in cube_handling.observe(cli, timeout=2.0) if cube.connected]
        if seen:
            break
    if not seen:
        raise SystemExit("No cube in front of Cozmo.")
    cozmo_cube = seen[0]
    player_cube = next(cube for cube in cli.cubes if cube.connected and cube is not cozmo_cube)
    print("Cozmo plays with {}, you with {}.".format(cozmo_cube.object_type.name, player_cube.object_type.name))

    if not quick_tap.take_position(cli, cozmo_cube):
        raise SystemExit("Cozmo could not get to its cube.")
    winner = quick_tap.QuickTap(cli, cozmo_cube, player_cube).play()
    print("Cozmo wins!" if winner == quick_tap.COZMO else "You win!")
    quick_tap.leave_position(cli)
