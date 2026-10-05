
Cozmo Off-Board Functions
=========================

Most of what makes Cozmo Cozmo did not run on the robot. The robot plays animations, drives its motors, follows paths
and streams its camera; everything else - choosing what to do, recognizing cubes and faces, keeping track of where
things are, speaking - ran in the Cozmo application, on the phone, in Anki's engine. PyCozmo reimplements that engine
from the application's resources, which the engine read as data.

This document describes those resources, and for each off-board function what the application did, what PyCozmo does
and what is missing. [own_behavior.md](own_behavior.md) tells how the robot behaves with what is there, and
[sound.md](sound.md), [vision.md](vision.md) and [cubes_and_games.md](cubes_and_games.md) give the details of the rest.


Resources
---------

`tools/pycozmo_resources.py download` fetches the application's resources into `~/.pycozmo/assets/`:

```
cozmo_resources/
    assets/
        animationGroupMaps/      animation triggers -> animation groups
        animationGroups/         groups of animations with the same purpose, by head angle, with cooldowns
        animations/              animations, FlatBuffers .bin files
        cubeAnimationGroupMaps/  cube light triggers -> cube light animations
        faceAnimations/          image sequences some animations show on the screen
        RewardedActions/
    config/
        engine/
            behaviorSystem/
                activities/      freeplay activities and sparks (tricks asked for in the application)
                behaviors/       behavior configurations
                reactionTrigger_behavior_map.json
            emotionevents/       what events do to the mood
            lights/
                backpackLights/
                cubeLights/      cube light animations
            mood_config.json, needs_*.json
            vision_config.json   motion detection and face detection parameters
            cozmo_mprim.json     motion primitives for path planning
            game_request_weights.json, do_a_trick_weights.json
            firmware/            robot firmware images
    sound/                       Wwise sound banks (.bnk) and sounds (.wem)
    tts/Voices/                  text-to-speech voices: English, French, German, Japanese
```


Animations
----------

Animations are series of keyframes - body, lift and head movement, face images, backpack lights, sound - stored as
[FlatBuffers](https://google.github.io/flatbuffers/), as `cozmo_anim.fbs` declares. Anki's
[animation pipeline talk](https://www.gdcvault.com/play/1024488/Cozmo-Animation-Pipeline-for-a) gives background.

PyCozmo plays them: `Client.play_anim()` and `play_anim_group()`, which resolves an animation trigger through the
group maps, picks the animation for the current head angle band - 43 groups have one per band - and honours per
animation cooldowns. Frames go out as the robot plays them, paced by its animation buffer: see
[functions.md](functions.md#animations).

Face images are generated procedurally from 43 parameters - 5 for the face and 19 for each eye - and PyCozmo does the
same between animations (`pycozmo.procedural_face`). The `faceAnimations` image sequences are shown too.


Audio
-----

Sounds are Wwise sound banks and `.wem` files; animations and behaviors name Wwise events and switches. PyCozmo maps
events to sounds and decodes the PCM and ADPCM ones; the Vorbis ones need the codebooks from the application, which
`tools/pycozmo_convert_audio.py` uses to convert them. Two thirds of what the animations trigger plays as downloaded,
98% once converted: see [sound.md](sound.md).

**Songs**: the 39 `Singing` behaviors name a Wwise switch - `Cozmo_Sings_80Bpm` / `Cozmo_Sings_Danny_Boy` - rather
than an event; the singing animation's event plays a music switch, which plays the song the switch picks: a MIDI
track, whose notes go to an instrument of sung notes. `pycozmo.songs` reads all of that out of Cozmo's sound bank and
renders the song, and the song stops where the animation's stop event says, as Wwise stopped it. What Wwise worked
out at run time is PyCozmo's reading of the bank: the held note is taken to fall silent as it is released, and the
vibrato the instrument has is left out - see the module. The sung notes are Vorbis, so they play once converted.

Missing:
- **Text-to-speech.** The `tts/Voices` voices are Acapela's, and the engine that speaks with them is native code in
  the application, not a resource. Cozmo saying names, and the SDK's `say_text()`, need another speech engine.


Cube lights
-----------

Forty cube light animations, by trigger: patterns of on and off colours, periods and fades per light, rotating
round the cube or not, in steps with durations. `pycozmo.cube_lights` loads them and `Client.cubes.play_lights()`
shows them. The brain lights connected cubes as the application did: `Connected`, a dim cyan breath every five
seconds, and `Visible`, a steady cyan while the robot sees the cube. Quick Tap shows its own colours, and
`speedTapWin` and `speedTapLose` after each hand; the others serve games and tricks that are not implemented yet. How
long a light frame lasts was measured on a robot: 33.3 ms, 30 frames a second.


Emotions and needs
------------------

The mood engine keeps seven emotions, each decaying on its own schedule and moved by emotion events. The nurture
needs - Energy, Repair, Play - fall over hours and drive the requests and activities that read them. Both work: see
[What the robot needs](own_behavior.md#what-the-robot-needs). What raised the needs back was the application: feeding
with a shaken cube, a repair minigame, playing. `Brain.apply_need_action()` takes those actions; nothing performs
them yet.


Behaviors, reactions and activities
-----------------------------------

Behaviors are small programs over the robot's API, configured in `behaviors/`. There are 178 of them in 76 classes,
5 of which are factory and development tests. Reactions map 21 triggers to behaviors; activities choose what the
robot does when nothing has happened, in priority order: 14 sparks, which the application asked for, 3 severe-need
activities, and the freeplay ones.

PyCozmo implements 35 classes, 122 behaviors: the animation players, the reactions to being picked up, shaken, put on
its side or on the charger, cliffs, cube moves, driving off the charger, pouncing on motion, expressing needs,
waiting, picking cubes up, putting them down, stacking them, working out with one, rolling one back upright, popping
wheelies, singing, dancing, and asking for a game. Eleven of the reaction triggers are raised, a twelfth once faces
are found. The activity engine runs freeplay as Anki's configuration says, with its scores, cooldowns and mood gates,
and honours the unlock a behavior needs: `pycozmo.unlocks` reads those of a new robot and those the needs levels
reward; nothing keeps a progression, so the robot is taken to have them all.

Missing classes, by what they need:

| Needs | Classes |
|---|---|
| Knocking a stack over: recorded on a robot, Anki's own behavior gave it up, and left nothing to go by | `KnockOverCubes` |
| More handling of cubes | `RespondPossiblyRoll`, `CheckForStackAtInterval`, `CantHandleTallStack`, `ReactToStackOfCubes`, `BuildPyramid`, `BuildPyramidBase`, `ReactToPyramid`, `PyramidThankYou`, `BringCubeToBeacon`, `ThinkAboutBeacons`, `GuardDog`, `Bouncer`, `FeedingSearchForCube`, `FeedingEat`, `OnboardingShowCube` |
| Faces | `FindFaces`, `SearchForFace`, `DriveToFace`, `InteractWithFaces`, `PeekABoo`, `PlayAnimWithFace`, `LookForFaceAndCube`, `EnrollFace`, `RespondToRenameFace` |
| A map of the surroundings | `ExploreLookAroundInPlace`, `ExploreVisitPossibleMarker`, `VisitInterestingEdge`, `LookInPlaceMemoryMap` |
| The application, whose code some tricks were: their moves are not in the resources | `EarnedSparks`, `OnConfigSeen`, `FireTruckAlarm` |
| A laser pointer | `TrackLaser` |
| An animation the resources lack | `ReactToMotorCalibration`, `ReactToPlacedOnSlope`, `ReactToReturnedToTreads` |


Vision
------

The robot streams its camera; the engine looked for markers, faces, pets, motion and a laser dot in it.

Done:
- **Camera calibration.** Every robot got a lens calibration in the factory, in NV storage;
  `Client.read_camera_calibration()` reads it, and it is exactly what Anki's engine uses. Calibrating anew is not done.
- **Motion detection**, in the image with Anki's peripheral regions and on the ground: `pycozmo.motion_detection`.
- **Cube markers**: `pycozmo.marker_detection` finds them, tells the three cubes apart - their sides carry the symbol
  either way round - and places them from their 25 mm size. Against Anki's engine, through its SDK, it named 655 of
  656 frames the same and placed the cubes within 2%. Close to, where the cube's black corners touch a frame, it is
  found from the hole inside it: 130 more of the cubes Anki saw over 4092 images of two robots.

- **The charger's marker**: `pycozmo.charger_detection` finds it - a dark ring, 24 mm wide and 17.5 high, a battery
  drawn in it - and places it. Anki's engine has no drawing of it in its resources, so the drawing is from the
  robot's own camera images. See [Vision](vision.md#the-chargers-marker).
- **Going back to the charger**: `pycozmo.charger_handling` looks for the marker, drives round the charger if it is
  behind it, stands 20 cm in front, turns round and backs on. The version of the application this follows never did
  it: the robot was put on its charger by hand.

Open:
- **Where the camera points.** The robot's reported pitch, flat on a table, drifts from one session to the next by
  about a degree, which moves what is placed on the ground by a few percent. A cube sitting on the table, whose
  centre is 22.5 mm up, could correct it.
- **Far in the dark.** In a dim room, a cube 28 cm away was not found: its frame is some 27 pixels wide, the light
  margin inside it is lost in the blur, and the frame and the symbol make one dark patch. Taking such patches in
  too found nothing more in the images of both robots.

- **Faces**: `pycozmo.face_detection` finds them with OpenCV's YuNet and tells them apart with SFace; OpenCV is the
  `faces` extra of a source install, or `opencv-python-headless`, and `pycozmo_faces.py download` fetches the two
  models. `pycozmo.faces` keeps track of the faces seen, places them in the world from the distance between their eyes,
  and knows people by name once they are enrolled, from features kept in the user's own directory. Not yet tried on a
  robot, nor on faces of people.

Missing:
- **Facial expressions**, and gaze, smile and blink: Anki's engine measured them, and no model here does.
- **Pets.**
- **The SDK's custom markers.**
- **The laser dot** that `TrackLaser` follows.
- **A memory map** of what is where: seen ground, edges, obstacles, cubes.


Cubes
-----

The robot talks to its Light Cubes over Bluetooth LE. `Client.cubes` keeps the three: which of each kind the robot
hears, connects and drops, their taps, moves and up axis, and where the camera last saw them, in the robot's world
frame. The brain connects one of each kind, lights them, acknowledges a cube seen for the first time or where it was
moved to, and reacts to one moved in its sight - not to one it is handling or playing with, which is marked in use,
nor to the one in its lift. A cube moved by someone else is no longer taken to be where it was seen.


Moving and handling
-------------------

The robot follows paths made of lines, arcs and turns in place, and PyCozmo drives it along them:
`Client.go_to_pose()`, `turn_in_place()`, `drive_straight()` and, for any path, `execute_path()`, which report
whether the robot got there - a cliff interrupts a path when the robot stops at cliffs, which the brain turns on.
They go at the speeds of Anki's engine's default path motion profile: 100 mm/s, and turns in place at 2 rad/s,
which the robot takes in rad/s whatever the protocol's field names say. Anki's engine planned those paths with the
motion primitives in `cozmo_mprim.json`, around the obstacles in its map, and docked with cubes by their markers.

`pycozmo.cube_handling` handles the cubes as Anki's engine was seen doing it, through the SDK, on a robot - 30 times
a second, its pose, lift, head and wheels, and where it placed the cube. It finds a cube, looking round for it if need
be, goes to stand some 15 cm from the side it saw and has a look, then docks with its head down at -17 degrees,
looking at the marker again on the way. From there it makes the manoeuvre's own moves:

| Manoeuvre | Cube's centre ahead of the robot's origin | Then |
|---|---|---|
| Picking up | 47 mm | The lift rises while the robot creeps on 8 mm at 15 mm/s: the fork slides under as it goes up |
| Putting down | 52.5 mm, where it is set down | The lift comes down, the robot backs off 30 mm |
| Setting on another | 38.5 mm, the lift up | The lift comes down to 76 mm only, which lets go; the robot backs off 55 mm |
| Rolling | 34 mm, the lift up | The fork comes down on the top edge at 74 mm, then all the way down as the robot backs off at 55 mm/s: the cube tips over towards it |
| Popping a wheelie | 32.5 mm, the lift up | The lift slams down while the robot drives on at 150 mm/s: it ends up on its back, at some 74 degrees |

The distances are Anki's, 2.5 mm longer: PyCozmo places a cube that much further than Anki's engine did on the same
images. The robot does not stop at cliffs while it rolls a cube or pops a wheelie, which tip it up, as Anki's engine
did not. A cube is taken to be in the fork if the cube says it moved or the robot says it carries one, and it is not
still seen on the ground ahead. The workout's put-down, which an animation makes, lowers the lift, pushes the cube on
and backs off: the cube is followed there.

A cube's accelerometer says which of its sides is up, not which way its top points on the ground, which Anki's engine
read off its markers: they differ from one side to another. `RollBlock` rolls a cube lying on its side back upright
from where the robot stands. When a roll takes the cube from one side to another, its top points to the robot's left
or right, and the two sides that were up tell which: the robot goes round to the cube's bottom and rolls it from there.

Before this, on a robot, PyCozmo's own docking picked a cube up twice out of two, and once went to set a cube on
another without it in the fork. The handling as it now is, from the recordings, is not yet tried on a robot.

Missing: planning around obstacles, and knocking a stack over.


Games
-----

`RequestGameSimple` behaviors ask the player for a game: Quick Tap, Memory Match, Keep Away, and Cozmo performing a
trick. The games themselves were the application's code, not resources: their animations and cube lights are there,
not their rules.

`pycozmo.quick_tap` plays Quick Tap, by PyCozmo's reading of the game: the cubes light up, the same colour and the
first to tap wins the point, different colours and whoever taps loses it; five points a round, two rounds the game.
The robot sits at its cube, the lift over it, and taps it with Anki's animations. `examples/quick_tap.py` plays a
game. `pycozmo.memory_match` plays Memory Match: the cubes light up in a pattern that grows by one each round, the
player repeats it by tapping them and Cozmo by turning to point at each, and whoever gets it wrong first loses;
`examples/memory_match.py`. In freeplay the robot asks for either, through `PlayWithHumans`; the player answers on a
cube rather than on a phone - a tap takes the game up - and the robot asks from where it is, without looking for a
face or bringing a cube over. `pycozmo.keep_away` plays Keep Away: Cozmo moves to where its pounce reaches the
player's cube, raises its lift and pounces, or pretends to; the lift tells whether the pounce caught the cube - it
stops on the cube, at 52 to 70 mm, and goes to the bottom, at 27 to 35, when the cube has been pulled away; the cube's
own tap is no judge, the lift slamming on the floor beside it makes one - and a cube moved while Cozmo only waited or
pretended is a flinch, and Cozmo's point; `examples/keep_away.py`.

The games' moves were measured on a robot, and hands of Quick Tap, Memory Match and Keep Away played on one, a whole
game of none: Quick Tap's tap comes down on a cube with the robot 42 mm from its centre, and the robot takes some 0.4 s
to start the animation, which the game allows for; Keep Away's pounces carried the robot 39 to 72 mm on as the lift
came down, and caught a cube 88 mm ahead, though not every pounce reaches that far; Memory Match's turns were 20 to
27 degrees small and 46 big, and the robot points straight ahead at a cube less than 12 degrees off, with a big turn
from 35. A cube is not a good judge of a tap: on a robot it told 8 of 10 or 11 taps, and sometimes told one twice, 0.07
to 0.16 s apart, which Memory Match takes for one.


Compared with the Cozmo SDK
---------------------------

The SDK drove Anki's engine through the application. What its robot and world offered, and where PyCozmo stands:

| SDK | PyCozmo |
|---|---|
| Robot state, IMU, cliff, charger, pose, head and lift | Yes: `Client` and its events |
| `drive_wheel_motors`, `move_head`, `move_lift`, `set_head_angle`, `set_lift_height`, `stop_all_motors` | Yes |
| `turn_in_place`, `drive_straight` | Yes, along paths, reporting whether the robot got there |
| `go_to_pose` | Yes, without obstacle avoidance |
| `go_to_object`, `dock_with_cube`, `pickup_object`, `place_on_object`, `place_object_on_ground_here` | Yes, for cubes: `pycozmo.cube_handling` |
| `roll_cube`, `pop_a_wheelie` | Yes: `pycozmo.cube_handling` |
| `play_anim`, `play_anim_trigger`, idle animations | Animations and triggers yes; idle animations no |
| `play_audio`, `set_robot_volume` | Yes |
| `say_text` | No |
| `play_song` | No |
| Backpack lights, head light, OLED face image | Yes |
| Camera images, colour, exposure | Yes |
| `enable_stop_on_cliff` | Yes, and the brain turns it on, as the application did |
| Light cubes: connection, lights, taps, moves | Yes: `Client.cubes` |
| Light cubes: pose | Yes, from their markers |
| Charger pose | Yes, from its marker, and from where the robot stood on it: `Client.charger`, `pycozmo.charger_detection` |
| Going back to the charger | Yes, which Anki's application never did: `pycozmo.charger_handling` |
| Custom objects | No |
| Faces: seeing, naming, where they are | Yes, with OpenCV: `Client.faces` |
| Facial expressions, pets | No |
| Navigation memory map | No |
| `start_behavior`: `FindFaces`, `KnockOverCubes`, `LookAroundInPlace`, `PounceOnMotion`, `RollBlock`, `StackBlocks` | `PounceOnMotion`, `RollBlock` and `StackBlocks` |
| Freeplay | Yes: `pycozmo.brain` |
| Needs levels | Yes |


Compared with the Cozmo application
-----------------------------------

| Application | PyCozmo |
|---|---|
| Freeplay: waking up, reactions, bored and idle animations, pouncing on motion, hiking | Yes |
| Freeplay with cubes: seeing them, lighting them, reacting to them | Yes |
| Freeplay with cubes: lifting, working out, stacking, rolling, popping wheelies | Yes, as Anki's engine was recorded doing them; not yet tried on a robot as written |
| Freeplay with cubes: knocking over, pyramids | No |
| Freeplay with faces: greeting, peek-a-boo, fist bumps | A face that appears is acknowledged; the fist bump behavior is there, and nothing asks for it; the others, which look for faces and drive to them, are not |
| Needs, and asking to be played with | Yes |
| Feeding, repairing | Taken as actions; no minigame |
| Sparks: tricks on request | No way to ask for one |
| Games: Quick Tap, Memory Match, Keep Away | Yes, asked for in freeplay and answered on a cube; their moves measured on a robot, hands of each played on one, a whole game of none |
| Meeting people: enrolling faces, saying names | Enrolling yes, by `Faces.enroll()`; no asking, and no saying names: that is text to speech |
| Songs | Yes, all 39, in freeplay now and then; the vibrato is left out |
| Explorer mode: driving by hand, with the camera | `examples/rc.py` drives it with an Xbox 360 controller, without the camera |
| Code Lab | No |
