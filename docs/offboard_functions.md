
Cozmo Off-Board Functions
=========================

Most of what makes Cozmo Cozmo did not run on the robot. The robot plays animations, drives its motors, follows paths
and streams its camera; everything else - choosing what to do, recognizing cubes and faces, keeping track of where
things are, speaking - ran in the Cozmo application, on the phone, in Anki's engine. PyCozmo reimplements that engine
from the application's resources, which the engine read as data.

This document describes those resources, and for each off-board function what the application did, what PyCozmo does
and what is missing. The README's [Cozmo's Own Behavior](../README.md#cozmos-own-behavior) section tells how the
robot behaves with what is there.


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
98% once converted: see the README's [Sound](../README.md#sound) section.

Missing:
- **Songs.** The 39 `Singing` behaviors name a Wwise switch - `Cozmo_Sings_80Bpm` / `Cozmo_Sings_Danny_Boy` - rather
  than an event, and nothing plays switches yet.
- **Text-to-speech.** The `tts/Voices` voices are Acapela's, and the engine that speaks with them is native code in
  the application, not a resource. Cozmo saying names, and the SDK's `say_text()`, need another speech engine.


Cube lights
-----------

Forty cube light animations, by trigger: patterns of on and off colours, periods and fades per light, rotating
round the cube or not, in steps with durations. `pycozmo.cube_lights` loads them and `Client.cubes.play_lights()`
shows them. The brain lights connected cubes as the application did: `Connected`, a dim cyan breath every five
seconds, and `Visible`, a steady cyan while the robot sees the cube. Quick Tap shows its own colours, and
`speedTapWin` and `speedTapLose` after each hand; the others serve games and tricks that are not implemented yet. How
long a light frame lasts is not measured; 30 ms fits Anki's periods.


Emotions and needs
------------------

The mood engine keeps seven emotions, each decaying on its own schedule and moved by emotion events. The nurture
needs - Energy, Repair, Play - fall over hours and drive the requests and activities that read them. Both work: see
[What the robot needs](../README.md#what-the-robot-needs). What raised the needs back was the application: feeding
with a shaken cube, a repair minigame, playing. `Brain.apply_need_action()` takes those actions; nothing performs
them yet.


Behaviors, reactions and activities
-----------------------------------

Behaviors are small programs over the robot's API, configured in `behaviors/`. There are 178 of them in 76 classes,
5 of which are factory and development tests. Reactions map 21 triggers to behaviors; activities choose what the
robot does when nothing has happened, in priority order: 14 sparks, which the application asked for, 3 severe-need
activities, and the freeplay ones.

PyCozmo implements 31 classes, 76 behaviors: the animation players, the reactions to being picked up, shaken, put on
its side or on the charger, cliffs, cube moves, driving off the charger, pouncing on motion, expressing needs,
waiting, picking cubes up, putting them down, stacking them, working out with one, and asking for a game of Quick
Tap. Eleven of the reaction triggers are raised. The activity engine runs freeplay as Anki's configuration says,
with its scores, cooldowns and mood gates, and honours the unlock a behavior needs: `pycozmo.unlocks` reads those of
a new robot and those the needs levels reward; nothing keeps a progression, so the robot is taken to have them all.

Missing classes, by what they need:

| Needs | Classes |
|---|---|
| Rolling and knocking over cubes: the robot's own manoeuvres, to be recorded on one | `RollBlock`, `RespondPossiblyRoll`, `PopAWheelie`, `KnockOverCubes` |
| More handling of cubes | `CheckForStackAtInterval`, `CantHandleTallStack`, `ReactToStackOfCubes`, `BuildPyramid`, `BuildPyramidBase`, `ReactToPyramid`, `PyramidThankYou`, `BringCubeToBeacon`, `ThinkAboutBeacons`, `GuardDog`, `Bouncer`, `FeedingSearchForCube`, `FeedingEat`, `OnboardingShowCube` |
| Faces | `FindFaces`, `SearchForFace`, `DriveToFace`, `InteractWithFaces`, `PeekABoo`, `PlayAnimWithFace`, `LookForFaceAndCube`, `EnrollFace`, `RespondToRenameFace` |
| A map of the surroundings | `ExploreLookAroundInPlace`, `ExploreVisitPossibleMarker`, `VisitInterestingEdge`, `LookInPlaceMemoryMap` |
| Sound | `Singing`, `Dance`, `FireTruckAlarm` |
| The application | `EarnedSparks`, `OnConfigSeen` |
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
  656 frames the same and placed the cubes within 2%.

Open:
- **Where the camera points.** The robot's reported pitch, flat on a table, drifts from one session to the next by
  about a degree, which moves what is placed on the ground by a few percent. A cube sitting on the table, whose
  centre is 22.5 mm up, could correct it.

Missing:
- **Faces**: detection, recognition by name, expressions. This needs a face detector, which would be a new
  dependency.
- **Pets.**
- **The charger**, which carries a marker of its own; and the SDK's custom markers.
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

`pycozmo.cube_handling` handles the cubes: it finds one, looking round for it if need be, goes to face the side it
saw, has a last look from there and docks, driving the last centimetres blind at 60 mm/s as Anki's engine did. It
picks the cube up, puts it down, sets it on another, and follows a cube an animation sets down: the workout's
put-down lowers the lift, pushes the cube on and backs off. A cube in the fork is taken to be 58 mm ahead of the
robot's origin, by its 3D model, and half a side beyond; this is not measured on a robot.

Missing: planning around obstacles; rolling a cube, popping a wheelie against it and knocking a stack over. Anki's
engine had the robot's firmware do these - its docking actions `DA_ROLL_LOW`, `DA_POP_A_WHEELIE` - through a message
PyCozmo's protocol does not know; the lift and wheel moves they make are to be recorded on a robot, through the SDK,
before they can be done again.


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
face or bringing a cube over. Keep Away is not implemented: the player moves a cube about and Cozmo pounces on it.


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
| `roll_cube`, `pop_a_wheelie` | No |
| `play_anim`, `play_anim_trigger`, idle animations | Animations and triggers yes; idle animations no |
| `play_audio`, `set_robot_volume` | Yes |
| `say_text` | No |
| `play_song` | No |
| Backpack lights, head light, OLED face image | Yes |
| Camera images, colour, exposure | Yes |
| `enable_stop_on_cliff` | Yes, and the brain turns it on, as the application did |
| Light cubes: connection, lights, taps, moves | Yes: `Client.cubes` |
| Light cubes: pose | Yes, from their markers |
| Charger pose, custom objects | No |
| Faces, facial expressions, pets | No |
| Navigation memory map | No |
| `start_behavior`: `FindFaces`, `KnockOverCubes`, `LookAroundInPlace`, `PounceOnMotion`, `RollBlock`, `StackBlocks` | `PounceOnMotion` and `StackBlocks` |
| Freeplay | Yes: `pycozmo.brain` |
| Needs levels | Yes |


Compared with the Cozmo application
-----------------------------------

| Application | PyCozmo |
|---|---|
| Freeplay: waking up, reactions, bored and idle animations, pouncing on motion, hiking | Yes |
| Freeplay with cubes: seeing them, lighting them, reacting to them | Yes |
| Freeplay with cubes: lifting, working out, stacking | Yes, not yet tried on a robot |
| Freeplay with cubes: rolling, popping wheelies, knocking over, pyramids | No |
| Freeplay with faces: greeting, peek-a-boo, fist bumps | The fist bump behavior is there, and nothing asks for it; the others need faces |
| Needs, and asking to be played with | Yes |
| Feeding, repairing | Taken as actions; no minigame |
| Sparks: tricks on request | No way to ask for one |
| Games: Quick Tap, Memory Match | Yes, asked for in freeplay and answered on a cube; not yet tried on a robot |
| Games: Keep Away | No |
| Meeting people: enrolling faces, saying names | No |
| Songs | No |
| Explorer mode: driving by hand, with the camera | `examples/rc.py` drives it with an Xbox 360 controller, without the camera |
| Code Lab | No |
