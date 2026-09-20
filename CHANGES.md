Revision History
================

This file holds two separate histories. "Fork" covers the changes made in this fork of zayfod/pycozmo, after
upstream development stopped in November 2020. "Upstream" below it is the inherited history, left as it was.


Fork
====

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
    animation still completes on a robot that does not acknowledge a start. Caught by the cozmo-emu
    integration suite, which exercises the whole stack and had not been run before v0.9.9 went out.

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
