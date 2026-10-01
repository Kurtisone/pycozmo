Cozmo's own behavior
====================

Cozmo's personality engine ran off-board, in the Cozmo app, and PyCozmo replaces that app: reproducing the robot's own
behavior means reading Anki's resource files and driving the robot from them. How to run it is in the
[README](../README.md#cozmos-own-behavior); this is what it does, and what it does not yet.


What is reproduced
------------------

Pick it up, set it down on an edge, put it on the charger or turn it on its back, and it reacts the way it did with the
Cozmo app, playing the animation Anki authored for that reaction, then goes back to what it was doing:

```
pycozmo.reaction     INFO     Processing CliffDetected
pycozmo.emotion      INFO     CliffDetected: Brave -0.12, Calm -0.12, Happy -0.12
pycozmo.behavior     INFO     Activating ReactToCliff
pycozmo.animation    INFO     Playing animation group ReactToCliff
pycozmo.animation    INFO     Playing animation anim_reacttocliff_edgeliftup_01
```

`reactionTrigger_behavior_map.json` maps 21 reaction triggers to a behavior. 18 of them play the animation group Anki
gave them, resolved through `AnimationTriggerMap.json`. The remaining three - `MotorCalibration`, `RobotPlacedOnSlope`
and `ReturnedToTreads` - have no animation anywhere in the resources, under their behavior ID, their trigger name or
any name close to either, so they log a warning and end.

Of those 21 triggers, eleven are raised today: `CliffDetected`, `RobotPickedUp`, `RobotFalling`, `PlacedOnCharger`,
`Hiccup`, the four the robot's attitude produces, `RobotOnBack`, `RobotOnFace`, `RobotOnSide` and `ReturnedToTreads`,
and for the cubes `ObjectPositionUpdated` and `CubeMoved` - see [cubes_and_games.md](cubes_and_games.md); a twelfth,
`FacePositionUpdated`, once faces are found. The rest wait on parts that are not implemented: the other vision triggers
need pet detection, and the others come from game and engine states the activity engine does not reach yet. None of them
needs motion detection - `UnexpectedMovement`, despite its name, is not something the camera sees.

Two details matter for the result to look right rather than merely work:

- Animations are chosen for the current head angle. 43 of the animation groups hold one animation per head angle band,
  with the angle baked into the animation, so playing the wrong one makes the head jump. Per-animation cooldowns are
  honoured too, which is what keeps a reaction from repeating itself.
- A reaction marked `shouldResumeLast` puts back what it interrupted. A cliff, a shove or a motor calibration
  interrupts what the robot was doing rather than ending it.

The mood engine works: emotion events shift the seven emotions and each decays on its own schedule. It gates the one
activity whose configuration asks it to - see [below](#what-the-robot-does-when-nothing-has-happened) - and
does not yet weigh anything else.


What the robot needs
--------------------

Alongside the mood sit three nurture needs - `Repair`, `Energy` and `Play`. A need is not an emotion: an emotion is a
shove that decays back to nothing within a couple of minutes, while a need starts full, falls for hours, and only
something done to the robot puts it back. They are what turns a robot left to itself from merely idle into one that
starts asking for something.

They fall at the rates Anki set, which depend on how low they already are, and each holds at full for twenty minutes
first. Left alone from a fresh start, a robot reaches each bracket at:

| Need | Normal | Warning | Critical |
|---|---|---|---|
| `Play` | 20 min | 55 min | 1 h 23 |
| `Energy` | 22 min | 1 h 39 | 3 h 24 |
| `Repair` | 34 min | 9 h 51 | 19 h 11 |

One need also drags on another: `Repair` between 0.03 and 0.3 makes `Play` fall twice as fast, so a broken robot gets
bored quicker. It is the only cross-effect in the whole configuration - every other multiplier in the file is one.

**What you see.** `NothingToDo`, `PlayAlone`, `Hiking`, `Socialize`, `BuildPyramid` and `PlayWithHumans` all list the
five needs requests as interludes, so from about 55 minutes in the robot starts slipping them between whatever else it
is doing, the more often the lower the need:

```
pycozmo.behavior     INFO     Activating Needs_MildLowPlayRequest
pycozmo.animation    INFO     Playing animation group NeedsMildLowPlayRequest
```

**Once a need is critical**, an activity of its own takes the robot and keeps it. The announcement plays once, and then
the robot wanders and asks for help, over and over, until the need is met:

```
pycozmo.behavior     INFO     Starting activity NeedsSevereLowEnergy
pycozmo.behavior     INFO     Activating Needs_SevereLowEnergyGetIn
pycozmo.animation    INFO     Playing animation group NeedsSevereLowEnergyGetIn
pycozmo.behavior     INFO     Activating Needs_SevereLowEnergyState
pycozmo.animation    INFO     Playing animation group NeedsSevereLowEnergyRequest
pycozmo.behavior     INFO     Activating Needs_SevereLowEnergyState
```

One round of that is a turn, a drive of 1.5 to 6.5 seconds, and the request animation - and then the round ends, which
is what lets a fed robot get on with its life. Neither of Anki's two `DriveInDesperation` behaviors names the need it
belongs to, so watching one is not on offer; ending each round and letting the engine think again does the same job
without risking a robot that begs for ever because nothing was watching. `useCubes` is not read: on a real robot it
sent a hungry Cozmo towards a cube to be fed from.

`Repair` outranks `Energy`, so a robot both broken and starving asks to be mended rather than fed - that is
`needsSevereLowEnergy` standing aside through its `higherPriorityStrategyConfig`.

**Putting a need back** is something no part of this library does on its own, because on a real robot it was a thing
the player did in the app: `Feed` is worth a third of `Energy`, and `RepairHead`, `RepairLift` and `RepairTreads` a
third of `Repair` each. An application built on PyCozmo offers them the same way:

```python
brain.apply_need_action("Feed")
```

The other eighty-odd actions are what the robot and the player do together, most of them worth `Play` alone. A few are
applied from here: a fall costs 0.15 of `Repair`, being laid on its side 0.03 of `Play`, a behavior whose name is also
an action - `FistBump`, `PopAWheelie` - is worth that action when it finishes, and the cube behaviors and singing credit
the action their configuration names once they have done their part. The rest wait on what only the application did:
the feeding and repairing minigames, for one.

`needs_handlers_config.json`, which describes the face glitching as `Repair` falls, is the one part of the needs not
read yet.


What the robot does when nothing has happened
---------------------------------------------

When the brain starts, the robot first wakes up, with one of the five `anim_launch_wakeup` animations Anki's
`ConnectWakeUp` trigger names - what the Cozmo application played on connecting - and nothing else is chosen for it
until it has.

Reactions answer events. Between them, the activity engine decides what the robot does of its own accord. `Freeplay`
lists 25 sub-activities in priority order, and the first one that wants to run and has a behavior to offer gets the
robot:

- 14 spark activities, which need the application to send a spark.
- 3 severe-need activities, which take the robot once a nurture need (Energy, Repair, Play) is critical: see
  [What the robot needs](#what-the-robot-needs). While its needs are full, they never run.
- `PutDownDispatch`, when the robot was set down on its treads in the last 5 s.
- `Socialize`, when the robot has not been social lately: its configuration scores the `Social` emotion through a graph
  and asks for 0.5, which the graph gives while `Social` is at or below 0.3. This is the only place in Anki's resources
  where the mood decides an activity.
- `Singing`, which has Cozmo sing one of its 39 songs every 10 to 40 minutes, the sooner the better its Play need is
  met; and `BuildPyramid`, which needs a pyramid of cubes.
- `PlayWithHumans`, when the robot can ask for a game: Keep Away with a cube seen, Quick Tap with two cubes
  connected and one of them seen, Memory Match with the three connected and one seen.
- `PlayAlone`, `Hiking` and `NothingToDo`.

Within an activity, behaviors are drawn in a random order weighted by their score. A behavior that has just run loses
part of its score and wins it back over time: `GuardDog` scores nothing for 5 minutes and is whole again a quarter of
an hour on, and the bored animations keep half their score for 9 seconds, which is what keeps the robot from playing
two bored sequences in a row.

What actually runs today is what needs no face to be looked for or driven to - a face that appears is only acknowledged:
`DriveOffCharger`, the hiking intro, the `NothingToDo` idle and bored animations, `PounceOnMotion`, and with cubes the
workout, stacking, rolling a cube back upright, popping wheelies, putting a carried cube down, asking for a game, and
singing. An activity that wants the robot but can offer nothing is passed over rather than entered, since entering it
would leave the robot still for as long as its duration - 25 s for `PlayAlone`, a minute for `Hiking`.


Pouncing on motion
------------------

`PounceOnMotion` plays with the motion the camera sees on the ground (see [vision.md](vision.md#motion)). Once motion
has been seen there, Socialize and Hiking give it the robot: it puts its head down to watch, turns towards what moves,
creeps up on it, and pounces with the lift. A lift that stays up after a pounce is taken to have come down on something
- a finger, say - and gets `PounceSuccess`; one that reached the bottom missed, and gets `PounceFail`. On a real robot
that does not work yet: its lift came down all the way on a finger too. It looks elsewhere after a few seconds without
motion and gets bored after more, both as each of Anki's four configurations says. How it goes about it is PyCozmo's
own, built on Anki's pounce animations, since the configurations only tune it: in particular, motion is pounced on
within 120 mm, which the pounce animations' 45 mm lunge suggests but no file states.
