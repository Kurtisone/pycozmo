#!/usr/bin/env python
"""
Play Keep Away with Cozmo: put a cube in front of it, and pull the cube away when Cozmo pounces - not when it only
pretends to. Cozmo moves to where its pounce reaches the cube.

Cozmo lunges when it pounces: keep it away from the edge of the table.
"""

import time

import pycozmo
from pycozmo import cube_handling, keep_away


with pycozmo.connect() as cli:

    cli.load_anims()
    cli.cubes.auto_connect = True
    print("Waiting for a cube...")
    while not any(cube.connected for cube in cli.cubes):
        time.sleep(0.5)
    cube = next(cube for cube in cli.cubes if cube.connected)

    print("Put {} in front of Cozmo.".format(cube.object_type.name))
    while not cube_handling.look_for_cube(cli, cube, timeout=5.0):
        pass

    winner = keep_away.KeepAway(cli, cube).play()
    print("Cozmo wins!" if winner == keep_away.COZMO else "You win!" if winner else "The cube is gone.")
