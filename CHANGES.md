Revision History
================

This file holds two separate histories. "Fork" covers the changes made in this fork of zayfod/pycozmo, after
upstream development stopped in November 2020. "Upstream" below it is the inherited history, left as it was.


Fork
====

Unreleased
----------

Checked against an emulator of the robot, which is not part of this repository, and in part on a robot, with the Cozmo
application's own engine recorded through the SDK beside it: how cubes connect, the animation stream, the cube lights,
and the moves of the games and of Anki's cube handling. On a robot, tried: picking a cube up and putting it down, and
setting one on another, each to the user's eye; rolling a cube, which went over each time; a song, which sounded
right; driving off the charger; a whole game of Quick Tap and a whole game of Memory Match, with the user as the
player: the cubes lit up, the taps were told, Cozmo turned to each cube in the pattern, within 3 to 11 degrees, and
its animations for a hand, a round and a game played. A quick player beat Cozmo's tap in most hands of Quick Tap, which
cannot come sooner than 0.5 s after the lights. Three songs, one at each tempo, were sung with the wheels moving as
the animations move them, the robot staying within 36 mm and 16 degrees of where it began, and the user found it very
good. Not tried: a whole game of Keep Away, popping a wheelie, and most of the behaviors that run these.
Keep Away was played in hands: Cozmo took its place, waited, pretended and pounced, and the cube said it was tapped
when the lift came down.
The RollBlock behavior, on an upside-down cube, had the robot rear up to 50 degrees and fall back on a first try, at
3.6 V, the cube not rolled, and why is not known: its lift came down in 0.13 s, not the 1.1 s it was asked to take. On
a second try, at 4.0 V, the same behavior on the same cube rolled it as Anki's engine did, the lift down in 0.9 s and
the nose up 33 degrees, and the user was pleased; the robot did not find the cube again for the roll after.

New features:
- Handling the cubes. pycozmo.cube_handling does it as Anki's engine was recorded doing it on a robot: it finds a
    cube, looking round for it if need be, goes to stand some 15 cm from the side it saw and has a look, then docks
    with its head down, steering by the marker in each camera image. It picks the cube up, the lift rising as the
    robot creeps on; puts it down; sets it on another, letting go at 76 mm; rolls it, the lift coming steadily down
    and the robot backing off part way into it; and pops a wheelie, the lift slamming down on the cube as the robot
    drives on at 150 mm/s. The distances are Anki's, 2.5 mm longer, as PyCozmo places cubes. A cube an animation sets
    down is followed where the fork pushes it.
- The charger's marker. pycozmo.charger_detection finds it in a camera image and places it in the robot's frame: a dark
    ring 24 mm wide and 17.5 high on the charger's face, with a battery drawn in it. The marker is drawn at every size it
    can have and matched with the image, normalised so that a dim room does as well as a bright one, and its pose is
    then fitted to every pixel of the sticker. Tried on a robot, in 16 images from 21 to 46 cm, it was found in each,
    to 5% in distance and 6 mm sideways; its heading, seen squarely, is told no better than 15 degrees, and from a way
    round, in drawn images, to 8. Nothing was taken for it in 159 images without a charger but four, all of a cube,
    which a caller that knows the cubes' frames can pass to avoid.
- Going back to the charger. Anki's application never did it: the robot was put on its charger by hand. Client.charger
    keeps where the charger is - from where the robot stood on it at the start, and from the views of its marker put
    together, in the frame the robot's position is in, which a pick-up ends. pycozmo.charger_handling.go_to_charger()
    looks for the marker, where it remembers the charger to be and then all round; drives round the charger if it is
    behind it; stands 20 cm in front, and looks again; turns round, and backs on, with the cliff sensors off, until the
    robot says it is on, and tries again if that does not take. The marker is not in the middle of the charger: the
    ramp's axis is 22 mm to the marker's left, looking the way it faces (AXIS_OFFSET): backings 4 to 24 mm to its
    other side, by the marker, and one aimed 13 mm to the left, went off to the right of the charger, the right of a
    Cozmo on it, and one aimed 24 mm to the left went off to the left; those that docked were 18 and 24 mm to the
    left, and the window is some 14 mm wide. The robot stands 165 mm in front of the marker to turn round, where a
    heading a few degrees out takes it less off its line before the ramp, and the heading it rested on the charger with
    counts, against the views, for that of three seen from the side. A half turn the gyro
    reports as 180 degrees is 182.4 on the floor, 1.3% more, which sent the robot back along a line 5 degrees out; it is
    asked for that much less (TURN_SCALE). The robot's own camera calibration is read, which the first tries used one
    of another robot's for, with a focal length 4% off and an optical centre 12 px out. The robot stops backing when
    one tread is held against the ramp's edge, which pushed the charger along the floor, and after a miss drives
    straight out to 18 cm in front of the marker, not round the charger. The views of the marker are put together by
    where the robot was when it saw them, the newer counting for more, for odometry is some 20 mm out from one place
    to the next; its heading is told by the views from the side, which are better than those seen squarely. The rest
    has been tried against an emulator of the robot, which has the charger, its marker, its ramp and its contacts.
    Not yet: a backing that took, other than by hand.
- Anki's cube behaviors: PutDownBlock, PickUpCube, PickUpAndPutDownCube, StackBlocks, RollBlock, PopAWheelie, and
    CubeLiftWorkout, which reads Anki's four workouts, lifts as many times as the robot is confident, and has its
    energy choose the workout. Each runs its steps on a thread of its own, and deactivating it cancels them. A
    behavior goes for a cube connected, seen, the right way up, with nothing on top and not in the lift; RollBlock
    for one lying on its side, which it rolls back upright. A cube says which side is up, not which way its top
    points, and when a roll shows it points aside, the robot goes round the cube to its bottom.
- Cubes.factory_ids chooses which cube of a kind the robot connects when it hears several.
- Unlocks. A behavior needing an unlock runs only if the robot has it; pycozmo.unlocks reads those of a new robot
    and those the needs levels reward. Nothing keeps a progression, so the brain takes the robot to have them all.
- Quick Tap. pycozmo.quick_tap plays the game of the Cozmo application, by PyCozmo's reading of its rules: the
    cubes light up, the same colour and the first to tap wins the point, different colours and whoever taps loses
    it. The robot taps its cube with Anki's animations, the lift raised over it, and goes back to its place before
    each hand, which those animations turn it off a few degrees at a time. examples/quick_tap.py plays a game.
- Memory Match. pycozmo.memory_match plays the other cube game of the application: the three cubes light up in a
    pattern one longer each round, the player repeats it by tapping them, and so does Cozmo, turning to point at
    each with Anki's animations - straight ahead, a small turn or a big one, by where the cube is. Whoever gets it
    wrong first loses; alone, the player scores the longest pattern repeated. examples/memory_match.py plays a game.
- Keep Away. pycozmo.keep_away plays the third cube game of the application: Cozmo moves to where its pounce
    reaches the player's cube, raises its lift, and pounces with Anki's animations, or pretends to. The cube tells
    what happened: a tap, the lift came down on it, Cozmo's point; a move, the player pulled it away, theirs; a move
    while Cozmo only waited or pretended, a flinch, Cozmo's. examples/keep_away.py plays a game.
- Asking for a game. RequestGameSimple asks for Quick Tap, Memory Match or Keep Away, and the PlayWithHumans
    activity is evaluated: the robot asks now and then, the player takes the game up by tapping a cube, and a no
    makes it wait 120 s, 1.3 times as long for each no in a row. EvtGameRequestAnswered carries the answer.
- The top and bottom of a cube, whose symbol has no bar, are recognized as well as its sides.
- Faces. pycozmo.face_detection finds faces in the camera images with OpenCV's YuNet, and tells them apart with
    SFace; OpenCV is the pycozmo[faces] extra, not a dependency, and pycozmo_faces.py fetches the two models.
    Client.faces keeps track of the faces seen - where each is, from the distance between its eyes; which is which,
    from their features - and knows people by name: Faces.enroll() takes a few views of the face in front of the
    camera, and keeps the features, not the pictures, in the user's own directory. EvtFaceAppeared, EvtFaceObserved,
    EvtFaceIdentified and EvtFaceDisappeared; a face that appears raises Anki's FacePositionUpdated, which has
    Cozmo acknowledge it. A face is taken for one at a score of 0.9 but followed down to 0.7, and told by the
    last six views of it, not the last: in a dim room, the first try on a robot, one person was seven faces. Not yet
    tried on anybody else. examples/faces.py shows what Cozmo sees.
- Songs. pycozmo.songs sings Cozmo's songs: the singing animations' event plays a song a WWise switch picks, a MIDI
    track of Cozmo's sound bank, and PyCozmo renders it as the application's sound engine did, with the instrument of
    sung notes the bank holds, then cuts it short where the animation stops it. Client.set_audio_switch() picks the
    song. The Singing behavior sings each of the 39 its configuration names, and the Singing activity has Cozmo sing
    now and then in freeplay. The notes are Vorbis: Cozmo sings once they are converted. How a held note ends, and
    the instrument's vibrato, are PyCozmo's reading of the bank: see the module. The Dance behavior plays its moves.
- Cube markers are found close to as well, where the cube's black corners touch the frame and the dark pixels no
    longer outline it: from the hole inside them. Over 4092 images of two robots, 130 more of the cubes Anki's
    engine saw are found, and they are placed as well as the others, within a few mm.
- Cube markers are found with the cubes' lights on. Three cubes lit, 20 to 26 cm from a robot in a dim room, were
    none of them seen: the dark about each light was joined to the ring of its frame in a neighbourhood 12 pixels
    around. A frame is looked for with a neighbourhood 6 pixels around as well. Over 4154 images of two robots, 13
    more of the cubes Anki's engine saw are found, and cubes it did not know of; the search takes twice as long.
- A cube light frame lasts 33.3 ms, as measured on a robot, not 30 ms.

Bug fixes:
- Client.load_anims() took 8 s, 7 of them finding the files of the animation groups' 573 triggers: each walked the whole
    tree of Cozmo's resources, 75 000 directories in all. The tree is walked once, and it takes 0.3 s; the brain starts
    as fast, and the tests that load the animations, which are most of them, are the quicker for it.
- Quick Tap gave the hand to whichever cube was heard first when Cozmo's tap came down, and on a robot every cube
    reports that tap, in the order of their numbers: the player's cube, when it had the lower number, was heard before
    Cozmo's, and the player won hands they had not tapped. The cubes that report within 0.1 s of each other, 0.4 s or
    more after the tap was asked for, are Cozmo's tap; a tap of the player's before them is still the player's.
- Cozmo's tap in Quick Tap pushed its cube, once in a round of 15 hands on a robot, and the next taps would have fallen
    short of it. The cube says it has moved; the robot backs off, looks for the cube and takes its place again before the
    next hand.
- Quick Tap drove the robot at its cube after a hand when the robot had been lifted: its position began again at zero,
    in a new frame, and the place the game held, in the old one, put the robot 130 mm from where it stood. The place
    is now the robot's where the frame has changed, and the robot goes back no more than 60 mm.
- Keep Away gave Cozmo the point for a tap the cube reported, and a player who had pulled the cube away in time lost
    the hand: the lift slamming down on the floor beside the cube, or the cube jerked away, made it report a tap. A
    pounce is now judged by the lift and by whether the cube moved. On a robot the lift stopped at 52 to 55 mm on a cube
    left in place, and went down to 27 to 49 mm when it had been taken away: the clips whose lift is sent to 48 or to 57
    mm stop at 46 and 49 on the floor, within 4 mm of a cube. So from 55 mm up the lift is on the cube; below 45 it came
    down on nothing; between, a cube that moved was pulled away in time, and one that did not is caught if it said it
    was tapped. The lift's first coming down is the one that counts: every pounce ends with it going to the bottom,
    the robot having backed off. A robot that says nothing of its lift is still judged by the cube's tap. The middle
    zone is not clean: on a robot a hand the user took for a pull too late, the lift landing at 45.7 mm, was counted
    for the player, which it may rightly have been, Cozmo having only just missed the cube; a pull in time had landed
    at 48.7. What a cube said of the knock, and when, did not tell the two apart.
- The cubes lit up by Quick Tap and Memory Match showed the wrong colours: they were told 0 frames on and 0 off, and a
    white came out as two lights, a yellow and a red. Anki's patterns for a light that stays are 30 frames on, a second,
    and none off, which cube_lights.steady() gives.
- A colour of several channels, on all four of a cube's lights, lost channels when it was too strong, with the frames
    right: a white of 31 each came out as two yellow lights and two red, a yellow of 31 and 24 the same, a cyan of 31
    and 31 as two green and two cyan; a colour of one channel, at 31, was right, and so was a single light in white.
    cube_lights.limit() scales a colour down to what fits - a cost of red, 1.4 times green and 0.7 times blue, under
    50 - keeping its hue, and steady() and Anki's patterns are limited with it: the games' yellow is 23 and 18, white
    16 each, cyan 23 each, each seen right by the user, on four lights. It is the cubes' limit, not the frames'; the
    cubes' batteries read 140 to 145, and no other cause was found.
- A cube reported one knock twice in Memory Match, 0.12 s apart, and the second was taken for the next tap, a wrong one:
    the player lost a hand they had won. A cube that has spoken is not heard again for 0.3 s. A cube also misses
    knocks, which nothing here can mend: on a robot it told 8 of the 10 or 11 the user gave it, once each, and in a
    longer run twice 5 of 17 times, 0.07 to 0.16 s after the knock. Its accelerometer, streamed at 29 Hz, is too slow
    to see a knock, and a knock does not move the cube, so its moves tell nothing either; the protocol has no setting
    for how hard a cube has to be knocked. A tap the cube misses costs the player a hand of Memory Match and a point of
    Quick Tap; Memory Match gives 10 s a tap, not 6, for the player to see no light and tap again.
- Cozmo's tap in Quick Tap came late, and fell short. A robot takes some 0.4 s to start an animation it is sent, so its
    lift met the cube 0.5 to 0.57 s after the tap was asked for, not the 0.13 s of the clip, and the player, tapping
    in 0.7 s, won nearly every hand. The tap is now asked for that much before it is to land. Cozmo also stood at 50
    mm from its cube, the fork hardly over the cube's top: it stands at 42 mm, and the fork comes down on the cube.
- Looking round for a cube, the robot turned 45 degrees at a time, the camera seeing 57: a cube 12 cm away
    stood across the edges of two views, whole in neither, and was not found in a full turn. It turns 30 degrees at
    a time.
- DriveOffCharger took the robot off its charger by 25 mm and no more, the brain having turned on the robot's cliff
    stop, which the charger's lip trips. The cliff stop is off while the robot drives off, and on again after: 95 mm
    in a straight line on a robot, 4 mm to one side.
- Only one cube stayed connected at a time. ObjectConnect's second field, taken for a "connect" flag, is the slot
    the robot puts the cube in, 0 to 4, and every cube went into the same one, pushing the last out. Each kind of
    cube has a slot of its own.
- The robot's animation buffer overflowed, now and then, during animations that drive the wheels: the robot's count
    of the frames it has played runs ahead of the frames it has taken from the buffer, by some twenty over a session.
    What it has played is told by its count of bytes, which is exact: no overflow in 24 animations on a robot.
- A camera image whose last chunk held a single byte, or none, was lost with an error: such byte arrays were read
    as lists.
- The brain reacted to cubes the robot moved itself - lifting one, docking with it, tapping it, knocking one while
    handling another - and the reaction cut short the behavior that had moved it. Cubes in use are left alone, and
    so is the one in the lift, and any cube while the robot handles one or plays a game.
- A cube moved by someone else is no longer taken to be where it was last seen.
- A robot did not pick a cube up, or picked it up crooked, whichever way PyCozmo tried: on a real robot a cube was
    never taken properly, in the six tries made. Three things were wrong. The wheels, told 15 mm/s, do not turn - they
    start at some 20 mm/s - so the 8 mm creep on that lifted the cube was 1 mm; it is a path now. The lift rose in 0.3 s
    where Anki's engine took 0.75 s. And the robot came in by where the cube was seen, a turn and a drive, which leaves
    it a few mm to the side and ten degrees off: it now steers by the marker in each camera image, as Anki's engine did,
    and comes in along the line the cube's side makes to within a millimetre or two, in simulation. On a robot: a cube
    taken flat on the next try. Docking for the other manoeuvres steers the same way.

Documentation:
- The README is a front page again, at half its length: what the robot does, needs, sees and hears, which had grown
    into 330 lines of notes, is in docs/own_behavior.md, sound.md, vision.md and cubes.md, and the Sphinx documentation
    lists them. What the README said wrongly is corrected: the requirements (Pillow was 6.0.0 and NumPy was missing),
    the tools and examples it did not list, the severe-need activities it said never run, and ideas and bug reports,
    which it sent upstream. Links that no longer lead anywhere are gone - the DDL Discord invite, Anki's website, an
    ArUco example, the Google Play page of the app, a wiki page on the sound bank format - and CONTRIBUTING.md now says
    what the README does. The README says who the project is for.
- The Sphinx documentation builds without a warning. Its overview is the README, made from it when the documentation
    is built, instead of a copy of an old one; its API lists the 25 modules it lacked, including all the fork added, and
    no longer one that does not exist; it links Python 3's documentation, not 3.6's. The documents' names no longer
    clash with those of modules, which Sphinx could not tell apart.
- Faces were said to install with pip install pycozmo[faces], which gives upstream's version from PyPI, where there is
    no such extra: the messages and documents say pip install opencv-python-headless, or the extra of a source install.


v0.9.21 (Sep 28, 2026)
----------------------

The first release checked against real robots, two of them. Most of what they showed was wrong on the way to the
robot: the sound, the speed of every animation, the animation stream, the transport.

New features:
- Motion detection. The brain turns the camera on and compares each image with the one before it, and what moved is
    announced as EvtMotionObserved: the fraction of the image, its centroid, Anki's three peripheral regions, and
    where it is on the ground, in mm ahead of and to the left of the robot. Pixels are compared by the ratio of
    their brightness, so that the exposure following the light is not motion; nothing is compared while the camera
    moves, nor for the first 2.5 s of a stream, which on a real robot often scrolls and comes out garbled while the
    sensor locks.

    The ground position comes from the lens calibration every robot got in the factory, kept in NV storage.
    Client.read_camera_calibration() reads it and pycozmo.camera decodes it. A Light Cube 100 mm ahead of the
    treads, filmed at seven head angles, was placed between 117.7 and 120.5 mm ahead of the robot's origin.
- PounceOnMotion. Cozmo watches the ground, turns towards what moves, creeps up on it and pounces with the lift, then
    plays PounceSuccess or PounceFail and backs off. Four behaviors in the resources are this class, and Socialize now
    offers the ones behind its objectives, which its chooser never did. Turns and creeps go by the pose the robot
    reports, since a turning robot's treads slip: its heading changed about half as much as its wheel speeds said.
    Telling a catch from a miss by the lift does not work yet: on a robot, the lift came down all the way either way.
- The robot wakes up when the brain starts, with the ConnectWakeUp animations, as the Cozmo application did.
- Face animations. anim_bored_event_02 and anim_bored_event_04, a swinging clock and a slot machine, show the image
    sequences they name rather than a still face, or a black one.
- EvtConnectionLost. A robot silent for 5 s is given up for lost: nothing more is sent to it, and pycozmo_app stops
    with an error status. It used to resend into the void for as long as the program ran - 29 777 packets in one run.
- Cube markers. pycozmo.marker_detection finds the dark frames around the markers on the Light Cubes' sides, places
    them - centre, facing and distance, in the robot's frame - and tells which cube's symbol each holds, by comparing
    it with Anki's drawings of the three, turned four ways. Checked on a robot, over 16 images from four head angles, a
    cube's marker stayed where it stood with a standard deviation of 0.27 mm, and came out as the Deli Slicer it was
    in all of them; and checked against Anki's own engine, through its SDK, every frame found of a Paperclip and an
    Anglepoise Lamp came out as the cube Anki saw there, placed within 2% of where Anki had it. The markers are
    25 mm wide.
- The cubes, as the Cozmo application had them. The brain connects one Light Cube of each kind as soon as the robot
    hears it and lights it with Anki's cube light animations - a dim cyan breath once connected, a steady cyan while
    the robot sees it. It looks for markers five times a second while the robot keeps still, and places each cube it
    sees in the robot's world frame, in Client.cubes. A cube seen for the first time, or where it was moved to, is
    acknowledged, and one moved in the robot's sight is reacted to: Anki's ObjectPositionUpdated and CubeMoved.
- Moving along paths: Client.turn_in_place() and drive_straight(), and execute_path() for any path, say whether the
    robot got there. The brain has the robot stop at cliffs by itself, as the Cozmo application did, and a cliff
    interrupts a path. The camera's exposure can be set.

Bug fixes:
- Sound came out harsh on a real robot since v0.9.15, which complemented the u-law bytes the way G.711 does. The
    robot takes them uncomplemented: a 440 Hz tone played both ways settled it by ear. Silence is 0x00 again.
- Animations moved far too fast. AnimHead and AnimLift carry their duration in a byte, and one head keyframe in
    eight lasted longer than 255 ms and was cut short, up to 18 times; long moves now go out in pieces. AnimBody's
    unknown field is a curvature radius, now curvature_radius_mm: arcs used to go out as DriveWheels at up to 1.47
    million mm/s, and turns in place as TurnInPlaceAtSpeed with degrees taken for mm/s.
- RobotState's lift_height_mm is the lift's angle in radians, now lift_angle_rad, and lift_position reads it as one.
    It used to be about nought whatever the lift did.
- Animations now go out as the robot plays them. Frames went out 30 times a second, and the robot plays 29.9: the
    frames waiting on it piled up, delaying every animation more, and when a sound followed a silence its buffer of
    8 KB overflowed and was cleared - "BufferFull", then corrupt frames. A frame now goes out only while fewer than ten
    wait on the robot, by the frames it reports played in AnimationState, in less than 7.5 KB, by the bytes. The robot
    never counts the samples of the last frame of a sound, and they appear to keep their room until its buffer is
    cleared: judged by the frames alone, the animations that followed a sound overflowed the buffer. And a robot that
    stops playing is sent no more than its buffer holds; frames went out a tick at a time a second after it stopped.
- "Expecting either audio sample or silence next in animation buffer" when one animation gave way to another:
    EndAnimation went out between frames, or ended one it shared, or ended an animation already over. It now goes out
    in a frame of its own, and only for an animation the robot has started.
- The transport resent a lost packet only once the robot fell silent for 100 ms, and it sends RobotState 30 times a
    second. It now resends after 100 ms without an acknowledgement, and waits twice as long each time, up to 0.8 s.
- The acknowledgement sent to the robot was the sequence number of the last frame received, which most of its frames
    do not have, and which acknowledged packets lost before it. It is now the last packet delivered in order.
- The robot's log printed float and negative arguments as their raw bits: "Speed of 1138982912.000000 deg/s".
- CameraCalibration.undistort() diverged in the corners of the image, where the robot's lens model folds back: pixel
    (0, 239) came out at the optical centre. It now finds the radius by bisection and polishes it by Newton's method,
    and the pixels beyond the model's reach come out at its edge, in their own direction.
- go_to_pose() waited for good on a robot that did not answer, and left a handler behind each time. It turned at the
    robot's top speed, too: the speed of a turn in place is in rad/s, and it sent 40. It now times out, says whether
    the robot got there, and goes at the speeds of Anki's engine's path motion profile.
- The brain and its behaviors handled the client's events on the thread that handles everything the robot sends, and
    a behavior starting an animation for the first time, which prepares it, held up the robot's state and the reports
    the animation stream waits on for as much as 1.2 s. They have a thread of their own now, and sound decodes twice
    as fast and encodes six times as fast: 0.64 s at most.

API changes:
- RobotState.lift_height_mm is now lift_angle_rad, and AnimBody.unknown is now curvature_radius_mm.
- New: EvtMotionObserved, EvtConnectionLost, Client.read_camera_calibration(), camera.CameraCalibration,
    camera.DEFAULT_CALIBRATION, camera.ground_points(), camera.camera_to_robot(), pycozmo.motion_detection,
    pycozmo.marker_detection, event.Dispatcher.listens_to(), Client.cubes, pycozmo.cubes, pycozmo.cube_lights,
    EvtCubeConnectionChange, EvtCubeMovingChange, EvtCubeTapped, EvtCubeObserved, Client.turn_in_place(),
    drive_straight(), execute_path(), enable_stop_on_cliff(), set_camera_exposure(), enable_auto_exposure(), and
    robot.PATH_SPEED, PATH_ACCEL, PATH_DECEL, POINT_TURN_SPEED, POINT_TURN_ACCEL and POINT_TURN_TOLERANCE.
- Client.go_to_pose() says whether the robot got there, and takes wait and timeout.
- Client.activate_behavior() and deactivate_behavior() take the dispatcher the behavior gets its events from, the
    client by default. The brain's behaviors get theirs from Brain.dispatcher, on the brain's own thread, and its
    handlers are there too: Brain.handlers is gone.


v0.9.20 (Sep 20, 2026)
----------------------

New features:
- A robot in real trouble now stays in trouble until it is helped. A critical need took its activity
    over and announced itself, and then had nothing else to offer, so the robot went back to its
    ordinary business as though nothing were wrong: the two behaviors the severe needs activities
    fall back on, DriveInDesperation and Wait, were never implemented and so never wanted to run.
    Both are now.

    BehaviorDriveInDesperation is one round of wandering and asking for help - a turn, a drive of the
    length minTimeToIdle and maxTimeToIdle bound, and then the request animation the configuration
    names - after which the round ends and the engine thinks again. Neither of Anki's two of them
    names the need it belongs to, so there is no need to watch; ending each round instead keeps the
    robot asking for as long as the need is critical and hands it back the moment the need is met.
    Read from the configuration: the two idle times, the motion profile's speed_mmps and
    pointTurnSpeed_rad_per_sec, and requestAnimTrigger. How far it turns is not in there - a random
    part of a half turn either way is what keeps the robot milling about rather than setting off in a
    straight line and driving off the table. useCubes is not read, for want of anything that sees
    cubes.

    BehaviorWait stands still. Anki's engine could take a behavior off the robot part way through, so
    a wait there could last until something else wanted the robot; this engine only looks for
    something to do once nothing is running, so it waits for BehaviorWait.DURATION and reports itself
    done. Nothing reaches it while DriveInDesperation can run, which is always.

Bug fixes:
- A behavior taken off the robot can no longer report itself done. One waiting on a timer or an
    animation - DriveOffCharger, and now DriveInDesperation - can have its callback run just after a
    reaction has taken its place, and the EvtBehaviorDone it posted then ended the reaction rather
    than itself. The client marks a behavior as off the robot before deactivating it, and done() and
    give_up() hold their tongue. DriveOffCharger has been exposed to this since v0.9.11 and runs at
    most three times a session; DriveInDesperation runs for as long as a need is critical, which is
    what brought it to light.


v0.9.19 (Sep 20, 2026)
----------------------

Bug fixes:
- Fixed the needs falling at a stale rate when more than one decay period had gone by at once.
    How fast a need falls depends on the bracket it is in, and several periods were applied together
    at the rate that held when the first of them started. The heartbeat asks thirty times a second,
    so a period is never missed while it is running - but it stopped running once already, in
    v0.9.13, and a caller stepping the needs itself has no such guarantee. Periods are applied one at
    a time now, the rate and the cross-need multipliers read afresh for each, which also makes
    stepping coarsely and finely agree exactly.


v0.9.18 (Sep 20, 2026)
----------------------

New features:
- The nurture needs. Cozmo has three - Repair, Energy and Play - which start full, fall for hours,
    and only something done to the robot puts back. They are not the mood: an emotion is a shove
    that decays to nothing in a couple of minutes, while a need is what turns a robot left to itself
    from merely idle into one that starts asking for something. Four of Anki's configuration files
    describe them and three are now read: the bounds, brackets and fullness wait from
    needs_config.json, the level dependent decay rates and cross-need multipliers from
    needs_decay_config.json, and all eighty-odd actions from needs_action_config.json.

    Left alone from a fresh start, Play reaches its warning bracket after 55 minutes and its critical
    one after 83, Energy after 99 and 204, Repair after ten hours and nineteen. Each need holds at
    full for twenty minutes first, and one drags on another: Repair between 0.03 and 0.3 makes Play
    fall twice as fast, which is the only cross-effect in the whole configuration.

    What that shows as: NothingToDo, PlayAlone, Hiking, Socialize, BuildPyramid and PlayWithHumans
    all list the five needs requests in their interlude chooser, so the robot starts slipping them
    between whatever else it is doing - the lower the need, the more often, from a graph read at the
    need's own level. Once a need is critical, two activities of their own take the robot over and
    announce it, with Repair outranking Energy so that a robot both broken and starving asks to be
    mended rather than fed.

    Three of the four activity strategies that were stubs are now evaluated. "Needs" and
    "SevereNeedTransition" read a condition the strategy states - InNeedsBracket or
    ExpressNeedsTransition - against the needs, with higherPriorityStrategyConfig letting one need
    stand aside for a more urgent one. "NeedBasedCooldown" reads how long to rest off a graph at a
    need's level: Singing rests 600 s with Play full and 1455 s with Play as low as it goes, so a
    bored robot sings less, not more. Only Spark, Pyramid and PlayWithHumans are left unevaluated.

    New: pycozmo.needs, behavior.BehaviorExpressNeeds, behavior.BehaviorPlayAnimOnNeedsChange,
    activity.NeedsStrategyConfig and Brain.apply_need_action().

    Putting a need back is not done here on its own, because on a real robot it was a thing the
    player did in the application: Feed is worth a third of Energy, and RepairHead, RepairLift and
    RepairTreads a third of Repair each. Brain.apply_need_action() is how an application offers them.
    Three actions are applied from here already - a fall, being laid on its side, and any behavior
    whose name is also an action, which is FistBump and PopAWheelie.

    Left out on purpose: "Wait", the behavior a severe-needs activity falls back on after asking for
    help, which holds the robot until the need is met. With DriveInDesperation not implemented there
    would be nothing between the announcement and sitting still forever, so the activity is left
    with nothing to offer and the engine moves on. needs_handlers_config.json, which describes the
    face glitching as Repair falls, is not read either.

Bug fixes:
- Fixed the off-board function list still saying two thirds of the animation audio plays and the
    WWise Vorbis files stay silent, which v0.9.17 changed.


v0.9.17 (Sep 20, 2026)
----------------------

New features:
- The WWise Vorbis sounds play. They are a third of what Cozmo's animations reach for and most of its
    voice, and they were silent because WWise does not store playable Vorbis: it strips the setup
    header's codebooks out and leaves 10 bit indices into a library that lives in its sound engine,
    which shipped inside the Cozmo application rather than with the robot's resources. There is not
    one codebook anywhere under cozmo_resources, so nothing in the resources alone could ever have
    decoded them.

    With a copy of the application they can be decoded, and tools/pycozmo_convert_audio.py does it
    once: it finds the library of 598 codebooks in the sound engine, rebuilds each file into a real
    Ogg Vorbis stream - identification and comment headers written afresh, codebooks widened back out
    of their packed form, the bits marking an audio packet and joining its windows worked out again,
    and an Ogg container built around the lot - and leaves a WAV per sound under
    util.get_converted_sound_dir(), which the audio library then plays in place of the file it cannot
    read. All 1987 files convert, in 159 s, for 255 MB, and every one comes out within 50 ms of the
    length its own header states. Animation sound goes from 66 % of triggers to 98 %; the 2 % left are
    events the sound banks do not resolve to a file at all, which is not a codec problem.

    Nothing from the application is copied: the codebooks are read while converting and never stored.
    The Vorbis decoding is left to ffmpeg, which is why this is a tool and not part of the library -
    PyCozmo gains no dependency from it.

    New: pycozmo.audiokinetic.bits, .ogg, .codebooks and .vorbis; util.get_converted_sound_dir() and
    audiolib.add_converted_sound().

Other changes:
- The audio library no longer keeps decoded samples. It answers whether a take can be played from the
    file's header instead of by decoding it, which is what choosing between an event's takes needs,
    and keeps only the encoded frames - capped now at FRAME_CACHE_SIZE, since Cozmo's sounds run to
    97 minutes once the converted Vorbis is counted and holding all of them decoded would not fit.
- Wem now keeps the format chunk's extension, where WWise describes a Vorbis stream.


v0.9.16 (Sep 20, 2026)
----------------------

Bug fixes:
- Fixed every animation frame being sent twice, which ran the animations at about half speed and
    chopped their sound into a stutter. play_anim_ppclip() turned keyframe times into frames by
    adding each gap to a running total that already counted the frame it had just sent, so a gap of
    one frame cost two: the keyframe's own, and an empty one after it. Measured over the 9100 clips
    in Anki's resources, an animation took 1.721 times as long as its own keyframes say it lasts;
    it now takes 1.0000 times, and no clip is off by more than a frame.

    The sound was the audible half. A keyframe lays one frame of audio every 33 ms, which are
    exactly the gaps that doubled, so an empty frame landed between every two frames of sound -
    verified on anim_bored_02, whose 40 frames of sound all came out alone between two silences. On
    a robot that is 15 Hz of chop; through the emulator the browser is handed half the sound it
    needs per unit of time and its buffer starves instead. None of the 17740 runs of sound in the
    resources is a single frame now, and the longest is 319 frames unbroken, 10.5 s. The bug arrived
    with the animation controller in 2019 and went unheard because nothing played audio until
    v0.9.15.

    A keyframe now belongs to the frame its time falls on. The 3 % of keyframes in the resources
    that do not sit on the 33 ms grid are rounded to their nearest frame rather than having their
    gaps rounded and added up, which is what keeps a long animation on time. Keyframes that then
    land on the same frame share it, as the robot's one slot per frame does, rather than being
    spread over consecutive frames, which would stretch the clip again: that costs a sound on 197
    of 325678 audio frames, across 48 of the 9100 clips, against the 35853 sounds already masked by
    a keyframe naming several events at once, which PyCozmo does not mix.

Other changes:
- Added robot.FRAME_MS, the 33 ms grid the animation resources are authored on, which anim.py and
    client.py both used as a literal.


v0.9.15 (Sep 20, 2026)
----------------------

New features:
- The animations have sound. An animation does not carry audio: it names a WWise event by the 32 bit
    identifier WWise hashed its name into, and working out what that event plays was a TODO left
    where the keyframe was read, untouched since 2019. 893 of the 993 animations now come with
    sound. Getting there takes three steps - the sound banks map an event to a container of takes and
    each take to a media file, the media file is decoded, and the samples are resampled to the
    22050 Hz the speaker runs at and U-law encoded into the 744 sample frames OutputAudio carries,
    one per animation frame. Against an emulated robot, seventy seconds of ordinary idling streamed
    578 audio frames, nineteen seconds of sound.
- pycozmo.audiokinetic.wem reads WEM files. WWise declares its ADPCM with Microsoft's format tag but
    does not use Microsoft's layout: there is no coefficient table in the format chunk and each block
    opens with four bytes of IMA state per channel rather than Microsoft's seven, which is why
    ffmpeg's decoder rejects these files outright. The layout was settled from the resources
    themselves - the byte rate implies exactly 64 samples per 36 byte mono block, which leaves four
    bytes of header and makes every remaining nibble a sample - and the nibble order was settled by
    measuring the decoded spectrum: taking the low nibble first leaves 0.112% of the energy above
    18 kHz against 0.976% the other way round, on 48 kHz audio that should have almost none. All 227
    ADPCM files decode to exactly the sample count their headers imply. The stereo interleave was
    settled the same way, four byte groups per channel against contiguous halves, 0.17% against
    1.22%.
- The WWise Vorbis files are reported rather than guessed at. WWise strips the Vorbis setup header
    out of its files and keeps the codebooks in its own sound engine, which shipped inside the Cozmo
    application: there is not one codebook sync pattern anywhere under cozmo_resources, in any of the
    banks or the 2214 media files. Those files cannot be decoded from what the robot came with, so
    wem reports them and the animation plays without them. Weighted by how often the animations
    trigger them, that is 34% of the audio events silent against 64% playing - the ADPCM side is the
    screen, the servos, the blinks; the Vorbis side is most of Cozmo's voice.
- The sound bank reader follows containers. An event points at a container of takes far more often
    than straight at a sound - 331 of the 380 events Cozmo's animations name do - and containers were
    skipped, so those events resolved to nothing. A container's child list does not sit at a fixed
    offset, NodeBaseParams being variable length, so it is found by what it has to look like: a count
    followed by exactly that many distinct object identifiers. That is unambiguous for 491 of the 514
    containers in Cozmo's bank, and resolving the events this way agrees with the file names WWise
    recorded in SoundbanksInfo.xml. Cozmo's own bank, the one holding those 380 events, is also the
    one bank the resources do not unpack, so it is read out of AudioAssets.zip.

Bug fixes:
- Fixed the U-law encoder producing noise. It negated the complement of the sign, exponent and
    mantissa instead of masking it, which is the same as adding one to the uncomplemented byte:
    fed a 440 Hz sine and decoded by ffmpeg, the samples came back with a correlation of -0.05
    against the input, where the corrected encoder gives +0.9999. Everything that has ever called
    play_audio() sent noise to the speaker. A byte of 0xFF also overflowed the bytearray it was
    stored into, which is how this surfaced: an animation crashed on it.
- Frames are padded with silence rather than noughts. A short final frame is filled out to 744
    samples, and now that the encoder complements properly a nought byte is very nearly full scale
    negative - it decodes to -32124 - so the padding clicked. U-law silence is 0xFF.
- The speaker is given a volume on connection. The robot comes up silent, nothing else set one, and
    an emulated robot confirmed it: the volume read nought while the audio frames arrived. Half range
    is a robot you can hear across a desk; applications can call set_volume() for something else.


v0.9.14 (Sep 20, 2026)
----------------------

Bug fixes:
- Fixed the procedural face crashing on a negative lid bend. The lid's crease is a chord whose
    bounding box is built from the signed `bend` clip parameter, and current Pillow rejects a box
    whose corners come out of order, where older Pillow silently normalised it. Eleven call sites in
    procedural_face.py build a box the same way, from bend, an eye corner radius, or a lid raised
    past fully open - all signed parameters read straight from Anki's clip data - and every one of
    them is fixed the same way, by sorting the corners before drawing. A rectangle, pieslice or chord
    is the same shape either way, so this changes nothing about what gets drawn. It surfaced on an
    actual robot: the heartbeat thread died mid-session playing anim_bored_02, silently, since Thread
    only puts an uncaught exception on stderr - and a dead heartbeat stops emotion decay, hiccups,
    and the activity engine along with it.


v0.9.13 (Sep 20, 2026)
----------------------

New features:
- The activity engine chooses what the robot does when nothing has happened to it. The brain only ever ran a behavior
    because a reaction trigger named one; between reactions it did nothing, and deactivate_behavior() carried a note
    asking whether a behavior should be chosen from the activity. Freeplay lists 25 sub-activities in priority order,
    and the first one that wants to run and has a behavior to offer now gets the robot. Against an emulated robot, the
    result over seventy seconds is the hiking intro, then the NothingToDo idle and bored animations, Hiking coming
    round again each time its fifteen second cooldown is up: eleven behaviors, no warnings, nothing spinning.
- An activity's strategy is read rather than reduced to its type name. "Simple" is the only one evaluated: it carries
    how long the activity may and should run, how long it rests afterwards, whether it starts in cooldown, and two
    conditions on the world - that the robot was set down on its treads in the last few seconds, and that the mood
    scores high enough. Socialize is the only activity in Anki's resources that gates on mood, scoring the Social
    emotion through a graph that gives 1.0 while Social is at or below 0.3 against a required 0.5, so the robot goes
    looking for company when it has not had any. The other six strategy types gate on a spark from the application,
    the nurture needs, a pyramid of cubes or a player asking for a game, none of which this library has; an activity
    carrying one never wants to run, which is what the robot does while nothing has sparked it and its needs are full.
- A behavior says whether it would take the robot. An unimplemented behavior class never does - activating one only
    logs that and reports it done, and the engine would offer it the robot again straight away - and an animation
    behavior wants the animations it names to be on disk. ReactToObstacle, the one behavior in the resources carrying
    a wantsToRunStrategyConfig, asks for ObstacleDetected and so holds back. Two more ask for something to have just
    happened: the hiking intro wants a quarter of a second since its activity was entered, the hiking wake-up a second
    since the robot drove off its charger.
- DriveOffCharger drives off the charger. It used to report itself done without moving, which is why the brain's
    start() carried a note to drive off if on the charger. The robot backs onto its charger, so leaving it means
    driving forward, over the contacts and then the extra distance the configuration asks for - 60 mm for
    DriveOffCharger, 45 for Hiking_DriveOffCharger - timed at 50 mm/s, the robot reporting no odometry a behavior
    could wait on. It gives the status a second to catch up before trying again, and gives up after three attempts:
    the resources say nothing about retrying, and a status stuck on the charger would otherwise drive the robot across
    the table. Each of the two behaviors counts its own attempts.

Bug fixes:
- Fixed a behavior's repetition penalty never wearing off. The graph was read at the number of times the behavior had
    run and subtracted from its score, which took the bored animations to nothing for good after two runs. The x axis
    is seconds since the behavior last ran, and the y axis the fraction of its score it has won back - the three
    hundreds and nine hundreds in the graphs are not repetition counts - so GuardDog scores nothing for five minutes
    and is whole again a quarter of an hour on, and the bored animations keep half their score for nine seconds. That
    is what the comment in nothingToDo.json asks for: "we set some repetition penalty so that we Idle normally after
    playing a bored-game sequence". A graph is read at its last node rather than extrapolated past it, since the
    lockout MeetCozmo_InteractWithFaces uses ends on two nodes sharing an x, which makes the extended line flat at
    nought rather than at one.
- Fixed BehaviorChooser.get_sorted_choices() on a "StrictPriority" chooser reading self.iteration, which nothing ever
    assigned outside reset(), and handing back the raw entries rather than behavior identifiers. Both branches return
    identifiers now, which is what it takes to look a behavior up.
- Fixed every score reaching nought being divided by a total of nought. The chooser reports having nothing to offer.
- Fixed Feeding's behaviors being read into a list nothing consulted. They sit under "universalChooser", the
    activity having no sub-activities to share them with, and are its chooser.

Other changes:
- Activity.strategy is an ActivityStrategy rather than the strategy type string, which is now strategy.type. The
    choosers and the sub-activities moved to the base class, each subclass having read its own; PyramidActivity keeps
    its setup and build choosers. BehaviorChooser.apply_repetition_penalty() is behavior_ran(), the scores are
    computed by get_scores() rather than held, and repetition_penaltys is spelled repetition_penalties.
- Brain.activity is the activity that holds the others, and Brain.sub_activity the one that has the robot. Three
    threads can activate a behavior now - the heartbeat looking for something to do, the reaction thread answering a
    trigger, and the client's dispatcher reporting a behavior done - so the brain guards the transitions with a lock.
    Stopping the brain gives the activity up as well as the behavior.


v0.9.12 (Sep 20, 2026)
----------------------

Bug fixes:
- Fixed the robot's screen freezing after an animation was cancelled. v0.9.9 moved the clearing of
    the playing flag behind the identifier check it added, so an end that was dropped left it set,
    and nothing redrew the procedural face - it is only drawn while no animation is playing. A
    behavior preempted mid-animation by one that plays no animation at all, such as
    ReactToReturnedToTreads, reaches this; v0.9.11 made it last longer, an unchanged image going out
    every 5 s rather than thirty times a second. The flag now goes whichever animation the robot
    reports ending, only the completion event staying behind the identifier.
- Fixed all but one of the behaviors a reaction trigger names being dropped on load. The map was
    read into a dictionary keyed by trigger, and Frustration appears in it twice, so
    ReactToFrustrationMinor was overwritten and the harsher reaction was the only one that trigger
    could run. The map holds a list per trigger now, and the choice is made when it fires.
- Fixed Brain.stop() leaving the robot in the brain's hands. The running behavior stayed active, so
    its animation kept playing and, since ReactToOnCharger gained its timers in v0.9.9, one could
    fire into a session being torn down. The brain's own event handlers stayed registered too, so a
    reaction posted afterwards queued up for a thread that no longer ran, and a behavior reporting
    itself done would have had the brain start another one.

Other changes:
- The frustration reaction is graded on how confident the robot is, which the resources have always
    asked for: the minor variant applies at or below -0.6 confidence, the major one at or below
    -0.9, and the minor one carries a 60 s cooldown. Grading it only became possible once the
    emotions held a value, which they did not before v0.9.9. Too confident for either and the
    mildest runs anyway, a trigger that fired being better answered than ignored. A reaction held
    back by its cooldown is skipped, and the trigger passes if every variant is.
- Hiccups come in bouts, as hiccupParams asks: five to ten of them, four and a half to eight seconds
    apart, then five to fifty-five minutes of quiet. One was posted every sixty seconds, which is
    both far too often and not the shape of it - often enough to cut into whatever was running, as
    it did to a charger sleep sequence while this was being measured.
- Brain.reaction_trigger_beahvior_map is spelled reaction_trigger_behavior_map, and holds a list of
    reactions per trigger rather than one. ReactionTrigger carries the confidence, the cooldown and
    the configuration entry it was read from. Nothing outside the brain read any of it.

Maintenance:
- Added tests for the reaction choice, the hiccup bouts and stopping the brain.

v0.9.11 (Sep 20, 2026)
----------------------

Other changes:
- The screen image is no longer sent thirty times a second when it has not changed. The robot keeps
    the last image and only blanks its screen after 30 s with nothing new, so an image identical to
    the one already displayed now goes out only every 5 s, to beat that deadline. Measured against
    an emulated robot, the packets sent now match the images that genuinely differ: five to nine a
    second where thirty went out before, the procedural face being redrawn on every frame but
    changing far less often than that. With the face off the screen is static, and one packet every
    five seconds replaces thirty a second. The screen is treated as unknown again whenever the
    animation controller starts, a robot just connected to being free to show anything.
    The 30 s figure is the one the code has always carried; it has not been checked against a real
    robot, hence a refresh six times inside it.
- pycozmo_app.py takes --robot-addr HOST[:PORT], to run the personality engine against an emulator
    rather than the fixed address of a real robot, and --no-face, which leaves the procedural face
    undrawn. Both were reachable only by writing a wrapper around the application and reassigning
    pycozmo.conn.ROBOT_ADDR, or patching connect(), before importing it.

Maintenance:
- Added a test module for pycozmo_app.py, which had none. The tools were untested altogether.

v0.9.10 (Sep 19, 2026)
----------------------

Bug fixes:
- Fixed EvtAnimationCompleted no longer reaching an animation the application started itself. The
    previous release matched the end of an animation against the identifier the client had recorded
    while playing one, so an animation started by sending StartAnimation directly - pycozmo being a
    protocol library, an ordinary thing to do - never completed, and whatever waited on it waited
    for good. The robot acknowledges every animation it starts, whoever started it, so its answer is
    now what the end is matched against. The identifier the client records is kept alongside, so an
    animation still completes on a robot that does not acknowledge a start. Caught by an integration
    suite run against an emulator of the robot, which exercises the whole stack and had not been run before v0.9.9
    went out.

v0.9.9 (Sep 19, 2026)
---------------------

Bug fixes:
- Fixed the end of one animation being taken for the end of another. EndAnimation carries no identifier, so the robot
    answers with the identifier of whatever was actually playing, and starting an animation cancels the one before it.
    Every interruption therefore produced an end for the old animation, dispatched as EvtAnimationCompleted after the
    new one had already been started, and whatever played next took that for the end of its own. A behavior
    interrupted mid-animation collapsed: against an emulated robot, a cliff reaction preempting a running behavior
    reported itself done 7 ms after starting instead of playing its eleven seconds, and the behavior resumed after it
    ran through two animations in 380 ms. Nothing exercised this before, because a reaction only ever interrupted an
    idle robot. The identifier now decides which end is whose, and cancelling drops the expectation altogether.
- Fixed the animation identifier never wrapping. It was incremented without bound, so StartAnimation refused it on
    the 255th animation of a session, its field being a uint8.
- Fixed the robot orientation being read from pose_angle_rad, which is the heading in the world frame, not the roll.
    Every turn of more than 23 degrees reported the robot as lying on a side: driving 49 degrees round on the spot,
    with the accelerometer reading dead flat throughout, produced four orientation changes and left the client
    believing the robot was on its right side. That is what the orientation reactions in the brain were commented out
    over. The accelerometer is the only signal that tells a roll apart from a turn; nose up and nose down stay on the
    pitch the robot reports, which is filtered on the robot. An orientation now also has to hold for half a second
    before it is accepted, the righting animations throwing the robot around enough to retrigger the reactions
    otherwise.
- Fixed three animation controller handlers taking no argument, while the status flag change events they are
    registered for are dispatched with the client and the new state. The TypeError propagated out of the dispatch and
    aborted the rest of the robot state handling, losing every flag change after the animating one, and the
    orientation update that follows them.

Other changes:
- The reaction behaviors are implemented. reactionTrigger_behavior_map.json maps 21 reaction triggers to a behavior,
    each with a behavior class of its own, and four of those classes existed; every other reaction logged "not
    implemented" and ended at once. Eighteen now play the animation group Anki gave them, taken from
    AnimationTriggerMap.json rather than guessed. MotorCalibration, RobotPlacedOnSlope and ReturnedToTreads have no
    animation anywhere in the resources, under their behavior ID, their trigger name, or anything close to either, so
    they keep the warning. ReactToOnCharger is not only an animation: its configuration gives the delay before the
    robot falls asleep on the charger and the delay before it lets the connection go, and it ends early if the robot
    is taken off the charger.
- BehaviorPlayAnim plays the whole sequence of animation triggers rather than only the first, and drops a trigger the
    resources do not define instead of waiting forever for a completion that can never arrive.
- Animations are chosen for the current head angle. 281 of the 1047 animation group members declare the band they
    were authored for, and 43 of the 507 groups hold one animation per band with the angle baked in, so drawing at
    random made the head jump. Per-animation cooldowns, which 43 members declare, are honoured too. Either filter
    gives way rather than leaving nothing to play. Mood is still ignored, every member carrying Mood "Default".
- The mood engine works. The brain loaded the seven emotion types and the 34 emotion events and decayed them on every
    heartbeat, but nothing ever shifted one: EmotionType held no value and its update() was a stub. An emotion now
    holds a value between -1 and 1 that events shift and that decays along its graph from mood_config.json. What
    posts an event is taken from the data: a reaction trigger posts the event of its own name, and behaviors post
    theirs through the new EvtEmotionEvent. The mood is reported on a logger of its own, pycozmo.emotion. It does not
    yet steer which behavior or activity is chosen.
- Reactions marked shouldResumeLast put back the behavior they interrupted, which was a TODO. Three of the 21
    triggers are marked with it - CliffDetected, MotorCalibration and UnexpectedMovement, exactly the set the
    TooManyResumesCliffOrMovement emotion event is named after.
- AnimationGroup.member_probabilities is gone, weights now being normalised over the members actually in the running,
    and AnimationGroup.choose_member() takes an optional head angle. This is the only part of the public surface that
    changed shape.
- The README documents how to run Cozmo's own behavior, which was reachable only by reading brain.py, including that
    the reaction, behavior, animation and emotion loggers sit at the robot log level and so default to silence.

Maintenance:
- Added test modules for the behaviors, the brain, the emotions, the orientation, the animation groups and the
    animation completions, none of which had any. The suite goes from 213 to 305 tests.

v0.9.8 (Sep 18, 2026)
---------------------

Bug fixes:
- Fixed every packet of a full frame being lost. A frame flushed because the next packet no longer fitted
    advertised that next packet's sequence number while carrying only the packets before it. The peer numbers the
    packets it decodes from first_seq and checks the count against the advertised sequence, so it rejected the
    whole frame and logged a decode failure; the sender never found out, and the resent copies were rejected the
    same way. Any burst large enough to fill a frame hit this, which a run of large packets such as DisplayImage
    does in a handful of messages. Reported earlier as needing a robot to confirm, this turned out to be
    reproducible on the loopback interface as soon as a server could answer as a robot.

Other changes:
- Connection(server=True) can now answer as a robot, not only echo as an engine. It sends ROBOT frames rather
    than ENGINE ones, and sends out-of-band packets - RobotState, ImageChunk, AnimationState, ObjectAvailable -
    outside the send window, where they belong. Previously those frames advertised a sequence their packets did
    not consume and the peer discarded them, so the server side could not emulate a robot at all. Pings echoed by
    the server now go out as PING frames instead of relying on a special case in the decoder.
- The package now ships a py.typed marker. It is fully annotated and the type checker reports no issue on it, but
    dependent projects saw no types and had to silence the import.

v0.9.7 (Sep 18, 2026)
---------------------

- Fixed the build failing on Python 3.12, 3.13 and 3.14. The type checker had only ever been run against 3.11, the
    version its configuration declares, while the build matrix covers four. A suppression needed on 3.11 is
    unnecessary from 3.12 on, where the stubs recognise numpy arrays as buffers, and unused suppressions are
    errors. The array is now converted explicitly, which no version objects to. All four are verified.
- Raised test coverage from 63% to 67%, concentrating on what this fork changed and had checked only by hand.
    camera.py went from 21% to 97% and the JSON loader and the packet filter are now fully covered. New tests cover
    the procedural face renderer, the camera frame assembly, the logging setup, the animation queue and the frame
    rate timer.
- Pinned coverage and documented how to run it.

v0.9.6 (Sep 18, 2026)
---------------------

Bug fixes:
- Fixed Objective reading its completion maximum from the minimum. The two conversions are copies of one another
    and the second was not fully edited, so an objective declaring a range stored the bottom of it as both ends,
    and one with a maximum but no minimum reached int(None).
- Fixed an unnamed Color holding the NoneType class as its name. The default was written as the typing form
    Optional[None], which evaluates to that class, and a class is truthy, so testing a color's name answered the
    opposite of what it should. The module's own predefined colors were unaffected.
- Fixed the lift height keyframe crashing on animation clips that omit heightVariability_mm. Its sibling, the head
    angle keyframe, already defaulted the equivalent field to zero.
- Image.NEAREST and Image.FLIP_LEFT_RIGHT are now spelled through Image.Resampling and Image.Transpose. Both
    survive in Pillow 12 only as aliases, and Image.ANTIALIAS from the same group was removed in Pillow 10.
- Corrected the animation queue, which declared that it returns bytes where it returns packets, and several other
    signatures that described neither what they took nor what they gave back.

Maintenance:
- The type checker now passes on every file in the tree and a failure fails the build. It was advisory because the
    project could not pass it; the backlog stood at 556 reports when this fork started. The eleven that remain are
    marked in place with the reason, and unused suppressions are reported.
- The animation encoder tests assert which kind of keyframe a round trip produced. They previously read fields off
    whatever came back, so a test for one keyframe type passed just as well on another whose field names matched.
- Added test modules for util, lights, activity and the event dispatcher, none of which had any.

v0.9.5 (Sep 17, 2026)
---------------------

- Fixed comparing an Angle or a sound bank FileInfo against another type raising instead of answering. Both
    narrowed the argument of __eq__ to their own type and reached straight for its attributes. It matters most for
    Angle, which reaches application code through Client.head_angle and Client.pose_pitch, where comparing against
    None, or looking one up in a mixed container, is ordinary.
- Fixed an empty <PrefetchSize/> element crashing the SoundbanksInfo reader, and made it require the Language, Name
    and ObjectPath attributes rather than storing the string "None" when they are missing.
- Corrected find_file(), hex_load() and ImageDecoder.decode(), which returned something other than what they
    declared.
- Added a test module for util, which had none.

v0.9.4 (Sep 17, 2026)
---------------------

- Fixed the send thread dying when the server side has no client connected. The receiver address is unset until a
    client connects and again after a reset; sending then raised TypeError, which the surrounding handler does not
    catch. Such a frame is now discarded, as one that fails to send already was.
- Corrected Filter.filter(), which declared an int parameter while its body tests the argument against None and
    both of its callers pass an optional packet id.
- Widened the log level parameters of setup_basic_logging() to accept numbers as well as names, which the body
    already produced when reading them from the environment.
- Typed the connection layer, reachable from application code through Client.conn, and the remaining untyped parts
    of the animation encoder.
- Declared trigger_time_ms on the animation keyframe base class. All ten keyframe types carry it, and both the
    encoder and its tests read it off the base.

v0.9.3 (Sep 17, 2026)
---------------------

- Made the internal logger imports unambiguous. "from . import logger" names both the logger module and the Logger
    object the package rebinds over it. It resolves to the Logger at run time, so nothing about the emitted records
    changes, but it left every logging call in the package unverifiable by a type checker. Two modules already
    imported from the module directly; the other ten now do too.

v0.9.2 (Sep 17, 2026)
---------------------

- Fixed Client.wait_for() timing out on any event that carries a payload. Client shadowed the working version from
    Dispatcher with a copy whose handler accepted a single argument, so waiting for the next camera frame or for an
    orientation change raised Timeout although the event had fired, while the receive loop took a TypeError.
    Waiting for a camera frame could not have worked.
- Typed the Client public API: its attributes, the light and motion commands and the packet handlers. A caller now
    gets Client from connect(), sees which robot fields are optional until the robot reports them, and is told when
    it passes something that is not a LightState to the backpack lights.
- Added a test module for the event dispatcher, which had none.

v0.9.1 (Sep 17, 2026)
---------------------

A maintenance release. No API changes; every item below is a fix to code, to a
signature that misdescribed the code, or to packaging.

- Corrected three functions that lied about what they return: find_file(), which returns None when the file is not
    found, DecayGraph.get_line_parameters(), which returns a pair rather than a single value, and the frame rate
    timer's start time, which is also now tested for absence rather than for falsiness.
- Made the SoundbanksInfo reader raise its documented AudioKineticFormatError on files missing a required
    attribute or element, instead of letting TypeError and AttributeError escape.
- Qualified the documentation link, which points at upstream v0.8.0 and does not describe this fork, and added the
    command that builds the documentation from a checkout.
- Fixed the return annotations of five generator functions, including connect(), the documented entry point, whose
    signature prevented type checkers from resolving `with pycozmo.connect() as cli:` in calling code.
- Fixed the drawing context annotations in the procedural face renderers, which named the PIL.ImageDraw module
    where its class was meant. Same defect as upstream issue #68, on call sites that fix missed.
- Made Activity.get_sorted_choices() honour its declared return type.
- Declared the build system in pyproject.toml, per PEP 518, and dropped the license classifier that setuptools
    deprecates. Package metadata stays in setup.py so the command line tools keep their documented names.
- Re-enabled test_send_30, disabled as intermittently failing since 2020. The transport was not at fault: the test
    stopped waiting one packet early, then asserted that all of them had arrived. The suite now has no skipped tests.
- Pointed the README at the public GitHub mirror and described how that mirror works, so that clone URLs in the
    documentation are ones a reader can actually use.


v0.9.0 (Sep 17, 2026)
---------------------

First release of this fork. Its version number continues the upstream sequence: upstream stopped at v0.8.0 and is
not expected to publish again. The minor version is raised rather than the patch version because the supported
Python range changed, which is a breaking change for anyone still on 3.6 to 3.10.

Python 3.12 and 3.13 support:
- Fixed `import pycozmo` failing with `ModuleNotFoundError: No module named 'chunk'` on Python 3.13. The `chunk`
    standard library module was removed by PEP 594. A minimal replacement is now vendored in the audiokinetic
    package. The failure affected every user, not only those reading sound banks (upstream issues #67 and #69).
- Fixed the setup script failing on Python 3.12 and newer, where `distutils` has been removed.

Bug fixes:
- Fixed `Client.last_image_timestamp` always being zero. The timestamp was read from the first chunk of a frame,
    where the robot leaves it unset (partial cherry-pick of upstream pull request #55, thanks to ADebor).
- Fixed image type annotations naming the `PIL.Image` module where the image class was meant, which type checkers
    reject and which showed the wrong type in the generated API documentation (upstream issue #68).
- Fixed the FlatBuffers deprecation warnings raised by passing an element count to `Builder.EndVector()`.

Maintenance:
- Raised the minimum supported Python version to 3.11. Support for 3.6 through 3.10 is dropped; all of those are
    out of support or nearly so, and holding on to them blocked pinning dependencies to maintained releases.
- Pinned dependency versions. The setup script declares bounded ranges, the requirements files declare the exact
    versions each revision is tested against.
- Fixed the mypy configuration, which refused to start at all because it declared Python 3.6, and which then
    descended into the virtualenv and into build artifacts.
- Replaced the remaining type comments with variable annotations, which also cleared the last flake8 warnings.

Infrastructure:
- Added a Forgejo Actions workflow running flake8, mypy and the unit tests on Python 3.11 through 3.14. No step
    requires a robot.
- Updated the inherited GitHub workflow, which tested Python versions no longer available on its runners using
    retired action versions.
- Documented the fork, its scope and how to run the checks, in the README.


Upstream
========

v0.8.0 (Nov 12, 2020)
---------------------
- New animation controller that synchronizes animations, audio playback, and image displaying.
- Procedural face generation to bring the roboto to life.
- Loading of Cozmo resource files - activities, behaviors, emotions, light animations
    (thanks to Aitor Miguel Blanco / gimait)
- pycozmo.audiokinetic module for working with Audiokinetic WWise SoundBank files.
- New tool for managing Cozmo resources - pycozmo_resources.py .
- Initial Cozmo application with rudimentary reactions and behaviors - pycozmo_app.py .
- Cozmo protocol client robustness improvements.
- CLAD encoding optimizations.
- Cliff detection and procedural face rendering improvements (thanks to Aitor Miguel Blanco / gimait)
- Replaced pycozmo.run_program() with pycozmo.connect() context manager. 
- Renamed the NextFrame packet to OutputSilence to better describe its function.
- Dropped support for Python 3.5 and added support for Python 3.9.
- Bug fixes and documentation improvements.

v0.7.0 (Sep 26, 2020)
---------------------
- Full robot audio support and a new AudioManager class (thanks to Aitor Miguel Blanco / gimait).  
- Cozmo protocol client robustness improvements (thanks to Aitor Miguel Blanco / gimait):
    - frame retransmission
    - transmission of multiple packets in a single frame
- Procedural face rendering improvements (thanks to Catherine Chambers / ca2-chambers).
- Added support for robot firmware debug message decoding.
- Added a new video.py example.
- Added a tool for over-the-air firmware updates - pycozmo_update.py (thanks to Einfari).
- Added hardware version description.
- Bug fixes and documentation improvements.

v0.6.0 (Jan 2, 2020)
--------------------
- Improved localization - SetOrigin and SyncTime commands and pose (position and orientation) interpretation.
- Added new path tracking commands (AppendPath*, ExecutePath, etc.) and examples (path.py, go_to_pose.py). 
- Added support for drawing procedural faces (thanks to Pedro Tiago Pereira / ppedro74).
- Added support for reading and writing animations in FlatBuffers (.bin) and JSON format.
- Added a new tool for examining and manipulating animation files - pycozmo_anim.py .
- Added commands for working with cube/object accelerometers - StreamObjectAccel, ObjectAccel.
- Improved function description. 
- Bug fixes and documentation improvements.

v0.5.0 (Oct 12, 2019)
---------------------
- Added initial client API.
- Separated low-level Cozmo connection handling into a new ClientConnection class.
- Improved the ImageDecoder class and added a new ImageEncoder class for handling Cozmo protocol image encoding.
- Added new examples for displaying images from files and drawing on Cozmo's OLED display.
- New protocol commands: EnableBodyACC, EnableAnimationState, AnimHead, AnimLift, AnimBackpackLights, AnimBody,
    StartAnimation, EndAnimation, AnimationStarted, AnimationEnded, DebugData.
- Initial support for Cozmo animations in FlatBuffer .bin files.
- Improved filtering through packet groups for pycozmo_dump.py and pycozmo_replay.py .
- Added type hints in the protocol generator.
- Bug fixes and documentation improvements.

v0.4.0 (Sep 13, 2019)
---------------------
- New commands: Enable, TurnInPlace, DriveStraight, ButtonPressed, HardwareInfo, BodyInfo, EnableColorImages,
    EnableStopOnCliff, NvStorageOp, NvStorageOpResult, FirmwareUpdate, FirmwareUpdateResult.
- New events: AnimationState, ObjectAvailable, ImageIMUData.
- New examples: cube_lights.py, charger_lights.py, cube_light_animation.py.
- Improved handling of 0x04 frames
- Added support for Int8, Int32, and enumeration packet fields.
- Improved robot state access.
- Added object availability and animation state access.
- Added initial pycozmo_replay.py tool for replaying PCAP files to Cozmo.
- Added OLED display initial image encoder code. 
- Added initial function description.

v0.3.0 (Sep 1, 2019)
--------------------
- Camera control and image reconstruction commands.
- Initial robot state commands (coordinates, orientation, track speed, battery voltage).
- Cube control commands.
- Fall detection commands.
- Audio volume control command.
- Firmware signature identification commands.
- Improved logging control.
- Python 3.5 compatibility fixes (thanks to Cyke).

v0.2.0 (Aug 25, 2019)
---------------------
- Backpack light control commands and example.
- Raw display control commands and example.
- Audio output commands and example.

v0.1.0 (Aug 15, 2019)
---------------------
- Initial release.
