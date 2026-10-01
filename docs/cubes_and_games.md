Cubes and games
===============

How the brain treats the Light Cubes, how the robot handles them, and the games it plays with them. Where the cubes
are comes from their markers: see [vision.md](vision.md#cube-markers).


The cubes
---------

The brain does as the Cozmo application did with the cubes. It connects one of each kind as soon as the robot hears
it, and lights it with Anki's own cube light animations: a dim cyan breath every five seconds once connected, a steady
cyan while the robot sees it. It looks for markers five times a second while the robot keeps still, and places each
cube it sees in the robot's world frame, in `cli.cubes`. A cube seen for the first time, or where it was moved to, is
acknowledged (`ObjectPositionUpdated`), and one moved while the robot sees it is reacted to (`CubeMoved`) - unless
the robot moved it itself, lifting it, docking with it or tapping it in a game. Taps and moves come as `EvtCubeTapped`
and `EvtCubeMovingChange`, sightings as `EvtCubeObserved`. A light frame lasts 33.3 ms, measured on a robot.


Handling
--------

`pycozmo.cube_handling` handles the cubes as Anki's engine was seen doing it, recorded through the official SDK on a
robot: it goes to stand some 15 cm from a cube and has a look, docks with its head down, looking at the marker again on
the way, and makes the manoeuvre's own moves. It picks the cube up, the lift rising as the robot creeps on; puts it
down; sets it on another, letting go at 76 mm; rolls it, the fork hooking the top edge at 74 mm and coming down as the
robot backs off, which tips the cube over towards it; and pops a wheelie, the lift slamming down on the cube as the
robot drives on at 150 mm/s. The brain's behaviors build on it. `PlayAlone` picks a cube up and works out with it
(`CubeLiftWorkout`, as many lifts as the robot is confident, in a workout its energy chooses), stacks one on another
(`StackBlocks`), rolls one lying on its side back upright (`RollBlock`) and pops wheelies (`PopAWheelie`). A cube says
which side is up, not which way its top points; when a roll shows it points aside, `RollBlock` goes round the cube to
its bottom. All this is checked against an emulator of the robot, which is not part of this repository, and not yet on a
robot as written. Knocking a stack over is not done: recorded on a robot, Anki's own behavior gave it up.


Games
-----

`pycozmo.quick_tap` plays Quick Tap, the cube game of the Cozmo application: both cubes light up, and on the same
colour the first to tap their cube wins the point, on different colours whoever taps loses it. The robot sits at its
cube, the lift raised over it, and taps it with Anki's animations. The game's rules were the application's code; these
are PyCozmo's reading of them. `pycozmo.memory_match` plays Memory Match: the three cubes light up one after
another, the player repeats the pattern by tapping them, and so does Cozmo, turning to point at each with Anki's
animations; the pattern grows by one each round, and whoever gets it wrong first loses. `pycozmo.keep_away` plays
Keep Away: Cozmo raises its lift and pounces on the player's cube, or pretends to; pulled away in time, the point is
the player's, caught or flinched, Cozmo's. `PlayWithHumans` has the robot ask for a game now and then: the player
takes it up by tapping a cube - for Quick Tap and Keep Away, the cube becomes theirs - and turning it down, letting
the request time out, makes the robot wait longer before asking again.
