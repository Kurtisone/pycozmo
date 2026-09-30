"""

Behavior representation and reading.

"""

import math
import os
import random
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple, Any

from . import event
from . import client
from . import emotions
from . import needs
from . import robot
from .logger import logger, logger_behavior
from .json_loader import get_json_files, load_json_file


__all__ = [
    "ReactionTrigger",
    "Behavior",

    "load_behaviors",
    "load_reaction_trigger_behavior_map",
]


class ReactionTrigger:
    """ Reaction trigger representation class. """
    __slots__ = [
        "name",
        "behavior_id",
        "should_resume_last",
        "max_confidence",
        "cooldown_time",
        "conf",
        "last_run",
    ]

    def __init__(self, name: str, behavior_id: str, should_resume_last: Optional[bool] = False,
                 max_confidence: Optional[float] = None, cooldown_time: float = 0.0,
                 conf: Optional[Dict] = None):
        self.name = str(name)
        self.behavior_id = str(behavior_id)
        self.should_resume_last = bool(should_resume_last)
        #: Confidence at or below which the reaction applies, when a trigger names several.
        self.max_confidence = None if max_confidence is None else float(max_confidence)
        #: Seconds before the reaction may run again. Zero for no cooldown at all.
        self.cooldown_time = float(cooldown_time)
        #: The whole configuration entry, which carries parameters nothing reads yet.
        self.conf = dict(conf or {})
        # When the reaction last ran, from time.perf_counter(). 0.0 until it runs once.
        self.last_run = 0.0

    @classmethod
    def from_json(cls, data: Dict) -> "ReactionTrigger":
        # The confidence and the cooldown only ever come under frustrationParams in the resources,
        # Frustration being the only trigger that grades its reaction.
        frustration = data.get('frustrationParams', {})
        return cls(name=data['reactionTrigger'],
                   behavior_id=data['behaviorID'],
                   should_resume_last=data.get('genericStrategyParams', {}).get('shouldResumeLast'),
                   max_confidence=frustration.get('maxConfidence'),
                   cooldown_time=frustration.get('cooldownTime_s', 0.0),
                   conf=data)

    def is_on_cooldown(self, now: Optional[float] = None) -> bool:
        """ Whether the reaction ran too recently to run again. """
        if not self.cooldown_time or not self.last_run:
            return False
        now = time.perf_counter() if now is None else now
        return now - self.last_run < self.cooldown_time

    def ran(self, now: Optional[float] = None) -> None:
        """ Record the reaction as just run, which starts its cooldown. """
        self.last_run = time.perf_counter() if now is None else now


class Behavior(event.Dispatcher):
    """ Behavior representation class. """

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None) -> None:
        super().__init__()
        self.cli = cli
        self.conf = conf
        # The robot's nurture needs, for the behaviors that ask about them. The brain owns them and
        # hands them over when it loads the behaviors; None means nothing tracks them.
        self.needs = robot_needs
        # Whether the behavior has been taken off the robot. A behavior that waits on a timer can
        # have its callback run just after that, and one that then reported itself done would end
        # whatever had taken its place - a reaction, usually. It starts False so that a behavior
        # driven straight rather than through the client works as it always did.
        self.deactivated = False

    def get_id(self) -> str:
        behavior_id: str = self.conf["behaviorID"]
        return behavior_id

    def give_up(self, reason: str) -> None:
        """ Report the behavior as done without doing anything, and say why. """
        logger.warning("Behavior '{}' {}.".format(self.get_id(), reason))
        self.done()

    def done(self) -> None:
        """ Report the behavior as done, unless it has already been taken off the robot. """
        if self.deactivated:
            logger.debug("Behavior '{}' finished after being deactivated.".format(self.get_id()))
            return
        self.cli.conn.post_event(event.EvtBehaviorDone, self.cli)

    def post_emotion_event(self, name: str) -> None:
        """ Post an emotion event, by name, for the brain to apply to the mood. """
        self.cli.conn.post_event(event.EvtEmotionEvent, self.cli, name)

    def wants_to_run(self) -> bool:
        """
        Whether the behavior would take the robot if the activity engine offered it now.

        A behavior whose class is not implemented never would: activating it only logs that and
        reports it done, and the engine would offer it the robot again straight away.
        """
        return False

    def activate(self) -> None:
        self.give_up("not implemented")

    def deactivate(self) -> None:
        pass


class BehaviorPlayAnim(Behavior):
    """
    Play a sequence of animation triggers.

    An animation trigger is a "CladEvent" name from
    cozmo_resources/assets/animationGroupMaps/AnimationTriggerMap.json, which the client resolves
    to an animation group. Behaviors that only react with an animation need nothing more than the
    triggers: either "animTriggers" in their own configuration, or, for the reaction behaviors
    whose configuration does not name any, the `default_anim_triggers` attribute of a subclass.
    """

    #: Animation triggers for subclasses whose configuration carries none of its own.
    default_anim_triggers: Sequence[str] = ()

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.anim_triggers: List[str] = list(conf.get("animTriggers", []))
        # Triggers left to play during the current activation, and the position in them.
        self.sequence: List[str] = []
        self.current_trigger = 0
        self.add_handler(event.EvtAnimationCompleted, self._on_animation_completed)

    def get_anim_triggers(self) -> Sequence[str]:
        """ Animation triggers to play, in order, on activation. """
        return self.anim_triggers or self.default_anim_triggers

    def wants_to_run(self) -> bool:
        # A strategy this library cannot evaluate holds the behavior back. ReactToObstacle is the
        # only behavior in the resources carrying one, and it asks for ObstacleDetected.
        strategy = self.conf.get("wantsToRunStrategyConfig")
        if strategy is not None:
            logger.debug("Behavior '{}' wants a {} strategy, which is not implemented.".format(
                self.get_id(), strategy.get("strategyType")))
            return False
        # Playing nothing at all would leave the engine looking for something to do again at once.
        groups = self.cli.animation_groups or {}
        return any(trigger in groups for trigger in self.get_anim_triggers())

    def activate(self) -> None:
        # An animation group the assets do not define never completes, which would leave the
        # behavior active forever, so unknown triggers are dropped before anything is played.
        groups = self.cli.animation_groups or {}
        self.sequence = [trigger for trigger in self.get_anim_triggers() if trigger in groups]
        self.current_trigger = 0
        if not self.sequence:
            self.give_up("has no animation trigger to play")
            return
        self._play_next()

    def _play_next(self) -> None:
        trigger = self.sequence[self.current_trigger]
        self.current_trigger += 1
        self.cli.play_anim_group(trigger)

    def _on_animation_completed(self, cli: client.Client) -> None:
        if self.current_trigger < len(self.sequence):
            self._play_next()
        else:
            self.on_sequence_completed()

    def on_sequence_completed(self) -> None:
        """ Called once the whole sequence has played. """
        self.done()

    def deactivate(self) -> None:
        self.cli.cancel_anim()


class BehaviorPlayArbitraryAnim(BehaviorPlayAnim):
    """ Play a random animation trigger. """

    def activate(self) -> None:
        # TODO: Pick random animation trigger and put it in sequence
        super().activate()


class BehaviorAcknowledge(BehaviorPlayAnim):
    """ AcknowledgeFace and AcknowledgeObject - acknowledge what was just seen. """

    def get_anim_triggers(self) -> Sequence[str]:
        # Both behaviors name their animation trigger, under a key of their own.
        trigger = self.conf.get("ReactionAnimGroup")
        return (trigger, ) if trigger else ()


class BehaviorReactToCliff(BehaviorPlayAnim):
    """ ReactToCliff behavior - currently, just plays animation. """

    # TODO: Back away from the cliff.
    default_anim_triggers = ("ReactToCliff", )


class BehaviorReactToPickup(BehaviorPlayAnim):
    """ ReactToPickup behavior - react to being lifted off the ground. """

    default_anim_triggers = ("ReactToPickup", )


class BehaviorReactToImpact(BehaviorPlayAnim):
    """ ReactToImpact behavior - react to hitting something. """

    # Reached from the RobotFalling reaction trigger, hence a fall that ends in an impact.
    default_anim_triggers = ("ReactToImpact", )


class BehaviorReactToUnexpectedMovement(BehaviorPlayAnim):
    """ ReactToUnexpectedMovement behavior - react to being moved or held back while driving. """

    # TODO: The engine has severe low-energy and low-repair variants of this reaction.
    default_anim_triggers = ("ReactToUnexpectedMovement", )

    def on_sequence_completed(self) -> None:
        # Being shoved around costs confidence.
        self.post_emotion_event("ReactToUnexpectedMovement")
        super().on_sequence_completed()


class BehaviorReactToRobotOnBack(BehaviorPlayAnim):
    """ ReactToRobotOnBack behavior - roll back onto the treads. """

    # The animation drives the lift and the head to flip the robot over by itself.
    default_anim_triggers = ("FlipDownFromBack", )


class BehaviorReactToRobotOnFace(BehaviorPlayAnim):
    """ ReactToRobotOnFace behavior - roll off the face and back onto the treads. """

    # TODO: FailedToRightFromFace, when the robot is still on its face afterwards.
    default_anim_triggers = ("FacePlantRoll", )


class BehaviorReactToRobotOnSide(BehaviorPlayAnim):
    """ ReactToRobotOnSide behavior - ask to be put back on the treads. """

    def get_anim_triggers(self) -> Sequence[str]:
        # The robot cannot right itself from its side, so it asks, facing the side it lies on.
        if self.cli.robot_orientation == robot.RobotOrientation.ON_LEFT_SIDE:
            return ("ReactToOnLeftSide", )
        return ("ReactToOnRightSide", )


class BehaviorReactToRobotShaken(BehaviorPlayAnim):
    """ ReactToRobotShaken behavior - recover from being shaken. """

    # TODO: Loop DizzyShakeLoop while the shaking lasts and pick the reaction by its intensity
    #  (DizzyReactionSoft, DizzyReactionMedium, DizzyReactionHard).
    default_anim_triggers = ("DizzyShakeStop", )


class BehaviorReactToPet(BehaviorPlayAnim):
    """ ReactToPet behavior - react to a cat or a dog being seen. """

    # TODO: PetDetectionShort_Cat and PetDetectionShort_Dog, once the pet type is known.
    default_anim_triggers = ("PetDetectionShort", )


class BehaviorReactToCubeMoved(BehaviorPlayAnim):
    """ ReactToCubeMoved behavior - react to a cube being moved by the player. """

    default_anim_triggers = ("CubeMovedSense", )


class BehaviorReactToSparked(BehaviorPlayAnim):
    """ ReactToSparked behavior - react to being sparked from the application. """

    default_anim_triggers = ("SparkGetIn", )


class BehaviorReactToFrustration(BehaviorPlayAnim):
    """ ReactToFrustration behavior - react to a failed attempt at something. """

    def get_anim_triggers(self) -> Sequence[str]:
        # Minor and Major frustration differ only by their configuration.
        trigger = self.conf.get("anim")
        return (trigger, ) if trigger else ()

    def on_sequence_completed(self) -> None:
        # Getting over it restores confidence: 0.1 for the minor variant, a full 1.0 for the major.
        emotion_event = self.conf.get("finalEmotionEvent")
        if emotion_event:
            self.post_emotion_event(emotion_event)
        super().on_sequence_completed()

    # TODO: For the major variant, drive away by a random randomDrive* amount.


class BehaviorRamIntoBlock(BehaviorPlayAnim):
    """ RamIntoBlock behavior - drive into a block that cannot be approached to dock with. """

    # TODO: Drive into the block. Only the sound of the crash is played for now.
    default_anim_triggers = ("SoundOnlyRamIntoBlock", )


class BehaviorFistBump(BehaviorPlayAnim):
    """ FistBump behavior - ask for a fist bump. """

    # The bump itself is felt on the accelerometer, which is not wired up, so the robot asks
    # once and is left hanging.
    # TODO: Look for a face first (maxTimeToLookForFace_s, abortIfNoFaceFound), detect the bump
    #  and play FistBumpSuccess, retrying with FistBumpRequestRetry.
    default_anim_triggers = ("FistBumpRequestOnce", "FistBumpLeftHanging")


class BehaviorReactToOnCharger(BehaviorPlayAnim):
    """
    ReactToOnCharger behavior - react to being placed on the charger, then go to sleep.

    Unlike the other reactions, this one is not over when its animation is: the configuration
    gives the delay before the robot falls asleep on the charger and the delay before it lets
    the connection go. The behavior ends early if the robot is taken off the charger.
    """

    default_anim_triggers = ("PlacedOnCharger", )
    #: Animations played when the sleep delay expires.
    sleep_anim_triggers = ("GoToSleepGetIn", "GoToSleepSleeping")

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.time_til_sleep_animation = float(conf.get("timeTilSleepAnimation_s", 300.0))
        self.time_til_disconnection = float(conf.get("timeTilDisconnection_s", 330.0))
        self.asleep = False
        self.timer: Optional[threading.Timer] = None
        self.add_handler(event.EvtRobotOnChargerChange, self._on_robot_on_charger_change)

    def activate(self) -> None:
        self.asleep = False
        super().activate()

    def on_sequence_completed(self) -> None:
        if not self.asleep:
            # Wait on the charger, then fall asleep.
            self._start_timer(self.time_til_sleep_animation, self._fall_asleep)
        else:
            # Asleep: wait out the rest of the disconnection delay.
            delay = max(self.time_til_disconnection - self.time_til_sleep_animation, 0.0)
            self._start_timer(delay, self._disconnection_delay_expired)

    def _start_timer(self, delay: float, f: Any) -> None:
        self._cancel_timer()
        self.timer = threading.Timer(delay, f)
        self.timer.daemon = True
        self.timer.start()

    def _cancel_timer(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _fall_asleep(self) -> None:
        self.asleep = True
        groups = self.cli.animation_groups or {}
        self.sequence = [trigger for trigger in self.sleep_anim_triggers if trigger in groups]
        self.current_trigger = 0
        if not self.sequence:
            self.on_sequence_completed()
            return
        self._play_next()

    def _disconnection_delay_expired(self) -> None:
        # The robot would drop the connection here to save power. pycozmo keeps it, since
        # dropping it is the application's call, not a behavior's.
        logger.info("Behavior '{}' reached its disconnection delay.".format(self.get_id()))
        self.done()

    def _on_robot_on_charger_change(self, cli: client.Client, state: bool) -> None:
        if not state:
            self._cancel_timer()
            self.done()

    def deactivate(self) -> None:
        self._cancel_timer()
        super().deactivate()


class BehaviorExpressNeeds(BehaviorPlayAnim):
    """
    Ask for what the robot needs, while a need sits in a bracket.

    Six behaviors in the resources, and the five that any activity names are what makes the needs
    visible: NothingToDo, PlayAlone, Hiking, Socialize, BuildPyramid and PlayWithHumans all list
    them in their interlude chooser, so a robot getting bored or hungry says so between whatever
    else it is doing. Which one comes first is the order that chooser lists them in - the severe
    play requests before the mild repair, energy and play ones.

    The cooldown is a graph read at the need's own level, so the lower the need falls the more often
    the robot asks: Needs_MildLowEnergyRequest comes every 60 s while Energy is above 0.5 and every
    20 s once it is under 0.1.
    """

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.need: Optional[str] = conf.get("need")
        self.need_bracket: Optional[str] = conf.get("needBracket")
        nodes = (conf.get("cooldown") or {}).get("nodes") or []
        self.cooldown_graph: Optional[emotions.DecayGraph] = \
            emotions.DecayGraph([emotions.Node(x=n['x'], y=n['y']) for n in nodes]) if nodes else None
        self.last_run_time: Optional[float] = None

    def get_cooldown(self) -> float:
        """ How long to hold back before asking again, at the need's current level. """
        if self.cooldown_graph is None or self.needs is None or self.need is None:
            return 0.0
        return max(float(self.cooldown_graph.get_increment(self.needs.level(self.need))), 0.0)

    def wants_to_run(self) -> bool:
        if self.needs is None or self.need is None:
            return False
        if self.need_bracket is not None and not self.needs.in_bracket(self.need, self.need_bracket):
            return False
        if self.last_run_time is not None and \
                time.perf_counter() - self.last_run_time < self.get_cooldown():
            return False
        return super().wants_to_run()

    def activate(self) -> None:
        # Counted from the start rather than the end, which is what the graph's figures suit: a
        # 20 s cooldown on an animation that itself lasts several seconds.
        self.last_run_time = time.perf_counter()
        super().activate()


class BehaviorPlayAnimOnNeedsChange(BehaviorPlayAnim):
    """
    Announce a need having moved to another bracket.

    The three get-in behaviors of the severe needs activities. The activity's own strategy is what
    decides the robot is in trouble; this plays the animation that says so, once, and does not play
    again until the need moves somewhere else.
    """

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.need: Optional[str] = conf.get("need")
        # The bracket the announcement was last made for.
        self.announced_bracket: Optional[str] = None

    def wants_to_run(self) -> bool:
        if self.needs is None or self.need is None:
            return False
        if self.needs.bracket(self.need) == self.announced_bracket:
            return False
        return super().wants_to_run()

    def activate(self) -> None:
        if self.needs is not None and self.need is not None:
            self.announced_bracket = self.needs.bracket(self.need)
        super().activate()


class BehaviorWait(Behavior):
    """
    Do nothing, for a while.

    Two behaviors in the resources, both carrying no configuration at all, and only Needs_Wait is
    named by anything: it is the last resort of the two severe needs activities, for when the robot
    has asked for help and there is nothing else left to do.

    Anki's engine could take a behavior off the robot part way through, so a wait there could last
    until something else wanted the robot. This engine only looks for something to do once nothing
    is running, so waiting for ever would be waiting for ever. It waits for DURATION instead and
    reports itself done, which leaves the robot just as still while letting the engine think again.
    """

    #: How long one wait lasts, in seconds. The resources name no duration.
    DURATION = 5.0

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.timer: Optional[threading.Timer] = None

    def wants_to_run(self) -> bool:
        return True

    def activate(self) -> None:
        self.timer = threading.Timer(self.DURATION, self.done)
        self.timer.daemon = True
        self.timer.start()

    def deactivate(self) -> None:
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def done(self) -> None:
        self.timer = None
        super().done()


class BehaviorDriveInDesperation(BehaviorPlayAnim):
    """
    Wander about asking for help, which is what a robot in real trouble does.

    The state behavior of the two severe needs activities: Needs_SevereLowEnergyState and
    Needs_SevereLowRepairState. One activation is one round of it - turn, drive, then play the
    request animation the configuration names - and then the behavior reports itself done so that
    the engine can think again. While the need is still critical its activity is chosen again and
    the round starts over, so the robot keeps wandering and asking; the moment the need is met,
    something else takes the robot. Neither configuration names the need it belongs to, so watching
    one is not on offer, and holding the robot until it recovered would have held it for ever.

    What is read from the configuration: minTimeToIdle and maxTimeToIdle, which bound how long the
    robot drives before it stops to ask, the motion profile's speed_mmps and
    pointTurnSpeed_rad_per_sec, and requestAnimTrigger. How far it turns is not in there; a random
    part of a half turn either way is what keeps the robot milling about rather than setting off in
    one direction and driving off the table. useCubes is not read: on a real robot it sent a hungry
    Cozmo towards a cube to be fed from, and nothing here sees cubes.
    """

    #: Widest turn between two drives, in radians. Not from the resources.
    MAX_TURN = math.pi

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.min_time_to_idle = float(conf.get("minTimeToIdle", 1.5))
        self.max_time_to_idle = float(conf.get("maxTimeToIdle", 6.5))
        profile = conf.get("motionProfile") or {}
        self.speed = float(profile.get("speed_mmps", 40.0))
        self.turn_speed = float(profile.get("pointTurnSpeed_rad_per_sec", 1.5))
        self.timer: Optional[threading.Timer] = None

    def get_anim_triggers(self) -> Sequence[str]:
        trigger = self.conf.get("requestAnimTrigger")
        return (trigger, ) if trigger else ()

    def activate(self) -> None:
        # Turn first, so that one round after another does not add up to a straight line.
        angle = random.uniform(-self.MAX_TURN, self.MAX_TURN)
        # One wheel forward and the other back turns the robot on the spot, at twice the wheel speed
        # over the track width.
        wheel_speed = self.turn_speed * robot.TRACK_WIDTH.mm / 2.0
        if angle < 0.0:
            wheel_speed = -wheel_speed
        self.cli.drive_wheels(-wheel_speed, wheel_speed)
        self._after(abs(angle) / self.turn_speed, self._turned)

    def _turned(self) -> None:
        self.cli.drive_wheels(self.speed, self.speed)
        self._after(random.uniform(self.min_time_to_idle, self.max_time_to_idle), self._arrived)

    def _arrived(self) -> None:
        self.cli.stop_all_motors()
        if self.deactivated:
            # Something took the robot between the timer firing and this running.
            return
        # And now ask, which is what the round was for.
        super().activate()

    def _after(self, delay: float, f: Any) -> None:
        self.timer = threading.Timer(delay, f)
        self.timer.daemon = True
        self.timer.start()

    def deactivate(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.cli.stop_all_motors()
        super().deactivate()


class BehaviorDriveOffCharger(Behavior):
    """
    DriveOffCharger behavior - get off the charger.

    The robot backs onto its charger, so leaving it means driving forward: off the contacts, then
    the extra distance the configuration asks for. The distance is timed rather than measured, the
    robot reporting no odometry a behavior could wait on.
    """

    #: Speed the robot drives off at, in mm/s.
    SPEED = 50.0
    #: Distance covered before the extra distance the configuration asks for, in mm. That is about
    #: how far back the charger holds the treads from its lip.
    CONTACTS_DISTANCE = 40.0
    #: How long the behavior leaves the robot's status to catch up before trying again, in seconds.
    #: Without it, a status not yet showing the charger clear would have the robot drive off twice.
    SETTLE_TIME = 1.0
    #: How many times in a row the robot will try. The resources say nothing about retrying; this
    #: is here so that a status stuck on the charger cannot drive the robot across the table.
    MAX_ATTEMPTS = 3

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.extra_distance = float(conf.get("extraDistanceToDrive_mm", 60.0))
        self.attempts = 0
        self.last_run = 0.0
        self.timer: Optional[threading.Timer] = None

    def wants_to_run(self) -> bool:
        if not self.cli.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER:
            # Off the charger, so whatever it took to get there is behind us.
            self.attempts = 0
            return False
        if self.attempts >= self.MAX_ATTEMPTS:
            return False
        return time.perf_counter() - self.last_run >= self.SETTLE_TIME

    def activate(self) -> None:
        # TODO: Play a wake up animation. The resources name none for this behavior.
        # Getting going under its own steam makes the robot more confident.
        self.attempts += 1
        self.last_run = time.perf_counter()
        self.post_emotion_event("DriveOffCharger")
        self.cli.drive_wheels(self.SPEED, self.SPEED)
        self.timer = threading.Timer(
            (self.CONTACTS_DISTANCE + self.extra_distance) / self.SPEED, self._arrived)
        self.timer.daemon = True
        self.timer.start()

    def _arrived(self) -> None:
        self.cli.stop_all_motors()
        if self.attempts >= self.MAX_ATTEMPTS:
            logger.warning(
                "Behavior '{}' has driven off the charger {} times and the robot still reads as on "
                "it. Leaving it there.".format(self.get_id(), self.attempts))
        self.done()

    def deactivate(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.cli.stop_all_motors()


class BehaviorPounceOnMotion(Behavior):
    """
    Watch the ground for something moving, creep up on it, and pounce with the lift.

    Four behaviors in the resources are this class: PounceOnMotion_Socialize, Hiking_PounceOnMotion,
    SparksPounceOnMotion and VC_PounceOnMotion. Their configurations name what they tune, not how
    the behavior goes about it, so the sequence here is PyCozmo's own, built on Anki's animation
    triggers:

    - PounceInitial to get in, then the head all the way down, where the camera sees the ground from
      65 to 400 mm ahead - PounceDrive holds the head there too;
    - motion off to one side turns the robot towards it, motion too far away has it creep closer,
      and motion within reach gets PouncePounce;
    - a pounce is judged by the lift: the animations bring it all the way down, so a lift that stays
      up has come down on something - PounceSuccess - and one that reached the bottom missed -
      PounceFail. Then the robot backs off by backUpDistance and watches again;
    - after timeBeforeRotate_Sec without motion it turns to look elsewhere, by up to
      searchAmplitudeDeg either way, and after a turn pounces anyway with oddsOfPouncingOnTurn -
      which is what makes it look like it is playing rather than scanning;
    - after maxNoGroundMotionBeforeBored_running_Sec without motion it gets bored, plays PounceGetOut
      unless skipGetOutAnim, and is done. maxTimeBehaviorTimeout_Sec, where given, ends it anyway.

    It only wants to run when motion has been seen on the ground within
    maxNoGroundMotionBeforeBored_notRunning_Sec: Hiking's configuration says it should never run
    unless the robot sees motion.

    How far a pounce reaches is not in the resources either. The pounce animations lunge about 45 mm
    once the robot has capped their speeds, and the lift, lowered, hides the ground up to about 58 mm
    ahead, so motion within POUNCE_DISTANCE is pounced on.
    """

    #: Head angle to watch the ground from. The camera then sees from 65 to 400 mm ahead.
    WATCH_HEAD_ANGLE = robot.MIN_HEAD_ANGLE
    #: Motion closer than this, in mm ahead of the robot's origin, is within reach of a pounce.
    POUNCE_DISTANCE = 120.0
    #: Where creeping up on motion stops, in mm ahead of the origin, and the most it goes at once.
    APPROACH_DISTANCE = 100.0
    MAX_APPROACH = 150.0
    #: Motion further off to one side than this is turned towards first, in radians.
    TURN_THRESHOLD = math.radians(15.0)
    #: How far above its lowest the lift has to have stopped for a pounce to have caught something.
    CATCH_MARGIN_MM = 5.0
    #: Speeds for creeping and turning. Slow on purpose: the camera sees nothing while they last.
    DRIVE_SPEED = 40.0
    TURN_SPEED = 1.5
    #: How close to its target a turn, in radians, or a drive, in mm, has to get to be done.
    TURN_TOLERANCE = math.radians(2.0)
    DRIVE_TOLERANCE = 2.0
    #: How many times its nominal duration a turn or a drive is given, and then some, before it is
    #: stopped wherever it has got to.
    MOVE_TIMEOUT_FACTOR = 3.0
    MOVE_TIMEOUT_MARGIN = 1.0

    def __init__(self, cli: client.Client, conf: Any,
                 robot_needs: Optional[needs.Needs] = None):
        super().__init__(cli, conf, robot_needs)
        self.bored_running = float(conf.get("maxNoGroundMotionBeforeBored_running_Sec", 20.0))
        self.bored_not_running = float(conf.get("maxNoGroundMotionBeforeBored_notRunning_Sec", 3.0))
        self.back_up_distance = float(conf.get("backUpDistance", -50.0))
        # Hiking_PounceOnMotion spells it TimeBeforeRotate_Sec.
        self.time_before_rotate = float(conf.get("timeBeforeRotate_Sec", conf.get("TimeBeforeRotate_Sec", 6.0)))
        self.odds_of_pouncing_on_turn = float(conf.get("oddsOfPouncingOnTurn", 0.0))
        self.search_amplitude = math.radians(float(conf.get("searchAmplitudeDeg", 90.0)))
        self.max_time: Optional[float] = conf.get("maxTimeBehaviorTimeout_Sec")
        self.skip_get_out = bool(conf.get("skipGetOutAnim", False))
        self.needs_action: Optional[str] = conf.get("needsActionID")
        self.lock = threading.RLock()
        self.state = "idle"
        self.timers: Dict[str, threading.Timer] = {}
        self.last_motion_time = 0.0
        # Where a turn or a drive started, and what it is after: (heading, x, y, target, then).
        self.move: Optional[Tuple[float, float, float, float, Any]] = None
        #: Pounces made and caught during the current activation.
        self.pounces = 0
        self.catches = 0
        self.add_handler(event.EvtMotionObserved, self._on_motion)
        self.add_handler(event.EvtAnimationCompleted, self._on_animation_completed)
        self.add_handler(event.EvtRobotStateUpdated, self._on_robot_state)

    def wants_to_run(self) -> bool:
        groups = self.cli.animation_groups or {}
        if "PouncePounce" not in groups:
            return False
        last = self.cli.last_ground_motion
        return last is not None and time.perf_counter() - last[0] <= self.bored_not_running

    def activate(self) -> None:
        with self.lock:
            self.pounces = 0
            self.catches = 0
            self.last_motion_time = time.perf_counter()
            if self.max_time is not None:
                self._after("max_time", float(self.max_time), self._get_out)
            if self._play("getting_in", "PounceInitial"):
                return
            self._watch()

    # ------------------------------------------------------------------ states

    def _watch(self) -> None:
        """ Look at the ground and wait for something to move. """
        self.state = "watching"
        self.cli.set_head_angle(self.WATCH_HEAD_ANGLE.radians)
        idle = time.perf_counter() - self.last_motion_time
        self._after("bored", max(self.bored_running - idle, 0.0), self._get_out)
        self._after("rotate", self.time_before_rotate, self._look_elsewhere)

    def _on_motion(self, cli: client.Client, motion: Any) -> None:
        with self.lock:
            if self.state != "watching" or motion.ground_centroid is None or self.deactivated:
                return
            self.last_motion_time = time.perf_counter()
            self._cancel("bored", "rotate")
            x, y = motion.ground_centroid
            bearing = math.atan2(y, x)
            self.post_emotion_event("MotionReact")
            if abs(bearing) > self.TURN_THRESHOLD:
                logger_behavior.info("Motion at {:.0f}, {:.0f} mm: turning {:.0f} degrees.".format(
                    x, y, math.degrees(bearing)))
                self._turn(bearing, self._watch)
            elif x <= self.POUNCE_DISTANCE:
                logger_behavior.info("Motion at {:.0f}, {:.0f} mm: pouncing.".format(x, y))
                self._pounce()
            else:
                distance = min(x - self.APPROACH_DISTANCE, self.MAX_APPROACH)
                logger_behavior.info("Motion at {:.0f}, {:.0f} mm: creeping {:.0f} mm closer.".format(
                    x, y, distance))
                self._drive(distance, "approaching", self._watch)

    def _look_elsewhere(self) -> None:
        with self.lock:
            if self.state != "watching" or self.deactivated:
                return
            angle = random.uniform(-self.search_amplitude, self.search_amplitude)
            pounce = random.random() < self.odds_of_pouncing_on_turn
            logger_behavior.info("Nothing moving: looking {:.0f} degrees away{}.".format(
                math.degrees(angle), ", then pouncing" if pounce else ""))
            self._turn(angle, self._pounce if pounce else self._watch)

    def _pounce(self) -> None:
        self.pounces += 1
        if self.needs is not None and self.needs_action:
            self.needs.apply_action(self.needs_action)
        if not self._play("pouncing", "PouncePounce"):
            self._watch()

    def _judge_pounce(self) -> None:
        """ Did the lift come down on something? """
        lift = self.cli.lift_position.height.mm
        caught = lift > robot.MIN_LIFT_HEIGHT.mm + self.CATCH_MARGIN_MM
        if caught:
            self.catches += 1
        logger_behavior.info("Pounced, lift at {:.1f} mm: {}.".format(lift, "caught" if caught else "missed"))
        if not self._play("reacting", "PounceSuccess" if caught else "PounceFail"):
            self._back_up()

    def _back_up(self) -> None:
        self._drive(self.back_up_distance, "backing_up", self._watch)

    def _get_out(self) -> None:
        with self.lock:
            if self.deactivated or self.state in ("idle", "getting_out"):
                return
            self._cancel(*list(self.timers))
            self.cli.stop_all_motors()
            if self.skip_get_out or not self._play("getting_out", "PounceGetOut"):
                self.state = "idle"
                self.done()

    def _on_animation_completed(self, cli: client.Client) -> None:
        with self.lock:
            if self.deactivated:
                return
            if self.state == "getting_in":
                self._watch()
            elif self.state == "pouncing":
                self._judge_pounce()
            elif self.state == "reacting":
                self._back_up()
            elif self.state == "getting_out":
                self.state = "idle"
                self.done()

    # ------------------------------------------------------------------ motion

    def _turn(self, angle: float, then: Any) -> None:
        """
        Turn on the spot by angle, radians, left positive, then call then.

        The wheels are only a rough guide to how far the robot has turned: its treads slip in a
        turn, and on a robot the heading changed about half as much as the wheel speeds said. So
        the turn goes on until the heading the robot reports has changed by the angle.
        """
        self.state = "turning"
        self._start_move(angle, then)
        wheel_speed = math.copysign(self.TURN_SPEED * robot.TRACK_WIDTH.mm / 2.0, angle)
        self.cli.drive_wheels(-wheel_speed, wheel_speed)
        self._after("move", abs(angle) / self.TURN_SPEED * self.MOVE_TIMEOUT_FACTOR + self.MOVE_TIMEOUT_MARGIN,
                    lambda: self._stop_then("turning", then))

    def _drive(self, distance: float, state: str, then: Any) -> None:
        """ Drive straight by distance, mm, forwards if positive, then call then. """
        self.state = state
        self._start_move(distance, then)
        speed = math.copysign(self.DRIVE_SPEED, distance)
        self.cli.drive_wheels(speed, speed)
        self._after("move", abs(distance) / self.DRIVE_SPEED * self.MOVE_TIMEOUT_FACTOR + self.MOVE_TIMEOUT_MARGIN,
                    lambda: self._stop_then(state, then))

    def _start_move(self, target: float, then: Any) -> None:
        pose = self.cli.pose
        self.move = (pose.rotation.angle_z.radians, pose.position.x, pose.position.y, target, then)

    def _on_robot_state(self, cli: client.Client) -> None:
        """ Stop a turn or a drive once the robot's own pose says it has gone far enough. """
        with self.lock:
            if self.move is None or self.deactivated or self.state not in ("turning", "approaching", "backing_up"):
                return
            heading, x, y, target, then = self.move
            pose = self.cli.pose
            if self.state == "turning":
                turned = (pose.rotation.angle_z.radians - heading + math.pi) % (2.0 * math.pi) - math.pi
                arrived = abs(turned) >= abs(target) - self.TURN_TOLERANCE
            else:
                travelled = math.hypot(pose.position.x - x, pose.position.y - y)
                arrived = travelled >= abs(target) - self.DRIVE_TOLERANCE
            if arrived:
                self._stop_then(self.state, then)

    def _stop_then(self, state: str, then: Any) -> None:
        with self.lock:
            if self.deactivated or self.state != state:
                return
            self._cancel("move")
            self.move = None
            self.cli.stop_all_motors()
            then()

    # ------------------------------------------------------------------ plumbing

    def _play(self, state: str, trigger: str) -> bool:
        """ Play an animation trigger, if the resources have it, and enter a state until it ends. """
        if trigger not in (self.cli.animation_groups or {}):
            return False
        self.state = state
        self.cli.play_anim_group(trigger)
        return True

    def _after(self, name: str, delay: float, f: Any) -> None:
        self._cancel(name)
        timer = threading.Timer(delay, f)
        timer.daemon = True
        self.timers[name] = timer
        timer.start()

    def _cancel(self, *names: str) -> None:
        for name in names:
            timer = self.timers.pop(name, None)
            if timer is not None:
                timer.cancel()

    def deactivate(self) -> None:
        with self.lock:
            self._cancel(*list(self.timers))
            self.state = "idle"
        self.cli.stop_all_motors()
        self.cli.cancel_anim()


def get_behavior_class_from_dict(data):
    """ Choose a behavior class, based on the behaviorClass JSON attribute. """
    # TODO: Replace with a behavior package.
    class_map = {
        "PlayAnim": BehaviorPlayAnim,
        "PlayArbitraryAnim": BehaviorPlayArbitraryAnim,
        "AcknowledgeFace": BehaviorAcknowledge,
        "AcknowledgeObject": BehaviorAcknowledge,
        "DriveOffCharger": BehaviorDriveOffCharger,
        "FistBump": BehaviorFistBump,
        "RamIntoBlock": BehaviorRamIntoBlock,
        "ReactToCliff": BehaviorReactToCliff,
        "ReactToCubeMoved": BehaviorReactToCubeMoved,
        "ReactToFrustration": BehaviorReactToFrustration,
        "ReactToImpact": BehaviorReactToImpact,
        "ReactToOnCharger": BehaviorReactToOnCharger,
        "ReactToPet": BehaviorReactToPet,
        "ReactToPickup": BehaviorReactToPickup,
        "ReactToRobotOnBack": BehaviorReactToRobotOnBack,
        "ReactToRobotOnFace": BehaviorReactToRobotOnFace,
        "ReactToRobotOnSide": BehaviorReactToRobotOnSide,
        "ReactToRobotShaken": BehaviorReactToRobotShaken,
        "ReactToSparked": BehaviorReactToSparked,
        "ReactToUnexpectedMovement": BehaviorReactToUnexpectedMovement,
        "ExpressNeeds": BehaviorExpressNeeds,
        "PlayAnimOnNeedsChange": BehaviorPlayAnimOnNeedsChange,
        "DriveInDesperation": BehaviorDriveInDesperation,
        "Wait": BehaviorWait,
        "PounceOnMotion": BehaviorPounceOnMotion,
        # Not implemented, for lack of an animation in AnimationTriggerMap.json:
        # ReactToMotorCalibration, ReactToPlacedOnSlope, ReactToReturnedToTreads.
    }
    # The cube handling and game behaviors build on this module, so they join the map here.
    from . import cube_behaviors, game_behaviors
    class_map.update({
        "RequestGameSimple": game_behaviors.BehaviorRequestGameSimple,
        "PutDownBlock": cube_behaviors.BehaviorPutDownBlock,
        "PickUpCube": cube_behaviors.BehaviorPickUpCube,
        "PickUpAndPutDownCube": cube_behaviors.BehaviorPickUpAndPutDownCube,
        "CubeLiftWorkout": cube_behaviors.BehaviorCubeLiftWorkout,
        "StackBlocks": cube_behaviors.BehaviorStackBlocks,
        "RollBlock": cube_behaviors.BehaviorRollBlock,
        "PopAWheelie": cube_behaviors.BehaviorPopAWheelie,
    })
    cls = class_map.get(data["behaviorClass"], Behavior)
    return cls


def load_behaviors(resource_dir: str, cli: client.Client,
                   robot_needs: Optional[needs.Needs] = None) -> Dict[str, Behavior]:

    start_time = time.perf_counter()

    behavior_files = get_json_files(
        resource_dir, [os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'behaviors')])
    behaviors = {}
    for filename in behavior_files:
        data = load_json_file(filename)
        cls = get_behavior_class_from_dict(data)
        behaviors[data['behaviorID']] = cls(cli, data, robot_needs)

    logger.debug("Loaded {} behaviors in {:.02f} s.".format(len(behaviors), time.perf_counter() - start_time))

    return behaviors


def load_reaction_trigger_behavior_map(resource_dir: str) -> Dict[str, List[ReactionTrigger]]:
    """
    Load the reaction trigger behavior map.

    A trigger can name more than one behavior, to be chosen between when it fires. Frustration names
    two, a minor and a major variant, each declaring the confidence at or below which it applies.
    Keeping one behavior per trigger silently dropped all but the last, so the map holds a list.
    """

    start_time = time.perf_counter()

    reaction_trigger_behavior_map: Dict[str, List[ReactionTrigger]] = {}
    filename = os.path.join(resource_dir, 'cozmo_resources', 'config',
                            'engine', 'behaviorSystem', 'reactionTrigger_behavior_map.json')

    json_data = load_json_file(filename)
    for trigger in json_data['reactionTriggerBehaviorMap']:
        reaction = ReactionTrigger.from_json(trigger)
        reaction_trigger_behavior_map.setdefault(reaction.name, []).append(reaction)

    logger.debug("Loaded {} entry reaction trigger behavior map in {:.02f} s.".format(
        sum(len(r) for r in reaction_trigger_behavior_map.values()), time.perf_counter() - start_time))

    return reaction_trigger_behavior_map
